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
  from ets_remote import ETSRemote, should_block_start
  remote = ETSRemote(current_version="0.5.1")
  info = remote.check()
  # Prefer should_block_start / classify_info — unsigned kill-switch is warn-only
  blocked, reason = should_block_start(info)
  if blocked:
      print(reason)
"""
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from ets_pk_store import (
    PKExtraCorruptError,
    PKExtraStoreError,
    atomic_write_json as _store_atomic_write_json,
    filter_pk_extra_schema as _store_filter_pk_extra_schema,
    load_pk_extra as _store_load_pk_extra,
    merge_write_pk_extra,
)


# ── Mirror sources (ordered by priority for Chinese users) ──
# Each entry: (name, url_template)
# {owner}/{repo} will be substituted at runtime
_MIRROR_TEMPLATES = [
    ("ghproxy",  "https://ghfast.top/https://raw.githubusercontent.com/{owner}/{repo}/master/info.json"),
    ("gitee",    "https://gitee.com/{owner}/{repo}/raw/master/info.json"),
    ("github",   "https://raw.githubusercontent.com/{owner}/{repo}/master/info.json"),
]

# H18/H19: only these hosts may be contacted for info.json / pkExtraUrl.
# Rejects file://, arbitrary IPs, and random domains (supply-chain write path).
_ALLOWED_URL_HOSTS = frozenset({
    'raw.githubusercontent.com',
    'gitee.com',
    'ghfast.top',
    'github.com',  # downloadUrl display / release pages
})

# Remote bodies are always bounded before JSON decoding.
_INFO_MAX_BYTES = 256 * 1024
# Max body size for pk_extra.json download (bytes)
_PK_EXTRA_MAX_BYTES = 2 * 1024 * 1024

_CACHE_MAX_AGE = 24 * 60 * 60
_CACHE_FUTURE_SKEW = 5 * 60
_VERSION_MAX_CHARS = 128
_ANNOUNCEMENT_MAX_CHARS = 4096
_URL_MAX_CHARS = 2048
_SOURCE_MAX_CHARS = 64
_VERSION_RE = re.compile(
    r'(?P<core>\d+(?:\.\d+)*)'
    r'(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
    r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?')


# Default GitHub repo for ETS_Auto
DEFAULT_OWNER = "yigenhuobah"
DEFAULT_REPO = "ETS_Auto"

# Timeout per mirror request (seconds)
_REQUEST_TIMEOUT = 8

# Local cache path (beside the exe or script)
_CACHE_FILENAME = "remote_info_cache.json"


def is_url_allowed(url):
    """Return True only for https URLs whose host is on the remote allowlist.

    H18/H19: blocks file://, http://, non-allowlisted hosts, and empty/malformed
    URLs. Mirror hosts used by this module (ghfast.top, gitee.com,
    raw.githubusercontent.com) are explicitly permitted.
    """
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme != 'https':
        return False
    host = (parsed.hostname or '').lower()
    if not host or host not in _ALLOWED_URL_HOSTS:
        return False
    # Reject credentials in URL
    if parsed.username or parsed.password:
        return False
    return True


class _AllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject non-allowlisted redirect targets before urllib follows them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            redirect_url = urllib.parse.urljoin(req.full_url, newurl)
        except (TypeError, ValueError) as exc:
            raise urllib.error.URLError(
                'invalid redirect target: %s' % exc) from exc
        if not is_url_allowed(redirect_url):
            raise urllib.error.URLError(
                'redirect target is not allowlisted: %s' % redirect_url)
        return super().redirect_request(
            req, fp, code, msg, headers, redirect_url)


def _open_remote_url(request, timeout):
    """Open a remote request with per-hop redirect allowlist enforcement."""
    opener = urllib.request.build_opener(_AllowlistedRedirectHandler())
    return opener.open(request, timeout=timeout)


def _response_url_allowed(response, requested_url):
    """Validate the final URL after redirects.

    urllib responses return a string from geturl(). Lightweight test doubles
    often omit it (or expose a generic mock), so those fall back to the already
    validated request URL.
    """
    getter = getattr(response, 'geturl', None)
    if not callable(getter):
        return is_url_allowed(requested_url)
    try:
        final_url = getter()
    except Exception:
        return False
    if not isinstance(final_url, str):
        final_url = requested_url
    return is_url_allowed(final_url)


def _read_bounded_body(response, max_bytes):
    """Read at most ``max_bytes + 1`` while supporting no-arg test doubles."""
    try:
        raw = response.read(max_bytes + 1)
    except TypeError:
        raw = response.read()
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError('HTTP response body must be bytes')
    raw = bytes(raw)
    if len(raw) > max_bytes:
        raise ValueError('HTTP response body exceeds size limit')
    return raw


def _valid_cache_timestamp(value, now=None):
    """Return a trusted finite timestamp, otherwise ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        fetched = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(fetched):
        return None
    if now is None:
        now = time.time()
    if fetched > now + _CACHE_FUTURE_SKEW:
        return None
    if now - fetched > _CACHE_MAX_AGE:
        return None
    return fetched


