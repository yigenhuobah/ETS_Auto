#!/usr/bin/env python3
"""Regression tests for pk_extra persistence and connected-run cleanup."""
import io
import json
import multiprocessing
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch


AUTO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if AUTO_DIR not in sys.path:
    sys.path.insert(0, AUTO_DIR)

import ets_auto
import ets_hotkey
import ets_pk_store
import ets_remote
import ets_word_pk


def _write_json(path, data):
    with open(path, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False)


def _read_json(path):
    with open(path, 'r', encoding='utf-8') as stream:
        return json.load(stream)


def _process_merge_once(target, updates, ready, start, result):
    try:
        ready.put(True)
        if not start.wait(timeout=10):
            raise TimeoutError('process start barrier timed out')
        merged = ets_pk_store.merge_write_pk_extra(target, updates)
        result.put(('ok', merged))
    except BaseException as exc:
        result.put(('error', type(exc).__name__, str(exc)))


def _terminate_process(process):
    if process.is_alive():
        process.terminate()
    process.join(timeout=5)


class TestPKExtraStore(unittest.TestCase):
    def test_canonical_aliases_share_one_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            direct = os.path.join(tmp, 'pk_extra.json')
            alias = os.path.join(tmp, '.', 'pk_extra.json')
            self.assertIs(
                ets_pk_store.path_lock(direct),
                ets_pk_store.path_lock(alias),
            )

    def test_schema_filter_rejects_bad_top_level_and_drops_bad_entries(self):
        self.assertIsNone(ets_pk_store.filter_pk_extra_schema([]))
        self.assertEqual(
            ets_pk_store.filter_pk_extra_schema(
                {'good': 'value', 'bad': 1, 2: 'bad'}),
            {'good': 'value'},
        )

    def test_invalid_primary_restores_healthy_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'pk_extra.json')
            with open(target, 'w', encoding='utf-8') as stream:
                stream.write('{broken')
            _write_json(target + '.bak', {'saved': 'answer'})

            data, status = ets_pk_store.load_pk_extra(target)

            self.assertEqual(status, 'restored')
            self.assertEqual(data, {'saved': 'answer'})
            self.assertEqual(_read_json(target), {'saved': 'answer'})

    def test_invalid_primary_without_backup_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'pk_extra.json')
            with open(target, 'w', encoding='utf-8') as stream:
                stream.write('{broken')

            data, status = ets_pk_store.load_pk_extra(target)

            self.assertEqual(status, 'invalid')
            self.assertEqual(data, {})

    def test_deeply_nested_primary_restores_healthy_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'pk_extra.json')
            with open(target, 'w', encoding='utf-8') as stream:
                stream.write('[' * 1500 + '0' + ']' * 1500)
            _write_json(target + '.bak', {'saved': 'answer'})

            data, status = ets_pk_store.load_pk_extra(target)

            self.assertEqual(status, 'restored')
            self.assertEqual(data, {'saved': 'answer'})
            self.assertEqual(_read_json(target), {'saved': 'answer'})

    def test_deeply_nested_primary_without_backup_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'pk_extra.json')
            with open(target, 'w', encoding='utf-8') as stream:
                stream.write('[' * 1500 + '0' + ']' * 1500)

            data, status = ets_pk_store.load_pk_extra(target)

            self.assertEqual(status, 'invalid')
            self.assertEqual(data, {})

    def test_fsync_failure_preserves_primary_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'pk_extra.json')
            original = {'kept': 'before-fsync'}
            _write_json(target, original)
            files_before = set(os.listdir(tmp))

            with (
                patch.object(
                    ets_pk_store.os, 'fsync', side_effect=OSError('disk flush failed')),
                self.assertRaisesRegex(OSError, 'disk flush failed'),
            ):
                ets_pk_store.atomic_write_json(target, {'new': 'value'})

            self.assertEqual(_read_json(target), original)
            self.assertEqual(set(os.listdir(tmp)), files_before)

    def test_transient_lock_file_open_failure_is_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'pk_extra.json')
            real_open_lock_file = ets_pk_store._open_lock_file
            attempts = []

            def flaky_open_lock_file(*args, **kwargs):
                attempts.append(args[0])
                if len(attempts) == 1:
                    raise ets_pk_store.PKExtraLockUnavailableError(
                        'transient lock-file sharing violation')
                return real_open_lock_file(*args, **kwargs)

            with (
                patch.object(ets_pk_store, '_RETRY_LOCK_OPEN', True),
                patch.object(ets_pk_store, '_LOCK_RETRY_SECONDS', 0),
                patch.object(
                    ets_pk_store,
                    '_open_lock_file',
                    side_effect=flaky_open_lock_file,
                ),
                ets_pk_store.interprocess_path_lock(target, timeout=1),
            ):
                self.assertEqual(len(attempts), 2)



