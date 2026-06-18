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
        # Optional threading.Event: when set, interruptible_sleep raises InterruptedError
        self.stop_event = stop_event
        # Callback hooks
        self._on_connect = None
        self._on_question = None
        self._on_complete = None
        self._on_error = None

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
            except Exception:
                pass

    def _fire_question(self, info):
        """Fire on_question callback if registered."""
        if self._on_question:
            try:
                self._on_question(info)
            except Exception:
                pass

    def _fire_complete(self, stats):
        """Fire on_complete callback if registered."""
        if self._on_complete:
            try:
                self._on_complete(stats)
            except Exception:
                pass

    def _fire_error(self, msg):
        """Fire on_error callback if registered."""
        if self._on_error:
            try:
                self._on_error(msg)
            except Exception:
                pass

    # ── Utilities ──────────────────────────────────────────────

    def debug(self, msg):
        """Print debug message if debug_mode is enabled."""
        if self.debug_mode:
            print("  [D] " + msg)

    def connect(self):
        """Find ETS tab and establish CDP WebSocket connection.

        Discovers ETS tabs on the local CDP port and connects to the
        first one found. Subclasses may override to add post-connect logic.
        """
        url = "http://localhost:%d/json" % self.port
        tabs = json.loads(urllib.request.urlopen(url, timeout=5).read())
        ets_tabs = [t for t in tabs if "ets100.com" in t.get("url", "")]
        if not ets_tabs:
            raise Exception("No ETS tab found on port %d" % self.port)
        self.tab = ets_tabs[0]
        self.ws = websocket.create_connection(self.tab["webSocketDebuggerUrl"], timeout=None)
        print("ETS connected")
        self.debug("URL: " + self.tab['url'][:120])

    _EVAL_JS_TIMEOUT = 15  # seconds per eval_js call

    def eval_js(self, expr):
        """Evaluate JavaScript expression via CDP Runtime.evaluate.

        Handles event filtering — CDP may send events between request
        and response; this method skips non-matching messages until
        the response with matching id is found.

        Includes a safety timeout so a dead browser or blocked JS
        cannot deadlock the calling thread.
        Catches WebSocket disconnection gracefully.
        """
        self.mid += 1
        payload = json.dumps({
            "id": self.mid, "method": "Runtime.evaluate",
            "params": {"expression": expr, "returnByValue": True}
        })
        try:
            self.ws.send(payload)
        except websocket.WebSocketConnectionClosedException:
            raise ConnectionError(
                "WebSocket closed — browser disconnected before eval_js send")
        except OSError as e:
            raise ConnectionError(
                "WebSocket I/O error during eval_js send: %s" % e)
        deadline = time.time() + self._EVAL_JS_TIMEOUT
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(
                    "eval_js timed out after %ds (browser may have crashed)"
                    % self._EVAL_JS_TIMEOUT)
            self.ws.settimeout(remaining)
            try:
                raw = self.ws.recv()
            except websocket.WebSocketConnectionClosedException:
                raise ConnectionError(
                    "WebSocket closed — browser disconnected during eval_js")
            except websocket.WebSocketTimeoutException:
                raise TimeoutError(
                    "eval_js timed out after %ds (browser may have crashed)"
                    % self._EVAL_JS_TIMEOUT)
            except OSError as e:
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
        """Escape string for safe JS single-quoted or double-quoted string injection."""
        return (s.replace('\\', '\\\\').replace("'", "\\'")
                 .replace('"', '\\"').replace('\n', '\\n').replace('\r', ''))
