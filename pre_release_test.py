#!/usr/bin/env python3
"""
ETS_Auto Pre-Release Test Suite
Runs before building exe to catch import errors, missing deps, and basic regressions.
Exit code 0 = all pass, non-zero = fail (CI should abort).
"""
import sys
import os
import re
import json

# Add src/auto to path so imports work like they do in production
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src', 'auto')
sys.path.insert(0, SRC)

passed = 0
failed = 0
errors = []

def test(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  [PASS] {name}")
    except Exception as e:
        failed += 1
        errors.append((name, str(e)))
        print(f"  [FAIL] {name}: {e}")

# ── 1. Import tests ─────────────────────────────────────────
print("\n=== Import Tests ===")

def t_import_common():
    import ets_common
    assert hasattr(ets_common, 'ETSBase'), "ETSBase not found in ets_common"
test("import ets_common + ETSBase exists", t_import_common)
def t_import_compat():
    import ets_compat
    assert callable(ets_compat.collect_compatibility_report)
    assert callable(ets_compat.format_compatibility_report)
test("import ets_compat + compatibility API exists", t_import_compat)

def t_import_selftest():
    import ets_selftest
    assert callable(ets_selftest.add_runtime_check_arguments)
    assert callable(ets_selftest.run_self_test)
    assert set(ets_selftest.TARGET_IMPORTS) == {'exam', 'pk', 'gui'}
test("import ets_selftest + packaged runtime API exists", t_import_selftest)

def t_runtime_self_tests_are_offline():
    import builtins
    import socket
    import urllib.request
    from unittest.mock import patch
    import websocket
    import ets_selftest

    real_open = builtins.open

    def guarded_open(file, mode='r', *args, **kwargs):
        if any(flag in str(mode) for flag in ('w', 'a', 'x', '+')):
            raise AssertionError("offline self-test attempted a file write")
        return real_open(file, mode, *args, **kwargs)

    def forbidden_side_effect(*args, **kwargs):
        raise AssertionError("offline self-test attempted network access")

    old_dont_write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        with (
            patch('builtins.open', guarded_open),
            patch.object(socket, 'create_connection', side_effect=forbidden_side_effect),
            patch.object(urllib.request, 'urlopen', side_effect=forbidden_side_effect),
            patch.object(websocket, 'create_connection', side_effect=forbidden_side_effect),
        ):
            from ets_auto import ETSAutoAnswer
            from ets_word_pk import ETSWordPK
            assert ets_selftest.run_self_test('exam', ETSAutoAnswer) == 0
            assert ets_selftest.run_self_test('pk', ETSWordPK) == 0
            assert ets_selftest.run_self_test('gui') == 0
    finally:
        sys.dont_write_bytecode = old_dont_write
test("real packaged self-tests stay offline and read-only", t_runtime_self_tests_are_offline)


def t_import_auto():
    import ets_auto
    assert hasattr(ets_auto, 'ETSAutoAnswer'), "ETSAutoAnswer not found in ets_auto"
test("import ets_auto + ETSAutoAnswer exists", t_import_auto)

def t_import_pk():
    import ets_word_pk
    assert hasattr(ets_word_pk, 'ETSWordPK'), "ETSWordPK not found in ets_word_pk"
test("import ets_word_pk + ETSWordPK exists", t_import_pk)

def t_import_parser():
    import ets_parser
    assert hasattr(ets_parser, 'scan_sets'), "scan_sets not found in ets_parser"
    assert hasattr(ets_parser, 'create_browser_tab'), "create_browser_tab not found in ets_parser"
    assert ets_parser.ctk is not None, "customtkinter not imported in ets_parser (ctk is None)"
test("import ets_parser + scan_sets/create_browser_tab + ctk available", t_import_parser)

def t_import_gui():
    import ets_gui
    assert hasattr(ets_gui, 'ETSApp'), "ETSApp not found in ets_gui"
test("import ets_gui + ETSApp exists", t_import_gui)

def t_import_browser_ui():
    import ets_browser_ui
    assert hasattr(ets_browser_ui, 'create_browser_tab'), "create_browser_tab not found in ets_browser_ui"
test("import ets_browser_ui + create_browser_tab exists", t_import_browser_ui)

def t_import_remote():
    import ets_remote
    assert hasattr(ets_remote, 'ETSRemote'), "ETSRemote not found in ets_remote"
test("import ets_remote + ETSRemote exists", t_import_remote)

# ── 2. Dependency tests ─────────────────────────────────────
print("\n=== Dependency Tests ===")

def t_dep_websocket():
    import websocket
test("import websocket-client", t_dep_websocket)

def t_dep_customtkinter():
    import customtkinter as ctk
    assert ctk.CTk is not None
test("import customtkinter + CTk available", t_dep_customtkinter)

def t_dep_psutil():
    import psutil
test("import psutil", t_dep_psutil)

# ── 3. Inheritance & interface tests ────────────────────────
print("\n=== Interface Tests ===")

def t_force_utf8_stdio():
    from ets_common import force_utf8_stdio
    # Should not raise even if called multiple times
    force_utf8_stdio()
    force_utf8_stdio(line_buffering=True)
test("force_utf8_stdio() callable without error", t_force_utf8_stdio)

def t_callback_hooks():
    from ets_common import ETSBase
    base = ETSBase()
    # Verify hooks start as None
    assert base._on_connect is None
    assert base._on_question is None
    assert base._on_complete is None
    assert base._on_error is None
    # Register and fire
    results = []
    base.on_connect(lambda inst: results.append('connect'))
    base.on_question(lambda info: results.append(info.get('type', '?')))
    base.on_complete(lambda stats: results.append('complete'))
    base.on_error(lambda msg: results.append(msg))
    base._fire_connect()
    base._fire_question({'type': 'choose'})
    base._fire_complete({'total': 10})
    base._fire_error('test error')
    assert results == ['connect', 'choose', 'complete', 'test error'], f"Callbacks fired out of order: {results}"
test("ETSBase callback hooks register + fire", t_callback_hooks)

def t_inheritance():
    from ets_common import ETSBase
    from ets_auto import ETSAutoAnswer
    from ets_word_pk import ETSWordPK
    assert issubclass(ETSAutoAnswer, ETSBase), "ETSAutoAnswer should inherit ETSBase"
    assert issubclass(ETSWordPK, ETSBase), "ETSWordPK should inherit ETSBase"
test("ETSAutoAnswer/ETSWordPK inherit ETSBase", t_inheritance)

def t_parser_scan():
    from ets_parser import scan_sets
    result = scan_sets()
    # scan_sets returns (list, error_msg_or_None), even if empty (no ETS data on CI)
    assert isinstance(result, tuple) and len(result) == 2, f"scan_sets should return (list, str|None), got {type(result)}"
    assert isinstance(result[0], list), f"scan_sets[0] should be list, got {type(result[0])}"
    assert result[1] is None or isinstance(result[1], str), f"scan_sets[1] should be str|None, got {type(result[1])}"
test("ets_parser.scan_sets() returns (list, error_msg)", t_parser_scan)

def t_parser_render():
    from ets_parser import render_section
    result = render_section({'type': 'collector.choose', 'items': []})
    assert isinstance(result, (str, list)), f"render_section should return str/list, got {type(result)}"
test("ets_parser.render_section() runs without error", t_parser_render)

# ── 4. Data file tests ─────────────────────────────────────
print("\n=== Data File Tests ===")

def test_data_file():
    dict_path = os.path.join(SRC, '..', '..', 'ecdict_pk.json')
    if not os.path.exists(dict_path):
        # Try relative to project root
        dict_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ecdict_pk.json')
    assert os.path.exists(dict_path), f"ecdict_pk.json not found (checked {dict_path})"
    import json
    with open(dict_path, encoding='utf-8') as f:
        data = json.load(f)
    assert isinstance(data, dict), f"ecdict_pk.json should be dict, got {type(data)}"
    assert len(data) > 0, "ecdict_pk.json is empty"
test("ecdict_pk.json exists and is valid JSON dict", test_data_file)

def test_requirements():
    req_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'requirements.txt')
    assert os.path.exists(req_path), "requirements.txt not found"
    with open(req_path, encoding='utf-8') as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    assert len(lines) >= 3, f"requirements.txt should have >=3 deps, got {len(lines)}"
