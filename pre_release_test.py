#!/usr/bin/env python3
"""
ETS_Auto Pre-Release Test Suite
Runs before building exe to catch import errors, missing deps, and basic regressions.
Exit code 0 = all pass, non-zero = fail (CI should abort).
"""
import sys
import os

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

def t_inheritance():
    from ets_common import ETSBase
    from ets_auto import ETSAutoAnswer
    from ets_word_pk import ETSWordPK
    assert issubclass(ETSAutoAnswer, ETSBase), "ETSAutoAnswer should inherit ETSBase"
    assert issubclass(ETSWordPK, ETSBase), "ETSWordPK should inherit ETSBase"
test("ETSAutoAnswer/ETSWordPK inherit ETSBase", t_inheritance)

def t_parser_scan():
    from ets_parser import scan_sets
    sets = scan_sets()
    # scan_sets returns a list, even if empty (no ETS data on CI)
    assert isinstance(sets, list), f"scan_sets should return list, got {type(sets)}"
test("ets_parser.scan_sets() returns list", t_parser_scan)

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

# ── 5. GUI Widget Smoke Tests ─────────────────────────────
print("\n=== GUI Widget Tests ===")

def t_gui_widgets():
    import customtkinter as ctk
    root = ctk.CTk()
    # CTkEntry does NOT support command=; only bind() works
    entry = ctk.CTkEntry(root, placeholder_text="test")
    entry.bind('<Return>', lambda e: None)  # This should work
    # Verify configure() only accepts valid CTkEntry kwargs
    try:
        entry.configure(command=lambda: None)
        root.destroy()
        raise AssertionError("CTkEntry.configure(command=...) should raise ValueError")
    except ValueError:
        pass  # Expected - command is not a valid CTkEntry argument
    root.destroy()
test("CTkEntry widget creation + bind (no command=)", t_gui_widgets)

def t_gui_parser_tab():
    """Smoke test: create_browser_tab must not crash with minimal data."""
    import customtkinter as ctk
    from ets_parser import create_browser_tab
    root = ctk.CTk()
    tab = ctk.CTkFrame(root)
    tab.pack()
    try:
        create_browser_tab(tab)
    except Exception as e:
        # If ETS data dir doesn't exist, that's fine - just no crash from widget code
        if 'ETS' not in str(e) and 'AppData' not in str(e) and 'scan_sets' not in str(e):
            raise
    root.destroy()
test("create_browser_tab() widget creation", t_gui_parser_tab)

# ── 6. Syntax check all .py files ──────────────────────────
print("\n=== Syntax Check ===")

def t_syntax_all():
    import py_compile
    py_files = []
    for name in ['ets_common.py', 'ets_auto.py', 'ets_word_pk.py', 'ets_parser.py', 'ets_gui.py', 'run.py']:
        fpath = os.path.join(SRC, name)
        if os.path.exists(fpath):
            py_files.append((name, fpath))
    for name, fpath in py_files:
        py_compile.compile(fpath, doraise=True)
test("all .py files pass py_compile", t_syntax_all)

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
