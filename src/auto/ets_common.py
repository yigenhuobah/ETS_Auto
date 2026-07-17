#!/usr/bin/env python3
"""
ETS Common — Shared CDP connection and utilities for ETS automation.

Provides ETSBase class with:
  - CDP tab discovery and WebSocket connection
  - JavaScript evaluation via Runtime.evaluate (with event filtering)
  - Debug logging helper
  - JS string escaping utility
"""
import shutil
import tempfile
import socket
import json, time, urllib.request, websocket, os, sys, threading
import urllib.error
from urllib.parse import urlparse

# Single source of truth for app version (imported by exam/PK/GUI/remote)
APP_VERSION = "0.7.1"


def is_loopback_ws_url(ws_url):
    """Return True if webSocketDebuggerUrl targets a local loopback host.

    CDP attach is a high-privilege local control plane; reject non-loopback
    hosts even if the /json listing somehow points elsewhere.
    """
    if not ws_url or not isinstance(ws_url, str):
        return False
    try:
        parsed = urlparse(ws_url.strip())
    except Exception:
        return False
    if parsed.scheme not in ('ws', 'wss'):
        return False
    host = (parsed.hostname or '').lower()
    if not host:
        return False
    if '%' in host:
        host = host.split('%', 1)[0]
    if host == 'localhost':
        return True
    try:
        import ipaddress
        addr = ipaddress.ip_address(host)
        if addr.is_loopback:
            return True
        # IPv4-mapped IPv6 (::ffff:127.0.0.1) is not .is_loopback in stdlib
        mapped = getattr(addr, 'ipv4_mapped', None)
        return bool(mapped is not None and mapped.is_loopback)
    except ValueError:
        return False


def _close_failed_websocket(ws):
    """Immediately release a WebSocket that did not complete its handshake."""
    if ws is None:
        return
    try:
        shutdown = getattr(ws, 'shutdown', None)
        if callable(shutdown):
            shutdown()
        else:
            close = getattr(ws, 'close', None)
            if callable(close):
                close()
    except Exception:
        pass


def _connect_local_cdp_websocket(ws_url, timeout, ws_factory=None,
                                 socket_factory=None):
    """Attach to loopback CDP without consulting proxy environment variables.

    websocket-client reads HTTP_PROXY even for WebSocket handshakes.  Supplying
    a pre-connected stream bypasses that proxy path.  Redirect following is
    disabled and the resulting handshake must be HTTP 101; websocket-client
    otherwise treats a 3xx response as connected when redirect_limit is 0.
    """
    clean_url = ws_url.strip() if isinstance(ws_url, str) else ''
    try:
        parsed = urlparse(clean_url)
        host = (parsed.hostname or '').lower()
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid CDP WebSocket endpoint') from exc
    if (parsed.scheme != 'ws' or parsed.username or parsed.password
            or not host or port is None or not 1 <= port <= 65535
            or not is_loopback_ws_url(ws_url)):
        raise ValueError(
            'CDP WebSocket endpoint must be loopback ws:// with an explicit port')

    # Do not trust name resolution for the special localhost spelling.  Numeric
    # loopback literals are already verified by is_loopback_ws_url().
    connect_host = '127.0.0.1' if host == 'localhost' else host
    ws_factory = ws_factory or websocket.create_connection
    socket_factory = socket_factory or socket.create_connection
    raw_socket = None
    ws = None
    try:
        raw_socket = socket_factory(
            (connect_host, port), timeout=timeout)
        if raw_socket is None:
            raise ConnectionError('direct CDP socket factory returned no socket')
        ws = ws_factory(
            clean_url,
            timeout=timeout,
            socket=raw_socket,
            redirect_limit=0,
        )
        response = getattr(ws, 'handshake_response', None)
        status = getattr(response, 'status', None)
        if status != 101:
            raise websocket.WebSocketException(
                'CDP WebSocket handshake returned HTTP %r; redirects are not allowed'
                % (status,))
        return ws
    except Exception:
        _close_failed_websocket(ws)
        if raw_socket is not None:
            try:
                raw_socket.close()
            except Exception:
                pass
        raise


def is_ets_page_url(page_url):
    """Return True only for HTTP(S) pages on ets100.com or its subdomains."""
    if not page_url or not isinstance(page_url, str):
        return False
    try:
        parsed = urlparse(page_url.strip())
    except Exception:
        return False
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
        return False
    host = (parsed.hostname or '').lower().rstrip('.')
    return host == 'ets100.com' or host.endswith('.ets100.com')


