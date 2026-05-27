#!/usr/bin/env python3
"""
ETS Parser — Offline exam paper browser for ETS cached data.

Scans %APPDATA%\\ETS for locally cached exam papers and displays
them in a CustomTkinter GUI with answers highlighted in red.

Supports question types:
  - collector.choose   → Multiple choice (A/B/C)
  - collector.fill     → Fill-in-the-blank (standard answers)
  - collector.role     → Oral response (acceptable answer variants)
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

# ── Type labels (Chinese) ───────────────────────────────────
TYPE_LABELS = {
    'collector.choose':  '📝 选择题',
    'collector.fill':    '✏️ 填空题',
    'collector.role':    '🗣️ 口语问答',
    'collector.picture': '🖼️ 图片描述',
    'collector.read':    '📖 朗读',
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
    # Last resort: replace errors
    return json.loads(raw.decode('utf-8', errors='replace'))


def _html_to_text(html_str):
    """Convert HTML markup from ETS content to plain text.

    Handles: <br>, </br>, <p>, </p>, <span class="italic">, <i>, <b>,
    HTML entities (&rsquo; &lsquo; etc.)
    """
    if not html_str:
        return ''
    text = html_str
    # <br> and </br> → newline
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    # <p> → newline
    text = re.sub(r'<p\s*/?>', '\n', text, flags=re.IGNORECASE)
    # </p> → nothing (already have newline from <p>)
    text = re.sub(r'</p>', '', text, flags=re.IGNORECASE)
    # Remove remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # HTML entities (&rsquo; &lsquo; &hellip; etc.)
    text = html.unescape(text)
    # Collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _strip_template_prefix(text):
    """Remove ETS template variable prefixes like 'ets_th1', 'ets_sm1' from text.

    ETS content.json sometimes includes these as inline prefixes in question text,
    e.g. 'ets_th1 Where did the woman see Sam?' → 'Where did the woman see Sam?'
    """
    if not text:
        return text
    # Match ets_xxN at start of text followed by space or as standalone
    return re.sub(r'^ets_\w+\d*\s*', '', text).strip()


# ═══════════════════════════════════════════════════════════

def scan_sets():
    """Scan ETS data directory and return list of exam set dicts.

    Each dict: {id, path, sections, total_questions, types}
    """
    if not os.path.isdir(ETS_DATA_DIR):
        return []

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

        if sections:
            sets.append({
                'id': name,
                'path': set_path,
                'sections': sections,
                'total_questions': total_q,
                'types': types,
            })

    return sets


def render_section(section_data):
    """Render a content section into rich text for display.

    Returns a list of (text, tag) tuples where tag is '' for normal
    or 'answer'/'header'/'muted' for styled text.
    """
    data = section_data.get('data', {})
    stype = data.get('structure_type', '')
    info = data.get('info', {})
    parts = []

    # ── Section type label ───────────────────────────────
    label = TYPE_LABELS.get(stype, stype)
    parts.append(("━━ %s ━━\n\n" % label, 'header'))

    # ── Choose (multiple choice) ────────────────────────
    if stype == 'collector.choose':
        for xt in info.get('xtlist', []):
            q_num = xt.get('xt_xh', '')
            q_raw = _html_to_text(xt.get('xt_nr', ''))
            q_text = _strip_template_prefix(q_raw)
            q_value = _html_to_text(xt.get('xt_value', ''))
            answer = xt.get('answer', '')

            parts.append(("【题%s】%s\n" % (q_num, q_text), ''))

            if q_value:
                parts.append(("听力原文：\n%s\n\n" % q_value, 'muted'))

            # Options
            for xx in xt.get('xxlist', []):
                opt = xx.get('xx_mc', '')
                opt_text = _html_to_text(xx.get('xx_nr', ''))
                is_correct = (opt == answer)
                tag = 'answer' if is_correct else ''
                prefix = "✅ " if is_correct else "   "
                parts.append(("%s%s. %s\n" % (prefix, opt, opt_text), tag))

            if answer:
                parts.append(("\n正确答案：%s\n" % answer, 'answer'))
            parts.append(("\n", ''))

    # ── Fill (fill-in-the-blank) ────────────────────────
    elif stype == 'collector.fill':
        passage = _html_to_text(info.get('value', ''))
        if passage:
            parts.append(("短文/对话：\n%s\n\n" % passage, ''))

        for std in info.get('std', []):
            q_num = std.get('th', '')
            answer = std.get('value', '')
            ai = std.get('ai', '')
            parts.append(("【第%s空】" % q_num, ''))
            parts.append(("%s\n" % answer, 'answer'))
            if ai and ai != answer:
                parts.append(("  (AI识别: %s)\n" % ai, 'muted'))

        parts.append(("\n", ''))

    # ── Role (oral Q&A) ─────────────────────────────────
    elif stype == 'collector.role':
        passage = _html_to_text(info.get('value', ''))
        if passage:
            parts.append(("对话/材料：\n%s\n\n" % passage, ''))

        for qi, q in enumerate(info.get('question', []), 1):
            ask_raw = _html_to_text(q.get('ask', ''))
            ask = _strip_template_prefix(ask_raw)
            keywords = q.get('keywords', '')
            parts.append(("【问%s】%s\n" % (qi, ask), ''))
            if keywords:
                parts.append(("关键词：%s\n" % keywords, 'muted'))

            # Show first few acceptable answers
            stds = q.get('std', [])
            if stds:
                parts.append(("可接受答案：\n", ''))
                shown = set()
                for s in stds[:8]:
                    val = s.get('value', '')
                    if val not in shown:
                        shown.add(val)
                        parts.append(("  • %s\n" % val, 'answer'))
                if len(stds) > 8:
                    parts.append(("  ... 共%d个变体\n" % len(stds), 'muted'))
            parts.append(("\n", ''))

    # ── Picture (picture description) ───────────────────
    elif stype == 'collector.picture':
        topic = info.get('topic', '')
        if topic:
            parts.append(("话题：%s\n\n" % topic, ''))

        passage = _html_to_text(info.get('value', ''))
        if passage:
            parts.append(("原文：\n%s\n\n" % passage, ''))

        keypoint = _html_to_text(info.get('keypoint', ''))
        if keypoint:
            parts.append(("要点：\n%s\n\n" % keypoint, ''))

        for i, std in enumerate(info.get('std', []), 1):
            answer = std.get('value', '')
            parts.append(("【参考答案%d】\n%s\n\n" % (i, answer), 'answer'))

    # ── Read (read aloud) ───────────────────────────────
    elif stype == 'collector.read':
        passage = _html_to_text(info.get('value', ''))
        if passage:
            parts.append(("朗读文本：\n%s\n" % passage, ''))

    # ── Unknown type fallback ───────────────────────────
    else:
        parts.append(("(未识别题型: %s)\n" % stype, 'muted'))
        # Dump raw for debugging
        raw = json.dumps(info, ensure_ascii=False, indent=2)
        if len(raw) > 2000:
            raw = raw[:2000] + '\n...(truncated)'
        parts.append(("%s\n" % raw, 'muted'))

    return parts


# ═══════════════════════════════════════════════════════════
#  GUI — CustomTkinter offline paper browser
# ═══════════════════════════════════════════════════════════

def create_browser_tab(tab_frame):
    """Build the offline paper browser UI inside a CTkTabview tab.

    This is called from ets_gui.py to add the browser tab.
    """
    # ── State ────────────────────────────────────────────
    _sets = []
    _current_set = [None]  # mutable list for closure

    # ── Layout: left panel (set list) + right panel (content) ──
    tab_frame.grid_columnconfigure(1, weight=1)
    tab_frame.grid_rowconfigure(0, weight=1)

    # Left panel
    left = ctk.CTkFrame(tab_frame, width=220)
    left.grid(row=0, column=0, sticky='ns', padx=(8, 4), pady=8)
    left.grid_propagate(False)

    ctk.CTkLabel(left, text="📚 试卷列表", font=ctk.CTkFont(size=14, weight='bold')).pack(
        pady=(8, 4))

    # Search entry
    search_var = ctk.StringVar(value='')
    search_entry = ctk.CTkEntry(left, placeholder_text="搜索ID...", textvariable=search_var, width=200)
    search_entry.pack(padx=8, pady=(0, 4))

    # Set list (scrollable)
    set_list = ctk.CTkScrollableFrame(left, width=200, label_text='')
    set_list.pack(fill='both', expand=True, padx=8, pady=(0, 8))

    # Right panel
    right = ctk.CTkFrame(tab_frame)
    right.grid(row=0, column=1, sticky='nsew', padx=(4, 8), pady=8)
    right.grid_columnconfigure(0, weight=1)
    right.grid_rowconfigure(1, weight=1)

    # Header
    header_frame = ctk.CTkFrame(right, fg_color='transparent')
    header_frame.grid(row=0, column=0, sticky='ew', padx=4, pady=(4, 0))

    set_title_label = ctk.CTkLabel(
        header_frame, text="← 选择一份试卷",
        font=ctk.CTkFont(size=15, weight='bold'), anchor='w')
    set_title_label.pack(fill='x')

    # Content display
    content_box = ctk.CTkTextbox(
        right, wrap='word', state='disabled',
        font=ctk.CTkFont(family='Consolas', size=13),
        activate_scrollbars=True)
    content_box.grid(row=1, column=0, sticky='nsew', padx=4, pady=4)

    # Configure tags for rich text (access internal tkinter Text widget)
    content_box._textbox.tag_configure(
        'answer', foreground='#e74c3c',
        font=ctk.CTkFont(family='Consolas', size=13, weight='bold'))
    content_box._textbox.tag_configure(
        'header', foreground='#3498db',
        font=ctk.CTkFont(family='Consolas', size=14, weight='bold'))
    content_box._textbox.tag_configure(
        'muted', foreground='#7f8c8d')

    # ── Section navigation ───────────────────────────────
    nav_frame = ctk.CTkFrame(right, fg_color='transparent')
    nav_frame.grid(row=2, column=0, sticky='ew', padx=4, pady=(0, 4))

    section_var = ctk.StringVar(value='')
    prev_btn = ctk.CTkButton(nav_frame, text="◀ 上一节", width=100, state='disabled')
    prev_btn.pack(side='left', padx=(0, 4))
    next_btn = ctk.CTkButton(nav_frame, text="下一节 ▶", width=100, state='disabled')
    next_btn.pack(side='left')
    section_label = ctk.CTkLabel(nav_frame, textvariable=section_var, font=ctk.CTkFont(size=12))
    section_label.pack(side='left', padx=12)

    _section_idx = [0]  # mutable for closure

    def _show_section(idx):
        if not _current_set[0] or idx < 0 or idx >= len(_current_set[0]['sections']):
            return
        _section_idx[0] = idx
        sec = _current_set[0]['sections'][idx]
        parts = render_section(sec)

        content_box.configure(state='normal')
        content_box._textbox.delete('1.0', 'end')
        for text, tag in parts:
            if tag:
                content_box._textbox.insert('end', text, tag)
            else:
                content_box._textbox.insert('end', text)
        content_box.configure(state='disabled')
        content_box._textbox.see('1.0')

        total = len(_current_set[0]['sections'])
        section_var.set("第 %d/%d 节" % (idx + 1, total))
        prev_btn.configure(state='normal' if idx > 0 else 'disabled')
        next_btn.configure(state='normal' if idx < total - 1 else 'disabled')

    def _on_prev():
        _show_section(_section_idx[0] - 1)

    def _on_next():
        _show_section(_section_idx[0] + 1)

    prev_btn.configure(command=_on_prev)
    next_btn.configure(command=_on_next)

    # ── Set card click ───────────────────────────────────
    def _on_set_click(set_data):
        _current_set[0] = set_data
        types_str = '  '.join(TYPE_LABELS.get(t, t) for t in sorted(set_data['types']))
        set_title_label.configure(text="试卷 %s  (%d题)  %s" % (
            set_data['id'], set_data['total_questions'], types_str))
        _show_section(0)

    # ── Render set list ──────────────────────────────────
    def _render_sets(filter_text=''):
        # Clear existing cards
        for w in set_list.winfo_children():
            w.destroy()

        for s in _sets:
            if filter_text and filter_text not in s['id']:
                continue

            # Build concise type labels
            type_parts = []
            for t in sorted(s['types']):
                lbl = TYPE_LABELS.get(t, t)
                # Extract emoji + first 2 chars
                type_parts.append(lbl)
            types_str = '  '.join(type_parts)

            card_text = "📄 %s  (%d题)\n   %s" % (s['id'], s['total_questions'], types_str)

            btn = ctk.CTkButton(
                set_list, text=card_text, anchor='w',
                font=ctk.CTkFont(size=12),
                fg_color='transparent', hover_color=('#d0d0d0', '#3a3a3a'),
                text_color=('#333333', '#eeeeee'),
                command=lambda sd=s: _on_set_click(sd))
            btn.pack(fill='x', pady=2)

    # ── Search ───────────────────────────────────────────
    def _on_search(_=None):
        _render_sets(search_var.get().strip())

    search_entry.configure(command=_on_search)

    # ── Load data ────────────────────────────────────────
    _sets = scan_sets()
    _render_sets()

    if not _sets:
        content_box.configure(state='normal')
        content_box._textbox.insert('1.0',
            "未找到 ETS 缓存数据。\n\n"
            "请确认路径：%s\n\n"
            "需要先在 ETS 客户端中开始一次作业，\n"
            "系统会自动缓存试卷数据。" % ETS_DATA_DIR)
        content_box.configure(state='disabled')

    return tab_frame


# ═══════════════════════════════════════════════════════════
#  Standalone mode
# ═══════════════════════════════════════════════════════════

def main():
    """Run as standalone browser window."""
    import customtkinter as ctk

    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('blue')

    root = ctk.CTk()
    root.title("ETS 离线试卷浏览器")
    root.geometry("900x600")
    root.minsize(700, 450)

    create_browser_tab(root)
    root.mainloop()


if __name__ == '__main__':
    main()
