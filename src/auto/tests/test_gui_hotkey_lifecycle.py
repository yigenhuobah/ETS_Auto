#!/usr/bin/env python3
"""Lifecycle regression tests for the GUI and global hotkey manager."""

import io
import os
import queue
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(TEST_DIR)
if AUTO_DIR not in sys.path:
    sys.path.insert(0, AUTO_DIR)

import ets_hotkey
from ets_gui import ETSApp, QueueWriter


class _StuckThread:
    def __init__(self, target=None, daemon=None):
        self.target = target
        self.daemon = daemon
        self.alive = True
        self.join_calls = 0
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        del timeout
        self.join_calls += 1


class TestGuiLifecycle(unittest.TestCase):
    def test_close_signals_and_destroys_without_joining_worker(self):
        worker = _StuckThread()
        log_queue = queue.Queue()
        original_out = io.StringIO()
        original_err = io.StringIO()
        writer_out = QueueWriter(log_queue, original_out)
        writer_err = QueueWriter(log_queue, original_err)
        destroy = mock.Mock()
        app = SimpleNamespace(
            _closed=False,
            _running=True,
            _worker=worker,
            _stop_event=threading.Event(),
            _status_var=mock.Mock(),
            _queue_writer_out=writer_out,
            _queue_writer_err=writer_err,
            destroy=destroy,
        )
        app._restore_streams = ETSApp._restore_streams.__get__(app)

        saved_out, saved_err = sys.stdout, sys.stderr
        try:
            sys.stdout, sys.stderr = writer_out, writer_err
            ETSApp._on_close(app)
            self.assertTrue(app._closed)
            self.assertTrue(app._stop_event.is_set())
            self.assertFalse(app._running)
            self.assertEqual(worker.join_calls, 0)
            self.assertIs(sys.stdout, original_out)
            self.assertIs(sys.stderr, original_err)
            destroy.assert_called_once_with()

            writer_out.write("late worker output")
            self.assertIn("late worker output", original_out.getvalue())
            self.assertEqual(log_queue.get_nowait(), "late worker output")
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err

    def test_restore_streams_only_releases_owned_streams(self):
        log_queue = queue.Queue()
        original_out = io.StringIO()
        original_err = io.StringIO()
        writer_out = QueueWriter(log_queue, original_out)
        writer_err = QueueWriter(log_queue, original_err)
        newer_stdout = io.StringIO()
        app = SimpleNamespace(
            _queue_writer_out=writer_out,
            _queue_writer_err=writer_err,
        )

        saved_out, saved_err = sys.stdout, sys.stderr
        try:
            sys.stdout, sys.stderr = newer_stdout, writer_err
            ETSApp._restore_streams(app)
            self.assertIs(sys.stdout, newer_stdout)
            self.assertIs(sys.stderr, original_err)
            self.assertIsNone(app._queue_writer_out)
            self.assertIsNone(app._queue_writer_err)
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err

    def test_safe_after_rejects_close_and_destroy_races(self):
        closed = SimpleNamespace(_closed=True, after=mock.Mock())
        self.assertFalse(ETSApp._safe_after(closed, 0, lambda: None))
        closed.after.assert_not_called()

        destroyed = SimpleNamespace(
            _closed=False,
            after=mock.Mock(side_effect=RuntimeError("destroyed")),
        )
        self.assertFalse(ETSApp._safe_after(destroyed, 0, lambda: None))
        destroyed.after.assert_called_once()

    def test_worker_completion_falls_back_when_tk_cannot_schedule(self):
        restore = mock.Mock()
        app = SimpleNamespace(
            _running=True,
            _safe_after=mock.Mock(return_value=False),
            _restore_streams=restore,
            _run_finished=mock.Mock(),
        )
        app._worker_cleanup_without_ui = (
            ETSApp._worker_cleanup_without_ui.__get__(app)
        )

        self.assertFalse(ETSApp._schedule_run_finished(app))
        restore.assert_called_once_with()
        self.assertFalse(app._running)
        app._run_finished.assert_not_called()

    def test_closed_log_poll_does_not_reschedule(self):
        app = SimpleNamespace(
            _closed=True,
            _log_queue=queue.Queue(),
            _append_log=mock.Mock(),
            _safe_after=mock.Mock(),
        )
        ETSApp._poll_log(app)
        app._append_log.assert_not_called()
        app._safe_after.assert_not_called()

    def test_start_does_not_claim_hotkeys_before_registration(self):
        hotkey_var = mock.Mock()
        app = SimpleNamespace(
            _running=False,
            _remote_is_blocked=mock.Mock(
                side_effect=[(False, ''), (True, 'blocked')]),
            _apply_remote_block=mock.Mock(),
            _port_var=mock.Mock(get=mock.Mock(return_value='10086')),
            _max_var=mock.Mock(get=mock.Mock(return_value='10')),
            _append_log=mock.Mock(),
            _stop_event=threading.Event(),
            _start_btn=mock.Mock(),
            _stop_btn=mock.Mock(),
            _status_var=mock.Mock(),
            _hotkey_var=hotkey_var,
            _progress_var=mock.Mock(),
            _progress_label_var=mock.Mock(),
        )

        ETSApp._on_start(app)

        pending_text = hotkey_var.set.call_args.args[0]
        self.assertIn("热键状态见运行日志", pending_text)
        self.assertNotIn("F9", pending_text)
        self.assertNotIn("F10", pending_text)
        self.assertNotIn("F12", pending_text)


