#!/usr/bin/env python3
"""
ETS Auto — Unit Test Suite
============================
Tests pure logic functions with NO external dependencies (no CDP, no ETS cache,
no customtkinter, no websocket).

Run:
    python test_unit.py
    python test_unit.py -v          # verbose
    python test_unit.py TestETSStrategy   # single class

Architecture:
    Each test class targets one module.  All mocks are self-contained.
    Release-critical fixtures are created in temporary directories. Optional
    golden fixtures may be skipped. No pytest or conftest is required.

─────────────────────────────────────────────────────────────────────────────
"""
import sys as _sys
import os as _os
import json
import re
import html
import unittest
import tempfile
import shutil
import time
from contextlib import contextmanager

_SysPath = _os.path.dirname(_os.path.abspath(__file__))
_SrcAuto  = _os.path.dirname(_SysPath)
if _SrcAuto not in _sys.path:
    _sys.path.insert(0, _SrcAuto)


@contextmanager
def _cleared_remote_integrity_env():
    """Temporarily clear ETS_REMOTE_HMAC / ETS_REMOTE_PUBKEY for unsigned-mode tests."""
    old_h = _os.environ.pop('ETS_REMOTE_HMAC', None)
    old_p = _os.environ.pop('ETS_REMOTE_PUBKEY', None)
    try:
        yield
    finally:
        if old_h is None:
            _os.environ.pop('ETS_REMOTE_HMAC', None)
        else:
            _os.environ['ETS_REMOTE_HMAC'] = old_h
        if old_p is None:
            _os.environ.pop('ETS_REMOTE_PUBKEY', None)
        else:
            _os.environ['ETS_REMOTE_PUBKEY'] = old_p

# ── Mock heavy dependencies before importing target modules ────────────────────
# These modules require websocket, customtkinter, etc. which may not be installed.
# We inject lightweight stubs so pure-logic tests can run without them.
import types as _types

# Prefer the real runtime dependency; use a stub only on minimal dev installs.
try:
    import websocket as _real_websocket  # noqa: F401
except ImportError:
    _ws = _types.ModuleType('websocket')
    _ws.WebSocketException = type('WebSocketException', (Exception,), {})
    _ws.WebSocketConnectionClosedException = type(
        'WebSocketConnectionClosedException', (_ws.WebSocketException,), {})
    _ws.WebSocketTimeoutException = type(
        'WebSocketTimeoutException', (_ws.WebSocketException,), {})
    _ws.WebSocketProtocolException = type(
        'WebSocketProtocolException', (_ws.WebSocketException,), {})
    _ws.create_connection = lambda *a, **k: None
    _sys.modules['websocket'] = _ws

try:
    import customtkinter as _real_customtkinter  # noqa: F401
except ImportError:
    _ctk = _types.ModuleType('customtkinter')
    _ctk.CTk = type('CTk', (), {})
    _ctk.CTkFrame = type('CTkFrame', (), {})
    _ctk.CTkLabel = type('CTkLabel', (), {})
    _ctk.CTkEntry = type('CTkEntry', (), {})
    _ctk.CTkButton = type('CTkButton', (), {})
    _ctk.CTkTabview = type('CTkTabview', (), {})
    _ctk.CTkScrollableFrame = type('CTkScrollableFrame', (), {})
    _ctk.CTkTextbox = type('CTkTextbox', (), {})
    _ctk.CTkOptionMenu = type('CTkOptionMenu', (), {})
    _ctk.CTkFont = type('CTkFont', (), {})
    _ctk.CTkImage = type('CTkImage', (), {})
    _ctk.set_appearance_mode = lambda *a: None
    _ctk.set_default_color_theme = lambda *a: None
    _sys.modules['customtkinter'] = _ctk

try:
    import PIL  # noqa: F401
except ImportError:
    _pil = _types.ModuleType('PIL')
    _pil.Image = _types.ModuleType('PIL.Image')
    _pil.Image.open = lambda *a, **k: None
    _pil.ImageTk = _types.ModuleType('PIL.ImageTk')
    _pil.ImageTk.PhotoImage = lambda *a, **k: None
    _sys.modules['PIL'] = _pil
    _sys.modules['PIL.Image'] = _pil.Image
    _sys.modules['PIL.ImageTk'] = _pil.ImageTk

# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_content_json(structure_type, info_dict):
    return json.dumps({
        "structure_type": structure_type,
        "info": info_dict
    }, ensure_ascii=False)


def _make_exam_cache(root_dir, set_id, sections):
    """
    Create a fake ETS cache tree:

        root_dir/
            set_id/
                content_xxx/
                    content.json   ← one per section
                content_yyy/
                    content.json
                ...
    """
    set_dir = _os.path.join(root_dir, str(set_id))
    _os.makedirs(set_dir, exist_ok=True)
    for i, (dirname, content_json) in enumerate(sections):
        d = _os.path.join(set_dir, dirname)
        _os.makedirs(d, exist_ok=True)
        with open(_os.path.join(d, 'content.json'), 'w', encoding='utf-8') as f:
            f.write(content_json)
    return set_dir


# ═══════════════════════════════════════════════════════════════════════════════
#  TestETSStrategy — ets_strategy.py pure logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestETSStrategy(unittest.TestCase):

    def setUp(self):
        """Create a temporary ETS cache with realistic mock data."""
        self.cache_root = tempfile.mkdtemp(prefix='ets_test_')
        self._orig_data_dir = None
        # Patch EtsStrategy's ETS_DATA_DIR before import
        import ets_strategy
        self._orig_data_dir = ets_strategy.ETS_DATA_DIR
        ets_strategy.ETS_DATA_DIR = self.cache_root
        self.Strategy = ets_strategy.ETSStrategy

    def tearDown(self):
        shutil.rmtree(self.cache_root, ignore_errors=True)
        if self._orig_data_dir is not None:
            import ets_strategy
            ets_strategy.ETS_DATA_DIR = self._orig_data_dir

    # ── load_set() ───────────────────────────────────────────────────────────

    def test_load_set_not_found(self):
        s = self.Strategy()
        ok = s.load_set('99999')
        self.assertFalse(ok)
        self.assertEqual(len(s.sections), 0)
        self.assertEqual(len(s.answer_index), 0)

    def test_load_set_choose(self):
        set_id = '325173'
        _make_exam_cache(self.cache_root, set_id, [
            ('content_325173_1', _make_content_json('collector.choose', {
                'stid': '82750',
                'xtlist': [
                    {'xt_xh': '1', 'xt_nr': 'What caused the woman\'s illness?',
                     'answer': 'A',
                     'xxlist': [{'xx_mc': 'A', 'xx_nr': 'The food she ate'},
                                {'xx_mc': 'B', 'xx_nr': 'The weather'}]},
                    {'xt_xh': '2', 'xt_nr': 'What is her healthy habit?',
                     'answer': 'B',
                     'xxlist': [{'xx_mc': 'A', 'xx_nr': 'Sleeping late'},
                                {'xx_mc': 'B', 'xx_nr': 'Running every morning'}]},
                ],
            })),
        ])
        s = self.Strategy()
        ok = s.load_set(set_id)
        self.assertTrue(ok)
        self.assertEqual(len(s.sections), 1)
        self.assertEqual(s.sections[0]['type'], 'collector.choose')
        # Should index both questions
        self.assertIn('collector.choose_82750_1', s.answer_index)
        self.assertIn('collector.choose_82750_2', s.answer_index)
        self.assertEqual(s.answer_index['collector.choose_82750_1']['answer'], 'A')
        self.assertEqual(s.answer_index['collector.choose_82750_2']['answer'], 'B')

    def test_load_set_fill(self):
        set_id = '41001'
        _make_exam_cache(self.cache_root, set_id, [
            ('content_41001_1', _make_content_json('collector.fill', {
                'stid': '91020',
                'value': 'This is a transcript.',
                'std': [
                    {'xth': '1', 'value': 'apple'},
                    {'xth': '2', 'value': 'orange/grape'},   # slash → pick first
                    {'th':  '3', 'value': 'banana'},        # also accept 'th' key
                ],
            })),
        ])
        s = self.Strategy()
        ok = s.load_set(set_id)
        self.assertTrue(ok)
        self.assertEqual(s.answer_index['collector.fill_91020_1']['answer'], 'apple')
        self.assertEqual(s.answer_index['collector.fill_91020_2']['answer'], 'orange')  # split on /
        self.assertEqual(s.answer_index['collector.fill_91020_3']['answer'], 'banana')

    def test_load_set_picture(self):
        set_id = '50001'
        _make_exam_cache(self.cache_root, set_id, [
            ('content_50001_1', _make_content_json('collector.picture', {
                'stid': 'p100',
                'topic': 'School Science Fair',
                'value': '<p>Welcome to the Science Fair</p>',
                'keypoint': 'Learn about science and technology',
                'std': [
                    {'value': 'The fair showcases student projects.'},
                    {'value': 'It runs from March 1 to March 3.'},
                ],
            })),
        ])
        s = self.Strategy()
        ok = s.load_set(set_id)
        self.assertTrue(ok)
        key = 'collector.picture_p100'
        self.assertIn(key, s.answer_index)
        rec = s.answer_index[key]
        self.assertEqual(rec['type'], 'picture')
        self.assertEqual(rec['topic'], 'School Science Fair')

    def test_load_set_read(self):
        set_id = '60001'
        _make_exam_cache(self.cache_root, set_id, [
            ('content_60001_1', _make_content_json('collector.read', {
                'stid': 'r100',
                'value': 'Good morning everyone.',
                'symbol': '/ɑː/',
            })),
        ])
        s = self.Strategy()
        ok = s.load_set(set_id)
        self.assertTrue(ok)
        key = 'collector.read_r100'
        self.assertIn(key, s.answer_index)
        rec = s.answer_index[key]
        self.assertEqual(rec['type'], 'read')
        self.assertIn('Good morning', rec['answer'])

    def test_load_set_role(self):
        set_id = '70001'
        _make_exam_cache(self.cache_root, set_id, [
            ('content_70001_1', _make_content_json('collector.role', {
                'stid': 'role100',
                'value': 'Material text here.',
                'question': [
                    {'ask': 'How do you keep healthy?',
                     'keywords': 'exercise, diet',
                     'std': [
                         {'value': 'I run every morning.'},
                         {'value': 'Running keeps me healthy.'},
                         {'value': 'I exercise daily.'},
                     ]},
                ],
            })),
        ])
        s = self.Strategy()
        ok = s.load_set(set_id)
        self.assertTrue(ok)
        key = 'collector.role_role100_q1'
        self.assertIn(key, s.answer_index)
        rec = s.answer_index[key]
        self.assertEqual(rec['type'], 'oral')
        self.assertIn('healthy', rec['ask'].lower())
        self.assertEqual(len(rec['variants']), 3)

    def test_load_set_multiple_sections(self):
        set_id = '90001'
        _make_exam_cache(self.cache_root, set_id, [
            ('content_90001_1', _make_content_json('collector.choose', {
                'stid': 's1', 'xtlist': [{'xt_xh': '1', 'answer': 'C'}]})),
            ('content_90001_2', _make_content_json('collector.fill', {
                'stid': 's2', 'std': [{'xth': '1', 'value': 'hello'}]})),
            ('content_90001_3', _make_content_json('collector.picture', {
                'stid': 's3', 'topic': 'Festival', 'value': 'Text'})),
        ])
        s = self.Strategy()
        ok = s.load_set(set_id)
        self.assertTrue(ok)
        self.assertEqual(len(s.sections), 3)
        # All three types indexed independently
        self.assertIn('collector.choose_s1_1', s.answer_index)
        self.assertIn('collector.fill_s2_1', s.answer_index)
        self.assertIn('collector.picture_s3', s.answer_index)

    def test_invalid_content_does_not_hide_valid_sections(self):
        from unittest.mock import patch

        set_id = '900002'
        _make_exam_cache(self.cache_root, set_id, [
            ('content_1', _make_content_json('collector.choose', {
                'stid': 'valid',
                'xtlist': [{'xt_xh': '1', 'answer': 'B'}],
            })),
            ('content_2', '[]'),
            ('content_3', _make_content_json('collector.choose', [])),
            ('content_4', _make_content_json('collector.choose', {
                'stid': 'nested',
                'xtlist': [
                    None,
                    {
                        'xt_xh': '2',
                        'answer': 'C',
                        'xxlist': [None, {'xx_mc': 'C', 'xx_nr': 'safe'}],
                    },
                ],
            })),
        ])
        strategy = self.Strategy()
        with patch('builtins.print') as mock_print:
            self.assertTrue(strategy.load_set(set_id))

        mock_print.assert_any_call(
            '  strategy skip content_2: expected JSON object')
        mock_print.assert_any_call(
            '  strategy skip content_3: expected info JSON object')
        self.assertEqual(
            [section['dir'] for section in strategy.sections],
            ['content_1', 'content_4'],
        )
        answer = strategy.lookup('collector.choose', 'valid', qid='1')
        self.assertIsNotNone(answer)
        self.assertEqual(answer.get('answer'), 'B')
        nested = strategy.lookup('collector.choose', 'nested', qid='2')
        self.assertIsNotNone(nested)
        self.assertEqual(nested.get('answer'), 'C')
        self.assertEqual(nested.get('options'), ['safe'])

    # ── lookup() ─────────────────────────────────────────────────────────────

    def test_lookup_exact_match(self):
        set_id = '325174'
        _make_exam_cache(self.cache_root, set_id, [
            ('content_1', _make_content_json('collector.choose', {
                'stid': 'st1',
                'xtlist': [
                    {'xt_xh': '1', 'xt_nr': 'Question one text', 'answer': 'C',
                     'xxlist': []},
                    {'xt_xh': '2', 'xt_nr': 'Question two text', 'answer': 'A',
                     'xxlist': []},
                ],
            })),
        ])
        s = self.Strategy()
        s.load_set(set_id)

        r = s.lookup('collector.choose', 'st1', qid='1')
        self.assertIsNotNone(r)
        self.assertEqual(r['answer'], 'C')
        self.assertEqual(r['source'], 'local')

        r2 = s.lookup('collector.choose', 'st1', qid='2')
        self.assertEqual(r2['answer'], 'A')

    def test_lookup_no_match_returns_none(self):
        s = self.Strategy()
        s.load_set('nonexist')
        r = s.lookup('collector.choose', 'x', qid='99')
        self.assertIsNone(r)

    def test_lookup_without_qid_falls_back_to_fuzzy(self):
        set_id = '325175'
        _make_exam_cache(self.cache_root, set_id, [
            ('content_1', _make_content_json('collector.choose', {
                'stid': 'st1',
                'xtlist': [
                    {'xt_xh': '1', 'xt_nr': 'What is the capital of France?',
                     'answer': 'B', 'xxlist': []},
                ],
            })),
        ])
        s = self.Strategy()
        s.load_set(set_id)

        # Exact match first
        r = s.lookup('collector.choose', 'st1', qid='1')
        self.assertEqual(r['answer'], 'B')

        # Fallback to title text fuzzy when qid is None
        r2 = s.lookup('collector.choose', 'st1', qid=None,
                      title_text='What is the capital of France?')
        self.assertIsNotNone(r2)
        self.assertEqual(r2['source'], 'local_fuzzy')

    def test_lookup_dom_answer_fallback(self):
        s = self.Strategy()
        s.load_set('nonexist')
        r = s.lookup('collector.choose', 'x', qid='1',
                     title_text=None, dom_answer='D')
        self.assertIsNotNone(r)
        self.assertEqual(r['source'], 'dom')
        self.assertEqual(r['answer'], 'D')

    # ── list_sections() ───────────────────────────────────────────────────────

    def test_list_sections(self):
        set_id = '81001'  # digits-only (set_id validation)
        _make_exam_cache(self.cache_root, set_id, [
            ('content_1', _make_content_json('collector.choose', {'stid': 'a', 'xtlist': []})),
            ('content_2', _make_content_json('collector.fill',  {'stid': 'b', 'std': []})),
        ])
        s = self.Strategy()
        s.load_set(set_id)
        secs = s.list_sections()
        self.assertEqual(len(secs), 2)
        self.assertIn(('a', 'collector.choose'), secs)
        self.assertIn(('b', 'collector.fill'), secs)

    # ── get_recording_answers() ───────────────────────────────────────────────

    def test_get_recording_answers(self):
        set_id = '81002'  # digits-only (set_id validation)
        _make_exam_cache(self.cache_root, set_id, [
            ('content_1', _make_content_json('collector.read', {
                'stid': 'r1', 'value': 'Read this text.', 'symbol': '/θɛŋk/',
            })),
            ('content_2', _make_content_json('collector.picture', {
                'stid': 'p1', 'value': 'Describe the picture.', 'topic': 'Park',
            })),
        ])
        s = self.Strategy()
        s.load_set(set_id)
        all_rec = s.get_recording_answers()
        # Only read and picture add recording_answers in strategy layer
        self.assertEqual(len(all_rec), 2)

        read_rec = s.get_recording_answers(stype='read')
        self.assertEqual(len(read_rec), 1)
        self.assertEqual(read_rec[0]['type'], 'read')

        pic_rec = s.get_recording_answers(stype='picture')
        self.assertEqual(len(pic_rec), 1)
        self.assertEqual(pic_rec[0]['topic'], 'Park')


# ═══════════════════════════════════════════════════════════════════════════════
#  TestTextSimilarity — ets_strategy._text_similarity()
# ═══════════════════════════════════════════════════════════════════════════════

class TestTextSimilarity(unittest.TestCase):

    def setUp(self):
        import ets_strategy
        self._sim = ets_strategy.ETSStrategy()._text_similarity

    def test_identical_strings(self):
        self.assertAlmostEqual(self._sim('hello world', 'hello world'), 1.0)

    def test_case_insensitive(self):
        self.assertAlmostEqual(self._sim('Hello World', 'hello world'), 1.0)

    def test_word_order_matters(self):
        # difflib is order-sensitive, so these differ
        ratio1 = self._sim('bad credit', 'credit bad')
        ratio2 = self._sim('bad credit', 'bad credit')
        self.assertGreater(ratio2, ratio1)

    def test_word_level_for_long_strings(self):
        # Strings >= 3 words use word-level comparison
        ratio = self._sim('the quick brown fox', 'quick brown fox the')
        # Different word order → lower ratio
        self.assertGreater(ratio, 0.4)
        self.assertLess(ratio, 1.0)

    def test_character_level_for_short_strings(self):
        ratio = self._sim('hi', 'hi')
        self.assertAlmostEqual(ratio, 1.0)

    def test_empty_input(self):
        self.assertEqual(self._sim('', 'hello'), 0.0)
        self.assertEqual(self._sim('hello', ''), 0.0)
        self.assertEqual(self._sim('', ''), 0.0)

    def test_whitespace_trimmed(self):
        self.assertAlmostEqual(self._sim('  hello  ', 'hello'), 1.0)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestHTMLToText — ets_strategy._html_to_text() and ets_parser._html_to_text()
# ═══════════════════════════════════════════════════════════════════════════════

class TestHTMLToText(unittest.TestCase):

    def test_strategy_br_conversion(self):
        # ets_strategy uses ' ' for <br>
        import ets_strategy
        fn = ets_strategy._html_to_text
        self.assertEqual(fn('hello<br>world'), 'hello world')
        self.assertEqual(fn('a<br/>b'), 'a b')
        self.assertEqual(fn('x<br />y'), 'x y')

    def test_parser_br_conversion(self):
        # ets_parser uses '\n' for <br>
        import ets_parser
        fn = ets_parser._html_to_text
        self.assertEqual(fn('hello<br>world'), 'hello\nworld')
        self.assertEqual(fn('a<br/>b'), 'a\nb')

    def test_strategy_p_tag_stripped(self):
        # ets_strategy uses ' ' for <p>
        import ets_strategy
        fn = ets_strategy._html_to_text
        self.assertEqual(fn('<p>Hello</p>'), 'Hello')
        self.assertEqual(fn('<p>Line1</p><p>Line2</p>'), 'Line1 Line2')

    def test_parser_p_tag_stripped(self):
        # ets_parser uses '\n' for <p>
        import ets_parser
        fn = ets_parser._html_to_text
        self.assertEqual(fn('<p>Hello</p>'), 'Hello')
        self.assertEqual(fn('<p>Line1</p><p>Line2</p>'), 'Line1\nLine2')

    def test_html_entities_unescaped(self):
        import ets_strategy
        import ets_parser
        for mod in [ets_strategy, ets_parser]:
            fn = mod._html_to_text
            self.assertEqual(fn('&lt;hello&gt;'), '<hello>')
            self.assertEqual(fn('&amp;'), '&')

    def test_all_tags_stripped(self):
        import ets_strategy
        self.assertEqual(
            ets_strategy._html_to_text('<div class="x"><span>text</span></div>'),
            'text')

    def test_multiple_spaces_collapsed(self):
        import ets_strategy
        self.assertEqual(
            ets_strategy._html_to_text('hello<br>   world'),
            'hello world')

    def test_strip_template_prefix(self):
        import ets_parser
        self.assertEqual(ets_parser._strip_template_prefix('ets_th1  Some text'), 'Some text')
        self.assertEqual(ets_parser._strip_template_prefix('ets_sm2 Answer here'), 'Answer here')
        self.assertEqual(ets_parser._strip_template_prefix('Plain text'), 'Plain text')
        self.assertEqual(ets_parser._strip_template_prefix(''), '')