class TestPKExtraInterprocess(unittest.TestCase):
    def setUp(self):
        self.context = multiprocessing.get_context('spawn')

    def _start_worker(self, target, updates, ready, start, result):
        process = self.context.Process(
            target=_process_merge_once,
            args=(target, updates, ready, start, result),
        )
        process.start()
        self.addCleanup(_terminate_process, process)
        return process

    def _assert_process_ok(self, process, result):
        payload = result.get(timeout=10)
        process.join(timeout=10)
        self.assertFalse(process.is_alive(), 'child process did not exit')
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(payload[0], 'ok', payload)
        return payload[1]

    def test_merge_waits_for_lock_held_by_another_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'pk_extra.json')
            _write_json(target, {'existing': 'value'})
            ready = self.context.Queue()
            start = self.context.Event()
            result = self.context.Queue()

            with ets_pk_store.interprocess_path_lock(target, timeout=2):
                process = self._start_worker(
                    target, {'child': 'value'}, ready, start, result)
                self.assertTrue(ready.get(timeout=10))
                start.set()
                time.sleep(0.35)
                with self.assertRaises(queue.Empty):
                    result.get_nowait()
                self.assertTrue(process.is_alive())

            merged = self._assert_process_ok(process, result)
            self.assertEqual(merged, {
                'existing': 'value', 'child': 'value',
            })

    def test_two_process_merges_preserve_both_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'pk_extra.json')
            _write_json(target, {'base': 'value'})
            ready = self.context.Queue()
            start = self.context.Event()
            results = [self.context.Queue(), self.context.Queue()]
            processes = [
                self._start_worker(
                    target, {'first': 'one'}, ready, start, results[0]),
                self._start_worker(
                    target, {'second': 'two'}, ready, start, results[1]),
            ]
            self.assertTrue(ready.get(timeout=10))
            self.assertTrue(ready.get(timeout=10))
            start.set()

            for process, result in zip(processes, results):
                self._assert_process_ok(process, result)
            self.assertEqual(_read_json(target), {
                'base': 'value', 'first': 'one', 'second': 'two',
            })

    def test_backup_failure_does_not_change_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'pk_extra.json')
            original = {'kept': 'old'}
            _write_json(target, original)
            real_atomic_write = ets_pk_store.atomic_write_json

            def fail_backup(path, data):
                if path.endswith('.bak'):
                    raise OSError('backup is read-only')
                return real_atomic_write(path, data)

            with patch.object(
                    ets_pk_store, 'atomic_write_json', side_effect=fail_backup), \
                    self.assertRaises(ets_pk_store.PKExtraBackupError):
                ets_pk_store.merge_write_pk_extra(
                    target, {'new': 'value'}, backup_existing=True)

            self.assertEqual(_read_json(target), original)


class TestPKDictionaryRecovery(unittest.TestCase):
    def _make_pk(self, tmp, extra_path):
        dictionary = os.path.join(tmp, 'dict.json')
        _write_json(dictionary, [])
        pk = ets_word_pk.ETSWordPK()
        pk.dict_path = dictionary
        pk.ecdict_path = os.path.join(tmp, 'missing-ecdict.json')
        pk.extra_path = extra_path
        pk.word_trans = {}
        pk.trans_index = {}
        pk.cn_seg_index = {}
        pk.pk_extra = {}
        return pk

    def test_dictionary_load_recovers_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            extra = os.path.join(tmp, 'pk_extra.json')
            with open(extra, 'w', encoding='utf-8') as stream:
                stream.write('{broken')
            _write_json(extra + '.bak', {'learned': 'kept'})
            pk = self._make_pk(tmp, extra)
            output = io.StringIO()

            with redirect_stdout(output):
                loaded = pk.load_dictionary()

            self.assertTrue(loaded)
            self.assertEqual(pk.pk_extra, {'learned': 'kept'})
            self.assertIn('restored invalid pk_extra.json', output.getvalue())

    def test_dictionary_load_warns_and_ignores_unrecoverable_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            extra = os.path.join(tmp, 'pk_extra.json')
            with open(extra, 'w', encoding='utf-8') as stream:
                stream.write('{broken')
            pk = self._make_pk(tmp, extra)
            output = io.StringIO()

            with redirect_stdout(output):
                loaded = pk.load_dictionary()

            self.assertTrue(loaded)
            self.assertEqual(pk.pk_extra, {})
            self.assertIn('no healthy backup', output.getvalue())

    def test_failed_learn_write_does_not_poison_memory_and_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            extra = os.path.join(tmp, 'pk_extra.json')
            pk = ets_word_pk.ETSWordPK()
            pk.extra_path = extra
            pk.pk_extra = {}
            pk.trans_index = {}
            pk.cn_seg_index = {}

            with patch.object(
                    ets_pk_store, 'atomic_write_json',
                    side_effect=OSError('disk full')):
                with redirect_stdout(io.StringIO()):
                    saved = pk.learn_miss('question', 'answer')

            self.assertFalse(saved)
            self.assertNotIn('question', pk.pk_extra)
            self.assertFalse(os.path.exists(extra))

            with redirect_stdout(io.StringIO()):
                retried = pk.learn_miss('question', 'answer')
            self.assertTrue(retried)
            self.assertEqual(pk.pk_extra['question'], 'answer')
            self.assertEqual(_read_json(extra)['question'], 'answer')


