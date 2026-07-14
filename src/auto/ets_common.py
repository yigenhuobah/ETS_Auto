#!/usr/bin/env python3
"""
ETS Common — Shared CDP connection and utilities for ETS automation.

Provides ETSBase class with:
  - CDP tab discovery and WebSocket connection
  - JavaScript evaluation via Runtime.evaluate (with event filtering)
  - Debug logging helper
  - JS string escaping utility
"""
import json, time, urllib.request, websocket, os, sys, threading
from urllib.parse import urlparse

# Single source of truth for app version (imported by exam/PK/GUI/remote)
APP_VERSION = "0.6.7"


def is_loopback_ws_url(ws_url):
    """Return True if webSocketDebuggerUrl targets a local loopback host.

    CDP attach is a high-privilege local control plane; reject non-loopback
    hosts even if the /json listing somehow points elsewhere.

    Accepts common loopback spellings: 127.0.0.1, localhost, ::1,
    IPv4-mapped ::ffff:127.0.0.1, expanded IPv6 loopback, and 127.0.0.0/8.
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
    if host in ('127.0.0.1', 'localhost', '::1'):
        return True
    if host in ('::ffff:127.0.0.1', '0:0:0:0:0:ffff:127.0.0.1'):
        return True
    if host in ('0:0:0:0:0:0:0:1', '0000:0000:0000:0000:0000:0000:0000:0001'):
        return True
    if host.startswith('127.'):
        parts = host.split('.')
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return True
    return False


def user_data_path(filename, anchor_file=None):
    """Resolve a user-writable path (beside exe when frozen; project root in dev).

    Shared by PK (pk_extra/misses), remote (pk_extra download), and exam stats.
    Only the basename of filename is used so absolute paths cannot escape base.
    """
    raw = str(filename or '')
    raw = raw.replace(chr(92), '/').rstrip('/')
    name = os.path.basename(raw)
    if not name or name in ('.', '..'):
        name = 'data.bin'
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        anchor = anchor_file or __file__
        base = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(anchor))))
    return os.path.join(base, name)

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
        if self.ws:
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

    def connect(self):
        """Find ETS tab and establish CDP WebSocket connection.

        Discovers ETS tabs on the local CDP port and connects to a preferred
        exam-related tab when several exist (OPEN-H3).
        """
        url = "http://localhost:%d/json" % self.port
        tabs = json.loads(urllib.request.urlopen(url, timeout=5).read())
        ets_tabs = [t for t in tabs if "ets100.com" in t.get("url", "")]
        if not ets_tabs:
            raise Exception("No ETS tab found on port %d" % self.port)
        self.tab = self._pick_ets_tab(ets_tabs)
        if not self.tab:
            raise Exception(
                "No attachable ETS tab found on port %d "
                "(no candidate with webSocketDebuggerUrl)" % self.port)
        ws_url = (self.tab.get("webSocketDebuggerUrl") or "").strip()
        if not ws_url:
            raise Exception(
                "Selected ETS tab has no webSocketDebuggerUrl on port %d "
                "(url=%s)" % (self.port, (self.tab.get("url") or "")[:120]))
        if not is_loopback_ws_url(ws_url):
            raise Exception(
                "Refusing non-loopback CDP webSocketDebuggerUrl: %s"
                % (ws_url[:120],))
        self.ws = websocket.create_connection(ws_url, timeout=None)
        # Fresh socket: always start mid at 0 (matches reconnect; avoids stale ids
        # if connect() is called again after a prior session without a new instance).
        self.mid = 0
        print("ETS connected")
        self.debug("URL: " + self.tab['url'][:120])
        if len(ets_tabs) > 1:
            self.debug("Multiple ETS tabs (%d); selected preferred target" % len(ets_tabs))

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
        last_err = None
        for attempt in range(1, self._RECONNECT_MAX_RETRIES + 1):
            self.debug("Reconnect attempt %d/%d..." % (attempt, self._RECONNECT_MAX_RETRIES))
            try:
                self._drop_connection()
                url = "http://localhost:%d/json" % self.port
                tabs = json.loads(urllib.request.urlopen(url, timeout=5).read())
                ets_tabs = [t for t in tabs if "ets100.com" in t.get("url", "")]
                if not ets_tabs:
                    raise Exception("No ETS tab found")
                self.tab = self._pick_ets_tab(ets_tabs)
                if not self.tab:
                    raise Exception(
                        "No attachable ETS tab found "
                        "(no candidate with webSocketDebuggerUrl)")
                ws_url = (self.tab.get("webSocketDebuggerUrl") or "").strip()
                if not ws_url:
                    raise Exception(
                        "Selected ETS tab has no webSocketDebuggerUrl "
                        "(url=%s)" % ((self.tab.get("url") or "")[:120]))
                if not is_loopback_ws_url(ws_url):
                    raise Exception(
                        "Refusing non-loopback CDP webSocketDebuggerUrl: %s"
                        % (ws_url[:120],))
                self.ws = websocket.create_connection(ws_url, timeout=None)
                # New socket: reset mid so eval_js ids don't collide with stale state
                self.mid = 0
                print("ETS reconnected (attempt %d)" % attempt)
                self.debug("URL: " + self.tab['url'][:120])
                return True
            except InterruptedError:
                self._drop_connection()
                raise
            except Exception as e:
                last_err = e
                self.debug("Reconnect attempt %d failed: %s" % (attempt, e))
                self._drop_connection()
                if attempt < self._RECONNECT_MAX_RETRIES:
                    # interruptible when stop_event present; else plain sleep
                    self.interruptible_sleep(self._RECONNECT_DELAY)
        self._drop_connection()
        raise ConnectionError(
            "Reconnect failed after %d attempts: %s" % (self._RECONNECT_MAX_RETRIES, last_err))

    _EVAL_JS_TIMEOUT = 15  # seconds per eval_js call

    def _invalidate_ws(self, reason=""):
        """Close and drop the current WebSocket after timeout/poison state.

        A timed-out Runtime.evaluate leaves a late response in the socket
        buffer that would desync subsequent eval_js mid matching. Callers
        should reconnect() before further CDP use.
        """
        if reason:
            self.debug("Invalidating WebSocket: %s" % reason)
        if self.ws:
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
            self.ws.send(payload)
        except websocket.WebSocketConnectionClosedException:
            self._invalidate_ws("closed before send")
            raise ConnectionError(
                "WebSocket closed — browser disconnected before eval_js send")
        except OSError as e:
            self._invalidate_ws("I/O error on send")
            raise ConnectionError(
                "WebSocket I/O error during eval_js send: %s" % e)
        deadline = time.time() + self._EVAL_JS_TIMEOUT
        # Recv slices so stop_event / F12 can abort mid-wait without full 15s block.
        # 1.0s balances stop latency vs exception/settimeout churn on every eval_js.
        _recv_slice = 1.0
        while True:
            remaining = deadline - time.time()
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
                if time.time() >= deadline:
                    self._invalidate_ws("recv timeout")
                    raise TimeoutError(
                        "eval_js timed out after %ds (browser may have crashed)"
                        % self._EVAL_JS_TIMEOUT)
                continue
            except OSError as e:
                self._invalidate_ws("I/O error on recv")
                raise ConnectionError(
                    "WebSocket I/O error during eval_js: %s" % e)
            try:
                resp = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                # Non-JSON protocol message — skip it (e.g. browser shutdown noise)
                self.debug("[WS] non-JSON message skipped: %s" % raw[:200])
                continue
            if resp.get("id") == self.mid:
                if "error" in resp:
                    self.debug("[WS ERROR] " + str(resp["error"]))
                    return None
                # Check for JS runtime exceptions (e.g. TypeError from null iframe)
                result_obj = resp.get("result", {}).get("result", {})
                exc_detail = resp.get("result", {}).get("exceptionDetails")
                if exc_detail:
                    exc_text = exc_detail.get("text", "")
                    # Try to extract exception description from the first preview property
                    exc_obj = exc_detail.get("exception", {})
                    if exc_obj.get("type") == "object" and exc_obj.get("preview", {}).get("properties"):
                        for prop in exc_obj["preview"]["properties"]:
                            if prop.get("name") == "message":
                                exc_text = prop.get("value", exc_text)
                                break
                    elif exc_obj.get("description"):
                        exc_text = exc_obj["description"]
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
        end = time.time() + seconds
        while time.time() < end:
            if self.stop_event.is_set():
                raise InterruptedError("User stopped")
            remaining = end - time.time()
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