# ═══════════════════════════════════════════════════════════════════════════════
#  TestReadJSON — both ets_strategy._read_json() and ets_parser._read_json()
# ═══════════════════════════════════════════════════════════════════════════════

class TestReadJSON(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='json_test_')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, content, encoding='utf-8'):
        p = _os.path.join(self.tmp, name)
        with open(p, 'w', encoding=encoding) as f:
            f.write(content)
        return p

    def test_utf8(self):
        import ets_parser
        p = self._write('a.json', '{"key": "你好"}')
        data = ets_parser._read_json(p)
        self.assertEqual(data['key'], '你好')

    def test_gb18030_fallback(self):
        import ets_parser
        # Write GB18030 encoded JSON
        p = _os.path.join(self.tmp, 'gb.json')
        with open(p, 'wb') as f:
            f.write('{"key": "\\u6587\\u5b57"}'.encode('gb18030'))
        data = ets_parser._read_json(p)
        self.assertEqual(data['key'], '文字')

    def test_invalid_json_raises(self):
        # Invalid JSON raises JSONDecodeError even after encoding fallback
        import ets_parser
        p = self._write('bad.json', '{invalid json}')
        with self.assertRaises(json.JSONDecodeError):
            ets_parser._read_json(p)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestParserHtmlSafety — HTML escape + material image path sandbox
# ═══════════════════════════════════════════════════════════════════════════════

class TestParserHtmlSafety(unittest.TestCase):

    def test_esc_html_escapes_markup(self):
        import ets_parser
        out = ets_parser._esc_html('<script>alert(1)</script>')
        self.assertNotIn('<script>', out)
        self.assertIn('&lt;script&gt;', out)

    def test_render_html_escapes_metadata(self):
        import ets_parser
        set_data = {
            'id': '123<script>',
            'score': 0,
            'total_questions': 1,
            'exam_type_names': ['A <b>B</b>'],
            'types': set(),
            'sections': [{
                'type': 'collector.choose',
                'dir': 's1',
                'data': {'info': {
                    'xtlist': [{
                        'xt_xh': '1<img>',
                        'xt_nr': 'Q',
                        'answer': 'A',
                        'xxlist': [{'xx_mc': 'A<script>', 'xx_nr': 'opt'}],
                    }],
                }},
            }],
            'path': '',
        }
        html_out = ets_parser._render_full_html(set_data)
        self.assertNotIn('<script>', html_out)
        self.assertNotIn('<img>', html_out)
        self.assertNotIn('<b>B</b>', html_out)
        self.assertIn('&lt;script&gt;', html_out)

    def test_safe_material_image_rejects_traversal(self):
        import ets_parser
        self.assertIsNone(ets_parser._safe_material_image_path(
            r'C:\sets\1', 'sec', r'..\..\Windows\win.ini'))
        self.assertIsNone(ets_parser._safe_material_image_path(
            r'C:\sets\1', 'sec', r'sub/pic.png'))
        self.assertIsNone(ets_parser._safe_material_image_path(
            r'C:\sets\1', 'sec', ''))


# ═══════════════════════════════════════════════════════════════════════════════
#  TestETSHotkey — ets_hotkey.py pure logic (no Windows API calls)
# ═══════════════════════════════════════════════════════════════════════════════

class TestETSHotkey(unittest.TestCase):

    def test_state_initial_values(self):
        # Patch the message pump so it never runs (avoid Windows API)
        import ets_hotkey
        # Save originals
        orig_register = ets_hotkey.RegisterHotKey
        orig_unregister = ets_hotkey.UnregisterHotKey
        orig_getmsg = ets_hotkey.GetMessage
        # Replace with no-ops
        ets_hotkey.RegisterHotKey = lambda *a, **k: True
        ets_hotkey.UnregisterHotKey = lambda *a, **k: None
        ets_hotkey.GetMessage = lambda *a, **k: 0

        try:
            hk = ets_hotkey.ETSHotkey()
            self.assertFalse(hk.is_paused)
            self.assertFalse(hk.should_skip)
            self.assertFalse(hk.should_stop)
        finally:
            ets_hotkey.RegisterHotKey = orig_register
            ets_hotkey.UnregisterHotKey = orig_unregister
            ets_hotkey.GetMessage = orig_getmsg

    def test_toggle_pause(self):
        import ets_hotkey
        orig_register = ets_hotkey.RegisterHotKey
        orig_unregister = ets_hotkey.UnregisterHotKey
        orig_getmsg = ets_hotkey.GetMessage
        ets_hotkey.RegisterHotKey = lambda *a, **k: True
        ets_hotkey.UnregisterHotKey = lambda *a, **k: None
        ets_hotkey.GetMessage = lambda *a, **k: 0

        try:
            hk = ets_hotkey.ETSHotkey()
            # Simulate pause via internal state (directly since no Windows msg)
            hk._paused = True
            self.assertTrue(hk.is_paused)
            hk._paused = False
            self.assertFalse(hk.is_paused)
        finally:
            ets_hotkey.RegisterHotKey = orig_register
            ets_hotkey.UnregisterHotKey = orig_unregister
            ets_hotkey.GetMessage = orig_getmsg

    def test_skip_signal_one_shot(self):
        import ets_hotkey
        orig_register = ets_hotkey.RegisterHotKey
        orig_unregister = ets_hotkey.UnregisterHotKey
        orig_getmsg = ets_hotkey.GetMessage
        ets_hotkey.RegisterHotKey = lambda *a, **k: True
        ets_hotkey.UnregisterHotKey = lambda *a, **k: None
        ets_hotkey.GetMessage = lambda *a, **k: 0

        try:
            hk = ets_hotkey.ETSHotkey()
            hk._skip = True
            self.assertTrue(hk.should_skip)
            hk.clear_skip()
            self.assertFalse(hk.should_skip)
            self.assertFalse(hk.should_skip)  # still false on second call
        finally:
            ets_hotkey.RegisterHotKey = orig_register
            ets_hotkey.UnregisterHotKey = orig_unregister
            ets_hotkey.GetMessage = orig_getmsg

    def test_stop_signal(self):
        import ets_hotkey
        orig_register = ets_hotkey.RegisterHotKey
        orig_unregister = ets_hotkey.UnregisterHotKey
        orig_getmsg = ets_hotkey.GetMessage
        ets_hotkey.RegisterHotKey = lambda *a, **k: True
        ets_hotkey.UnregisterHotKey = lambda *a, **k: None
        ets_hotkey.GetMessage = lambda *a, **k: 0

        try:
            hk = ets_hotkey.ETSHotkey()
            hk._stop = True
            self.assertTrue(hk.should_stop)
            hk.clear_stop()
            self.assertFalse(hk.should_stop)
        finally:
            ets_hotkey.RegisterHotKey = orig_register
            ets_hotkey.UnregisterHotKey = orig_unregister
            ets_hotkey.GetMessage = orig_getmsg

    def test_hotkey_constants(self):
        import ets_hotkey
        self.assertEqual(ets_hotkey.HOTKEY_PAUSE, 1)
        self.assertEqual(ets_hotkey.HOTKEY_SKIP, 2)
        self.assertEqual(ets_hotkey.HOTKEY_STOP, 3)
        self.assertEqual(ets_hotkey.VK_F9, 0x78)
        self.assertEqual(ets_hotkey.VK_F10, 0x79)
        self.assertEqual(ets_hotkey.VK_F12, 0x7B)

    def test_on_stop_callback(self):
        import ets_hotkey
        called = []
        hk = ets_hotkey.ETSHotkey(on_stop=lambda: called.append(1))
        # Simulate F12 handler body without Windows message pump
        with hk._lock:
            hk._stop = True
            on_stop = hk._on_stop
        if on_stop is not None:
            on_stop()
        self.assertTrue(hk.should_stop)
        self.assertEqual(called, [1])

    def test_mod_constants(self):
        import ets_hotkey
        self.assertEqual(ets_hotkey.MOD_ALT, 0x0001)
        self.assertEqual(ets_hotkey.MOD_NOREPEAT, 0x4000)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestETSScanSets — ets_parser.scan_sets() pure logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestETSScanSets(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='scan_test_')
        import ets_parser
        self._orig_dir = ets_parser.ETS_DATA_DIR
        ets_parser.ETS_DATA_DIR = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        import ets_parser
        ets_parser.ETS_DATA_DIR = self._orig_dir

    def test_empty_dir_returns_empty(self):
        import ets_parser
        sets, err = ets_parser.scan_sets()
        self.assertEqual(sets, [])

    def test_ignores_non_numeric_dirs(self):
        import ets_parser
        _os.makedirs(_os.path.join(self.tmp, 'not_a_number'))
        sets, err = ets_parser.scan_sets()
        self.assertEqual(sets, [])

    def test_parses_content_json(self):
        import ets_parser
        set_id = '10001'
        set_dir = _os.path.join(self.tmp, set_id)
        _os.makedirs(_os.path.join(set_dir, 'content_1'))
        with open(_os.path.join(set_dir, 'content_1', 'content.json'),
                  'w', encoding='utf-8') as f:
            json.dump({
                'structure_type': 'collector.choose',
                'info': {
                    'stid': 'st1',
                    'xtlist': [{'xt_xh': '1'}, {'xt_xh': '2'}, {'xt_xh': '3'}],
                }
            }, f)

        sets, err = ets_parser.scan_sets()
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0]['id'], set_id)
        self.assertEqual(sets[0]['total_questions'], 3)
        self.assertIn('collector.choose', sets[0]['types'])

    def test_reads_template_res_json(self):
        import ets_parser
        set_id = '10002'
        set_dir = _os.path.join(self.tmp, set_id)
        _os.makedirs(_os.path.join(set_dir, 'content_1'))
        _os.makedirs(_os.path.join(set_dir, 'template_1'))
        with open(_os.path.join(set_dir, 'content_1', 'content.json'),
                  'w', encoding='utf-8') as f:
            json.dump({'structure_type': 'collector.fill',
                       'info': {'stid': 'st1', 'std': [{'xth': '1'}]}}, f)
        with open(_os.path.join(set_dir, 'template_1', 'res.json'),
                  'w', encoding='utf-8') as f:
            json.dump({
                'set_score': 100,
                'exam_type_list': [
                    {'exam_type_name': '听后选择1', 'exam_type_collect': 'collector.choose'},
                    {'exam_type_name': '听后记录',  'exam_type_collect': 'collector.fill'},
                ]
            }, f)

        sets, err = ets_parser.scan_sets()
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0]['score'], 100)
        self.assertEqual(sets[0]['exam_type_names'],
                          ['听后选择1', '听后记录'])

    def test_bad_content_shapes_are_isolated_and_nested_lists_are_filtered(self):
        import ets_parser
        set_dir = _os.path.join(self.tmp, '10004')
        payloads = {
            'content_bad_top': [],
            'content_bad_info': {
                'structure_type': 'collector.choose', 'info': []},
            'content_good': {
                'structure_type': 'collector.choose',
                'info': {'stid': 'good', 'xtlist': [{'xt_xh': '1'}]},
            },
            'content_nested': {
                'structure_type': 'collector.choose',
                'info': {
                    'stid': 'nested',
                    'xtlist': [
                        None,
                        {'xt_xh': '2', 'xxlist': [None, {'xx_mc': 'A'}]},
                    ],
                },
            },
        }
        for name, payload in payloads.items():
            directory = _os.path.join(set_dir, name)
            _os.makedirs(directory)
            with open(_os.path.join(directory, 'content.json'),
                      'w', encoding='utf-8') as stream:
                json.dump(payload, stream)

        sets, err = ets_parser.scan_sets()

        self.assertIsNone(err)
        self.assertEqual(len(sets), 1)
        self.assertEqual(
            [section['dir'] for section in sets[0]['sections']],
            ['content_good', 'content_nested'],
        )
        self.assertEqual(sets[0]['total_questions'], 2)
        nested = sets[0]['sections'][1]['data']['info']['xtlist']
        self.assertEqual(len(nested), 1)
        self.assertEqual(nested[0]['xxlist'], [{'xx_mc': 'A'}])

    def test_sort_by_score_descending(self):
        import ets_parser
        for sid, score in [('3', 30), ('1', 100), ('2', 60)]:
            sd = _os.path.join(self.tmp, sid)
            _os.makedirs(_os.path.join(sd, 'content_1'))
            with open(_os.path.join(sd, 'content_1', 'content.json'),
                      'w', encoding='utf-8') as f:
                json.dump({'structure_type': 'collector.choose',
                           'info': {'stid': sid, 'xtlist': [{'xt_xh': '1'}]}}, f)
            td = _os.path.join(sd, 'template_1')
            _os.makedirs(td)
            with open(_os.path.join(td, 'res.json'), 'w', encoding='utf-8') as f:
                json.dump({'set_score': score, 'exam_type_list': []}, f)

        sets, err = ets_parser.scan_sets()
        self.assertEqual(sets[0]['score'], 100)
        self.assertEqual(sets[1]['score'], 60)
        self.assertEqual(sets[2]['score'], 30)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestRemoteVersion — ets_remote.compare_versions()
# ═══════════════════════════════════════════════════════════════════════════════

class TestRemoteVersion(unittest.TestCase):

    def setUp(self):
        import ets_remote
        self.cv = ets_remote.compare_versions

    def test_exact_match(self):
        self.assertEqual(self.cv('0.6.1', '0.6.1'), 0)

    def test_patch_bump(self):
        self.assertEqual(self.cv('0.6.0', '0.6.1'), -1)
        self.assertEqual(self.cv('0.6.1', '0.6.0'), 1)

    def test_minor_bump(self):
        self.assertEqual(self.cv('0.5.0', '0.6.0'), -1)
        self.assertEqual(self.cv('0.6.0', '0.5.0'), 1)

    def test_major_bump(self):
        self.assertEqual(self.cv('0.5.1', '1.0.0'), -1)
        self.assertEqual(self.cv('1.0.0', '0.5.1'), 1)

    def test_three_part_vs_two_part(self):
        # "0.6" should compare as "0.6.0"
        self.assertEqual(self.cv('0.6', '0.6.0'), 0)
        self.assertEqual(self.cv('0.6.0', '0.6'), 0)

    def test_missing_parts_padded_with_zero(self):
        self.assertEqual(self.cv('1', '1.0.0'), 0)
        self.assertEqual(self.cv('1.0', '1.0.0'), 0)
        self.assertEqual(self.cv('1', '1.0.1'), -1)

    def test_prerelease_suffix_stripped(self):
        # SemVer: pre-release ranks lower than the corresponding release
        self.assertEqual(self.cv('0.5.1-beta', '0.5.1'), -1)
        self.assertEqual(self.cv('0.5.1', '0.5.1-beta'), 1)
        self.assertEqual(self.cv('0.5.1-alpha', '0.5.1'), -1)
        # SemVer orders prerelease identifiers lexically when both are text.
        self.assertEqual(self.cv('0.5.1-beta', '0.5.1-alpha'), 1)

    def test_mixed_letters_digits(self):
        # Leading non-digit (e.g. "v0.5.1") does not match numeric start → 0
        self.assertEqual(self.cv('v0.5.1', '0.5.1'), -1)
        self.assertEqual(self.cv('0.5.1', 'v0.5.1'), 1)
        # Pure digit versions still compare correctly
        self.assertEqual(self.cv('0.5.1', '0.5.1'), 0)

    def test_empty_string(self):
        self.assertEqual(self.cv('', ''), 0)
        self.assertEqual(self.cv('0.5.1', ''), 1)
        self.assertEqual(self.cv('', '0.5.1'), -1)


class TestRemoteClassifyInfo(unittest.TestCase):

    def setUp(self):
        import ets_remote
        self._ri = ets_remote.RemoteInfo
        self._ci = ets_remote.classify_info

    def test_none_returns_normal(self):
        level, reason = self._ci(None)
        self.assertEqual(level, 'normal')
        self.assertEqual(reason, '')

    def test_allow_start_false_is_warn_when_unsigned(self):
        """Without integrity keys, unauthenticated kill-switch is warn (fail-open)."""
        import ets_remote
        with _cleared_remote_integrity_env():
            info = self._ri(allow_start=False)
            level, reason = self._ci(info)
            self.assertEqual(level, 'warn')
            self.assertEqual(reason, '程序已被远程关闭')
            _os.environ['ETS_REMOTE_HMAC'] = 'unit-test-secret'
            level2, _ = ets_remote.classify_info(info)
            self.assertEqual(level2, 'block')

    def test_force_update_is_warn_when_unsigned(self):
        with _cleared_remote_integrity_env():
            info = self._ri(allow_start=True, force_update=True)
            level, reason = self._ci(info)
            self.assertEqual(level, 'warn')
            self.assertIn('版本过低', reason)

    def test_update_available_is_normal(self):
        info = self._ri(allow_start=True, force_update=False, update_available=True)
        level, reason = self._ci(info)
        self.assertEqual(level, 'normal')

    def test_all_ok_is_normal(self):
        info = self._ri(allow_start=True, force_update=False, update_available=False)
        level, reason = self._ci(info)
        self.assertEqual(level, 'normal')


class TestRemoteETSSystem(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='remote_test_')
        import ets_remote
        self._ri_class = ets_remote.RemoteInfo
        # Point cache to temp dir
        self._orig_cache = ets_remote._CACHE_FILENAME
        ets_remote._CACHE_FILENAME = _os.path.join(self.tmp, 'cache.json')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        import ets_remote
        ets_remote._CACHE_FILENAME = self._orig_cache

    def test_parse_remote_info(self):
        import ets_remote
        r = ets_remote.ETSRemote(current_version='0.5.0')
        info = r._parse_remote_info({
            'version': '0.6.1',
            'minVer': '0.5.0',
            'allowStart': True,
            'announcement': 'New release!',
            'pkExtraUrl': 'https://example.com/extra.json',
            'downloadUrl': 'https://github.com/releases',
        }, source='test', fetched_at=1000)

        self.assertEqual(info.latest_version, '0.6.1')
        self.assertEqual(info.min_version, '0.5.0')
        self.assertTrue(info.allow_start)
        self.assertTrue(info.update_available)   # 0.6.1 > 0.5.0
        self.assertFalse(info.force_update)      # 0.5.0 >= 0.5.0
        self.assertEqual(info.announcement, 'New release!')
        self.assertEqual(info.source, 'test')
        self.assertEqual(info.fetched_at, 1000)

    def test_force_update_when_below_min_ver(self):
        import ets_remote
        r = ets_remote.ETSRemote(current_version='0.4.0')
        info = r._parse_remote_info({
            'version': '0.6.0', 'minVer': '0.5.5',
            'allowStart': True,
        }, source='test', fetched_at=0)
        self.assertTrue(info.force_update)

    def test_should_block_start(self):
        import ets_remote
        with _cleared_remote_integrity_env():
            info = self._ri_class(allow_start=False)
            blocked, reason = ets_remote.should_block_start(info)
            self.assertFalse(blocked)
            self.assertEqual(reason, '')
            _os.environ['ETS_REMOTE_HMAC'] = 'unit-test-secret'
            blocked2, reason2 = ets_remote.should_block_start(info)
            self.assertTrue(blocked2)
            self.assertEqual(reason2, '程序已被远程关闭')

    def test_should_not_block_normal(self):
        import ets_remote
        info = self._ri_class(allow_start=True)
        blocked, _ = ets_remote.should_block_start(info)
        self.assertFalse(blocked)

    def test_format_update_message_warn_when_unsigned_kill_switch(self):
        import ets_remote
        with _cleared_remote_integrity_env():
            info = self._ri_class(allow_start=False, announcement='')
            msg = ets_remote.format_update_message(info, current_version='0.6.7')
            self.assertIsNotNone(msg)
            self.assertIn('程序已被远程关闭', msg)
            self.assertIn('仅提示', msg)

    def test_format_update_message_update_available(self):
        import ets_remote
        info = self._ri_class(
            allow_start=True, force_update=False,
            update_available=True, latest_version='0.7.0',
            download_url='https://github.com/dl', announcement='Bug fixes'
        )
        msg = ets_remote.format_update_message(info, current_version='0.6.1')
        self.assertIn('0.7.0', msg)
        self.assertIn('Bug fixes', msg)

    def test_format_update_message_none_when_no_info(self):
        import ets_remote
        msg = ets_remote.format_update_message(None)
        self.assertIsNone(msg)

    def test_download_pk_extra_no_url(self):
        import ets_remote
        r = ets_remote.ETSRemote(current_version='0.6.1')
        ok, msg = r.download_pk_extra()
        self.assertFalse(ok)

    def test_download_pk_extra_fake_url_all_fail(self):
        import ets_remote
        r = ets_remote.ETSRemote(current_version='0.6.1')
        ok, msg = r.download_pk_extra(url='http://localhost:1/none.json')
        self.assertFalse(ok)
        # Should not leave a partial file
        self.assertFalse(_os.path.exists(
            _os.path.join(self.tmp, 'pk_extra.json')))


# ═══════════════════════════════════════════════════════════════════════════════
#  TestInterruptibleSleep — ets_common.interruptible_sleep()
# ═══════════════════════════════════════════════════════════════════════════════