class TestPKExtraInterleaving(unittest.TestCase):
    URL = (
        'https://raw.githubusercontent.com/'
        'yigenhuobah/ETS_Auto/main/pk_extra.json'
    )

    def test_remote_commit_rereads_after_concurrent_learn(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'pk_extra.json')
            _write_json(target, {'existing': 'old'})

            download_entered = threading.Event()
            release_download = threading.Event()
            outcome = {}

            class Response:
                status = 200

                def __enter__(self):
                    download_entered.set()
                    if not release_download.wait(timeout=5):
                        raise TimeoutError('test barrier timed out')
                    return self

                def __exit__(self, *args):
                    return False

                def read(self, _limit=None):
                    return json.dumps({'remote': 'new'}).encode('utf-8')

            remote = ets_remote.ETSRemote(current_version='test', timeout=1)

            def download():
                outcome['value'] = remote.download_pk_extra(
                    url=self.URL, target_path=target)

            with patch.dict(
                    os.environ,
                    {'ETS_REMOTE_HMAC': '', 'ETS_REMOTE_PUBKEY': ''}), \
                    patch.object(
                        ets_remote, '_open_remote_url',
                        return_value=Response()):
                thread = threading.Thread(target=download)
                thread.start()
                self.assertTrue(download_entered.wait(timeout=5))

                pk = ets_word_pk.ETSWordPK()
                pk.extra_path = target
                pk.pk_extra = {'existing': 'old'}
                pk.trans_index = {}
                pk.cn_seg_index = {}
                with redirect_stdout(io.StringIO()):
                    self.assertTrue(pk.learn_miss('local', 'new'))

                release_download.set()
                thread.join(timeout=5)

            self.assertFalse(thread.is_alive())
            self.assertTrue(outcome['value'][0], outcome['value'])
            self.assertEqual(
                _read_json(target),
                {'existing': 'old', 'local': 'new', 'remote': 'new'},
            )

    def test_stale_pk_memory_rereads_a_completed_remote_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'pk_extra.json')
            _write_json(target, {'existing': 'old'})

            class Response:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self, _limit=None):
                    return json.dumps({'remote': 'new'}).encode('utf-8')

            # Construct PK before the remote commit so its in-memory map is
            # intentionally stale when learn_miss runs.
            pk = ets_word_pk.ETSWordPK()
            pk.extra_path = target
            pk.pk_extra = {'existing': 'old'}
            pk.trans_index = {}
            pk.cn_seg_index = {}
            remote = ets_remote.ETSRemote(current_version='test', timeout=1)

            with patch.dict(
                    os.environ,
                    {'ETS_REMOTE_HMAC': '', 'ETS_REMOTE_PUBKEY': ''}), \
                    patch.object(
                        ets_remote, '_open_remote_url',
                        return_value=Response()):
                updated = remote.download_pk_extra(
                    url=self.URL, target_path=target)
            self.assertTrue(updated[0], updated)

            with redirect_stdout(io.StringIO()):
                self.assertTrue(pk.learn_miss('local', 'new'))

            self.assertEqual(
                _read_json(target),
                {'existing': 'old', 'remote': 'new', 'local': 'new'},
            )


