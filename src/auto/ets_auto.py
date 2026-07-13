#!/usr/bin/env python3
"""
ETS Exam Auto — e听说PC端套卷自动答题工具
CDP + JS注入DOM，支持听后选择和听后记录题型。

Usage:
  python ets_auto.py              # 自动答题（默认安全上限 999 步）
  python ets_auto.py --max 50     # 限制步数
  python ets_auto.py --debug      # 调试模式
  python ets_auto.py --show-answers  # 仅查看答案
  python ets_auto.py --json       # JSON 输出
"""
import json, os, time, sys, threading, re
from urllib.parse import urlparse, parse_qs
from urllib.error import URLError

from ets_common import APP_VERSION, ETSBase, force_utf8_stdio
from ets_recording_ui import ETSRecordingMixin
from ets_rw_mode import ETSReadWriteMixin
from ets_tee import TeeOutput

# Re-export (single source: ets_common.APP_VERSION)
__version__ = APP_VERSION
# Backward-compatible re-export for run.py / external imports
__all__ = ['ETSAutoAnswer', 'TeeOutput', 'APP_VERSION']

from ets_strategy import (
    ETSStrategy, _safe_set_id, _resolve_exam_dir, _read_json, _html_to_text,
)


class ETSAutoAnswer(ETSRecordingMixin, ETSReadWriteMixin, ETSBase):
    def __init__(self, port=10086, debug_mode=False, stop_event=None):
        super().__init__(port=port, debug_mode=debug_mode, stop_event=stop_event)
        # C1: always have a real Event so .set() never crashes on CLI paths
        self.ensure_stop_event()
        self.ets_base = None
        self.answers = {}
        self.set_id = None
        self.homework_mode = None
        self.homework_id = None
        self.answered_questions = []
        self._recording_window_closed = False
        self._rec_done_event = None  # C2: GUI recording Toplevel done signal
        self.total_questions = 0
        self.recording_answers = []   # list of dicts for picture/dialogue
        self.strategy = ETSStrategy()  # strategy layer for local answer lookup
        self.rw_mode = False  # read-write (读写同步) mode flag
        self.rw_show_data = None  # cached showData from iframe
        self._rw_cache_time = 0   # timestamp when rw_show_data was fetched
        # Legacy callback (not in base — specific to exam mode)
        self._on_question_answered = None # fn(qid, answer, qtype) where qtype='choose'|'fill'
        self.stats = {
            'choose_answered': 0, 'choose_skip': 0,
            'fill_answered': 0, 'fill_skip': 0,
            'next_click': 0, 'errors': 0
        }

    def _signal_stop(self):
        """C1: safely set stop_event (delegates to ETSBase.signal_stop)."""
        self.signal_stop()

    @staticmethod
    def _is_next_waiting(reason):
        """True when Next is temporarily unavailable (disabled or audio/timer hide)."""
        if not reason:
            return False
        return reason == 'disabled' or reason.startswith('next_icon hidden')

    # ── Public API (CLI + GUI) ─────────────────────────────

    def get_all_answers(self):
        """Return all loaded answers. Usable before or after run().
        Returns: dict like {'82750_1': {'type': 'choose', 'answer': 'C'}, ...}"""
        return dict(self.answers)

    def show_answers(self):
        """Print all answers for current exam."""
        if not self.answers:
            print("No answers loaded")
            return
        print("\nAnswers for set_id=%s:\n" % (self.set_id or "?"))
        for key, val in sorted(self.answers.items()):
            tag = {'choose': '[CHS]', 'fill': '[FIL]', 'dialogue': '[DLG]', 'picture': '[PIC]', 'read': '[RD]', 'role': '[ROL]'}.get(val['type'], '[??]')
            print("  %s %s → %s" % (tag, key, val['answer']))
        print("\n%d total answers" % len(self.answers))

    def on_question_answered(self, fn):
        """Register callback: fn(qid, answer, qtype). Called per question (exam-specific)."""
        self._on_question_answered = fn

    # ── Connection ────────────────────────────────────────────

    def connect(self):
        """Connect to ETS and read Pinia config for dynamic paths/mode."""
        super().connect()

        # Read Pinia stores for dynamic config + mode detection
        self._read_pinia_config()

        # Fallback: extract set_id from URL if Pinia didn't give us one
        if not self.set_id:
            self.set_id = self._get_url_set_id()
            if self.set_id:
                self.debug("set_id from URL: " + self.set_id)

        # Last-resort fallback for ets_base (must be before _find_latest_set_id)
        if not self.ets_base:
            self.ets_base = os.path.join(os.path.expandvars(r'%APPDATA%'), 'ETS')
            self.debug("ets_base (default): " + self.ets_base)

        # #5: Last-resort fallback — scan ETS data dir for most recent set
        if not self.set_id and self.ets_base:
            self.set_id = self._find_latest_set_id()
            if self.set_id:
                self.debug("set_id from latest scan: " + self.set_id)

        # Detect read-write (读写同步) mode from URL hash
        self._detect_rw_mode()

    def _tab_url(self):
        """Safe tab URL after reconnect may set tab=None (_drop_connection)."""
        tab = self.tab
        if not isinstance(tab, dict):
            return ''
        return str(tab.get('url') or '')

    def _get_url_set_id(self):
        """Extract set_id from URL fragment query string.
        Supports both snake_case 'set_id' and camelCase 'setId' (ETS uses both)."""
        try:
            parsed = urlparse(self._tab_url())
            fragment = parsed.fragment
            if '?' in fragment:
                qs = parse_qs(fragment.split('?', 1)[1])
                # Try snake_case first, then camelCase
                for key in ('set_id', 'setId'):
                    if key in qs:
                        return qs[key][0]
        except Exception:
            pass
        return None

    def _find_latest_set_id(self):
        """#5: Scan ETS data dir for the most recently modified set directory.

        Used as a last-resort fallback when neither Pinia nor URL provide
        a set_id. Returns the directory name (set_id) or None.
        """
        if not self.ets_base or not os.path.isdir(self.ets_base):
            return None
        try:
            candidates = []
            for entry in os.listdir(self.ets_base):
                # Digits-only set_id (same rule as strategy._safe_set_id)
                if not entry.isdigit():
                    continue
                full = os.path.join(self.ets_base, entry)
                if not os.path.isdir(full):
                    continue
                # Set dirs contain content_* subdirectories
                has_content = any(
                    d.startswith('content_') for d in os.listdir(full)
                    if os.path.isdir(os.path.join(full, d))
                )
                if has_content:
                    mtime = os.path.getmtime(full)
                    candidates.append((mtime, entry))
            if not candidates:
                return None
            candidates.sort(reverse=True)  # newest first
            self.debug("Latest set_id scan found %d candidates, newest: %s" %
                       (len(candidates), candidates[0][1]))
            return candidates[0][1]
        except Exception as e:
            self.debug("_find_latest_set_id error: %s" % e)
            return None

    def _read_pinia_config(self):
        """Read ETS config + homework mode from main-frame Pinia stores."""
        js = '''(function(){
        try {
            var app = document.getElementById('app');
            if (!app || !app.__vue_app__) return JSON.stringify({error: "no vue3 app"});
            var pinia = app.__vue_app__.config.globalProperties.$pinia;
            var result = {};
            var cfg = pinia.state.value.appConfig || {};
            result.appDataPath = cfg.appDataPath || '';
            var hw = pinia.state.value.homeworkStore || {};
            result.doHomework = !!hw.doHomework;
            result.homework_id = String(hw.homework_id || hw.dataModel?.current_class_id || '');
            result.hw_set_id = String(hw.dataModel?.current_class_id || '');
            return JSON.stringify(result);
        } catch(e) { return JSON.stringify({error: e.message}); }
        })()'''
        result = self.eval_js(js)
        if not result:
            return
        cfg = self.parse_eval_json(result)
        if cfg.get('error'):
            self.debug("Pinia error: " + cfg['error'])
            return
        try:
            if cfg.get('appDataPath'):
                # Normalize separators + trailing slash so ".../ETS/" still
                # counts as the ETS root (endswith('/ETS') alone false-negatives).
                path = cfg['appDataPath'].replace('\\', '/').rstrip('/')
                if not path.upper().endswith('/ETS'):
                    path = path + '/ETS'
                self.ets_base = path
                self.debug("Pinia: dataPath=" + self.ets_base)
            self.homework_mode = cfg.get('doHomework')
            self.homework_id = str(cfg.get('homework_id') or '')
            hw_set = str(cfg.get('hw_set_id') or '')
            # H6: only trust homework store set_id when actually in homework mode;
            # practice/mock keeps residual current_class_id which would load wrong answers.
            if hw_set and self.homework_mode:
                self.set_id = hw_set
                self.debug("Pinia: set_id=" + self.set_id + " (homework)")
            elif hw_set and not self.homework_mode:
                self.debug("Pinia: ignoring hw_set_id=%s (not doHomework; prefer URL)" % hw_set)
            mode_str = "HOMEWORK" if self.homework_mode else ("PRACTICE" if self.homework_mode is False else "UNKNOWN")
            self.debug("Pinia: mode=" + mode_str)
        except Exception as e:
            self.debug("Pinia parse error: " + str(e))

    # ── Bridge Injection ──────────────────────────────────────

    def inject_bridge(self):
        """Wrap kttb_ReturnChoose / kttb_returnPcBlank (idempotent).
        In homework mode: call native CEF function first, then record.
        In practice mode: record only (no native function exists).
        Guards against re-wrapping: checks __ets_hooked flag so repeated
        calls on the same iframe don't nest wrappers."""
        js = '''(function(){
        %s;
        if (!iframe) return JSON.stringify({nativeChoose: false, nativeFill: false, error: "no iframe"});
        var win = iframe.contentWindow;
        window.top.__ets_recorded = window.top.__ets_recorded || [];
        window.top.__ets_recorded_fill = window.top.__ets_recorded_fill || [];
        /* Idempotent guard: skip if already hooked on this iframe */
        if (win.__ets_hooked) {
            return JSON.stringify({nativeChoose: !!win.__ets_origChoose, nativeFill: !!win.__ets_origBlank, skipped: true});
        }
        var hadNativeChoose = typeof win.kttb_ReturnChoose === 'function';
        var hadNativeFill = typeof win.kttb_returnPcBlank === 'function';
        var _origChoose = win.kttb_ReturnChoose;
        var _origBlank = win.kttb_returnPcBlank;
        win.kttb_ReturnChoose = function(data) {
            if (_origChoose && typeof _origChoose === 'function') {
                try { _origChoose(data); } catch(e) {}
            }
            window.top.__ets_recorded.push(data);
            /* Drain: keep array bounded to prevent V8 heap OOM */
            if (window.top.__ets_recorded.length > 200) {
                window.top.__ets_recorded = window.top.__ets_recorded.slice(-100);
            }
        };
        Object.defineProperty(win.kttb_ReturnChoose, 'toString', {
            value: function() { return "function kttb_ReturnChoose() { [native code] }"; },
            enumerable: false, configurable: false
        });
        win.kttb_returnPcBlank = function(data) {
            if (_origBlank && typeof _origBlank === 'function') {
                try { _origBlank(data); } catch(e) {}
            }
            window.top.__ets_recorded_fill.push(data);
            if (window.top.__ets_recorded_fill.length > 200) {
                window.top.__ets_recorded_fill = window.top.__ets_recorded_fill.slice(-100);
            }
        };
        Object.defineProperty(win.kttb_returnPcBlank, 'toString', {
            value: function() { return "function kttb_returnPcBlank() { [native code] }"; },
            enumerable: false, configurable: false
        });
        /* Mark as hooked and stash originals for idempotent guard */
        win.__ets_hooked = true;
        win.__ets_origChoose = hadNativeChoose ? _origChoose : null;
        win.__ets_origBlank = hadNativeFill ? _origBlank : null;
        return JSON.stringify({
            nativeChoose: hadNativeChoose,
            nativeFill: hadNativeFill
        });
        })()''' % self._IFRAME_FINDER
        result = self.eval_js(js)
        info = self.parse_eval_json(result)
        if info.get('error'):
            self.debug("Bridge result: " + str(result))
            return {}
        mode = "HOMEWORK" if info.get('nativeChoose') else "PRACTICE"
        self.debug("Bridge: " + mode)
        return info

    # ── RW iframe helper ────────────────────────────────────

    # Shared JS snippet: find read-write iframe with fallback
    _RW_IFRAME_FINDER = r'''
    var iframe = document.querySelector("iframe[src*=read-write]");
    if (!iframe) {
        var iframes = document.querySelectorAll("iframe");
        for (var _i = 0; _i < iframes.length; _i++) {
            var _src = iframes[_i].getAttribute("src") || "";
            if (_src && _src !== "about:blank") { iframe = iframes[_i]; break; }
        }
    }'''

    # Shared JS snippet: find first content iframe (normal mode)
    _IFRAME_FINDER = r'''
    var iframe = document.querySelector("iframe");
    if (!iframe) {
        var iframes = document.querySelectorAll("iframe");
        for (var _i = 0; _i < iframes.length; _i++) {
            var _src = iframes[_i].getAttribute("src") || "";
            if (_src && _src !== "about:blank") { iframe = iframes[_i]; break; }
        }
    }'''

    # ── Read-Write (读写同步) Mode ──────────────────────────

    def load_answers(self):
        """Load answers from local ETS cache (content.json per content_* dir)."""
        import re as _html_re
        import html as _html_mod

        def _strip_html(t):
            """Preserve newlines for recording text; do not use strategy flatten."""
            if not t:
                return ''
            t = _html_re.sub(r'</p>\s*<p[^>]*>', '\n', t, flags=_html_re.IGNORECASE)
            t = _html_re.sub(r'<br\s*/?>', '\n', t, flags=_html_re.IGNORECASE)
            t = _html_re.sub(r'</?[a-zA-Z][^>]*>', '', t)
            t = _html_mod.unescape(t)
            t = _html_re.sub(r' {2,}', ' ', t)
            t = _html_re.sub(r'\n{3,}', '\n\n', t)
            return t.strip()

        # C5: clear prior load so reconnect/set change never merges stale keys
        self.answers = {}
        self.recording_answers = []
        self.total_questions = 0

        if not self.set_id:
            print("ERROR: No set_id available (not in Pinia, not in URL)")
            return False
        # Path-safe resolve (same guards as strategy.load_set): digits-only
        # set_id and exam_dir must stay under ets_base (no traversal).
        if not self.ets_base:
            print("ERROR: No ets_base (ETS data root) available")
            return False
        safe_id = _safe_set_id(self.set_id)
        exam_dir = _resolve_exam_dir(safe_id, self.ets_base) if safe_id else None
        if exam_dir is None:
            url_set_id = _safe_set_id(self._get_url_set_id())
            if url_set_id and url_set_id != safe_id:
                alt_dir = _resolve_exam_dir(url_set_id, self.ets_base)
                if alt_dir is not None:
                    self.debug("Pinia set_id %s not found, using URL set_id: %s" % (
                        self.set_id, url_set_id))
                    safe_id = url_set_id
                    exam_dir = alt_dir
        if exam_dir is None:
            print("ERROR: Exam data not found for set_id=%s under %s" % (
                self.set_id, self.ets_base))
            return False
        self.set_id = safe_id

        self.debug("Loading from: " + exam_dir)
        skipped_content = 0
        for d in sorted(os.listdir(exam_dir)):
            if not d.startswith('content_'):
                continue
            cj = os.path.join(exam_dir, d, 'content.json')
            if not os.path.exists(cj):
                continue
            try:
                data = _read_json(cj)
                stype = data.get('structure_type', '')
                info = data.get('info', {})
                stid = info.get('stid', '')
                if stype == 'collector.choose':
                    for xt in info.get('xtlist', []):
                        if not isinstance(xt, dict):
                            continue
                        xt_xh = xt.get('xt_xh')
                        if xt_xh is None or xt_xh == '':
                            self.debug("Skip choose item missing xt_xh in %s" % d)
                            continue
                        key = stid + '_' + str(xt_xh)
                        ans = xt.get('answer', '')
                        if ans:
                            self.answers[key] = {'type': 'choose', 'answer': ans}
                elif stype == 'collector.fill':
                    for std in info.get('std', []):
                        if not isinstance(std, dict):
                            continue
                        xth = std.get('xth')
                        if xth is None or xth == '':
                            self.debug("Skip fill item missing xth in %s" % d)
                            continue
                        key = stid + '_' + str(xth)
                        ans = std.get('value', '')
                        if ans:
                            alternatives = []
                            if '/' in ans:
                                parts = [p.strip() for p in ans.split('/') if p.strip()]
                                ans = parts[0]
                                alternatives = parts[1:]
                                self.debug("Fill split '%s' -> '%s' + alts %s" % (
                                    '/'.join(parts), ans, alternatives))
                            self.answers[key] = {'type': 'fill', 'answer': ans}
                            if alternatives:
                                self.answers[key]['alternatives'] = alternatives
                elif stype == 'collector.read':
                    key = stid
                    ref_text = _strip_html(info.get('value', ''))
                    symbol = info.get('symbol', '')
                    if ref_text:
                        self.answers[key] = {'type': 'read', 'answer': ref_text, 'symbol': symbol}
                        self.recording_answers.append({'stid': stid, 'type': 'read', 'answer': ref_text, 'symbol': symbol})
                elif stype == 'collector.picture':
                    key = stid
                    ref_text = _strip_html(info.get('value', ''))
                    topic = info.get('topic', '')
                    if not ref_text:
                        # H8: strip keypoint the same way as value
                        ref_text = _strip_html(info.get('keypoint', ''))
                    if not ref_text:
                        ref_text = '\n\n'.join([
                            _strip_html(s.get('value', '')) for s in info.get('std', []) if s.get('value', '')
                        ])
                    if ref_text:
                        self.answers[key] = {'type': 'picture', 'answer': ref_text, 'topic': topic}
                        self.recording_answers.append({'stid': stid, 'type': 'picture', 'topic': topic, 'answer': ref_text})
                elif stype in ('collector.dialogue', 'collector.role'):
                    # H7: role uses same q_answers / recording_answers build as dialogue
                    key = stid
                    rec_type = 'role' if stype == 'collector.role' else 'dialogue'
                    questions = info.get('question', [])
                    # Build per-question reference answers from std (shortest standard variant)
                    q_answers = []
                    for qi, q in enumerate(questions):
                        q_ask = _strip_html(q.get('ask', ''))
                        q_std_list = q.get('std', [])
                        # std is a list of acceptable answer variants; pick shortest clean one
                        best_ans = ''
                        if q_std_list:
                            candidates = []
                            for s in q_std_list:
                                v = s.get('value', '') if isinstance(s, dict) else str(s)
                                v = _strip_html(v)
                                if v:
                                    candidates.append(v)
                            if candidates:
                                best_ans = min(candidates, key=len)
                        q_answers.append({'ask': q_ask, 'answer': best_ans})

                    # Also store full material text for reference
                    material_plain = _strip_html(info.get('value', ''))

                    if q_answers:
                        q_texts = [qa['ask'] for qa in q_answers]
                        self.answers[key] = {'type': rec_type, 'answer': material_plain, 'questions': q_texts, 'q_answers': q_answers}
                        self.recording_answers.append({'stid': stid, 'type': rec_type, 'questions': q_texts, 'answer': material_plain, 'q_answers': q_answers})
                    elif material_plain:
                        # Fallback: no per-question answers, use material text only
                        self.answers[key] = {'type': rec_type, 'answer': material_plain, 'questions': [], 'q_answers': []}
                        self.recording_answers.append({'stid': stid, 'type': rec_type, 'questions': [], 'answer': material_plain, 'q_answers': []})
            except Exception as e:
                skipped_content += 1
                self.debug("Error loading %s: %s" % (d, e))
                print("  ⚠ Skip unreadable content dir %s: %s" % (d, e))

        if skipped_content:
            print("  ⚠ load_answers skipped %d content_* dir(s) due to errors" % skipped_content)

        # H22: auto progress denominator is choose+fill only (recording stays separate)
        self.total_questions = sum(
            1 for v in self.answers.values() if v.get('type') in ('choose', 'fill')
        )
        print("Loaded %d auto answers + %d recording (set_id=%s)" % (
            self.total_questions, len(self.recording_answers), self.set_id))

        # ── Load strategy layer (composite key index + fallback chain) ──
        if self.set_id:
            strat_ok = self.strategy.load_set(self.set_id, data_dir=self.ets_base)
            if strat_ok:
                print("Strategy layer: %d sections, %d indexed answers" % (
                    len(self.strategy.sections), len(self.strategy.answer_index)))
            else:
                self.debug("Strategy layer: no cache data for set_id=%s" % self.set_id)

        return self.total_questions > 0 or len(self.recording_answers) > 0

    # ── Page State ────────────────────────────────────────────

    def get_page_state(self):
        """Get current iframe state: choices grouped by question, fill inputs."""
        js = r'''(function(){
        %s;
        if (!iframe) return JSON.stringify({error: "no iframe"});
        var doc = iframe.contentDocument || iframe.contentWindow.document;
        if (!doc) return JSON.stringify({error: "no doc"});
        var choices = doc.querySelectorAll(".choose2");
        var groups = {};
        var choiceInfo = [];
        var hasReviewMode = false;
        choices.forEach(function(c){
            if (c.offsetHeight <= 0) return;  // skip hidden (Vue ghost DOM)
            var id = c.id || "";
            // Extract qid: strip trailing _N (option index like _1, _2, _3, _4)
            var qid = id.replace(/_\d+$/, '');
            if (!groups[qid]) groups[qid] = {qid: qid, choices: [], anySelected: false, inReview: false};
            var sel = c.classList.contains("choose_selected") || c.classList.contains("on");
            var isWrong = c.classList.contains("choose_wrong");
            var isCorrect = c.classList.contains("choose_correct");
            var isDisabled = c.classList.contains("choose_disable");
            if (isWrong || isCorrect || isDisabled) {
                groups[qid].inReview = true;
                hasReviewMode = true;
            }
            groups[qid].choices.push({id: id, selected: sel, text: c.textContent.trim().substring(0, 40), wrong: isWrong, correct: isCorrect, disabled: isDisabled});
            if (sel) groups[qid].anySelected = true;
            choiceInfo.push({id: id, selected: sel, text: c.textContent.trim().substring(0, 40)});
        });
        var groupList = [];
        for (var k in groups) groupList.push(groups[k]);
        var inputs = doc.querySelectorAll(".fill_word_input, input.fill_word_input, input[type='text']");
        var inputInfo = [];
        inputs.forEach(function(inp){
            if (inp.offsetHeight <= 0) return;  // skip hidden (Vue ghost DOM)
            inputInfo.push({id: inp.id || "", value: inp.value || ""});
        });
        return JSON.stringify({
            choices: choiceInfo, question_groups: groupList, inputs: inputInfo,
            hasChoice: choiceInfo.length > 0, hasInput: inputInfo.length > 0,
            inReviewMode: hasReviewMode
        });
        })()''' % self._IFRAME_FINDER
        result = self.eval_js(js)
        # Distinguish CDP/JS failure from a real empty page (no choices/inputs).
        return self.parse_eval_json(result)

    # ── Recording Page Detection ────────────────────────────

    def answer_choose(self):
        """Answer all visible choice questions using setPCChoose2 (primary)
        with jQuery trigger and native click as fallbacks."""
        state = self.get_page_state()
        if self.is_cdp_parse_error(state):
            raise ConnectionError("page state: %s" % state.get('error'))
        groups = state.get('question_groups', [])
        if not groups:
            return False, False  # (any_answered, likely_done)

        # If page is in review/analysis mode (submitted answers shown), skip answering
        if state.get('inReviewMode'):
            self.debug("Page in review mode (choose_wrong/choose_correct/choose_disable detected)")
            return False, True  # (no_new_answers, likely_done=True → advance to next)

        any_new = False
        all_already_done = True
        for g in groups:
            qid = g['qid']

            if g.get('inReview'):
                self.debug("Q:%s in review mode, skipping" % qid)
                self.stats['choose_skip'] += 1
                continue

            if g.get('anySelected'):
                self.debug("Q:%s already selected" % qid)
                self.stats['choose_skip'] += 1
                continue

            all_already_done = False
            ans = self.answers.get(qid, {})
            if ans.get('type') != 'choose':
                self.debug("Q:%s no answer in cache" % qid)
                continue

            # ── Strategy layer double-check ──
            stid_part, qid_part = (qid.rsplit('_', 1) + [''])[:2]
            strat_ans = self.strategy.lookup('collector.choose', stid_part, qid=qid_part)
            if strat_ans and strat_ans.get('source') == 'local':
                strat_letter = strat_ans['answer'].upper()
                if strat_letter != ans['answer'].upper():
                    print("  ⚠ MISMATCH Q:%s: answers=%s strategy=%s — using strategy" % (
                        qid, ans['answer'], strat_letter))
                    ans = strat_ans

            answer_letter = ans['answer'].upper()
            answer_map = {'A': '1', 'B': '2', 'C': '3', 'D': '4', 'E': '5', 'F': '6', 'G': '7'}
            target_idx = answer_map.get(answer_letter)
            if target_idx is None:
                self.debug("Q:%s invalid answer letter '%s', skipping" % (qid, answer_letter))
                continue
            target_id = qid + '_' + target_idx
            print("  Choose Q:%s -> %s" % (qid, answer_letter))
            safe_tid = self.js_escape(target_id)

            # Click via setPCChoose2 → jQuery → native
            js_click = r'''(function(){
            ''' + self._IFRAME_FINDER + r''';
            if (!iframe) return JSON.stringify({error: "no iframe"});
            var win = iframe.contentWindow;
            var el = win.document.getElementById("%s");
            if (!el) return JSON.stringify({error: "not found"});
            if (typeof win.setPCChoose2 === 'function') {
                try { win.setPCChoose2("%s"); return JSON.stringify({method: "setPCChoose2"}); } catch(e) {}
            }
            if (typeof win.$ !== 'undefined') {
                try { win.$("#%s").trigger("click"); return JSON.stringify({method: "jquery"}); } catch(e) {}
            }
            try { el.click(); } catch(e) {}
            return JSON.stringify({method: "native"});
            })()''' % (safe_tid, safe_tid, safe_tid)
            click_result = self.eval_js(js_click)
            self.debug("Click method: " + str(click_result))

            # Poll for choose_selected class
            selected = False
            for _ in range(12):
                js_check = r'''(function(){
                ''' + self._IFRAME_FINDER + r''';
                if (!iframe) return false;
                var doc = iframe.contentDocument || iframe.contentWindow.document;
                var el = doc.getElementById("%s");
                return el ? el.classList.contains("choose_selected") : false;
                })()''' % (safe_tid)
                if self.eval_js(js_check):
                    selected = True
                    break
                self.interruptible_sleep(0.15)

            if selected:
                js_collect = r'''(function(){
                ''' + self._IFRAME_FINDER + r''';
                if (!iframe) return 0;
                var win = iframe.contentWindow;
                if(typeof win.kttb_getPcChoise === 'function'){
                    try { win.kttb_getPcChoise(); } catch(e){}
                }
                return (window.top.__ets_recorded || []).length;
                })()'''
                total = self.eval_js(js_collect) or 0
                self.debug("kttb recorded: %d" % total)
                self.stats['choose_answered'] += 1
                self.answered_questions.append(qid)
                if self._on_question_answered:
                    try:
                        self._on_question_answered(qid, answer_letter, 'choose')
                    except Exception as e:
                        self.debug("on_question_answered error: " + str(e))
                self._fire_question({'type': 'choose', 'type_label': '选择题',
                                           'qid': qid, 'answer': answer_letter,
                                           'answered': self.stats['choose_answered'],
                                           'total_questions': self.total_questions})
                any_new = True
            else:
                self.debug("Q:%s polling failed" % qid)
                self.stats['errors'] += 1

        likely_done = all_already_done and len(groups) > 0
        return any_new, likely_done

    # ── Fill Answer ───────────────────────────────────────────

    def answer_fill(self):
        """Fill all visible blank inputs with Shadow DOM fallback."""
        state = self.get_page_state()
        if self.is_cdp_parse_error(state):
            raise ConnectionError("page state: %s" % state.get('error'))
        inputs = state.get('inputs', [])
        if not inputs:
            return False, False

        any_new = False
        fill_count = len(inputs)
        for inp in inputs:
            inp_id = inp.get('id', '')
            if not inp_id:
                continue

            ans = self.answers.get(inp_id, {})
            if ans.get('type') != 'fill':
                self.debug("No fill answer for: " + inp_id)
                continue

            # ── Strategy layer double-check ──
            stid_part, qid_part = (inp_id.rsplit('_', 1) + [''])[:2]
            strat_ans = self.strategy.lookup('collector.fill', stid_part, qid=qid_part)
            if strat_ans and strat_ans.get('source') == 'local':
                if strat_ans['answer'].strip().lower() != ans['answer'].strip().lower():
                    print("  ⚠ FILL MISMATCH %s: answers=%s strategy=%s — using strategy" % (
                        inp_id, ans['answer'], strat_ans['answer']))
                    ans = strat_ans

            value = ans['answer']
            alternatives = ans.get('alternatives', [])

            if inp.get('value') and inp['value'].strip():
                if inp['value'].strip().lower() == value.strip().lower():
                    self.debug("Already filled: %s = %s" % (inp_id, value))
                    self.stats['fill_skip'] += 1
                    continue

            print("  Fill %s = %s" % (inp_id, value), end='')
            if alternatives:
                print(" (alts: %s)" % ', '.join(alternatives), end='')
            print()
            safe_val = self.js_escape(value)
            safe_id = self.js_escape(inp_id)

            js_fill = '''(function(){
            %s;
            if (!iframe) return JSON.stringify({error: "no iframe"});
            var doc = iframe.contentDocument || iframe.contentWindow.document;
            var inp = doc.getElementById("%s") || doc.querySelector(".fill_word_input[id='%s']") || doc.querySelector("input[type='text'][id='%s']");
            if (!inp) return JSON.stringify({error: "not found"});
            var target = inp;
            if (inp.shadowRoot) {
                var inner = inp.shadowRoot.querySelector("input, textarea");
                if (inner) target = inner;
            }
            target.focus();
            var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
            setter.call(target, "%s");
            target.dispatchEvent(new Event("input", {bubbles: true}));
            target.dispatchEvent(new Event("change", {bubbles: true}));
            target.blur();
            if (target !== inp && target.value) {
                try {
                    setter.call(inp, target.value);
                    inp.dispatchEvent(new Event("input", {bubbles: true}));
                    inp.dispatchEvent(new Event("change", {bubbles: true}));
                } catch(e) {}
            }
            return JSON.stringify({filled: true, value: target.value, shadow: !!inp.shadowRoot});
            })()''' % (self._IFRAME_FINDER, safe_id, safe_id, safe_id, safe_val)
            r1 = self.parse_eval_json(self.eval_js(js_fill))
            self.debug("Fill result: " + str(r1))

            # Bug fix: only count as answered if fill actually succeeded
            if r1.get('filled'):
                self.stats['fill_answered'] += 1
            else:
                self.stats.setdefault('fill_errors', 0)
                self.stats['fill_errors'] += 1
                print("  Fill FAILED for %s: %s" % (inp_id, r1.get('error', 'unknown')))
                continue
            if self._on_question_answered:
                try:
                    self._on_question_answered(inp_id, value, 'fill')
                except Exception as e:
                    self.debug("on_question_answered error: " + str(e))
            self._fire_question({'type': 'fill', 'type_label': '填空题',
                                       'qid': inp_id, 'answer': value,
                                       'answered': self.stats['fill_answered'],
                                       'total_questions': self.total_questions})
            any_new = True

        if any_new:
            js_collect = '''(function(){
            %s;
            if (!iframe) return 0;
            var win = iframe.contentWindow;
            if(typeof win.kttb_getPcBlank === 'function'){
                try { win.kttb_getPcBlank(); } catch(e){}
            }
            return (window.top.__ets_recorded_fill || []).length;
            })()''' % self._IFRAME_FINDER
            total = self.eval_js(js_collect) or 0
            self.debug("Fill recorded: %d" % total)

        return any_new, fill_count > 0

    # ── Recording Helper ────────────────────────────────────

    def click_next(self):
        """Advance to next question. Try iframe next() first, then .next_icon in iframe,
        then .icon-nextQuestion in main frame."""
        # 1. Try iframe.next() (older ETS versions)
        js_iframe_next = '''(function(){
        %s;
        if (iframe && iframe.contentWindow && typeof iframe.contentWindow.next === 'function') {
            try { iframe.contentWindow.next(); return true; } catch(e) {}
        }
        return false;
        })()''' % self._IFRAME_FINDER
        if self.eval_js(js_iframe_next):
            self.stats['next_click'] += 1
            self.debug("Next: iframe.next()")
            return {'success': True, 'method': 'iframe.next()'}

        # 2. Try .next_icon inside iframe (listen-say choose2 pages)
        js_iframe_next_icon = r'''(function(){
        ''' + self._IFRAME_FINDER + r''';
        if (!iframe) return JSON.stringify({success: false, reason: "no iframe"});
        var iDoc = iframe.contentDocument || iframe.contentWindow.document;
        var ni = iDoc.querySelector(".next_icon");
        if (!ni) return JSON.stringify({success: false, reason: "no next_icon"});
        // Bug fix: do NOT force-show hidden submit button — ETS hides it while
        // audio is playing or a timer is running. Forcing it may submit before
        // the server is ready, causing score anomalies. Instead, report it as
        // not-ready so the caller waits.
        var parent = ni.parentElement;
        if (parent && getComputedStyle(parent).display === "none") {
            // C3: stable reason code for callers (startswith / exact match)
            return JSON.stringify({success: false, reason: "next_icon hidden"});
        }
        ni.click();
        return JSON.stringify({success: true, method: "iframe .next_icon"});
        })()'''
        result = self.parse_eval_json(self.eval_js(js_iframe_next_icon))
        if result.get('error') and 'success' not in result:
            result = {'success': False, 'reason': result.get('error')}
        if result.get('success'):
            self.stats['next_click'] += 1
            self.debug("Next: iframe .next_icon")
            return result

        # 3. Try .icon-nextQuestion in main frame (legacy)
        js = '''(function(){
        var btn = document.querySelector(".icon-nextQuestion");
        if(btn){
            var p = btn.closest("button");
            if(p){
                if(p.disabled || p.classList.contains("disabled")){
                    return JSON.stringify({success: false, reason: "disabled"});
                }
                p.click();
                return JSON.stringify({success: true, method: "button"});
            }
        }
        var btns = document.querySelectorAll("button");
        for(var i=0; i<btns.length; i++){
            if(btns[i].disabled) continue;
            if(btns[i].querySelector(".icon-nextQuestion")){
                btns[i].click();
                return JSON.stringify({success: true, method: "button"});
            }
        }
        return JSON.stringify({success: false, reason: "not found"});
        })()'''
        result = self.parse_eval_json(self.eval_js(js))
        if result.get('error') and 'success' not in result:
            return {'success': False, 'reason': result.get('error')}
        if result.get('success'):
            self.stats['next_click'] += 1
            self.debug("Next: button")
        return result

    def wait_iframe_ready(self, timeout=15):
        """Wait for iframe to contain choices or inputs.

        Adaptive timeout: scales with total_questions (more questions → more
        tolerance for slow page loads), capped at 30s.
        """
        # #7: Adaptive timeout — larger exams tend to have heavier pages
        adaptive_timeout = min(10 + max(self.total_questions, 1) // 3, 30)
        timeout = max(timeout, adaptive_timeout)
        start = time.time()
        while time.time() - start < timeout:
            state = self.get_page_state()
            if self.is_cdp_parse_error(state):
                raise ConnectionError("page state: %s" % state.get('error'))
            if state.get('choices') or state.get('inputs') or state.get('hasChoice') or state.get('hasInput'):
                return True, True
            self.interruptible_sleep(0.3)
        return False, False

    def _all_sidebar_correct(self):
        """Check if all sidebar question items are marked is-correct (exam/homework complete).
        Returns True if sidebar exists and all items are is-correct.
        Note: does NOT require answered >= total_questions because sidebar may include
        recording questions (picture/dialogue/read) that we don't track in choose/fill stats."""
        js = '''(function(){
        var orders = document.querySelectorAll('.question-order');
        if (!orders || orders.length === 0) return JSON.stringify({hasSidebar: false});
        var total = 0, correct = 0;
        for (var i = 0; i < orders.length; i++) {
            total++;
            if (orders[i].classList.contains('is-correct')) correct++;
        }
        return JSON.stringify({hasSidebar: true, total: total, correct: correct, allCorrect: total > 0 && total === correct});
        })()'''
        try:
            result = self.parse_eval_json(self.eval_js(js))
            return bool(result.get('allCorrect'))
        except Exception:
            return False

    # ── Main Loop ─────────────────────────────────────────────

    def _wait_for_next(self, max_wait_loops=30, wait_sec=2, label="next"):
        """Wait for Next button to become available after it was disabled/not found.
        Returns True if Next succeeded, False if exam appears complete."""
        for _ in range(max_wait_loops):
            self.interruptible_sleep(wait_sec)
            if self._recording_window_closed:
                return False
            # Early exit: if all sidebar items are correct, exam is complete
            if self._all_sidebar_correct():
                self.debug("All sidebar items are correct, exam complete")
                return False
            nr2 = self.click_next()
            if nr2.get('success'):
                self.interruptible_sleep(0.6)
                return True
            reason = nr2.get('reason') or ''
            if reason == 'not found' or self._is_next_waiting(reason):
                # Page may still be loading / audio/timer hiding next — keep waiting
                continue
            # Unexpected reason — treat as complete
            return False
        # Exhausted wait — exam likely complete
        return False

    def _run_loop(self, max_steps=999):
        """Inner business-logic loop. Called by run(); separated so that
        run() can put this in a worker thread when a GUI is present."""
        print("-" * 40)

        # Register global hotkeys (Windows only)
        hotkey = None
        try:
            from ets_hotkey import ETSHotkey
            hotkey = ETSHotkey(on_stop=self._signal_stop)
            hotkey.register()
        except Exception as e:
            self.debug("Hotkey init failed (non-Windows?): %s" % e)
            hotkey = None

        try:
            return self._run_loop_body(max_steps, hotkey)
        except InterruptedError:
            # CLI F12 / stop_event during interruptible_sleep — clean exit
            print("\n" + "\u5df2\u505c\u6b62")
            return {
                'set_id': self.set_id,
                'mode': 'HOMEWORK' if self.homework_mode else 'PRACTICE',
                'total_questions': self.total_questions,
                'choose_answered': self.stats.get('choose_answered', 0),
                'fill_answered': self.stats.get('fill_answered', 0),
                'total_answered': (
                    self.stats.get('choose_answered', 0)
                    + self.stats.get('fill_answered', 0)
                ),
                'coverage_pct': 0,
                'errors': self.stats.get('errors', 0),
                'next_clicks': self.stats.get('next_click', 0),
                'stopped': True,
            }
        finally:
            # Always cleanup hotkey + ws even on InterruptedError / CDP crash
            self._drop_connection()
            if hotkey:
                try:
                    hotkey.unregister()
                except Exception:
                    pass


    def _exam_post_reconnect(self, old_set_id):
        """Post-hook after exam CDP reconnect: pinia, bridge, answers.

        Returns True if safe to resume, False to stop.
        """
        self._read_pinia_config()
        self._detect_rw_mode()
        # OPEN-H5: re-wrap kttb bridge immediately after new CDP session
        try:
            self.inject_bridge()
        except Exception as br_err:
            self.debug("inject_bridge after reconnect: %s" % br_err)
        # Clear RW cache so stale showData isn't used after reconnect
        self.rw_show_data = None
        self._rw_cache_time = 0
        # Reload answers if set_id changed. load_answers clears maps first —
        # failure must not resume empty.
        if self.set_id != old_set_id:
            self.debug("set_id changed after reconnect: %s → %s" % (
                old_set_id, self.set_id))
            if not self.load_answers():
                print("Reconnect: failed to reload answers for set_id=%s, stopping."
                      % self.set_id)
                return False
            if self.strategy:
                self.strategy.load_set(self.set_id, data_dir=self.ets_base)
        elif not self.answers and not self.recording_answers:
            print("Reconnect: answer table empty after reconnect, reloading...")
            if not self.load_answers():
                print("Reconnect: still no answers after reload, stopping.")
                return False
        return True

    def _handle_exam_reconnect(self, conn_err, consecutive_conn_errors):
        """Reconnect after eval_js timeout / WS drop mid exam step.

        eval_js invalidates ws on timeout; answer_choose/fill/click_next must
        call this (not only wait_iframe_ready). Returns True to continue the
        main loop, False to stop.
        """
        self.debug("Connection error detail: %s" % conn_err)
        old_set_id = self.set_id

        def _post():
            return self._exam_post_reconnect(old_set_id)

        action = self.reconnect_control(
            consecutive_conn_errors,
            post_ok=_post,
            label='Exam',
            max_errors=3,
            sleep_ok=1.0,
            sleep_fail=1.0,
        )
        return action == 'continue'

    def _run_loop_body(self, max_steps, hotkey):
        """Core exam loop body (cleanup handled by _run_loop finally)."""
        consecutive_empty = 0
        step = 0
        consecutive_conn_errors = 0  # #3: track connection drops for reconnect logic

        # ── Adaptive thresholds ──────────────────────────────────────────
        # Hardcoded thresholds (5/10) cause premature termination on jittery
        # networks.  Instead, scale with exam size and use exponential backoff.
        consecutive_choose_empty = 0   # Empty after choose section
        consecutive_unreachable = 0    # iframe not ready
        # Adaptive max: more questions → more tolerance
        max_empty = min(5 + max(self.total_questions, 1) // 5, 15)
        max_unreachable = min(8 + max(self.total_questions, 1) // 3, 25)
        self.debug("Thresholds: max_empty=%d, max_unreachable=%d" % (max_empty, max_unreachable))

        while True:
            # ── Stop check (GUI button / external signal) ──
            if self.stop_event and self.stop_event.is_set():
                break

            # ── Hotkey checks ──
            if hotkey and hotkey.should_stop:
                print("\n🛑 Emergency stop (F12)")
                self._signal_stop()
                break
            if hotkey and hotkey.should_skip:
                hotkey.clear_skip()
                print("\n⏭ Skipping current question (F10)")
                try:
                    nr = self.click_next()
                except (ConnectionError, TimeoutError) as conn_err:
                    consecutive_conn_errors += 1
                    if self._handle_exam_reconnect(conn_err, consecutive_conn_errors):
                        continue
                    break
                consecutive_conn_errors = 0
                if nr.get('success'):
                    self.interruptible_sleep(1)
                continue
            if hotkey and hotkey.is_paused:
                self.interruptible_sleep(0.5)
                continue

            # Check if recording window was closed (user signal to stop)
            if self._recording_window_closed:
                print("\nRecording window closed - stopping script")
                self._signal_stop()
                break

            step += 1
            if step > max_steps:
                print("\nSafety limit reached (%d steps)" % max_steps)
                self._signal_stop()
                break

            # ── #3: Connection resilience — page state + answer + next ──
            # eval_js timeout invalidates ws; answer_choose/fill/click_next
            # must also reconnect (not only the initial wait_iframe_ready).
            try:
                ready, _ = self.wait_iframe_ready()
                self.inject_bridge()
                self.interruptible_sleep(0.3)

                if not ready:
                    self.debug("Step %d: iframe not ready, waiting..." % step)
                    nr = self.click_next()
                    if nr.get('success'):
                        self.interruptible_sleep(1)
                    # Wait for iframe to stabilize
                    for _wait_i in range(15):
                        self.interruptible_sleep(2)
                        ready2, _ = self.wait_iframe_ready(timeout=5)
                        if ready2:
                            break
                    else:
                        consecutive_unreachable += 1
                        if consecutive_unreachable >= max_unreachable:
                            print("Too many unreachable pages (%d), stopping." % consecutive_unreachable)
                            break
                        # Exponential backoff: 2s, 4s, 8s (cap at 8s)
                        backoff = min(2 * (2 ** (consecutive_unreachable - 1)), 8)
                        self.debug("  Unreachable page %d, backoff %.0fs" % (consecutive_unreachable, backoff))
                        self.interruptible_sleep(backoff)
                        continue
                    state = self.get_page_state()
                    consecutive_unreachable = 0
                    consecutive_empty = 0
                else:
                    consecutive_unreachable = 0
                    consecutive_empty = 0
                    state = self.get_page_state()

                # CDP/parse failure must not look like empty page; semantic "no iframe" stays soft
                if self.is_cdp_parse_error(state):
                    self.debug("Step %d: page state CDP error: %s" % (step, state.get('error')))
                    raise ConnectionError("page state: %s" % state.get('error'))

                # ── Real-time question info for GUI ──
                if self._on_question:
                    try:
                        q_info = {'step': step, 'total_questions': self.total_questions}
                        if state.get('hasChoice'):
                            q_info['type'] = 'choose'
                            q_info['type_label'] = '选择题'
                        elif state.get('hasInput'):
                            q_info['type'] = 'fill'
                            q_info['type_label'] = '填空题'
                        else:
                            q_info['type'] = 'transition'
                            q_info['type_label'] = '过渡页'
                        self._on_question(q_info)
                    except Exception:
                        pass

                if state.get('hasChoice'):
                    any_new, likely_done = self.answer_choose()
                    if likely_done and not any_new:
                        # Review mode or all choices already answered — check completion then advance
                        if self._all_sidebar_correct():
                            print("Exam completed (all %d questions answered and correct)" % self.total_questions)
                            break
                        nr = self.click_next()
                        if nr.get('success'):
                            self.interruptible_sleep(0.6)
                            consecutive_conn_errors = 0
                            continue
                        elif self._is_next_waiting(nr.get('reason')):
                            # Button temporarily disabled / hidden — check sidebar then wait
                            if self._all_sidebar_correct():
                                print("Exam completed (all questions correct)")
                                break
                            self.debug("  Next disabled/hidden after choices, waiting...")
                            if self._wait_for_next(max_wait_loops=30, wait_sec=2, label="choices"):
                                consecutive_conn_errors = 0
                                continue
                            print("Exam completed (next disabled/hidden after choices)")
                            break
                        elif nr.get('reason') == 'not found':
                            # Page may still be loading
                            self.debug("  Next button not found after choices, waiting...")
                            if self._wait_for_next(max_wait_loops=10, wait_sec=2, label="choices-load"):
                                consecutive_conn_errors = 0
                                continue
                            print("Exam completed (next not found after choices)")
                            break
                        else:
                            print("Exam completed")
                            break
                    if not any_new:
                        consecutive_choose_empty += 1
                        consecutive_empty += 1
                        if consecutive_choose_empty >= 3 and self._all_sidebar_correct():
                            print("All choices correct but next not advancing — exam may be complete")
                            break
                        nr = self.click_next()
                        if nr.get('success'):
                            self.interruptible_sleep(0.6)
                            consecutive_conn_errors = 0
                            continue
                        elif self._is_next_waiting(nr.get('reason')):
                            # Audio may still be playing / answer not yet selected — wait
                            self.debug("  Next disabled/hidden (no choice answer), waiting...")
                            if self._wait_for_next(max_wait_loops=30, wait_sec=2, label="choice-no-answer"):
                                consecutive_empty = 0
                                consecutive_choose_empty = 0
                                consecutive_conn_errors = 0
                                continue
                            print("Exam completed (next disabled/hidden, no answer)")
                            break
                        elif nr.get('reason') == 'not found':
                            if self._wait_for_next(max_wait_loops=10, wait_sec=2, label="choice-no-ans-load"):
                                consecutive_empty = 0
                                consecutive_choose_empty = 0
                                consecutive_conn_errors = 0
                                continue
                            consecutive_empty = max_empty
                            consecutive_choose_empty = max_empty
                        else:
                            consecutive_empty = max_empty
                            consecutive_choose_empty = max_empty
                elif state.get('hasInput'):
                    any_new, has_fills = self.answer_fill()
                    if any_new:
                        consecutive_empty = 0
                    if has_fills and not any_new:
                        nr = self.click_next()
                        if nr.get('success'):
                            self.interruptible_sleep(0.6)
                            consecutive_conn_errors = 0
                            continue
                        elif self._is_next_waiting(nr.get('reason')):
                            # Button disabled/hidden during fill section (ETS replays audio)
                            # Could also be a fill+recording hybrid page
                            if self.is_recording_page():
                                self.debug("Recording page detected (fill + stopRecord visible)")
                                if self.wait_for_recording_done():
                                    consecutive_empty = 0
                                    consecutive_conn_errors = 0
                                    continue
                                else:
                                    print("Recording wait ended, stopping script.")
                                    self._signal_stop()
                                    break
                            self.debug("  Next disabled (fill audio replay), waiting for button...")
                            if self._wait_for_next(max_wait_loops=60, wait_sec=4, label="fill-audio"):
                                consecutive_conn_errors = 0
                                continue
                            print("Exam completed (fill section, next disabled too long)")
                            break
                        elif nr.get('reason') == 'not found':
                            if self._wait_for_next(max_wait_loops=10, wait_sec=2, label="fill-load"):
                                consecutive_conn_errors = 0
                                continue
                            print("Exam completed (fill section, next not found)")
                            break
                        consecutive_conn_errors = 0
                        continue
                else:
                    # No choices AND no inputs — could be section transition OR recording page
                    # Check for recording page first (btn-stopRecord visible)
                    if self.is_recording_page():
                        self.debug("Recording page detected (no choice/input, stopRecord visible)")
                        if self.wait_for_recording_done():
                            consecutive_empty = 0
                            consecutive_conn_errors = 0
                            continue  # next button is ready, loop will click it
                        else:
                            print("Recording wait ended, stopping script.")
                            self._signal_stop()
                            break

                    # Not a recording page — treat as section transition
                    consecutive_empty += 1
                    self.debug("Section transition, waiting... (empty %d/%d)" % (consecutive_empty, max_empty))
                    if consecutive_empty >= max_empty:
                        # Before stopping, do one last check — maybe we're on result page
                        url = (self.tab or {}).get('url', '')
                        if 'Result' in url or 'mockExamResult' in url:
                            print("Exam completed (result page reached)")
                            break
                        print("Too many empty pages (%d), stopping." % consecutive_empty)
                        break
                    # Gentle backoff for section transitions: 2s, 2.5s, 3s...
                    backoff = min(2 + consecutive_empty * 0.5, 6)
                    self.interruptible_sleep(backoff)
                    consecutive_conn_errors = 0
                    continue

                self.interruptible_sleep(0.3)
                nr = self.click_next()
                consecutive_conn_errors = 0  # full step succeeded
                if nr.get('success'):
                    self.interruptible_sleep(0.6)
                elif self._is_next_waiting(nr.get('reason')):
                    # Don't immediately break — audio may still be playing / answer pending
                    self.debug("  Next disabled/hidden after answering, waiting...")
                    if self._wait_for_next(max_wait_loops=30, wait_sec=2, label="post-answer"):
                        continue
                    print("Exam completed (next disabled/hidden)")
                    break
                elif nr.get('reason') == 'not found':
                    # Page may still be loading — wait instead of breaking
                    self.debug("  Next button not found, waiting for page load...")
                    if self._wait_for_next(max_wait_loops=10, wait_sec=2, label="post-answer-load"):
                        continue
                    print("Exam completed (next not found)")
                    break
                else:
                    print("Exam completed")
                    break
            except (ConnectionError, TimeoutError) as conn_err:
                consecutive_conn_errors += 1
                if self._handle_exam_reconnect(conn_err, consecutive_conn_errors):
                    continue
                break

        # ── Summary ──
        choose_count = self.stats['choose_answered']
        fill_count = self.stats['fill_answered']
        total_done = choose_count + fill_count
        pct = total_done / self.total_questions * 100 if self.total_questions else 0
        result = {
            'set_id': self.set_id, 'mode': 'HOMEWORK' if self.homework_mode else 'PRACTICE',
            'total_questions': self.total_questions,
            'choose_answered': choose_count, 'fill_answered': fill_count,
            'total_answered': total_done, 'coverage_pct': round(pct, 1),
            'errors': self.stats['errors'], 'next_clicks': self.stats['next_click']
        }
        print("\n" + "=" * 40)
        if total_done > 0:
            print("Done: %d choose + %d fill = %d answered" % (choose_count, fill_count, total_done))
            if self.total_questions:
                print("Coverage: %d/%d (%.0f%%)" % (total_done, self.total_questions, pct))
                if pct >= 100:
                    print("\nAll questions answered. Exam complete!")
        else:
            print("No questions answered this run")
            if 'mockExamResult' in self._tab_url():
                print("Note: You are on a completed exam result page.")
                print("Open a mock exam or homework page to auto-answer.")
        if self.stats['errors']:
            print("Errors: %d" % self.stats['errors'])

        # Recovery hint for early termination
        if 0 < total_done < self.total_questions:
            pct_val = total_done / self.total_questions * 100 if self.total_questions else 0
            if pct_val < 90:
                print("\n💡 Tip: Script stopped early. Possible causes:")
                print("   - Network jitter (try again)")
                print("   - Page loaded slowly (increase wait with F9 pause)")
                print("   - Exam already completed (check result page)")
                print("   Re-run the script to resume from where it left off.")

        # Fire on_complete callback
        self._fire_complete(result)

        # #2: Save statistics report to file for later analysis
        try:
                        # OPEN-M11: do not pollute APPDATA ETS — write beside project/exe
            from ets_common import user_data_path
            stats_path = user_data_path('ets_stats.json', anchor_file=__file__)
            report = {
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'set_id': self.set_id,
                'mode': 'HOMEWORK' if self.homework_mode else 'PRACTICE',
                'rw_mode': self.rw_mode,
                'total_questions': self.total_questions,
                'stats': dict(self.stats),
                'result': result
            }
            with open(stats_path, 'w', encoding='utf-8') as sf:
                json.dump(report, sf, ensure_ascii=False, indent=2)
            self.debug("Stats report saved: %s" % stats_path)
        except Exception as e:
            self.debug("Stats report save failed: %s" % e)

        # Cleanup (ws/hotkey) is in _run_loop finally
        return result

    def run(self, max_steps=999):
        """Run auto-answer loop. Stops when exam is done or recording window is closed.
        max_steps is a safety limit only — you should never need to set it."""
        print("ETS Auto")
        print("=" * 40)

        try:
            self.connect()
        except URLError as e:
            print("\n❌ 连接失败: %s" % e)
            print("诊断：")
            print("  1. e听说PC端是否已启动？")
            print("  2. 调试端口 %d 是否正确？" % self.port)
            print("  3. Chrome --remote-debugging-port=%d 是否开启？" % self.port)
            return
        except ConnectionRefusedError:
            print("\n❌ 连接被拒绝 (端口 %d)" % self.port)
            print("诊断：e听说PC端可能未启动，或端口不匹配")
            return
        except Exception as e:
            print("\n❌ 连接失败: %s" % e)
            print("诊断：请确认 e听说PC端已启动且调试端口 %d 正确" % self.port)
            return

        if 'Result' in self._tab_url():
            print("Already on a result page — open a mock exam to auto-answer")
            return

        # Read-Write mode: answers come from iframe showData, not local cache
        if self.rw_mode:
            print("Mode: 读写同步 (Read-Write)")
            print("=" * 40)
            return self._run_rw_loop(max_steps)

        if not self.load_answers():
            print("Failed to load answers, aborting")
            return

        mode_str = "HOMEWORK" if self.homework_mode else "PRACTICE"
        print("Mode: %s | Questions: %d" % (mode_str, self.total_questions))

        # Fire on_connect callback AFTER load_answers so total_questions is populated
        # Note: exam mode passes extra args (ets_base, set_id, homework_mode, total_questions)
        # The base _fire_connect only passes self; for backward compat, call directly
        if self._on_connect:
            try:
                self._on_connect(self.ets_base, self.set_id, self.homework_mode, self.total_questions)
            except Exception as e:
                self.debug("on_connect callback error: " + str(e))

        # Recording answers window: never nest a second exam thread.
        # - GUI mode (existing Tk root): open Toplevel non-blocking on main thread,
        #   then run _run_loop on THIS thread (already the GUI worker).
        # - CLI mode (no root): open Tk on a side thread, run loop here, join window.
        if self.recording_answers:
            print("Recording answers: %d types available" % len(self.recording_answers))
            import threading

            if self._existing_tk_root() is not None:
                # GUI worker path: non-blocking Toplevel, loop on this thread
                def _poll_destroy_when_stopped():
                    try:
                        if self.stop_event is not None and self.stop_event.is_set():
                            if self._tk_root and self._tk_root.winfo_exists():
                                self._tk_root.destroy()
                            return
                        if self._tk_root and self._tk_root.winfo_exists():
                            self._tk_root.after(500, _poll_destroy_when_stopped)
                    except Exception:
                        pass

                ready = threading.Event()
                self.open_recording_window_async(
                    poll_worker_fn=_poll_destroy_when_stopped,
                    ready_event=ready)
                # Wait for main-thread create (cap 2s) — avoids fixed-sleep race
                ready.wait(timeout=2.0)
                try:
                    return self._run_loop(max_steps)
                finally:
                    try:
                        if self._tk_root is not None and self._tk_root.winfo_exists():
                            self._tk_root.destroy()
                    except Exception:
                        pass
            else:
                # CLI: Tk mainloop must own main thread — keep prior dual-thread shape
                # but only for CLI (no existing root / no outer GUI worker).
                import queue
                result_q = queue.Queue()

                def _worker():
                    try:
                        r = self._run_loop(max_steps)
                        result_q.put(r)
                    except Exception as e:
                        result_q.put(e)

                t = threading.Thread(target=_worker, daemon=True)
                t.start()

                def _poll_worker():
                    if not t.is_alive():
                        try:
                            if self._rec_done_event is not None:
                                self._rec_done_event.set()
                        except Exception:
                            pass
                        try:
                            if self._tk_root and self._tk_root.winfo_exists():
                                self._tk_root.destroy()
                        except Exception:
                            pass
                        return
                    try:
                        if self._tk_root and self._tk_root.winfo_exists():
                            self._tk_root.after(500, _poll_worker)
                    except Exception:
                        pass

                self.show_recording_answers_window(poll_worker_fn=_poll_worker)
                if self._recording_window_closed or (
                        self.stop_event is not None and self.stop_event.is_set()):
                    self._signal_stop()
                t.join(timeout=20)
                if t.is_alive():
                    print("Recording path: worker still running after join — forcing disconnect")
                    try:
                        self._drop_connection()
                    except Exception:
                        pass
                    try:
                        if getattr(self, '_hotkey', None):
                            self._hotkey.unregister()
                    except Exception:
                        pass
                    self._signal_stop()
                    t.join(timeout=5)
                    if not result_q.empty():
                        result = result_q.get_nowait()
                        if isinstance(result, Exception):
                            raise result
                        if isinstance(result, dict):
                            result = dict(result)
                            result['incomplete'] = True
                            result['errors'] = result.get('errors', 0) + 1
                            return result
                    return {'total_answered': 0, 'errors': 1, 'incomplete': True}
                if not result_q.empty():
                    result = result_q.get_nowait()
                    if isinstance(result, Exception):
                        raise result
                    return result
                # Worker exited but put nothing — not a live zombie
                return {'total_answered': 0, 'errors': 1, 'empty_result': True}
        else:
            print("No recording questions in this exam")

        return self._run_loop(max_steps)


if __name__ == "__main__":
    # Force unbuffered output on Windows (subprocess/pipe detection hides prints)
    force_utf8_stdio(line_buffering=True)

    import argparse
    parser = argparse.ArgumentParser(description="ETS Exam Auto — e听说PC端套卷自动答题")
    parser.add_argument("--max", type=int, default=999, help="Safety limit (default: 999)")
    parser.add_argument("--debug", action="store_true", help="Verbose output for troubleshooting")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--show-answers", action="store_true", help="Show all answers without auto-answering")
    parser.add_argument("--log", type=str, default=None, metavar="FILE", help="Save all output to a log file")
    parser.add_argument("--log-keep", type=int, default=7, metavar="DAYS",
                        help="Auto-delete log files older than N days in the log directory (default: 7, 0=disable)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Load answers and simulate without actually answering (safety check)")
    args = parser.parse_args()

    # #8: Auto-clean old log files
    if args.log and args.log_keep > 0:
        try:
            log_dir = os.path.dirname(os.path.abspath(args.log)) or '.'
            cutoff = time.time() - args.log_keep * 86400
            for fname in os.listdir(log_dir):
                fpath = os.path.join(log_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                # Only clean .log files to avoid accidents
                if not fname.endswith('.log'):
                    continue
                if os.path.getmtime(fpath) < cutoff:
                    try:
                        os.remove(fpath)
                        print("[Cleanup] Removed old log: %s" % fname)
                    except Exception:
                        pass
        except Exception:
            pass  # cleanup is non-critical

    # Setup log file (tee stdout AND stderr to file)
    tee = None
    tee_err = None
    if args.log:
        tee = TeeOutput(args.log)  # opens file in 'w' mode
        sys.stdout = tee
        tee_err = TeeOutput(args.log, original_stream=sys.stderr, shared_handle=tee.log)
        sys.stderr = tee_err
    try:
        auto = ETSAutoAnswer(debug_mode=args.debug, stop_event=threading.Event())
        if args.show_answers:
            auto.connect()
            auto.load_answers()
            auto.show_answers()
            if args.json:
                print(json.dumps(auto.get_all_answers(), ensure_ascii=False, indent=2))
            if auto.ws:
                try:
                    auto.ws.close()
                except Exception:
                    pass
        else:
            if args.dry_run:
                # #4: Dry-run mode — load answers, print summary, don't answer
                auto.connect()
                auto.load_answers()
                print("\n[DRY RUN] Answers loaded, no questions will be answered.")
                print("Set ID: %s" % auto.set_id)
                print("Total questions: %d" % auto.total_questions)
                print("Choose answers: %d" % sum(1 for v in auto.answers.values() if v.get('type') == 'choose'))
                print("Fill answers: %d" % sum(1 for v in auto.answers.values() if v.get('type') == 'fill'))
                print("Recording answers: %d" % len(auto.recording_answers))
                print("\nDry run complete. Re-run without --dry-run to actually answer.")
                if auto.ws:
                    try:
                        auto.ws.close()
                    except Exception:
                        pass
            else:
                try:
                    result = auto.run(max_steps=args.max)
                    if args.json and result:
                        print(json.dumps(result, ensure_ascii=False))
                except InterruptedError:
                    print("\n已停止")
                except (ConnectionError, TimeoutError) as e:
                    print("\n连接断开: %s" % e)
    finally:
        # Cleanup: restore stdout/stderr BEFORE closing log file
        # (so any exception during close can still write to stderr)
        if tee_err:
            sys.stderr = tee_err.terminal
        if tee:
            sys.stdout = tee.terminal
            tee.close()
            print("Log saved to: " + args.log)