def _text_field(value, default='', max_chars=None, truncate=False):
    if not isinstance(value, str):
        return default
    if max_chars is not None and len(value) > max_chars:
        return value[:max_chars] if truncate else default
    return value


def _version_field(value, default='0.0.0'):
    value = _text_field(value, default, max_chars=_VERSION_MAX_CHARS).strip()
    if not value or _VERSION_RE.fullmatch(value) is None:
        return default
    return value


def verify_remote_payload_integrity(data, signature_hex=None, public_key_pem=None):
    """OPEN-H4: optional integrity check for remote info.json / pk_extra.

    Modes (first match wins):
      1) ETS_REMOTE_HMAC set → require HMAC-SHA256 hex over canonical JSON
      2) ETS_REMOTE_PUBKEY / public_key_pem set → require Ed25519 signature
      3) neither configured → allowlist-only success (backward compatible)

    Canonical body: json.dumps(..., sort_keys=True, separators=(',', ':')).

    Returns (ok: bool, reason: str).
    """
    # Cheap exit: only serialize when a verifier is actually configured
    hmac_secret = os.environ.get('ETS_REMOTE_HMAC', '').strip()
    pem = public_key_pem
    if not pem:
        pem = os.environ.get('ETS_REMOTE_PUBKEY', '').strip()
        if pem and os.path.isfile(pem):
            try:
                with open(pem, 'r', encoding='utf-8') as f:
                    pem = f.read()
            except OSError:
                return False, 'cannot read ETS_REMOTE_PUBKEY file'
    if not hmac_secret and not pem:
        return True, 'no public key configured (allowlist only)'

    try:
        body = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        body_b = body.encode('utf-8')
    except Exception as e:
        return False, 'cannot serialize payload: %s' % e

    if hmac_secret:
        if not signature_hex:
            return False, 'missing remote signature'
        import hmac as _hmac
        import hashlib
        expected = _hmac.new(hmac_secret.encode('utf-8'), body_b, hashlib.sha256).hexdigest()
        sig = str(signature_hex).strip().lower()
        exp = expected.lower()
        # compare_digest(str,str) requires ASCII on CPython — reject non-ASCII early
        try:
            sig_b = sig.encode('ascii')
            exp_b = exp.encode('ascii')
        except UnicodeEncodeError:
            return False, 'HMAC signature not ASCII'
        if not _hmac.compare_digest(exp_b, sig_b):
            return False, 'HMAC signature mismatch'
        return True, 'hmac ok'

    if not signature_hex:
        return False, 'missing remote signature'

    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        import binascii
        pub = load_pem_public_key(pem.encode('utf-8') if isinstance(pem, str) else pem)
        if not isinstance(pub, Ed25519PublicKey):
            return False, 'public key is not Ed25519'
        sig = binascii.unhexlify(str(signature_hex).strip())
        pub.verify(sig, body_b)
        return True, 'ed25519 ok'
    except ImportError:
        return False, 'cryptography not installed and ETS_REMOTE_HMAC not set'
    except Exception as e:
        return False, 'signature verify failed: %s' % e