def normalize_ets_content(data):
    """Return a shallow-normalized ETS content object, or None if unusable.

    Cached content is external input. One malformed section must not crash the
    offline browser or discard every healthy answer section in the same set.
    """
    if not isinstance(data, dict):
        return None
    info = data.get('info')
    if not isinstance(info, dict):
        return None

    normalized = dict(data)
    structure_type = data.get('structure_type', 'unknown')
    if not isinstance(structure_type, str) or not structure_type:
        structure_type = 'unknown'
    normalized['structure_type'] = structure_type

    normalized_info = dict(info)
    normalized_info['stid'] = str(info.get('stid') or '')
    for field in ('xtlist', 'std', 'question'):
        raw_items = info.get(field, [])
        if not isinstance(raw_items, list):
            raw_items = []
        items = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            nested_field = 'xxlist' if field == 'xtlist' else (
                'std' if field == 'question' else None)
            if nested_field is not None:
                nested = raw_item.get(nested_field, [])
                if not isinstance(nested, list):
                    nested = []
                item[nested_field] = [
                    value for value in nested if isinstance(value, dict)
                ]
            items.append(item)
        normalized_info[field] = items
    normalized['info'] = normalized_info
    return normalized


def _read_limited_response(response, max_bytes):
    """Read at most max_bytes from an urllib response, raising if oversized."""
    try:
        raw = response.read(max_bytes + 1)
    except TypeError:
        # Compatibility with small test doubles that implement read() only.
        raw = response.read()
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("CDP /json response must be bytes")
    if len(raw) > max_bytes:
        raise ValueError("CDP /json response exceeds %d bytes" % max_bytes)
    return bytes(raw)

class _RejectCDPRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse every CDP redirect before urllib can contact another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers
        raise urllib.error.URLError(
            'CDP endpoint redirects are not allowed: %s' % newurl)