class TestExamConnectedLifecycle(unittest.TestCase):
    def _make_auto(self, url='https://ets100.com/mockExamDetail'):
        auto = object.__new__(ets_auto.ETSAutoAnswer)
        auto.port = 10086
        auto.tab = {'url': url}
        auto.rw_mode = False
        auto.connect = Mock()
        auto._drop_connection = Mock()
        return auto

    def test_result_page_drops_connection(self):
        auto = self._make_auto('https://ets100.com/mockExamResult')
        with redirect_stdout(io.StringIO()):
            auto.run()
        auto._drop_connection.assert_called_once_with()

    def test_load_false_drops_connection(self):
        auto = self._make_auto()
        auto.load_answers = Mock(return_value=False)
        with redirect_stdout(io.StringIO()):
            auto.run()
        auto._drop_connection.assert_called_once_with()

    def test_load_exception_drops_connection_and_propagates(self):
        auto = self._make_auto()
        auto.load_answers = Mock(side_effect=RuntimeError('bad answers'))
        with redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'bad answers'):
                auto.run()
        auto._drop_connection.assert_called_once_with()

    def test_rw_return_also_uses_outer_drop(self):
        auto = self._make_auto()
        auto.rw_mode = True
        auto._run_rw_loop = Mock(return_value={'mode': 'read-write'})
        with redirect_stdout(io.StringIO()):
            result = auto.run()
        self.assertEqual(result, {'mode': 'read-write'})
        auto._drop_connection.assert_called_once_with()

    def test_hotkey_false_result_degrades_and_cleans_up(self):
        auto = object.__new__(ets_auto.ETSAutoAnswer)
        auto.debug = Mock()
        auto._signal_stop = Mock()
        auto._drop_connection = Mock()
        auto._run_loop_body = Mock(return_value='done')
        instances = []

        class RefusingHotkey:
            def __init__(self, on_stop=None):
                self.on_stop = on_stop
                self.unregistered = False
                instances.append(self)

            def register(self):
                return False

            def unregister(self):
                self.unregistered = True

        with patch.object(ets_hotkey, 'ETSHotkey', RefusingHotkey), \
                redirect_stdout(io.StringIO()):
            result = auto._run_loop(max_steps=3)

        self.assertEqual(result, 'done')
        auto._run_loop_body.assert_called_once_with(3, None)
        self.assertTrue(instances[0].unregistered)
        auto._drop_connection.assert_called_once_with()


class TestPKConnectedLifecycle(unittest.TestCase):
    def _make_pk(self):
        pk = object.__new__(ets_word_pk.ETSWordPK)
        pk.port = 10086
        pk.connect = Mock()
        pk._drop_connection = Mock()
        return pk

    def test_load_false_drops_connection(self):
        pk = self._make_pk()
        pk.load_dictionary = Mock(return_value=False)
        with redirect_stdout(io.StringIO()):
            pk.run(max_q=0)
        pk._drop_connection.assert_called_once_with()

    def test_load_exception_drops_connection_and_propagates(self):
        pk = self._make_pk()
        pk.load_dictionary = Mock(side_effect=RuntimeError('bad dictionary'))
        with redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'bad dictionary'):
                pk.run(max_q=0)
        pk._drop_connection.assert_called_once_with()

    def test_hotkey_registration_failure_degrades_and_cleans_up(self):
        pk = self._make_pk()
        pk.load_dictionary = Mock(return_value=True)
        pk.stop_event = threading.Event()
        pk.stats = {'answered': 0, 'no_match': 0, 'errors': 0, 'learned': 0}
        pk._on_complete = None
        pk.debug_mode = False
        pk.debug = Mock()
        instances = []

        class BrokenHotkey:
            def __init__(self, on_stop=None):
                self.on_stop = on_stop
                self.unregistered = False
                instances.append(self)

            def register(self):
                return False

            def unregister(self):
                self.unregistered = True

        output = io.StringIO()
        with patch.object(ets_word_pk, 'ETSHotkey', BrokenHotkey), \
                redirect_stdout(output):
            pk.run(max_q=0)

        self.assertIn('continuing without hotkeys', output.getvalue())
        self.assertTrue(instances[0].unregistered)
        self.assertIsNone(pk._hotkey)
        pk._drop_connection.assert_called_once_with()

    def test_repeated_pk_state_errors_reach_reconnect_limit(self):
        pk = self._make_pk()
        pk.load_dictionary = Mock(return_value=True)
        pk.stop_event = threading.Event()
        pk.stats = {'answered': 0, 'no_match': 0, 'errors': 0, 'learned': 0}
        pk._on_complete = None
        pk.debug_mode = False
        pk.debug = Mock()
        pk.get_pk_state = Mock(return_value={'error': 'eval_js_failed'})
        pk.reconnect_control = Mock(
            side_effect=lambda count, **_kwargs: (
                'break' if count >= 3 else 'continue'))
        hotkey = Mock()
        hotkey.register.return_value = False

        with patch.object(ets_word_pk, 'ETSHotkey', return_value=hotkey), \
                redirect_stdout(io.StringIO()):
            pk.run(max_q=1)

        self.assertEqual(
            [call.args[0] for call in pk.reconnect_control.call_args_list],
            [1, 2, 3],
        )
        self.assertEqual(pk.get_pk_state.call_count, 3)
        pk._drop_connection.assert_called_once_with()


if __name__ == '__main__':
    unittest.main(verbosity=2)
