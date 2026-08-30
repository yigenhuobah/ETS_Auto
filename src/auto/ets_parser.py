#!/usr/bin/env python3
"""
ETS Parser — Offline exam paper browser for ETS cached data.

Scans %APPDATA%\\ETS for locally cached exam papers and displays
them in a CustomTkinter GUI with answers highlighted.

Supports question types:
  - collector.choose   → Multiple choice (A/B/C)
  - collector.fill     → Fill-in-the-blank (standard answers)
  - collector.role     → Oral response (acceptable answer variants)
  - collector.dialogue → Dialogue-based oral Q&A (same structure as role)
  - collector.picture  → Picture description (model answers)
  - collector.read     → Read aloud (passage text)

Usage:
  python ets_parser.py          # standalone
  Integrated as Tab 2 in ets_gui.py
"""
import sys
import os
import json
import re
import html
from ets_common import normalize_ets_content

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

# ── Force UTF-8 on Windows (shared helper) ──────────────────
try:
    from ets_common import force_utf8_stdio
    force_utf8_stdio()
except Exception:
    if sys.platform == 'win32':
        os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

# ── ETS data directory ──────────────────────────────────────
ETS_DATA_DIR = os.path.join(os.environ.get('APPDATA', ''), 'ETS')

# ── Type labels & short labels ──────────────────────────────
TYPE_LABELS = {
    'collector.choose':  '选择题',
    'collector.fill':    '填空题',
    'collector.role':    '口语问答',
    'collector.dialogue': '对话问答',
    'collector.picture': '图片描述',
    'collector.read':    '朗读',
}

TYPE_ICONS = {
    'collector.choose':  '📝',
    'collector.fill':    '✏️',
    'collector.role':    '🗣️',
    'collector.dialogue': '💬',
    'collector.picture': '🖼️',
    'collector.read':    '📖',
}


# ═══════════════════════════════════════════════════════════
#  Data layer — read & parse ETS cache
# ═══════════════════════════════════════════════════════════

def _read_json(path):
    """Read JSON file, trying UTF-8 then gb18030."""
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8', 'gb18030'):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return json.loads(raw.decode('utf-8', errors='replace'))


