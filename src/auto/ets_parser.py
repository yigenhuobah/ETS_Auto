#!/usr/bin/env python3
"""
ETS Parser — Offline exam paper browser for ETS cached data.

Scans %APPDATA%\\ETS for locally cached exam papers and displays
them in a CustomTkinter GUI with answers highlighted.

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
    'collector.picture': '图片描述',
    'collector.read':    '朗读',
}

TYPE_ICONS = {
    'collector.choose':  '📝',
    'collector.fill':    '✏️',
    'collector.role':    '🗣️',
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
    """Convert HTML markup from ETS content to plain text."""
    if not html_str:
        return ''
    text = html_str
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<p\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
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
    """Scan ETS data directory and return list of exam set dicts."""
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
            q_num = std.get('th', '')
            answer = std.get('value', '')
            ai = std.get('ai', '')
            parts.append(("第%s空 → " % q_num, 'q_num'))
            parts.append(("%s\n" % answer, 'answer'))
            if ai and ai != answer:
                parts.append(("  (AI识别: %s)\n" % ai, 'muted'))

        parts.append(("\n", ''))

    # ── Role ────────────────────────────────────────────
    elif stype == 'collector.role':
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
                    val = s.get('value', '')
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
            answer = std.get('value', '')
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
#  GUI — CustomTkinter offline paper browser
# ═══════════════════════════════════════════════════════════

def create_browser_tab(tab_frame):
    """Build the offline paper browser UI inside a CTkTabview tab.

    Layout:
      Left panel  (260px): set list with real-time search
      Right panel (flex):  header + section dropdown + content + nav
    """
    # ── State ────────────────────────────────────────────
    _current_set = [None]
    _selected_card = [None]  # track highlighted card frame

    # ── Color palette (works for both light & dark) ─────
    _CARD_FG = ('#f0f0f0', '#2b2b2b')
    _CARD_HOVER = ('#dce6f0', '#354050')
    _CARD_ACTIVE_FG = ('#cde0f0', '#2a4a6a')
    _CARD_ACTIVE_TEXT = ('#1a5276', '#7ec8e3')
    _CARD_TEXT = ('#333333', '#eeeeee')
    _CARD_SUBTEXT = ('#666666', '#aaaaaa')

    # ── Main grid ───────────────────────────────────────
    tab_frame.grid_columnconfigure(1, weight=1)
    tab_frame.grid_rowconfigure(0, weight=1)

    # ── Left panel ──────────────────────────────────────
    left = ctk.CTkFrame(tab_frame, width=260, corner_radius=8)
    left.grid(row=0, column=0, sticky='ns', padx=(8, 4), pady=8)
    left.grid_propagate(False)

    # Left header
    ctk.CTkLabel(
        left, text="📚 试卷列表",
        font=ctk.CTkFont(size=15, weight='bold')
    ).pack(pady=(10, 6), padx=12, anchor='w')

    # Search with real-time filter
    search_var = ctk.StringVar(value='')
    search_entry = ctk.CTkEntry(
        left, placeholder_text="输入ID搜索...",
        textvariable=search_var, width=236, height=32,
        corner_radius=6)
    search_entry.pack(padx=12, pady=(0, 6))

    # Scrollable set list
    set_list = ctk.CTkScrollableFrame(left, width=236, label_text='')
    set_list.pack(fill='both', expand=True, padx=12, pady=(0, 10))

    # ── Right panel ─────────────────────────────────────
    right = ctk.CTkFrame(tab_frame, corner_radius=8)
    right.grid(row=0, column=1, sticky='nsew', padx=(4, 8), pady=8)
    right.grid_columnconfigure(0, weight=1)
    right.grid_rowconfigure(1, weight=1)

    # Header bar: title + section dropdown
    header_bar = ctk.CTkFrame(right, fg_color='transparent', height=40)
    header_bar.grid(row=0, column=0, sticky='ew', padx=12, pady=(10, 0))
    header_bar.grid_columnconfigure(1, weight=1)

    set_title_label = ctk.CTkLabel(
        header_bar, text="← 选择一份试卷",
        font=ctk.CTkFont(size=15, weight='bold'), anchor='w')
    set_title_label.grid(row=0, column=0, sticky='w')

    section_var = ctk.StringVar(value='')
    section_menu = ctk.CTkOptionMenu(
        header_bar, variable=section_var,
        values=[], width=200, height=30,
        font=ctk.CTkFont(size=12),
        command=lambda v: None)  # placeholder
    section_menu.grid(row=0, column=1, sticky='e', padx=(8, 0))

    # Content area
    content_box = ctk.CTkTextbox(
        right, wrap='word', state='disabled',
        font=ctk.CTkFont(family='Microsoft YaHei UI', size=13),
        activate_scrollbars=True,
        corner_radius=6)
    content_box.grid(row=1, column=0, sticky='nsew', padx=12, pady=8)

    # Configure text tags via internal tkinter Text widget
    _t = content_box._textbox
    _t.configure(spacing1=2, spacing3=4, padx=8, pady=4)

    _t.tag_configure(
        'answer', foreground='#2ecc71',
        font=ctk.CTkFont(family='Microsoft YaHei UI', size=13, weight='bold'),
        lmargin1=20, lmargin2=20)
    _t.tag_configure(
        'section_title', foreground='#3498db',
        font=ctk.CTkFont(family='Microsoft YaHei UI', size=15, weight='bold'),
        spacing1=8, spacing3=4)
    _t.tag_configure(
        'q_num', foreground='#e67e22',
        font=ctk.CTkFont(family='Microsoft YaHei UI', size=13, weight='bold'),
        lmargin1=8)
    _t.tag_configure(
        'header', foreground='#3498db',
        font=ctk.CTkFont(family='Microsoft YaHei UI', size=14, weight='bold'))
    _t.tag_configure(
        'muted', foreground='#7f8c8d',
        font=ctk.CTkFont(family='Microsoft YaHei UI', size=12),
        lmargin1=20, lmargin2=20)
    _t.tag_configure(
        'option', foreground='#95a5a6',
        font=ctk.CTkFont(family='Microsoft YaHei UI', size=13),
        lmargin1=30, lmargin2=30)

    # ── Bottom navigation ───────────────────────────────
    nav_frame = ctk.CTkFrame(right, fg_color='transparent', height=36)
    nav_frame.grid(row=2, column=0, sticky='ew', padx=12, pady=(0, 10))

    prev_btn = ctk.CTkButton(
        nav_frame, text="◀ 上一节", width=110, height=32,
        state='disabled', corner_radius=6)
    prev_btn.pack(side='left', padx=(0, 6))

    section_label = ctk.CTkLabel(
        nav_frame, textvariable=section_var,
        font=ctk.CTkFont(size=12), anchor='center')
    section_label.pack(side='left', padx=12)

    next_btn = ctk.CTkButton(
        nav_frame, text="下一节 ▶", width=110, height=32,
        state='disabled', corner_radius=6)
    next_btn.pack(side='left')

    _section_idx = [0]

    # ── Section display logic ───────────────────────────
    def _show_section(idx):
        if not _current_set[0] or idx < 0 or idx >= len(_current_set[0]['sections']):
            return
        _section_idx[0] = idx
        sec = _current_set[0]['sections'][idx]
        parts = render_section(sec)

        content_box.configure(state='normal')
        _t.delete('1.0', 'end')
        for text, tag in parts:
            if tag:
                _t.insert('end', text, tag)
            else:
                _t.insert('end', text)
        content_box.configure(state='disabled')
        _t.see('1.0')

        total = len(_current_set[0]['sections'])
        section_var.set("%d / %d" % (idx + 1, total))
        prev_btn.configure(state='normal' if idx > 0 else 'disabled')
        next_btn.configure(state='normal' if idx < total - 1 else 'disabled')
        # Sync dropdown
        section_menu.set(TYPE_ICONS.get(sec['type'], '📋') + ' ' +
                         TYPE_LABELS.get(sec['type'], sec['type']))

    def _on_prev():
        _show_section(_section_idx[0] - 1)

    def _on_next():
        _show_section(_section_idx[0] + 1)

    prev_btn.configure(command=_on_prev)
    next_btn.configure(command=_on_next)

    # ── Section dropdown handler ────────────────────────
    def _on_section_menu_select(value):
        if not _current_set[0]:
            return
        sections = _current_set[0]['sections']
        for i, sec in enumerate(sections):
            menu_text = TYPE_ICONS.get(sec['type'], '📋') + ' ' + TYPE_LABELS.get(sec['type'], sec['type'])
            if menu_text == value:
                _show_section(i)
                break

    section_menu.configure(command=_on_section_menu_select)

    # ── Card click & highlight helpers ──────────────────
    def _highlight_card(card_frame, id_label, sub_label):
        """Apply active highlight to a card."""
        card_frame.configure(fg_color=_CARD_ACTIVE_FG)
        id_label.configure(text_color=_CARD_ACTIVE_TEXT)
        sub_label.configure(text_color=_CARD_ACTIVE_TEXT)

    def _unhighlight_card(card_frame, id_label, sub_label):
        """Remove active highlight from a card."""
        card_frame.configure(fg_color=_CARD_FG)
        id_label.configure(text_color=_CARD_TEXT)
        sub_label.configure(text_color=_CARD_SUBTEXT)

    def _on_set_click(set_data, card_frame, id_label, sub_label):
        # Reset previous card highlight
        if _selected_card[0] and _selected_card[0] != card_frame:
            prev = _selected_card[0]
            _unhighlight_card(
                prev, prev._id_label, prev._sub_label)

        # Highlight current card
        _highlight_card(card_frame, id_label, sub_label)
        _selected_card[0] = card_frame

        _current_set[0] = set_data
        types_str = ' · '.join(TYPE_LABELS.get(t, t) for t in sorted(set_data['types']))
        set_title_label.configure(
            text="📄 %s  ·  %d题  ·  %s" % (
                set_data['id'], set_data['total_questions'], types_str))

        # Populate section dropdown
        sec_labels = []
        for sec in set_data['sections']:
            icon = TYPE_ICONS.get(sec['type'], '📋')
            lbl = TYPE_LABELS.get(sec['type'], sec['type'])
            sec_labels.append("%s %s" % (icon, lbl))
        section_menu.configure(values=sec_labels)

        _show_section(0)

    # ── Render set list ─────────────────────────────────
    def _render_sets(filter_text=''):
        for w in set_list.winfo_children():
            w.destroy()

        _selected_card[0] = None

        for s in _sets:
            if filter_text and filter_text not in s['id']:
                continue

            # Card container frame (replaces CTkButton for better layout)
            card = ctk.CTkFrame(
                set_list, fg_color=_CARD_FG,
                corner_radius=6, height=50,
                cursor='hand2')
            card.pack(fill='x', pady=2, padx=2)
            card.pack_propagate(False)

            # Card line 1: ID
            id_label = ctk.CTkLabel(
                card, text="📄 %s" % s['id'],
                font=ctk.CTkFont(size=13, weight='bold'),
                text_color=_CARD_TEXT, anchor='w')
            id_label.pack(fill='x', padx=(8, 8), pady=(6, 0))

            # Card line 2: count + types
            type_tags = '  '.join(TYPE_LABELS.get(t, t) for t in sorted(s['types']))
            sub_label = ctk.CTkLabel(
                card, text="%d题 · %s" % (s['total_questions'], type_tags),
                font=ctk.CTkFont(size=11),
                text_color=_CARD_SUBTEXT, anchor='w')
            sub_label.pack(fill='x', padx=(8, 8), pady=(0, 4))

            # Store label refs on card frame for highlight access
            card._id_label = id_label
            card._sub_label = sub_label

            # Hover effects
            def _on_enter(event, cf=card, il=id_label, sl=sub_label):
                if _selected_card[0] != cf:
                    cf.configure(fg_color=_CARD_HOVER)

            def _on_leave(event, cf=card, il=id_label, sl=sub_label):
                if _selected_card[0] != cf:
                    cf.configure(fg_color=_CARD_FG)

            card.bind('<Enter>', _on_enter)
            card.bind('<Leave>', _on_leave)
            id_label.bind('<Enter>', _on_enter)
            id_label.bind('<Leave>', _on_leave)
            sub_label.bind('<Enter>', _on_enter)
            sub_label.bind('<Leave>', _on_leave)

            # Click handlers (on card + labels so clicking text also works)
            def _on_click(event, sd=s, cf=card, il=id_label, sl=sub_label):
                _on_set_click(sd, cf, il, sl)

            card.bind('<Button-1>', _on_click)
            id_label.bind('<Button-1>', _on_click)
            sub_label.bind('<Button-1>', _on_click)

    # ── Real-time search ────────────────────────────────
    def _on_search_change(*_):
        _render_sets(search_var.get().strip())

    search_var.trace_add('write', _on_search_change)

    # ── Load data ───────────────────────────────────────
    _sets = scan_sets()
    _render_sets()

    if not _sets:
        content_box.configure(state='normal')
        _t.insert('1.0',
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