class TestHotkeyLifecycle(unittest.TestCase):
    def tearDown(self):
        with ets_hotkey.ETSHotkey._UNRESOLVED_PUMPS_GUARD:
            ets_hotkey.ETSHotkey._UNRESOLVED_PUMPS.clear()
        super().tearDown()

    def test_readiness_timeout_keeps_orphan_handle_and_blocks_second_pump(self):
        created = []

        def make_thread(target=None, daemon=None):
            thread = _StuckThread(target=target, daemon=daemon)
            created.append(thread)
            return thread

        hotkey = ets_hotkey.ETSHotkey()
        hotkey._PUMP_READY_TIMEOUT = 0.01
        hotkey._STOP_TIMEOUT = 0
        output = io.StringIO()
        with mock.patch.object(ets_hotkey.threading, 'Thread', make_thread):
            with redirect_stdout(output):
                self.assertFalse(hotkey.register())
                first_thread = hotkey._thread
                self.assertFalse(hotkey.register())

        self.assertEqual(len(created), 1)
        self.assertIs(hotkey._thread, first_thread)
        self.assertTrue(first_thread.is_alive())
        self.assertEqual(first_thread.join_calls, 2)
        self.assertIn("previous listener is still stopping", output.getvalue())

    def test_native_initialization_failure_releases_readiness_waiter(self):
        hotkey = ets_hotkey.ETSHotkey()
        hotkey._run_message_pump = mock.Mock(
            side_effect=RuntimeError("native init failed"))
        hotkey._do_unregister = mock.Mock()

        hotkey._message_pump()

        self.assertTrue(hotkey._pump_ready.is_set())
        self.assertIsInstance(hotkey._pump_error, RuntimeError)
        hotkey._do_unregister.assert_called_once_with()

    def test_dead_pump_after_init_failure_returns_without_hanging(self):
        hotkey = ets_hotkey.ETSHotkey()
        hotkey._PUMP_READY_TIMEOUT = 0.5

        class FailedThread(_StuckThread):
            def start(self):
                self.started = True
                hotkey._pump_error = RuntimeError("init failed")
                hotkey._pump_ready.set()
                self.alive = False

        started_at = time.monotonic()
        with mock.patch.object(ets_hotkey.threading, 'Thread', FailedThread):
            with redirect_stdout(io.StringIO()):
                self.assertFalse(hotkey.register())
        self.assertLess(time.monotonic() - started_at, 0.2)
        self.assertIsNone(hotkey._thread)
        self.assertIsNone(hotkey._thread_id)

    def test_stop_timeout_preserves_thread_and_thread_id(self):
        hotkey = ets_hotkey.ETSHotkey()
        hotkey._STOP_TIMEOUT = 0
        thread = _StuckThread()
        hotkey._thread = thread
        hotkey._thread_id = 402

        with mock.patch.object(ets_hotkey, 'PostThreadMessage', return_value=True):
            self.assertFalse(hotkey._stop_pump())

        self.assertIs(hotkey._thread, thread)
        self.assertEqual(hotkey._thread_id, 402)
        self.assertEqual(thread.join_calls, 1)
        self.assertTrue(hotkey._stopping)
        self.assertFalse(hotkey._registered)

    def test_unregister_timeout_blocks_false_successful_reregister(self):
        hotkey = ets_hotkey.ETSHotkey()
        hotkey._STOP_TIMEOUT = 0
        thread = _StuckThread()
        hotkey._thread = thread
        hotkey._thread_id = 402
        hotkey._registered = True
        hotkey._bindings = {'F9': True, 'F10': True, 'F12': True}

        output = io.StringIO()
        with mock.patch.object(
                ets_hotkey, 'PostThreadMessage', return_value=True), \
                redirect_stdout(output):
            self.assertFalse(hotkey.unregister())
            self.assertFalse(hotkey.register())

        self.assertFalse(hotkey._registered)
        self.assertTrue(hotkey._stopping)
        self.assertEqual(
            hotkey._bindings,
            {'F9': False, 'F10': False, 'F12': False},
        )
        self.assertIs(hotkey._thread, thread)
        self.assertEqual(thread.join_calls, 2)
        self.assertIn("previous listener is still stopping", output.getvalue())

    def test_unresolved_pump_is_retained_and_blocks_another_instance(self):
        first = ets_hotkey.ETSHotkey(on_stop=mock.Mock())
        first._STOP_TIMEOUT = 0
        thread = _StuckThread()
        first._thread = thread
        first._thread_id = 402
        first._registered = True
        second = ets_hotkey.ETSHotkey(on_stop=mock.Mock())
        second._STOP_TIMEOUT = 0
        created = []

        def make_thread(target=None, daemon=None):
            created.append(_StuckThread(target=target, daemon=daemon))
            return created[-1]

        try:
            output = io.StringIO()
            with mock.patch.object(
                    ets_hotkey, 'PostThreadMessage', return_value=True), \
                    mock.patch.object(
                        ets_hotkey.threading, 'Thread', make_thread), \
                    redirect_stdout(output):
                self.assertFalse(first.unregister())
                self.assertFalse(second.register())

            self.assertIn(
                first, ets_hotkey.ETSHotkey._unresolved_pumps())
            self.assertIsNone(first._on_stop)
            self.assertEqual(created, [])
            self.assertIn(
                'earlier listener is still stopping', output.getvalue())
        finally:
            thread.alive = False
            first._stop_pump()

        self.assertNotIn(
            first, ets_hotkey.ETSHotkey._unresolved_pumps())

    def test_pump_exit_after_registration_is_not_reported_as_success(self):
        hotkey = ets_hotkey.ETSHotkey()
        created = []

        class ReadyThread(_StuckThread):
            def start(self):
                self.started = True
                hotkey._thread_id = 91
                hotkey._pump_ready.set()

        def make_thread(target=None, daemon=None):
            thread = ReadyThread(target=target, daemon=daemon)
            created.append(thread)
            return thread

        def post_message(_thread_id, message, _wparam, _lparam):
            if message == 0x0401:
                hotkey._reg_result = {'F9': True, 'F10': False, 'F12': False}
                hotkey._reg_done.set()
                created[0].alive = False
            return True

        with mock.patch.object(ets_hotkey.threading, 'Thread', make_thread), \
                mock.patch.object(ets_hotkey, 'PostThreadMessage', post_message), \
                redirect_stdout(io.StringIO()):
            self.assertFalse(hotkey.register())

        self.assertFalse(hotkey._registered)
        self.assertIsNone(hotkey._thread)
    def test_partial_registration_reports_only_successful_bindings(self):
        hotkey = ets_hotkey.ETSHotkey()
        created = []

        class ReadyThread(_StuckThread):
            def start(self):
                self.started = True
                hotkey._thread_id = 73
                hotkey._pump_ready.set()

        def make_thread(target=None, daemon=None):
            thread = ReadyThread(target=target, daemon=daemon)
            created.append(thread)
            return thread

        def post_message(_thread_id, message, _wparam, _lparam):
            if message == 0x0401:
                hotkey._do_register()
            elif message == ets_hotkey.WM_QUIT:
                created[0].alive = False
            return True

        register_api = mock.Mock(
            side_effect=[True, False, False, False, False])
        output = io.StringIO()
        with mock.patch.object(ets_hotkey.threading, 'Thread', make_thread), \
                mock.patch.object(ets_hotkey, 'PostThreadMessage', post_message), \
                mock.patch.object(ets_hotkey, 'RegisterHotKey', register_api), \
                mock.patch.object(ets_hotkey, 'UnregisterHotKey'):
            with redirect_stdout(output):
                self.assertTrue(hotkey.register())
            self.assertEqual(
                hotkey._bindings,
                {'F9': True, 'F10': False, 'F12': False},
            )
            self.assertTrue(hotkey.unregister())

        status = output.getvalue()
        self.assertIn("Hotkeys: F9=Pause", status)
        self.assertIn("unavailable: F10, F12", status)
        self.assertNotIn("F10=Skip", status)
        self.assertNotIn("F12=Stop", status)
        for call in register_api.call_args_list:
            self.assertTrue(call.args[2] & ets_hotkey.MOD_NOREPEAT)


if __name__ == '__main__':
    unittest.main()