test("requirements.txt exists with >=3 deps", test_requirements)

def test_packaged_smoke_contract():
    import packaged_smoke_test
    required = {
        (exe, arg)
        for exe in ('ets_gui.exe', 'ets_auto.exe', 'ets_pk.exe')
        for arg in ('--version', '--help', '--self-test')
    }
    actual = {(exe, arg) for exe, arg, _ in packaged_smoke_test.SMOKE_CASES}
    assert required.issubset(actual), f"packaged smoke cases missing: {required - actual}"
    assert ('ets_gui.exe', '--verify-version={version}') in actual
    assert len(actual) == len(required) + 1
    workflow_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '.github', 'workflows', 'build-exe.yml')
    workflow = open(workflow_path, encoding='utf-8').read()
    assert workflow.count('--hidden-import=ets_selftest') == 3
    assert 'python packaged_smoke_test.py --dist dist --timeout 60' in workflow
test("packaged EXE smoke matrix + workflow wiring", test_packaged_smoke_contract)

# ── 5. GUI Widget Smoke Tests ─────────────────────────────
print("\n=== GUI Widget Tests ===")

def _destroy_ctk_root(root):
    """Cancel CustomTkinter timers before destroying its Tcl interpreter."""
    try:
        callback_ids = root.tk.call('after', 'info')
        if isinstance(callback_ids, str):
            callback_ids = (callback_ids,) if callback_ids else ()
        for callback_id in callback_ids:
            try:
                # Widget.destroy() still owns and deletes the registered Tcl command.
                root.tk.call('after', 'cancel', callback_id)
            except Exception:
                pass
    finally:
        root.destroy()

def t_gui_widgets():
    import customtkinter as ctk
    root = ctk.CTk()
    try:
        # CTkEntry does NOT support command=; only bind() works
        entry = ctk.CTkEntry(root, placeholder_text="test")
        entry.bind('<Return>', lambda e: None)  # This should work
        # Verify configure() only accepts valid CTkEntry kwargs
        try:
            entry.configure(command=lambda: None)
        except ValueError:
            pass  # Expected - command is not a valid CTkEntry argument
        else:
            raise AssertionError("CTkEntry.configure(command=...) should raise ValueError")
    finally:
        _destroy_ctk_root(root)
test("CTkEntry widget creation + bind (no command=)", t_gui_widgets)

