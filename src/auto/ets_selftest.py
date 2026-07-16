#!/usr/bin/env python3
"""Offline runtime checks shared by source and frozen entry points."""
from __future__ import annotations

import importlib
import json
import os
import sys

from ets_common import APP_VERSION


TARGET_IMPORTS = {
    'exam': (
        'websocket',
        'ets_common',
        'ets_strategy',
        'ets_hotkey',
        'ets_recording_ui',
        'ets_rw_mode',
        'ets_tee',
    ),
    'pk': (
        'websocket',
        'ets_common',
        'ets_hotkey',
    ),
    'gui': (
        'websocket',
        'ets_common',
        'ets_compat',
        'ets_auto',
        'ets_word_pk',
        'ets_strategy',
        'ets_hotkey',
        'ets_remote',
        'ets_parser',
        'ets_browser_ui',
        'ets_recording_ui',
        'ets_rw_mode',
        'ets_tee',
        'customtkinter',
        'darkdetect',
    ),
}

_NULL_STREAMS = []


def ensure_cli_streams():
    """Give windowed PyInstaller processes writable argparse streams."""
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is not None:
            continue
        stream = open(os.devnull, 'w', encoding='utf-8')
        setattr(sys, name, stream)
        _NULL_STREAMS.append(stream)


def add_runtime_check_arguments(parser):
    """Add deterministic, offline early-exit arguments to an ArgumentParser."""
    parser.add_argument(
        '--version', action='version', version='%(prog)s ' + APP_VERSION)
    parser.add_argument(
        '--self-test', action='store_true',
        help='Run offline packaged-runtime checks and exit')
    return parser


def _emit(message, error=False):
    stream = sys.stderr if error else sys.stdout
    if stream is None:
        return
    try:
        print(message, file=stream, flush=True)
    except (AttributeError, OSError, RuntimeError):
        pass


def _import_target_modules(target):
    try:
        module_names = TARGET_IMPORTS[target]
    except KeyError as exc:
        raise ValueError('unknown self-test target: %s' % target) from exc
    for module_name in module_names:
        importlib.import_module(module_name)


def _validate_pk_dictionary(path):
    if not path or not os.path.isfile(path):
        raise FileNotFoundError('bundled ecdict_pk.json not found: %s' % path)
    with open(path, encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError('ecdict_pk.json must contain a JSON object')
    if not data:
        raise ValueError('ecdict_pk.json is empty')
    word, translation = next(iter(data.items()))
    if not isinstance(word, str) or not word or not isinstance(translation, str):
        raise ValueError('ecdict_pk.json has an invalid first entry')


def _destroy_ctk_root(root):
    """Cancel CustomTkinter timers before destroying the Tcl interpreter."""
    try:
        callback_ids = root.tk.call('after', 'info')
        if isinstance(callback_ids, str):
            callback_ids = (callback_ids,) if callback_ids else ()
        for callback_id in callback_ids:
            try:
                root.tk.call('after', 'cancel', callback_id)
            except Exception:
                pass
    finally:
        root.destroy()


def _validate_gui_runtime():
    ctk = importlib.import_module('customtkinter')
    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('blue')
    root = ctk.CTk()
    try:
        root.withdraw()
        root.update_idletasks()
    finally:
        _destroy_ctk_root(root)


def _validate_exam_runtime(factory):
    if factory is None:
        raise ValueError('exam self-test requires a factory')
    instance = factory()
    if getattr(instance, 'stop_event', None) is None:
        raise RuntimeError('exam stop_event was not initialized')
    if getattr(instance, 'strategy', None) is None:
        raise RuntimeError('exam strategy was not initialized')


def _validate_pk_runtime(factory):
    if factory is None:
        raise ValueError('PK self-test requires a factory')
    instance = factory()
    if getattr(instance, 'stop_event', None) is None:
        raise RuntimeError('PK stop_event was not initialized')
    _validate_pk_dictionary(getattr(instance, 'ecdict_path', None))


def run_self_test(target, factory=None):
    """Run a no-network packaged-runtime check and return a process exit code."""
    try:
        _import_target_modules(target)
        if target == 'gui':
            _validate_gui_runtime()
        elif target == 'exam':
            _validate_exam_runtime(factory)
        elif target == 'pk':
            _validate_pk_runtime(factory)
        else:
            raise ValueError('unknown self-test target: %s' % target)
    except Exception as exc:
        _emit('SELF-TEST FAIL: %s: %s: %s' % (
            target, type(exc).__name__, exc), error=True)
        return 1
    _emit('SELF-TEST OK: %s %s' % (target, APP_VERSION))
    return 0
