#!/usr/bin/env python3
"""Regression tests for durable user-data paths and legacy migration."""
from __future__ import annotations

from contextlib import contextmanager
import json
import multiprocessing
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch


AUTO_DIR = Path(__file__).resolve().parents[1]
if str(AUTO_DIR) not in sys.path:
    sys.path.insert(0, str(AUTO_DIR))

import ets_common
import ets_pk_store
import ets_remote
import ets_selftest
import ets_word_pk


def _migrate_family_process(executable, target_root, ready, result):
    """Spawn-safe worker that signals immediately before lock acquisition."""
    original_lock = ets_pk_store.interprocess_path_lock

    @contextmanager
    def signalled_lock(*args, **kwargs):
        ready.set()
        with original_lock(*args, **kwargs) as locked:
            yield locked

    try:
        ets_common.sys.frozen = True
        ets_common.sys.executable = executable
        os.environ['ETS_AUTO_DATA_DIR'] = target_root
        with patch.object(
                ets_pk_store, 'interprocess_path_lock', signalled_lock):
            resolved = ets_common.migrate_legacy_user_data_family(
                'pk_extra.json')
        result.put(('ok', resolved))
    except BaseException as exc:
        result.put(('error', type(exc).__name__, str(exc)))


def _terminate_process(process):
    if process.is_alive():
        process.terminate()
    process.join(timeout=5)


class TestUserDataPaths(unittest.TestCase):
    def test_development_defaults_to_project_root(self):
        with patch.object(ets_common.sys, 'frozen', False, create=True), \
                patch.dict(os.environ, {}, clear=True):
            resolved = ets_common.user_data_path(
                '../outside/pk_extra.json', anchor_file=ets_common.__file__)

        project_root = Path(ets_common.__file__).resolve().parents[2]
        self.assertEqual(Path(resolved), project_root / 'pk_extra.json')

    def test_explicit_override_wins_and_retains_basename_jail(self):
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / 'state'
            with patch.object(ets_common.sys, 'frozen', False, create=True), \
                    patch.dict(os.environ, {
                        'ETS_AUTO_DATA_DIR': str(override),
                    }, clear=True):
                resolved = ets_common.user_data_path(r'C:\escape\pk_misses.jsonl')

            self.assertEqual(Path(resolved), override / 'pk_misses.jsonl')
            self.assertFalse(override.exists())

    def test_frozen_defaults_to_local_appdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = str(Path(tmp) / 'bin' / 'ETS_Auto.exe')
            local_appdata = Path(tmp) / 'Local'
            with patch.object(ets_common.sys, 'frozen', True, create=True), \
                    patch.object(ets_common.sys, 'executable', executable), \
                    patch.dict(os.environ, {
                        'LOCALAPPDATA': str(local_appdata),
                        'APPDATA': str(Path(tmp) / 'Roaming'),
                    }, clear=True):
                resolved = ets_common.user_data_path('ets_stats.json')

            self.assertEqual(
                Path(resolved), local_appdata / 'ETS_Auto' / 'ets_stats.json')
            self.assertFalse((local_appdata / 'ETS_Auto').exists())

    def test_frozen_falls_back_to_appdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            appdata = Path(tmp) / 'Roaming'
            with patch.object(ets_common.sys, 'frozen', True, create=True), \
                    patch.dict(os.environ, {'APPDATA': str(appdata)}, clear=True):
                resolved = ets_common.user_data_path('pk_extra.json')

            self.assertEqual(
                Path(resolved), appdata / 'ETS_Auto' / 'pk_extra.json')

    def test_frozen_falls_back_to_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'
            with patch.object(ets_common.sys, 'frozen', True, create=True), \
                    patch.dict(os.environ, {}, clear=True), \
                    patch.object(ets_common.os.path, 'expanduser', return_value=str(home)):
                resolved = ets_common.user_data_path('pk_extra.json')

            self.assertEqual(
                Path(resolved), home / '.ets_auto' / 'pk_extra.json')

    def test_path_resolution_never_creates_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ets_common.sys, 'frozen', True, create=True), \
                    patch.dict(os.environ, {
                        'LOCALAPPDATA': str(Path(tmp) / 'missing'),
                    }, clear=True), \
                    patch.object(ets_common.os, 'makedirs') as makedirs:
                ets_common.user_data_path('ets_stats.json')

            makedirs.assert_not_called()


