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

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

# ── Path setup ───────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    _BASE = sys._MEIPASS
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))

# ── Force UTF-8 on Windows ──────────────────────────────────
if sys.platform == 'win32':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, LookupError):
        pass

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
                data = _read_json(content_path)
            except Exception:
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


def render_section(section_data):
    """Render a content section into rich text for display.

    Returns a list of (text, tag) tuples where tag is '' for normal
    or 'answer'/'header'/'muted'/'q_num'/'option'/'section_title' for styled text.
    """
    data = section_data.get('data', {})
    stype = data.get('structure_type', '')
    info = data.get('info', {})
    parts = []

    # Section type header
    icon = TYPE_ICONS.get(stype, '📋')
    label = TYPE_LABELS.get(stype, stype)
    parts.append(("%s %s\n\n" % (icon, label), 'section_title'))

    # ── Choose ──────────────────────────────────────────
    if stype == 'collector.choose':
        for xt in info.get('xtlist', []):
            q_num = xt.get('xt_xh', '')
            q_raw = _html_to_text(xt.get('xt_nr', ''))
            q_text = _strip_template_prefix(q_raw)
            q_value = _html_to_text(xt.get('xt_value', ''))
            answer = xt.get('answer', '')

            parts.append(("题 %s" % q_num, 'q_num'))
            parts.append(("  %s\n" % q_text, ''))

            if q_value:
                parts.append(("  听力原文：\n", 'muted'))
                for line in q_value.split('\n'):
                    parts.append(("    %s\n" % line, 'muted'))
                parts.append(("\n", ''))

            for xx in xt.get('xxlist', []):
                opt = xx.get('xx_mc', '')
                opt_text = _html_to_text(xx.get('xx_nr', ''))
                is_correct = (opt == answer)
                if is_correct:
                    parts.append(("  ✓ %s. " % opt, 'answer'))
                    parts.append(("%s\n" % opt_text, 'answer'))
                else:
                    parts.append(("    %s. %s\n" % (opt, opt_text), 'option'))

            parts.append(("\n", ''))

    # ── Fill ────────────────────────────────────────────
    elif stype == 'collector.fill':
        passage = _html_to_text(info.get('value', ''))
        if passage:
            parts.append(("短文/对话：\n", 'muted'))
            for line in passage.split('\n'):
                parts.append(("  %s\n" % line, ''))
            parts.append(("\n", ''))

        for std in info.get('std', []):
            q_num = std.get('xth', std.get('th', ''))
            answer = _html_to_text(std.get('value', ''))
            ai = std.get('ai', '')
            parts.append(("第%s空 → " % q_num, 'q_num'))
            parts.append(("%s\n" % answer, 'answer'))
            if ai and ai != answer:
                parts.append(("  (AI识别: %s)\n" % ai, 'muted'))

        parts.append(("\n", ''))

    # ── Role / Dialogue ────────────────────────────────
    elif stype in ('collector.role', 'collector.dialogue'):
        passage = _html_to_text(info.get('value', ''))
        if passage:
            parts.append(("对话/材料：\n", 'muted'))
            for line in passage.split('\n'):
                parts.append(("  %s\n" % line, ''))
            parts.append(("\n", ''))

        for qi, q in enumerate(info.get('question', []), 1):
            ask_raw = _html_to_text(q.get('ask', ''))
            ask = _strip_template_prefix(ask_raw)
            keywords = q.get('keywords', '')
            parts.append(("问 %d" % qi, 'q_num'))
            parts.append(("  %s\n" % ask, ''))
            if keywords:
                parts.append(("  关键词：%s\n" % keywords, 'muted'))

            stds = q.get('std', [])
            if stds:
                parts.append(("  可接受答案：\n", ''))
                shown = set()
                for s in stds[:8]:
                    val = _html_to_text(s.get('value', ''))
                    if val not in shown:
                        shown.add(val)
                        parts.append(("    • %s\n" % val, 'answer'))
                if len(stds) > 8:
                    parts.append(("    ... 共%d个变体\n" % len(stds), 'muted'))
            parts.append(("\n", ''))

    # ── Picture ─────────────────────────────────────────
    elif stype == 'collector.picture':
        topic = info.get('topic', '')
        if topic:
            parts.append(("话题：%s\n\n" % topic, ''))

        passage = _html_to_text(info.get('value', ''))
        if passage:
            parts.append(("原文：\n", 'muted'))
            for line in passage.split('\n'):
                parts.append(("  %s\n" % line, ''))
            parts.append(("\n", ''))

        keypoint = _html_to_text(info.get('keypoint', ''))
        if keypoint:
            parts.append(("要点：\n", 'muted'))
            for line in keypoint.split('\n'):
                parts.append(("  %s\n" % line, ''))
            parts.append(("\n", ''))

        for i, std in enumerate(info.get('std', []), 1):
            answer = _html_to_text(std.get('value', ''))
            parts.append(("参考答案 %d\n" % i, 'q_num'))
            parts.append(("  %s\n\n" % answer, 'answer'))

    # ── Read ────────────────────────────────────────────
    elif stype == 'collector.read':
        passage = _html_to_text(info.get('value', ''))
        if passage:
            parts.append(("朗读文本：\n", 'muted'))
            for line in passage.split('\n'):
                parts.append(("  %s\n" % line, ''))

    # ── Unknown fallback ────────────────────────────────
    else:
        parts.append(("(未识别题型: %s)\n" % stype, 'muted'))
        raw = json.dumps(info, ensure_ascii=False, indent=2)
        if len(raw) > 2000:
            raw = raw[:2000] + '\n...(truncated)'
        parts.append(("%s\n" % raw, 'muted'))

    return parts