def _split_remote_signature(data):
    """Split optional signature/sig fields from a remote JSON object.

    Returns (payload_without_sig, signature_hex_or_None).
    Non-dict input is returned unchanged with no signature.
    """
    if not isinstance(data, dict):
        return data, None
    sig = data.get('signature') or data.get('sig')
    payload = {k: v for k, v in data.items() if k not in ('signature', 'sig')}
    return payload, sig


def _verify_remote_dict(data):
    """Verify a remote JSON dict the same way as check() / download_pk_extra.

    Returns (ok: bool, reason: str, payload).
    payload has signature/sig keys stripped when data is a dict.
    When no integrity keys are configured, ok is always True (allowlist only).
    """
    payload, sig = _split_remote_signature(data)
    ok, why = verify_remote_payload_integrity(payload, signature_hex=sig)
    return ok, why, payload


def resolve_pk_extra_path(filename='pk_extra.json'):
    """Resolve and migrate frozen ``pk_extra`` plus its backup."""
    from ets_common import migrate_legacy_user_data_family
    return migrate_legacy_user_data_family(filename, anchor_file=__file__)


def _filter_pk_extra_schema(data):
    """Compatibility wrapper around the shared pk_extra schema filter."""
    return _store_filter_pk_extra_schema(data)


def _load_local_pk_extra(path):
    """Load existing local pk_extra.json; return {} if missing/invalid.

    Prefer _load_local_pk_extra_status() when callers must distinguish
    missing vs corrupt (download merge must not wipe a corrupt local file).
    """
    data, _status = _load_local_pk_extra_status(path)
    return data


def _load_local_pk_extra_status(path):
    """Load through the shared lock, including healthy ``.bak`` recovery."""
    data, status = _store_load_pk_extra(path)
    if status in ('restored', 'backup'):
        status = 'ok'
    return data, status


def _merge_pk_extra(local_data, remote_data):
    """Merge remote pk_extra into local learn map (H16).

    Policy (documented choice):
      - Remote keys upsert (remote wins on conflict — intentional hot-update).
      - Local-only keys are preserved (do not wipe self-learned mappings).
      - Pure remote wipe is intentionally avoided so offline learn_miss results
        survive a remote refresh.
    """
    merged = dict(local_data) if local_data else {}
    if remote_data:
        merged.update(remote_data)
    return merged


def _atomic_write_json(path, data):
    """Compatibility wrapper for cache writes and older callers."""
    return _store_atomic_write_json(path, data)


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
    def _parse(v):
        if not isinstance(v, str):
            return ([0], None)
        text = v.strip()
        if not text or len(text) > _VERSION_MAX_CHARS:
            return ([0], None)
        match = _VERSION_RE.fullmatch(text)
        if match is None:
            return ([0], None)
        try:
            parts = [int(component) for component in match.group('core').split('.')]
        except (OverflowError, ValueError):
            return ([0], None)
        prerelease = match.group('pre')
        if prerelease is None:
            return (parts, None)
        identifiers = []
        for identifier in prerelease.split('.'):
            if identifier.isdigit():
                identifiers.append((0, int(identifier)))
            else:
                identifiers.append((1, identifier))
        return (parts, tuple(identifiers))


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
    # Numeric core equal: a release outranks any prerelease.
    if a_pre is None and b_pre is None:
        return 0
    if a_pre is not None and b_pre is None:
        return -1
    if a_pre is None and b_pre is not None:
        return 1

    for left, right in zip(a_pre, b_pre):
        if left < right:
            return -1
        if left > right:
            return 1
    if len(a_pre) < len(b_pre):
        return -1
    if len(a_pre) > len(b_pre):
        return 1
    return 0


def _remote_integrity_configured():
    """True when HMAC or Ed25519 pubkey env is set (hard verify mode)."""
    return bool(
        os.environ.get('ETS_REMOTE_HMAC', '').strip()
        or os.environ.get('ETS_REMOTE_PUBKEY', '').strip()
    )


