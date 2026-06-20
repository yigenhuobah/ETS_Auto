#!/usr/bin/env python3
"""
ETS Remote — Remote config, version check, forced update, and announcement system.

Provides:
  - Version comparison (semantic versioning)
  - Forced update check (minVer threshold)
  - Remote kill switch (allowStart)
  - Announcement push (from remote info.json)
  - pk_extra.json hot-update (silent download & replace)

Design: Zero-ops — uses GitHub + Chinese mirror (ghproxy/gitee) as CDN,
no self-hosted server required. Falls back through multiple mirror sources
with timeout protection on each.

Remote info.json schema:
  {
    "version": "0.5.1",
    "minVer": "0.4.0",
    "allowStart": true,
    "pkExtraUrl": "https://xxx/pk_extra.json",
    "announcement": "v0.5.1 修复了...",
    "downloadUrl": "https://github.com/xxx/releases/latest"
  }

Usage:
  from ets_remote import ETSRemote
  remote = ETSRemote(current_version="0.5.1")
  info = remote.check()
  if info and not info.allow_start:
      print("远程已关闭，程序无法启动")
  if info and info.force_update:
      print("版本过低，请更新到 %s" % info.latest_version)
"""
import json
import os
import shutil
import sys
import time
import urllib.request
import urllib.error


# ── Mirror sources (ordered by priority for Chinese users) ──
# Each entry: (name, url_template)
# {owner}/{repo} will be substituted at runtime
_MIRROR_TEMPLATES = [
    ("ghproxy",  "https://ghfast.top/https://raw.githubusercontent.com/{owner}/{repo}/main/info.json"),
    ("gitee",    "https://gitee.com/{owner}/{repo}/raw/main/info.json"),
    ("github",   "https://raw.githubusercontent.com/{owner}/{repo}/main/info.json"),
]

# Default GitHub repo for ETS_Auto
DEFAULT_OWNER = "yigenhuobah"
DEFAULT_REPO = "ETS_Auto"

# Timeout per mirror request (seconds)
_REQUEST_TIMEOUT = 8

# Local cache path (beside the exe or script)
_CACHE_FILENAME = "remote_info_cache.json"


class RemoteInfo:
    """Parsed result from remote info.json."""

    __slots__ = (
        'latest_version', 'min_version', 'allow_start',
        'force_update', 'update_available', 'announcement',
        'pk_extra_url', 'download_url', 'source', 'fetched_at',
    )

    def __init__(self, **kwargs):
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


def compare_versions(v1, v2):
    """Compare two semantic version strings.

    Returns:
        -1 if v1 < v2
         0 if v1 == v2
         1 if v1 > v2

    Pre-release versions (e.g. "0.5.1-beta") are ranked lower than
    the corresponding release version ("0.5.1"), following SemVer spec.

    Examples:
        compare_versions("0.5.0", "0.5.1")         → -1
        compare_versions("0.5.1", "0.5.0")         →  1
        compare_versions("0.5.1", "0.5.1")         →  0
        compare_versions("1.0", "0.9.9")           →  1
        compare_versions("0.5.1-beta", "0.5.1")    → -1
        compare_versions("0.5.1", "0.5.1-beta")    →  1
        compare_versions("0.5.1-beta", "0.5.0")    →  1
    """
    import re as _re

    def _parse(v):
        if not v or not v.strip():
            return ([0], False)
        # Split numeric version from pre-release suffix
        m = _re.match(r'^(\d+(?:\.\d+)*)', v.strip())
        if not m:
            return ([0], False)
        numeric_str = m.group(1)
        suffix = v.strip()[len(m.group(0)):].strip()
        is_prerelease = bool(suffix)
        parts = []
        for p in numeric_str.split('.'):
            parts.append(int(p) if p.isdigit() else 0)
        return (parts if parts else [0], is_prerelease)

    a, a_pre = _parse(v1)
    b, b_pre = _parse(v2)
    # Pad shorter list with zeros
    max_len = max(len(a), len(b))
    a += [0] * (max_len - len(a))
    b += [0] * (max_len - len(b))
    for x, y in zip(a, b):
        if x < y:
            return -1
        if x > y:
            return 1
    # Numeric parts equal — prerelease < release
    if a_pre and not b_pre:
        return -1
    if not a_pre and b_pre:
        return 1
    return 0