def _open_local_cdp_url(url, timeout):
    """Open a loopback HTTP endpoint with proxies and redirects disabled."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid CDP endpoint') from exc
    if (parsed.scheme != 'http' or parsed.username or parsed.password
            or not host or port is None):
        raise ValueError('CDP endpoint must be loopback HTTP with an explicit port')
    try:
        import ipaddress
        loopback = host == 'localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == 'localhost'
    if not loopback:
        raise ValueError('CDP endpoint must use a loopback host')

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectCDPRedirectHandler(),
    )
    return opener.open(url, timeout=timeout)



def _user_data_basename(filename):
    """Return a jailed basename for a user-data filename."""
    raw = str(filename or '')
    raw = raw.replace(chr(92), '/').rstrip('/')
    name = os.path.basename(raw)
    if not name or name in ('.', '..'):
        return 'data.bin'
    return name


def _configured_user_data_root():
    """Return the explicit user-data override, or an empty string."""
    override = os.environ.get('ETS_AUTO_DATA_DIR', '').strip()
    if not override:
        return ''
    return os.path.abspath(os.path.expanduser(os.path.expandvars(override)))


def _frozen_user_data_root():
    """Resolve the per-user state directory for a frozen application."""
    local_appdata = os.environ.get('LOCALAPPDATA', '').strip()
    if local_appdata:
        return os.path.abspath(os.path.join(local_appdata, 'ETS_Auto'))
    appdata = os.environ.get('APPDATA', '').strip()
    if appdata:
        return os.path.abspath(os.path.join(appdata, 'ETS_Auto'))
    return os.path.abspath(os.path.join(os.path.expanduser('~'), '.ets_auto'))


def user_data_path(filename, anchor_file=None):
    """Resolve an app-owned user-data path without touching the filesystem.

    Shared by PK (pk_extra/misses), remote (pk_extra download), and exam stats.
    ``ETS_AUTO_DATA_DIR`` is an explicit override. Otherwise frozen builds use
    a per-user directory and source runs retain the historical project root.
    Only the basename is used, so ``filename`` cannot escape the selected root.
    """
    name = _user_data_basename(filename)
    configured_root = _configured_user_data_root()
    if configured_root:
        base = configured_root
    elif getattr(sys, 'frozen', False):
        base = _frozen_user_data_root()
    else:
        anchor = anchor_file or __file__
        base = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(anchor))))
    return os.path.join(base, name)


def ensure_parent_dir(path):
    """Create and return the parent directory for a pending file write."""
    parent = os.path.dirname(os.path.abspath(os.fspath(path))) or '.'
    os.makedirs(parent, exist_ok=True)
    return parent


def _is_readable_file(path):
    try:
        with open(path, 'rb'):
            return True
    except OSError:
        return False


def _is_readable_json_mapping(path):
    """Return True when ``path`` is a readable JSON object."""
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            return isinstance(json.load(stream), dict)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        return False


def _same_path(left, right):
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
            os.path.abspath(right))
    except (OSError, ValueError):
        return False


def _migration_lock_target(target):
    """Use the primary filename as the lock key for a ``.bak`` family."""
    if target.lower().endswith('.bak'):
        return target[:-4]
    return target


def _publish_temp_without_replace(tmp_path, target):
    """Atomically publish a completed temp file only when target is absent."""
    if os.name == 'nt':
        # Windows rename is atomic within one directory and refuses an
        # existing destination, unlike POSIX os.rename.
        os.rename(tmp_path, target)
        return
    # POSIX link is an atomic create-if-absent operation. The temporary link is
    # removed by the caller after the target name exists.
    os.link(tmp_path, target)


def _copy_legacy_file_atomic(legacy, target):
    """Copy to a hidden same-directory temp file, then publish atomically."""
    if os.path.lexists(target):
        return False

    directory = ensure_parent_dir(target)
    fd = -1
    tmp_path = ''
    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix='.%s.' % os.path.basename(target),
            suffix='.migrate',
            dir=directory,
        )
        with open(legacy, 'rb') as source, os.fdopen(fd, 'wb') as destination:
            fd = -1
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        _publish_temp_without_replace(tmp_path, target)
        if os.name == 'nt':
            # rename consumed the temporary path.
            tmp_path = ''
        return True
    except FileExistsError:
        return False
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _migrate_locked(legacy, target):
    """Migrate one file while the caller holds the family lock."""
    if os.path.lexists(target) or not os.path.isfile(legacy):
        return target
    _copy_legacy_file_atomic(legacy, target)
    return target


def migrate_legacy_user_data_file(filename, anchor_file=None):
    """Copy a legacy frozen sidecar into the current user-data directory.

    Source runs never migrate. Frozen runs copy a readable file located next
    to the executable only when the new target is absent. The legacy source is
    preserved and an existing target is never overwritten. If migration cannot
    complete, the readable legacy path is returned so existing data remains
    usable for that run. Directory creation occurs here, never during ordinary
    path resolution.
    """
    target = user_data_path(filename, anchor_file=anchor_file)
    if not getattr(sys, 'frozen', False):
        return target

    name = _user_data_basename(filename)
    legacy = os.path.join(os.path.dirname(sys.executable), name)
    if (_same_path(legacy, target) or os.path.lexists(target)
            or not _is_readable_file(legacy)):
        return target

    try:
        from ets_pk_store import interprocess_path_lock, path_lock

        lock_target = _migration_lock_target(target)
        with path_lock(lock_target), interprocess_path_lock(
                lock_target, create_parent=True):
            _migrate_locked(legacy, target)
        return target
    except (ImportError, OSError):
        return legacy if _is_readable_file(legacy) else target


def _family_migration_result(target, legacy, target_backup, legacy_backup):
    """Choose the base whose JSON family still contains a healthy mapping."""
    if (_is_readable_json_mapping(target)
            or _is_readable_json_mapping(target_backup)):
        return target
    if (_is_readable_json_mapping(legacy)
            or _is_readable_json_mapping(legacy_backup)):
        # Returning the legacy primary base lets the store discover a healthy
        # ``legacy + '.bak'`` without overwriting a corrupt current family.
        return legacy
    return target


def migrate_legacy_user_data_family(filename, anchor_file=None):
    """Migrate a primary frozen sidecar together with its ``.bak`` file.

    Both files share the primary file's in-process and interprocess locks, so
    cooperating readers cannot observe a half-migrated family. A backup-only
    legacy install remains recoverable. If a new family cannot be completed,
    its legacy primary base path is returned so callers can still find the
    legacy ``.bak`` file.
    """
    name = _user_data_basename(filename)
    target = user_data_path(name, anchor_file=anchor_file)
    if not getattr(sys, 'frozen', False):
        return target

    legacy = os.path.join(os.path.dirname(sys.executable), name)
    target_backup = target + '.bak'
    legacy_backup = legacy + '.bak'
    if _same_path(legacy, target):
        return target

    primary_preexisting = os.path.lexists(target)
    primary_needed = (
        not primary_preexisting and _is_readable_file(legacy))
    backup_needed = (
        not os.path.lexists(target_backup)
        and _is_readable_file(legacy_backup))
    if not primary_needed and not backup_needed:
        return _family_migration_result(
            target, legacy, target_backup, legacy_backup)

    try:
        from ets_pk_store import interprocess_path_lock, path_lock

        with path_lock(target), interprocess_path_lock(
                target, create_parent=True):
            try:
                _migrate_locked(legacy, target)
            except OSError:
                pass
            try:
                _migrate_locked(legacy_backup, target_backup)
            except OSError:
                # A failed backup migration may require a legacy-family
                # fallback even when the primary copy already completed.
                pass
    except (ImportError, OSError):
        pass
    return _family_migration_result(
        target,
        legacy,
        target_backup,
        legacy_backup,
    )


def constrain_ets_data_root(path, appdata=None):
    """Normalize Pinia/appDataPath to the %APPDATA%\\ETS data root (or None).

    - Accepts Roaming (or similar) without ETS leaf, or any path under ETS.
    - realpath + commonpath jail: must stay under APPDATA\\ETS.
    - Always returns the canonical ETS root (not a subdirectory), or None
      if empty/unsafe/escapes jail.
    """
    if path is None:
        return None
    raw = str(path).strip()
    if not raw:
        return None
    raw = raw.replace('\\', '/').rstrip('/')
    parts = [p for p in raw.split('/') if p]
    if not any(p.upper() == 'ETS' for p in parts):
        # appDataPath often points at Roaming without ETS leaf
        raw = raw + '/ETS'
    candidate = raw.replace('/', os.sep)
    app = appdata if appdata is not None else os.environ.get('APPDATA', '')
    if not app:
        return None
    jail = os.path.realpath(os.path.join(app, 'ETS'))
    try:
        resolved = os.path.realpath(os.path.abspath(candidate))
    except (OSError, ValueError):
        return None
    try:
        common = os.path.commonpath([jail, resolved])
    except ValueError:
        return None
    if common != jail:
        return None
    # Data root is always APPDATA\\ETS, never a random subfolder under it
    return jail


def force_utf8_stdio(line_buffering=False):
    """Force stdout/stderr to UTF-8 on Windows to avoid GBK encoding errors.

    Call this at module level or in __main__ to ensure IPA, CJK and other
    non-ASCII characters print correctly in Windows terminals.

    Args:
        line_buffering: If True, use line-buffered stdout (for subprocess pipes).
    """
    if sys.platform != 'win32':
        return
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace',
                               line_buffering=line_buffering)
        sys.stderr.reconfigure(encoding='utf-8', errors='replace',
                               line_buffering=line_buffering)
    except (AttributeError, LookupError):
        pass


class ETSBase:
    """Base class for ETS automation scripts.

    Handles Chrome DevTools Protocol (CDP) connection to the ETS PC client
    and provides common utilities shared by exam and PK scripts.

    Callback hooks (set via on_* methods):
      on_connect   — fn(instance) after CDP connection established
      on_question  — fn(info_dict) real-time question/answer display
      on_complete  — fn(stats_dict) when automation finishes
      on_error     — fn(error_msg) on non-fatal errors
    """

    def __init__(self, port=10086, debug_mode=False, stop_event=None):
        self.port = port
        self.ws = None
        self.mid = 0
        self.debug_mode = debug_mode
        self.tab = None
        # When set, interruptible_sleep raises InterruptedError.
        # Exam/PK call ensure_stop_event() so CLI always has a real Event.
        self.stop_event = stop_event
        # Callback hooks
        self._on_connect = None
        self._on_question = None
        self._on_complete = None
        self._on_error = None

    def ensure_stop_event(self):
        """Install a threading.Event if stop_event is still None."""
        if self.stop_event is None:
            self.stop_event = threading.Event()
        return self.stop_event

    def signal_stop(self):
        """Set stop_event if present (safe no-op when None)."""
        if self.stop_event is not None:
            self.stop_event.set()

    def _drop_connection(self):
        """Close socket and clear tab so callers never see a half-open pair."""
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = None
        self.tab = None

    # ── Callback registration ─────────────────────────────────

    def on_connect(self, fn):
        """Register callback: fn(instance). Called after CDP connection established."""
        self._on_connect = fn

    def on_question(self, fn):
        """Register callback: fn(info_dict). Called per question for real-time display.

        info_dict typically contains: type, type_label, qid, answer, answered, total.
        """
        self._on_question = fn

    def on_complete(self, fn):
        """Register callback: fn(stats_dict). Called when automation finishes."""
        self._on_complete = fn

    def on_error(self, fn):
        """Register callback: fn(error_msg). Called on non-fatal errors."""
        self._on_error = fn

    def _fire_connect(self):
        """Fire on_connect callback if registered."""
        if self._on_connect:
            try:
                self._on_connect(self)
            except Exception as e:
                self.debug("on_connect callback error: %s" % e)

    def _fire_question(self, info):
        """Fire on_question callback if registered."""
        if self._on_question:
            try:
                self._on_question(info)
            except Exception as e:
                self.debug("on_question callback error: %s" % e)

    def _fire_complete(self, stats):
        """Fire on_complete callback if registered."""
        if self._on_complete:
            try:
                self._on_complete(stats)
            except Exception as e:
                self.debug("on_complete callback error: %s" % e)

    def _fire_error(self, msg):
        """Fire on_error callback if registered."""
        if self._on_error:
            try:
                self._on_error(msg)
            except Exception as e:
                self.debug("on_error callback error: %s" % e)

    def reconnect_control(self, consecutive_conn_errors, *, post_ok=None,
                          label='', max_errors=3, sleep_ok=1.0, sleep_fail=1.0):
        """Shared reconnect shell for exam/RW/PK loops.

        Returns 'continue' | 'break'. post_ok is optional callable() -> bool;
        if it returns False after a successful reconnect(), the loop breaks.
        """
        self.debug("%s connection error #%d" % (label or 'CDP', consecutive_conn_errors))
        if consecutive_conn_errors >= max_errors:
            print("\n%s: Connection lost repeatedly, stopping." % (label or 'Connection'))
            return 'break'
        print("\n%s: Connection lost. Reconnecting (%d/%d)..." % (
            label or 'CDP', consecutive_conn_errors, max_errors))
        try:
            self.reconnect()
            if post_ok is not None and not post_ok():
                print("%s: post-reconnect check failed, stopping." % (label or 'CDP'))
                return 'break'
            print("%s: Reconnected successfully, resuming..." % (label or 'CDP'))
            self.interruptible_sleep(sleep_ok)
            return 'continue'
        except InterruptedError:
            raise
        except Exception as recon_err:
            print("%s: Reconnect failed: %s" % (label or 'CDP', recon_err))
            # Budget already checked at entry; failed reconnect always retries
            # via outer loop until consecutive_conn_errors hits max_errors.
            self.interruptible_sleep(sleep_fail)
            return 'continue'

    # ── Utilities ──────────────────────────────────────────────

    def debug(self, msg):
        """Print debug message if debug_mode is enabled."""
        if self.debug_mode:
            print("  [D] " + msg)

    def parse_eval_json(self, result, empty_error='eval_js_failed'):
        """Parse eval_js JSON result; never collapse failure to {}.

        Returns a dict. On missing/empty result → {'error': empty_error}.
        On JSON parse failure → {'error': str(result)}.
        """
        if not result:
            return {'error': empty_error}
        try:
            data = json.loads(result)
            return data if isinstance(data, dict) else {'error': 'non_object_result'}
        except Exception:
            return {'error': str(result)}

    # CDP/parse failures from parse_eval_json — not semantic page states like "no iframe"
    _CDP_PARSE_ERRORS = frozenset({
        'eval_js_failed', 'non_object_result',
    })

    def is_cdp_parse_error(self, state):
        """True when state['error'] means CDP/JSON failure (reconnect), not page shell."""
        if not isinstance(state, dict):
            return False
        err = state.get('error')
        if not err:
            return False
        if err in self._CDP_PARSE_ERRORS:
            return True
        # parse_eval_json uses str(raw) for JSON decode failures
        if isinstance(err, str) and (
                err.startswith('{') or err.startswith('[') or 'Expecting' in err):
            return True
        return False

    _RECONNECT_MAX_RETRIES = 3
    _RECONNECT_DELAY = 2  # seconds between retries
    _CDP_DISCOVERY_TIMEOUT = 5
    _CDP_JSON_MAX_BYTES = 1024 * 1024
    _WS_CONNECT_TIMEOUT = 10

    def _pick_ets_tab(self, ets_tabs):
        """Prefer an active exam/homework tab over a bare portal home page.

        OPEN-H3: multiple ets100.com targets may exist; picking [0] alone can
        attach to the wrong page. Prefer URLs that look like exam/PK/homework.

        Also prefer targets with a non-empty webSocketDebuggerUrl (attachable
        via CDP) and type "page" when present. Never return a tab without
        webSocketDebuggerUrl if any candidate has one.
        """
        if not ets_tabs:
            return None
        prefer_keys = (
            'mockExam', 'doHomework', 'readingWriting', 'homework',
            'exam', 'pk', 'word', 'practice', 'detail',
        )
        any_has_ws = any((t.get('webSocketDebuggerUrl') or '').strip() for t in ets_tabs)
        scored = []
        for t in ets_tabs:
            ws_url = (t.get('webSocketDebuggerUrl') or '').strip()
            if any_has_ws and not ws_url:
                # Skip non-attachable targets when attachable ones exist
                continue
            u = (t.get('url') or '').lower()
            score = sum(1 for k in prefer_keys if k.lower() in u)
            # Prefer non-blank titles and longer paths (more specific pages)
            title = (t.get('title') or '')
            score += 1 if title.strip() else 0
            # Prefer type "page" when present (vs service_worker, iframe, etc.)
            if (t.get('type') or '').lower() == 'page':
                score += 10
            # Prefer attachable debugger targets
            if ws_url:
                score += 100
            scored.append((score, len(u), t))
        if not scored:
            return None
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return scored[0][2]

    def _raise_if_stopped(self):
        if self.stop_event is not None and self.stop_event.is_set():
            raise InterruptedError("User stopped")

    def _read_cdp_tabs(self):
        """Fetch and validate the bounded local CDP target list."""
        url = "http://127.0.0.1:%d/json" % self.port
        response = _open_local_cdp_url(url, timeout=self._CDP_DISCOVERY_TIMEOUT)
        try:
            raw = _read_limited_response(response, self._CDP_JSON_MAX_BYTES)
        finally:
            close = getattr(response, 'close', None)
            if close:
                close()
        tabs = json.loads(raw.decode('utf-8'))
        if not isinstance(tabs, list):
            raise ValueError("CDP /json response is not a list")
        return tabs

    def _connect_once(self):
        """Discover one ETS target and attach a bounded WebSocket."""
        self._raise_if_stopped()
        tabs = self._read_cdp_tabs()
        ets_tabs = [
            tab for tab in tabs
            if isinstance(tab, dict) and is_ets_page_url(tab.get('url'))
        ]
        if not ets_tabs:
            raise Exception("No ETS tab found on port %d" % self.port)
        self.tab = self._pick_ets_tab(ets_tabs)
        if not self.tab:
            raise Exception(
                "No attachable ETS tab found on port %d "
                "(no candidate with webSocketDebuggerUrl)" % self.port)
        ws_url = self.tab.get('webSocketDebuggerUrl')
        ws_url = ws_url.strip() if isinstance(ws_url, str) else ''
        if not ws_url:
            raise Exception(
                "Selected ETS tab has no webSocketDebuggerUrl on port %d "
                "(url=%s)" % (self.port, str(self.tab.get('url') or '')[:120]))
        if not is_loopback_ws_url(ws_url):
            raise Exception(
                "Refusing non-loopback CDP webSocketDebuggerUrl: %s"
                % (ws_url[:120],))
        self._raise_if_stopped()
        self.ws = _connect_local_cdp_websocket(
            ws_url, timeout=self._WS_CONNECT_TIMEOUT)
        self._raise_if_stopped()
        self.mid = 0
        return len(ets_tabs)

    def connect(self):
        """Find ETS tab and establish CDP WebSocket connection.

        Discovers ETS tabs on the local CDP port and connects to a preferred
        exam-related tab when several exist (OPEN-H3).
        """
        self._drop_connection()
        try:
            ets_tab_count = self._connect_once()
        except Exception:
            self._drop_connection()
            raise
        print("ETS connected")
        self.debug("URL: " + str(self.tab.get('url') or '')[:120])
        if ets_tab_count > 1:
            self.debug(
                "Multiple ETS tabs (%d); selected preferred target"
                % ets_tab_count)

    def reconnect(self):
        """Attempt to re-establish CDP WebSocket after disconnection.

        Tries up to _RECONNECT_MAX_RETRIES times with _RECONNECT_DELAY second
        intervals. Uses interruptible_sleep when stop_event is set so GUI stop
        can abort mid-retry; falls back to time.sleep when stop_event is None.
        On success, resets mid so CDP message ids stay consistent with the new
        socket. On total failure (or stop), leaves ws/tab as None so callers
        never see a half-open tab without a live socket.
        Raises ConnectionError if all retries fail; InterruptedError if stopped.
        """
        self._drop_connection()
        self._raise_if_stopped()
        last_err = None
        for attempt in range(1, self._RECONNECT_MAX_RETRIES + 1):
            self.debug(
                "Reconnect attempt %d/%d..."
                % (attempt, self._RECONNECT_MAX_RETRIES))
            try:
                self._drop_connection()
                self._connect_once()
                print("ETS reconnected (attempt %d)" % attempt)
                self.debug("URL: " + str(self.tab.get('url') or '')[:120])
                return True
            except InterruptedError:
                self._drop_connection()
                raise
            except Exception as e:
                last_err = e
                self.debug("Reconnect attempt %d failed: %s" % (attempt, e))
                self._drop_connection()
                if attempt < self._RECONNECT_MAX_RETRIES:
                    self.interruptible_sleep(self._RECONNECT_DELAY)
        self._drop_connection()
        raise ConnectionError(
            "Reconnect failed after %d attempts: %s"
            % (self._RECONNECT_MAX_RETRIES, last_err))

    _EVAL_JS_TIMEOUT = 15  # seconds per eval_js call

    def _invalidate_ws(self, reason=""):
        """Close and drop the current WebSocket after timeout/poison state.

        A timed-out Runtime.evaluate leaves a late response in the socket
        buffer that would desync subsequent eval_js mid matching. Callers
        should reconnect() before further CDP use.
        """
        if reason:
            self.debug("Invalidating WebSocket: %s" % reason)
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None

    def eval_js(self, expr):
        """Evaluate JavaScript expression via CDP Runtime.evaluate.

        Handles event filtering — CDP may send events between request
        and response; this method skips non-matching messages until
        the response with matching id is found.

        Includes a safety timeout so a dead browser or blocked JS
        cannot deadlock the calling thread.
        On timeout the socket is invalidated (closed) so a subsequent
        reconnect() starts clean — never reuse a half-poisoned WS.
        Catches WebSocket disconnection gracefully.
        """
        if self.ws is None:
            raise ConnectionError(
                "WebSocket not connected — call connect()/reconnect() first")
        self.mid += 1
        payload = json.dumps({
            "id": self.mid, "method": "Runtime.evaluate",
            "params": {"expression": expr, "returnByValue": True}
        })
        try:
            # Bound the first send too; create_connection no longer leaves an
            # infinite socket timeout before the recv deadline is installed.
            self.ws.settimeout(self._EVAL_JS_TIMEOUT)
            self.ws.send(payload)
        except websocket.WebSocketTimeoutException as e:
            self._invalidate_ws("timeout before/during send")
            raise TimeoutError(
                "eval_js send timed out after %ss"
                % self._EVAL_JS_TIMEOUT) from e
        except websocket.WebSocketConnectionClosedException as e:
            self._invalidate_ws("closed before send")
            raise ConnectionError(
                "WebSocket closed — browser disconnected before eval_js send"
            ) from e
        except websocket.WebSocketProtocolException as e:
            self._invalidate_ws("protocol error on send")
            raise ConnectionError(
                "WebSocket protocol error during eval_js send: %s" % e
            ) from e
        except websocket.WebSocketException as e:
            self._invalidate_ws("WebSocket error on send")
            raise ConnectionError(
                "WebSocket error during eval_js send: %s" % e
            ) from e
        except OSError as e:
            self._invalidate_ws("I/O error on send")
            raise ConnectionError(
                "WebSocket I/O error during eval_js send: %s" % e
            ) from e
        deadline = time.monotonic() + self._EVAL_JS_TIMEOUT
        # Recv slices so stop_event / F12 can abort mid-wait without full 15s block.
        # 1.0s balances stop latency vs exception/settimeout churn on every eval_js.
        _recv_slice = 1.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._invalidate_ws("eval_js deadline exceeded")
                raise TimeoutError(
                    "eval_js timed out after %ds (browser may have crashed)"
                    % self._EVAL_JS_TIMEOUT)
            if self.stop_event is not None and self.stop_event.is_set():
                self._invalidate_ws("stop_event during eval_js")
                raise InterruptedError("User stopped")
            self.ws.settimeout(min(remaining, _recv_slice))
            try:
                raw = self.ws.recv()
            except websocket.WebSocketConnectionClosedException:
                self._invalidate_ws("closed during recv")
                raise ConnectionError(
                    "WebSocket closed — browser disconnected during eval_js")
            except websocket.WebSocketTimeoutException:
                # Slice timeout: re-check stop + overall deadline, then keep waiting
                if self.stop_event is not None and self.stop_event.is_set():
                    self._invalidate_ws("stop_event during eval_js recv")
                    raise InterruptedError("User stopped")
                if time.monotonic() >= deadline:
                    self._invalidate_ws("recv timeout")
                    raise TimeoutError(
                        "eval_js timed out after %ds (browser may have crashed)"
                        % self._EVAL_JS_TIMEOUT)
                continue
            except OSError as e:
                self._invalidate_ws("I/O error on recv")
                raise ConnectionError(
                    "WebSocket I/O error during eval_js: %s" % e) from e
            except websocket.WebSocketProtocolException as e:
                self._invalidate_ws("protocol error on recv")
                raise ConnectionError(
                    "WebSocket protocol error during eval_js recv: %s" % e
                ) from e
            except websocket.WebSocketException as e:
                self._invalidate_ws("WebSocket error on recv")
                raise ConnectionError(
                    "WebSocket error during eval_js recv: %s" % e
                ) from e
            try:
                resp = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                # Non-JSON protocol message — skip it (e.g. browser shutdown noise)
                self.debug(
                    "[WS] non-JSON message skipped: %s" % repr(raw)[:200])
                continue
            if not isinstance(resp, dict):
                self.debug("[WS] non-object message skipped: %s" % repr(resp)[:200])
                continue
            if resp.get("id") == self.mid:
                if "error" in resp:
                    self.debug("[WS ERROR] " + str(resp["error"]))
                    return None
                command_result = resp.get("result")
                if not isinstance(command_result, dict):
                    self._invalidate_ws("malformed Runtime.evaluate response")
                    raise ConnectionError(
                        "Malformed CDP Runtime.evaluate response: result is not an object")
                result_obj = command_result.get("result")
                if not isinstance(result_obj, dict):
                    self._invalidate_ws("malformed Runtime.evaluate value")
                    raise ConnectionError(
                        "Malformed CDP Runtime.evaluate response: value is not an object")
                exc_detail = command_result.get("exceptionDetails")
                if (exc_detail is not None
                        and not isinstance(exc_detail, dict)):
                    self._invalidate_ws("malformed Runtime.evaluate exception")
                    raise ConnectionError(
                        "Malformed CDP Runtime.evaluate response: exception is not an object")
                if exc_detail:
                    exc_text = str(exc_detail.get("text", ""))
                    # Try to extract exception description from the first preview property
                    exc_obj = exc_detail.get("exception", {})
                    if not isinstance(exc_obj, dict):
                        exc_obj = {}
                    preview = exc_obj.get("preview")
                    properties = preview.get("properties") if isinstance(preview, dict) else None
                    if isinstance(properties, list):
                        for prop in properties:
                            if isinstance(prop, dict) and prop.get("name") == "message":
                                exc_text = str(prop.get("value", exc_text))
                                break
                    elif exc_obj.get("description"):
                        exc_text = str(exc_obj["description"])
                    self.debug("[JS EXCEPTION] " + exc_text)
                    return None
                return result_obj.get("value")

    def interruptible_sleep(self, seconds):
        """Sleep that can be interrupted by stop_event.

        If stop_event is set during the sleep, raises InterruptedError.
        If no stop_event was provided, falls back to normal time.sleep().
        """
        if self.stop_event is None:
            time.sleep(seconds)
            return
        if self.stop_event.is_set():
            raise InterruptedError("User stopped")
        # Sleep in small chunks, checking stop_event between each
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.stop_event.is_set():
                raise InterruptedError("User stopped")
            remaining = end - time.monotonic()
            self.stop_event.wait(timeout=min(0.2, remaining))
            if self.stop_event.is_set():
                raise InterruptedError("User stopped")

    @staticmethod
    def js_escape(s):
        """Escape string for safe JS single/double-quoted string injection.

        Also escapes U+2028/U+2029 (line/paragraph separators) which are valid
        in JSON/Python strings but terminate JS string literals (OPEN-M3).
        """
        if s is None:
            return ''
        if not isinstance(s, str):
            s = str(s)
        s = s.replace('\\', '\\\\')
        s = s.replace("'", "\\'")
        s = s.replace('"', '\\"')
        s = s.replace('\n', '\\n')
        s = s.replace('\r', '')
        s = s.replace('\u2028', '\\u2028')
        s = s.replace('\u2029', '\\u2029')
        return s