class TestLegacyMigration(unittest.TestCase):
    def _frozen_patches(self, executable, target_root):
        return (
            patch.object(ets_common.sys, 'frozen', True, create=True),
            patch.object(ets_common.sys, 'executable', str(executable)),
            patch.dict(os.environ, {
                'ETS_AUTO_DATA_DIR': str(target_root),
            }, clear=True),
        )

    def test_migration_copies_and_preserves_legacy_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'legacy' / 'ETS_Auto.exe'
            executable.parent.mkdir()
            legacy = executable.parent / 'pk_extra.json'
            legacy.write_text('{"old": "value"}', encoding='utf-8')
            target_root = root / 'new'
            frozen, executable_patch, environment = self._frozen_patches(
                executable, target_root)

            with frozen, executable_patch, environment:
                resolved = ets_common.migrate_legacy_user_data_file(
                    'pk_extra.json')

            target = target_root / 'pk_extra.json'
            self.assertEqual(Path(resolved), target)
            self.assertEqual(target.read_text(encoding='utf-8'), '{"old": "value"}')
            self.assertEqual(legacy.read_text(encoding='utf-8'), '{"old": "value"}')

    def test_backup_only_family_migrates_and_restores_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'legacy' / 'ETS_Auto.exe'
            executable.parent.mkdir()
            legacy_backup = executable.parent / 'pk_extra.json.bak'
            expected = {'backup-only': 'kept'}
            legacy_backup.write_text(
                json.dumps(expected), encoding='utf-8')
            target_root = root / 'new'
            frozen, executable_patch, environment = self._frozen_patches(
                executable, target_root)

            with frozen, executable_patch, environment:
                resolved = ets_common.migrate_legacy_user_data_family(
                    'pk_extra.json')

            target = Path(resolved)
            target_backup = Path(str(target) + '.bak')
            self.assertFalse(target.exists())
            self.assertEqual(
                json.loads(target_backup.read_text(encoding='utf-8')),
                expected)
            self.assertTrue(legacy_backup.is_file())

            data, status = ets_pk_store.load_pk_extra(str(target))

            self.assertEqual(status, 'restored')
            self.assertEqual(data, expected)
            self.assertEqual(
                json.loads(target.read_text(encoding='utf-8')), expected)

    def test_corrupt_primary_family_migrates_backup_and_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'legacy' / 'ETS_Auto.exe'
            executable.parent.mkdir()
            legacy = executable.parent / 'pk_extra.json'
            legacy.write_text('{broken', encoding='utf-8')
            legacy_backup = executable.parent / 'pk_extra.json.bak'
            expected = {'healthy-backup': 'restored'}
            legacy_backup.write_text(
                json.dumps(expected), encoding='utf-8')
            target_root = root / 'new'
            frozen, executable_patch, environment = self._frozen_patches(
                executable, target_root)

            with frozen, executable_patch, environment:
                resolved = ets_common.migrate_legacy_user_data_family(
                    'pk_extra.json')

            target = Path(resolved)
            self.assertEqual(target.read_text(encoding='utf-8'), '{broken')
            self.assertTrue(Path(str(target) + '.bak').is_file())

            data, status = ets_pk_store.load_pk_extra(str(target))

            self.assertEqual(status, 'restored')
            self.assertEqual(data, expected)
            self.assertEqual(
                json.loads(target.read_text(encoding='utf-8')), expected)
            self.assertEqual(legacy.read_text(encoding='utf-8'), '{broken')
            self.assertTrue(legacy_backup.is_file())

    def test_existing_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'legacy' / 'ETS_Auto.exe'
            executable.parent.mkdir()
            (executable.parent / 'pk_extra.json').write_text(
                'legacy', encoding='utf-8')
            target_root = root / 'new'
            target_root.mkdir()
            target = target_root / 'pk_extra.json'
            target.write_text('current', encoding='utf-8')
            frozen, executable_patch, environment = self._frozen_patches(
                executable, target_root)

            with frozen, executable_patch, environment, \
                    patch.object(ets_common.shutil, 'copyfileobj') as copy_file:
                resolved = ets_common.migrate_legacy_user_data_file(
                    'pk_extra.json')

            self.assertEqual(Path(resolved), target)
            self.assertEqual(target.read_text(encoding='utf-8'), 'current')
            copy_file.assert_not_called()

    def test_copy_failure_falls_back_to_readable_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'legacy' / 'ETS_Auto.exe'
            executable.parent.mkdir()
            legacy = executable.parent / 'pk_misses.jsonl'
            legacy.write_text('{"question": "old"}\n', encoding='utf-8')
            target_root = root / 'new'
            frozen, executable_patch, environment = self._frozen_patches(
                executable, target_root)

            with frozen, executable_patch, environment, \
                    patch.object(ets_common.shutil, 'copyfileobj',
                                 side_effect=OSError('disk full')):
                resolved = ets_common.migrate_legacy_user_data_file(
                    'pk_misses.jsonl')

            self.assertEqual(Path(resolved), legacy)
            self.assertFalse((target_root / 'pk_misses.jsonl').exists())

    def test_backup_only_lock_failure_returns_legacy_family(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'legacy' / 'ETS_Auto.exe'
            executable.parent.mkdir()
            legacy = executable.parent / 'pk_extra.json'
            expected = {'backup-only': 'available-after-lock-failure'}
            Path(str(legacy) + '.bak').write_text(
                json.dumps(expected), encoding='utf-8')
            target_root = root / 'new'
            frozen, executable_patch, environment = self._frozen_patches(
                executable, target_root)

            with frozen, executable_patch, environment, patch.object(
                    ets_pk_store,
                    'interprocess_path_lock',
                    side_effect=ets_pk_store.PKExtraLockTimeoutError('busy'),
            ):
                resolved = ets_common.migrate_legacy_user_data_family(
                    'pk_extra.json')

            self.assertEqual(Path(resolved), legacy)
            self.assertFalse((target_root / 'pk_extra.json').exists())
            data, status = ets_pk_store.load_pk_extra(resolved)
            self.assertEqual(status, 'restored')
            self.assertEqual(data, expected)

    def test_corrupt_current_family_lock_failure_uses_legacy_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'legacy' / 'ETS_Auto.exe'
            executable.parent.mkdir()
            legacy = executable.parent / 'pk_extra.json'
            expected = {'healthy-backup': 'available'}
            Path(str(legacy) + '.bak').write_text(
                json.dumps(expected), encoding='utf-8')
            target_root = root / 'new'
            target_root.mkdir()
            target = target_root / 'pk_extra.json'
            target.write_text('{broken', encoding='utf-8')
            frozen, executable_patch, environment = self._frozen_patches(
                executable, target_root)

            with frozen, executable_patch, environment, patch.object(
                    ets_pk_store,
                    'interprocess_path_lock',
                    side_effect=ets_pk_store.PKExtraLockTimeoutError('busy'),
            ):
                resolved = ets_common.migrate_legacy_user_data_family(
                    'pk_extra.json')

            self.assertEqual(Path(resolved), legacy)
            self.assertEqual(target.read_text(encoding='utf-8'), '{broken')
            data, status = ets_pk_store.load_pk_extra(resolved)
            self.assertEqual(status, 'restored')
            self.assertEqual(data, expected)

    def test_backup_only_copy_failure_returns_legacy_family(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'legacy' / 'ETS_Auto.exe'
            executable.parent.mkdir()
            legacy = executable.parent / 'pk_extra.json'
            expected = {'backup-only': 'available-after-copy-failure'}
            Path(str(legacy) + '.bak').write_text(
                json.dumps(expected), encoding='utf-8')
            target_root = root / 'new'
            frozen, executable_patch, environment = self._frozen_patches(
                executable, target_root)

            with frozen, executable_patch, environment, patch.object(
                    ets_common,
                    '_copy_legacy_file_atomic',
                    side_effect=OSError('disk full'),
            ):
                resolved = ets_common.migrate_legacy_user_data_family(
                    'pk_extra.json')

            self.assertEqual(Path(resolved), legacy)
            self.assertFalse((target_root / 'pk_extra.json').exists())
            self.assertFalse((target_root / 'pk_extra.json.bak').exists())
            data, status = ets_pk_store.load_pk_extra(resolved)
            self.assertEqual(status, 'restored')
            self.assertEqual(data, expected)

    def test_corrupt_migrated_primary_backup_failure_uses_legacy_family(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'legacy' / 'ETS_Auto.exe'
            executable.parent.mkdir()
            legacy = executable.parent / 'pk_extra.json'
            legacy.write_text('{broken', encoding='utf-8')
            expected = {'healthy-backup': 'still-reachable'}
            Path(str(legacy) + '.bak').write_text(
                json.dumps(expected), encoding='utf-8')
            target_root = root / 'new'
            target = target_root / 'pk_extra.json'
            target_backup = target_root / 'pk_extra.json.bak'
            frozen, executable_patch, environment = self._frozen_patches(
                executable, target_root)
            original_copy = ets_common._copy_legacy_file_atomic

            def fail_backup_copy(source, destination):
                if destination.endswith('.bak'):
                    raise OSError('backup copy failed')
                return original_copy(source, destination)

            with frozen, executable_patch, environment, patch.object(
                    ets_common,
                    '_copy_legacy_file_atomic',
                    side_effect=fail_backup_copy,
            ):
                resolved = ets_common.migrate_legacy_user_data_family(
                    'pk_extra.json')

            self.assertEqual(Path(resolved), legacy)
            self.assertEqual(target.read_text(encoding='utf-8'), '{broken')
            self.assertFalse(target_backup.exists())
            data, status = ets_pk_store.load_pk_extra(resolved)
            self.assertEqual(status, 'restored')
            self.assertEqual(data, expected)

    def test_copy_is_invisible_until_atomic_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'legacy' / 'ETS_Auto.exe'
            executable.parent.mkdir()
            legacy = executable.parent / 'pk_extra.json'
            expected = b'complete-legacy-payload'
            legacy.write_bytes(expected)
            target_root = root / 'new'
            target = target_root / 'pk_extra.json'
            frozen, executable_patch, environment = self._frozen_patches(
                executable, target_root)
            copy_started = threading.Event()
            release_copy = threading.Event()
            result = []
            original_copy = ets_common.shutil.copyfileobj

            def blocked_copy(source, destination):
                destination.write(source.read(8))
                destination.flush()
                copy_started.set()
                if not release_copy.wait(timeout=10):
                    raise TimeoutError('copy release timed out')
                original_copy(source, destination)

            def migrate():
                try:
                    resolved = ets_common.migrate_legacy_user_data_file(
                        'pk_extra.json')
                    result.append(('ok', resolved))
                except BaseException as exc:
                    result.append(('error', type(exc).__name__, str(exc)))

            with frozen, executable_patch, environment, patch.object(
                    ets_common.shutil, 'copyfileobj', blocked_copy):
                worker = threading.Thread(target=migrate, daemon=True)
                worker.start()
                try:
                    self.assertTrue(copy_started.wait(timeout=10))
                    self.assertFalse(
                        target.exists(),
                        'partially copied target became externally visible',
                    )
                finally:
                    release_copy.set()
                    worker.join(timeout=10)

            self.assertFalse(worker.is_alive())
            self.assertEqual(result, [('ok', str(target))])
            self.assertEqual(target.read_bytes(), expected)

    def test_family_waits_on_primary_interprocess_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'legacy' / 'ETS_Auto.exe'
            executable.parent.mkdir()
            legacy = executable.parent / 'pk_extra.json'
            legacy_backup = executable.parent / 'pk_extra.json.bak'
            legacy.write_text('{"primary": "complete"}', encoding='utf-8')
            legacy_backup.write_text(
                '{"backup": "complete"}', encoding='utf-8')
            target_root = root / 'new'
            target_root.mkdir()
            target = target_root / 'pk_extra.json'
            target_backup = target_root / 'pk_extra.json.bak'
            context = multiprocessing.get_context('spawn')
            ready = context.Event()
            result = context.Queue()

            with ets_pk_store.interprocess_path_lock(str(target), timeout=2):
                process = context.Process(
                    target=_migrate_family_process,
                    args=(str(executable), str(target_root), ready, result),
                )
                process.start()
                self.addCleanup(_terminate_process, process)
                self.assertTrue(
                    ready.wait(timeout=10),
                    'child did not attempt to acquire the primary lock',
                )
                time.sleep(0.2)
                self.assertTrue(process.is_alive())
                with self.assertRaises(queue.Empty):
                    result.get_nowait()
                self.assertFalse(target.exists())
                self.assertFalse(target_backup.exists())

            payload = result.get(timeout=10)
            process.join(timeout=10)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(payload, ('ok', str(target)))
            self.assertEqual(
                target.read_text(encoding='utf-8'),
                '{"primary": "complete"}',
            )
            self.assertEqual(
                target_backup.read_text(encoding='utf-8'),
                '{"backup": "complete"}',
            )

    def test_missing_legacy_does_not_create_target_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'legacy' / 'ETS_Auto.exe'
            target_root = root / 'new'
            frozen, executable_patch, environment = self._frozen_patches(
                executable, target_root)

            with frozen, executable_patch, environment:
                resolved = ets_common.migrate_legacy_user_data_file(
                    'pk_extra.json')

            self.assertEqual(Path(resolved), target_root / 'pk_extra.json')
            self.assertFalse(target_root.exists())

    def test_development_mode_never_migrates(self):
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / 'new'
            with patch.object(ets_common.sys, 'frozen', False, create=True), \
                    patch.dict(os.environ, {
                        'ETS_AUTO_DATA_DIR': str(override),
                    }, clear=True), \
                    patch.object(ets_common.shutil, 'copyfileobj') as copy_file:
                resolved = ets_common.migrate_legacy_user_data_file(
                    'pk_extra.json')

            self.assertEqual(Path(resolved), override / 'pk_extra.json')
            self.assertFalse(override.exists())
            copy_file.assert_not_called()


class TestRemoteUserDataIntegration(unittest.TestCase):
    def test_pk_extra_resolution_uses_migration_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            expected = str(Path(tmp) / 'state' / 'pk_extra.json')
            with patch.object(
                    ets_common, 'migrate_legacy_user_data_family',
                    return_value=expected) as migrate:
                resolved = ets_remote.resolve_pk_extra_path('pk_extra.json')

            self.assertEqual(resolved, expected)
            migrate.assert_called_once_with(
                'pk_extra.json', anchor_file=ets_remote.__file__)

    def test_remote_cache_migrates_frozen_executable_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'legacy' / 'ETS_Auto.exe'
            executable.parent.mkdir()
            legacy = executable.parent / 'remote_info_cache.json'
            legacy.write_text('{"version": "old"}', encoding='utf-8')
            target_root = root / 'new'

            with patch.object(
                    ets_remote, '_CACHE_FILENAME',
                    'remote_info_cache.json'), \
                    patch.object(
                        ets_common.sys, 'frozen', True, create=True), \
                    patch.object(
                        ets_common.sys, 'executable', str(executable)), \
                    patch.dict(os.environ, {
                        'ETS_AUTO_DATA_DIR': str(target_root),
                    }, clear=True):
                remote = object.__new__(ets_remote.ETSRemote)
                resolved = remote._resolve_cache_path()

            target = target_root / 'remote_info_cache.json'
            self.assertEqual(Path(resolved), target)
            self.assertEqual(
                target.read_text(encoding='utf-8'), '{"version": "old"}')
            self.assertEqual(
                legacy.read_text(encoding='utf-8'), '{"version": "old"}')

    def test_absolute_cache_override_bypasses_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            absolute = str(Path(tmp) / 'test-cache.json')
            with patch.object(ets_remote, '_CACHE_FILENAME', absolute), \
                    patch.object(
                        ets_common, 'migrate_legacy_user_data_file',
                        side_effect=AssertionError('must not migrate')) as migrate:
                remote = object.__new__(ets_remote.ETSRemote)
                resolved = remote._resolve_cache_path()

            self.assertEqual(resolved, absolute)
            migrate.assert_not_called()


class TestWriteDirectoryCreation(unittest.TestCase):
    def test_ensure_parent_dir_creates_nested_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'nested' / 'state' / 'data.json'
            parent = ets_common.ensure_parent_dir(target)

            self.assertEqual(Path(parent), target.parent)
            self.assertTrue(target.parent.is_dir())
            self.assertFalse(target.exists())

    def test_record_miss_creates_parent_before_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'state' / 'pk_misses.jsonl'
            pk = object.__new__(ets_word_pk.ETSWordPK)
            pk.misses_path = str(target)
            pk.debug = Mock()

            ets_word_pk.ETSWordPK.record_miss(pk, ' question ', ['A', 'B'])

            self.assertTrue(target.is_file())
            record = json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(record['question'], 'question')
            self.assertEqual(record['options'], ['A', 'B'])
            pk.debug.assert_not_called()

    def test_normal_pk_load_resolves_both_legacy_sidecars(self):
        pk = object.__new__(ets_word_pk.ETSWordPK)
        pk.dict_path = os.path.join('missing', 'dictionary.json')
        paths = ['new-extra.json', 'new-misses.jsonl']
        with patch.object(ets_word_pk, 'migrate_legacy_user_data_family',
                          return_value=paths[0]) as family, \
                patch.object(ets_word_pk, 'migrate_legacy_user_data_file',
                             return_value=paths[1]) as migrate:
            loaded = ets_word_pk.ETSWordPK.load_dictionary(pk)

        self.assertFalse(loaded)
        self.assertEqual(pk.extra_path, paths[0])
        self.assertEqual(pk.misses_path, paths[1])
        family.assert_called_once_with(
            'pk_extra.json', anchor_file=ets_word_pk.__file__)
        migrate.assert_called_once_with(
            'pk_misses.jsonl', anchor_file=ets_word_pk.__file__)

    def test_custom_pk_paths_are_never_overwritten_by_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            pk = object.__new__(ets_word_pk.ETSWordPK)
            pk.dict_path = os.path.join(tmp, 'missing-dictionary.json')
            custom_extra = os.path.join(tmp, 'custom', 'extra.json')
            custom_misses = os.path.join(tmp, 'custom', 'misses.jsonl')
            pk.extra_path = custom_extra
            pk.misses_path = custom_misses

            with patch.object(
                    ets_word_pk,
                    'migrate_legacy_user_data_family') as family, \
                    patch.object(
                        ets_word_pk,
                        'migrate_legacy_user_data_file') as migrate:
                loaded = ets_word_pk.ETSWordPK.load_dictionary(pk)

            self.assertFalse(loaded)
            self.assertEqual(pk.extra_path, custom_extra)
            self.assertEqual(pk.misses_path, custom_misses)
            family.assert_not_called()
            migrate.assert_not_called()

    def test_pk_self_test_does_not_migrate_or_create_directories(self):
        with patch.object(ets_selftest, '_import_target_modules'), \
                patch.object(ets_selftest, '_validate_pk_dictionary'), \
                patch.object(
                    ets_word_pk,
                    'migrate_legacy_user_data_family') as family, \
                patch.object(ets_word_pk, 'migrate_legacy_user_data_file') as migrate, \
                patch.object(ets_common.os, 'makedirs') as makedirs:
            result = ets_selftest.run_self_test('pk', ets_word_pk.ETSWordPK)

        self.assertEqual(result, 0)
        family.assert_not_called()
        migrate.assert_not_called()
        makedirs.assert_not_called()


if __name__ == '__main__':
    unittest.main()