def _html_to_text(html_str):
    """Convert HTML markup from ETS content to plain text.

    Uses a conservative tag-matching pattern that requires tags to start
    with a letter — avoids false matches on math text like "x < y".
    """
    if html_str is None:
        return ''
    if not isinstance(html_str, str):
        if not isinstance(html_str, (bool, int, float)):
            return ''
        html_str = str(html_str)
    if not html_str:
        return ''
    text = html_str
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<p\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '', text, flags=re.IGNORECASE)
    # Match only valid HTML tags: < followed by optional / then a letter
    text = re.sub(r'</?[a-zA-Z][^>]*>', '', text)
    text = html.unescape(text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _strip_template_prefix(text):
    """Remove ETS template variable prefixes like 'ets_th1', 'ets_sm1'."""
    if not text:
        return text
    return re.sub(r'^ets_\w+\d*\s*', '', text).strip()


# ═══════════════════════════════════════════════════════════

def scan_sets():
    """Scan ETS data directory and return list of exam set dicts.
    
    Reads template_*/res.json for rich metadata (score, exam type names).
    Falls back to content.json-only scan when res.json is absent.
    Sorts by score descending (full exams first, small exercises last).
    """
    if not os.path.isdir(ETS_DATA_DIR):
        return [], "ETS数据目录不存在：%s" % ETS_DATA_DIR

    sets = []
    for name in sorted(os.listdir(ETS_DATA_DIR)):
        set_path = os.path.join(ETS_DATA_DIR, name)
        if not os.path.isdir(set_path) or not name.isdigit():
            continue

        sections = []
        total_q = 0
        types = set()

        for entry in sorted(os.listdir(set_path)):
            if not entry.startswith('content_'):
                continue
            content_path = os.path.join(set_path, entry, 'content.json')
            if not os.path.exists(content_path):
                continue

            try:
                data = normalize_ets_content(_read_json(content_path))
            except Exception:
                continue
            if data is None:
                continue

            stype = data.get('structure_type', 'unknown')
            info = data.get('info', {})
            n_q = len(info.get('xtlist', []))
            std_count = len(info.get('std', []))
            q_count = len(info.get('question', []))
            total_q += max(n_q, std_count, q_count)
            types.add(stype)

            sections.append({
                'dir': entry,
                'type': stype,
                'stid': info.get('stid', ''),
                'data': data,
            })

        if not sections:
            continue

        # ── Read template res.json for rich metadata ──
        set_score = 0
        exam_type_names = []   # e.g. ["听后选择1", "听后选择2", "听后记录"]
        exam_type_tags = []    # e.g. [("听后选择1", "collector.choose"), ...]

        for entry in sorted(os.listdir(set_path)):
            if not entry.startswith('template_'):
                continue
            res_path = os.path.join(set_path, entry, 'res.json')
            if not os.path.exists(res_path):
                continue
            try:
                res = _read_json(res_path)
                set_score = int(res.get('set_score', 0) or 0)
                for et in res.get('exam_type_list', []):
                    etn = et.get('exam_type_name', '')
                    etc = et.get('exam_type_collect', '')
                    if etn:
                        exam_type_names.append(etn)
                        exam_type_tags.append((etn, etc))
            except Exception:
                pass
            break  # only read first template's res.json

        sets.append({
            'id': name,
            'path': set_path,
            'sections': sections,
            'total_questions': total_q,
            'types': types,
            'score': set_score,
            'exam_type_names': exam_type_names,
            'exam_type_tags': exam_type_tags,
        })

    # Sort: highest score first (full exams), then by ID descending
    sets.sort(key=lambda s: (-s['score'], -int(s['id'])))
    if not sets:
        return [], "ETS数据目录为空（%s），请先在ETS客户端中开始一次作业" % ETS_DATA_DIR
    return sets, None


def _dedup_consecutive(names):
    """Drop consecutive duplicate names (e.g. 听后选择1 appearing twice)."""
    deduped = []
    for n in names:
        if not deduped or deduped[-1] != n:
            deduped.append(n)
    return deduped


def build_section_view(section_data):
    """Normalize one section into a typed view model (single field pass).

    Every renderer (browser rich text, Markdown export, HTML export) formats
    this view instead of navigating ETS fields itself — adding a question
    type or a field name changes one place, not three.

    Returns a dict with 'stype'/'icon'/'label' plus type-specific keys, or
    'unknown': True with a bounded 'raw_dump' for unrecognized types.
    """
    raw_data = section_data.get('data', {}) if isinstance(section_data, dict) else {}
    data = normalize_ets_content(raw_data)
    if data is None:
        data = {'structure_type': 'unknown', 'info': {}}
    stype = data.get('structure_type', '')
    info = data.get('info', {})
    view = {
        'stype': stype,
        'icon': TYPE_ICONS.get(stype, '📋'),
        'label': TYPE_LABELS.get(stype, stype),
        'unknown': False,
    }

    if stype == 'collector.choose':
        questions = []
        for xt in info.get('xtlist', []):
            questions.append({
                'q_num': xt.get('xt_xh', ''),
                'q_text': _strip_template_prefix(_html_to_text(xt.get('xt_nr', ''))),
                'q_value': _html_to_text(xt.get('xt_value', '')),
                'answer': xt.get('answer', ''),
                'options': [(xx.get('xx_mc', ''), _html_to_text(xx.get('xx_nr', '')))
                            for xx in xt.get('xxlist', [])],
            })
        view['questions'] = questions

    elif stype == 'collector.fill':
        view['passage'] = _html_to_text(info.get('value', ''))
        view['blanks'] = [{
            'q_num': std.get('xth', std.get('th', '')),
            'answer': _html_to_text(std.get('value', '')),
            'ai': std.get('ai', ''),
        } for std in info.get('std', [])]

    elif stype in ('collector.role', 'collector.dialogue'):
        questions = []
        for qi, q in enumerate(info.get('question', []), 1):
            stds = q.get('std', [])
            variants = []
            for s in stds[:8]:
                val = _html_to_text(s.get('value', ''))
                if val not in variants:
                    variants.append(val)
            questions.append({
                'qi': qi,
                'ask': _strip_template_prefix(_html_to_text(q.get('ask', ''))),
                'keywords': q.get('keywords', ''),
                'variants': variants,
                'total_variants': len(stds),
            })
        view['passage'] = _html_to_text(info.get('value', ''))
        view['questions'] = questions

    elif stype == 'collector.picture':
        view['topic'] = info.get('topic', '')
        view['passage'] = _html_to_text(info.get('value', ''))
        view['keypoint'] = _html_to_text(info.get('keypoint', ''))
        view['image_name'] = info.get('image', '')
        view['answers'] = [_html_to_text(std.get('value', ''))
                           for std in info.get('std', [])]

    elif stype == 'collector.read':
        view['passage'] = _html_to_text(info.get('value', ''))

    else:
        view['unknown'] = True
        raw = json.dumps(info, ensure_ascii=False, indent=2)
        if len(raw) > 2000:
            raw = raw[:2000] + '\n...(truncated)'
        view['raw_dump'] = "%s\n" % raw

    return view


def render_section(section_data):
    """Render a content section into rich text for display.

    Returns a list of (text, tag) tuples where tag is '' for normal
    or 'answer'/'header'/'muted'/'q_num'/'option'/'section_title' for styled text.
    """
    try:
        view = build_section_view(section_data)
        return _format_section_richtext(view)
    except Exception:
        # One malformed section must not break the whole browser.
        return [("(本节无法解析)\n", 'muted')]


def _format_section_richtext(view):
    """Format a section view model as (text, tag) rich-text parts."""
    stype = view['stype']
    parts = [("%s %s\n\n" % (view['icon'], view['label']), 'section_title')]

    if view['unknown']:
        parts.append(("(未识别题型: %s)\n" % stype, 'muted'))
        parts.append((view['raw_dump'], 'muted'))
        return parts

    if stype == 'collector.choose':
        for q in view['questions']:
            parts.append(("题 %s" % q['q_num'], 'q_num'))
            parts.append(("  %s\n" % q['q_text'], ''))
            if q['q_value']:
                parts.append(("  听力原文：\n", 'muted'))
                for line in q['q_value'].split('\n'):
                    parts.append(("    %s\n" % line, 'muted'))
                parts.append(("\n", ''))
            for opt, opt_text in q['options']:
                if opt == q['answer']:
                    parts.append(("  ✓ %s. " % opt, 'answer'))
                    parts.append(("%s\n" % opt_text, 'answer'))
                else:
                    parts.append(("    %s. %s\n" % (opt, opt_text), 'option'))
            parts.append(("\n", ''))

    elif stype == 'collector.fill':
        passage = view['passage']
        if passage:
            parts.append(("短文/对话：\n", 'muted'))
            for line in passage.split('\n'):
                parts.append(("  %s\n" % line, ''))
            parts.append(("\n", ''))
        for b in view['blanks']:
            parts.append(("第%s空 → " % b['q_num'], 'q_num'))
            parts.append(("%s\n" % b['answer'], 'answer'))
            if b['ai'] and b['ai'] != b['answer']:
                parts.append(("  (AI识别: %s)\n" % b['ai'], 'muted'))
        parts.append(("\n", ''))

    elif stype in ('collector.role', 'collector.dialogue'):
        passage = view['passage']
        if passage:
            parts.append(("对话/材料：\n", 'muted'))
            for line in passage.split('\n'):
                parts.append(("  %s\n" % line, ''))
            parts.append(("\n", ''))
        for q in view['questions']:
            parts.append(("问 %d" % q['qi'], 'q_num'))
            parts.append(("  %s\n" % q['ask'], ''))
            if q['keywords']:
                parts.append(("  关键词：%s\n" % q['keywords'], 'muted'))
            if q['variants']:
                parts.append(("  可接受答案：\n", ''))
                for val in q['variants']:
                    parts.append(("    • %s\n" % val, 'answer'))
                if q['total_variants'] > 8:
                    parts.append(("    ... 共%d个变体\n" % q['total_variants'], 'muted'))
            parts.append(("\n", ''))

    elif stype == 'collector.picture':
        topic = view['topic']
        if topic:
            parts.append(("话题：%s\n\n" % topic, ''))
        passage = view['passage']
        if passage:
            parts.append(("原文：\n", 'muted'))
            for line in passage.split('\n'):
                parts.append(("  %s\n" % line, ''))
            parts.append(("\n", ''))
        keypoint = view['keypoint']
        if keypoint:
            parts.append(("要点：\n", 'muted'))
            for line in keypoint.split('\n'):
                parts.append(("  %s\n" % line, ''))
            parts.append(("\n", ''))
        for i, answer in enumerate(view['answers'], 1):
            parts.append(("参考答案 %d\n" % i, 'q_num'))
            parts.append(("  %s\n\n" % answer, 'answer'))

    elif stype == 'collector.read':
        passage = view['passage']
        if passage:
            parts.append(("朗读文本：\n", 'muted'))
            for line in passage.split('\n'):
                parts.append(("  %s\n" % line, ''))

    return parts


def _safe_material_image_path(set_path, sec_dir, img_name):
    """Resolve picture material path; reject traversal outside material/.

    Only basenames are accepted (no path separators / ..). Returns realpath
    when the file exists under material/, else None.
    """
    if not img_name or not isinstance(img_name, str):
        return None
    # Require a single path segment (no dirs / absolute / ..)
    normalized = img_name.replace('\\', '/')
    if '/' in normalized or normalized in ('.', '..'):
        return None
    base = os.path.basename(img_name)
    if not base or base in ('.', '..'):
        return None
    material_root = os.path.realpath(
        os.path.join(set_path or '', sec_dir or '', 'material'))
    candidate = os.path.realpath(os.path.join(material_root, base))
    try:
        common = os.path.commonpath([material_root, candidate])
    except ValueError:
        return None
    if common != material_root:
        return None
    if os.path.isfile(candidate):
        return candidate
    return None


def _render_full_markdown(set_data):
    """Render a full exam set as plain Markdown text (for export)."""
    import datetime
    lines = []
    title_parts = ["📄 %s" % set_data['id']]
    score = set_data.get('score', 0)
    if score:
        title_parts.append("%d分" % score)
    title_parts.append("%d题" % set_data['total_questions'])
    exam_names = set_data.get('exam_type_names', [])
    if exam_names:
        title_parts.append(' · '.join(_dedup_consecutive(exam_names)))
    else:
        title_parts.append(' · '.join(TYPE_LABELS.get(t, t) for t in sorted(set_data['types'])))
    lines.append("# " + ' · '.join(title_parts))
    lines.append("")
    lines.append("*导出时间: %s*" % datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
    lines.append("")

    for sec in set_data['sections']:
        if not isinstance(sec, dict):
            continue
        try:
            view = build_section_view(sec)
        except Exception:
            lines.append("## (本节无法解析)")
            lines.append("")
            continue
        stype = view['stype']
        lines.append("## %s %s" % (view['icon'], view['label']))
        lines.append("")
        if view['unknown']:
            lines.append("_（未识别题型: %s）_" % stype)
            lines.append("")
            continue

        if stype == 'collector.choose':
            for q in view['questions']:
                lines.append("### 题 %s" % q['q_num'])
                lines.append(q['q_text'])
                lines.append("")
                if q['q_value']:
                    lines.append("**听力原文：**")
                    for line in q['q_value'].split('\n'):
                        lines.append(line)
                    lines.append("")
                for opt, opt_text in q['options']:
                    marker = '**[✓]**' if opt == q['answer'] else '    '
                    lines.append("%s %s. %s" % (marker, opt, opt_text))
                lines.append("")

        elif stype == 'collector.fill':
            passage = view['passage']
            if passage:
                lines.append("**短文/对话：**")
                for line in passage.split('\n'):
                    lines.append(line)
                lines.append("")
            for b in view['blanks']:
                lines.append("- **第%s空 →** %s" % (b['q_num'], b['answer']))
            lines.append("")

        elif stype in ('collector.role', 'collector.dialogue'):
            passage = view['passage']
            if passage:
                lines.append("**材料：**")
                for line in passage.split('\n'):
                    lines.append("> " + line)
                lines.append("")
            for q in view['questions']:
                lines.append("**问 %d：** %s" % (q['qi'], q['ask']))
                if q['keywords']:
                    lines.append("_关键词：%s_" % q['keywords'])
                if q['variants']:
                    lines.append("_可接受答案：%s_" % ' | '.join(q['variants']))
                lines.append("")

        elif stype == 'collector.picture':
            topic = view['topic']
            if topic:
                lines.append("**话题：** %s" % topic)
            # Reference picture image (basename only; no path traversal)
            img_name = view['image_name']
            if img_name:
                img_path = _safe_material_image_path(
                    set_data.get('path', ''), sec.get('dir', ''), img_name)
                if img_path:
                    lines.append('![图片](%s)' % img_path.replace('\\', '/'))
            keypoint = view['keypoint']
            if keypoint:
                lines.append("**要点：**")
                for line in keypoint.split('\n'):
                    lines.append("- " + line)
            for i, answer in enumerate(view['answers'], 1):
                lines.append("**参考答案 %d：** %s" % (i, answer))
            lines.append("")

        elif stype == 'collector.read':
            passage = view['passage']
            if passage:
                lines.append("**朗读文本：**")
                lines.append(passage)
            lines.append("")

    return '\n'.join(lines)


def _render_full_html(set_data):
    """Render a full exam set as a self-contained HTML page (for print preview)."""
    import datetime
    score = set_data.get('score', 0)
    exam_names = set_data.get('exam_type_names', [])
    if exam_names:
        types_text = ' · '.join(_dedup_consecutive(exam_names))
    else:
        types_text = ' · '.join(TYPE_LABELS.get(t, t) for t in sorted(set_data['types']))

    set_id_s = _esc_html(str(set_data.get('id', '')))
    types_text_s = _esc_html(str(types_text))

    html_lines = ['<!DOCTYPE html>',
        '<html lang="zh">',
        '<head>',
        '<meta charset="UTF-8">',
        '<title>ETS %s</title>' % set_id_s,
        '<style>',
        '  body { font-family: "Microsoft YaHei UI", "微软雅黑", sans-serif; '
        '         max-width: 800px; margin: 40px auto; padding: 0 20px; '
        '         font-size: 14px; line-height: 1.8; color: #222; }',
        '  h1 { font-size: 20px; border-bottom: 2px solid #3498db; '
        '        padding-bottom: 8px; margin-bottom: 6px; }',
        '  .meta { color: #888; font-size: 12px; margin-bottom: 20px; }',
        '  h2 { font-size: 16px; color: #3498db; margin-top: 24px; '
        '        border-left: 4px solid #3498db; padding-left: 8px; }',
        '  h3 { font-size: 14px; color: #e67e22; margin: 12px 0 4px; }',
        '  .q-text { margin: 4px 0 8px 20px; }',
        '  .option { margin: 2px 0 2px 28px; }',
        '  .correct { color: #27ae60; font-weight: bold; }',
        '  .blank { color: #27ae60; font-weight: bold; margin: 4px 0 4px 20px; }',
        '  .muted { color: #888; font-size: 12px; }',
        '  .passage { background: #f9f9f9; padding: 10px 16px; '
        '             border-radius: 6px; margin: 8px 0; }',
        '  blockquote { border-left: 3px solid #ccc; margin: 4px 0; '
        '               padding-left: 12px; color: #555; }',
        '  @media print { body { margin: 20px; font-size: 12px; } '
        '    h1 { font-size: 16px; } }',
        '</style>',
        '</head>',
        '<body>',
        '<h1>📄 %s  ·  %s  ·  %d题%s</h1>' % (
            set_id_s, types_text_s, set_data['total_questions'],
            ('  ·  %d分' % score if score else '')),
        '<p class="meta">导出时间: %s</p>' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        '']


    for sec in set_data['sections']:
        if not isinstance(sec, dict):
            continue
        try:
            view = build_section_view(sec)
        except Exception:
            html_lines.append('<p class="muted">（本节无法解析）</p>')
            continue
        stype = view['stype']
        html_lines.append('<h2>%s %s</h2>' % (view['icon'], _esc_html(str(view['label']))))
        if view['unknown']:
            html_lines.append('<p class="muted">（未识别题型: %s）</p>' % _esc_html(stype))
            continue

        if stype == 'collector.choose':
            for q in view['questions']:
                html_lines.append('<h3>题 %s</h3>' % _esc_html(str(q['q_num'])))
                html_lines.append('<p class="q-text">%s</p>' % _esc_html(q['q_text']))
                if q['q_value']:
                    html_lines.append('<p class="muted">📢 听力原文：</p>')
                    html_lines.append('<div class="passage">%s</div>' % _esc_html(q['q_value']))
                for opt, opt_text in q['options']:
                    cls = 'correct' if opt == q['answer'] else ''
                    mark = '✓ ' if opt == q['answer'] else ''
                    html_lines.append(
                        '<p class="option %s">%s%s. %s</p>' % (
                            cls, mark, _esc_html(str(opt)), _esc_html(opt_text)))

        elif stype == 'collector.fill':
            passage = view['passage']
            if passage:
                html_lines.append('<p class="muted">短文/对话：</p>')
                html_lines.append('<div class="passage">%s</div>' % _esc_html(passage))
            for b in view['blanks']:
                html_lines.append(
                    '<p class="blank">第%s空 → %s</p>' % (
                        _esc_html(str(b['q_num'])), _esc_html(b['answer'])))

        elif stype in ('collector.role', 'collector.dialogue'):
            passage = view['passage']
            if passage:
                html_lines.append('<p class="muted">材料：</p>')
                html_lines.append('<blockquote>%s</blockquote>' % _esc_html(passage))
            for q in view['questions']:
                html_lines.append('<p><b>问 %d：</b> %s</p>' % (q['qi'], _esc_html(q['ask'])))
                if q['keywords']:
                    html_lines.append('<p class="muted">关键词：%s</p>' % _esc_html(q['keywords']))
                if q['variants']:
                    html_lines.append(
                        '<p class="muted">可接受答案：%s</p>' % ' | '.join(_esc_html(v) for v in q['variants']))

        elif stype == 'collector.picture':
            topic = view['topic']
            if topic:
                html_lines.append('<p><b>话题：</b>%s</p>' % _esc_html(topic))
            # Embed picture image as base64 (basename only; no path traversal)
            img_name = view['image_name']
            if img_name:
                img_path = _safe_material_image_path(
                    set_data.get('path', ''), sec.get('dir', ''), img_name)
                if img_path:
                    import base64
                    try:
                        with open(img_path, 'rb') as _f:
                            _b64 = base64.b64encode(_f.read()).decode('ascii')
                        safe_name = os.path.basename(img_name)
                        _ext = os.path.splitext(safe_name)[1].lstrip('.').lower()
                        _mime = {'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png', 'gif': 'gif'}.get(_ext, 'jpeg')
                        html_lines.append(
                            '<div style="margin:8px 0"><img src="data:image/%s;base64,%s" '
                            'style="max-width:500px;border-radius:8px;box-shadow:0 2px 8px #aaa" /></div>'
                            % (_mime, _b64))
                    except Exception:
                        html_lines.append(
                            '<p class="muted">[图片: %s]</p>' % _esc_html(os.path.basename(str(img_name))))
            keypoint = view['keypoint']
            if keypoint:
                html_lines.append('<p class="muted">要点：</p>')
                for line in keypoint.split('\n'):
                    html_lines.append('<p class="muted">• %s</p>' % _esc_html(line))
            for i, answer in enumerate(view['answers'], 1):
                html_lines.append('<p class="blank">参考答案 %d：%s</p>' % (i, _esc_html(answer)))

        elif stype == 'collector.read':
            passage = view['passage']
            if passage:
                html_lines.append('<p class="muted">朗读文本：</p>')
                html_lines.append('<div class="passage">%s</div>' % _esc_html(passage))

    html_lines += ['</body>', '</html>']
    return '\n'.join(html_lines)


def _esc_html(text):
    """Escape HTML special characters."""
    return (html.escape(str(text) if text is not None else '', quote=True)
             .replace('\n', '<br>')
             .replace('  ', ' &nbsp;'))


# ═══════════════════════════════════════════════════════════
#  GUI — Backward-compatible shim (moved to ets_browser_ui.py)
# ═══════════════════════════════════════════════════════════

def create_browser_tab(tab_frame):
    """Build the offline paper browser UI inside a CTkTabview tab.

    Delegates to ets_browser_ui.create_browser_tab().
    Kept here for backward compatibility (ets_gui.py imports from ets_parser).
    """
    from ets_browser_ui import create_browser_tab as _real_create
    return _real_create(tab_frame)


def main():
    """Run as standalone browser window."""
    if ctk is None:
        raise ImportError("customtkinter is required. Install: pip install customtkinter")

    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('blue')

    root = ctk.CTk()
    root.title("ETS 离线试卷浏览器")
    root.geometry("960x640")
    root.minsize(720, 480)

    create_browser_tab(root)
    root.mainloop()


if __name__ == '__main__':
    main()
