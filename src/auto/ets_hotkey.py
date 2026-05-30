#!/usr/bin/env python3
"""
ETS Hotkey — Global hotkey support via ctypes RegisterHotKey (Windows).

Uses Windows native RegisterHotKey API — zero antivirus false positives
compared to keyboard/pynput which use WH_KEYBOARD_LL hooks.

Hotkeys:
  F9  — Pause / Resume
  F10 — Skip current question
  F12 — Emergency stop (disconnect CDP)

Usage:
  from ets_hotkey import ETSHotkey
  hk = ETSHotkey()
  hk.register()  # registers F9/F10/F12

  # In main loop:
  if hk.is_paused:
      ...
  if hk.should_skip:
      hk.clear_skip()
      ...
  if hk.should_stop:
      break

  hk.unregister()  # cleanup
"""
import threading
import ctypes
import ctypes.wintypes

# Windows constants
MOD_ALT     = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT   = 0x0004
MOD_WIN     = 0x0008
MOD_NOREPEAT = 0x4000

VK_F9  = 0x78
VK_F10 = 0x79
VK_F12 = 0x87

WM_HOTKEY = 0x0312

# RegisterHotKey / UnregisterHotKey
user32 = ctypes.windll.user32
RegisterHotKey = user32.RegisterHotKey
UnregisterHotKey = user32.UnregisterHotKey
GetMessage = user32.GetMessageW
PostThreadMessage = user32.PostThreadMessageW

# Message constants for thread shutdown
WM_QUIT = 0x0012

# Hotkey IDs
HOTKEY_PAUSE = 1
HOTKEY_SKIP  = 2
HOTKEY_STOP  = 3


class ETSHotkey:
    """
    Global hotkey manager using Windows RegisterHotKey API.

    Thread-safe: runs a background message pump thread.
    Main thread checks state via is_paused / should_skip / should_stop.
    """

    def __init__(self):
        self._paused = False
        self._skip = False
        self._stop = False
        self._thread = None
        self._registered = False
        self._lock = threading.Lock()
        self._thread_id = None

    # ── Public state queries ──────────────────────────────────

    @property
    def is_paused(self):
        """True when F9 has toggled pause on."""
        with self._lock:
            return self._paused

    @property
    def should_skip(self):
        """True when F10 has been pressed (one-shot)."""
        with self._lock:
            return self._skip

    @property
    def should_stop(self):
        """True when F12 has been pressed (emergency stop)."""
        with self._lock:
            return self._stop

    def clear_skip(self):
        """Acknowledge skip signal — reset to False."""
        with self._lock:
            self._skip = False

    def clear_stop(self):
        """Acknowledge stop signal — reset to False."""
        with self._lock:
            self._stop = False

    # ── Register / Unregister ─────────────────────────────────

    def register(self):
        """Register F9/F10/F12 global hotkeys and start listener thread."""
        if self._registered:
            return

        self._thread = threading.Thread(target=self._message_pump, daemon=True)
        self._thread.start()

        # Wait for thread to be ready
        while self._thread_id is None:
            threading.Event().wait(0.01)

        # Register hotkeys on the listener thread's message queue
        # (RegisterHotKey is thread-bound: messages go to registering thread)
        PostThreadMessage(self._thread_id, 0x0401, 0, 0)  # custom: do register
        threading.Event().wait(0.1)
        self._registered = True

        print("Hotkeys: F9=Pause  F10=Skip  F12=Stop")

    def unregister(self):
        """Unregister all hotkeys and stop listener thread."""
        if not self._registered:
            return

        if self._thread_id:
            PostThreadMessage(self._thread_id, WM_QUIT, 0, 0)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

        self._registered = False

    # ── Internal ──────────────────────────────────────────────

    def _do_register(self):
        """Called inside the listener thread to register hotkeys."""
        ok1 = RegisterHotKey(None, HOTKEY_PAUSE, 0, VK_F9)
        ok2 = RegisterHotKey(None, HOTKEY_SKIP, 0, VK_F10)
        ok3 = RegisterHotKey(None, HOTKEY_STOP, 0, VK_F12)

        if not ok1:
            # F9 might be taken; try Alt+F9
            ok1 = RegisterHotKey(None, HOTKEY_PAUSE, MOD_ALT, VK_F9)
            if ok1:
                print("  F9 taken, using Alt+F9 for Pause")
        if not ok2:
            ok2 = RegisterHotKey(None, HOTKEY_SKIP, MOD_ALT, VK_F10)
            if ok2:
                print("  F10 taken, using Alt+F10 for Skip")
        if not ok3:
            ok3 = RegisterHotKey(None, HOTKEY_STOP, MOD_ALT, VK_F12)
            if ok3:
                print("  F12 taken, using Alt+F12 for Stop")

    def _do_unregister(self):
        """Called inside the listener thread to unregister hotkeys."""
        UnregisterHotKey(None, HOTKEY_PAUSE)
        UnregisterHotKey(None, HOTKEY_SKIP)
        UnregisterHotKey(None, HOTKEY_STOP)

    def _message_pump(self):
        """Background thread: message pump for WM_HOTKEY."""
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        # Cold-start: force-initialize message queue before RegisterHotKey
        # Without this, the queue may not exist when PostThreadMessage arrives
        peek_msg = ctypes.wintypes.MSG()
        ctypes.windll.user32.PeekMessageW(ctypes.byref(peek_msg), 0, 0, 0, 0)

        # Wait for signal to register
        msg = ctypes.wintypes.MSG()
        while True:
            ret = GetMessage(ctypes.byref(msg), 0, 0, 0)
            if ret == 0:
                # WM_QUIT received
                break
            if ret == -1:
                # GetLastError — fatal; break to avoid infinite loop
                break
            if msg.message == 0x0401:
                # Custom message: do register
                self._do_register()
                continue
            if msg.message == WM_HOTKEY:
                hotkey_id = msg.wParam
                with self._lock:
                    if hotkey_id == HOTKEY_PAUSE:
                        self._paused = not self._paused
                        state = "PAUSED" if self._paused else "RESUMED"
                        print("\n  ⏸ %s (F9)" % state)
                    elif hotkey_id == HOTKEY_SKIP:
                        self._skip = True
                        print("\n  ⏭ SKIP (F10)")
                    elif hotkey_id == HOTKEY_STOP:
                        self._stop = True
                        print("\n  🛑 STOP (F12)")
            if msg.message == WM_QUIT:
                break

        self._do_unregister()


# ── Standalone test ──────────────────────────────────────────

def main():
    print("ETS Hotkey Test — F9/F10/F12 (Ctrl+C to exit)")
    hk = ETSHotkey()
    hk.register()
    try:
        while True:
            if hk.should_stop:
                print("Emergency stop triggered!")
                hk.clear_stop()
                break
            if hk.is_paused:
                import time
                time.sleep(0.1)
                continue
            if hk.should_skip:
                print("Skipping...")
                hk.clear_skip()
            import time
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        hk.unregister()
    print("Done")


if __name__ == '__main__':
    main()
