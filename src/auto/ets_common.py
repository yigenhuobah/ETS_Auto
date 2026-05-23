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
        self.ws.send(json.dumps({
            "id": self.mid, "method": "Runtime.evaluate",
            "params": {"expression": expr, "returnByValue": True}
        }))
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
            resp = json.loads(raw)
            if resp.get("id") == self.mid:
                if "error" in resp:
                    self.debug("[WS ERROR] " + str(resp["error"]))
                    return None
                return resp.get("result", {}).get("result", {}).get("value")

    @staticmethod
    def js_escape(s):
        """Escape string for safe JS single-quoted string injection."""
        return s.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '')
