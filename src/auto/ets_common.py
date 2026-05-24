#!/usr/bin/env python3
"""
ETS Common — Shared CDP connection and utilities for ETS automation.

Provides ETSBase class with:
  - CDP tab discovery and WebSocket connection
  - JavaScript evaluation via Runtime.evaluate (with event filtering)
  - Debug logging helper
  - JS string escaping utility
"""
import json, time, urllib.request, websocket, os


class ETSBase:
    """Base class for ETS automation scripts.

    Handles Chrome DevTools Protocol (CDP) connection to the ETS PC client
    and provides common utilities shared by exam and PK scripts.
    """

    def __init__(self, port=10086, debug_mode=False):
        self.port = port
        self.ws = None
        self.mid = 0
        self.debug_mode = debug_mode
        self.tab = None

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

    @staticmethod
    def js_escape(s):
        """Escape string for safe JS single-quoted or double-quoted string injection."""
        return (s.replace('\\', '\\\\').replace("'", "\\'")
                 .replace('"', '\\"').replace('\n', '\\n').replace('\r', ''))