# ═══════════════════════════════════════════════════════════
#  Export helpers (used by the browser tab)
# ═══════════════════════════════════════════════════════════

def _render_full_markdown(set_data):
    """"Render a full exam set as plain Markdown text (for export)."""
    import datetime
    lines = []
    title_parts = ["📄 %s" % set_data['id']]
    score = set_data.get('score', 0)
    if score:
        title_parts.append("%d分" % score)
    title_parts.append("%d题" % set_data['total_questions'])
    exam_names = set_data.get('exam_type_names', [])
    if exam_names:
        deduped = []
        for n in exam_names:
            if not deduped or deduped[-1] != n:
                deduped.append(n)
        title_parts.append(' · '.join(deduped))
    else:
        title_parts.append(' · '.join(TYPE_LABELS.get(t, t) for t in sorted(set_data['types'])))
    lines.append("# " + ' · '.join(title_parts))
    lines.append("")
    lines.append("*导出时间: %s*" % datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
    lines.append("")

    for sec in set_data['sections']:
        stype = sec['type']
        icon = TYPE_ICONS.get(stype, '📋')
        label = TYPE_LABELS.get(stype, stype)
        lines.append("## %s %s" % (icon, label))
        lines.append("")

        data = sec.get('data', {})
        info = data.get('info', {})

        if stype == 'collector.choose':
            for xt in info.get('xtlist', []):
                q_num = xt.get('xt_xh', '')
                q_raw = _html_to_text(xt.get('xt_nr', ''))
                q_text = _strip_template_prefix(q_raw)
                answer = xt.get('answer', '')
                lines.append("### 题 %s" % q_num)
                lines.append(q_text)
                lines.append("")
                for xx in xt.get('xxlist', []):
                    opt = xx.get('xx_mc', '')
                    opt_text = _html_to_text(xx.get('xx_nr', ''))
                    marker = '**[✓]**' if opt == answer else '    '
                    lines.append("%s %s. %s" % (marker, opt, opt_text))
                lines.append("")

        elif stype == 'collector.fill':
            passage = _html_to_text(info.get('value', ''))
            if passage:
                lines.append("**短文/对话：**")
                for line in passage.split('\n'):
                    lines.append(line)
                lines.append("")
            for std in info.get('std', []):
                q_num = std.get('xth', std.get('th', ''))
                answer = _html_to_text(std.get('value', ''))
                lines.append("- **第%s空 →** %s" % (q_num, answer))
            lines.append("")

        elif stype in ('collector.role', 'collector.dialogue'):
            passage = _html_to_text(info.get('value', ''))
            if passage:
                lines.append("**材料：**")
                for line in passage.split('\n'):
                    lines.append("> " + line)
                lines.append("")
            for qi, q in enumerate(info.get('question', []), 1):
                ask_raw = _html_to_text(q.get('ask', ''))
                ask = _strip_template_prefix(ask_raw)
                keywords = q.get('keywords', '')
                lines.append("**问 %d：** %s" % (qi, ask))
                if keywords:
                    lines.append("_关键词：%s_" % keywords)
                stds = q.get('std', [])
                if stds:
                    shown = []
                    for s in stds[:8]:
                        val = _html_to_text(s.get('value', ''))
                        if val not in shown:
                            shown.append(val)
                    lines.append("_可接受答案：%s_" % ' | '.join(shown))
                lines.append("")

        elif stype == 'collector.picture':
            topic = info.get('topic', '')
            if topic:
                lines.append("**话题：** %s" % topic)
            # Reference picture image
            img_name = info.get('image', '')
            if img_name:
                sec_dir = sec.get('dir', '')
                img_path = os.path.join(set_data.get('path', ''), sec_dir, 'material', img_name)
                if os.path.exists(img_path):
                    lines.append('![图片](%s)' % img_path.replace('\\', '/'))
            keypoint = _html_to_text(info.get('keypoint', ''))
            if keypoint:
                lines.append("**要点：**")
                for line in keypoint.split('\n'):
                    lines.append("- " + line)
            for i, std in enumerate(info.get('std', []), 1):
                answer = _html_to_text(std.get('value', ''))
                lines.append("**参考答案 %d：** %s" % (i, answer))
            lines.append("")

        elif stype == 'collector.read':
            passage = _html_to_text(info.get('value', ''))
            if passage:
                lines.append("**朗读文本：**")
                lines.append(passage)
            lines.append("")

        else:
            lines.append("_（未识别题型: %s）_" % stype)
            lines.append("")

    return '\n'.join(lines)



def _render_full_html(set_data):
    """Render a full exam set as a self-contained HTML page (for print preview)."""
    import datetime
    score = set_data.get('score', 0)
    exam_names = set_data.get('exam_type_names', [])
    if exam_names:
        deduped = []
        for n in exam_names:
            if not deduped or deduped[-1] != n:
                deduped.append(n)
        types_text = ' · '.join(deduped)
    else:
        types_text = ' · '.join(TYPE_LABELS.get(t, t) for t in sorted(set_data['types']))

    html_lines = ['<!DOCTYPE html>',
        '<html lang="zh">',
        '<head>',
        '<meta charset="UTF-8">',
        '<title>ETS %s</title>' % set_data['id'],
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
            set_data['id'], types_text, set_data['total_questions'],
            ('  ·  %d分' % score if score else '')),
        '<p class="meta">导出时间: %s</p>' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        '']


    for sec in set_data['sections']:
        stype = sec['type']
        icon = TYPE_ICONS.get(stype, '📋')
        label = TYPE_LABELS.get(stype, stype)
        html_lines.append('<h2>%s %s</h2>' % (icon, label))

        data = sec.get('data', {})
        info = data.get('info', {})

        if stype == 'collector.choose':
            for xt in info.get('xtlist', []):
                q_num = xt.get('xt_xh', '')
                q_raw = _html_to_text(xt.get('xt_nr', ''))
                q_text = _strip_template_prefix(q_raw)
                answer = xt.get('answer', '')
                q_value = _html_to_text(xt.get('xt_value', ''))
                html_lines.append('<h3>题 %s</h3>' % q_num)
                html_lines.append('<p class="q-text">%s</p>' % _esc_html(q_text))
                if q_value:
                    html_lines.append('<p class="muted">📢 听力原文：</p>')
                    html_lines.append('<div class="passage">%s</div>' % _esc_html(q_value))
                for xx in xt.get('xxlist', []):
                    opt = xx.get('xx_mc', '')
                    opt_text = _html_to_text(xx.get('xx_nr', ''))
                    cls = 'correct' if opt == answer else ''
                    mark = '✓ ' if opt == answer else ''
                    html_lines.append(
                        '<p class="option %s">%s%s. %s</p>' % (cls, mark, opt, _esc_html(opt_text)))

        elif stype == 'collector.fill':
            passage = _html_to_text(info.get('value', ''))
            if passage:
                html_lines.append('<p class="muted">短文/对话：</p>')
                html_lines.append('<div class="passage">%s</div>' % _esc_html(passage))
            for std in info.get('std', []):
                q_num = std.get('xth', std.get('th', ''))
                answer = _html_to_text(std.get('value', ''))
                html_lines.append('<p class="blank">第%s空 → %s</p>' % (q_num, _esc_html(answer)))

        elif stype in ('collector.role', 'collector.dialogue'):
            passage = _html_to_text(info.get('value', ''))
            if passage:
                html_lines.append('<p class="muted">材料：</p>')
                html_lines.append('<blockquote>%s</blockquote>' % _esc_html(passage))
            for qi, q in enumerate(info.get('question', []), 1):
                ask_raw = _html_to_text(q.get('ask', ''))
                ask = _strip_template_prefix(ask_raw)
                keywords = q.get('keywords', '')
                html_lines.append('<p><b>问 %d：</b> %s</p>' % (qi, _esc_html(ask)))
                if keywords:
                    html_lines.append('<p class="muted">关键词：%s</p>' % _esc_html(keywords))
                stds = q.get('std', [])
                if stds:
                    shown = []
                    for s in stds[:8]:
                        val = _html_to_text(s.get('value', ''))
                        if val not in shown:
                            shown.append(val)
                    html_lines.append(
                        '<p class="muted">可接受答案：%s</p>' % ' | '.join(_esc_html(v) for v in shown))

        elif stype == 'collector.picture':
            topic = info.get('topic', '')
            if topic:
                html_lines.append('<p><b>话题：</b>%s</p>' % _esc_html(topic))
            # Embed picture image as base64
            img_name = info.get('image', '')
            if img_name:
                sec_dir = sec.get('dir', '')
                img_path = os.path.join(set_data.get('path', ''), sec_dir, 'material', img_name)
                if os.path.exists(img_path):
                    import base64
                    try:
                        with open(img_path, 'rb') as _f:
                            _b64 = base64.b64encode(_f.read()).decode('ascii')
                        _ext = os.path.splitext(img_name)[1].lstrip('.').lower()
                        _mime = {'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png', 'gif': 'gif'}.get(_ext, 'jpeg')
                        html_lines.append(
                            '<div style="margin:8px 0"><img src="data:image/%s;base64,%s" '
                            'style="max-width:500px;border-radius:8px;box-shadow:0 2px 8px #aaa" /></div>'
                            % (_mime, _b64))
                    except Exception:
                        html_lines.append('<p class="muted">[图片: %s]</p>' % _esc_html(img_name))
            keypoint = _html_to_text(info.get('keypoint', ''))
            if keypoint:
                html_lines.append('<p class="muted">要点：</p>')
                for line in keypoint.split('\n'):
                    html_lines.append('<p class="muted">• %s</p>' % _esc_html(line))
            for i, std in enumerate(info.get('std', []), 1):
                answer = _html_to_text(std.get('value', ''))
                html_lines.append('<p class="blank">参考答案 %d：%s</p>' % (i, _esc_html(answer)))

        elif stype == 'collector.read':
            passage = _html_to_text(info.get('value', ''))
            if passage:
                html_lines.append('<p class="muted">朗读文本：</p>')
                html_lines.append('<div class="passage">%s</div>' % _esc_html(passage))

        else:
            html_lines.append('<p class="muted">（未识别题型: %s）</p>' % _esc_html(stype))

    html_lines += ['</body>', '</html>']
    return '\n'.join(html_lines)


def _esc_html(text):
    """Escape HTML special characters."""
    return (html.escape(text, quote=True)
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
