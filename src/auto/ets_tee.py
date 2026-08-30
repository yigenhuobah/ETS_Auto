#!/usr/bin/env python3
"""Tee stdout/stderr to a log file (shared by ets_auto CLI and run.py)."""
import sys
import threading


class TeeOutput:
    """Tee output to both terminal and log file."""
    # RLock: chained Tee setups (a Tee wrapping another Tee) re-enter write().
    _shared_lock = threading.RLock()  # protect concurrent writes to same file

    def __init__(self, file_path, original_stream=None, mode='w', shared_handle=None):
        self.terminal = original_stream or sys.stdout
        if shared_handle is not None:
            self.log = shared_handle
            self._owns_handle = False
        else:
            self.log = open(file_path, mode, encoding='utf-8')
            self._owns_handle = True

    def write(self, message):
        with self._shared_lock:
            if self.terminal is not None:
                self.terminal.write(message)
            try:
                self.log.write(message)
            except (OSError, ValueError):
                # Disk full / handle closed (AV lock) must never kill the
                # automation loop via print() — degrade to terminal-only.
                pass

    def flush(self):
        with self._shared_lock:
            if self.terminal is not None:
                self.terminal.flush()
            try:
                self.log.flush()
            except (OSError, ValueError):
                pass
    def close(self):
        if self._owns_handle:
            self.log.close()
    # Standard text IO attributes (Bug 13: PyInstaller/pip may read these)
    @property
    def encoding(self):
        return self.terminal.encoding if self.terminal and hasattr(self.terminal, 'encoding') else 'utf-8'
    @property
    def errors(self):
        return self.terminal.errors if self.terminal and hasattr(self.terminal, 'errors') else 'replace'
    @property
    def mode(self):
        return 'w'
    @property
    def name(self):
        return self.log.name if hasattr(self.log, 'name') else None
    def fileno(self):
        return self.log.fileno()
    def isatty(self):
        return self.terminal.isatty() if self.terminal and hasattr(self.terminal, 'isatty') else False