def t_gui_parser_tab():
    """Smoke test: create_browser_tab must not crash with minimal data."""
    import customtkinter as ctk
    from ets_parser import create_browser_tab
    root = ctk.CTk()
    try:
        tab = ctk.CTkFrame(root)
        tab.pack()
        try:
            create_browser_tab(tab)
        except Exception as e:
            # If ETS data dir doesn't exist, that's fine - just no crash from widget code
            if 'ETS' not in str(e) and 'AppData' not in str(e) and 'scan_sets' not in str(e):
                raise
    finally:
        _destroy_ctk_root(root)
test("create_browser_tab() widget creation", t_gui_parser_tab)

# ── 6. Syntax check all .py files ──────────────────────────
print("\n=== Syntax Check ===")

def t_syntax_all():
    import py_compile
    import tempfile
    py_files = []
    for name in [
        'ets_common.py', 'ets_auto.py', 'ets_word_pk.py', 'ets_parser.py',
        'ets_browser_ui.py', 'ets_remote.py', 'ets_gui.py', 'run.py',
        'ets_strategy.py', 'ets_hotkey.py', 'ets_compat.py',
    ]:
        fpath = os.path.join(SRC, name)
        if os.path.exists(fpath):
            py_files.append((name, fpath))
        else:
            raise AssertionError("required module missing for py_compile: %s" % name)
    with tempfile.TemporaryDirectory(prefix='ets_compile_') as compile_dir:
        for name, fpath in py_files:
            cfile = os.path.join(compile_dir, name + 'c')
            py_compile.compile(fpath, cfile=cfile, doraise=True)
test("all .py files pass py_compile (incl. strategy/hotkey)", t_syntax_all)

# ── 7. Strategy layer tests ────────────────────────────────
print("\n=== Strategy Layer Tests ===")

def t_strategy_import():
    from ets_strategy import ETSStrategy, ETS_DATA_DIR
    # CI runners don't have ETS data — skip if directory absent
    if not os.path.isdir(ETS_DATA_DIR):
        print(f"  [SKIP] ETS_DATA_DIR not found: {ETS_DATA_DIR}")
        return
test("import ETSStrategy + ETS_DATA_DIR exists", t_strategy_import)

# Find a real set_id with choose data for tests below
_STRATEGY_TEST_SET = None
_STRATEGY_TEST_STID = None
_STRATEGY_TEST_QID = None
_STRATEGY_TEST_ANSWER = None

