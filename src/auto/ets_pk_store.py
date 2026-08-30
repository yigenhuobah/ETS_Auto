#!/usr/bin/env python3
"""Crash-safe, process-serialized persistence for ``pk_extra.json``."""

from contextlib import contextmanager
import json
import math
import os
import tempfile
import threading
import time


class PKExtraStoreError(Exception):
    """Base error for a pk_extra persistence operation."""


class PKExtraCorruptError(PKExtraStoreError):
    """The primary file is invalid and no healthy backup can restore it."""


class PKExtraBackupError(PKExtraStoreError):
    """A required backup refresh failed before the primary commit."""


class PKExtraLockUnavailableError(OSError, PKExtraStoreError):
    """The lock file cannot be opened in the target directory."""


class PKExtraLockTimeoutError(TimeoutError, PKExtraStoreError):
    """Another process held the path lock beyond the bounded wait."""


# A user-writable state file beyond this size is treated as invalid (and .bak
# recovery applies) instead of being loaded wholesale into memory.
_PK_EXTRA_MAX_BYTES = 8 * 1024 * 1024
_PK_EXTRA_MAX_ENTRIES = 200000


_PATH_LOCKS = {}
_PATH_LOCKS_GUARD = threading.Lock()
_LOCK_TIMEOUT_SECONDS = 10.0
_LOCK_RETRY_SECONDS = 0.05
_LOCK_OPEN_RETRY_SECONDS = 0.5
_RETRY_LOCK_OPEN = os.name == 'nt'


def _checked_timeout(timeout):
    try:
        value = float(timeout)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('lock timeout must be a finite non-negative number') from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError('lock timeout must be a finite non-negative number')
    return value


def canonical_path(path):
    """Return the canonical key used to serialize access to one file."""
    if not path:
        raise ValueError('pk_extra path is required')
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def path_lock(path):
    """Return the process-wide re-entrant lock for ``path``."""
    key = canonical_path(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _open_lock_file(target, create_parent=False):
    """Open the stable sidecar used for the kernel-owned process lock."""
    directory = os.path.dirname(target) or '.'
    if create_parent:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            raise PKExtraLockUnavailableError(
                'cannot create pk_extra lock directory: %s' % directory
            ) from exc
    lock_path = target + '.lock'
    stream = None
    try:
        stream = open(lock_path, 'a+b', buffering=0)
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'\0')
        stream.seek(0)
        return stream
    except OSError as exc:
        if stream is not None:
            stream.close()
        raise PKExtraLockUnavailableError('cannot open pk_extra lock file: %s' % lock_path) from exc


def _try_acquire_file_lock(stream):
    stream.seek(0)
    if os.name == 'nt':
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_file_lock(stream):
    stream.seek(0)
    if os.name == 'nt':
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def interprocess_path_lock(path, timeout=_LOCK_TIMEOUT_SECONDS, create_parent=False):
    """Acquire a bounded, crash-released process lock for one canonical path.

    The sidecar is intentionally retained. Deleting it after unlock could let
    a waiter and a new opener lock different filesystem objects.
    """
    target = canonical_path(path)
    started = time.monotonic()
    wait_seconds = _checked_timeout(timeout)
    deadline = started + wait_seconds
    open_deadline = min(deadline, started + _LOCK_OPEN_RETRY_SECONDS)
    while True:
        try:
            stream = _open_lock_file(target, create_parent=create_parent)
            break
        except PKExtraLockUnavailableError:
            remaining = open_deadline - time.monotonic()
            if not _RETRY_LOCK_OPEN or remaining <= 0:
                raise
            time.sleep(min(_LOCK_RETRY_SECONDS, remaining))

    acquired = False
    try:
        while True:
            try:
                _try_acquire_file_lock(stream)
                acquired = True
                break
            except OSError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PKExtraLockTimeoutError(
                        'timed out waiting for pk_extra lock: %s' % (target + '.lock')
                    ) from exc
                time.sleep(min(_LOCK_RETRY_SECONDS, remaining))
        yield target
    finally:
        if acquired:
            try:
                _release_file_lock(stream)
            except OSError:
                # Closing the descriptor still releases the kernel lock. Do
                # not turn a completed primary commit into a reported failure.
                pass
        stream.close()


