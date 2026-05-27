#!/usr/bin/env python3
"""
ETS Auto GUI — CustomTkinter launcher for ETS automation tools.

Provides a graphical interface for:
  - Tab 1: Selecting mode (Exam / Word PK) + CDP settings
  - Tab 2: 📚 离线试卷浏览器 (ETS cached data viewer)
  - Real-time log output for automation

Usage:
  python ets_gui.py
  python ets_gui.py --debug
"""
import sys
import os
import threading
import queue
import time

# ── Path setup: ensure src/auto is importable ────────────────
# When running from PyInstaller bundle or from project root
if getattr(sys, 'frozen', False):
    # PyInstaller: _MEIPASS is the temp bundle root
    _BASE = sys._MEIPASS
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, os.path.join(_BASE, 'src', 'auto'))

# ── Force UTF-8 on Windows ──────────────────────────────────
if sys.platform == 'win32':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, LookupError):
        pass

import customtkinter as ctk


# ── Queue-based stdout bridge ────────────────────────────────
class QueueWriter:
    """Redirects stdout/stderr writes into a thread-safe queue.

    The GUI polls this queue via .after() and appends to the log widget.
    This avoids calling tkinter from a background thread (which would crash).
    """
    def __init__(self, log_queue, original=None):
        self.log_queue = log_queue
        self.original = original  # keep original for fallback

    def write(self, message):
        if message:
            self.log_queue.put(message)
        if self.original:
            try:
                self.original.write(message)
            except Exception:
                pass

    def flush(self):
        if self.original:
            try:
                self.original.flush()
            except Exception:
                pass

    # Standard text IO attributes (PyInstaller/pip may read these)
    @property
    def encoding(self):
        return 'utf-8'

    @property
    def errors(self):
        return 'replace'