def classify_info(info):
    """Unified decision: classify RemoteInfo into block / warn / normal.

    Unsigned mode (no ETS_REMOTE_HMAC / ETS_REMOTE_PUBKEY):
      allowStart:false and force_update become **warn** (fail-open), not hard
      block — unauthenticated remote JSON must not DoS local clients.
    Signed mode (keys configured): keep hard **block** (fail-closed).

    Returns:
        (level: str, reason: str)
        level: "block" | "warn" | "normal"
        reason: human-readable explanation (empty for normal)
    """
    if info is None:
        return "normal", ""

    if not info.allow_start:
        msg = "程序已被远程关闭"
        return ("block" if _remote_integrity_configured() else "warn"), msg

    if info.force_update:
        msg = "版本过低，请更新到 %s" % info.latest_version
        return ("block" if _remote_integrity_configured() else "warn"), msg

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
        """Resolve the cache and migrate a frozen executable sidecar.

        Absolute _CACHE_FILENAME (used by unit tests) is honored as-is.
        """
        name = _CACHE_FILENAME
        if os.path.isabs(name):
            return name  # unit tests may override with an absolute path
        from ets_common import migrate_legacy_user_data_file
        return migrate_legacy_user_data_file(name, anchor_file=__file__)

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
        H18: only allowlisted https hosts are contacted.
        """
        if not is_url_allowed(url):
            return None
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'ETS_Auto/%s' % self.current_version,
                'Accept': 'application/json',
            })
            with _open_remote_url(req, timeout=self.timeout) as resp:
                if not _response_url_allowed(resp, url):
                    return None
                if resp.status != 200:
                    return None
                raw = _read_bounded_body(resp, _INFO_MAX_BYTES)
            parsed = json.loads(raw.decode('utf-8'))
            return parsed if isinstance(parsed, dict) else None
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, OSError, TimeoutError, TypeError,
                UnicodeDecodeError, ValueError, RecursionError):
            return None

    def _save_cache(self, data):
        """Save fetched data to local cache file (atomic temp + replace).

        Uses a wrapper structure to keep internal metadata (_fetched_at, _source)
        separate from the raw remote JSON data.
        """
        try:
            _atomic_write_json(self._cache_path, data)
        except (OSError, TypeError, ValueError, RecursionError):
            # Cache is best-effort: never abort remote check() for write/serialize
            pass

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

            now = time.time()
            # New wrapper format: {"data": {...}, "_fetched_at": ..., "_source": ...}
            if 'data' in raw and isinstance(raw.get('data'), dict):
                fetched = _valid_cache_timestamp(
                    raw.get('_fetched_at'), now=now)
                if fetched is None:
                    return None
                # Return in wrapper format so _parse_remote_info can extract
                raw['_fetched_at'] = fetched
                return raw

            # Legacy flat format (migrate on read)
            fetched = _valid_cache_timestamp(raw.get('_fetched_at'), now=now)
            if fetched is None:
                return None
            # Convert to wrapper format
            data = {k: v for k, v in raw.items() if not k.startswith('_')}
            return {
                'data': data,
                '_fetched_at': fetched,
                '_source': raw.get('_source', 'cache'),
            }
        except (OSError, json.JSONDecodeError, ValueError, RecursionError):
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
            # OPEN-H4: optional signature field on payload
            ok_int, why, payload = _verify_remote_dict(data)
            if not ok_int:
                # Reject poisoned remote; fall through to cache if allowed
                print("[Remote] integrity reject (live): %s" % why)
                data = None
                source = None
                if not use_cache:
                    return None
            else:
                # Persist full signed payload (incl. signature/sig) so cache
                # can be re-verified later when integrity keys are configured.
                cache_entry = {
                    'data': data if isinstance(data, dict) else payload,
                    '_fetched_at': now,
                    '_source': source,
                    '_integrity': why,
                }
                self._save_cache(cache_entry)
                # Use stripped payload for schema parse
                data = payload
        if data is None and use_cache:
            cache_entry = self._load_cache()
            if cache_entry is None:
                return None
            cached_data = cache_entry.get('data', {})
            # Fail-closed for kill-switch: when HMAC/pubkey is configured,
            # re-verify the cache payload instead of trusting it blindly.
            # Without keys configured this is a no-op allowlist-only success.
            ok_cache, why_cache, cached_payload = _verify_remote_dict(cached_data)
            if not ok_cache:
                print("[Remote] integrity reject (cache): %s" % why_cache)
                return None
            data = cached_payload
            source = cache_entry.get('_source', 'cache')
            now = cache_entry.get('_fetched_at', now)
        elif data is None:
            return None

        if not isinstance(data, dict):
            return None
        info = self._parse_remote_info(data, source, now)
        self._last_info = info
        return info

    def _parse_remote_info(self, data, source, fetched_at):
        """Parse raw JSON dict into RemoteInfo with version logic applied.

        H18/H19: pkExtraUrl / downloadUrl that fail the host allowlist are
        cleared (empty string) so callers never act on disallowed hosts.
        """
        latest = _version_field(data.get('version'))
        min_ver = _version_field(data.get('minVer'))
        raw_allow_start = data.get('allowStart', True)
        allow_start = raw_allow_start if isinstance(raw_allow_start, bool) else True
        announcement = _text_field(
            data.get('announcement'), max_chars=_ANNOUNCEMENT_MAX_CHARS, truncate=True)
        pk_extra_url = _text_field(data.get('pkExtraUrl'), max_chars=_URL_MAX_CHARS)
        download_url = _text_field(data.get('downloadUrl'), max_chars=_URL_MAX_CHARS)

        # H19: drop non-allowlisted pkExtraUrl (supply-chain write path)
        if pk_extra_url and not is_url_allowed(pk_extra_url):
            pk_extra_url = ''
        # downloadUrl is display-only; still strip disallowed schemes/hosts
        if download_url and not is_url_allowed(download_url):
            download_url = ''

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
            source=_text_field(source, 'unknown', max_chars=_SOURCE_MAX_CHARS),
            fetched_at=fetched_at,
        )

    def download_pk_extra(self, url=None, target_path=None):
        """Download updated pk_extra.json from remote URL.

        H12: default target_path matches ETSWordPK._exe_dir_path
             (project root in dev; beside exe when frozen).
        H16: schema validate dict[str,str]; merge remote into local
             (remote upserts; keep local-only learn keys); atomic write.
        H19: only allowlisted https hosts; reject file:// and random hosts.
        H17: race with GUI worker is owned by GUI (late block does not stop
             an already-running worker); this method only hardens download.

        Offline: on total download failure, existing local file is left
        untouched (no wipe). Offline cache for info.json is unchanged.

        Args:
            url: Direct URL to pk_extra.json. If None, uses the pkExtraUrl
                 from the last successful check().
            target_path: Local file path. If None, uses resolve_pk_extra_path().

        Returns:
            (success: bool, message: str)
        """
        if url is None:
            # Use cached info from last check (no extra network call)
            if self._last_info is not None and self._last_info.pk_extra_url:
                url = self._last_info.pk_extra_url
            else:
                return False, "无法获取 pk_extra.json 下载地址"

        if not is_url_allowed(url):
            return False, "pkExtraUrl 主机不在允许列表中（拒绝下载）"

        # H12: same path semantics as ets_word_pk._exe_dir_path
        if target_path is None:
            target_path = resolve_pk_extra_path('pk_extra.json')

        # Validate/recover before network I/O. The final commit re-reads again
        # under the same process-wide path lock, so this is not a stale snapshot.
        _local_data, local_status = _load_local_pk_extra_status(target_path)
        if local_status == 'invalid':
            return False, (
                "本地 pk_extra.json 损坏/格式无效，且没有健康备份；"
                "已拒绝远程覆盖以免丢失学习记录（可手动删除后重试）")

        # Build download URL list with mirror fallback (all must pass allowlist)
        download_urls = [url]
        if 'raw.githubusercontent.com' in url:
            ghfast_url = url.replace(
                'https://raw.githubusercontent.com',
                'https://ghfast.top/https://raw.githubusercontent.com')
            gitee_url = url.replace(
                'https://raw.githubusercontent.com', 'https://gitee.com')
            gitee_url = gitee_url.replace(
                '/main/', '/raw/main/').replace(
                '/master/', '/raw/master/')
            # Prefer Chinese mirror first
            download_urls = [ghfast_url, url, gitee_url]

        # Dedup while preserving order; drop non-allowlisted mirrors
        seen = set()
        filtered_urls = []
        for dl_url in download_urls:
            if dl_url in seen:
                continue
            seen.add(dl_url)
            if is_url_allowed(dl_url):
                filtered_urls.append(dl_url)
        if not filtered_urls:
            return False, "pkExtraUrl 主机不在允许列表中（拒绝下载）"

        for dl_url in filtered_urls:
            try:
                req = urllib.request.Request(dl_url, headers={
                    'User-Agent': 'ETS_Auto/%s' % self.current_version,
                    'Accept': 'application/json',
                })
                with _open_remote_url(req, timeout=self.timeout) as resp:
                    if not _response_url_allowed(resp, dl_url):
                        continue
                    if resp.status != 200:
                        continue
                    # Bound body size (supply-chain / DoS)
                    raw = _read_bounded_body(resp, _PK_EXTRA_MAX_BYTES)
                remote_obj = json.loads(raw.decode('utf-8'))
                # OPEN-H4: verify signed pk_extra the same way as check()
                # when ETS_REMOTE_HMAC / ETS_REMOTE_PUBKEY is set; allowlist
                # only (ok) when neither is configured.
                ok_int, _why_int, remote_payload = _verify_remote_dict(remote_obj)
                if not ok_int:
                    continue
                remote_data = _filter_pk_extra_schema(remote_payload)
                if remote_data is None:
                    # Invalid top-level schema — try next mirror
                    continue
                # H16: merge — remote updates; preserve local-only learn keys
                try:
                    merged = merge_write_pk_extra(
                        target_path, remote_data, backup_existing=True)
                except PKExtraCorruptError:
                    return False, (
                        "本地 pk_extra.json 在下载期间损坏，且没有健康备份；"
                        "已拒绝远程覆盖以免丢失学习记录")
                except (PKExtraStoreError, OSError) as exc:
                    return False, (
                        "pk_extra.json 已下载，但保存失败：%s" % exc)
                local_only = sum(1 for key in merged if key not in remote_data)
                return True, "pk_extra.json 已更新（merge %d remote + %d local-only → %d keys）" % (
                    len(remote_data),
                    local_only,
                    len(merged),
                )
            except (urllib.error.URLError, urllib.error.HTTPError,
                    json.JSONDecodeError, OSError, TimeoutError, ValueError,
                    UnicodeDecodeError, TypeError, RecursionError):
                continue

        # Download failed: leave existing local file intact (offline cache safe).
        # Do NOT restore backup over a still-valid local file that we never
        # truncated (atomic write either fully replaced or left original).
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
    cur = current_version or "?"
    latest = info.latest_version or "?"

    if level in ("block", "warn") and reason:
        # Shared body for hard-block vs unsigned advisory (wording only differs)
        if info.force_update:
            if level == "block":
                lines.append("⚠️ 版本过低，必须更新！")
                lines.append("当前版本：%s → 最新版本：%s" % (cur, latest))
            else:
                lines.append("⚠️ 版本过低，建议更新到 %s（未配置远程签名，仅提示）" % latest)
                lines.append("当前版本：%s" % cur)
        elif level == "block":
            lines.append("🚫 %s" % reason)
        else:
            lines.append("⚠️ %s（未配置远程签名，仅提示不拦截）" % reason)
        if info.download_url:
            lines.append("下载地址：%s" % info.download_url)

    elif info.update_available:
        lines.append("🔄 发现新版本：%s（当前 %s）" % (latest, cur))
        if info.download_url:
            lines.append("下载地址：%s" % info.download_url)

    if info.announcement:
        lines.append("")
        lines.append("📢 %s" % info.announcement)

    return '\n'.join(lines) if lines else None


def should_block_start(info):
    """Check if the app should be blocked from starting.

    Returns (blocked: bool, reason: str).
    Delegates to classify_info() for unified decision logic.

    H17 note: this is a pure decision helper. The GUI race (user can Start
    while remote check is still in flight; late allowStart=false does not
    stop an already-running worker) is owned by ets_gui.py — not fixed here.
    """
    level, reason = classify_info(info)
    if level == "block":
        return True, reason
    return False, ""