def filter_pk_extra_schema(data):
    """Normalize a ``dict[str, str]`` payload, rejecting other top levels.

    Entry count is capped: pk_extra is a learned-question map, and a runaway
    or hostile file must be treated as invalid (triggering .bak recovery)
    instead of being merged wholesale into the matching index.
    """
    if not isinstance(data, dict):
        return None
    if len(data) > _PK_EXTRA_MAX_ENTRIES:
        return None
    return {
        key: value for key, value in data.items() if isinstance(key, str) and isinstance(value, str)
    }


def merge_pk_extra_values(local_data, updates):
    """Return local values plus updates, with updates winning conflicts."""
    merged = dict(local_data) if local_data else {}
    if updates:
        merged.update(updates)
    return merged


def atomic_write_json(path, data):
    """Write JSON through a same-directory temporary file and ``os.replace``."""
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    tmp_path = ''
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.json', dir=directory)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
        tmp_path = ''
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _read_status_unlocked(path):
    if not path or not os.path.exists(path):
        return {}, 'missing'
    try:
        if os.path.getsize(path) > _PK_EXTRA_MAX_BYTES:
            return {}, 'invalid'
        with open(path, 'r', encoding='utf-8') as stream:
            filtered = filter_pk_extra_schema(json.load(stream))
        if filtered is None:
            return {}, 'invalid'
        return filtered, 'ok'
    except (
            OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError,
            ValueError, RecursionError):
        return {}, 'invalid'


def _load_read_only_unlocked(path):
    """Read without repair when a legacy directory cannot host a lock file."""
    data, status = _read_status_unlocked(path)
    if status not in ('invalid', 'missing'):
        return data, status
    backup_data, backup_status = _read_status_unlocked(path + '.bak')
    if backup_status == 'ok':
        return backup_data, 'backup'
    return {}, status


def _load_with_recovery_unlocked(path):
    data, status = _read_status_unlocked(path)
    if status not in ('invalid', 'missing'):
        return data, status

    backup_data, backup_status = _read_status_unlocked(path + '.bak')
    if backup_status != 'ok':
        return {}, status
    try:
        atomic_write_json(path, backup_data)
    except OSError:
        # The healthy backup is still safe to use for this process even when
        # the primary path is temporarily read-only.
        return backup_data, 'backup'
    return backup_data, 'restored'


def load_pk_extra(path):
    """Load a mapping, restoring a missing or invalid primary from ``.bak``.

    Status is one of ``ok``, ``missing``, ``restored``, ``backup`` or
    ``invalid``. ``backup`` means recovery data was usable but rewriting the
    primary failed.
    """
    if not path:
        return {}, 'missing'
    target = canonical_path(path)
    with path_lock(target):
        try:
            with interprocess_path_lock(target):
                return _load_with_recovery_unlocked(target)
        except (PKExtraLockUnavailableError, PKExtraLockTimeoutError):
            # Legacy frozen installs may live beside a read-only executable.
            # Atomic replacement makes an unlocked read safe, but never repair.
            return _load_read_only_unlocked(target)


def merge_write_pk_extra(path, updates, backup_existing=True):
    """Re-read, merge and atomically replace one pk_extra file under its lock.

    Re-reading inside the lock is the key lost-update guarantee: callers may
    perform network or answer work before this function without retaining a
    stale disk snapshot.
    """
    filtered_updates = filter_pk_extra_schema(updates)
    if filtered_updates is None:
        raise ValueError('pk_extra updates must be a dict')

    target = canonical_path(path)
    with path_lock(target):
        with interprocess_path_lock(target, create_parent=True):
            current, status = _load_with_recovery_unlocked(target)
            if status == 'invalid':
                raise PKExtraCorruptError('pk_extra.json is invalid and has no healthy backup')
            merged = merge_pk_extra_values(current, filtered_updates)
            if merged == current and status not in ('backup',):
                return merged

            if backup_existing and status in ('ok', 'restored', 'backup'):
                try:
                    atomic_write_json(target + '.bak', current)
                except OSError as exc:
                    raise PKExtraBackupError(
                        'cannot refresh pk_extra backup; primary was not changed'
                    ) from exc
            atomic_write_json(target, merged)
            return merged
