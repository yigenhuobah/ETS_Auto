#!/usr/bin/env python3
"""
ETS Browser UI — CustomTkinter offline paper browser widget.

Extracted from ets_parser.py to separate UI from data logic.
This module only builds the GUI; data scanning/rendering comes from ets_parser.

Usage:
  Integrated as Tab 2 in ets_gui.py via create_browser_tab()
  Standalone: python ets_browser_ui.py
"""
import os
import sys
import threading

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

from ets_parser import (
    scan_sets,
    render_section,
    _render_full_markdown,
    _render_full_html,
    _dedup_consecutive,
    TYPE_LABELS,
    TYPE_ICONS,
)

# ── Force UTF-8 on Windows ──────────────────────────────────
if sys.platform == 'win32':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, LookupError):
        pass


class BrowserTab:
    """Offline paper browser tab: set cards + search + section viewer.

    Replaces the former 420-line create_browser_tab closure so state is
    instance data and every handler is a testable method.
    """

    # ── Color palette (works for both light & dark) ─────
    CARD_FG = ('#f0f0f0', '#2b2b2b')
    CARD_HOVER = ('#dce6f0', '#354050')
    CARD_ACTIVE_FG = ('#cde0f0', '#2a4a6a')
    CARD_ACTIVE_TEXT = ('#1a5276', '#7ec8e3')
    CARD_TEXT = ('#333333', '#eeeeee')
    CARD_SUBTEXT = ('#666666', '#aaaaaa')

    SEARCH_DEBOUNCE_MS = 250

    def __init__(self, tab_frame):
        self.tab_frame = tab_frame
        self._current_set = None
        self._selected_card = None
        self._section_idx = 0
        self._sets = []
        self._scan_error = None
        self._scan_done = False
        self._search_job = None

        self._build_layout()
        # D-3: scan in a background thread — the synchronous scan used to run
        # on the Tk main thread during __init__ and froze startup.
        self._start_scan()

    # ── Layout ───────────────────────────────────────────

    def _build_layout(self):
        tab_frame = self.tab_frame
        tab_frame.grid_columnconfigure(1, weight=1)
        tab_frame.grid_rowconfigure(0, weight=1)

        # ── Left panel ──────────────────────────────────
        left = ctk.CTkFrame(tab_frame, width=260, corner_radius=8)
        left.grid(row=0, column=0, sticky='ns', padx=(8, 4), pady=8)
        left.grid_propagate(False)

        ctk.CTkLabel(
            left, text="📚 试卷列表",
            font=ctk.CTkFont(size=15, weight='bold')
        ).pack(pady=(10, 6), padx=12, anchor='w')

        self.search_var = ctk.StringVar(value='')
        search_entry = ctk.CTkEntry(
            left, placeholder_text="搜索ID / 题型 / 名称...",
            textvariable=self.search_var, width=236, height=32,
            corner_radius=6)
        search_entry.pack(padx=12, pady=(0, 6))

        set_list = ctk.CTkScrollableFrame(left, width=236, label_text='')
        set_list.pack(fill='both', expand=True, padx=12, pady=(0, 10))
        self.set_list = set_list

        # ── Right panel ─────────────────────────────────
        right = ctk.CTkFrame(tab_frame, corner_radius=8)
        right.grid(row=0, column=1, sticky='nsew', padx=(4, 8), pady=8)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        header_bar = ctk.CTkFrame(right, fg_color='transparent', height=40)
        header_bar.grid(row=0, column=0, sticky='ew', padx=12, pady=(10, 0))
        header_bar.grid_columnconfigure(1, weight=1)

        self.set_title_label = ctk.CTkLabel(
            header_bar, text="← 选择一份试卷",
            font=ctk.CTkFont(size=15, weight='bold'), anchor='w')
        self.set_title_label.grid(row=0, column=0, sticky='w')

        self.section_var = ctk.StringVar(value='')
        self.section_menu = ctk.CTkOptionMenu(
            header_bar, variable=self.section_var,
            values=[], width=260, height=30,
            font=ctk.CTkFont(size=12),
            command=lambda v: None)  # placeholder, wired in _wire_handlers
        self.section_menu.grid(row=0, column=1, sticky='e', padx=(8, 0))

        self.content_box = ctk.CTkTextbox(
            right, wrap='word', state='disabled',
            font=ctk.CTkFont(family='Microsoft YaHei UI', size=13),
            activate_scrollbars=True,
            corner_radius=6)
        self.content_box.grid(row=1, column=0, sticky='nsew', padx=12, pady=8)

        self._configure_text_tags()

        # ── Bottom navigation ───────────────────────────
        nav_frame = ctk.CTkFrame(right, fg_color='transparent', height=36)
        nav_frame.grid(row=2, column=0, sticky='ew', padx=12, pady=(0, 10))

        self.prev_btn = ctk.CTkButton(
            nav_frame, text="◀ 上一节", width=110, height=32,
            state='disabled', corner_radius=6)
        self.prev_btn.pack(side='left', padx=(0, 6))

        ctk.CTkLabel(
            nav_frame, textvariable=self.section_var,
            font=ctk.CTkFont(size=12), anchor='center').pack(side='left', padx=12)

        self.next_btn = ctk.CTkButton(
            nav_frame, text="下一节 ▶", width=110, height=32,
            state='disabled', corner_radius=6)
        self.next_btn.pack(side='left')

        ctk.CTkLabel(nav_frame, text="").pack(side='left', expand=True)

        export_md_btn = ctk.CTkButton(
            nav_frame, text="📋 导出MD", width=90, height=32,
            corner_radius=6, command=self._on_export_md)
        export_md_btn.pack(side='right', padx=(0, 4))

        print_btn = ctk.CTkButton(
            nav_frame, text="🖨 打印/预览", width=100, height=32,
            corner_radius=6, command=self._on_print)
        print_btn.pack(side='right', padx=(0, 4))

        self._wire_handlers()

    def _configure_text_tags(self):
        """Style the underlying tkinter Text of a CTkTextbox.

        `_textbox` is a private CustomTkinter detail; if it disappears in a
        future CTk version the browser degrades to unstyled text instead of
        crashing (inserts go through the public CTkTextbox API).
        """
        self._t = getattr(self.content_box, '_textbox', None)
        if self._t is None:
            return
        try:
            self._t.configure(spacing1=2, spacing3=4, padx=8, pady=4)
            self._t.tag_configure(
                'answer', foreground='#2ecc71',
                font=ctk.CTkFont(family='Microsoft YaHei UI', size=13, weight='bold'),
                lmargin1=20, lmargin2=20)
            self._t.tag_configure(
                'section_title', foreground='#3498db',
                font=ctk.CTkFont(family='Microsoft YaHei UI', size=15, weight='bold'),
                spacing1=8, spacing3=4)
            self._t.tag_configure(
                'q_num', foreground='#e67e22',
                font=ctk.CTkFont(family='Microsoft YaHei UI', size=13, weight='bold'),
                lmargin1=8)
            self._t.tag_configure(
                'header', foreground='#3498db',
                font=ctk.CTkFont(family='Microsoft YaHei UI', size=14, weight='bold'))
            self._t.tag_configure(
                'muted', foreground='#7f8c8d',
                font=ctk.CTkFont(family='Microsoft YaHei UI', size=12),
                lmargin1=20, lmargin2=20)
            self._t.tag_configure(
                'option', foreground='#95a5a6',
                font=ctk.CTkFont(family='Microsoft YaHei UI', size=13),
                lmargin1=30, lmargin2=30)
        except Exception:
            self._t = None

    def _wire_handlers(self):
        self.prev_btn.configure(command=self._on_prev)
        self.next_btn.configure(command=self._on_next)
        self.section_menu.configure(command=self._on_section_menu_select)
        self.search_var.trace_add('write', self._on_search_changed)

    # ── Background scan (D-3) ───────────────────────────

    def _start_scan(self):
        self._show_info("⏳ 正在扫描 ETS 缓存…")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            sets, err = scan_sets()
        except Exception as e:
            sets, err = [], str(e)
        try:
            self.tab_frame.after(0, self._on_scan_done, sets, err)
        except Exception:
            pass  # window destroyed mid-scan

    def _on_scan_done(self, sets, err):
        self._sets = sets or []
        self._scan_error = err
        self._scan_done = True
        self._render_sets()
        if not self._sets:
            msg = self._scan_error or "未找到 ETS 缓存数据"
            self._show_info(
                "⚠ 未找到 ETS 缓存数据\n\n"
                "%s\n\n"
                "请先在 ETS 客户端中开始一次作业，\n"
                "系统会自动缓存试卷数据。" % msg)

    def _show_info(self, message):
        self.content_box.configure(state='normal')
        self.content_box.delete('1.0', 'end')
        self.content_box.insert('1.0', message)
        self.content_box.configure(state='disabled')

    # ── Section display ─────────────────────────────────

    @staticmethod
    def _stid_suffix(stid):
        return (' ' + stid[-4:]) if len(stid) > 4 else (' ' + stid if stid else '')

    def _section_label(self, idx, sec):
        icon = TYPE_ICONS.get(sec['type'], '📋')
        lbl = TYPE_LABELS.get(sec['type'], sec['type'])
        return "%d. %s %s%s" % (idx + 1, icon, lbl, self._stid_suffix(sec.get('stid', '')))

    def _show_section(self, idx):
        if not self._current_set or idx < 0 or idx >= len(self._current_set['sections']):
            return
        self._section_idx = idx
        sec = self._current_set['sections'][idx]
        parts = render_section(sec)

        self.content_box.configure(state='normal')
        self.content_box.delete('1.0', 'end')
        for text, tag in parts:
            self.content_box.insert('end', text, tag or None)
        self.content_box.configure(state='disabled')
        self.content_box.see('1.0')

        total = len(self._current_set['sections'])
        self.section_var.set("%d / %d" % (idx + 1, total))
        self.prev_btn.configure(state='normal' if idx > 0 else 'disabled')
        self.next_btn.configure(state='normal' if idx < total - 1 else 'disabled')
        # Sync dropdown — include index for disambiguation
        self.section_menu.set(self._section_label(idx, sec))

    def _on_prev(self):
        self._show_section(self._section_idx - 1)

    def _on_next(self):
        self._show_section(self._section_idx + 1)

    def _on_section_menu_select(self, value):
        if not self._current_set:
            return
        # Value format: "N. icon label stid" — extract index N
        try:
            idx = int(value.split('.')[0]) - 1
            self._show_section(idx)
        except (ValueError, IndexError):
            pass

    # ── Set selection ───────────────────────────────────

    def _on_set_click(self, set_data, card_frame, id_label, sub_label, score_label):
        # Reset previous card highlight
        if self._selected_card and self._selected_card != card_frame:
            prev = self._selected_card
            self._unhighlight_card(prev, prev._id_label, prev._sub_label, prev._score_label)

        self._highlight_card(card_frame, id_label, sub_label, score_label)
        self._selected_card = card_frame

        self._current_set = set_data
        # Build rich title from res.json metadata
        score = set_data.get('score', 0)
        exam_names = set_data.get('exam_type_names', [])
        if exam_names:
            types_summary = ' · '.join(_dedup_consecutive(exam_names))
        else:
            types_summary = ' · '.join(TYPE_LABELS.get(t, t) for t in sorted(set_data['types']))
        score_text = "%d分" % score if score else ""
        header_parts = ["📄 %s" % set_data['id']]
        if score_text:
            header_parts.append(score_text)
        header_parts.append("%d题" % set_data['total_questions'])
        self.set_title_label.configure(
            text="  ·  ".join(header_parts) + "  ·  " + types_summary)

        # Populate section dropdown — include index + stid for disambiguation
        sec_labels = [self._section_label(i, sec)
                      for i, sec in enumerate(set_data['sections'])]
        self.section_menu.configure(values=sec_labels)

        self._show_section(0)

    # ── Card rendering ──────────────────────────────────

    def _highlight_card(self, card_frame, id_label, sub_label, score_label):
        """Apply active highlight to a card."""
        card_frame.configure(fg_color=self.CARD_ACTIVE_FG)
        id_label.configure(text_color=self.CARD_ACTIVE_TEXT)
        sub_label.configure(text_color=self.CARD_ACTIVE_TEXT)
        score_label.configure(text_color=self.CARD_ACTIVE_TEXT)

    def _unhighlight_card(self, card_frame, id_label, sub_label, score_label):
        """Remove active highlight from a card."""
        card_frame.configure(fg_color=self.CARD_FG)
        id_label.configure(text_color=self.CARD_TEXT)
        sub_label.configure(text_color=self.CARD_SUBTEXT)
        score_label.configure(text_color=self.CARD_SUBTEXT)

    @staticmethod
    def _matches(s, ft):
        """Search filter: ID, exam_type_names, section type labels."""
        if not ft:
            return True
        ft_lower = ft.lower()
        if ft_lower in s['id'].lower():
            return True
        for n in s.get('exam_type_names', []):
            if ft_lower in n.lower():
                return True
        for t in s['types']:
            lbl = TYPE_LABELS.get(t, t)
            if ft_lower in lbl.lower():
                return True
        return False

    def _render_sets(self, filter_text=''):
        for w in self.set_list.winfo_children():
            w.destroy()
        self._selected_card = None

        for s in self._sets:
            if not self._matches(s, filter_text):
                continue
            self._build_card(s)

    def _build_card(self, s):
        score = s.get('score', 0)
        exam_names = s.get('exam_type_names', [])

        card = ctk.CTkFrame(
            self.set_list, fg_color=self.CARD_FG,
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
            text_color=self.CARD_TEXT, anchor='w')
        id_label.pack(side='left')

        score_text = "%d分" % score if score else "—"
        score_label = ctk.CTkLabel(
            line1, text=score_text,
            font=ctk.CTkFont(size=11, weight='bold'),
            text_color=self.CARD_SUBTEXT, anchor='e')
        score_label.pack(side='right')

        # Card line 2: exam type names (from res.json) or fallback to section types
        if exam_names:
            display_names = _dedup_consecutive(exam_names)[:4]
            if len(_dedup_consecutive(exam_names)) > 4:
                display_names.append('...')
            type_tags = ' · '.join(display_names)
        else:
            type_tags = '  '.join(TYPE_LABELS.get(t, t) for t in sorted(s['types']))
        sub_label = ctk.CTkLabel(
            card, text="%d题 · %s" % (s['total_questions'], type_tags),
            font=ctk.CTkFont(size=11),
            text_color=self.CARD_SUBTEXT, anchor='w')
        sub_label.pack(fill='x', padx=(8, 8), pady=(0, 4))

        # Store label refs on card frame for highlight access
        card._id_label = id_label
        card._sub_label = sub_label
        card._score_label = score_label

        self._bind_card_events(s, card, id_label, sub_label, score_label)

    def _bind_card_events(self, s, card, id_label, sub_label, score_label):
        def _on_enter(event, cf=card):
            if self._selected_card != cf:
                cf.configure(fg_color=self.CARD_HOVER)

        def _on_leave(event, cf=card):
            if self._selected_card != cf:
                cf.configure(fg_color=self.CARD_FG)

        def _on_click(event, sd=s, cf=card, il=id_label, sl=sub_label, scl=score_label):
            self._on_set_click(sd, cf, il, sl, scl)

        card.bind('<Enter>', _on_enter)
        card.bind('<Leave>', _on_leave)
        id_label.bind('<Enter>', _on_enter)
        id_label.bind('<Leave>', _on_leave)
        sub_label.bind('<Enter>', _on_enter)
        sub_label.bind('<Leave>', _on_leave)
        score_label.bind('<Enter>', _on_enter)
        score_label.bind('<Leave>', _on_leave)

        # Click handlers (on card + labels so clicking text also works)
        card.bind('<Button-1>', _on_click)
        id_label.bind('<Button-1>', _on_click)
        sub_label.bind('<Button-1>', _on_click)
        score_label.bind('<Button-1>', _on_click)

    # ── Real-time search (debounced) ────────────────────

    def _on_search_changed(self, *_):
        # Debounce: destroying/rebuilding all cards per keystroke jitters on
        # large caches; merge keystrokes within SEARCH_DEBOUNCE_MS.
        if self._search_job is not None:
            try:
                self.tab_frame.after_cancel(self._search_job)
            except Exception:
                pass
        self._search_job = self.tab_frame.after(
            self.SEARCH_DEBOUNCE_MS,
            lambda: self._render_sets(self.search_var.get().strip()))

    # ── Export ──────────────────────────────────────────

    def _on_export_md(self):
        if not self._current_set:
            return
        from tkinter import messagebox
        set_data = self._current_set
        sid = set_data['id']
        # Save to Desktop
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        out_path = os.path.join(desktop, 'ETS_%s.md' % sid)
        try:
            if os.path.exists(out_path):
                if not messagebox.askyesno('文件已存在',
                        '桌面已存在 ETS_%s.md\n是否覆盖？' % sid):
                    return
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(_render_full_markdown(set_data))
        except OSError as e:
            # OneDrive redirection / permissions must not die silently.
            messagebox.showerror('导出失败', '写入失败：%s\n%s' % (out_path, e))
            return
        messagebox.showinfo('导出成功', '已保存到:\n%s' % out_path)

    def _on_print(self):
        if not self._current_set:
            return
        import subprocess
        from tkinter import messagebox
        html_text = _render_full_html(self._current_set)
        sid = self._current_set['id']
        # Write temp HTML
        tmp_dir = os.path.join(os.environ.get('TEMP', os.path.expanduser('~')), 'ets_preview')
        tmp_path = os.path.join(tmp_dir, 'ETS_%s.html' % sid)
        try:
            os.makedirs(tmp_dir, exist_ok=True)
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write(html_text)
            subprocess.Popen(['cmd', '/c', 'start', '', tmp_path], shell=False)
        except OSError as e:
            messagebox.showerror('预览失败', '写入临时文件失败：\n%s' % e)


def create_browser_tab(tab_frame):
    """Build the offline paper browser UI inside a CTkTabview tab.

    Layout:
      Left panel  (260px): set list with real-time search
      Right panel (flex):  header + section dropdown + content + nav
    """
    BrowserTab(tab_frame)
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