# ── Main Application ────────────────────────────────────────
class ETSApp(ctk.CTk):
    MODE_EXAM = "exam"
    MODE_PK = "pk"

    def __init__(self):
        super().__init__()
        self.title("ETS Auto")
        self.geometry("680x560")
        self.resizable(True, True)
        self.minsize(580, 480)

        # State
        self._worker = None
        self._stop_event = threading.Event()
        self._log_queue = queue.Queue()
        self._running = False

        # Theme
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._build_ui()
        self._poll_log()

    # ── UI Construction ──────────────────────────────────────
    def _build_ui(self):
        # Tab view
        self._tabview = ctk.CTkTabview(self)
        self._tabview.pack(fill="both", expand=True, padx=10, pady=10)

        # Tab 1: Auto mode
        tab1 = self._tabview.add("🤖 自动答题")
        self._build_auto_tab(tab1)

        # Tab 2: Offline browser
        tab2 = self._tabview.add("📚 离线试卷浏览器")
        self._build_browser_tab(tab2)

    def _build_auto_tab(self, parent):
        """Build the auto-answer tab (original GUI content)."""
        # Top frame: settings
        settings = ctk.CTkFrame(parent, fg_color="transparent")
        settings.pack(fill="x", padx=16, pady=(16, 8))

        # Mode selector
        mode_label = ctk.CTkLabel(settings, text="模式", width=60, anchor="w")
        mode_label.grid(row=0, column=0, sticky="w", padx=(0, 8))

        self._mode_var = ctk.StringVar(value=self.MODE_EXAM)
        self._mode_exam = ctk.CTkRadioButton(
            settings, text="套卷答题", variable=self._mode_var,
            value=self.MODE_EXAM, command=self._on_mode_change)
        self._mode_exam.grid(row=0, column=1, padx=(0, 12))

        self._mode_pk = ctk.CTkRadioButton(
            settings, text="单词PK", variable=self._mode_var,
            value=self.MODE_PK, command=self._on_mode_change)
        self._mode_pk.grid(row=0, column=2)

        # CDP Port
        port_label = ctk.CTkLabel(settings, text="CDP端口", width=60, anchor="w")
        port_label.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))

        self._port_var = ctk.StringVar(value="10086")
        self._port_entry = ctk.CTkEntry(settings, textvariable=self._port_var, width=100)
        self._port_entry.grid(row=1, column=1, sticky="w", pady=(8, 0))

        # Debug checkbox
        self._debug_var = ctk.BooleanVar(value=False)
        self._debug_cb = ctk.CTkCheckBox(settings, text="调试模式", variable=self._debug_var)
        self._debug_cb.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # Max steps / max questions
        self._max_label = ctk.CTkLabel(settings, text="步数上限", width=60, anchor="w")
        self._max_label.grid(row=3, column=0, sticky="w", padx=(0, 8), pady=(8, 0))

        self._max_var = ctk.StringVar(value="999")
        self._max_entry = ctk.CTkEntry(settings, textvariable=self._max_var, width=100)
        self._max_entry.grid(row=3, column=1, sticky="w", pady=(8, 0))

        # Buttons
        btn_frame = ctk.CTkFrame(parent, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(4, 8))

        self._start_btn = ctk.CTkButton(
            btn_frame, text="🚀 开始", width=120, command=self._on_start)
        self._start_btn.pack(side="left", padx=(0, 12))

        self._stop_btn = ctk.CTkButton(
            btn_frame, text="⏹ 停止", width=120, command=self._on_stop,
            fg_color="#c0392b", hover_color="#a93226", state="disabled")
        self._stop_btn.pack(side="left")

        # Status bar
        self._status_var = ctk.StringVar(value="就绪")
        self._status_label = ctk.CTkLabel(
            parent, textvariable=self._status_var, anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"))
        self._status_label.pack(fill="x", padx=16, pady=(0, 4))

        # Log output
        self._log_text = ctk.CTkTextbox(
            parent, wrap="word", state="disabled",
            font=ctk.CTkFont(family="Consolas", size=12))
        self._log_text.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    def _build_browser_tab(self, parent):
        """Build the offline paper browser tab using ets_parser."""
        try:
            from ets_parser import create_browser_tab
            create_browser_tab(parent)
        except ImportError as e:
            ctk.CTkLabel(parent, text="加载离线浏览器失败: %s" % e,
                         text_color="red").pack(padx=20, pady=20)

    def _on_mode_change(self):
        """Update max label based on selected mode."""
        if self._mode_var.get() == self.MODE_PK:
            self._max_label.configure(text="题目上限")
        else:
            self._max_label.configure(text="步数上限")

    # ── Start / Stop ─────────────────────────────────────────
    def _on_start(self):
        if self._running:
            return

        # Validate port
        try:
            port = int(self._port_var.get())
            assert 1 <= port <= 65535
        except (ValueError, AssertionError):
            self._append_log("[错误] 无效端口号 (1-65535)\n")
            return

        # Validate max
        try:
            max_val = int(self._max_var.get())
            assert max_val > 0
        except (ValueError, AssertionError):
            self._append_log("[错误] 无效步数上限\n")
            return

        self._running = True
        self._stop_event.clear()
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._status_var.set("运行中...")

        # Redirect stdout/stderr to queue
        self._queue_writer_out = QueueWriter(self._log_queue, original=sys.stdout)
        self._queue_writer_err = QueueWriter(self._log_queue, original=sys.stderr)
        sys.stdout = self._queue_writer_out
        sys.stderr = self._queue_writer_err

        mode = self._mode_var.get()
        debug = self._debug_var.get()

        self._worker = threading.Thread(
            target=self._run_worker,
            args=(mode, port, debug, max_val),
            daemon=True
        )
        self._worker.start()

    def _on_stop(self):
        if not self._running:
            return
        self._stop_event.set()
        self._status_var.set("正在停止...")
        self._append_log("\n[用户] 请求停止...\n")

    def _run_finished(self):
        """Called on main thread when worker finishes."""
        # Restore stdout/stderr
        if hasattr(self, '_queue_writer_out') and self._queue_writer_out.original:
            sys.stdout = self._queue_writer_out.original
        if hasattr(self, '_queue_writer_err') and self._queue_writer_err.original:
            sys.stderr = self._queue_writer_err.original

        self._running = False
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        if self._stop_event.is_set():
            self._status_var.set("已停止")
        else:
            self._status_var.set("已完成")

    # ── Worker thread ────────────────────────────────────────
    def _run_worker(self, mode, port, debug, max_val):
        """Run the selected automation in a background thread."""
        try:
            if mode == self.MODE_EXAM:
                from ets_auto import ETSAutoAnswer
                auto = ETSAutoAnswer(port=port, debug_mode=debug)

                # Wire up stop check
                original_run = auto.run
                def _run_with_stop(*args, **kwargs):
                    # Monkey-patch time.sleep to check stop_event
                    _orig_sleep = time.sleep
                    def _interruptible_sleep(seconds):
                        if self._stop_event.is_set():
                            raise InterruptedError("User stopped")
                        # Sleep in small chunks for responsiveness
                        end = time.time() + seconds
                        while time.time() < end:
                            if self._stop_event.is_set():
                                raise InterruptedError("User stopped")
                            _orig_sleep(min(0.2, end - time.time()))
                    time.sleep = _interruptible_sleep
                    try:
                        return original_run(*args, **kwargs)
                    finally:
                        time.sleep = _orig_sleep

                auto.run = _run_with_stop
                try:
                    auto.run(max_steps=max_val)
                except InterruptedError:
                    print("\n已停止")
                except (ConnectionError, TimeoutError) as e:
                    print("\n连接断开: %s" % e)
                except Exception as e:
                    print("\n错误: %s" % e)

            elif mode == self.MODE_PK:
                from ets_word_pk import ETSWordPK
                pk = ETSWordPK(port=port, debug_mode=debug)

                # Wire up stop check via sleep patching
                _orig_sleep = time.sleep
                def _interruptible_sleep(seconds):
                    if self._stop_event.is_set():
                        raise InterruptedError("User stopped")
                    end = time.time() + seconds
                    while time.time() < end:
                        if self._stop_event.is_set():
                            raise InterruptedError("User stopped")
                        _orig_sleep(min(0.2, end - time.time()))
                time.sleep = _interruptible_sleep

                try:
                    pk.run(max_q=max_val)
                except InterruptedError:
                    print("\n已停止")
                except (ConnectionError, TimeoutError) as e:
                    print("\n连接断开: %s" % e)
                except Exception as e:
                    print("\n错误: %s" % e)
                finally:
                    time.sleep = _orig_sleep

        except ImportError as e:
            print("[错误] 导入失败: %s" % e)
            print("请确保 ets_auto.py / ets_word_pk.py / ets_common.py 在正确路径")
        except Exception as e:
            print("[错误] %s" % e)
        finally:
            # Schedule UI update on main thread
            self.after(0, self._run_finished)

    # ── Log polling ──────────────────────────────────────────
    def _poll_log(self):
        """Drain the log queue and update the textbox (runs on main thread)."""
        while True:
            try:
                msg = self._log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_log(msg)
        # Re-schedule every 100ms
        self.after(100, self._poll_log)

    def _append_log(self, text):
        """Append text to the log widget."""
        self._log_text.configure(state="normal")
        self._log_text.insert("end", text)
        self._log_text.configure(state="disabled")
        # Auto-scroll to bottom
        self._log_text.see("end")


# ── Entry point ──────────────────────────────────────────────
def main():
    app = ETSApp()
    app.mainloop()


if __name__ == "__main__":
    main()