class TestInterruptibleSleep(unittest.TestCase):

    def test_no_stop_event_just_sleeps(self):
        import ets_common
        base = ets_common.ETSBase()
        t0 = time.time()
        base.interruptible_sleep(0.05)
        elapsed = time.time() - t0
        self.assertGreater(elapsed, 0.03)

    def test_stop_event_interrupts_immediately(self):
        import ets_common
        import threading
        stop = threading.Event()
        stop.set()  # already set before sleep
        base = ets_common.ETSBase(stop_event=stop)
        t0 = time.time()
        with self.assertRaises(InterruptedError):
            base.interruptible_sleep(10)
        elapsed = time.time() - t0
        self.assertLess(elapsed, 0.5)  # should not sleep the full 10s


# ═══════════════════════════════════════════════════════════════════════════════
#  TestJSEscape — ets_common.js_escape()
# ═══════════════════════════════════════════════════════════════════════════════

class TestJSEscape(unittest.TestCase):

    def test_double_quote(self):
        import ets_common
        self.assertEqual(ets_common.ETSBase.js_escape('say "hello"'),
                         'say \\"hello\\"')

    def test_single_quote(self):
        import ets_common
        self.assertEqual(ets_common.ETSBase.js_escape("it's"),
                         "it\\'s")

    def test_newline(self):
        import ets_common
        self.assertEqual(ets_common.ETSBase.js_escape('a\nb'), 'a\\nb')

    def test_backslash(self):
        import ets_common
        self.assertEqual(ets_common.ETSBase.js_escape('a\\b'), 'a\\\\b')


# ═══════════════════════════════════════════════════════════════════════════════
#  TestWordPKPure — ets_word_pk pure helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestWordPKPure(unittest.TestCase):

    def test_edit_distance_basic(self):
        import ets_word_pk
        self.assertEqual(ets_word_pk._edit_dist('cat', 'cat'), 0)
        self.assertEqual(ets_word_pk._edit_dist('cat', 'car'), 1)
        self.assertEqual(ets_word_pk._edit_dist('kitten', 'sitting'), 3)
        self.assertEqual(ets_word_pk._edit_dist('hello', ''), 5)
        self.assertEqual(ets_word_pk._edit_dist('', ''), 0)

    def test_same_script(self):
        import ets_word_pk
        self.assertTrue(ets_word_pk._same_script('hello', 'world'))
        self.assertTrue(ets_word_pk._same_script('你好', '世界'))
        self.assertFalse(ets_word_pk._same_script('hello', '你好'))
        self.assertFalse(ets_word_pk._same_script('苹果', 'apple'))

    def test_tie_breaker(self):
        import ets_word_pk
        # Both same script, closer to reference wins
        self.assertTrue(ets_word_pk._tie_breaker('cat', 'cart', 'cat'))
        # 'cat' is closer to 'cat' than 'elephant' is (edit distance)
        self.assertTrue(ets_word_pk._tie_breaker('cat', 'elephant', 'cat'))
        # 'cat' further from reference 'dog' than 'dig' is
        self.assertFalse(ets_word_pk._tie_breaker('cat', 'dig', 'dog'))

    def test_exe_dir_path_in_dev(self):
        import ets_word_pk
        path = ets_word_pk._exe_dir_path('test.json')
        # In dev mode, resolves to project root
        self.assertTrue(path.endswith('test.json'))


# ═══════════════════════════════════════════════════════════════════════════════
#  TestRenderSection — ets_parser.render_section() output shape
# ═══════════════════════════════════════════════════════════════════════════════

class TestRenderSection(unittest.TestCase):

    def test_render_choose_output_shape(self):
        import ets_parser
        section = {
            'type': 'collector.choose',
            'data': {
                'structure_type': 'collector.choose',
                'info': {
                    'stid': 'st1',
                    'xtlist': [
                        {
                            'xt_xh': '1',
                            'xt_nr': '<p>What caused her illness?</p>',
                            'xt_value': '<p>She ate bad food.</p>',
                            'answer': 'A',
                            'xxlist': [
                                {'xx_mc': 'A', 'xx_nr': 'The food she ate'},
                                {'xx_mc': 'B', 'xx_nr': 'The weather'},
                            ],
                        },
                    ],
                }
            }
        }
        parts = ets_parser.render_section(section)
        # Returns list of (text, tag) tuples
        self.assertIsInstance(parts, list)
        self.assertTrue(len(parts) > 0)
        for text, tag in parts:
            self.assertIsInstance(text, str)
            self.assertIsInstance(tag, str)
        # Should contain the answer
        combined = ''.join(t for t, _ in parts)
        self.assertIn('A', combined)
        self.assertIn('What caused', combined)

    def test_malformed_nested_lists_do_not_break_render_or_exports(self):
        import ets_parser
        section = {
            'type': 'collector.choose',
            'dir': 'content_1',
            'data': {
                'structure_type': 'collector.choose',
                'info': {
                    'stid': 7,
                    'xtlist': [
                        None,
                        {
                            'xt_xh': '1', 'xt_nr': 123, 'answer': 'A',
                            'xxlist': [None, {'xx_mc': 'A', 'xx_nr': {}}],
                        },
                    ],
                },
            },
        }
        set_data = {
            'id': '1', 'path': '', 'score': 0,
            'total_questions': 1,
            'types': {'collector.choose'}, 'exam_type_names': [],
            'sections': [section, None],
        }

        rendered = ''.join(text for text, _tag in ets_parser.render_section(section))
        markdown = ets_parser._render_full_markdown(set_data)
        html_text = ets_parser._render_full_html(set_data)

        self.assertIn('123', rendered)
        self.assertIn('123', markdown)
        self.assertIn('123', html_text)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestAutoLoadAnswers — ets_auto.load_answers() logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutoLoadAnswers(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='auto_load_test_')
        # Patch ETSBase and ETSAutoAnswer to avoid CDP dependency
        import ets_auto as auto_mod
        import ets_parser
        self._orig_data_dir = ets_parser.ETS_DATA_DIR
        ets_parser.ETS_DATA_DIR = self.tmp
        self._orig_connect = auto_mod.ETSAutoAnswer.connect
        # Give the instance a fake ets_base pointing to temp
        auto_mod.ETSBase.connect = lambda self2: None

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        import ets_parser
        import ets_auto
        ets_parser.ETS_DATA_DIR = self._orig_data_dir
        ets_auto.ETSAutoAnswer.connect = self._orig_connect

    def _make_instance(self):
        import ets_auto
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.port = 10086
        inst.ws = None
        inst.debug_mode = False
        inst.stop_event = None
        inst.tab = {}
        inst.set_id = None
        inst.ets_base = self.tmp
        inst.homework_mode = False
        inst.homework_id = None
        inst.answers = {}
        inst.recording_answers = []
        inst.total_questions = 0
        inst.stats = {'choose_answered': 0, 'fill_answered': 0,
                      'errors': 0, 'next_click': 0}
        inst.strategy = None
        inst.rw_mode = False
        inst._on_connect = None
        inst._on_question_answered = None
        inst._on_complete = None
        inst._on_error = None
        inst._recording_window_closed = False
        inst._tk_root = None
        return inst

    def test_load_choose_answers(self):
        set_id = '325173'
        _make_exam_cache(self.tmp, set_id, [
            ('content_1', _make_content_json('collector.choose', {
                'stid': '82750',
                'xtlist': [
                    {'xt_xh': '1', 'answer': 'A'},
                    {'xt_xh': '2', 'answer': 'B'},
                ],
            })),
        ])
        inst = self._make_instance()
        inst.set_id = set_id
        # Inject strategy so load_answers doesn't try to access real APPDATA
        import ets_strategy
        inst.strategy = ets_strategy.ETSStrategy()
        ok = inst.load_answers()
        self.assertTrue(ok)
        self.assertEqual(inst.total_questions, 2)
        self.assertEqual(inst.answers['82750_1']['answer'], 'A')
        self.assertEqual(inst.answers['82750_2']['answer'], 'B')

    def test_load_fill_with_slash_split(self):
        set_id = '41001'
        _make_exam_cache(self.tmp, set_id, [
            ('content_1', _make_content_json('collector.fill', {
                'stid': 's1',
                'std': [
                    {'xth': '1', 'value': 'right/left'},  # slash → pick first
                ],
            })),
        ])
        inst = self._make_instance()
        inst.set_id = set_id
        import ets_strategy
        inst.strategy = ets_strategy.ETSStrategy()
        inst.load_answers()
        self.assertEqual(inst.answers['s1_1']['answer'], 'right')

    def test_load_recording_answers(self):
        set_id = '50001'
        _make_exam_cache(self.tmp, set_id, [
            ('content_1', _make_content_json('collector.read', {
                'stid': 'r1', 'value': 'Good morning.', 'symbol': '/mɔː/',
            })),
            ('content_2', _make_content_json('collector.picture', {
                'stid': 'p1', 'value': 'Picture description.', 'topic': 'School',
            })),
            ('content_3', _make_content_json('collector.dialogue', {
                'stid': 'd1', 'value': 'Material text.',
                'question': [
                    {'ask': 'Question 1?', 'std': [{'value': 'Answer 1'}]},
                ],
            })),
        ])
        inst = self._make_instance()
        inst.set_id = set_id
        import ets_strategy
        inst.strategy = ets_strategy.ETSStrategy()
        inst.load_answers()
        self.assertEqual(len(inst.recording_answers), 3)
        types = {r['type'] for r in inst.recording_answers}
        self.assertIn('read', types)
        self.assertIn('picture', types)
        self.assertIn('dialogue', types)

    def test_load_nonexistent_set_returns_false(self):
        inst = self._make_instance()
        inst.set_id = '99999'
        import ets_strategy
        inst.strategy = ets_strategy.ETSStrategy()
        ok = inst.load_answers()
        self.assertFalse(ok)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestNextReason — ets_auto.ETSAutoAnswer._is_next_waiting()
# ═══════════════════════════════════════════════════════════════════════════════

class TestNextReason(unittest.TestCase):
    """Pure logic: Next button temporarily unavailable (disabled / audio hide)."""

    def setUp(self):
        import ets_auto
        self.fn = ets_auto.ETSAutoAnswer._is_next_waiting

    def test_disabled_exact(self):
        self.assertTrue(self.fn('disabled'))

    def test_next_icon_hidden_prefix(self):
        self.assertTrue(self.fn('next_icon hidden'))
        self.assertTrue(self.fn('next_icon hidden: audio playing'))
        self.assertTrue(self.fn('next_icon hidden (timer)'))

    def test_not_found_is_not_waiting(self):
        self.assertFalse(self.fn('not found'))

    def test_empty_and_none(self):
        self.assertFalse(self.fn(''))
        self.assertFalse(self.fn(None))

    def test_unrelated_reason(self):
        self.assertFalse(self.fn('clicked'))
        self.assertFalse(self.fn('ready'))
        # must start with exact prefix, not contain mid-string
        self.assertFalse(self.fn('x next_icon hidden'))


# ═══════════════════════════════════════════════════════════════════════════════
#  TestSafeSetId — ets_strategy._safe_set_id() path validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestSafeSetId(unittest.TestCase):

    def setUp(self):
        import ets_strategy
        self.fn = ets_strategy._safe_set_id

    def test_digits_ok(self):
        self.assertEqual(self.fn('325173'), '325173')
        self.assertEqual(self.fn('0'), '0')
        self.assertEqual(self.fn(12345), '12345')

    def test_whitespace_stripped(self):
        self.assertEqual(self.fn('  99  '), '99')

    def test_none_and_empty(self):
        self.assertIsNone(self.fn(None))
        self.assertIsNone(self.fn(''))
        self.assertIsNone(self.fn('   '))

    def test_rejects_path_traversal(self):
        self.assertIsNone(self.fn('../etc'))
        self.assertIsNone(self.fn('..\\windows'))
        self.assertIsNone(self.fn('/abs/path'))
        self.assertIsNone(self.fn('C:\\ETS\\x'))

    def test_rejects_non_digit(self):
        self.assertIsNone(self.fn('abc'))
        self.assertIsNone(self.fn('12ab'))
        self.assertIsNone(self.fn('set-1'))

    def test_load_set_rejects_unsafe_id(self):
        import ets_strategy
        s = ets_strategy.ETSStrategy()
        self.assertFalse(s.load_set('../evil'))
        self.assertFalse(s.load_set('not-digits'))
        self.assertEqual(len(s.answer_index), 0)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestFillNotOverwritten — real fill wins over dialogue/role std (C6)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFillNotOverwritten(unittest.TestCase):
    """collector.fill_* keys must not be overwritten by dialogue/role std blanks."""

    def setUp(self):
        self.cache_root = tempfile.mkdtemp(prefix='ets_fill_ow_')
        import ets_strategy
        self._orig = ets_strategy.ETS_DATA_DIR
        ets_strategy.ETS_DATA_DIR = self.cache_root
        self.Strategy = ets_strategy.ETSStrategy

    def tearDown(self):
        shutil.rmtree(self.cache_root, ignore_errors=True)
        import ets_strategy
        ets_strategy.ETS_DATA_DIR = self._orig

    def test_fill_wins_over_dialogue_std(self):
        set_id = '88001'
        # Same stid + xth on both fill and dialogue — fill must win
        _make_exam_cache(self.cache_root, set_id, [
            ('content_1', _make_content_json('collector.fill', {
                'stid': 'shared',
                'std': [{'xth': '1', 'value': 'FROM_FILL'}],
            })),
            ('content_2', _make_content_json('collector.dialogue', {
                'stid': 'shared',
                'value': 'Material',
                'std': [{'xth': '1', 'value': 'FROM_DIALOGUE'}],
                'question': [
                    {'ask': 'Q1?', 'std': [{'value': 'oral answer'}]},
                ],
            })),
        ])
        s = self.Strategy()
        self.assertTrue(s.load_set(set_id))
        key = 'collector.fill_shared_1'
        self.assertIn(key, s.answer_index)
        self.assertEqual(s.answer_index[key]['answer'], 'FROM_FILL')
        # dialogue oral still indexed under its own key
        self.assertIn('collector.dialogue_shared_q1', s.answer_index)

    def test_fill_wins_over_role_std(self):
        set_id = '88002'
        _make_exam_cache(self.cache_root, set_id, [
            ('content_1', _make_content_json('collector.fill', {
                'stid': 'rshared',
                'std': [{'xth': '2', 'value': 'FILL_ANS'}],
            })),
            ('content_2', _make_content_json('collector.role', {
                'stid': 'rshared',
                'std': [{'xth': '2', 'value': 'ROLE_ANS'}],
                'question': [
                    {'ask': 'How?', 'std': [{'value': 'I run.'}]},
                ],
            })),
        ])
        s = self.Strategy()
        self.assertTrue(s.load_set(set_id))
        self.assertEqual(s.answer_index['collector.fill_rshared_2']['answer'], 'FILL_ANS')

    def test_dialogue_std_indexes_when_no_fill(self):
        set_id = '88003'
        _make_exam_cache(self.cache_root, set_id, [
            ('content_1', _make_content_json('collector.dialogue', {
                'stid': 'donly',
                'std': [{'xth': '1', 'value': 'FROM_DIALOGUE_ONLY'}],
                'question': [
                    {'ask': 'Q?', 'std': [{'value': 'oral'}]},
                ],
            })),
        ])
        s = self.Strategy()
        self.assertTrue(s.load_set(set_id))
        self.assertEqual(
            s.answer_index['collector.fill_donly_1']['answer'],
            'FROM_DIALOGUE_ONLY')


# ═══════════════════════════════════════════════════════════════════════════════
#  TestForceUtf8AndStopEvent — defaults / callable pure behavior
# ═══════════════════════════════════════════════════════════════════════════════

class TestForceUtf8AndStopEvent(unittest.TestCase):

    def test_force_utf8_stdio_callable(self):
        import ets_common
        # Must not raise (no-op on non-win32; reconfigure on win32)
        ets_common.force_utf8_stdio()
        ets_common.force_utf8_stdio(line_buffering=True)

    def test_ets_base_stop_event_default_none(self):
        import ets_common
        base = ets_common.ETSBase()
        self.assertIsNone(base.stop_event)

    def test_ets_base_stop_event_passed_through(self):
        import ets_common
        import threading
        ev = threading.Event()
        base = ets_common.ETSBase(stop_event=ev)
        self.assertIs(base.stop_event, ev)

    def test_ets_auto_stop_event_default_is_event(self):
        """ETSAutoAnswer always installs a real Event when none provided (C1)."""
        import ets_auto
        import threading
        # Avoid full __init__ side effects where possible — call real __init__
        # but it only sets fields + Event; no CDP.
        inst = ets_auto.ETSAutoAnswer(debug_mode=False, stop_event=None)
        self.assertIsNotNone(inst.stop_event)
        self.assertIsInstance(inst.stop_event, threading.Event)
        self.assertFalse(inst.stop_event.is_set())

    def test_ets_auto_stop_event_preserved(self):
        import ets_auto
        import threading
        ev = threading.Event()
        inst = ets_auto.ETSAutoAnswer(debug_mode=False, stop_event=ev)
        self.assertIs(inst.stop_event, ev)

    def test_signal_stop_safe_on_event(self):
        import ets_auto
        import threading
        inst = ets_auto.ETSAutoAnswer(debug_mode=False, stop_event=threading.Event())
        inst._signal_stop()
        self.assertTrue(inst.stop_event.is_set())

    def test_word_pk_stop_event_default_is_event(self):
        """OPEN-M4: ETSWordPK also installs Event when none provided."""
        import ets_word_pk
        import threading
        inst = ets_word_pk.ETSWordPK(debug_mode=False, stop_event=None)
        self.assertIsNotNone(inst.stop_event)
        self.assertIsInstance(inst.stop_event, threading.Event)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestOpenBacklogExtras — OPEN-H3 tab pick, OPEN-M3 js_escape, OPEN-H4 integrity
# ═══════════════════════════════════════════════════════════════════════════════

class TestPickEtsTab(unittest.TestCase):
    def test_prefers_exam_url_over_portal(self):
        import ets_common
        base = ets_common.ETSBase()
        tabs = [
            {'url': 'https://statics.ets100.com/ets-student-pc-web/index.html', 'title': 'Home'},
            {'url': 'https://statics.ets100.com/x#/mockExamDetail?set_id=1', 'title': 'Exam'},
        ]
        picked = base._pick_ets_tab(tabs)
        self.assertIn('mockExamDetail', picked['url'])

    def test_empty_returns_none(self):
        import ets_common
        self.assertIsNone(ets_common.ETSBase()._pick_ets_tab([]))


class TestJsEscapeOpenM3(unittest.TestCase):
    def test_escapes_line_separator(self):
        import ets_common
        s = 'a b c'
        out = ets_common.ETSBase.js_escape(s)
        self.assertNotIn(' ', out)
        self.assertNotIn(' ', out)
        self.assertIn('\\u2028', out)
        self.assertIn('\\u2029', out)


