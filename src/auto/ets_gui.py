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
from ets_common import force_utf8_stdio
force_utf8_stdio()

import customtkinter as ctk

# Version constant — bump on each release
APP_VERSION = "0.6.3"


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
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr

        # Theme
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Clean shutdown on window close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Remote info state
        self._remote_info = None

        self._build_ui()
        self._poll_log()

        # Background remote check (non-blocking)
        self._check_remote_async()

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

        # #1: Progress bar
        self._progress_var = ctk.DoubleVar(value=0.0)
        self._progress_bar = ctk.CTkProgressBar(parent, variable=self._progress_var, height=14)
        self._progress_bar.pack(fill="x", padx=16, pady=(0, 4))
        self._progress_bar.set(0.0)

        # Progress label
        self._progress_label_var = ctk.StringVar(value="")
        self._progress_label = ctk.CTkLabel(
            parent, textvariable=self._progress_label_var, anchor="w",
            font=ctk.CTkFont(size=11), text_color="gray")
        self._progress_label.pack(fill="x", padx=16, pady=(0, 4))

        # Hotkey hint
        self._hotkey_var = ctk.StringVar(value="")
        self._hotkey_label = ctk.CTkLabel(
            parent, textvariable=self._hotkey_var, anchor="w",
            font=ctk.CTkFont(size=11), text_color="gray")
        self._hotkey_label.pack(fill="x", padx=16, pady=(0, 4))

        # Log output
        self._log_text = ctk.CTkTextbox(
            parent, wrap="word", state="disabled",
            font=ctk.CTkFont(family="Consolas", size=12))
        self._log_text.pack(fill="both", expand=True, padx=16, pady=(0, 4))

        # ── Collapsible answer preview bar ──────────────
        self._preview_expanded = False
        self._preview_frame = ctk.CTkFrame(parent, fg_color="transparent", height=28)
        self._preview_frame.pack(fill="x", padx=16, pady=(0, 12))
        self._preview_frame.pack_propagate(False)

        # Toggle button
        self._preview_toggle_btn = ctk.CTkButton(
            self._preview_frame,
            text="📋 答案预览 ▸", width=140, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent", hover_color=("#e0e0e0", "#3a3a3a"),
            text_color=("#555555", "#aaaaaa"),
            anchor="w",
            command=self._toggle_preview)
        self._preview_toggle_btn.pack(side="left", padx=(0, 8))

        # Inline answer text (shown when collapsed)
        self._preview_inline = ctk.CTkLabel(
            self._preview_frame,
            text="", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#2ecc71")
        self._preview_inline.pack(side="left", fill="x", expand=True)

        # Expanded preview panel (hidden by default)
        self._preview_panel = None

    def _toggle_preview(self):
        """Toggle the answer preview panel."""
        self._preview_expanded = not self._preview_expanded
        if self._preview_expanded:
            self._preview_toggle_btn.configure(text="📋 答案预览 ▾")
            # Create expanded panel
            self._preview_frame.pack_propagate(True)
            self._preview_frame.configure(height=160)
            self._preview_panel = ctk.CTkTextbox(
                self._preview_frame, wrap="word", state="disabled",
                font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
                height=120, corner_radius=6)
            self._preview_panel.pack(fill="both", expand=True, pady=(28, 0))
            # Move toggle btn and inline label to top of frame
            self._preview_toggle_btn.pack_forget()
            self._preview_inline.pack_forget()
            self._preview_toggle_btn.pack(side="top", anchor="w", padx=(0, 8), pady=(0, 2))
            self._preview_inline.pack_forget()  # hide inline when expanded
        else:
            self._preview_toggle_btn.configure(text="📋 答案预览 ▸")
            if self._preview_panel:
                self._preview_panel.destroy()
                self._preview_panel = None
            self._preview_frame.configure(height=28)
            self._preview_frame.pack_propagate(False)
            # Restore layout
            self._preview_toggle_btn.pack_forget()
            self._preview_inline.pack_forget()
            self._preview_toggle_btn.pack(side="left", padx=(0, 8))
            self._preview_inline.pack(side="left", fill="x", expand=True)

    def _update_answer_preview(self, info):
        """Called from on_question callback to update preview display.

        info dict keys: type, type_label, qid, answer, answered, total_questions
        Thread-safe: schedules UI update on main thread via after().
        """
        # Schedule on main thread — callback may fire from worker thread
        self.after(0, lambda: self._do_update_answer_preview(info))

    def _do_update_answer_preview(self, info):
        """Actual UI update — always runs on main thread."""
        # Guard: widget may have been destroyed if window closed
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        try:
            qtype = info.get('type_label', '')
            answer = info.get('answer', '')
            qid = info.get('qid', '')
            answered = info.get('answered', 0)
            total = info.get('total_questions', 0)

            # Update inline text (always visible)
            inline_text = "%s %s → %s" % (qtype, qid, answer) if qid else qtype
            if total:
                inline_text += "  (%d/%d)" % (answered, total)
            self._preview_inline.configure(text=inline_text)

            # #1: Update progress bar
            if total and total > 0:
                pct = min(answered / total, 1.0)
                self._progress_var.set(pct)
                self._progress_label_var.set("进度: %d/%d (%.0f%%)" % (answered, total, pct * 100))
            elif answered:
                self._progress_label_var.set("已处理: %d" % answered)

            # Update expanded panel if open
            if self._preview_expanded and self._preview_panel:
                self._preview_panel.configure(state="normal")
                # Append new answer line
                line = "[%s] %s → %s" % (qtype, qid, answer) if qid else "[%s]" % qtype
                self._preview_panel.insert("end", line + "\n")
                self._preview_panel.configure(state="disabled")
                self._preview_panel.see("end")
        except Exception:
            pass  # Widget destroyed or not ready

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

        # Use already-fetched remote info (populated at startup or last check).
        # Bug fix: removed _check_remote_async() + sleep(0.3) here — it blocked
        # the tkinter main thread for 300ms causing UI freeze, and 0.3s wasn't
        # enough for slow networks anyway. The remote check runs at GUI launch
        # and via _on_remote_checked callback; we just use whatever is available.
        if self._remote_info is not None:
            try:
                from ets_remote import classify_info
                level, reason = classify_info(self._remote_info)
                if level == "block":
                    self._append_log("[远程] ⛔ %s\n" % reason)
                    if self._remote_info.download_url:
                        self._append_log("[远程] 下载地址：%s\n" % self._remote_info.download_url)
                    return
            except ImportError:
                pass  # ets_remote not available — allow start

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
        self._hotkey_var.set("⌨ F9暂停/恢复  F10跳过  F12停止")
        # Reset progress bar
        self._progress_var.set(0.0)
        self._progress_label_var.set("")

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

    # ── Remote Check ────────────────────────────────────
    def _check_remote_async(self):
        """Start background thread to check remote info.
        Network failures are logged but never crash the GUI."""
        def _worker():
            try:
                from ets_remote import ETSRemote, should_block_start, format_update_message
                remote = ETSRemote(current_version=APP_VERSION)
                info = remote.check()
                self._remote_info = info
                if info is not None:
                    self.after(0, self._on_remote_checked, info)
            except Exception as e:
                # Log to terminal but don't crash GUI — remote check is non-critical
                print("[Remote] Check failed: %s" % e)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_remote_checked(self, info):
        """Handle remote check result on main thread."""
        from ets_remote import classify_info, format_update_message

        # Unified block/normal decision
        level, reason = classify_info(info)
        if level == "block":
            self._status_var.set("⛔ %s" % reason)
            self._start_btn.configure(state="disabled")
            self._append_log("[远程] ⛔ %s\n" % reason)
            if info.download_url:
                self._append_log("[远程] 下载地址：%s\n" % info.download_url)
            return

        # Show announcement / update info
        msg = format_update_message(info, APP_VERSION)
        if msg:
            self._append_log("[远程] %s\n" % msg.replace('\n', '\n[远程] '))

        # Auto-download pk_extra.json update if URL available
        if info.pk_extra_url:
            self._try_update_pk_extra()

    def _try_update_pk_extra(self):
        """Attempt silent pk_extra.json update in background.

        Delegates to ETSRemote.download_pk_extra() for mirror fallback,
        backup/restore, and JSON validation.
        """
        def _worker():
            try:
                from ets_remote import ETSRemote
                # Use the pk_extra_url from the remote info already fetched in _on_remote_checked
                pk_url = getattr(self._remote_info, 'pk_extra_url', None) if self._remote_info else None
                if not pk_url:
                    return  # no URL available, skip silently
                remote = ETSRemote(current_version=APP_VERSION)
                success, message = remote.download_pk_extra(url=pk_url)
                if success:
                    self.after(0, lambda: self._append_log(
                        "[远程] %s\n" % message))
                else:
                    self.after(0, lambda: self._append_log(
                        "[远程] ⚠️ %s\n" % message))
            except Exception as e:
                pass  # pk_extra update is non-critical

        threading.Thread(target=_worker, daemon=True).start()

    def _on_stop(self):
        if not self._running:
            return
        self._stop_event.set()
        self._status_var.set("正在停止...")
        self._append_log("\n[用户] 请求停止...\n")

    def _on_close(self):
        """Handle window close: stop worker, wait briefly, then destroy."""
        if self._running:
            self._stop_event.set()
            self._status_var.set("正在停止...")
            # Wait for worker to exit (max 3s)
            if self._worker and self._worker.is_alive():
                self._worker.join(timeout=3)
            self._restore_streams()
        self.destroy()

    def _restore_streams(self):
        """Restore stdout/stderr to original, drain remaining log queue.
        Bug fix: join worker thread briefly first to close the race window
        where QueueWriter still writes after streams are restored."""
        # Wait briefly for worker thread to fully exit so it stops writing
        # to QueueWriter (which may still reference this queue).
        if self._worker and self._worker.is_alive() and self._worker is not threading.current_thread():
            self._worker.join(timeout=2)

        # Drain remaining log messages (already written to original by QueueWriter)
        while not self._log_queue.empty():
            try:
                self._log_queue.get_nowait()
            except queue.Empty:
                break

        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr

    def _run_finished(self):
        """Called on main thread when worker finishes."""
        self._restore_streams()

        self._running = False
        self._hotkey_var.set("")
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        # Bug fix: detect error state from worker thread
        had_error = getattr(self, '_worker_error', False)
        if self._stop_event.is_set():
            self._status_var.set("已停止")
            # Reset progress bar on stop
            self._progress_var.set(0.0)
            self._progress_label_var.set("")
        elif had_error:
            self._status_var.set("错误")
            # Keep progress bar where it is (partial progress visible)
            self._worker_error = False  # reset
        else:
            self._status_var.set("已完成")
            # Set progress bar to 100% if completed naturally
            self._progress_var.set(1.0)

    # ── Worker thread ────────────────────────────────────────
    def _run_worker(self, mode, port, debug, max_val):
        """Run the selected automation in a background thread.
        Uses stop_event passed to automation instances for clean interruption.
        No global monkey-patching of time.sleep."""
        self._worker_error = False
        try:
            if mode == self.MODE_EXAM:
                from ets_auto import ETSAutoAnswer
                auto = ETSAutoAnswer(port=port, debug_mode=debug,
                                     stop_event=self._stop_event)
                # Register on_question callback for real-time answer preview
                auto.on_question(self._update_answer_preview)
                try:
                    auto.run(max_steps=max_val)
                except InterruptedError:
                    print("\n已停止")
                except (ConnectionError, TimeoutError) as e:
                    print("\n连接断开: %s" % e)
                    self._worker_error = True
                except Exception as e:
                    print("\n错误: %s" % e)
                    self._worker_error = True

            elif mode == self.MODE_PK:
                from ets_word_pk import ETSWordPK
                pk = ETSWordPK(port=port, debug_mode=debug,
                               stop_event=self._stop_event)
                # Register on_question callback for real-time answer preview
                pk.on_question(self._update_answer_preview)
                try:
                    pk.run(max_q=max_val)
                except InterruptedError:
                    print("\n已停止")
                except (ConnectionError, TimeoutError) as e:
                    print("\n连接断开: %s" % e)
                    self._worker_error = True
                except Exception as e:
                    print("\n错误: %s" % e)
                    self._worker_error = True

        except ImportError as e:
            print("[错误] 导入失败: %s" % e)
            print("请确保 ets_auto.py / ets_word_pk.py / ets_common.py 在正确路径")
            self._worker_error = True
            self.after(0, lambda: self._show_error(
                "导入失败",
                "找不到必要模块：%s\n\n请确保 ets_auto.py / ets_word_pk.py / ets_common.py 在正确路径" % e))
        except Exception as e:
            print("[错误] %s" % e)
            self._worker_error = True
            self.after(0, lambda: self._show_error("运行错误", str(e)))
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

    def _show_error(self, title, message):
        """Show error messagebox on main thread (call via self.after)."""
        try:
            from tkinter import messagebox
            messagebox.showerror(title, message)
        except Exception:
            pass  # Don't crash if messagebox itself fails


# ── Entry point ──────────────────────────────────────────────
def main():
    app = ETSApp()
    app.mainloop()


if __name__ == "__main__":
    main()
