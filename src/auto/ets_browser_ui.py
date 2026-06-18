#!/usr/bin/env python3
"""
ETS Browser UI — CustomTkinter offline paper browser widget.

Extracted from ets_parser.py to separate UI from data logic.
This module only builds the GUI; data scanning/rendering comes from ets_parser.

Usage:
  Integrated as Tab 2 in ets_gui.py via create_browser_tab()
  Standalone: python ets_browser_ui.py
"""
import sys
import os

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


def create_browser_tab(tab_frame):
    """Build the offline paper browser UI inside a CTkTabview tab.

    Layout:
      Left panel  (260px): set list with real-time search
      Right panel (flex):  header + section dropdown + content + nav
    """
    from ets_parser import scan_sets, render_section, _render_full_markdown, _render_full_html
    from ets_parser import TYPE_LABELS, TYPE_ICONS

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
        left, placeholder_text="搜索ID / 题型 / 名称...",
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
        values=[], width=260, height=30,
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

    # Spacer
    ctk.CTkLabel(nav_frame, text="").pack(side='left', expand=True)

    # Export buttons (right side)
    def _on_export_md():
        if not _current_set[0]:
            return
        from tkinter import messagebox
        md_text = _render_full_markdown(_current_set[0])
        sid = _current_set[0]['id']
        # Save to Desktop
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        out_path = os.path.join(desktop, 'ETS_%s.md' % sid)
        if os.path.exists(out_path):
            if not messagebox.askyesno('文件已存在',
                    '桌面已存在 ETS_%s.md\n是否覆盖？' % sid):
                return
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(md_text)
        messagebox.showinfo('导出成功', '已保存到:\n%s' % out_path)

    def _on_print():
        if not _current_set[0]:
            return
        html_text = _render_full_html(_current_set[0])
        sid = _current_set[0]['id']
        # Write temp HTML
        tmp_dir = os.path.join(os.environ.get('TEMP', os.path.expanduser('~')), 'ets_preview')
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, 'ETS_%s.html' % sid)
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(html_text)
        import subprocess
        subprocess.Popen(['cmd', '/c', 'start', '', tmp_path], shell=False)

    export_md_btn = ctk.CTkButton(
        nav_frame, text="📋 导出MD", width=90, height=32,
        corner_radius=6,
        command=_on_export_md)
    export_md_btn.pack(side='right', padx=(0, 4))

    print_btn = ctk.CTkButton(
        nav_frame, text="🖨 打印/预览", width=100, height=32,
        corner_radius=6,
        command=_on_print)
    print_btn.pack(side='right', padx=(0, 4))

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
        # Sync dropdown — include index for disambiguation
        icon = TYPE_ICONS.get(sec['type'], '📋')
        lbl = TYPE_LABELS.get(sec['type'], sec['type'])
        stid = sec.get('stid', '')
        stid_short = (' ' + stid[-4:]) if len(stid) > 4 else (' ' + stid if stid else '')
        section_menu.set("%d. %s %s%s" % (idx + 1, icon, lbl, stid_short))

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
        # Value format: "N. icon label stid" — extract index N
        try:
            idx = int(value.split('.')[0]) - 1
            _show_section(idx)
        except (ValueError, IndexError):
            pass

    section_menu.configure(command=_on_section_menu_select)

    # ── Card click & highlight helpers ──────────────────
    def _highlight_card(card_frame, id_label, sub_label, score_label):
        """Apply active highlight to a card."""
        card_frame.configure(fg_color=_CARD_ACTIVE_FG)
        id_label.configure(text_color=_CARD_ACTIVE_TEXT)
        sub_label.configure(text_color=_CARD_ACTIVE_TEXT)
        score_label.configure(text_color=_CARD_ACTIVE_TEXT)

    def _unhighlight_card(card_frame, id_label, sub_label, score_label):
        """Remove active highlight from a card."""
        card_frame.configure(fg_color=_CARD_FG)
        id_label.configure(text_color=_CARD_TEXT)
        sub_label.configure(text_color=_CARD_SUBTEXT)
        score_label.configure(text_color=_CARD_SUBTEXT)

    def _on_set_click(set_data, card_frame, id_label, sub_label, score_label):
        # Reset previous card highlight
        if _selected_card[0] and _selected_card[0] != card_frame:
            prev = _selected_card[0]
            _unhighlight_card(
                prev, prev._id_label, prev._sub_label, prev._score_label)

        # Highlight current card
        _highlight_card(card_frame, id_label, sub_label, score_label)
        _selected_card[0] = card_frame

        _current_set[0] = set_data
        # Build rich title from res.json metadata
        score = set_data.get('score', 0)
        exam_names = set_data.get('exam_type_names', [])
        if exam_names:
            # Deduplicate consecutive identical names
            deduped = []
            for n in exam_names:
                if not deduped or deduped[-1] != n:
                    deduped.append(n)
            types_summary = ' · '.join(deduped)
        else:
            types_summary = ' · '.join(TYPE_LABELS.get(t, t) for t in sorted(set_data['types']))
        score_text = "%d分" % score if score else ""
        header_parts = ["📄 %s" % set_data['id']]
        if score_text:
            header_parts.append(score_text)
        header_parts.append("%d题" % set_data['total_questions'])
        set_title_label.configure(
            text="  ·  ".join(header_parts) + "  ·  " + types_summary)

        # Populate section dropdown — include index + stid for disambiguation
        sec_labels = []
        for i, sec in enumerate(set_data['sections']):
            icon = TYPE_ICONS.get(sec['type'], '📋')
            lbl = TYPE_LABELS.get(sec['type'], sec['type'])
            stid = sec.get('stid', '')
            stid_short = (' ' + stid[-4:]) if len(stid) > 4 else (' ' + stid if stid else '')
            sec_labels.append("%d. %s %s%s" % (i + 1, icon, lbl, stid_short))
        section_menu.configure(values=sec_labels)

        _show_section(0)

    # ── Render set list ─────────────────────────────────
    def _render_sets(filter_text=''):
        for w in set_list.winfo_children():
            w.destroy()

        _selected_card[0] = None

        # Build search index: combine ID + exam type names + section type labels
        def _matches(s, ft):
            if not ft:
                return True
            ft_lower = ft.lower()
            if ft_lower in s['id'].lower():
                return True
            # Match exam_type_names from res.json
            for n in s.get('exam_type_names', []):
                if ft_lower in n.lower():
                    return True
            # Match section type labels
            for t in s['types']:
                lbl = TYPE_LABELS.get(t, t)
                if ft_lower in lbl.lower():
                    return True
            return False

        for s in _sets:
            if not _matches(s, filter_text):
                continue

            score = s.get('score', 0)
            exam_names = s.get('exam_type_names', [])

            # Card container frame
            card = ctk.CTkFrame(
                set_list, fg_color=_CARD_FG,
                corner_radius=6, height=60,
                cursor='hand2')
            card.pack(fill='x', pady=2, padx=2)
            card.pack_propagate(False)

            # Card line 1: ID (left) + Score badge (right)
            line1 = ctk.CTkFrame(card, fg_color='transparent', height=22)
            line1.pack(fill='x', padx=(8, 8), pady=(6, 0))
            line1.pack_propagate(False)

            id_label = ctk.CTkLabel(
                line1, text="📄 %s" % s['id'],
                font=ctk.CTkFont(size=13, weight='bold'),
                text_color=_CARD_TEXT, anchor='w')
            id_label.pack(side='left')

            # Score badge on the right
            score_text = "%d分" % score if score else "—"
            score_label = ctk.CTkLabel(
                line1, text=score_text,
                font=ctk.CTkFont(size=11, weight='bold'),
                text_color=_CARD_SUBTEXT, anchor='e')
            score_label.pack(side='right')

            # Card line 2: exam type names (from res.json) or fallback to section types
            if exam_names:
                # Deduplicate consecutive identical names for compact display
                deduped = []
                for n in exam_names:
                    if not deduped or deduped[-1] != n:
                        deduped.append(n)
                # Limit to first 4 names, add "..." if more
                display_names = deduped[:4]
                if len(deduped) > 4:
                    display_names.append('...')
                type_tags = ' · '.join(display_names)
            else:
                type_tags = '  '.join(TYPE_LABELS.get(t, t) for t in sorted(s['types']))
            sub_label = ctk.CTkLabel(
                card, text="%d题 · %s" % (s['total_questions'], type_tags),
                font=ctk.CTkFont(size=11),
                text_color=_CARD_SUBTEXT, anchor='w')
            sub_label.pack(fill='x', padx=(8, 8), pady=(0, 4))

            # Store label refs on card frame for highlight access
            card._id_label = id_label
            card._sub_label = sub_label
            card._score_label = score_label

            # Hover effects
            def _on_enter(event, cf=card, il=id_label, sl=sub_label, scl=score_label):
                if _selected_card[0] != cf:
                    cf.configure(fg_color=_CARD_HOVER)

            def _on_leave(event, cf=card, il=id_label, sl=sub_label, scl=score_label):
                if _selected_card[0] != cf:
                    cf.configure(fg_color=_CARD_FG)

            card.bind('<Enter>', _on_enter)
            card.bind('<Leave>', _on_leave)
            id_label.bind('<Enter>', _on_enter)
            id_label.bind('<Leave>', _on_leave)
            sub_label.bind('<Enter>', _on_enter)
            sub_label.bind('<Leave>', _on_leave)
            score_label.bind('<Enter>', _on_enter)
            score_label.bind('<Leave>', _on_leave)

            # Click handlers (on card + labels so clicking text also works)
            def _on_click(event, sd=s, cf=card, il=id_label, sl=sub_label, scl=score_label):
                _on_set_click(sd, cf, il, sl, scl)

            card.bind('<Button-1>', _on_click)
            id_label.bind('<Button-1>', _on_click)
            sub_label.bind('<Button-1>', _on_click)
            score_label.bind('<Button-1>', _on_click)

    # ── Real-time search ────────────────────────────────
    def _on_search_change(*_):
        _render_sets(search_var.get().strip())

    search_var.trace_add('write', _on_search_change)

    # ── Load data ───────────────────────────────────────
    _sets, _scan_error = scan_sets()
    _render_sets()

    if not _sets:
        msg = _scan_error or "未找到 ETS 缓存数据"
        content_box.configure(state='normal')
        _t.insert('1.0',
            "⚠ 未找到 ETS 缓存数据\n\n"
            "%s\n\n"
            "请先在 ETS 客户端中开始一次作业，\n"
            "系统会自动缓存试卷数据。" % msg)
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