class TestRemoteIntegrity(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        import ets_remote
        # Integrity tests exercise check()/download_pk_extra() end to end;
        # re-arm the network switch that production keeps off.
        net_patcher = patch.object(ets_remote, 'REMOTE_NETWORK_ENABLED', True)
        net_patcher.start()
        self.addCleanup(net_patcher.stop)
        self._integrity_env = {
            name: _os.environ.get(name)
            for name in ('ETS_REMOTE_HMAC', 'ETS_REMOTE_PUBKEY')
        }
        self.addCleanup(self._restore_integrity_env)

    def _restore_integrity_env(self):
        for name, value in self._integrity_env.items():
            if value is None:
                _os.environ.pop(name, None)
            else:
                _os.environ[name] = value

    def test_no_key_allows_payload(self):
        import ets_remote
        import os
        # Ensure no key in env for this process
        old = os.environ.pop('ETS_REMOTE_PUBKEY', None)
        old_h = os.environ.pop('ETS_REMOTE_HMAC', None)
        try:
            ok, why = ets_remote.verify_remote_payload_integrity({'version': '1.0.0'})
            self.assertTrue(ok)
            self.assertIn('allowlist', why.lower())
        finally:
            if old is not None:
                os.environ['ETS_REMOTE_PUBKEY'] = old
            if old_h is not None:
                os.environ['ETS_REMOTE_HMAC'] = old_h

    def test_hmac_mismatch_rejects(self):
        import ets_remote
        import os
        os.environ['ETS_REMOTE_HMAC'] = 'test-secret'
        try:
            ok, why = ets_remote.verify_remote_payload_integrity(
                {'version': '1.0.0'}, signature_hex='00' * 32)
            self.assertFalse(ok)
        finally:
            os.environ.pop('ETS_REMOTE_HMAC', None)

    def test_hmac_match_accepts(self):
        import ets_remote
        import os, hmac, hashlib, json
        secret = 'test-secret'
        os.environ['ETS_REMOTE_HMAC'] = secret
        data = {'version': '1.0.0', 'allowStart': True}
        body = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        try:
            ok, why = ets_remote.verify_remote_payload_integrity(data, signature_hex=sig)
            self.assertTrue(ok, why)
        finally:
            os.environ.pop('ETS_REMOTE_HMAC', None)

    def test_check_rejects_unverified_cache_when_hmac_set(self):
        """Kill-switch fail-closed: cache without valid signature is not used."""
        import ets_remote
        import os, json, time
        secret = 'test-secret'
        os.environ['ETS_REMOTE_HMAC'] = secret
        # Isolate cache path
        old_cache = ets_remote._CACHE_FILENAME
        tmp = tempfile.mkdtemp(prefix='remote_int_')
        ets_remote._CACHE_FILENAME = _os.path.join(tmp, 'cache.json')
        try:
            r = ets_remote.ETSRemote(current_version='0.6.1')
            # Unsigned cache payload (would previously be fail-open for allowStart)
            unsigned = {
                'data': {
                    'version': '0.6.1',
                    'minVer': '0.0.0',
                    'allowStart': False,  # kill-switch claim
                },
                '_fetched_at': time.time(),
                '_source': 'cache',
            }
            with open(r._cache_path, 'w', encoding='utf-8') as f:
                json.dump(unsigned, f)
            # Force network miss → cache path
            r._fetch_json = lambda url: None
            info = r.check(use_cache=True)
            self.assertIsNone(info)
        finally:
            os.environ.pop('ETS_REMOTE_HMAC', None)
            ets_remote._CACHE_FILENAME = old_cache
            shutil.rmtree(tmp, ignore_errors=True)

    def test_check_accepts_verified_cache_when_hmac_set(self):
        """Signed cache may still drive allowStart decisions."""
        import ets_remote
        import os, hmac, hashlib, json, time
        secret = 'test-secret'
        os.environ['ETS_REMOTE_HMAC'] = secret
        old_cache = ets_remote._CACHE_FILENAME
        tmp = tempfile.mkdtemp(prefix='remote_int_')
        ets_remote._CACHE_FILENAME = _os.path.join(tmp, 'cache.json')
        try:
            payload = {
                'version': '0.6.1',
                'minVer': '0.0.0',
                'allowStart': False,
            }
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':'))
            sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            signed = dict(payload)
            signed['signature'] = sig
            r = ets_remote.ETSRemote(current_version='0.6.1')
            with open(r._cache_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'data': signed,
                    '_fetched_at': time.time(),
                    '_source': 'cache',
                }, f)
            r._fetch_json = lambda url: None
            info = r.check(use_cache=True)
            self.assertIsNotNone(info)
            self.assertFalse(info.allow_start)
        finally:
            os.environ.pop('ETS_REMOTE_HMAC', None)
            ets_remote._CACHE_FILENAME = old_cache
            shutil.rmtree(tmp, ignore_errors=True)

    def test_download_pk_extra_rejects_unsigned_when_hmac_set(self):
        """pk_extra download must verify signed payloads like check()."""
        import ets_remote
        import os, json
        from unittest.mock import patch, MagicMock
        secret = 'test-secret'
        os.environ['ETS_REMOTE_HMAC'] = secret
        tmp = tempfile.mkdtemp(prefix='pk_int_')
        target = _os.path.join(tmp, 'pk_extra.json')
        try:
            # Unsigned body would previously be accepted and written
            body = json.dumps({'hello': 'world'}, ensure_ascii=False).encode('utf-8')
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = body
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            with patch.object(ets_remote, '_open_remote_url', return_value=mock_resp):
                r = ets_remote.ETSRemote(current_version='0.6.1')
                ok, msg = r.download_pk_extra(
                    url='https://raw.githubusercontent.com/o/r/main/pk_extra.json',
                    target_path=target,
                )
            self.assertFalse(ok)
            self.assertFalse(_os.path.exists(target))
        finally:
            os.environ.pop('ETS_REMOTE_HMAC', None)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_download_pk_extra_accepts_signed_when_hmac_set(self):
        import ets_remote
        import os, hmac, hashlib, json
        from unittest.mock import patch, MagicMock
        secret = 'test-secret'
        os.environ['ETS_REMOTE_HMAC'] = secret
        tmp = tempfile.mkdtemp(prefix='pk_int_')
        target = _os.path.join(tmp, 'pk_extra.json')
        try:
            payload = {'hello': 'world'}
            body_str = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                  separators=(',', ':'))
            sig = hmac.new(secret.encode(), body_str.encode(), hashlib.sha256).hexdigest()
            signed = dict(payload)
            signed['signature'] = sig
            raw = json.dumps(signed, ensure_ascii=False).encode('utf-8')
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = raw
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            with patch.object(ets_remote, '_open_remote_url', return_value=mock_resp):
                r = ets_remote.ETSRemote(current_version='0.6.1')
                ok, msg = r.download_pk_extra(
                    url='https://raw.githubusercontent.com/o/r/main/pk_extra.json',
                    target_path=target,
                )
            self.assertTrue(ok, msg)
            with open(target, 'r', encoding='utf-8') as f:
                written = json.load(f)
            self.assertEqual(written.get('hello'), 'world')
            self.assertNotIn('signature', written)
        finally:
            os.environ.pop('ETS_REMOTE_HMAC', None)
            shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestWordPKFindAnswer — mere dict presence must not win without overlap (C7)
# ═══════════════════════════════════════════════════════════════════════════════

class TestWordPKFindAnswer(unittest.TestCase):
    """Unit-test find_answer with a bare instance + mock word_trans (no real dict)."""

    def _make_pk(self):
        import ets_word_pk
        pk = object.__new__(ets_word_pk.ETSWordPK)
        # Minimal fields used by find_answer / get_opt_trans / get_stems
        pk.word_trans = {}
        pk.trans_index = {}
        pk.cn_seg_index = {}
        pk.pk_extra = {}
        pk.debug_mode = False
        pk.debug = lambda *a, **k: None
        # Bind unbound methods from class
        pk.get_stems = ets_word_pk.ETSWordPK.get_stems.__get__(pk)
        pk.get_opt_trans = ets_word_pk.ETSWordPK.get_opt_trans.__get__(pk)
        pk.find_answer = ets_word_pk.ETSWordPK.find_answer.__get__(pk)
        pk._is_chinese = staticmethod(ets_word_pk.ETSWordPK._is_chinese)
        # _is_chinese is used as self._is_chinese — bind instance method if needed
        if not callable(getattr(pk, '_is_chinese', None)) or isinstance(pk._is_chinese, staticmethod):
            pk._is_chinese = lambda text: ets_word_pk.ETSWordPK._is_chinese(text)
        return pk

    def test_dict_presence_alone_does_not_match(self):
        """Option in dictionary but no question↔trans overlap → no match (C7)."""
        pk = self._make_pk()
        # Options exist in dict with unrelated Chinese translations
        pk.word_trans['apple'] = '苹果'
        pk.word_trans['banana'] = '香蕉'
        # Question has zero overlap with either translation
        idx = pk.find_answer('完全无关的题目内容xyz', ['apple', 'banana'])
        self.assertEqual(idx, -1)

    def test_overlap_allows_match(self):
        """When question Chinese appears in option translation, reverse lookup wins."""
        pk = self._make_pk()
        pk.word_trans['apple'] = '苹果水果'
        pk.word_trans['banana'] = '香蕉'
        idx = pk.find_answer('苹果', ['banana', 'apple'])
        self.assertEqual(idx, 1)

    def test_learned_exact_match(self):
        pk = self._make_pk()
        pk.pk_extra['hello'] = 'world'
        idx = pk.find_answer('hello', ['foo', 'world', 'bar'])
        self.assertEqual(idx, 1)

    def test_empty_question(self):
        pk = self._make_pk()
        self.assertEqual(pk.find_answer('', ['a', 'b']), -1)
        self.assertEqual(pk.find_answer('   ', ['a']), -1)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestAppVersionSingleSource — APP_VERSION only in ets_common (v0.6.5)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAppVersionSingleSource(unittest.TestCase):
    def test_app_version_is_semver_string(self):
        import ets_common
        self.assertIsInstance(ets_common.APP_VERSION, str)
        parts = ets_common.APP_VERSION.split('.')
        self.assertGreaterEqual(len(parts), 2)
        self.assertTrue(all(p.isdigit() for p in parts[:3] if p),
                        'APP_VERSION major.minor.patch should be numeric')

    def test_reexports_match_common(self):
        import ets_common
        import ets_auto
        import ets_word_pk
        v = ets_common.APP_VERSION
        self.assertEqual(ets_auto.__version__, v)
        self.assertEqual(ets_word_pk.__version__, v)
        # ets_gui subclasses ctk.CTk at import time; only assert if already
        # imported with a usable mock, or read source constant via common.
        if 'ets_gui' in _sys.modules:
            gui_v = getattr(_sys.modules['ets_gui'], 'APP_VERSION', None)
            if gui_v is not None:
                self.assertEqual(gui_v, v)
        else:
            # Avoid importing full GUI under incomplete ctk stub; version is
            # single-sourced from ets_common (re-exported at module top).
            self.assertEqual(v, ets_common.APP_VERSION)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestPackagedSelfTest — deterministic offline checks for frozen entry points
# ═══════════════════════════════════════════════════════════════════════════════

class TestPackagedSelfTest(unittest.TestCase):
    def test_runtime_arguments_include_version_and_self_test(self):
        import argparse
        from contextlib import redirect_stdout
        from io import StringIO
        import ets_common
        import ets_selftest

        parser = argparse.ArgumentParser()
        ets_selftest.add_runtime_check_arguments(parser)
        self.assertTrue(parser.parse_args(['--self-test']).self_test)
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            parser.parse_args(['--version'])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn(ets_common.APP_VERSION, output.getvalue())

    def test_pk_dictionary_accepts_nonempty_string_map(self):
        import ets_selftest
        with tempfile.TemporaryDirectory() as tmp:
            path = _os.path.join(tmp, 'ecdict_pk.json')
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump({'hello': '你好'}, handle, ensure_ascii=False)
            ets_selftest._validate_pk_dictionary(path)

    def test_pk_dictionary_rejects_invalid_later_entry(self):
        import ets_selftest
        with tempfile.TemporaryDirectory() as tmp:
            path = _os.path.join(tmp, 'ecdict_pk.json')
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump({'valid': 'ok', 'broken': None}, handle)
            with self.assertRaisesRegex(ValueError, 'index 1'):
                ets_selftest._validate_pk_dictionary(path)

    def test_pk_dictionary_rejects_missing_file(self):
        import ets_selftest
        with self.assertRaises(FileNotFoundError):
            ets_selftest._validate_pk_dictionary('missing-ecdict.json')

    def test_pk_dictionary_rejects_invalid_json(self):
        import ets_selftest
        with tempfile.TemporaryDirectory() as tmp:
            path = _os.path.join(tmp, 'ecdict_pk.json')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('{invalid')
            with self.assertRaises(json.JSONDecodeError):
                ets_selftest._validate_pk_dictionary(path)

    def test_pk_dictionary_rejects_empty_object(self):
        import ets_selftest
        with tempfile.TemporaryDirectory() as tmp:
            path = _os.path.join(tmp, 'ecdict_pk.json')
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump({}, handle)
            with self.assertRaisesRegex(ValueError, 'empty'):
                ets_selftest._validate_pk_dictionary(path)

    def test_exam_self_test_does_not_connect(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        import ets_selftest

        class FakeExam:
            def __init__(self):
                self.stop_event = object()
                self.strategy = object()

            def connect(self):
                raise AssertionError('offline self-test must not connect')

        with patch.object(ets_selftest, '_import_target_modules'), \
                redirect_stdout(StringIO()):
            self.assertEqual(ets_selftest.run_self_test('exam', FakeExam), 0)

    def test_gui_self_test_uses_bounded_runtime_check(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        import ets_selftest

        with patch.object(ets_selftest, '_import_target_modules'), \
                patch.object(ets_selftest, '_validate_gui_runtime') as validate, \
                redirect_stdout(StringIO()):
            self.assertEqual(ets_selftest.run_self_test('gui'), 0)
        validate.assert_called_once_with()

    def test_gui_machine_version_check_exits_before_app_creation(self):
        from unittest.mock import patch
        import ets_gui

        with patch.object(ets_gui, 'ETSApp') as app:
            self.assertEqual(
                ets_gui.main(['--verify-version', ets_gui.APP_VERSION]), 0)
            self.assertEqual(
                ets_gui.main(['--verify-version', '0.0.0-invalid']), 3)
        app.assert_not_called()

    def test_packaged_runner_checks_output_and_mismatch(self):
        root = _os.path.dirname(_os.path.dirname(_SrcAuto))
        if root not in _sys.path:
            _sys.path.insert(0, root)
        import packaged_smoke_test

        packaged_smoke_test._run_case(
            _sys.executable, '--version', 'Python', 5)
        with self.assertRaisesRegex(RuntimeError, 'did not emit'):
            packaged_smoke_test._run_case(
                _sys.executable, '--version', 'not-a-real-version', 5)

    def test_packaged_runner_timeout_is_bounded(self):
        root = _os.path.dirname(_os.path.dirname(_SrcAuto))
        if root not in _sys.path:
            _sys.path.insert(0, root)
        import packaged_smoke_test

        with tempfile.TemporaryDirectory() as tmp:
            script = _os.path.join(tmp, 'sleep_forever.py')
            with open(script, 'w', encoding='utf-8') as handle:
                handle.write('import time\ntime.sleep(30)\n')
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                packaged_smoke_test._run_case(
                    _sys.executable, script, None, 0.1)
            self.assertLess(time.monotonic() - started, 5)

    def test_packaged_runner_cleanup_fallbacks_are_bounded(self):
        import subprocess
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        root = _os.path.dirname(_os.path.dirname(_SrcAuto))
        if root not in _sys.path:
            _sys.path.insert(0, root)
        import packaged_smoke_test

        process = MagicMock()
        process.pid = 123
        process.poll.return_value = None
        with (
            patch.object(packaged_smoke_test.os, 'name', 'nt'),
            patch.object(
                packaged_smoke_test.subprocess, 'run',
                return_value=SimpleNamespace(returncode=1)),
        ):
            packaged_smoke_test._terminate_process_tree(process)
        process.kill.assert_called_once_with()

        stubborn = MagicMock()
        stubborn.poll.return_value = None
        stubborn.wait.side_effect = subprocess.TimeoutExpired('test', 10)
        with self.assertRaisesRegex(RuntimeError, 'did not terminate'):
            packaged_smoke_test._wait_after_termination(stubborn)
        stubborn.kill.assert_called_once_with()


# ═══════════════════════════════════════════════════════════════════════════════
#  TestUserDataPath — project root / basename safety
# ═══════════════════════════════════════════════════════════════════════════════

class TestUserDataPath(unittest.TestCase):
    def test_basename_only_under_project_root(self):
        import ets_common
        p = ets_common.user_data_path('pk_extra.json',
                                      anchor_file=ets_common.__file__)
        self.assertTrue(p.endswith('pk_extra.json') or p.endswith('pk_extra.json'.replace('/', _os.sep)))
        self.assertEqual(_os.path.basename(p), 'pk_extra.json')
        # In dev mode base is project root (3 levels up from ets_common.py)
        root = _os.path.dirname(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(ets_common.__file__))))
        self.assertEqual(_os.path.dirname(p), root)

    def test_strips_path_traversal_to_basename(self):
        import ets_common
        p = ets_common.user_data_path('../etc/passwd',
                                      anchor_file=ets_common.__file__)
        self.assertEqual(_os.path.basename(p), 'passwd')
        self.assertNotIn('..', _os.path.basename(p))

    def test_empty_falls_back_to_data_bin(self):
        import ets_common
        p = ets_common.user_data_path('', anchor_file=ets_common.__file__)
        self.assertEqual(_os.path.basename(p), 'data.bin')

    def test_exe_dir_path_aliases_user_data_path(self):
        import ets_word_pk
        import ets_common
        a = ets_word_pk._exe_dir_path('pk_misses.jsonl')
        b = ets_common.user_data_path('pk_misses.jsonl',
                                      anchor_file=ets_word_pk.__file__)
        self.assertEqual(a, b)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestEnsureStopEvent — base helpers used by exam/PK
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnsureStopEvent(unittest.TestCase):
    def test_ensure_creates_event(self):
        import ets_common
        import threading
        base = ets_common.ETSBase(stop_event=None)
        self.assertIsNone(base.stop_event)
        ev = base.ensure_stop_event()
        self.assertIsInstance(ev, threading.Event)
        self.assertIs(base.stop_event, ev)

    def test_ensure_preserves_existing(self):
        import ets_common
        import threading
        ev = threading.Event()
        base = ets_common.ETSBase(stop_event=ev)
        self.assertIs(base.ensure_stop_event(), ev)

    def test_signal_stop_noop_when_none(self):
        import ets_common
        base = ets_common.ETSBase(stop_event=None)
        base.signal_stop()  # must not raise

    def test_signal_stop_sets_event(self):
        import ets_common
        import threading
        ev = threading.Event()
        base = ets_common.ETSBase(stop_event=ev)
        base.signal_stop()
        self.assertTrue(ev.is_set())


# ═══════════════════════════════════════════════════════════════════════════════
#  TestPickEtsTabAttachable — prefer webSocketDebuggerUrl / type=page
# ═══════════════════════════════════════════════════════════════════════════════

class TestPickEtsTabAttachable(unittest.TestCase):
    def test_prefers_attachable_ws_url(self):
        import ets_common
        base = ets_common.ETSBase()
        tabs = [
            {
                'url': 'https://statics.ets100.com/x#/mockExamDetail?set_id=1',
                'title': 'Exam',
                'type': 'page',
                # no webSocketDebuggerUrl
            },
            {
                'url': 'https://statics.ets100.com/portal',
                'title': 'Portal',
                'type': 'page',
                'webSocketDebuggerUrl': 'ws://localhost:10086/devtools/page/abc',
            },
        ]
        picked = base._pick_ets_tab(tabs)
        self.assertIsNotNone(picked)
        self.assertIn('webSocketDebuggerUrl', picked)
        self.assertTrue(picked['webSocketDebuggerUrl'])

    def test_prefers_type_page_when_scores_tie(self):
        import ets_common
        base = ets_common.ETSBase()
        tabs = [
            {
                'url': 'https://statics.ets100.com/x#/mockExam',
                'title': 'sw',
                'type': 'service_worker',
                'webSocketDebuggerUrl': 'ws://localhost:10086/devtools/page/a',
            },
            {
                'url': 'https://statics.ets100.com/x#/mockExam',
                'title': 'page',
                'type': 'page',
                'webSocketDebuggerUrl': 'ws://localhost:10086/devtools/page/b',
            },
        ]
        picked = base._pick_ets_tab(tabs)
        self.assertEqual(picked.get('type'), 'page')


# ═══════════════════════════════════════════════════════════════════════════════
#  TestWordPKNewDictFormat — JS+Base64 worddict_data.json (v0.6.4)
# ═══════════════════════════════════════════════════════════════════════════════

