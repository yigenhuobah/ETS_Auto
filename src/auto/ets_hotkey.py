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
VK_F12 = 0x7B  # Windows VK_F12 (0x87 is not F12)

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

    _PUMP_READY_TIMEOUT = 1.0
    _REGISTER_TIMEOUT = 1.0
    _STOP_TIMEOUT = 2.0
    _UNRESOLVED_PUMPS = set()
    _UNRESOLVED_PUMPS_GUARD = threading.Lock()

    def __init__(self, on_stop=None):
        self._paused = False
        self._skip = False
        self._stop = False
        self._thread = None
        self._registered = False
        self._stopping = False
        self._lock = threading.Lock()
        self._thread_id = None
        self._pump_ready = threading.Event()
        self._pump_error = None
        self._reg_done = threading.Event()
        self._reg_result = None
        self._bindings = {'F9': False, 'F10': False, 'F12': False}
        # Optional callback invoked immediately on F12 (thread-safe set stop_event)
        self._configured_on_stop = on_stop
        self._on_stop = on_stop

    @classmethod
    def _unresolved_pumps(cls):
        with cls._UNRESOLVED_PUMPS_GUARD:
            return tuple(cls._UNRESOLVED_PUMPS)

    def _retain_unresolved_pump(self):
        """Keep a timed-out pump reachable and detach its stop callback."""
        with self._lock:
            self._on_stop = None
        with self._UNRESOLVED_PUMPS_GUARD:
            self._UNRESOLVED_PUMPS.add(self)

    def _release_unresolved_pump(self):
        with self._UNRESOLVED_PUMPS_GUARD:
            self._UNRESOLVED_PUMPS.discard(self)

    @classmethod
    def _stop_prior_unresolved_pumps(cls, current):
        """Retry old cleanup and refuse a second process-wide listener."""
        for owner in cls._unresolved_pumps():
            if owner is current:
                continue
            if not owner._stop_pump():
                return False
        return True

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
        """Start the pump and return True when at least one key is bound."""
        if not self._stop_prior_unresolved_pumps(self):
            print(
                "Hotkeys: WARNING — an earlier listener is still stopping")
            return False

        if self._stopping:
            if (self._thread is not None and self._thread.is_alive()
                    and not self._stop_pump()):
                print("Hotkeys: WARNING — previous listener is still stopping")
                return False
            self._stopping = False

        if (self._registered and self._thread is not None
                and self._thread.is_alive()):
            return True
        if self._registered:
            self._registered = False
            self._bindings = {'F9': False, 'F10': False, 'F12': False}

        if self._thread is not None and self._thread.is_alive():
            if not self._stop_pump():
                print("Hotkeys: WARNING — previous listener is still stopping")
                return False
        else:
            self._thread = None
            self._thread_id = None

        self._pump_ready = threading.Event()
        self._pump_error = None
        self._reg_done = threading.Event()
        self._reg_result = None
        self._bindings = {'F9': False, 'F10': False, 'F12': False}
        with self._lock:
            self._on_stop = self._configured_on_stop
        self._stopping = False
        self._thread = threading.Thread(target=self._message_pump, daemon=True)
        try:
            self._thread.start()
        except Exception as e:
            self._thread = None
            self._thread_id = None
            self._stopping = False
            print("Hotkeys: WARNING — listener failed to start (%s)" % e)
            return False

        ready = self._pump_ready.wait(timeout=self._PUMP_READY_TIMEOUT)
        if (not ready or self._thread_id is None
                or self._thread is None or not self._thread.is_alive()):
            self._stop_pump()
            detail = " (%s)" % self._pump_error if self._pump_error else ""
            print("Hotkeys: WARNING — listener did not become ready%s" % detail)
            return False

        # Register hotkeys on the listener thread's message queue
        # (RegisterHotKey is thread-bound: messages go to registering thread)
        if not PostThreadMessage(self._thread_id, 0x0401, 0, 0):
            self._stop_pump()
            print("Hotkeys: WARNING — could not signal listener thread")
            return False
        if not self._reg_done.wait(timeout=self._REGISTER_TIMEOUT):
            self._stop_pump()
            print("Hotkeys: WARNING — registration timed out")
            return False
        if self._thread is None or not self._thread.is_alive():
            self._stop_pump()
            print("Hotkeys: WARNING — listener stopped during registration")
            return False


        bindings = self._reg_result or {}
        self._bindings = {
            name: bool(bindings.get(name))
            for name in ('F9', 'F10', 'F12')
        }
        ok_any = any(self._bindings.values())
        if ok_any:
            self._registered = True
            self._print_binding_status()
            return True

        self._stop_pump()
        self._registered = False
        self._print_binding_status()
        return False

    def unregister(self):
        """Unregister keys; return False if the listener remains alive."""
        # Publish loss of capability before waiting for the native pump. A
        # timed-out join must never leave callers believing hotkeys still work.
        self._registered = False
        self._bindings = {'F9': False, 'F10': False, 'F12': False}
        if self._thread is not None and self._thread.is_alive():
            if not self._stop_pump():
                return False
        else:
            self._thread = None
            self._thread_id = None

        return True

    # ── Internal ──────────────────────────────────────────────

    def _print_binding_status(self):
        labels = (
            ('F9', 'F9=Pause'),
            ('F10', 'F10=Skip'),
            ('F12', 'F12=Stop'),
        )
        active = [label for name, label in labels if self._bindings.get(name)]
        missing = [name for name, _label in labels if not self._bindings.get(name)]
        if active:
            print("Hotkeys: " + "  ".join(active))
        if missing:
            print("Hotkeys: WARNING — unavailable: " + ", ".join(missing))

    def _stop_pump(self):
        """Post WM_QUIT and return True only after the pump has exited.

        Pump thread calls _do_unregister() on exit (RegisterHotKey is
        thread-bound, so UnregisterHotKey must run on the same thread).
        """
        thr = self._thread
        if thr is None or not thr.is_alive():
            self._thread = None
            self._thread_id = None
            self._stopping = False
            self._release_unresolved_pump()
            return True

        self._registered = False
        self._bindings = {'F9': False, 'F10': False, 'F12': False}
        self._stopping = True
        tid = self._thread_id
        if tid:
            PostThreadMessage(tid, WM_QUIT, 0, 0)
        if (thr is not None and thr.is_alive()
                and thr is not threading.current_thread()):
            thr.join(timeout=self._STOP_TIMEOUT)
        if thr is not None and thr.is_alive():
            # Preserve the handle process-wide even if a caller discards this
            # object after register()/unregister() reports failure.
            self._retain_unresolved_pump()
            return False
        self._thread = None
        self._thread_id = None
        self._stopping = False
        self._release_unresolved_pump()
        return True

    def _do_register(self):
        """Called inside the listener thread to register hotkeys."""
        ok1 = ok2 = ok3 = False
        try:
            base_flags = MOD_NOREPEAT
            alt_flags = MOD_ALT | MOD_NOREPEAT
            ok1 = bool(RegisterHotKey(None, HOTKEY_PAUSE, base_flags, VK_F9))
            ok2 = bool(RegisterHotKey(None, HOTKEY_SKIP, base_flags, VK_F10))
            ok3 = bool(RegisterHotKey(None, HOTKEY_STOP, base_flags, VK_F12))

            if not ok1:
                ok1 = bool(RegisterHotKey(None, HOTKEY_PAUSE, alt_flags, VK_F9))
                if ok1:
                    print("  F9 taken, using Alt+F9 for Pause")
            if not ok2:
                ok2 = bool(RegisterHotKey(None, HOTKEY_SKIP, alt_flags, VK_F10))
                if ok2:
                    print("  F10 taken, using Alt+F10 for Skip")
            if not ok3:
                ok3 = bool(RegisterHotKey(None, HOTKEY_STOP, alt_flags, VK_F12))
                if ok3:
                    print("  F12 taken, using Alt+F12 for Stop")
        except Exception as e:
            self._pump_error = e
        finally:
            self._reg_result = {'F9': ok1, 'F10': ok2, 'F12': ok3}
            self._bindings = dict(self._reg_result)
            self._reg_done.set()

        if not any(self._reg_result.values()):
            try:
                err = ctypes.windll.kernel32.GetLastError()
                print("  Hotkey register failed (GetLastError=%s)" % err)
            except Exception:
                print("  Hotkey register failed (no binding succeeded)")

    def _do_unregister(self):
        """Called inside the listener thread to unregister hotkeys."""
        UnregisterHotKey(None, HOTKEY_PAUSE)
        UnregisterHotKey(None, HOTKEY_SKIP)
        UnregisterHotKey(None, HOTKEY_STOP)

    def _message_pump(self):
        """Run the native message loop and always publish terminal state."""
        try:
            self._run_message_pump()
        except Exception as e:
            self._pump_error = e
        finally:
            self._pump_ready.set()
            try:
                self._do_unregister()
            except Exception:
                pass
            self._registered = False
            self._bindings = {'F9': False, 'F10': False, 'F12': False}
            self._stopping = False
            self._release_unresolved_pump()

    def _run_message_pump(self):
        """Background thread: message pump for WM_HOTKEY."""
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        # Cold-start: force-initialize message queue before RegisterHotKey
        # Without this, the queue may not exist when PostThreadMessage arrives
        peek_msg = ctypes.wintypes.MSG()
        ctypes.windll.user32.PeekMessageW(ctypes.byref(peek_msg), 0, 0, 0, 0)
        self._pump_ready.set()

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
                on_stop = None
                with self._lock:
                    if hotkey_id == HOTKEY_PAUSE:
                        self._paused = not self._paused
                        state = "PAUSED" if self._paused else "RESUMED"
                        # OPEN-M1: ASCII-only for GBK consoles
                        print("\n  [%s] (F9)" % state)
                    elif hotkey_id == HOTKEY_SKIP:
                        self._skip = True
                        print("\n  [SKIP] (F10)")
                    elif hotkey_id == HOTKEY_STOP:
                        self._stop = True
                        print("\n  [STOP] (F12)")
                        # Invoke outside lock so callback can safely touch stop_event
                        on_stop = self._on_stop
                if on_stop is not None:
                    try:
                        on_stop()
                    except Exception:
                        pass
            if msg.message == WM_QUIT:
                break


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