def _find_test_set():
    """Scan ETS cache for a set with collector.choose data."""
    global _STRATEGY_TEST_SET, _STRATEGY_TEST_STID, _STRATEGY_TEST_QID, _STRATEGY_TEST_ANSWER
    if _STRATEGY_TEST_SET is not None:
        return True
    from ets_strategy import ETS_DATA_DIR
    if not os.path.isdir(ETS_DATA_DIR):
        return False
    for set_id in os.listdir(ETS_DATA_DIR):
        exam_dir = os.path.join(ETS_DATA_DIR, set_id)
        if not os.path.isdir(exam_dir):
            continue
        for d in sorted(os.listdir(exam_dir)):
            if not d.startswith('content_'):
                continue
            cj = os.path.join(exam_dir, d, 'content.json')
            if not os.path.exists(cj):
                continue
            try:
                import json
                with open(cj, encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('structure_type') == 'collector.choose':
                    info = data.get('info', {})
                    xtlist = info.get('xtlist', [])
                    if xtlist and xtlist[0].get('answer'):
                        _STRATEGY_TEST_SET = set_id
                        _STRATEGY_TEST_STID = str(info.get('stid', ''))
                        _STRATEGY_TEST_QID = str(xtlist[0].get('xt_xh', ''))
                        _STRATEGY_TEST_ANSWER = xtlist[0]['answer']
                        return True
            except Exception:
                continue
    return False

def t_strategy_load_set():
    if not _find_test_set():
        # No ETS data on this machine — skip gracefully
        print("  [SKIP] No ETS cache data available")
        return
    from ets_strategy import ETSStrategy
    s = ETSStrategy()
    ok = s.load_set(_STRATEGY_TEST_SET)
    assert ok, f"load_set({_STRATEGY_TEST_SET}) returned False"
    assert len(s.sections) > 0, "No sections loaded"
    assert len(s.answer_index) > 0, "answer_index is empty after load_set"
test("strategy.load_set() loads sections + answer_index", t_strategy_load_set)

def t_strategy_lookup_choose():
    if not _find_test_set():
        return  # skip if no data
    from ets_strategy import ETSStrategy
    s = ETSStrategy()
    s.load_set(_STRATEGY_TEST_SET)
    result = s.lookup('collector.choose', _STRATEGY_TEST_STID, qid=_STRATEGY_TEST_QID)
    assert result is not None, f"lookup returned None for stid={_STRATEGY_TEST_STID} qid={_STRATEGY_TEST_QID}"
    assert result.get('type') == 'choose', f"Expected type=choose, got {result.get('type')}"
    assert result.get('answer', '').upper() == _STRATEGY_TEST_ANSWER.upper(), \
        f"Expected answer={_STRATEGY_TEST_ANSWER}, got {result.get('answer')}"
    assert result.get('source') == 'local', f"Expected source=local, got {result.get('source')}"
test("strategy.lookup() returns correct choose answer", t_strategy_lookup_choose)

def t_strategy_lookup_missing():
    """lookup with non-existent stid should return None."""
    from ets_strategy import ETSStrategy
    s = ETSStrategy()
    result = s.lookup('collector.choose', 'nonexistent_stid_99999', qid='999')
    assert result is None, f"Expected None for non-existent key, got {result}"
test("strategy.lookup() returns None for missing key", t_strategy_lookup_missing)

def t_strategy_text_similarity():
    from ets_strategy import ETSStrategy
    s = ETSStrategy()
    # Identical strings → high score
    score = s._text_similarity("hello world foo bar", "hello world foo bar")
    assert score == 1.0, f"Identical strings should score 1.0, got {score}"
    # Completely different → low score
    score = s._text_similarity("apple banana orange", "xyz123 abc456")
    assert score < 0.3, f"Unrelated strings should score <0.3, got {score}"
    # Empty → 0
    assert s._text_similarity("", "hello") == 0.0
    assert s._text_similarity(None, "hello") == 0.0
    # Similar but reordered → should be high but not 1.0
    score = s._text_similarity("the quick brown fox", "the brown quick fox")
    assert 0.3 < score < 1.0, f"Reordered words should score 0.3-1.0, got {score}"
test("strategy._text_similarity() returns expected scores", t_strategy_text_similarity)

def t_strategy_list_sections():
    if not _find_test_set():
        return
    from ets_strategy import ETSStrategy
    s = ETSStrategy()
    s.load_set(_STRATEGY_TEST_SET)
    sections = s.list_sections()
    assert isinstance(sections, list), f"list_sections should return list, got {type(sections)}"
    if sections:
        first = sections[0] if isinstance(sections, list) else None
        assert first is not None
test("strategy.list_sections() returns list", t_strategy_list_sections)

def t_strategy_get_recording_answers():
    """get_recording_answers should return list (may be empty if no recording data)."""
    if not _find_test_set():
        return
    from ets_strategy import ETSStrategy
    s = ETSStrategy()
    s.load_set(_STRATEGY_TEST_SET)
    result = s.get_recording_answers()
    assert isinstance(result, list), f"Expected list, got {type(result)}"
    # If non-empty, each item should have a 'type' field
    for item in result:
        assert isinstance(item, dict), f"Each recording answer should be dict, got {type(item)}"
        assert 'type' in item, f"Recording answer missing 'type' field: {item}"
test("strategy.get_recording_answers() returns list with type field", t_strategy_get_recording_answers)

# ── 8. Answer parsing tests (load_answers for 6 types) ─────
print("\n=== Answer Parsing Tests ===")

import tempfile, shutil, json as _json

def _make_content_json(structure_type, info):
    """Build a minimal content.json dict."""
    return {'structure_type': structure_type, 'info': info}

def _make_bare_auto(ets_base, set_id):
    """Create a minimal ETSAutoAnswer without __init__ (for offline testing)."""
    from ets_auto import ETSAutoAnswer
    from ets_strategy import ETSStrategy
    auto = ETSAutoAnswer.__new__(ETSAutoAnswer)
    auto.ets_base = ets_base
    auto.set_id = set_id
    auto.answers = {}
    auto.recording_answers = []
    auto._recording_window_closed = False
    auto.strategy = ETSStrategy()
    auto.debug = lambda *a: None
    return auto

def t_load_answers_choose():
    """Test load_answers parses collector.choose correctly."""
    tmp_base = tempfile.mkdtemp(prefix='ets_test_')
    set_id = '900001'  # digits-only (matches _safe_set_id)
    content_dir = os.path.join(tmp_base, set_id, 'content_001')
    os.makedirs(content_dir)
    data = _make_content_json('collector.choose', {
        'stid': '1001',
        'xtlist': [
            {'xt_xh': '1', 'answer': 'A', 'xt_nr': 'Q1', 'xxlist': []},
            {'xt_xh': '2', 'answer': 'C', 'xt_nr': 'Q2', 'xxlist': []},
        ]
    })
    with open(os.path.join(content_dir, 'content.json'), 'w', encoding='utf-8') as f:
        _json.dump(data, f, ensure_ascii=False)
    auto = _make_bare_auto(tmp_base, set_id)
    ok = auto.load_answers()
    assert ok, "load_answers returned False"
    assert '1001_1' in auto.answers, f"Missing key 1001_1, keys={list(auto.answers.keys())}"
    assert auto.answers['1001_1']['answer'] == 'A'
    assert auto.answers['1001_2']['answer'] == 'C'
    shutil.rmtree(tmp_base)
test("load_answers parses collector.choose", t_load_answers_choose)

def t_load_answers_fill():
    """Test load_answers parses collector.fill with slash-separated alternatives."""
    from ets_auto import ETSAutoAnswer
    tmp_base = tempfile.mkdtemp(prefix='ets_test_')
    set_id = '900002'  # digits-only (matches _safe_set_id)
    content_dir = os.path.join(tmp_base, set_id, 'content_001')
    os.makedirs(content_dir)
    data = _make_content_json('collector.fill', {
        'stid': '2001',
        'std': [
            {'xth': '1', 'value': 'Organise/Organize'},
            {'xth': '2', 'value': 'apple'},
        ]
    })
    with open(os.path.join(content_dir, 'content.json'), 'w', encoding='utf-8') as f:
        _json.dump(data, f, ensure_ascii=False)
    auto = _make_bare_auto(tmp_base, set_id)
    ok = auto.load_answers()
    assert ok
    assert auto.answers['2001_1']['answer'] == 'Organise'
    assert 'Organize' in auto.answers['2001_1'].get('alternatives', [])
    assert auto.answers['2001_2']['answer'] == 'apple'
    shutil.rmtree(tmp_base)
test("load_answers parses collector.fill (with alternatives)", t_load_answers_fill)

def t_strategy_index_role():
    """Test strategy layer indexes collector.role correctly.
    role questions are NOT parsed by load_answers (which handles choose/fill/read/picture/dialogue).
    They are indexed by strategy._index_section with key format: collector.role_{stid}_q{qi+1}.
    """
    if not _find_test_set():
        return
    from ets_strategy import ETSStrategy, ETS_DATA_DIR
    # Find a set that contains role data
    role_set = None
    role_stid = None
    for sid in os.listdir(ETS_DATA_DIR):
        exam_dir = os.path.join(ETS_DATA_DIR, sid)
        if not os.path.isdir(exam_dir):
            continue
        for d in os.listdir(exam_dir):
            if not d.startswith('content_'):
                continue
            cj = os.path.join(exam_dir, d, 'content.json')
            if not os.path.exists(cj):
                continue
            try:
                with open(cj, encoding='utf-8') as f:
                    data = _json.load(f)
                if data.get('structure_type') == 'collector.role':
                    role_set = sid
                    role_stid = str(data['info']['stid'])
                    break
            except Exception:
                continue
        if role_set:
            break
    if not role_set:
        return  # no role data, skip
    s = ETSStrategy()
    s.load_set(role_set)
    # Verify role key exists in answer_index with q1 format
    expected_key = 'collector.role_%s_q1' % role_stid
    assert expected_key in s.answer_index, \
        f"Key {expected_key} not in answer_index. Keys: {[k for k in s.answer_index if 'role' in k][:5]}"
    entry = s.answer_index[expected_key]
    assert entry.get('type') == 'oral', f"Expected type=oral, got {entry.get('type')}"
    assert 'variants' in entry, f"Missing 'variants' in role entry: {entry}"
    assert len(entry['variants']) > 0, "Empty variants list in role entry"
test("strategy._index_section indexes collector.role with q1 key", t_strategy_index_role)

def t_load_answers_empty_answer():
    """load_answers should skip empty answers gracefully."""
    from ets_auto import ETSAutoAnswer
    tmp_base = tempfile.mkdtemp(prefix='ets_test_')
    set_id = '900003'  # digits-only (matches _safe_set_id)
    content_dir = os.path.join(tmp_base, set_id, 'content_001')
    os.makedirs(content_dir)
    data = _make_content_json('collector.choose', {
        'stid': '4001',
        'xtlist': [
            {'xt_xh': '1', 'answer': '', 'xt_nr': '', 'xxlist': []},
            {'xt_xh': '2', 'answer': 'B', 'xt_nr': '', 'xxlist': []},
        ]
    })
    with open(os.path.join(content_dir, 'content.json'), 'w', encoding='utf-8') as f:
        _json.dump(data, f, ensure_ascii=False)
    auto = _make_bare_auto(tmp_base, set_id)
    ok = auto.load_answers()
    assert ok
    assert '4001_2' in auto.answers
    assert '4001_1' not in auto.answers, "Empty answer should be skipped"
    shutil.rmtree(tmp_base)
test("load_answers skips empty answers", t_load_answers_empty_answer)

def t_load_answers_missing_dir():
    """load_answers returns False when exam dir doesn't exist."""
    from ets_auto import ETSAutoAnswer
    auto = _make_bare_auto(tempfile.gettempdir(), 'nonexistent_set_99999')
    ok = auto.load_answers()
    assert not ok, "load_answers should return False for missing dir"
test("load_answers returns False for missing directory", t_load_answers_missing_dir)

# ── 9. ETS data structure validation ───────────────────────
print("\n=== ETS Data Structure Validation ===")

KNOWN_STRUCTURE_TYPES = {
    'collector.choose', 'collector.fill', 'collector.role',
    'collector.picture', 'collector.read', 'collector.dialogue',
    'collector.repeat_dialogue'  # repeat/follow-along dialogue (no answer needed)
}

def t_ets_data_structure_types():
    """Scan all local content.json files — verify structure_type is known."""
    from ets_strategy import ETS_DATA_DIR
    if not os.path.isdir(ETS_DATA_DIR):
        return  # skip if no ETS data
    unknown_types = set()
    count = 0
    for set_id in os.listdir(ETS_DATA_DIR):
        exam_dir = os.path.join(ETS_DATA_DIR, set_id)
        if not os.path.isdir(exam_dir):
            continue
        for d in os.listdir(exam_dir):
            if not d.startswith('content_'):
                continue
            cj = os.path.join(exam_dir, d, 'content.json')
            if not os.path.exists(cj):
                continue
            try:
                with open(cj, encoding='utf-8') as f:
                    data = _json.load(f)
                stype = data.get('structure_type', '')
                if stype not in KNOWN_STRUCTURE_TYPES:
                    unknown_types.add(stype)
                count += 1
            except Exception:
                pass
    assert count > 0, "No content.json files found in ETS cache"
    assert not unknown_types, \
        f"Unknown structure_type(s) found: {unknown_types}. " \
        f"If ETS added a new question type, update load_answers + strategy._index_section."
test("All local content.json have known structure_type", t_ets_data_structure_types)

def t_ets_data_choose_format():
    """Verify all collector.choose content.json have valid xtlist with answers."""
    from ets_strategy import ETS_DATA_DIR
    if not os.path.isdir(ETS_DATA_DIR):
        return
    issues = []
    count = 0
    for set_id in os.listdir(ETS_DATA_DIR):
        exam_dir = os.path.join(ETS_DATA_DIR, set_id)
        if not os.path.isdir(exam_dir):
            continue
        for d in os.listdir(exam_dir):
            if not d.startswith('content_'):
                continue
            cj = os.path.join(exam_dir, d, 'content.json')
            if not os.path.exists(cj):
                continue
            try:
                with open(cj, encoding='utf-8') as f:
                    data = _json.load(f)
                if data.get('structure_type') != 'collector.choose':
                    continue
                count += 1
                info = data.get('info', {})
                stid = info.get('stid', '')
                if not stid:
                    issues.append(f"{set_id}/{d}: missing stid")
                    continue
                xtlist = info.get('xtlist', [])
                if not xtlist:
                    issues.append(f"{set_id}/{d}: empty xtlist")
                    continue
                for i, xt in enumerate(xtlist):
                    ans = xt.get('answer', '')
                    if not ans:
                        issues.append(f"{set_id}/{d} xt[{i}]: empty answer")
                    elif not re.match(r'^[A-Z]$', ans):
                        issues.append(f"{set_id}/{d} xt[{i}]: invalid answer '{ans}'")
            except Exception as e:
                issues.append(f"{set_id}/{d}: parse error: {e}")
    assert count > 0, "No collector.choose data found"
    if issues:
        # Report first 5 issues
        msg = '; '.join(issues[:5])
        if len(issues) > 5:
            msg += f' ... and {len(issues) - 5} more'
        raise AssertionError(f"Choose data issues ({len(issues)}): {msg}")
test("All collector.choose data has valid stid + xtlist + answer", t_ets_data_choose_format)

def t_ets_data_fill_format():
    """Verify all collector.fill content.json have valid std with values."""
    from ets_strategy import ETS_DATA_DIR
    if not os.path.isdir(ETS_DATA_DIR):
        return
    import re as _re
    issues = []
    count = 0
    for set_id in os.listdir(ETS_DATA_DIR):
        exam_dir = os.path.join(ETS_DATA_DIR, set_id)
        if not os.path.isdir(exam_dir):
            continue
        for d in os.listdir(exam_dir):
            if not d.startswith('content_'):
                continue
            cj = os.path.join(exam_dir, d, 'content.json')
            if not os.path.exists(cj):
                continue
            try:
                with open(cj, encoding='utf-8') as f:
                    data = _json.load(f)
                if data.get('structure_type') != 'collector.fill':
                    continue
                count += 1
                info = data.get('info', {})
                stid = info.get('stid', '')
                if not stid:
                    issues.append(f"{set_id}/{d}: missing stid")
                    continue
                std = info.get('std', [])
                if not std:
                    issues.append(f"{set_id}/{d}: empty std")
                    continue
                for i, s in enumerate(std):
                    val = s.get('value', '')
                    if not val:
                        issues.append(f"{set_id}/{d} std[{i}]: empty value")
            except Exception as e:
                issues.append(f"{set_id}/{d}: parse error: {e}")
    if count == 0:
        return  # no fill data, skip
    if issues:
        msg = '; '.join(issues[:5])
        if len(issues) > 5:
            msg += f' ... and {len(issues) - 5} more'
        raise AssertionError(f"Fill data issues ({len(issues)}): {msg}")
test("All collector.fill data has valid stid + std + value", t_ets_data_fill_format)

# ── 10. Remote module logic tests ───────────────────────────
print("\n=== Remote Module Logic Tests ===")

def t_compare_versions():
    from ets_remote import compare_versions
    assert compare_versions('0.6.1', '0.6.2') == -1
    assert compare_versions('0.6.2', '0.6.1') == 1
    assert compare_versions('0.6.1', '0.6.1') == 0
    assert compare_versions('1.0.0', '0.9.9') == 1
    assert compare_versions('0.9.9', '1.0.0') == -1
    assert compare_versions('0.10.0', '0.9.0') == 1, "0.10.0 > 0.9.0"
    assert compare_versions('0.6.1', '0.6.10') == -1, "0.6.1 < 0.6.10"
test("compare_versions() handles semver correctly", t_compare_versions)

def _cleared_remote_integrity_env():
    """Context manager: clear integrity env; restore previous values on exit."""
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        old_h = os.environ.pop('ETS_REMOTE_HMAC', None)
        old_p = os.environ.pop('ETS_REMOTE_PUBKEY', None)
        try:
            yield
        finally:
            if old_h is None:
                os.environ.pop('ETS_REMOTE_HMAC', None)
            else:
                os.environ['ETS_REMOTE_HMAC'] = old_h
            if old_p is None:
                os.environ.pop('ETS_REMOTE_PUBKEY', None)
            else:
                os.environ['ETS_REMOTE_PUBKEY'] = old_p
    return _cm()


def t_classify_info_block():
    from ets_remote import RemoteInfo, classify_info
    with _cleared_remote_integrity_env():
        info = RemoteInfo(allow_start=False)
        level, reason = classify_info(info)
        assert level == 'warn', f"Expected warn when unsigned, got {level}"
        assert reason, "Expected non-empty reason"
        os.environ['ETS_REMOTE_HMAC'] = 'pre-release-secret'
        level2, _ = classify_info(info)
        assert level2 == 'block', f"Expected block when HMAC set, got {level2}"
test("classify_info() warns unsigned / blocks signed when allow_start=False", t_classify_info_block)

def t_classify_info_force_update():
    from ets_remote import RemoteInfo, classify_info
    with _cleared_remote_integrity_env():
        info = RemoteInfo(allow_start=True, force_update=True, latest_version='0.7.0')
        level, reason = classify_info(info)
        assert level == 'warn', f"Expected warn when unsigned, got {level}"
test("classify_info() warns when force_update=True and unsigned", t_classify_info_force_update)

def t_classify_info_normal():
    from ets_remote import RemoteInfo, classify_info
    info = RemoteInfo(allow_start=True)
    level, reason = classify_info(info)
    assert level == 'normal', f"Expected normal, got {level}"
test("classify_info() returns normal for allow_start=True", t_classify_info_normal)

def t_classify_info_none():
    from ets_remote import classify_info
    level, reason = classify_info(None)
    assert level == 'normal', f"Expected normal for None, got {level}"
test("classify_info() returns normal for None", t_classify_info_none)

def t_should_block_start():
    from ets_remote import RemoteInfo, should_block_start
    with _cleared_remote_integrity_env():
        blocked, reason = should_block_start(RemoteInfo(allow_start=False))
        assert blocked is False, "unsigned kill-switch must not hard-block"
        assert reason == ''
        os.environ['ETS_REMOTE_HMAC'] = 'pre-release-secret'
        blocked2, reason2 = should_block_start(RemoteInfo(allow_start=False))
        assert blocked2 is True
        assert reason2
        blocked3, reason3 = should_block_start(RemoteInfo(allow_start=True))
        assert blocked3 is False
        assert reason3 == ''
test("should_block_start() matches classify_info (unsigned warn / signed block)", t_should_block_start)

def t_remote_info_to_dict():
    from ets_remote import RemoteInfo
    info = RemoteInfo(latest_version='0.6.2', allow_start=True)
    d = info.to_dict()
    assert isinstance(d, dict)
    assert d['latest_version'] == '0.6.2'
    assert d['allow_start'] is True
    assert 'pk_extra_url' in d
test("RemoteInfo.to_dict() returns all slots", t_remote_info_to_dict)

# ── 11. Hotkey module tests ────────────────────────────────
print("\n=== Hotkey Module Tests ===")

def t_hotkey_import():
    from ets_hotkey import ETSHotkey
    hk = ETSHotkey()
    assert hk is not None
test("ETSHotkey can be instantiated", t_hotkey_import)

def t_hotkey_initial_state():
    from ets_hotkey import ETSHotkey
    hk = ETSHotkey()
    assert hk.is_paused is False, "is_paused should start False"
    assert hk.should_skip is False, "should_skip should start False"
    assert hk.should_stop is False, "should_stop should start False"
test("ETSHotkey initial state all False", t_hotkey_initial_state)

def t_hotkey_clear_skip():
    from ets_hotkey import ETSHotkey
    hk = ETSHotkey()
    hk.clear_skip()
    assert hk.should_skip is False
test("ETSHotkey.clear_skip() works", t_hotkey_clear_skip)

def t_hotkey_clear_stop():
    from ets_hotkey import ETSHotkey
    hk = ETSHotkey()
    # clear_stop may or may not exist — test if available
    if hasattr(hk, 'clear_stop'):
        hk.clear_stop()
        assert hk.should_stop is False
test("ETSHotkey.clear_stop() works", t_hotkey_clear_stop)

# ── 12. Method signature stability ─────────────────────────
print("\n=== Method Signature Tests ===")

def t_sig_auto_run():
    import inspect
    from ets_auto import ETSAutoAnswer
    sig = inspect.signature(ETSAutoAnswer.run)
    assert 'max_steps' in sig.parameters, f"run() missing max_steps param: {sig}"
test("ETSAutoAnswer.run(max_steps) signature stable", t_sig_auto_run)

def t_sig_auto_init():
    import inspect
    from ets_auto import ETSAutoAnswer
    sig = inspect.signature(ETSAutoAnswer.__init__)
    assert 'port' in sig.parameters, f"__init__ missing port: {sig}"
    assert 'debug_mode' in sig.parameters, f"__init__ missing debug_mode: {sig}"
    assert 'stop_event' in sig.parameters, f"__init__ missing stop_event: {sig}"
test("ETSAutoAnswer.__init__(port, debug_mode, stop_event) signature stable", t_sig_auto_init)

def t_sig_strategy_lookup():
    import inspect
    from ets_strategy import ETSStrategy
    sig = inspect.signature(ETSStrategy.lookup)
    assert 'structure_type' in sig.parameters
    assert 'stid' in sig.parameters
    assert 'qid' in sig.parameters
    assert 'title_text' in sig.parameters
    assert 'dom_answer' in sig.parameters
test("ETSStrategy.lookup() signature stable", t_sig_strategy_lookup)

def t_sig_remote_init():
    import inspect
    from ets_remote import ETSRemote
    sig = inspect.signature(ETSRemote.__init__)
    assert 'current_version' in sig.parameters
    assert 'owner' in sig.parameters
    assert 'repo' in sig.parameters
test("ETSRemote.__init__() signature stable", t_sig_remote_init)

def t_sig_base_connect():
    import inspect
    from ets_common import ETSBase
    sig = inspect.signature(ETSBase.connect)
    assert sig.parameters, "connect() should have parameters"
test("ETSBase.connect() signature stable", t_sig_base_connect)

# ── 13. Version / path / integrity hygiene (v0.6.5) ─────────
print("\n=== Version / Path / Integrity Hygiene ===")

def t_app_version_single_source():
    from ets_common import APP_VERSION
    import ets_auto
    import ets_word_pk
    import ets_gui
    assert isinstance(APP_VERSION, str) and APP_VERSION, "APP_VERSION empty"
    assert ets_auto.__version__ == APP_VERSION, (
        f"ets_auto.__version__={ets_auto.__version__!r} != {APP_VERSION!r}")
    assert ets_word_pk.__version__ == APP_VERSION, (
        f"ets_word_pk.__version__={ets_word_pk.__version__!r} != {APP_VERSION!r}")
    gui_v = getattr(ets_gui, 'APP_VERSION', APP_VERSION)
    assert gui_v == APP_VERSION, f"ets_gui.APP_VERSION={gui_v!r} != {APP_VERSION!r}"
    # info.json at repo root should match when present
    info_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'info.json')
    if os.path.isfile(info_path):
        with open(info_path, 'r', encoding='utf-8') as f:
            info = json.load(f)
        assert info.get('version') == APP_VERSION, (
            f"info.json version={info.get('version')!r} != {APP_VERSION!r}")
    # Project-only metadata should not silently lag behind the release version.
    project_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pyproject.toml')
    if os.path.isfile(project_path):
        import tomllib
        with open(project_path, 'rb') as f:
            project_meta = tomllib.load(f)
        assert project_meta.get('project', {}).get('version') == APP_VERSION, (
            f"pyproject.toml version={project_meta.get('project', {}).get('version')!r} != {APP_VERSION!r}")