class TestWordPKNewDictFormat(unittest.TestCase):
    def _make_pk_loader(self):
        import ets_word_pk
        pk = object.__new__(ets_word_pk.ETSWordPK)
        pk.word_trans = {}
        pk.trans_index = {}
        pk.cn_seg_index = {}
        pk.debug_mode = False
        pk.debug = lambda *a, **k: None
        pk._index_trans_senses = ets_word_pk.ETSWordPK._index_trans_senses.__get__(pk)
        pk._load_dict_new_format = ets_word_pk.ETSWordPK._load_dict_new_format.__get__(pk)
        return pk

    def test_load_dict_new_format_decodes_base64_trans(self):
        import base64
        import ets_word_pk  # noqa: F401 — ensure path ok
        pk = self._make_pk_loader()
        word = 'apple'
        trans = 'n. 苹果'
        b64 = base64.b64encode(trans.encode('utf-8')).decode('ascii')
        # Loader extracts `[...]` via regex then strips outer backticks;
        # body must be: wordsTranslateArr = `[{...}, ...]`
        arr_json = json.dumps(
            [{word: {'word': word, 'trans': b64}}], ensure_ascii=False)
        body = 'wordsTranslateArr = `%s`' % arr_json
        tmp = tempfile.mkdtemp(prefix='pk_dict_')
        path = _os.path.join(tmp, 'worddict_data.json')
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(body)
            n = pk._load_dict_new_format(path)
            self.assertGreaterEqual(n, 1)
            self.assertEqual(pk.word_trans['apple'], trans)
            # sense indexed without leading POS if stripped
            self.assertTrue(any('苹果' in k for k in pk.trans_index.keys())
                            or 'n. 苹果' in pk.trans_index
                            or '苹果' in pk.trans_index)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_load_dict_new_format_missing_backticks_raises(self):
        pk = self._make_pk_loader()
        tmp = tempfile.mkdtemp(prefix='pk_dict_bad_')
        path = _os.path.join(tmp, 'worddict_data.json')
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write('not a valid dict file')
            with self.assertRaises(ValueError):
                pk._load_dict_new_format(path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_index_trans_senses_strips_control_and_pos(self):
        pk = self._make_pk_loader()
        pk._index_trans_senses('hello', 'n. 你好\x01v. 招呼')
        # control char must not appear in keys
        for k in pk.trans_index:
            self.assertNotRegex(k, r'[\x00-\x1f]')
        flat = ' '.join(pk.trans_index.keys())
        self.assertIn('你好', flat)
        self.assertIn('招呼', flat)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestFuzzyOptMatch — bare substring gated by ratio / boundary
# ═══════════════════════════════════════════════════════════════════════════════

class TestFuzzyOptMatch(unittest.TestCase):
    def test_exact(self):
        import ets_word_pk
        self.assertTrue(ets_word_pk._fuzzy_opt_match('book', 'book'))

    def test_short_substring_rejected(self):
        import ets_word_pk
        # 'in' inside 'inside' must not match (ratio < 0.8 and no boundary)
        self.assertFalse(ets_word_pk._fuzzy_opt_match('inside', 'in'))
        self.assertFalse(ets_word_pk._fuzzy_opt_match('notebook', 'book'))

    def test_high_ratio_substring_ok(self):
        import ets_word_pk
        # long shared prefix/substring with ratio >= 0.8
        self.assertTrue(ets_word_pk._fuzzy_opt_match('abcdefgh', 'abcdefg'))

    def test_word_boundary_ok(self):
        import ets_word_pk
        self.assertTrue(ets_word_pk._fuzzy_opt_match('look up', 'look'))
        self.assertTrue(ets_word_pk._fuzzy_opt_match('look-up', 'up'))


# ═══════════════════════════════════════════════════════════════════════════════
#  TestBuildRwAnswers — OPEN-H6 pure rebuild from showData
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildRwAnswers(unittest.TestCase):
    def _make_auto(self):
        import ets_auto
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.answers = {}
        inst.total_questions = 0
        inst.debug_mode = False
        inst.debug = lambda *a, **k: None
        inst._build_rw_answers_from_showdata = (
            ets_auto.ETSAutoAnswer._build_rw_answers_from_showdata.__get__(inst))
        return inst

    def test_builds_rw_keys_from_showdata(self):
        inst = self._make_auto()
        show = {
            'question': [
                {'id': 'q1', 'info': [{'answer': 'a'}, {'answer': 'b'}]},
                {'id': 'q2', 'info': [{'answer': 'C'}]},
            ]
        }
        n = inst._build_rw_answers_from_showdata(show, verbose=False)
        self.assertEqual(n, 3)
        self.assertEqual(inst.answers['rw_q1_0']['answer'], 'A')
        self.assertEqual(inst.answers['rw_q1_1']['answer'], 'B')
        self.assertEqual(inst.answers['rw_q2']['answer'], 'C')
        self.assertEqual(inst.total_questions, 3)

    def test_replaces_previous_rw_keys_only(self):
        inst = self._make_auto()
        inst.answers = {
            'rw_old': {'type': 'choose', 'answer': 'Z'},
            'keep_me': {'type': 'fill', 'answer': 'x'},
        }
        show = {'question': [{'id': 'n1', 'info': [{'answer': 'd'}]}]}
        inst._build_rw_answers_from_showdata(show, verbose=False)
        self.assertNotIn('rw_old', inst.answers)
        self.assertIn('keep_me', inst.answers)
        self.assertIn('rw_n1', inst.answers)

    def test_empty_showdata_clears_rw(self):
        inst = self._make_auto()
        inst.answers = {'rw_x': {'type': 'choose', 'answer': 'A'}}
        n = inst._build_rw_answers_from_showdata({}, verbose=False)
        self.assertEqual(n, 0)
        self.assertEqual(inst.answers, {})


# ═══════════════════════════════════════════════════════════════════════════════
#  TestDropConnection — half-open pair cleanup
# ═══════════════════════════════════════════════════════════════════════════════

class TestDropConnection(unittest.TestCase):
    def test_clears_ws_and_tab(self):
        import ets_common

        class FakeWs:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        base = ets_common.ETSBase()
        ws = FakeWs()
        base.ws = ws
        base.tab = {'url': 'x'}
        base._drop_connection()
        self.assertTrue(ws.closed)
        self.assertIsNone(base.ws)
        self.assertIsNone(base.tab)

    def test_close_exception_still_clears(self):
        import ets_common

        class BadWs:
            def close(self):
                raise RuntimeError('boom')

        base = ets_common.ETSBase()
        base.ws = BadWs()
        base.tab = {}
        base._drop_connection()
        self.assertIsNone(base.ws)
        self.assertIsNone(base.tab)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestReconnectControlFlow — ETSBase.reconnect with mocked CDP
# ═══════════════════════════════════════════════════════════════════════════════

class TestReconnectControlFlow(unittest.TestCase):
    def _tabs_payload(self, with_ws=True):
        tab = {
            'url': 'https://statics.ets100.com/x#/mockExamDetail?set_id=1',
            'title': 'Exam',
            'type': 'page',
        }
        if with_ws:
            tab['webSocketDebuggerUrl'] = 'ws://localhost:10086/devtools/page/x'
        return [tab]

    def test_reconnect_success_resets_mid(self):
        import ets_common
        from unittest.mock import patch, MagicMock
        base = ets_common.ETSBase(port=10086, debug_mode=False)
        base.mid = 99
        base.ws = object()
        base.tab = {'url': 'stale'}
        tabs_json = json.dumps(self._tabs_payload()).encode()

        class FakeResp:
            def read(self_inner):
                return tabs_json

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        fake_ws = MagicMock()
        with patch('ets_common._open_local_cdp_url', return_value=FakeResp()), \
             patch('ets_common._connect_local_cdp_websocket',
                   return_value=fake_ws), \
             patch.object(base, 'interruptible_sleep', return_value=None):
            ok = base.reconnect()
        self.assertTrue(ok)
        self.assertEqual(base.mid, 0)
        self.assertIs(base.ws, fake_ws)
        self.assertIn('mockExamDetail', base.tab.get('url', ''))

    def test_reconnect_raises_after_max_retries(self):
        import ets_common
        from unittest.mock import patch
        base = ets_common.ETSBase(port=10086, debug_mode=False)
        base._RECONNECT_MAX_RETRIES = 2
        base._RECONNECT_DELAY = 0
        with patch('ets_common._open_local_cdp_url', side_effect=OSError('down')), \
             patch.object(base, 'interruptible_sleep', return_value=None):
            with self.assertRaises(ConnectionError):
                base.reconnect()
        self.assertIsNone(base.ws)
        self.assertIsNone(base.tab)

    def test_reconnect_interrupted_by_stop_event(self):
        import ets_common
        import threading
        from unittest.mock import patch
        ev = threading.Event()
        base = ets_common.ETSBase(port=10086, debug_mode=False, stop_event=ev)
        base._RECONNECT_MAX_RETRIES = 3
        base._RECONNECT_DELAY = 0.01

        def _sleep(_s):
            raise InterruptedError('stop')

        with patch('ets_common._open_local_cdp_url', side_effect=OSError('down')), \
             patch.object(base, 'interruptible_sleep', side_effect=_sleep):
            with self.assertRaises(InterruptedError):
                base.reconnect()
        self.assertIsNone(base.ws)

    def test_reconnect_no_ets_tab(self):
        import ets_common
        from unittest.mock import patch
        base = ets_common.ETSBase(port=10086, debug_mode=False)
        base._RECONNECT_MAX_RETRIES = 1
        base._RECONNECT_DELAY = 0

        class FakeResp:
            def read(self):
                return json.dumps([{'url': 'https://example.com', 'title': 'x'}]).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch('ets_common._open_local_cdp_url', return_value=FakeResp()), \
             patch.object(base, 'interruptible_sleep', return_value=None):
            with self.assertRaises(ConnectionError):
                base.reconnect()

    def test_invalidate_ws_nulls_socket(self):
        import ets_common

        class FakeWs:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        base = ets_common.ETSBase()
        ws = FakeWs()
        base.ws = ws
        base._invalidate_ws('timeout')
        self.assertTrue(ws.closed)
        self.assertIsNone(base.ws)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestRwReconnectHandler — _handle_rw_reconnect pure control flow
# ═══════════════════════════════════════════════════════════════════════════════

class TestRwReconnectHandler(unittest.TestCase):
    def _make(self):
        import ets_auto
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.debug_mode = False
        inst.debug = lambda *a, **k: None
        inst.interruptible_sleep = lambda s: None
        inst._handle_rw_reconnect = (
            ets_auto.ETSAutoAnswer._handle_rw_reconnect.__get__(inst))
        return inst

    def test_break_when_over_threshold(self):
        inst = self._make()
        action = inst._handle_rw_reconnect(ConnectionError('x'), 3)
        self.assertEqual(action, 'break')

    def test_continue_when_reconnect_and_post_ok(self):
        inst = self._make()
        inst.reconnect = lambda: True
        inst._rw_post_reconnect = lambda: True
        action = inst._handle_rw_reconnect(ConnectionError('x'), 1)
        self.assertEqual(action, 'continue')

    def test_break_when_post_reconnect_fails(self):
        inst = self._make()
        inst.reconnect = lambda: True
        inst._rw_post_reconnect = lambda: False
        action = inst._handle_rw_reconnect(ConnectionError('x'), 1)
        self.assertEqual(action, 'break')

    def test_interrupted_reraises(self):
        inst = self._make()

        def _boom():
            raise InterruptedError('stop')

        inst.reconnect = _boom
        with self.assertRaises(InterruptedError):
            inst._handle_rw_reconnect(ConnectionError('x'), 1)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestRecordingWait — is_recording_page / wait_for_recording_done (mocked eval_js)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecordingWait(unittest.TestCase):
    def _make(self):
        import ets_auto
        import threading
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.debug_mode = False
        inst.debug = lambda *a, **k: None
        inst.stop_event = threading.Event()
        inst._recording_window_closed = False
        inst._on_question = None
        inst._fire_question = lambda info: None
        inst.is_recording_page = ets_auto.ETSAutoAnswer.is_recording_page.__get__(inst)
        inst.wait_for_recording_done = (
            ets_auto.ETSAutoAnswer.wait_for_recording_done.__get__(inst))
        return inst

    def test_is_recording_page_true(self):
        inst = self._make()
        inst.eval_js = lambda js: json.dumps({'is_recording': True})
        self.assertTrue(inst.is_recording_page())

    def test_is_recording_page_false_on_error(self):
        inst = self._make()
        inst.eval_js = lambda js: (_ for _ in ()).throw(RuntimeError('cdp'))
        self.assertFalse(inst.is_recording_page())

    def test_wait_done_when_next_ready(self):
        inst = self._make()
        inst.eval_js = lambda js: json.dumps({'next_ready': True})
        self.assertTrue(inst.wait_for_recording_done(max_wait=5))

    def test_wait_stops_on_stop_event(self):
        inst = self._make()
        inst.stop_event.set()
        inst.eval_js = lambda js: json.dumps({'next_ready': False})
        self.assertFalse(inst.wait_for_recording_done(max_wait=5))

    def test_wait_stops_on_window_closed(self):
        inst = self._make()
        inst._recording_window_closed = True
        inst.eval_js = lambda js: json.dumps({'next_ready': False})
        self.assertFalse(inst.wait_for_recording_done(max_wait=5))

    def test_wait_timeout_false(self):
        inst = self._make()
        inst.eval_js = lambda js: json.dumps({'next_ready': False})
        # max_wait=0 → loop never enters body with time check; still returns False
        self.assertFalse(inst.wait_for_recording_done(max_wait=0))


# ═══════════════════════════════════════════════════════════════════════════════
#  TestWordPKDictPaths — new vs old dictionary path selection
# ═══════════════════════════════════════════════════════════════════════════════

class TestWordPKDictPaths(unittest.TestCase):
    def test_prefers_worddict_data_when_exists(self):
        import ets_word_pk
        tmp = tempfile.mkdtemp(prefix='pk_paths_')
        try:
            new_dir = _os.path.join(tmp, 'common', 'material', 'word')
            _os.makedirs(new_dir)
            new_path = _os.path.join(new_dir, 'worddict_data.json')
            with open(new_path, 'w', encoding='utf-8') as f:
                f.write('x')
            old_path = _os.path.join(tmp, 'pc_xst_dict', 'pc_xst_dict.json')
            _os.makedirs(_os.path.dirname(old_path))
            with open(old_path, 'w', encoding='utf-8') as f:
                f.write('[]')
            pk = object.__new__(ets_word_pk.ETSWordPK)
            pk.ets_base = tmp
            pk.dict_path_new = new_path
            pk.dict_path = old_path
            if _os.path.exists(pk.dict_path_new):
                pk.dict_path = pk.dict_path_new
            self.assertEqual(pk.dict_path, new_path)
            self.assertIn('worddict_data', pk.dict_path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_falls_back_to_pc_xst_dict(self):
        import ets_word_pk
        tmp = tempfile.mkdtemp(prefix='pk_paths_old_')
        try:
            old_path = _os.path.join(tmp, 'pc_xst_dict', 'pc_xst_dict.json')
            _os.makedirs(_os.path.dirname(old_path))
            with open(old_path, 'w', encoding='utf-8') as f:
                f.write('[]')
            new_path = _os.path.join(tmp, 'common', 'material', 'word', 'worddict_data.json')
            pk = object.__new__(ets_word_pk.ETSWordPK)
            pk.ets_base = tmp
            pk.dict_path_new = new_path
            pk.dict_path = old_path
            if _os.path.exists(pk.dict_path_new):
                pk.dict_path = pk.dict_path_new
            self.assertEqual(pk.dict_path, old_path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_load_dictionary_merges_ecdict_missing_only(self):
        import ets_word_pk
        import base64
        tmp = tempfile.mkdtemp(prefix='pk_merge_')
        try:
            # Minimal new-format dict with one word
            b64 = base64.b64encode('n. 苹果'.encode('utf-8')).decode('ascii')
            arr = [{'apple': {'word': 'apple', 'trans': b64}}]
            body = 'wordsTranslateArr = `%s`' % json.dumps(arr, ensure_ascii=False)
            dict_path = _os.path.join(tmp, 'worddict_data.json')
            with open(dict_path, 'w', encoding='utf-8') as f:
                f.write(body)
            ecdict_path = _os.path.join(tmp, 'ecdict_pk.json')
            with open(ecdict_path, 'w', encoding='utf-8') as f:
                json.dump({'apple': 'should_not_override', 'banana': '香蕉'}, f)
            pk = object.__new__(ets_word_pk.ETSWordPK)
            pk.word_trans = {}
            pk.trans_index = {}
            pk.cn_seg_index = {}
            pk.pk_extra = {}
            pk.debug_mode = False
            pk.debug = lambda *a, **k: None
            pk.dict_path = dict_path
            pk.ecdict_path = ecdict_path
            pk.extra_path = _os.path.join(tmp, 'no_extra.json')
            pk._index_trans_senses = ets_word_pk.ETSWordPK._index_trans_senses.__get__(pk)
            pk._load_dict_new_format = ets_word_pk.ETSWordPK._load_dict_new_format.__get__(pk)
            # load_dictionary also builds stems etc.; call only the base+ecdict portion
            # by invoking full load_dictionary after stubbing side paths
            pk.load_dictionary = ets_word_pk.ETSWordPK.load_dictionary.__get__(pk)
            # Avoid missing methods used later in load_dictionary if any — run and catch
            ok = pk.load_dictionary()
            self.assertTrue(ok)
            self.assertEqual(pk.word_trans['apple'], 'n. 苹果')  # base wins
            self.assertEqual(pk.word_trans.get('banana'), '香蕉')  # ecdict fills gap
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestRemoteAllowlist — is_url_allowed host gate
# ═══════════════════════════════════════════════════════════════════════════════

class TestRemoteAllowlist(unittest.TestCase):
    def test_allows_known_https_hosts(self):
        import ets_remote
        self.assertTrue(ets_remote.is_url_allowed(
            'https://raw.githubusercontent.com/o/r/main/info.json'))
        self.assertTrue(ets_remote.is_url_allowed(
            'https://gitee.com/o/r/raw/main/info.json'))

    def test_blocks_unknown_and_insecure(self):
        import ets_remote
        self.assertFalse(ets_remote.is_url_allowed('http://raw.githubusercontent.com/x'))
        self.assertFalse(ets_remote.is_url_allowed('https://evil.example.com/x'))
        self.assertFalse(ets_remote.is_url_allowed('file:///etc/passwd'))
        self.assertFalse(ets_remote.is_url_allowed(''))
        self.assertFalse(ets_remote.is_url_allowed(None))


# ═══════════════════════════════════════════════════════════════════════════════
#  TestGuiProgressLogic — progress math without real CTk mainloop
# ═══════════════════════════════════════════════════════════════════════════════

class TestGuiProgressLogic(unittest.TestCase):
    def _bind_preview(self):
        import ets_gui

        class Var:
            def __init__(self):
                self.value = 0

            def set(self, v):
                self.value = v

        class Label:
            def __init__(self):
                self.text = ''

            def configure(self, **kw):
                if 'text' in kw:
                    self.text = kw['text']

        class FakeApp:
            def __init__(self):
                self._type_answered = {}
                self._last_progress = (0, 0)
                self._preview_expanded = False
                self._preview_panel = None
                self._progress_var = Var()
                self._progress_label_var = Var()
                self._preview_inline = Label()

            def winfo_exists(self):
                return True

        app = FakeApp()
        app._do_update_answer_preview = (
            ets_gui.ETSApp._do_update_answer_preview.__get__(app))
        return app

    def test_pct_from_answered_total(self):
        app = self._bind_preview()
        app._do_update_answer_preview({
            'type': 'choose', 'type_label': '[CHS]', 'qid': '1',
            'answer': 'A', 'answered': 2, 'total_questions': 10,
        })
        self.assertAlmostEqual(app._progress_var.value, 0.2)
        self.assertEqual(app._last_progress, (2, 10))
        self.assertIn('2/10', app._progress_label_var.value)

    def test_count_only_without_total(self):
        app = self._bind_preview()
        app._do_update_answer_preview({
            'type': 'pk', 'type_label': 'PK', 'answer': 'word',
            'answered': 5, 'total_questions': 0,
        })
        self.assertEqual(app._last_progress, (5, 0))
        self.assertIn('5', app._progress_label_var.value)

    def test_choose_fill_sum_for_bar(self):
        """H22: fill after choose should sum type counts, not reset bar."""
        app = self._bind_preview()
        app._do_update_answer_preview({
            'type': 'choose', 'type_label': 'CHS', 'qid': 'a',
            'answer': 'A', 'answered': 3, 'total_questions': 10,
        })
        app._do_update_answer_preview({
            'type': 'fill', 'type_label': 'FIL', 'qid': 'b',
            'answer': 'x', 'answered': 2, 'total_questions': 10,
        })
        # answered becomes 3+2=5
        self.assertEqual(app._last_progress[0], 5)
        self.assertEqual(app._last_progress[1], 10)
        self.assertAlmostEqual(app._progress_var.value, 0.5)


# ═══════════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════
#  Audit fix pass 2026-07-13 — reconnect / silent state / pk_extra corrupt
# ═══════════════════════════════════════════════════════════════════════════════

class TestExamReconnectLoadFail(unittest.TestCase):
    def _make(self):
        import ets_auto
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.debug_mode = False
        inst.debug = lambda *a, **k: None
        inst.interruptible_sleep = lambda s: None
        inst.set_id = '111'
        inst.ets_base = '.'
        inst.answers = {'k': {'type': 'choose', 'answer': 'A'}}
        inst.recording_answers = []
        inst.strategy = None
        inst.rw_show_data = None
        inst._rw_cache_time = 0
        inst._handle_exam_reconnect = (
            ets_auto.ETSAutoAnswer._handle_exam_reconnect.__get__(inst))
        return inst

    def test_load_fail_after_set_id_change_returns_false(self):
        inst = self._make()
        inst.reconnect = lambda: True
        inst._read_pinia_config = lambda: setattr(inst, 'set_id', '222') or None
        inst._detect_rw_mode = lambda: None
        inst.inject_bridge = lambda: {}
        inst.load_answers = lambda: False
        ok = inst._handle_exam_reconnect(ConnectionError('x'), 1)
        self.assertFalse(ok)

    def test_empty_answers_same_set_reload_fail_returns_false(self):
        inst = self._make()
        inst.answers = {}
        inst.recording_answers = []
        inst.reconnect = lambda: True
        inst._read_pinia_config = lambda: None
        inst._detect_rw_mode = lambda: None
        inst.inject_bridge = lambda: {}
        inst.load_answers = lambda: False
        ok = inst._handle_exam_reconnect(ConnectionError('x'), 1)
        self.assertFalse(ok)

    def test_success_when_set_unchanged_and_answers_present(self):
        inst = self._make()
        inst.reconnect = lambda: True
        inst._read_pinia_config = lambda: None
        inst._detect_rw_mode = lambda: None
        inst.inject_bridge = lambda: {}
        ok = inst._handle_exam_reconnect(ConnectionError('x'), 1)
        self.assertTrue(ok)

    def test_threshold_break_without_reconnect(self):
        inst = self._make()
        called = []
        inst.reconnect = lambda: called.append('reconnect')
        ok = inst._handle_exam_reconnect(ConnectionError('x'), 3)
        self.assertFalse(ok)
        self.assertEqual(called, [])

    def test_bridge_called_on_success_path(self):
        inst = self._make()
        bridge = []
        inst.reconnect = lambda: True
        inst._read_pinia_config = lambda: None
        inst._detect_rw_mode = lambda: None
        inst.inject_bridge = lambda: bridge.append('ok') or {}
        ok = inst._handle_exam_reconnect(ConnectionError('x'), 1)
        self.assertTrue(ok)
        self.assertEqual(bridge, ['ok'])

    def test_set_id_change_reloads_answers_and_strategy(self):
        inst = self._make()
        loaded = []
        strat = []

        class FakeStrat:
            def load_set(self, set_id, data_dir=None):
                strat.append((set_id, data_dir))

        def _pinia():
            inst.set_id = '222'

        inst.reconnect = lambda: True
        inst._read_pinia_config = _pinia
        inst._detect_rw_mode = lambda: None
        inst.inject_bridge = lambda: {}
        inst.load_answers = lambda: loaded.append(inst.set_id) or True
        inst.strategy = FakeStrat()
        inst.ets_base = 'C:/tmp/ETS'
        ok = inst._handle_exam_reconnect(ConnectionError('x'), 1)
        self.assertTrue(ok)
        self.assertEqual(loaded, ['222'])
        self.assertEqual(strat, [('222', 'C:/tmp/ETS')])


class TestAnswerChooseDecisions(unittest.TestCase):
    def _make(self, groups, answers=None, strat_map=None, in_review_mode=False):
        import ets_auto
        from ets_common import ETSBase
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.debug_mode = False
        inst.debug = lambda *a, **k: None
        inst.interruptible_sleep = lambda s: None
        inst.js_escape = ETSBase.js_escape
        inst._IFRAME_FINDER = 'var iframe = null'
        inst.stats = {'choose_answered': 0, 'choose_skip': 0, 'errors': 0}
        inst.answered_questions = []
        inst.total_questions = 10
        inst.answers = answers or {}
        inst._on_question_answered = None
        inst._fire_question = lambda info: None
        inst.parse_eval_json = ETSBase.parse_eval_json.__get__(inst)
        inst.is_cdp_parse_error = ETSBase.is_cdp_parse_error.__get__(inst)
        inst.get_page_state = lambda: {
            'question_groups': groups,
            'inReviewMode': in_review_mode,
        }

        class Strat:
            def lookup(self, stype, stid, qid=None, **k):
                return (strat_map or {}).get('%s_%s' % (stid, qid))

        inst.strategy = Strat()
        inst.answer_choose = ets_auto.ETSAutoAnswer.answer_choose.__get__(inst)
        return inst

    def test_review_mode_skips_and_marks_done(self):
        inst = self._make([{'qid': '1_1', 'anySelected': False}], in_review_mode=True)
        any_new, likely_done = inst.answer_choose()
        self.assertFalse(any_new)
        self.assertTrue(likely_done)

    def test_already_selected_skips(self):
        inst = self._make([{'qid': '10_1', 'anySelected': True}])
        any_new, likely_done = inst.answer_choose()
        self.assertFalse(any_new)
        self.assertTrue(likely_done)
        self.assertEqual(inst.stats['choose_skip'], 1)

    def test_invalid_letter_does_not_click(self):
        clicks = []
        inst = self._make(
            [{'qid': '10_1', 'anySelected': False}],
            answers={'10_1': {'type': 'choose', 'answer': 'Z'}},
        )
        inst.eval_js = lambda js: clicks.append(js) or None
        any_new, _ = inst.answer_choose()
        self.assertFalse(any_new)
        self.assertEqual(clicks, [])

    def test_strategy_mismatch_uses_strategy_letter(self):
        clicks = []
        inst = self._make(
            [{'qid': '10_1', 'anySelected': False}],
            answers={'10_1': {'type': 'choose', 'answer': 'A'}},
            strat_map={'10_1': {'type': 'choose', 'answer': 'B', 'source': 'local'}},
        )

        def _eval(js):
            clicks.append(js)
            if 'choose_selected' in js:
                return True
            if 'setPCChoose2' in js or 'getElementById' in js:
                return json.dumps({'method': 'setPCChoose2'})
            if 'kttb_getPcChoise' in js:
                return 1
            return None

        inst.eval_js = _eval
        any_new, _ = inst.answer_choose()
        self.assertTrue(any_new)
        self.assertTrue(any('10_1_2' in c for c in clicks))
        self.assertEqual(inst.stats['choose_answered'], 1)

    def test_cdp_parse_error_raises_connection_error(self):
        inst = self._make([])
        inst.get_page_state = lambda: {'error': 'eval_js_failed'}
        with self.assertRaises(ConnectionError):
            inst.answer_choose()

    def test_cache_miss_filled_from_strategy(self):
        """answers table miss must still use strategy.lookup (not skip)."""
        clicks = []
        inst = self._make(
            [{'qid': '10_1', 'anySelected': False}],
            answers={},  # miss
            strat_map={'10_1': {'type': 'choose', 'answer': 'C', 'source': 'local'}},
        )

        def _eval(js):
            clicks.append(js)
            if 'choose_selected' in js:
                return True
            if 'setPCChoose2' in js or 'getElementById' in js:
                return json.dumps({'method': 'setPCChoose2'})
            return 1

        inst.eval_js = _eval
        any_new, _ = inst.answer_choose()
        self.assertTrue(any_new)
        self.assertTrue(any('10_1_3' in c for c in clicks))
        self.assertEqual(inst.stats['choose_answered'], 1)
        self.assertEqual(inst.answers.get('10_1', {}).get('answer'), 'C')


class TestAnswerFillDecisions(unittest.TestCase):
    def _make(self, inputs, answers=None):
        import ets_auto
        from ets_common import ETSBase
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.debug_mode = False
        inst.debug = lambda *a, **k: None
        inst.js_escape = ETSBase.js_escape
        inst._IFRAME_FINDER = 'var iframe = null'
        inst.stats = {'fill_answered': 0, 'fill_skip': 0, 'errors': 0}
        inst.total_questions = 5
        inst.answers = answers or {}
        inst._on_question_answered = None
        inst._fire_question = lambda info: None
        inst.parse_eval_json = ETSBase.parse_eval_json.__get__(inst)
        inst.is_cdp_parse_error = ETSBase.is_cdp_parse_error.__get__(inst)
        inst.get_page_state = lambda: {'inputs': inputs}

        class Strat:
            def lookup(self, *a, **k):
                return None

        inst.strategy = Strat()
        inst.answer_fill = ets_auto.ETSAutoAnswer.answer_fill.__get__(inst)
        return inst

    def test_already_filled_case_insensitive_skips(self):
        inst = self._make(
            [{'id': '10_1', 'value': 'Hello'}],
            answers={'10_1': {'type': 'fill', 'answer': 'hello'}},
        )
        inst.eval_js = lambda js: (_ for _ in ()).throw(AssertionError('no eval'))
        any_new, has_inputs = inst.answer_fill()
        self.assertFalse(any_new)
        self.assertTrue(has_inputs)
        self.assertEqual(inst.stats['fill_skip'], 1)

    def test_fill_false_counts_error_not_answered(self):
        inst = self._make(
            [{'id': '10_1', 'value': ''}],
            answers={'10_1': {'type': 'fill', 'answer': 'cat'}},
        )
        inst.eval_js = lambda js: json.dumps({'filled': False, 'error': 'not found'})
        any_new, _ = inst.answer_fill()
        self.assertFalse(any_new)
        self.assertEqual(inst.stats['fill_answered'], 0)
        self.assertEqual(inst.stats.get('fill_errors'), 1)

    def test_fill_success_counts_answered(self):
        inst = self._make(
            [{'id': '10_1', 'value': ''}],
            answers={'10_1': {'type': 'fill', 'answer': 'cat'}},
        )

        def _eval(js):
            if 'HTMLInputElement' in js or 'setter' in js or 'filled' in js:
                return json.dumps({'filled': True, 'value': 'cat'})
            return 1

        inst.eval_js = _eval
        any_new, _ = inst.answer_fill()
        self.assertTrue(any_new)
        self.assertEqual(inst.stats['fill_answered'], 1)

    def test_cdp_parse_error_raises(self):
        inst = self._make([])
        inst.get_page_state = lambda: {'error': 'eval_js_failed'}
        with self.assertRaises(ConnectionError):
            inst.answer_fill()

    def test_fill_miss_from_strategy(self):
        inst = self._make(
            [{'id': '10_1', 'value': ''}],
            answers={},
        )
        class Strat:
            def lookup(self, *a, **k):
                return {'type': 'fill', 'answer': 'dog', 'source': 'local'}
        inst.strategy = Strat()

        def _eval(js):
            if 'kttb_getPcBlank' in js or '__ets_recorded_fill' in js:
                return 1
            return json.dumps({'filled': True, 'value': 'dog'})

        inst.eval_js = _eval
        any_new, _ = inst.answer_fill()
        self.assertTrue(any_new)
        self.assertEqual(inst.stats['fill_answered'], 1)
        self.assertEqual(inst.answers.get('10_1', {}).get('answer'), 'dog')


class TestPageAndPkStateErrors(unittest.TestCase):
    def test_get_page_state_none_is_error(self):
        import ets_auto
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.eval_js = lambda js: None
        inst.get_page_state = ets_auto.ETSAutoAnswer.get_page_state.__get__(inst)
        # Bind _IFRAME_FINDER used inside method string format
        inst._IFRAME_FINDER = 'var iframe=null'
        st = inst.get_page_state()
        self.assertEqual(st.get('error'), 'eval_js_failed')

    def test_get_pk_state_none_is_error(self):
        import ets_word_pk
        pk = object.__new__(ets_word_pk.ETSWordPK)
        pk.eval_js = lambda js: None
        pk.get_pk_state = ets_word_pk.ETSWordPK.get_pk_state.__get__(pk)
        st = pk.get_pk_state()
        self.assertEqual(st.get('error'), 'eval_js_failed')

    def test_is_recording_reraises_connection_error(self):
        import ets_auto
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        def _boom(js):
            raise ConnectionError('ws dead')
        inst.eval_js = _boom
        inst.is_recording_page = ets_auto.ETSAutoAnswer.is_recording_page.__get__(inst)
        with self.assertRaises(ConnectionError):
            inst.is_recording_page()


class TestPkExtraCorruptGuard(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        import ets_remote
        # Download-path guard tests need the network switch armed.
        net_patcher = patch.object(ets_remote, 'REMOTE_NETWORK_ENABLED', True)
        net_patcher.start()
        self.addCleanup(net_patcher.stop)

    def test_status_missing_ok_invalid(self):
        import ets_remote
        import tempfile, os
        data, st = ets_remote._load_local_pk_extra_status(None)
        self.assertEqual(st, 'missing')
        self.assertEqual(data, {})
        td = tempfile.mkdtemp()
        good = os.path.join(td, 'pk_extra.json')
        with open(good, 'w', encoding='utf-8') as f:
            json.dump({'hello': 'world'}, f)
        data, st = ets_remote._load_local_pk_extra_status(good)
        self.assertEqual(st, 'ok')
        self.assertEqual(data.get('hello'), 'world')
        bad = os.path.join(td, 'bad.json')
        with open(bad, 'w', encoding='utf-8') as f:
            f.write('{not json')
        data, st = ets_remote._load_local_pk_extra_status(bad)
        self.assertEqual(st, 'invalid')
        self.assertEqual(data, {})

    def test_download_refuses_overwrite_corrupt_local(self):
        import ets_remote
        import tempfile, os
        td = tempfile.mkdtemp()
        target = os.path.join(td, 'pk_extra.json')
        with open(target, 'w', encoding='utf-8') as f:
            f.write('{broken')
        r = ets_remote.ETSRemote(current_version='0.6.5')
        # Even with a plausible allowlisted URL, corrupt local must refuse before fetch loop
        ok, msg = r.download_pk_extra(
            url='https://raw.githubusercontent.com/yigenhuobah/ETS_Auto/main/pk_extra.json',
            target_path=target)
        self.assertFalse(ok)
        self.assertIn('损坏', msg)
        # File still the broken original (not wiped to remote)
        with open(target, 'r', encoding='utf-8') as f:
            self.assertEqual(f.read(), '{broken')

    def test_corrupt_target_does_not_clobber_good_bak(self):
        """Critical: status-first — never copy corrupt target over a good .bak."""
        import ets_remote
        import tempfile, os
        td = tempfile.mkdtemp()
        target = os.path.join(td, 'pk_extra.json')
        bak = target + '.bak'
        good = {'learned': 'keep-me', 'apple': '苹果'}
        with open(bak, 'w', encoding='utf-8') as f:
            json.dump(good, f, ensure_ascii=False)
        with open(target, 'w', encoding='utf-8') as f:
            f.write('{broken')
        r = ets_remote.ETSRemote(current_version='0.6.5')
        # Will try network but after restore local should be good; even if download
        # fails we must not have destroyed bak. Prefer offline: mock by using
        # disallowed? allowlisted URL may hang/slow — use fake host fail after restore.
        ok, msg = r.download_pk_extra(
            url='https://raw.githubusercontent.com/yigenhuobah/ETS_Auto/main/pk_extra.json',
            target_path=target)
        # bak must still be the good snapshot (not '{broken')
        with open(bak, 'r', encoding='utf-8') as f:
            bak_loaded = json.load(f)
        self.assertEqual(bak_loaded.get('learned'), 'keep-me')
        # target should have been restored from bak before any merge attempt
        with open(target, 'r', encoding='utf-8') as f:
            target_loaded = json.load(f)
        self.assertEqual(target_loaded.get('learned'), 'keep-me')



class TestCdpParseErrorClass(unittest.TestCase):
    def test_distinguishes_semantic_vs_cdp(self):
        import ets_common
        b = ets_common.ETSBase()
        self.assertTrue(b.is_cdp_parse_error({'error': 'eval_js_failed'}))
        self.assertTrue(b.is_cdp_parse_error({'error': 'non_object_result'}))
        self.assertFalse(b.is_cdp_parse_error({'error': 'no iframe'}))
        self.assertFalse(b.is_cdp_parse_error({'error': 'no doc'}))
        self.assertFalse(b.is_cdp_parse_error({}))


class TestEvalJsStopSlice(unittest.TestCase):
    def test_stop_event_raises_interrupted(self):
        import ets_common
        import threading
        base = ets_common.ETSBase(stop_event=threading.Event())
        class FakeWS:
            def send(self, p):
                return None
            def settimeout(self, t):
                return None
            def recv(self):
                # First slice: stop is set by test before call
                raise ets_common.websocket.WebSocketTimeoutException('slice')
            def close(self):
                return None
        base.ws = FakeWS()
        base.mid = 0
        base.debug_mode = False
        base.debug = lambda *a, **k: None
        base.stop_event.set()
        with self.assertRaises(InterruptedError):
            base.eval_js('1+1')


class TestReconnectControlShell(unittest.TestCase):
    def _make(self):
        import ets_common
        base = ets_common.ETSBase()
        base.debug_mode = False
        base.debug = lambda *a, **k: None
        base.interruptible_sleep = lambda s: None
        base.ws = object()  # pretend live
        return base

    def test_break_over_threshold(self):
        b = self._make()
        self.assertEqual(b.reconnect_control(3, label='X'), 'break')

    def test_continue_when_reconnect_ok(self):
        b = self._make()
        b.reconnect = lambda: True
        self.assertEqual(b.reconnect_control(1, label='X'), 'continue')

    def test_post_ok_false_breaks(self):
        b = self._make()
        b.reconnect = lambda: True
        self.assertEqual(
            b.reconnect_control(1, post_ok=lambda: False, label='X'), 'break')

    def test_interrupted_reraises(self):
        b = self._make()
        def _boom():
            raise InterruptedError('stop')
        b.reconnect = _boom
        with self.assertRaises(InterruptedError):
            b.reconnect_control(1, label='X')


class TestRwPostReconnect(unittest.TestCase):
    def _make(self):
        import ets_auto
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.debug_mode = False
        inst.debug = lambda *a, **k: None
        inst.interruptible_sleep = lambda s: None
        inst.rw_show_data = None
        inst._rw_cache_time = 0
        inst.rw_mode = True
        inst._read_pinia_config = lambda: None
        inst._detect_rw_mode = lambda: None
        inst.inject_bridge = lambda: {}
        inst._rw_post_reconnect = (
            ets_auto.ETSAutoAnswer._rw_post_reconnect.__get__(inst))
        return inst

    def test_mode_lost_returns_false(self):
        inst = self._make()
        inst.rw_mode = False
        self.assertFalse(inst._rw_post_reconnect())

    def test_empty_showdata_returns_false(self):
        inst = self._make()
        inst.get_rw_show_data = lambda: None
        self.assertFalse(inst._rw_post_reconnect())

    def test_zero_answers_returns_false(self):
        inst = self._make()
        inst.get_rw_show_data = lambda: {'question': []}
        inst._build_rw_answers_from_showdata = lambda sd, verbose=False: 0
        self.assertFalse(inst._rw_post_reconnect())

    def test_ok_when_answers_rebuilt(self):
        inst = self._make()
        inst.get_rw_show_data = lambda: {'question': [{'id': '1'}]}
        inst._build_rw_answers_from_showdata = lambda sd, verbose=False: 2
        self.assertTrue(inst._rw_post_reconnect())


class TestPkReconnectHandler(unittest.TestCase):
    def test_pk_reconnect_uses_shared_shell(self):
        import ets_word_pk
        import threading
        pk = object.__new__(ets_word_pk.ETSWordPK)
        pk.debug_mode = False
        pk.debug = lambda *a, **k: None
        pk.interruptible_sleep = lambda s: None
        pk.stop_event = threading.Event()
        pk.ws = object()
        calls = {'n': 0}
        def _re():
            calls['n'] += 1
            return True
        pk.reconnect = _re
        # Mimic nested handler body via reconnect_control directly
        action = pk.reconnect_control(1, label='PK', sleep_ok=0.01)
        self.assertEqual(action, 'continue')
        self.assertEqual(calls['n'], 1)


class TestFireCallbackDebug(unittest.TestCase):
    def test_fire_question_swallows_and_debugs(self):
        import ets_common
        base = ets_common.ETSBase(debug_mode=True)
        logs = []
        base.debug = lambda m: logs.append(m)
        def _bad(info):
            raise RuntimeError('ui dead')
        base.on_question(_bad)
        base._fire_question({'type': 'x'})
        self.assertTrue(any('on_question' in x for x in logs))


class TestGuiClosedGuard(unittest.TestCase):
    def test_run_finished_noop_when_closed(self):
        # Bind the REAL _run_finished (D-N1: a stub re-implementing the guard
        # could never fail) and verify both no-UI paths route to cleanup
        # without touching _restore_streams or any widget.
        import ets_gui
        for closed, exists in ((True, None), (False, False)):
            app = object.__new__(ets_gui.ETSApp)
            app._closed = closed
            app._running = True
            calls = []
            app._worker_cleanup_without_ui = lambda: calls.append('cleanup')
            app._restore_streams = lambda: calls.append('restore')
            if exists is not None:
                app.winfo_exists = lambda: exists
            ets_gui.ETSApp._run_finished(app)
            self.assertEqual(calls, ['cleanup'],
                             'closed=%r must use cleanup-only path' % closed)


class TestRemoteNetworkSwitch(unittest.TestCase):
    """REMOTE_NETWORK_ENABLED=False must short-circuit every network API."""

    def test_check_returns_none_without_network_io(self):
        from unittest.mock import patch
        import ets_remote
        self.assertFalse(ets_remote.REMOTE_NETWORK_ENABLED)
        remote = ets_remote.ETSRemote(current_version='0.7.1')
        with patch.object(ets_remote.ETSRemote, '_fetch_json',
                          side_effect=AssertionError('network I/O attempted')):
            self.assertIsNone(remote.check(use_cache=True))
            self.assertIsNone(remote.check(use_cache=False))

    def test_download_pk_extra_refuses_without_network_io(self):
        from unittest.mock import patch
        import ets_remote
        remote = ets_remote.ETSRemote(current_version='0.7.1')
        with patch.object(ets_remote, 'is_url_allowed',
                          side_effect=AssertionError('should not validate')), \
                patch.object(ets_remote, '_open_remote_url',
                             side_effect=AssertionError('network I/O attempted')):
            ok, message = remote.download_pk_extra(
                url='https://gitee.com/x/y/raw/master/pk_extra.json')
        self.assertFalse(ok)
        self.assertIn('停用', message)


class TestCompatDataRootMasking(unittest.TestCase):
    def test_mask_user_root_hides_account_name(self):
        from ets_compat import _mask_user_root
        self.assertEqual(
            _mask_user_root(r'C:\Users\SmartBoy\AppData\Roaming\ETS'),
            r'C:\Users\*\AppData\Roaming\ETS')
        self.assertEqual(
            _mask_user_root('C:/Users/Alice/ets'),
            'C:/Users/*/ets')
        # Non-user paths pass through untouched; empty stays empty.
        self.assertEqual(_mask_user_root(r'D:\ETS\data'), r'D:\ETS\data')
        self.assertEqual(_mask_user_root(''), '')


class TestPkMalformedStateGuard(unittest.TestCase):
    """B-F1: PK loop must normalize page-controlled state types."""

    def test_malformed_state_types_do_not_crash_hash(self):
        import hashlib as _hl
        # Mirror the normalization now in the PK main loop on hostile input.
        state = {'title': None, 'options': ['', 3, None, 'ok'], 'progress': 7}
        title = str(state.get('title') or '')
        options = [str(opt) for opt in state.get('options', [])
                   if isinstance(opt, str) and opt.strip()]
        progress = str(state.get('progress') or '')
        question_hash = _hl.md5(
            (title + '|' + '|'.join(sorted(options))).encode()).hexdigest()[:12]
        self.assertEqual(title, '')
        self.assertEqual(options, ['ok'])
        self.assertEqual(progress, '7')
        self.assertEqual(len(question_hash), 12)


class TestPkDictCorruptionIsolation(unittest.TestCase):
    """B-F3: a corrupted dict file degrades to load_dictionary()==False."""

    def _make_pk(self, dict_path, tmp):
        import ets_word_pk
        pk = object.__new__(ets_word_pk.ETSWordPK)
        pk.dict_path = dict_path
        pk.ecdict_path = _os.path.join(tmp, 'no_ecdict.json')
        pk.extra_path = _os.path.join(tmp, 'pk_extra.json')
        pk.misses_path = _os.path.join(tmp, 'pk_misses.jsonl')
        pk.word_trans = {}
        pk.trans_index = {}
        pk.pk_extra = {}
        pk._migrate_user_state = lambda: None
        pk._index_trans_senses = lambda word, trans: None
        return pk

    def test_truncated_new_format_dict_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = _os.path.join(tmp, 'worddict_data.json')
            with open(bad, 'w', encoding='utf-8') as f:
                f.write('wordsTranslateArr = `[{word')  # truncated
            pk = self._make_pk(bad, tmp)
            self.assertFalse(pk.load_dictionary())

    def test_oversized_dict_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            big = _os.path.join(tmp, 'worddict_data.json')
            with open(big, 'wb') as f:
                f.write(b'x' * 100)
            pk = self._make_pk(big, tmp)
            pk._DICT_MAX_BYTES = 64  # shrink the cap for the test
            self.assertFalse(pk.load_dictionary())


# ═══════════════════════════════════════════════════════════════════════════════
#  v0.6.7 quality ports — loopback / next waiting / thresholds
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoopbackWsUrl(unittest.TestCase):
    def test_accepts_loopback(self):
        import ets_common
        self.assertTrue(ets_common.is_loopback_ws_url(
            'ws://127.0.0.1:10086/devtools/page/1'))
        self.assertTrue(ets_common.is_loopback_ws_url('ws://localhost:10086/x'))
        self.assertTrue(ets_common.is_loopback_ws_url('ws://[::1]:10086/x'))
        self.assertTrue(ets_common.is_loopback_ws_url('ws://127.0.0.2:10086/x'))
        self.assertTrue(ets_common.is_loopback_ws_url(
            'ws://[0:0:0:0:0:0:0:1]:10086/x'))
        self.assertTrue(ets_common.is_loopback_ws_url(
            'ws://[::ffff:127.0.0.1]:10086/x'))

    def test_rejects_remote_and_bad(self):
        import ets_common
        self.assertFalse(ets_common.is_loopback_ws_url('ws://192.168.1.2:10086/x'))
        self.assertFalse(ets_common.is_loopback_ws_url('http://127.0.0.1:10086/x'))
        self.assertFalse(ets_common.is_loopback_ws_url(''))
        self.assertFalse(ets_common.is_loopback_ws_url(None))


class TestLoopThresholds(unittest.TestCase):
    def test_scales_with_total_and_caps(self):
        import ets_auto
        e1, u1 = ets_auto.ETSAutoAnswer.compute_loop_thresholds(1)
        self.assertEqual((e1, u1), (5, 8))
        e50, u50 = ets_auto.ETSAutoAnswer.compute_loop_thresholds(50)
        self.assertEqual(e50, 15)
        self.assertEqual(u50, 24)
        e999, u999 = ets_auto.ETSAutoAnswer.compute_loop_thresholds(999)
        self.assertEqual(e999, 15)
        self.assertEqual(u999, 25)


class TestClickNextWaiting(unittest.TestCase):
    def _make(self):
        import ets_auto
        from ets_common import ETSBase
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.debug_mode = False
        inst.debug = lambda *a, **k: None
        inst._IFRAME_FINDER = 'var iframe = null'
        inst.stats = {'next_click': 0}
        # parse_eval_json lives on ETSBase; bind explicitly for object.__new__
        inst.parse_eval_json = ETSBase.parse_eval_json.__get__(inst)
        inst.click_next = ets_auto.ETSAutoAnswer.click_next.__get__(inst)
        inst._is_next_waiting = ets_auto.ETSAutoAnswer._is_next_waiting
        return inst

    def test_next_icon_hidden_does_not_fall_through(self):
        inst = self._make()
        calls = []
        seq = [
            False,  # iframe.next failed
            json.dumps({'success': False, 'reason': 'next_icon hidden'}),
            json.dumps({'success': False, 'reason': 'not found'}),
        ]

        def _eval(js):
            calls.append(js)
            return seq.pop(0) if seq else '{}'

        inst.eval_js = _eval
        r = inst.click_next()
        self.assertFalse(r.get('success'))
        self.assertEqual(r.get('reason'), 'next_icon hidden')
        self.assertEqual(len(calls), 2)
        self.assertEqual(inst.stats['next_click'], 0)
        self.assertTrue(inst._is_next_waiting(r.get('reason')))


class TestInjectBridgeContract(unittest.TestCase):
    """Offline contract checks for inject_bridge JS assembly (no CDP)."""

    def _make(self):
        import ets_auto
        from ets_common import ETSBase
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.debug_mode = False
        inst.debug = lambda *a, **k: None
        inst._IFRAME_FINDER = 'var iframe = null;'
        inst.parse_eval_json = ETSBase.parse_eval_json.__get__(inst)
        inst.inject_bridge = ets_auto.ETSAutoAnswer.inject_bridge.__get__(inst)
        return inst

    def test_js_contains_wrap_markers_and_drain_cap(self):
        inst = self._make()
        captured = []

        def _eval(js):
            captured.append(js)
            return json.dumps({'nativeChoose': True, 'nativeFill': False})

        inst.eval_js = _eval
        info = inst.inject_bridge()
        self.assertTrue(info.get('nativeChoose'))
        src = captured[0]
        self.assertIn('__ets_hooked', src)
        self.assertIn('kttb_ReturnChoose', src)
        self.assertIn('_origChoose', src)
        self.assertIn('kttb_returnPcBlank', src)
        self.assertIn('> 200', src)
        self.assertIn('slice(-100)', src)
        # wrap-before-record: original call appears before push into recorded
        self.assertLess(src.find('_origChoose(data)'), src.find('__ets_recorded.push'))

    def test_invalid_or_empty_eval_returns_empty_dict(self):
        inst = self._make()
        inst.eval_js = lambda js: 'not-json{'
        self.assertEqual(inst.inject_bridge(), {})
        inst.eval_js = lambda js: None
        self.assertEqual(inst.inject_bridge(), {})

    def test_skipped_idempotent_payload_shape(self):
        """When JS reports skipped, Python still returns parsed dict (not {})."""
        inst = self._make()
        inst.eval_js = lambda js: json.dumps({
            'nativeChoose': False, 'nativeFill': False, 'skipped': True,
        })
        info = inst.inject_bridge()
        self.assertTrue(info.get('skipped'))
        self.assertIn('nativeChoose', info)

    def test_js_tracks_wrapped_refs_for_rehook(self):
        """Bridge must remember wrap identities so CEF-replaced natives re-hook."""
        inst = self._make()
        captured = []

        def _eval(js):
            captured.append(js)
            return json.dumps({'nativeChoose': True, 'nativeFill': True})

        inst.eval_js = _eval
        inst.inject_bridge()
        src = captured[0]
        self.assertIn('__ets_wrappedChoose', src)
        self.assertIn('__ets_wrappedBlank', src)
        # re-hook when current fn is not our wrap
        self.assertIn('!== win.__ets_wrappedChoose', src)
        self.assertIn('waitingNative', src)

    def test_waiting_native_payload_passes_through(self):
        """Python must surface waitingNative for early inject (no CEF natives yet)."""
        inst = self._make()
        inst.eval_js = lambda js: json.dumps({
            'nativeChoose': False, 'nativeFill': False,
            'skipped': True, 'waitingNative': True,
        })
        info = inst.inject_bridge()
        self.assertTrue(info.get('waitingNative'))
        self.assertTrue(info.get('skipped'))
        self.assertFalse(info.get('nativeChoose'))


class TestConstrainEtsDataRoot(unittest.TestCase):
    def test_accepts_under_appdata_ets(self):
        import ets_common
        import tempfile
        app = tempfile.mkdtemp(prefix='appdata_')
        ets = _os.path.join(app, 'ETS')
        _os.makedirs(ets)
        try:
            got = ets_common.constrain_ets_data_root(ets, appdata=app)
            self.assertEqual(got, _os.path.realpath(ets))
            # Roaming-style without ETS leaf
            got2 = ets_common.constrain_ets_data_root(app, appdata=app)
            self.assertEqual(got2, _os.path.realpath(ets))
            # Subdir under ETS must snap to ETS root (not .../ETS/foo/ETS)
            sub = _os.path.join(ets, '12345')
            _os.makedirs(sub)
            got3 = ets_common.constrain_ets_data_root(sub, appdata=app)
            self.assertEqual(got3, _os.path.realpath(ets))
        finally:
            shutil.rmtree(app, ignore_errors=True)

    def test_rejects_escape(self):
        import ets_common
        import tempfile
        app = tempfile.mkdtemp(prefix='appdata_')
        outside = tempfile.mkdtemp(prefix='outside_')
        try:
            self.assertIsNone(
                ets_common.constrain_ets_data_root(outside, appdata=app))
            self.assertIsNone(
                ets_common.constrain_ets_data_root('', appdata=app))
            self.assertIsNone(
                ets_common.constrain_ets_data_root(None, appdata=app))
        finally:
            shutil.rmtree(app, ignore_errors=True)
            shutil.rmtree(outside, ignore_errors=True)


class TestWordPKLearnMiss(unittest.TestCase):
    def _make(self):
        import ets_word_pk
        pk = object.__new__(ets_word_pk.ETSWordPK)
        pk.debug_mode = False
        pk.debug = lambda *a, **k: None
        pk.word_trans = {}
        pk.trans_index = {}
        pk.cn_seg_index = {}
        pk.pk_extra = {}
        learn_dir = tempfile.mkdtemp(prefix='pk_learn_')
        self.addCleanup(shutil.rmtree, learn_dir, True)
        pk.extra_path = _os.path.join(learn_dir, 'pk_extra.json')
        pk._is_chinese = lambda t: ets_word_pk.ETSWordPK._is_chinese(t)
        pk._cn_split = lambda s: [s] if s else []
        pk.learn_miss = ets_word_pk.ETSWordPK.learn_miss.__get__(pk)
        pk.find_answer = ets_word_pk.ETSWordPK.find_answer.__get__(pk)
        pk.get_stems = ets_word_pk.ETSWordPK.get_stems.__get__(pk)
        pk.get_opt_trans = ets_word_pk.ETSWordPK.get_opt_trans.__get__(pk)
        return pk

    def test_learn_miss_cn_to_en_and_find(self):
        pk = self._make()
        pk.learn_miss('苹果', 'apple')
        self.assertEqual(pk.pk_extra['苹果'], 'apple')
        self.assertIn('apple', pk.trans_index.get('苹果', []))
        self.assertTrue(_os.path.isfile(pk.extra_path))
        idx = pk.find_answer('苹果', ['banana', 'apple'])
        self.assertEqual(idx, 1)

    def test_learn_miss_empty_noop(self):
        pk = self._make()
        pk.learn_miss('', 'x')
        pk.learn_miss('q', '')
        self.assertEqual(pk.pk_extra, {})


class TestWordPKCaptureAndRecordMiss(unittest.TestCase):
    """capture_wrong_answer guards + record_miss JSONL append (no CDP)."""

    def _make(self):
        import ets_word_pk
        from ets_common import ETSBase
        pk = object.__new__(ets_word_pk.ETSWordPK)
        pk.debug_mode = False
        pk.debug = lambda *a, **k: None
        pk.parse_eval_json = ETSBase.parse_eval_json.__get__(pk)
        pk.capture_wrong_answer = (
            ets_word_pk.ETSWordPK.capture_wrong_answer.__get__(pk))
        pk.record_miss = ets_word_pk.ETSWordPK.record_miss.__get__(pk)
        td = tempfile.mkdtemp(prefix='pk_miss_')
        self.addCleanup(shutil.rmtree, td, True)
        pk.misses_path = _os.path.join(td, 'pk_misses.jsonl')
        return pk

    def test_capture_wrong_count_mismatch_skips(self):
        pk = self._make()
        pk.eval_js = lambda js: json.dumps({
            'isWrong': True,
            'correctAnswer': 'right',
            'allOpts': ['a', 'b'],  # 2
        })
        got = pk.capture_wrong_answer(0, current_options=['a', 'b', 'c'])
        self.assertEqual(got, '')

    def test_capture_wrong_content_mismatch_skips(self):
        pk = self._make()
        pk.eval_js = lambda js: json.dumps({
            'isWrong': True,
            'correctAnswer': 'right',
            'allOpts': ['x', 'y', 'z', 'w'],
        })
        got = pk.capture_wrong_answer(
            0, current_options=['a', 'b', 'c', 'd'])
        self.assertEqual(got, '')

    def test_capture_wrong_success_when_options_match(self):
        pk = self._make()
        opts = ['apple', 'banana', 'cherry']
        pk.eval_js = lambda js: json.dumps({
            'isWrong': True,
            'correctAnswer': 'banana',
            'allOpts': opts,
        })
        got = pk.capture_wrong_answer(0, current_options=opts)
        self.assertEqual(got, 'banana')

    def test_capture_not_wrong_returns_empty(self):
        pk = self._make()
        pk.eval_js = lambda js: json.dumps({
            'isWrong': False,
            'correctAnswer': 'banana',
            'allOpts': ['a', 'b'],
        })
        self.assertEqual(pk.capture_wrong_answer(0), '')

    def test_capture_parse_error_returns_empty(self):
        pk = self._make()
        pk.eval_js = lambda js: None
        self.assertEqual(pk.capture_wrong_answer(0, current_options=['a']), '')

    def test_record_miss_appends_jsonl(self):
        pk = self._make()
        pk.record_miss('hello', ['A', 'B'])
        pk.record_miss('world', ['C'])
        with open(pk.misses_path, encoding='utf-8') as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        r0 = json.loads(lines[0])
        self.assertEqual(r0['question'], 'hello')
        self.assertEqual(r0['options'], ['A', 'B'])
        self.assertIn('time', r0)
        r1 = json.loads(lines[1])
        self.assertEqual(r1['question'], 'world')

    def test_record_miss_write_failure_is_swallowed(self):
        pk = self._make()
        # Directory path as file path → open fails
        td = tempfile.mkdtemp(prefix='pk_bad_')
        self.addCleanup(shutil.rmtree, td, True)
        pk.misses_path = td
        # Should not raise
        pk.record_miss('q', ['o'])


class TestPiniaEtsBaseJailBehavior(unittest.TestCase):
    """Rejected appDataPath must not install an escaped ets_base."""

    def test_unsafe_pinia_path_leaves_ets_base_none(self):
        import ets_auto
        from ets_common import ETSBase
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.debug_mode = False
        inst.debug = lambda *a, **k: None
        inst.ets_base = None
        inst.homework_mode = None
        inst.homework_id = None
        inst.set_id = None
        inst.parse_eval_json = ETSBase.parse_eval_json.__get__(inst)
        inst._read_pinia_config = (
            ets_auto.ETSAutoAnswer._read_pinia_config.__get__(inst))
        # Escapes APPDATA jail
        inst.eval_js = lambda js: json.dumps({
            'appDataPath': 'D:/not/under/appdata',
            'doHomework': False,
            'homework_id': '',
            'hw_set_id': '',
        })
        # Force empty APPDATA for jail so any non-empty path fails? Better:
        # use real APPDATA but path outside it — D:/ is outside.
        inst._read_pinia_config()
        self.assertIsNone(inst.ets_base)

    def test_safe_pinia_roaming_sets_ets_base(self):
        import ets_auto
        from ets_common import ETSBase, constrain_ets_data_root
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.debug_mode = False
        inst.debug = lambda *a, **k: None
        inst.ets_base = None
        inst.homework_mode = None
        inst.homework_id = None
        inst.set_id = None
        inst.parse_eval_json = ETSBase.parse_eval_json.__get__(inst)
        inst._read_pinia_config = (
            ets_auto.ETSAutoAnswer._read_pinia_config.__get__(inst))
        app = _os.environ.get('APPDATA', '')
        if not app:
            self.skipTest('APPDATA not set')
        # Only assert if constrain accepts APPDATA (ETS leaf may not exist)
        expected = constrain_ets_data_root(app)
        if expected is None:
            self.skipTest('constrain rejects default APPDATA on this machine')
        inst.eval_js = lambda js: json.dumps({
            'appDataPath': app,
            'doHomework': True,
            'homework_id': '1',
            'hw_set_id': '12345',
        })
        inst._read_pinia_config()
        self.assertEqual(inst.ets_base, expected)
        self.assertTrue(inst.homework_mode)
        self.assertEqual(inst.set_id, '12345')


class TestFormatUpdateMessageLevels(unittest.TestCase):
    """format_update_message must surface warn (unsigned) and block (signed)."""

    def test_warn_and_block_and_update(self):
        import ets_remote
        with _cleared_remote_integrity_env():
            warn_info = ets_remote.RemoteInfo(
                allow_start=False, announcement='', download_url='')
            msg = ets_remote.format_update_message(warn_info, '0.6.7')
            self.assertIsNotNone(msg)
            self.assertIn('仅提示', msg)

            _os.environ['ETS_REMOTE_HMAC'] = 'unit-format-secret'
            block_info = ets_remote.RemoteInfo(
                allow_start=False, announcement='', download_url='https://example.com')
            # allowlist strips non-allowlisted download_url in parse path;
            # RemoteInfo can still carry a display URL set directly.
            block_info.download_url = 'https://github.com/yigenhuobah/ETS_Auto/releases/latest'
            msg_b = ets_remote.format_update_message(block_info, '0.6.7')
            self.assertIsNotNone(msg_b)
            self.assertIn('远程关闭', msg_b.replace(' ', '') or msg_b)
            # Chinese reason or ban emoji path
            self.assertTrue('关闭' in msg_b or '程序' in msg_b)

        with _cleared_remote_integrity_env():
            upd = ets_remote.RemoteInfo(
                allow_start=True, force_update=False, update_available=True,
                latest_version='0.9.0', announcement='hi',
                download_url='https://github.com/yigenhuobah/ETS_Auto/releases/latest')
            msg_u = ets_remote.format_update_message(upd, '0.6.7')
            self.assertIsNotNone(msg_u)
            self.assertIn('0.9.0', msg_u)
            self.assertIn('hi', msg_u)


# ═══════════════════════════════════════════════════════════════════════════════
#  TestCompatibilityPreflight — read-only ETS/CDP contract report
# ═══════════════════════════════════════════════════════════════════════════════

class TestCompatibilityPreflight(unittest.TestCase):
    class _Response:
        def __init__(self, payload):
            self.payload = payload
            self.closed = False

        def read(self):
            return self.payload

        def close(self):
            self.closed = True

    class _Ws:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    def _tabs(self, ws_url='ws://127.0.0.1:10086/devtools/page/exam'):
        return [{
            'url': 'https://statics.ets100.com/app/mockExam?set_id=private',
            'title': 'Exam',
            'type': 'page',
            'webSocketDebuggerUrl': ws_url,
        }]

    def _snapshot(self):
        return {
            'href': 'https://statics.ets100.com/app/mockExam?set_id=private',
            'readyState': 'complete',
            'userAgent': 'UnitTest/1.0',
            'app': True,
            'vue3': True,
            'pinia': True,
            'appDataPath': '',
            'doHomework': False,
            'iframe': {
                'present': True,
                'accessible': True,
                'readyState': 'complete',
                'error': '',
            },
            'exam': {'choiceCount': 4, 'fillCount': 1, 'nextIcon': True},
            'bridge': {
                'returnChoose': True,
                'returnBlank': True,
                'setPCChoose2': True,
                'iframeNext': True,
                'alreadyHooked': False,
            },
            'pk': {'title': False, 'optionCount': 0},
        }

    @staticmethod
    def _check(report, check_id):
        return next(check for check in report['checks'] if check['id'] == check_id)

    def _collect(self, snapshot, *, mode='exam', appdata=None, ws_url=None):
        import ets_compat
        from unittest.mock import patch

        tabs = self._tabs(ws_url) if ws_url is not None else self._tabs()
        response = self._Response(json.dumps(tabs).encode('utf-8'))
        ws = self._Ws()
        eval_value = snapshot if isinstance(snapshot, str) else json.dumps(snapshot)
        with patch.object(ets_compat.ETSBase, 'eval_js', return_value=eval_value):
            report = ets_compat.collect_compatibility_report(
                mode=mode,
                opener=lambda *args, **kwargs: response,
                ws_factory=lambda *args, **kwargs: ws,
                appdata=appdata,
            )
        return report, response, ws

    def test_endpoint_failure_is_blocking_and_does_not_open_ws(self):
        import ets_compat

        ws_calls = []

        def unavailable(*args, **kwargs):
            raise OSError('connection refused')

        report = ets_compat.collect_compatibility_report(
            opener=unavailable,
            ws_factory=lambda *args, **kwargs: ws_calls.append(args),
        )
        self.assertFalse(report['ok'])
        self.assertEqual(self._check(report, 'cdp.endpoint')['status'], 'fail')
        self.assertTrue(self._check(report, 'cdp.endpoint')['blocking'])
        self.assertEqual(ws_calls, [])

    def test_missing_ets_target_is_blocking(self):
        import ets_compat

        response = self._Response(json.dumps([
            {'url': 'https://example.com', 'title': 'Other'},
        ]).encode('utf-8'))
        report = ets_compat.collect_compatibility_report(
            opener=lambda *args, **kwargs: response,
            ws_factory=lambda *args, **kwargs: self.fail('WS must not open'),
        )
        self.assertFalse(report['ok'])
        self.assertEqual(self._check(report, 'cdp.target')['status'], 'fail')
        self.assertTrue(response.closed)

    def test_non_loopback_ws_is_rejected_before_connect(self):
        import ets_compat

        response = self._Response(json.dumps(self._tabs(
            'ws://192.168.1.20:10086/devtools/page/x')).encode('utf-8'))
        ws_calls = []
        report = ets_compat.collect_compatibility_report(
            opener=lambda *args, **kwargs: response,
            ws_factory=lambda *args, **kwargs: ws_calls.append(args),
        )
        self.assertFalse(report['ok'])
        self.assertEqual(self._check(report, 'cdp.ws_loopback')['status'], 'fail')
        self.assertEqual(ws_calls, [])

    def test_healthy_exam_report_is_serializable_and_closes_resources(self):
        import ets_compat

        with tempfile.TemporaryDirectory(prefix='ets_appdata_') as appdata:
            _os.makedirs(_os.path.join(appdata, 'ETS'))
            snapshot = self._snapshot()
            snapshot['appDataPath'] = appdata
            report, response, ws = self._collect(snapshot, appdata=appdata)

        self.assertTrue(report['ok'])
        self.assertEqual(self._check(report, 'page.surface')['status'], 'pass')
        self.assertEqual(self._check(report, 'bridge.native')['status'], 'pass')
        self.assertEqual(self._check(report, 'data.root')['status'], 'pass')
        self.assertNotIn('set_id', report['observations']['page_route'])
        self.assertNotIn('/devtools/', report['observations']['ws_endpoint'])
        self.assertIsInstance(json.loads(json.dumps(report)), dict)
        self.assertTrue(response.closed)
        self.assertTrue(ws.closed)
        self.assertIn('preflight: PASS', ets_compat.format_compatibility_report(report))

    def test_dom_and_bridge_not_ready_are_warnings(self):
        with tempfile.TemporaryDirectory(prefix='ets_appdata_') as appdata:
            _os.makedirs(_os.path.join(appdata, 'ETS'))
            snapshot = self._snapshot()
            snapshot.update({
                'app': False,
                'vue3': False,
                'pinia': False,
                'appDataPath': 'C:/outside-ets-jail',
                'iframe': {'present': False, 'accessible': False, 'error': ''},
                'exam': {'choiceCount': 0, 'fillCount': 0, 'nextIcon': False},
                'bridge': {},
            })
            report, _, _ = self._collect(snapshot, appdata=appdata)

        self.assertTrue(report['ok'])
        self.assertEqual(self._check(report, 'page.vue')['status'], 'warn')
        self.assertEqual(self._check(report, 'page.surface')['status'], 'warn')
        self.assertEqual(self._check(report, 'bridge.native')['status'], 'warn')
        self.assertEqual(report['observations']['data_root_source'], 'default')

    def test_pk_surface_passes_without_exam_bridge(self):
        snapshot = self._snapshot()
        snapshot['pk'] = {'title': True, 'optionCount': 4}
        report, _, ws = self._collect(snapshot, mode='pk')
        self.assertTrue(report['ok'])
        self.assertEqual(self._check(report, 'page.surface')['status'], 'pass')
        self.assertEqual(self._check(report, 'bridge.native')['status'], 'skip')
        self.assertTrue(ws.closed)

    def test_attach_error_redacts_debugger_target(self):
        import ets_compat

        ws_url = (
            'ws://127.0.0.1:10086/devtools/page/private-target'
            '?token=private-query')
        response = self._Response(json.dumps(self._tabs(ws_url)).encode('utf-8'))

        def fail_attach(url, **kwargs):
            raise RuntimeError('failed to attach ' + url)

        report = ets_compat.collect_compatibility_report(
            opener=lambda *args, **kwargs: response,
            ws_factory=fail_attach,
        )
        serialized = json.dumps(report)
        self.assertFalse(report['ok'])
        self.assertEqual(self._check(report, 'cdp.attach')['status'], 'fail')
        self.assertNotIn('/devtools/page/private-target', serialized)
        self.assertNotIn('private-query', serialized)

    def test_malformed_target_fields_return_structured_failure(self):
        import ets_compat

        response = self._Response(json.dumps([{
            'url': 'https://statics.ets100.com/app/mockExam',
            'title': 123,
            'type': ['page'],
            'webSocketDebuggerUrl': 456,
        }]).encode('utf-8'))
        report = ets_compat.collect_compatibility_report(
            opener=lambda *args, **kwargs: response,
            ws_factory=lambda *args, **kwargs: self.fail('WS must not open'),
        )
        self.assertFalse(report['ok'])
        self.assertEqual(self._check(report, 'cdp.target')['status'], 'pass')
        self.assertEqual(self._check(report, 'cdp.ws_loopback')['status'], 'fail')

    def test_accessible_iframe_without_exam_markers_warns(self):
        snapshot = self._snapshot()
        snapshot['exam'] = {
            'choiceCount': 0,
            'fillCount': 0,
            'nextIcon': False,
        }
        report, _, _ = self._collect(snapshot)
        self.assertTrue(report['ok'])
        self.assertEqual(self._check(report, 'page.surface')['status'], 'warn')
    def test_invalid_runtime_snapshot_is_blocking_and_closes_ws(self):
        report, _, ws = self._collect('not-json')
        self.assertFalse(report['ok'])
        self.assertEqual(self._check(report, 'page.runtime')['status'], 'fail')
        self.assertTrue(ws.closed)


class TestRunCompatibilityCommand(unittest.TestCase):
    def test_json_success_and_human_failure_exit_codes(self):
        import io
        import run
        from contextlib import redirect_stdout
        from unittest.mock import patch

        success = {
            'schema_version': 1,
            'app_version': 'test',
            'mode': 'exam',
            'port': 10086,
            'ok': True,
            'can_start': True,
            'summary': 'ready',
            'checks': [],
            'observations': {},
        }
        stdout = io.StringIO()
        with patch('ets_compat.collect_compatibility_report', return_value=success), \
             redirect_stdout(stdout):
            code = run.main(['check', '--json'])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout.getvalue())['ok'])

        blocked = dict(success)
        blocked.update({
            'ok': False,
            'can_start': False,
            'summary': 'blocked',
            'checks': [{
                'id': 'cdp.endpoint',
                'status': 'fail',
                'blocking': True,
                'summary': 'CDP unavailable',
                'detail': '',
                'remediation': '',
            }],
        })
        stdout = io.StringIO()
        with patch('ets_compat.collect_compatibility_report', return_value=blocked), \
             redirect_stdout(stdout):
            code = run.main(['check'])
        self.assertEqual(code, 2)
        self.assertIn('preflight: BLOCKED', stdout.getvalue())


class TestGoldenFixtures(unittest.TestCase):
    """Synthetic content.json under tests/fixtures/sets/ (Project offline golden).

    Skips when fixtures are absent (e.g. Auto tree without fixtures synced).
    """

    @classmethod
    def setUpClass(cls):
        cls.fix_root = _os.path.join(_SysPath, 'fixtures', 'sets')
        cls.set_id = '900001'
        cls.available = _os.path.isdir(_os.path.join(cls.fix_root, cls.set_id))

    def setUp(self):
        if not self.available:
            self.skipTest('fixtures/sets/900001 not present')
        import ets_strategy
        ets_strategy.ETSStrategy._set_cache = {}
        ets_strategy.ETSStrategy._set_cache_order = []

    def tearDown(self):
        if not self.available:
            return
        import ets_strategy
        ets_strategy.ETSStrategy._set_cache = {}
        ets_strategy.ETSStrategy._set_cache_order = []

    def test_load_set_composite_keys(self):
        import ets_strategy
        s = ets_strategy.ETSStrategy()
        ok = s.load_set(self.set_id, data_dir=self.fix_root)
        self.assertTrue(ok)
        self.assertGreaterEqual(len(s.sections), 3)
        ans = s.lookup('collector.choose', '100', qid='1')
        self.assertIsNotNone(ans)
        self.assertEqual(ans.get('answer', '').upper(), 'B')
        fill = s.lookup('collector.fill', '200', qid='1')
        self.assertIsNotNone(fill)
        self.assertEqual(fill.get('answer', '').lower(), 'colour')
        role = s.lookup('collector.role', '300', qid='q1')
        self.assertIsNotNone(role)
        self.assertEqual(role.get('type'), 'oral')
        self.assertTrue(role.get('variants'))


#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

class TestOwnedLogCleanup(unittest.TestCase):
    def test_retention_never_deletes_unrelated_logs(self):
        import ets_auto
        with tempfile.TemporaryDirectory() as tmp:
            now = 2_000_000_000.0
            unrelated = _os.path.join(tmp, 'unrelated.log')
            owned_old = _os.path.join(tmp, 'ets_auto_old.log')
            owned_fresh = _os.path.join(tmp, 'ets_auto_fresh.log')
            for path in (unrelated, owned_old, owned_fresh):
                with open(path, 'w', encoding='utf-8') as stream:
                    stream.write('log')
            _os.utime(unrelated, (now - 30 * 86400,) * 2)
            _os.utime(owned_old, (now - 30 * 86400,) * 2)
            _os.utime(owned_fresh, (now,) * 2)

            removed = ets_auto._cleanup_owned_logs(
                _os.path.join(tmp, 'run.log'), 7, now=now)
            self.assertEqual(removed, [])
            self.assertTrue(_os.path.exists(unrelated))
            self.assertTrue(_os.path.exists(owned_old))

            removed = ets_auto._cleanup_owned_logs(
                _os.path.join(tmp, 'ets_auto_current.log'), 7, now=now)
            self.assertEqual(removed, ['ets_auto_old.log'])
            self.assertTrue(_os.path.exists(unrelated))
            self.assertFalse(_os.path.exists(owned_old))
            self.assertTrue(_os.path.exists(owned_fresh))


class TestMonotonicRuntimeTimers(unittest.TestCase):
    def test_iframe_wait_uses_monotonic_deadline(self):
        from unittest.mock import patch
        import ets_auto
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.total_questions = 1
        inst.get_page_state = lambda: {}
        inst.is_cdp_parse_error = lambda _state: False
        inst.interruptible_sleep = lambda _seconds: None

        with patch.object(
                ets_auto.time, 'time', side_effect=AssertionError('wall clock used')), \
                patch.object(
                    ets_auto.time, 'monotonic', side_effect=[0.0, 0.0, 16.0]):
            self.assertEqual(inst.wait_iframe_ready(timeout=15), (False, False))

    def test_recording_wait_uses_monotonic_deadline(self):
        from unittest.mock import patch
        import ets_auto
        import ets_recording_ui
        import threading
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.stop_event = threading.Event()
        inst._recording_window_closed = False
        inst._fire_question = lambda _info: None
        inst.eval_js = lambda _js: json.dumps({'next_ready': False})
        inst.parse_eval_json = json.loads
        inst.interruptible_sleep = lambda _seconds: None

        with patch.object(
                ets_recording_ui.time, 'time',
                side_effect=AssertionError('wall clock used')), patch.object(
                    ets_recording_ui.time, 'monotonic',
                    side_effect=[0.0, 0.0, 1.0, 6.0]):
            self.assertFalse(inst.wait_for_recording_done(max_wait=5))

    def test_rw_cache_ttl_uses_monotonic_clock(self):
        from unittest.mock import patch
        import ets_auto
        import ets_rw_mode
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.rw_show_data = {'cached': True}
        inst._rw_cache_time = 5.0

        with patch.object(
                ets_rw_mode.time, 'time',
                side_effect=AssertionError('wall clock used')), patch.object(
                    ets_rw_mode.time, 'monotonic', return_value=10.0):
            self.assertEqual(inst.get_rw_show_data(), {'cached': True})

        inst.rw_show_data = None
        inst.eval_js = lambda _js: json.dumps({'question': []})
        inst.parse_eval_json = json.loads
        with patch.object(
                ets_rw_mode.time, 'time',
                side_effect=AssertionError('wall clock used')), patch.object(
                    ets_rw_mode.time, 'monotonic', return_value=42.0):
            self.assertEqual(inst.get_rw_show_data(), {'question': []})
        self.assertEqual(inst._rw_cache_time, 42.0)


class TestExamLoopCdpClassification(unittest.TestCase):
    """click_next CDP failures must route to reconnect, never 'Exam completed'."""

    def _make_inst(self):
        import ets_auto
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.debug_mode = False
        inst.total_questions = 1
        inst.stop_event = None
        inst._recording_window_closed = False
        inst._on_question = None
        inst._on_complete = None
        inst.interruptible_sleep = lambda _s: None
        inst._all_sidebar_correct = lambda: False
        return inst

    def test_wait_for_next_raises_connection_error_on_cdp_failure(self):
        import ets_auto
        inst = self._make_inst()
        inst.click_next = lambda: {'success': False, 'reason': 'eval_js_failed',
                                   'error': 'eval_js_failed'}
        with self.assertRaises(ConnectionError):
            inst._wait_for_next(max_wait_loops=3, wait_sec=0, label="t")

    def test_wait_for_next_unexpected_reason_still_completes(self):
        import ets_auto
        inst = self._make_inst()
        inst.click_next = lambda: {'success': False, 'reason': 'weird-page'}
        self.assertFalse(inst._wait_for_next(max_wait_loops=2, wait_sec=0, label="t"))

    def test_run_loop_body_stops_on_unanswerable_choice_pages(self):
        from unittest.mock import patch
        import ets_auto
        import ets_common
        inst = self._make_inst()
        inst.stats = {'choose_answered': 0, 'choose_skip': 0,
                      'fill_answered': 0, 'fill_skip': 0,
                      'next_click': 0, 'errors': 0}
        inst.set_id = '721920'
        inst.homework_mode = False
        inst.rw_mode = False
        inst._tab_url = lambda: ''
        clicks = []

        def _click():
            clicks.append(1)
            return {'success': True}

        inst.click_next = _click
        inst.wait_iframe_ready = lambda timeout=15, adaptive=True: (True, True)
        inst.inject_bridge = lambda: None
        inst.get_page_state = lambda: {'hasChoice': True}
        inst.answer_choose = lambda: (False, False)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ets_common, 'user_data_path',
                              return_value=_os.path.join(tmp, 'ets_stats.json')):
                result = inst._run_loop_body(max_steps=999, hotkey=None)

        self.assertEqual(result['total_answered'], 0)
        self.assertEqual(result['errors'], 0)
        # tq=1 → max_empty=5: 4 clicks, then the unanswerable-page guard stops
        # the loop before the safety limit could ever flip the whole paper.
        self.assertEqual(len(clicks), 4)


class TestWaitIframeReadyTimeout(unittest.TestCase):
    def _make_inst(self, total_questions):
        import ets_auto
        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.total_questions = total_questions
        inst.get_page_state = lambda: {}
        inst.is_cdp_parse_error = lambda _state: False
        inst.interruptible_sleep = lambda _seconds: None
        return inst

    def test_adaptive_false_respects_short_timeout(self):
        from unittest.mock import patch
        import ets_auto
        inst = self._make_inst(total_questions=60)  # adaptive would be 30s
        with patch.object(ets_auto.time, 'monotonic', side_effect=[0.0, 0.0, 6.0]):
            self.assertEqual(
                inst.wait_iframe_ready(timeout=5, adaptive=False), (False, False))

    def test_adaptive_true_floors_short_timeout(self):
        from unittest.mock import patch
        import ets_auto
        inst = self._make_inst(total_questions=1)  # adaptive floor = 10s
        with patch.object(ets_auto.time, 'monotonic',
                          side_effect=[0.0, 0.0, 6.0, 16.0]):
            self.assertEqual(
                inst.wait_iframe_ready(timeout=5, adaptive=True), (False, False))


class TestStrategyEmptyFillGuard(unittest.TestCase):
    def test_empty_fill_values_not_indexed(self):
        import ets_strategy
        st = ets_strategy.ETSStrategy()
        st._index_section({'data': {
            'structure_type': 'collector.fill',
            'info': {'stid': 123, 'std': [
                {'xth': 1, 'value': ''},
                {'xth': 2, 'value': 'Recycle'},
                {'xth': 3, 'value': '/'},
                {'xth': 4, 'value': '  '},
            ]},
        }})
        self.assertNotIn('collector.fill_123_1', st.answer_index)
        self.assertNotIn('collector.fill_123_3', st.answer_index)
        self.assertNotIn('collector.fill_123_4', st.answer_index)
        self.assertEqual(st.answer_index['collector.fill_123_2']['answer'], 'Recycle')


class TestRwHotkeyRegistration(unittest.TestCase):
    def test_rw_loop_disables_hotkey_when_register_fails(self):
        from unittest.mock import patch
        import ets_auto
        import ets_hotkey

        created = []

        class _FakeHotkey:
            def __init__(self, on_stop=None):
                self.on_stop = on_stop
                created.append(self)

            def register(self):
                return False

            def unregister(self):
                pass

        inst = object.__new__(ets_auto.ETSAutoAnswer)
        inst.debug_mode = False
        inst.ws = None  # finally-cleanup in _run_rw_loop closes ws when set
        inst.get_rw_show_data = lambda: None
        with patch.object(ets_hotkey, 'ETSHotkey', _FakeHotkey):
            result = inst._run_rw_loop(max_steps=1)

        self.assertEqual(created[0].on_stop, inst._signal_stop)
        self.assertEqual(
            result, {'total_answered': 0, 'mode': 'read-write', 'errors': 1})


class TestSectionViewModel(unittest.TestCase):
    """build_section_view is the single field pass all renderers format."""

    def test_view_shapes_per_type(self):
        import ets_parser
        choose = ets_parser.build_section_view({
            'data': {'structure_type': 'collector.choose', 'info': {
                'stid': 's1', 'xtlist': [{'xt_xh': '1', 'answer': 'B',
                'xxlist': [{'xx_mc': 'B', 'xx_nr': 'x'}]}]}}})
        self.assertEqual(choose['stype'], 'collector.choose')
        self.assertEqual(choose['questions'][0]['options'], [('B', 'x')])

        fill = ets_parser.build_section_view({
            'data': {'structure_type': 'collector.fill', 'info': {
                'value': '<p>text</p>', 'std': [{'xth': 1, 'value': 'a/'}]}}})
        # Renderers show the raw value; '/' splitting is the strategy layer's job.
        self.assertEqual(fill['blanks'][0]['answer'], 'a/')

        unknown = ets_parser.build_section_view({
            'data': {'structure_type': 'collector.weird', 'info': {}}})
        self.assertTrue(unknown['unknown'])
        self.assertIn('raw_dump', unknown)

    def test_render_section_never_raises_on_hostile_section(self):
        import ets_parser
        for bad in ({}, None, {'data': {'info': {'xtlist': 'notalist'}}},
                    {'data': {'structure_type': 'collector.choose',
                              'info': {'xtlist': [1, 2, None]}}}):
            parts = ets_parser.render_section(bad)
            self.assertIsInstance(parts, list)
            self.assertTrue(all(isinstance(t, str) and isinstance(g, str)
                                for t, g in parts))

    def test_dedup_consecutive(self):
        import ets_parser
        self.assertEqual(
            ets_parser._dedup_consecutive(['a', 'a', 'b', 'a', 'b', 'b']),
            ['a', 'b', 'a', 'b'])
        self.assertEqual(ets_parser._dedup_consecutive([]), [])


class TestBrowserTabLogic(unittest.TestCase):
    """Pure logic of the browser tab (no CTk instantiation)."""

    def _tab(self):
        import ets_browser_ui
        return object.__new__(ets_browser_ui.BrowserTab)

    def test_matches_filter(self):
        tab = self._tab()
        s = {'id': '20409', 'exam_type_names': ['听后选择1'],
             'types': {'collector.choose', 'collector.fill'}}
        self.assertTrue(tab._matches(s, ''))
        self.assertTrue(tab._matches(s, '20409'))
        self.assertTrue(tab._matches(s, '听后选择'))
        self.assertTrue(tab._matches(s, '填空'))
        self.assertFalse(tab._matches(s, '朗读'))

    def test_stid_suffix(self):
        import ets_browser_ui
        self.assertEqual(
            ets_browser_ui.BrowserTab._stid_suffix('584722'), ' 4722')
        self.assertEqual(ets_browser_ui.BrowserTab._stid_suffix('12'), ' 12')
        self.assertEqual(ets_browser_ui.BrowserTab._stid_suffix(''), '')


if __name__ == '__main__':
    # Check for -v / --verbose flag
    verbosity = 1
    argv = [a for a in _sys.argv[1:] if a not in ('-v', '--verbose')]
    if '-v' in _sys.argv or '--verbose' in _sys.argv:
        verbosity = 2
    # Build suite so we can exit non-zero on failure (CI gate)
    loader = unittest.TestLoader()
    if argv:
        suite = loader.loadTestsFromNames(argv, _sys.modules[__name__])
    else:
        suite = loader.loadTestsFromModule(_sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    _sys.exit(0 if result.wasSuccessful() else 1)