def classify_info(info):
    """Unified decision: classify RemoteInfo into block / warn / normal.

    Returns:
        (level: str, reason: str)
        level: "block" | "warn" | "normal"
        reason: human-readable explanation (empty for normal)
    """
    if info is None:
        return "normal", ""

    if not info.allow_start:
        return "block", "程序已被远程关闭"

    if info.force_update:
        return "block", "版本过低，请更新到 %s" % info.latest_version

    return "normal", ""


class ETSRemote:
    """Remote config checker for ETS_Auto.

    Checks remote info.json for version updates, forced update requirements,
    kill switch, and announcements. Falls back through multiple CDN mirrors.

    Args:
        current_version: Current app version string (e.g. "0.5.1")
        owner: GitHub repo owner
        repo: GitHub repo name
        timeout: Per-request timeout in seconds
    """

    def __init__(self, current_version="0.0.0", owner=DEFAULT_OWNER,
                 repo=DEFAULT_REPO, timeout=_REQUEST_TIMEOUT):
        self.current_version = current_version
        self.owner = owner
        self.repo = repo
        self.timeout = timeout
        self._cache_path = self._resolve_cache_path()
        self._last_info = None  # Cache last check result for download_pk_extra

    def _resolve_cache_path(self):
        """Resolve cache file path: beside exe (PyInstaller) or beside script."""
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, _CACHE_FILENAME)

    def _build_urls(self):
        """Build ordered list of mirror URLs to try."""
        urls = []
        for name, template in _MIRROR_TEMPLATES:
            url = template.format(owner=self.owner, repo=self.repo)
            urls.append((name, url))
        return urls

    def _fetch_json(self, url):
        """Fetch JSON from a single URL with timeout.

        Returns parsed dict or None on failure.
        """
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'ETS_Auto/%s' % self.current_version,
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return None
                raw = resp.read()
                return json.loads(raw.decode('utf-8'))
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, OSError, TimeoutError):
            return None

    def _save_cache(self, data):
        """Save fetched data to local cache file.

        Uses a wrapper structure to keep internal metadata (_fetched_at, _source)
        separate from the raw remote JSON data.
        """
        try:
            with open(self._cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass  # Cache write failure is non-critical

    def _load_cache(self):
        """Load last known remote info from cache.

        Returns parsed dict or None if cache is missing/invalid/expired.
        Cache is considered valid for 24 hours.
        Supports both the new wrapper format and the legacy flat format.
        """
        try:
            if not os.path.exists(self._cache_path):
                return None
            with open(self._cache_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)

            if not isinstance(raw, dict):
                return None

            # New wrapper format: {"data": {...}, "_fetched_at": ..., "_source": ...}
            if 'data' in raw and isinstance(raw.get('data'), dict):
                fetched = raw.get('_fetched_at', 0)
                if time.time() - fetched > 86400:
                    return None
                # Return in wrapper format so _parse_remote_info can extract
                return raw

            # Legacy flat format (migrate on read)
            fetched = raw.get('_fetched_at', 0)
            if time.time() - fetched > 86400:
                return None
            # Convert to wrapper format
            data = {k: v for k, v in raw.items() if not k.startswith('_')}
            return {
                'data': data,
                '_fetched_at': fetched,
                '_source': raw.get('_source', 'cache'),
            }
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def check(self, use_cache=True):
        """Check remote info.json for updates, kill switch, and announcements.

        Tries each mirror in order until one succeeds. Falls back to local
        cache if all mirrors fail.

        Args:
            use_cache: If True, return cached result when fresh and mirrors fail.

        Returns:
            RemoteInfo instance, or None if all sources failed and no cache.
        """
        urls = self._build_urls()
        data = None
        source = None

        for name, url in urls:
            result = self._fetch_json(url)
            if result is not None:
                data = result
                source = name
                break

        now = time.time()

        if data is not None:
            # Fresh data from mirror — wrap and cache
            cache_entry = {
                'data': data,
                '_fetched_at': now,
                '_source': source,
            }
            self._save_cache(cache_entry)
        elif use_cache:
            cache_entry = self._load_cache()
            if cache_entry is None:
                return None
            data = cache_entry.get('data', {})
            source = cache_entry.get('_source', 'cache')
            now = cache_entry.get('_fetched_at', now)
        else:
            return None

        info = self._parse_remote_info(data, source, now)
        self._last_info = info
        return info

    def _parse_remote_info(self, data, source, fetched_at):
        """Parse raw JSON dict into RemoteInfo with version logic applied."""
        latest = data.get('version', '0.0.0')
        min_ver = data.get('minVer', '0.0.0')
        allow_start = data.get('allowStart', True)
        announcement = data.get('announcement', '')
        pk_extra_url = data.get('pkExtraUrl', '')
        download_url = data.get('downloadUrl', '')

        # Version comparisons
        update_available = compare_versions(latest, self.current_version) > 0
        force_update = compare_versions(self.current_version, min_ver) < 0

        return RemoteInfo(
            latest_version=latest,
            min_version=min_ver,
            allow_start=allow_start,
            force_update=force_update,
            update_available=update_available,
            announcement=announcement,
            pk_extra_url=pk_extra_url,
            download_url=download_url,
            source=source,
            fetched_at=fetched_at,
        )

    def download_pk_extra(self, url=None, target_path=None):
        """Download updated pk_extra.json from remote URL.

        Args:
            url: Direct URL to pk_extra.json. If None, uses the pkExtraUrl
                 from the last successful check().
            target_path: Local file path. If None, defaults to pk_extra.json
                         beside the exe or script.

        Returns:
            (success: bool, message: str)
        """
        if url is None:
            # Use cached info from last check (no extra network call)
            if self._last_info is not None and self._last_info.pk_extra_url:
                url = self._last_info.pk_extra_url
            else:
                return False, "无法获取 pk_extra.json 下载地址"

        # Resolve target path
        if target_path is None:
            if getattr(sys, 'frozen', False):
                base = os.path.dirname(sys.executable)
            else:
                base = os.path.dirname(os.path.abspath(__file__))
            target_path = os.path.join(base, 'pk_extra.json')

        # Backup existing file
        backup_path = target_path + '.bak'
        if os.path.exists(target_path):
            try:
                shutil.copy2(target_path, backup_path)
            except OSError:
                pass  # Backup failure is non-critical

        # Build download URL list with mirror fallback
        download_urls = [url]
        if 'raw.githubusercontent.com' in url:
            download_urls.insert(0, url.replace(
                'https://raw.githubusercontent.com',
                'https://ghfast.top/https://raw.githubusercontent.com'))
            # Also try gitee mirror (same pattern as info.json)
            gitee_url = url.replace(
                'https://raw.githubusercontent.com',
                'https://gitee.com').replace('/main/', '/raw/main/')
            download_urls.append(gitee_url)

        for dl_url in download_urls:
            try:
                req = urllib.request.Request(dl_url, headers={
                    'User-Agent': 'ETS_Auto/%s' % self.current_version,
                })
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.status != 200:
                        continue
                    raw = resp.read()
                # Validate it's valid JSON
                json.loads(raw.decode('utf-8'))
                # Write to target
                with open(target_path, 'wb') as f:
                    f.write(raw)
                return True, "pk_extra.json 已更新 (%d bytes)" % len(raw)
            except (urllib.error.URLError, urllib.error.HTTPError,
                    json.JSONDecodeError, OSError, TimeoutError):
                continue

        # Restore backup if download failed
        if os.path.exists(backup_path):
            try:
                shutil.copy2(backup_path, target_path)
            except OSError:
                pass

        return False, "pk_extra.json 下载失败（所有镜像源均不可用）"


# ═══════════════════════════════════════════════════════════
#  CLI helpers (for GUI integration)
# ═══════════════════════════════════════════════════════════

def format_update_message(info, current_version=""):
    """Format a human-readable update/announcement message for GUI display.

    Returns None if there's nothing to show.
    Uses classify_info() to avoid duplicating block-logic.
    """
    if info is None:
        return None

    level, reason = classify_info(info)
    lines = []

    if level == "block":
        if info.force_update:
            lines.append("⚠️ 版本过低，必须更新！")
            lines.append("当前版本：%s → 最新版本：%s" % (current_version or "?", info.latest_version))
        else:
            lines.append("🚫 %s" % reason)
        if info.download_url:
            lines.append("下载地址：%s" % info.download_url)

    elif info.update_available:
        lines.append("🔄 发现新版本：%s（当前 %s）" % (info.latest_version, current_version or "?"))
        if info.download_url:
            lines.append("下载地址：%s" % info.download_url)

    if info.announcement:
        lines.append("")
        lines.append("📢 %s" % info.announcement)

    # Don't duplicate block reason in message — classify_info already handles it

    return '\n'.join(lines) if lines else None


def should_block_start(info):
    """Check if the app should be blocked from starting.

    Returns (blocked: bool, reason: str).
    Delegates to classify_info() for unified decision logic.
    """
    level, reason = classify_info(info)
    if level == "block":
        return True, reason
    return False, ""
