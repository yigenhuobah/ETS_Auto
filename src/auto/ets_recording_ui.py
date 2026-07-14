#!/usr/bin/env python3
"""Recording-question helpers for ETSAutoAnswer (window + wait).

Extracted from ets_auto to shrink the exam core module.
Mixin expects ETSBase/ETSAutoAnswer attributes: eval_js, stop_event,
_signal_stop, interruptible_sleep, recording_answers, debug, _fire_question,
_recording_window_closed, _tk_root, _rec_done_event.
"""
import time


class ETSRecordingMixin:
    # Poll JS constants (avoid re-allocating every poll)
    _JS_IS_RECORDING = r'''(function(){
        var btn = document.querySelector('.btn-stopRecord');
        if (!btn) return JSON.stringify({is_recording: false});
        return JSON.stringify({is_recording: btn.offsetHeight > 0});
    })()'''
    _JS_NEXT_READY = r'''(function(){
        var btns = document.querySelectorAll('button');
        for (var i = 0; i < btns.length; i++) {
            if (btns[i].textContent.trim() === '下一步' && !btns[i].disabled) {
                return JSON.stringify({next_ready: true});
            }
        }
        return JSON.stringify({next_ready: false});
    })()'''

    def is_recording_page(self):
        """Check if current page is a recording question (btn-stopRecord visible)."""
        try:
            r = self.parse_eval_json(self.eval_js(self._JS_IS_RECORDING))
            if r.get('error'):
                return False
            return bool(r.get('is_recording', False))
        except (ConnectionError, TimeoutError, InterruptedError):
            raise
        except Exception:
            return False


    def wait_for_recording_done(self, max_wait=600):
        """Wait for user to finish recording manually. Polls until 'next' button
        becomes enabled (recording submitted) or timeout. Returns True if next
        became available, False on timeout/stop."""
        print("\n" + "=" * 40)
        print("🎤 \u5f55\u97f3\u9898\u5df2\u5230\u8fbe\uff01\u8bf7\u624b\u52a8\u5b8c\u6210\u5f55\u97f3\uff1a")
        print("   1. \u70b9\u51fb\u9875\u9762\u4e0a\u7684\u5f55\u97f3\u6309\u94ae\u5f00\u59cb\u5f55\u97f3")
        print("   2. \u5f55\u97f3\u7ed3\u675f\u540e\u70b9\u51fb\u201c\u7ed3\u675f\u5f55\u97f3\u201d")
        print("   3. \u63d0\u4ea4\u540e\u811a\u672c\u4f1a\u81ea\u52a8\u7ee7\u7eed")
        print("   \u7b49\u5f85\u6700\u957f %d \u79d2\uff08%d \u5206\u949f\uff09" % (max_wait, max_wait // 60))
        print("=" * 40 + "\n")

        # Notify GUI via callback
        self._fire_question({'type': 'recording', 'type_label': '\u5f55\u97f3\u9898-\u7b49\u5f85\u624b\u52a8\u5b8c\u6210',
                             'step': 'recording_wait'})

        start = time.time()
        notified_5min = False
        notified_1min = False

        while time.time() - start < max_wait:
            # Check stop signal
            if self.stop_event and self.stop_event.is_set():
                return False
            if self._recording_window_closed:
                return False

            # Check if next button is now enabled (recording done)
            try:
                r = self.parse_eval_json(self.eval_js(self._JS_NEXT_READY))
                if r.get('next_ready'):
                    elapsed = int(time.time() - start)
                    print("\u2705 \u5f55\u97f3\u5b8c\u6210\uff08\u8017\u65f6 %d \u79d2\uff09\uff0c\u7ee7\u7eed\u7b54\u9898" % elapsed)
                    return True
            except (ConnectionError, TimeoutError, InterruptedError):
                raise
            except Exception:
                pass

            # Progress notifications
            elapsed = time.time() - start
            if not notified_5min and elapsed > 300:
                print("\u23f0 \u5df2\u7b49\u5f85 5 \u5206\u949f\uff0c\u8bf7\u5c3d\u5feb\u5b8c\u6210\u5f55\u97f3")
                notified_5min = True
            if not notified_1min and elapsed > max_wait - 60:
                print("\u26a0\ufe0f \u5269\u4f59\u4e0d\u8db3 1 \u5206\u949f\uff0c\u5373\u5c06\u8d85\u65f6\u9000\u51fa")
                notified_1min = True

            self.interruptible_sleep(3)

        print("\u23f0 \u5f55\u97f3\u7b49\u5f85\u8d85\u65f6\uff08%d \u5206\u949f\uff09\uff0c\u811a\u672c\u9000\u51fa" % (max_wait // 60))
        return False

    # ── Choose Answer ─────────────────────────────────────────

    def _build_recording_window(self, root, poll_worker_fn=None, on_close=None):
        """Build recording answers window content on the given Tk root or Toplevel.
        Shared by CLI (tk.Tk) and GUI (tk.Toplevel) paths.

        on_close: optional callable for button / WM_DELETE / Escape. Default
        signals stop and destroys root. Callers that need extra flags should
        pass one handler instead of rebinding protocol after build.
        """
        import tkinter as tk
        from tkinter import scrolledtext

        self._tk_root = root  # Assign immediately so _poll_worker can reference it
        root.title('[Recording] 录音题参考答案')
        root.configure(bg='#1e1e2e')

        width, height = 750, 600
        x = (root.winfo_screenwidth() - width) // 2
        y = (root.winfo_screenheight() - height) // 3
        root.geometry('%dx%d+%d+%d' % (width, height, x, y))
        root.minsize(500, 350)
        root.resizable(True, True)

        # Header
        header = tk.Frame(root, bg='#2d2d3f', height=48)
        header.pack(fill='x')
        header.pack_propagate(False)
        hlbl = tk.Label(header, text='🎙 录音题参考答案（关闭窗口即停止脚本）',
                       bg='#2d2d3f', fg='#cdd6f4', font=('Microsoft YaHei UI', 13, 'bold'))
        hlbl.pack(side='left', padx=(16, 0), pady=10)

        # Content
        main = tk.Frame(root, bg='#1e1e2e')
        main.pack(fill='both', expand=True, padx=16, pady=(12, 8))

        st = scrolledtext.ScrolledText(
            main, wrap='word', bg='#313244', fg='#cdd6f4',
            insertbackground='#f5c2e7', borderwidth=0, relief='flat',
            font=('Microsoft YaHei UI', 11), padx=14, pady=12
        )
        st.pack(fill='both', expand=True)

        type_labels = {'picture': '听后转述', 'dialogue': '回答问题', 'role': '口语问答', 'read': '短文朗读'}
        type_icons = {'picture': '📖', 'dialogue': '💬', 'role': '🗣', 'read': '📚'}

        all_text = []
        for idx_r, rec in enumerate(self.recording_answers):
            rtype = rec['type']
            icon = type_icons.get(rtype, '🎤')
            label = type_labels.get(rtype, '录音题')
            topic = rec.get('topic', '') or rec.get('symbol', '')

            all_text.append('%s %s %s' % (icon, label, ('— ' + topic) if topic else ''))
            all_text.append('=' * 40)

            # Dialogue/role: show per-question answers (question + reference answer)
            if rtype in ('dialogue', 'role') and rec.get('q_answers'):
                for qi, qa in enumerate(rec['q_answers']):
                    all_text.append('Q%d: %s' % (qi + 1, qa.get('ask', '')))
                    ref = qa.get('answer', '')
                    if ref:
                        all_text.append('  → %s' % ref)
                    else:
                        all_text.append('  → (无参考答案)')
                    all_text.append('')
                # Also show material text as context
                material = rec.get('answer', '')
                if material:
                    all_text.append('📝 原文材料：')
                    all_text.append(material)
            else:
                answer_text = rec['answer']
                all_text.append(answer_text)
                # Add questions for dialogue/role type (fallback, no q_answers)
                if rtype in ('dialogue', 'role') and rec.get('questions'):
                    all_text.append('')
                    all_text.append('参考问题：')
                    for qi, q in enumerate(rec['questions']):
                        all_text.append('  %d. %s' % (qi + 1, q))

            if idx_r < len(self.recording_answers) - 1:
                all_text.append('')
                all_text.append('-' * 60)
                all_text.append('')

        st.insert('1.0', '\n'.join(all_text))
        st.configure(state='disabled')

        # Bottom bar
        bar = tk.Frame(root, bg='#2d2d3f', height=64)
        bar.pack(fill='x')
        bar.pack_propagate(False)

        hint = tk.Label(bar, text='💡 录音时参考此答案，关闭窗口即停止脚本',
                       bg='#2d2d3f', fg='#a6adc8', font=('Microsoft YaHei UI', 10))
        hint.pack(side='left', padx=(16, 0), pady=14)

        def _default_close():
            # Signal stop_event so worker thread exits cleanly
            self._signal_stop()
            root.destroy()

        close_handler = on_close or _default_close

        btn = tk.Button(bar, text='✅ 关闭并停止脚本', command=close_handler,
                       bg='#f38ba8', fg='#1e1e2e',
                       activebackground='#eba0ac', activeforeground='#1e1e2e',
                       font=('Microsoft YaHei UI', 12, 'bold'), relief='flat', padx=24, pady=8,
                       cursor='hand2')
        btn.pack(side='right', padx=(0, 16), pady=12)
        root.protocol('WM_DELETE_WINDOW', close_handler)
        root.bind('<Escape>', lambda e: close_handler())

        print('[REC] Recording answers window opened (%d types)' % len(self.recording_answers))

        # Register poll_worker AFTER root is created (fixes _tk_root AttributeError)
        if poll_worker_fn:
            root.after(500, poll_worker_fn)

        root.focus_force()

    def _existing_tk_root(self):
        """Return tk._default_root if a Tk app already exists (GUI mode)."""
        import tkinter as tk
        try:
            return tk._default_root
        except AttributeError:
            return None

    def open_recording_window_async(self, poll_worker_fn=None, ready_event=None):
        """GUI: schedule a non-blocking Toplevel on the existing Tk root.

        Used when the exam loop already runs on the GUI worker thread.
        Returns True if a create was scheduled, False if no root / no answers.

        ready_event: optional threading.Event set when create finishes (ok or fail)
        so callers can wait instead of a fixed sleep race.
        """
        if not self.recording_answers:
            return False
        import tkinter as tk
        existing_root = self._existing_tk_root()
        if existing_root is None:
            return False

        def _open_on_main():
            try:
                win = tk.Toplevel(existing_root)

                def _on_close():
                    self._recording_window_closed = True
                    self._signal_stop()
                    try:
                        win.destroy()
                    except Exception:
                        pass

                self._build_recording_window(
                    win, poll_worker_fn, on_close=_on_close)
            except Exception as e:
                print('[REC] Error creating Toplevel: %s' % e)
            finally:
                if ready_event is not None:
                    ready_event.set()

        existing_root.after(0, _open_on_main)
        return True

    def show_recording_answers_window(self, poll_worker_fn=None):
        """CLI: blocking recording window (tk.Tk + mainloop on this thread).

        For GUI (existing root), prefer open_recording_window_async() so the
        exam loop is not nested under a second worker. This method still
        supports GUI blocking wait for callers that need it.
        """
        if not self.recording_answers:
            return False
        import tkinter as tk

        existing_root = self._existing_tk_root()
        if existing_root is not None:
            # Blocking GUI wait (legacy): Toplevel + Event until close/stop
            import threading as _th
            done = _th.Event()
            self._rec_done_event = done
            user_closed = {'v': False}
            bound_handler = {'funcid': None}

            def _create_on_main():
                try:
                    win = tk.Toplevel(existing_root)

                    def _on_rec_closed(_e=None):
                        done.set()

                    bound_handler['funcid'] = existing_root.bind(
                        '<<RecWindowClosed>>', _on_rec_closed, add='+')

                    def _on_destroy(e):
                        if e.widget is win:
                            done.set()

                    win.bind('<Destroy>', _on_destroy)

                    def _on_close():
                        user_closed['v'] = True
                        self._signal_stop()
                        try:
                            win.destroy()
                        except Exception:
                            pass
                        done.set()

                    # Single close handler for button / X / Escape
                    self._build_recording_window(
                        win, poll_worker_fn, on_close=_on_close)
                except Exception as e:
                    print('[REC] Error creating Toplevel: %s' % e)
                    done.set()

            existing_root.after(0, _create_on_main)
            while not done.is_set():
                if self.stop_event and self.stop_event.is_set():
                    try:
                        if self._tk_root is not None and self._tk_root.winfo_exists():
                            self._tk_root.destroy()
                    except Exception:
                        pass
                    self._recording_window_closed = True
                    done.set()
                    break
                done.wait(timeout=0.5)
            try:
                fid = bound_handler.get('funcid')
                if fid:
                    existing_root.unbind('<<RecWindowClosed>>', fid)
            except Exception:
                pass
            if user_closed['v']:
                self._recording_window_closed = True
            self._rec_done_event = None
            return True

        # CLI: create new Tk root + mainloop (blocks this thread)
        root = tk.Tk()
        self._build_recording_window(root, poll_worker_fn)
        root.mainloop()
        self._recording_window_closed = True
        return True