test("APP_VERSION matches auto/pk/gui/info.json/pyproject", t_app_version_single_source)

def t_user_data_path_basename():
    from ets_common import user_data_path
    p = user_data_path('pk_extra.json')
    assert os.path.basename(p) == 'pk_extra.json', p
    p2 = user_data_path(r'..\..\evil.json')
    assert os.path.basename(p2) == 'evil.json', p2
    assert '..' not in os.path.basename(p2)
test("user_data_path() uses basename only (no path escape)", t_user_data_path_basename)

def t_verify_remote_integrity_api():
    from ets_remote import verify_remote_payload_integrity
    # No key configured → allowlist-only success
    ok, why = verify_remote_payload_integrity({'version': '0.6.5'})
    assert ok is True, why
    assert isinstance(why, str) and why
test("verify_remote_payload_integrity() allowlist-only when no key", t_verify_remote_integrity_api)

def t_word_pk_stop_event_default():
    import threading
    from ets_word_pk import ETSWordPK
    pk = ETSWordPK(debug_mode=False, stop_event=None)
    assert pk.stop_event is not None
    assert isinstance(pk.stop_event, threading.Event)
test("ETSWordPK default stop_event is Event (OPEN-M4)", t_word_pk_stop_event_default)

def t_safe_set_id_digits_only():
    from ets_strategy import _safe_set_id
    assert _safe_set_id('12345') == '12345'
    assert _safe_set_id(' 99 ') == '99'
    assert _safe_set_id('../x') is None
    assert _safe_set_id('12ab') is None
    assert _safe_set_id(None) is None
test("_safe_set_id() digits-only / rejects traversal", t_safe_set_id_digits_only)

def t_pick_ets_tab_prefers_exam():
    from ets_common import ETSBase
    base = ETSBase()
    tabs = [
        {'url': 'https://statics.ets100.com/home', 'title': 'Home',
         'webSocketDebuggerUrl': 'ws://localhost/1', 'type': 'page'},
        {'url': 'https://statics.ets100.com/x#/doHomework?id=1', 'title': 'HW',
         'webSocketDebuggerUrl': 'ws://localhost/2', 'type': 'page'},
    ]
    picked = base._pick_ets_tab(tabs)
    assert picked is not None
    assert 'doHomework' in picked['url'] or 'Homework' in picked.get('title', '')
test("_pick_ets_tab() prefers homework/exam URL (OPEN-H3)", t_pick_ets_tab_prefers_exam)

def t_js_escape_line_separators():
    from ets_common import ETSBase
    out = ETSBase.js_escape('a b c')
    assert ' ' not in out and ' ' not in out
    assert '\\u2028' in out and '\\u2029' in out
test("js_escape() escapes U+2028/U+2029 (OPEN-M3)", t_js_escape_line_separators)

# ── Summary ────────────────────────────────────────────────
print(f"\n{'='*40}")
print(f"Results: {passed} passed, {failed} failed")
if failed:
    print("\nFailed tests:")
    for name, err in errors:
        print(f"  [FAIL] {name}: {err}")
    sys.exit(1)
else:
    print("All tests passed!")
    sys.exit(0)
