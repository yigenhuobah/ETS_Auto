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
import json, os, time, sys, threading
from urllib.parse import urlparse, parse_qs
from urllib.error import URLError

# Version constant — keep in sync with ets_gui.py APP_VERSION
__version__ = "0.6.1"
from ets_common import ETSBase
from ets_strategy import ETSStrategy


class ETSAutoAnswer(ETSBase):
    def __init__(self, port=10086, debug_mode=False, stop_event=None):
        super().__init__(port=port, debug_mode=debug_mode, stop_event=stop_event)
        self.ets_base = None
        self.answers = {}
        self.set_id = None
        self.homework_mode = None
        self.homework_id = None
        self.answered_questions = []
        self._recording_window_closed = False
        self.total_questions = 0
        self.recording_answers = []   # list of dicts for picture/dialogue
        self.strategy = ETSStrategy()  # strategy layer for local answer lookup
        self.rw_mode = False  # read-write (读写同步) mode flag
        self.rw_show_data = None  # cached showData from iframe
        # Callback hooks (set via on_* methods or direct assignment)
        self._on_connect = None           # fn(ets_base, set_id, mode, total_questions)
        self._on_question_answered = None # fn(qid, answer, qtype) where qtype='choose'|'fill'
        self._on_complete = None          # fn(stats_dict)
        self._on_error = None             # fn(error_msg)
        self.stats = {
            'choose_answered': 0, 'choose_skip': 0,
            'fill_answered': 0, 'fill_skip': 0,
            'next_click': 0, 'errors': 0
        }

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
            tag = "[CHS]" if val['type'] == 'choose' else "[FIL]"
            print("  %s %s → %s" % (tag, key, val['answer']))
        print("\n%d total answers" % len(self.answers))

    def on_connect(self, fn):
        """Register callback: fn(ets_base, set_id, mode, total_questions)."""
        self._on_connect = fn

    def on_question_answered(self, fn):
        """Register callback: fn(qid, answer, qtype). Called per question."""
        self._on_question_answered = fn

    def on_complete(self, fn):
        """Register callback: fn(stats_dict). Called when exam ends."""
        self._on_complete = fn

    def on_error(self, fn):
        """Register callback: fn(error_msg). Called on non-fatal errors."""
        self._on_error = fn

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

        # Last-resort fallback for ets_base
        if not self.ets_base:
            self.ets_base = os.path.join(os.path.expandvars(r'%APPDATA%'), 'ETS')
            self.debug("ets_base (default): " + self.ets_base)

        # Detect read-write (读写同步) mode from URL hash
        self._detect_rw_mode()

    def _get_url_set_id(self):
        """Extract set_id from URL fragment query string.
        Supports both snake_case 'set_id' and camelCase 'setId' (ETS uses both)."""
        try:
            parsed = urlparse(self.tab['url'])
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
        try:
            cfg = json.loads(result)
            if cfg.get('error'):
                self.debug("Pinia error: " + cfg['error'])
                return
            if cfg.get('appDataPath'):
                path = cfg['appDataPath'].replace('\\', '/')
                if not path.replace('\\', '/').endswith('/ETS'):
                    path = os.path.join(path, 'ETS')
                self.ets_base = path
                self.debug("Pinia: dataPath=" + self.ets_base)
            self.homework_mode = cfg.get('doHomework')
            self.homework_id = str(cfg.get('homework_id') or '')
            hw_set = str(cfg.get('hw_set_id') or '')
            if hw_set:
                self.set_id = hw_set
                self.debug("Pinia: set_id=" + self.set_id)
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
        try:
            info = json.loads(result) if result else {}
            mode = "HOMEWORK" if info.get('nativeChoose') else "PRACTICE"
            self.debug("Bridge: " + mode)
            return info
        except:
            self.debug("Bridge result: " + str(result))
            return {}

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

    def _detect_rw_mode(self):
        """Detect if current page is read-write (读写同步) mode via URL hash.
        Sets self.rw_mode = True if on #/readingWritingDetails page."""
        try:
            url = self.tab.get('url', '')
            if 'readingWritingDetails' in url or 'readingWriting' in url:
                self.rw_mode = True
                self.debug("Read-Write mode detected (读写同步)")
                return True
        except Exception as e:
            self.debug("RW mode detection error: " + str(e))
        self.rw_mode = False
        return False

    def get_rw_show_data(self):
        """Get showData from read-write iframe. Contains all questions + answers."""
        if self.rw_show_data:
            return self.rw_show_data
        js = '(function(){\n        try {\n        %s;\n            if (!iframe) return JSON.stringify({error: "no read-write iframe"});\n            var data = iframe.contentWindow.showData;\n            if (!data) return JSON.stringify({error: "no showData"});\n            return JSON.stringify(data);\n        } catch(e) { return JSON.stringify({error: e.message}); }\n        })()' % self._RW_IFRAME_FINDER
        result = self.eval_js(js)
        if result:
            try:
                self.rw_show_data = json.loads(result)
                return self.rw_show_data
            except:
                pass
        return None

    def get_rw_page_state(self):
        """Get state from read-write page: li.pointer options grouped by question.
        Returns: {questions: [{qid, options: [{option, text, selected}], any_selected}]}"""
        js = '(function(){\n        try {\n        %s;\n            if (!iframe) return JSON.stringify({error: "no iframe"});\n            var doc = iframe.contentDocument;\n            if (!doc) return JSON.stringify({error: "no contentDocument"});\n            var result = {questions: []};\n            var uls = doc.querySelectorAll("ul[data-id]");\n            uls.forEach(function(ul){\n                var qid = ul.getAttribute("data-id").split("-").slice(1).join("-");\n                var opts = ul.querySelectorAll("li.pointer");\n                var qInfo = {qid: qid, options: [], any_selected: false};\n                opts.forEach(function(li){\n                    var opt = li.getAttribute("data-option") || "";\n                    var txt = (li.innerText || "").trim().substring(0, 50);\n                    var sel = li.classList.contains("on") || li.classList.contains("selected") || li.classList.contains("active");\n                    qInfo.options.push({option: opt, text: txt, selected: sel});\n                    if (sel) qInfo.any_selected = true;\n                });\n                result.questions.push(qInfo);\n            });\n            return JSON.stringify(result);\n        } catch(e) { return JSON.stringify({error: e.message}); }\n        })()' % self._RW_IFRAME_FINDER
        result = self.eval_js(js)
        if result:
            try:
                return json.loads(result)
            except:
                pass
        return {"error": str(result)}

    def answer_rw_choose(self):
        """Answer all visible choice questions in read-write mode.
        Uses showData for answers, clicks li.pointer[data-option].
        Matches questions by index (page order == showData order)."""
        show_data = self.get_rw_show_data()
        if not show_data or show_data.get('error'):
            self.debug("RW: No showData available")
            return False, False

        questions = show_data.get('question', [])
        if not questions:
            self.debug("RW: No questions in showData")
            return False, False

        state = self.get_rw_page_state()
        if state.get('error'):
            self.debug("RW state error: " + str(state.get('error')))
            return False, False

        page_questions = state.get('questions', [])
        if not page_questions:
            return False, False

        # Build flat answer list from showData: each question has info[].answer
        # A question may have multiple sub-questions (info items)
        answer_list = []  # [(qid, answer_letter)]
        for q in questions:
            qid = q.get('id', '')
            info_list = q.get('info', [])
            if info_list:
                for info_item in info_list:
                    ans = info_item.get('answer', '')
                    if ans:
                        answer_list.append((qid, ans.upper()))

        any_new = False
        all_done = True

        # Match by index: page_questions[i] ↔ answer_list[i]
        for i, pq in enumerate(page_questions):
            if pq.get('any_selected'):
                self.debug("RW Q#%d already selected" % (i + 1))
                continue
            all_done = False

            if i >= len(answer_list):
                self.debug("RW Q#%d: no answer in showData (only %d answers)" % (i + 1, len(answer_list)))
                continue

            qid, answer = answer_list[i]

            # Click the option matching the answer letter
            js_click = '(function(){\n            try {\n            %s;\n                if (!iframe) return "no iframe";\n                var doc = iframe.contentDocument;\n                var uls = doc.querySelectorAll("ul[data-id]");\n                var ul = uls[%d];\n                if (!ul) return "no ul at index %d";\n                var li = ul.querySelector("li.pointer[data-option=\'%s\']");\n                if (!li) return "no li for option %s";\n                li.click();\n                return "clicked %s";\n            } catch(e) { return "error: " + e.message; }\n            })()' % (self._RW_IFRAME_FINDER, i, i, answer, answer, answer)
            result = self.eval_js(js_click)
            self.debug("RW click Q#%d: %s" % (i + 1, str(result)))
            if 'clicked' in str(result):
                any_new = True
                self.stats['choose_answered'] += 1
                print("  RW Q#%d (id:%s) → %s" % (i + 1, qid, answer))
                if self._on_question_answered:
                    try:
                        self._on_question_answered(str(qid), answer, 'choose')
                    except:
                        pass

        return any_new, all_done

    def click_rw_next(self):
        """Click '下一步' or '提交' button in read-write mode (outer page, not iframe)."""
        js = '''(function(){
        var btns = document.querySelectorAll("button.el-button");
        for (var i = 0; i < btns.length; i++) {
            var txt = (btns[i].innerText || "").trim();
            if (txt === "下一步" || txt === "提交") {
                if (btns[i].disabled) return JSON.stringify({success: false, reason: "disabled"});
                btns[i].click();
                return JSON.stringify({success: true, method: txt});
            }
        }
        return JSON.stringify({success: false, reason: "not found"});
        })()'''
        result = self.eval_js(js)
        try:
            r = json.loads(result) if result else {}
            if r.get('success'):
                self.stats['next_click'] += 1
                self.debug("RW Next: 下一步 clicked")
            return r
        except:
            return {"success": False, "reason": str(result)}

    # ── Answer Loading ────────────────────────────────────────

    def load_answers(self):
        """Load answers from local ETS cache (content.json per content_* dir)."""
        if not self.set_id:
            print("ERROR: No set_id available (not in Pinia, not in URL)")
            return False
        exam_dir = os.path.join(self.ets_base, self.set_id)
        if not os.path.exists(exam_dir):
            url_set_id = self._get_url_set_id()
            if url_set_id and url_set_id != str(self.set_id):
                alt_dir = os.path.join(self.ets_base, url_set_id)
                if os.path.exists(alt_dir):
                    self.debug("Pinia set_id %s not found, using URL set_id: %s" % (self.set_id, url_set_id))
                    self.set_id = url_set_id
                    exam_dir = alt_dir
        if not os.path.exists(exam_dir):
            print("ERROR: Exam data not found: " + exam_dir)
            return False

        self.debug("Loading from: " + exam_dir)
        for d in sorted(os.listdir(exam_dir)):
            if not d.startswith('content_'):
                continue
            cj = os.path.join(exam_dir, d, 'content.json')
            if not os.path.exists(cj):
                continue
            try:
                with open(cj, 'r', encoding='utf-8') as _f:
                    data = json.load(_f)
                stype = data.get('structure_type', '')
                info = data.get('info', {})
                stid = info.get('stid', '')
                if stype == 'collector.choose':
                    for xt in info.get('xtlist', []):
                        key = stid + '_' + xt['xt_xh']
                        ans = xt.get('answer', '')
                        if ans:
                            self.answers[key] = {'type': 'choose', 'answer': ans}
                elif stype == 'collector.fill':
                    for std in info.get('std', []):
                        key = stid + '_' + std['xth']
                        ans = std.get('value', '')
                        if ans:
                            if '/' in ans:
                                self.debug("Fill '%s' split -> '%s'" % (ans, ans.split('/')[0].strip()))
                                ans = ans.split('/')[0].strip()
                            self.answers[key] = {'type': 'fill', 'answer': ans}
                elif stype == 'collector.read':
                    key = stid
                    ref_text = info.get('value', '')
                    symbol = info.get('symbol', '')
                    if ref_text:
                        self.answers[key] = {'type': 'read', 'answer': ref_text, 'symbol': symbol}
                        self.recording_answers.append({'stid': stid, 'type': 'read', 'answer': ref_text, 'symbol': symbol})
                elif stype == 'collector.picture':
                    key = stid
                    ref_text = info.get('value', '')
                    topic = info.get('topic', '')
                    if not ref_text:
                        ref_text = info.get('keypoint', '')
                    if not ref_text:
                        ref_text = '\n\n'.join([
                            s.get('value', '') for s in info.get('std', []) if s.get('value', '')
                        ])
                    if ref_text:
                        self.answers[key] = {'type': 'picture', 'answer': ref_text, 'topic': topic}
                        self.recording_answers.append({'stid': stid, 'type': 'picture', 'topic': topic, 'answer': ref_text})
                elif stype == 'collector.dialogue':
                    key = stid
                    questions = info.get('question', [])
                    ref_text = info.get('value', '')
                    if not ref_text:
                        parts = []
                        for q in questions:
                            parts.append(q.get('ask', ''))
                            kw = q.get('keywords', '')
                            if kw:
                                parts.append('  Keywords: ' + kw.replace('|', ' / '))
                        ref_text = '\n\n'.join(parts)
                    if ref_text:
                        q_texts = [q.get('ask', '') for q in questions]
                        self.answers[key] = {'type': 'dialogue', 'answer': ref_text, 'questions': q_texts}
                        self.recording_answers.append({'stid': stid, 'type': 'dialogue', 'questions': q_texts, 'answer': ref_text})
                    for std in info.get('std', []):
                        key = stid + '_' + std['xth']
                        ans = std.get('value', '')
                        if ans:
                            if '/' in ans:
                                self.debug("Fill '%s' split -> '%s'" % (ans, ans.split('/')[0].strip()))
                                ans = ans.split('/')[0].strip()
                            self.answers[key] = {'type': 'fill', 'answer': ans}
            except Exception as e:
                self.debug("Error loading %s: %s" % (d, e))

        self.total_questions = len(self.answers)
        print("Loaded %d answers (set_id=%s)" % (self.total_questions, self.set_id))

        # ── Load strategy layer (composite key index + fallback chain) ──
        if self.set_id:
            strat_ok = self.strategy.load_set(self.set_id)
            if strat_ok:
                print("Strategy layer: %d sections, %d indexed answers" % (
                    len(self.strategy.sections), len(self.strategy.answer_index)))
            else:
                self.debug("Strategy layer: no cache data for set_id=%s" % self.set_id)

        return self.total_questions > 0

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
            hasChoice: choices.length > 0, hasInput: inputs.length > 0,
            inReviewMode: hasReviewMode
        });
        })()''' % self._IFRAME_FINDER
        result = self.eval_js(js)
        try:
            return json.loads(result) if result else {}
        except:
            return {"error": str(result)}

    # ── Choose Answer ─────────────────────────────────────────

    def answer_choose(self):
        """Answer all visible choice questions using setPCChoose2 (primary)
        with jQuery trigger and native click as fallbacks."""
        state = self.get_page_state()
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
            target_idx = answer_map.get(answer_letter, '1')
            target_id = qid + '_' + target_idx
            print("  Choose Q:%s -> %s" % (qid, answer_letter))

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
            })()''' % (target_id, target_id, target_id)
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
                })()''' % (target_id)
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

            if inp.get('value') and inp['value'].strip():
                if inp['value'].strip().lower() == value.strip().lower():
                    self.debug("Already filled: %s = %s" % (inp_id, value))
                    self.stats['fill_skip'] += 1
                    continue

            print("  Fill %s = %s" % (inp_id, value))
            safe_val = self.js_escape(value)

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
            })()''' % (self._IFRAME_FINDER, inp_id, inp_id, inp_id, safe_val)
            r1 = json.loads(self.eval_js(js_fill) or "{}")
            self.debug("Fill result: " + str(r1))

            self.stats['fill_answered'] += 1
            if self._on_question_answered:
                try:
                    self._on_question_answered(inp_id, value, 'fill')
                except Exception as e:
                    self.debug("on_question_answered error: " + str(e))
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

    def show_recording_answers_window(self, poll_worker_fn=None):
        """Show ALL recording answers in a single tkinter window at startup.
        Window stays open while script runs; closing it signals the script to stop.
        poll_worker_fn: optional callback to register with root.after for thread monitoring."""
        if not self.recording_answers:
            return False
        import tkinter as tk
        from tkinter import scrolledtext

        root = tk.Tk()
        self._tk_root = root  # Assign immediately so _poll_worker can reference it
        root.title('[Recording] 录音题参考答案')
        root.configure(bg='#1e1e2e')

        width, height = 750, 600
        x = (root.winfo_screenwidth() - width) // 2
        y = (root.winfo_screenheight() - height) // 3
        root.geometry('%dx%d+%d+%d' % (width, height, x, y))
        root.minsize(500, 350)
        root.resizable(True, True)

        # Header
        header = tk.Frame(root, bg='#2d2d3f', height=48)
        header.pack(fill='x')
        header.pack_propagate(False)
        hlbl = tk.Label(header, text='🎙 录音题参考答案（关闭窗口即停止脚本）',
                       bg='#2d2d3f', fg='#cdd6f4', font=('Microsoft YaHei UI', 13, 'bold'))
        hlbl.pack(side='left', padx=(16, 0), pady=10)

        # Content
        main = tk.Frame(root, bg='#1e1e2e')
        main.pack(fill='both', expand=True, padx=16, pady=(12, 8))

        st = scrolledtext.ScrolledText(
            main, wrap='word', bg='#313244', fg='#cdd6f4',
            insertbackground='#f5c2e7', borderwidth=0, relief='flat',
            font=('Microsoft YaHei UI', 11), padx=14, pady=12
        )
        st.pack(fill='both', expand=True)

        import re
        type_labels = {'picture': '听后转述', 'dialogue': '回答问题', 'read': '短文朗读'}
        type_icons = {'picture': '📖', 'dialogue': '💬', 'read': '📚'}

        all_text = []
        for idx_r, rec in enumerate(self.recording_answers):
            rtype = rec['type']
            icon = type_icons.get(rtype, '🎤')
            label = type_labels.get(rtype, '录音题')
            topic = rec.get('topic', '') or rec.get('symbol', '')

            all_text.append('%s %s %s' % (icon, label, ('— ' + topic) if topic else ''))
            all_text.append('=' * 40)

            answer_text = rec['answer']
            answer_text = re.sub(r'<[^>]+>', '', answer_text)

            all_text.append(answer_text)

            # Add questions for dialogue type
            if rtype == 'dialogue' and rec.get('questions'):
                all_text.append('')
                all_text.append('参考问题：')
                for qi, q in enumerate(rec['questions']):
                    all_text.append('  %d. %s' % (qi + 1, q))

            if idx_r < len(self.recording_answers) - 1:
                all_text.append('')
                all_text.append('-' * 60)
                all_text.append('')

        st.insert('1.0', '\n'.join(all_text))
        st.configure(state='disabled')

        # Bottom bar
        bar = tk.Frame(root, bg='#2d2d3f', height=64)
        bar.pack(fill='x')
        bar.pack_propagate(False)

        hint = tk.Label(bar, text='💡 录音时参考此答案，关闭窗口即停止脚本',
                       bg='#2d2d3f', fg='#a6adc8', font=('Microsoft YaHei UI', 10))
        hint.pack(side='left', padx=(16, 0), pady=14)

        closed = [False]
        def on_close():
            closed[0] = True
            # Signal stop_event so worker thread exits cleanly
            if self.stop_event:
                self.stop_event.set()
            root.destroy()

        btn = tk.Button(bar, text='✅ 关闭并停止脚本', command=on_close,
                       bg='#f38ba8', fg='#1e1e2e',
                       activebackground='#eba0ac', activeforeground='#1e1e2e',
                       font=('Microsoft YaHei UI', 12, 'bold'), relief='flat', padx=24, pady=8,
                       cursor='hand2')
        btn.pack(side='right', padx=(0, 16), pady=12)
        root.protocol('WM_DELETE_WINDOW', on_close)
        root.bind('<Escape>', lambda e: on_close())

        print('[REC] Recording answers window opened (%d types)' % len(self.recording_answers))

        # Register poll_worker AFTER root is created (fixes _tk_root AttributeError)
        if poll_worker_fn:
            self._tk_root.after(500, poll_worker_fn)

        root.focus_force()
        root.mainloop()
        self._recording_window_closed = True
        return True

    # ── Navigation ────────────────────────────────────────────

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
        // If parent container is hidden, force-show it (ETS hides submit until audio/timeout,
        // but we've already selected an answer, so it's safe to submit)
        var parent = ni.parentElement;
        if (parent && getComputedStyle(parent).display === "none") {
            parent.classList.remove("none");
        }
        ni.click();
        return JSON.stringify({success: true, method: "iframe .next_icon"});
        })()'''
        result = json.loads(self.eval_js(js_iframe_next_icon) or "{}")
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
        result = json.loads(self.eval_js(js) or "{}")
        if result.get('success'):
            self.stats['next_click'] += 1
            self.debug("Next: button")
        return result

    def wait_iframe_ready(self, timeout=10):
        start = time.time()
        while time.time() - start < timeout:
            state = self.get_page_state()
            if state.get('choices') or state.get('inputs'):
                return True, True
            self.interruptible_sleep(0.3)
        return False, False

    def _all_sidebar_correct(self):
        """Check if all sidebar question items are marked is-correct (exam/homework complete).
        Returns True only if sidebar exists, all items are is-correct,
        AND we have answered at least as many questions as total_questions."""
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
            result = json.loads(self.eval_js(js) or '{}')
            # Only declare complete if sidebar all correct AND we've answered enough
            if result.get('allCorrect'):
                answered = self.stats['choose_answered'] + self.stats['fill_answered']
                if answered >= self.total_questions:
                    return True
            return False
        except:
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
            if nr2.get('reason') in ('not found', 'next_icon hidden'):
                # Page may still be loading / answer not yet selected — keep waiting
                continue
            if nr2.get('reason') != 'disabled':
                # Unexpected reason — treat as complete
                return False
        # Exhausted wait — exam likely complete
        return False

    def _run_rw_loop(self, max_steps=999):
        """Read-Write (读写同步) mode loop. Different DOM, different navigation."""

        # Get showData for answers
        show_data = self.get_rw_show_data()
        if not show_data or show_data.get('error'):
            print("ERROR: Cannot read showData from iframe: %s" % (show_data or {}).get('error', 'unknown'))
            print("Make sure the read-write page is fully loaded.")
            return {'total_answered': 0, 'mode': 'read-write', 'errors': 1}

        questions = show_data.get('question', [])
        print("Questions in showData: %d" % len(questions))

        # Build answer summary from showData
        for q in questions:
            qid = q.get('id', '')
            info_list = q.get('info', [])
            if info_list:
                ans = info_list[0].get('answer', '')
                if ans:
                    self.answers['rw_' + str(qid)] = {'type': 'choose', 'answer': ans.upper()}
                    print("  Q:%s → %s" % (qid, ans.upper()))

        self.total_questions = len(self.answers)
        print("Total answers: %d" % self.total_questions)

        step = 0
        consecutive_empty = 0
        consecutive_no_progress = 0  # pages where we couldn't answer anything

        while True:
            if self.stop_event and self.stop_event.is_set():
                break

            step += 1
            if step > max_steps:
                print("Safety limit reached (%d steps)" % max_steps)
                break

            # Read current page state
            state = self.get_rw_page_state()
            if state.get('error'):
                self.debug("RW step %d: state error: %s" % (step, state.get('error')))
                consecutive_empty += 1
                if consecutive_empty >= 10:
                    print("Too many errors, stopping.")
                    break
                self.interruptible_sleep(1)
                continue

            page_qs = state.get('questions', [])
            if not page_qs:
                consecutive_empty += 1
                if consecutive_empty >= 5:
                    self.debug("RW: No questions visible for %d steps" % consecutive_empty)
                    nr = self.click_rw_next()
                    if nr.get('success'):
                        consecutive_empty = 0
                        self.rw_show_data = None  # clear cache for next page group
                        self.interruptible_sleep(1)
                        continue
                    print("RW: No more questions visible.")
                    break
                self.interruptible_sleep(1)
                continue

            consecutive_empty = 0
            any_new, all_done = self.answer_rw_choose()

            if any_new:
                consecutive_no_progress = 0
                self.interruptible_sleep(0.5)

            # Only try to advance when all visible questions are answered
            if not all_done:
                # Some questions not yet answerable — wait and retry
                consecutive_no_progress += 1
                if consecutive_no_progress >= 20:
                    print("RW: No progress after %d attempts, stopping." % consecutive_no_progress)
                    break
                self.interruptible_sleep(1)
                continue

            # All questions on this page are done — advance
            consecutive_no_progress = 0
            nr = self.click_rw_next()
            if nr.get('success'):
                method = nr.get('method', '')
                if method == '提交':
                    print("RW: 提交 clicked, task complete.")
                    break
                self.rw_show_data = None  # clear cache for next page group
                self.interruptible_sleep(1)
            elif nr.get('reason') == 'disabled':
                # Still processing current step
                self.debug("RW: Next disabled, waiting...")
                for _ in range(15):
                    self.interruptible_sleep(1)
                    if self._all_sidebar_correct():
                        print("RW: All sidebar items correct, exam complete.")
                        break
                    nr2 = self.click_rw_next()
                    if nr2.get('success'):
                        method = nr2.get('method', '')
                        if method == '提交':
                            print("RW: 提交 clicked, task complete.")
                            break
                        self.rw_show_data = None
                        break
                else:
                    # Final sidebar check before giving up
                    if self._all_sidebar_correct():
                        print("RW: All sidebar items correct, exam complete.")
                    else:
                        print("RW: Next button disabled too long, may be complete.")
                    break
                if nr2.get('method') == '提交':
                    break
            elif nr.get('reason') == 'not found':
                # Maybe on last step or page changed
                self.debug("RW: Next button not found")
                # Check sidebar first
                if self._all_sidebar_correct():
                    print("RW: All sidebar items correct, exam complete.")
                    break
                self.interruptible_sleep(2)
                # Check if still on rw page
                try:
                    url = self.eval_js('document.location.hash')
                    if not url or 'readingWriting' not in (url or ''):
                        print("RW: Page changed, task may be complete.")
                        break
                except:
                    pass

        # Summary
        choose_count = self.stats['choose_answered']
        result = {
            'mode': 'read-write',
            'total_questions': self.total_questions,
            'choose_answered': choose_count,
            'total_answered': choose_count,
            'errors': self.stats['errors']
        }
        print("\n" + "=" * 40)
        print("Done: %d questions answered" % choose_count)
        if self.total_questions:
            print("Coverage: %d/%d (%.0f%%)" % (choose_count, self.total_questions, choose_count / self.total_questions * 100))

        if self.ws:
            try:
                self.ws.close()
            except:
                pass

        return result

    def _run_loop(self, max_steps=999):
        """Inner business-logic loop. Called by run(); separated so that
        run() can put this in a worker thread when a GUI is present."""
        print("-" * 40)

        # ── Register global hotkeys (Windows only) ──
        hotkey = None
        try:
            from ets_hotkey import ETSHotkey
            hotkey = ETSHotkey()
            hotkey.register()
        except Exception as e:
            self.debug("Hotkey init failed (non-Windows?): %s" % e)
            hotkey = None

        consecutive_empty = 0
        step = 0

        while True:
            # ── Hotkey checks ──
            if hotkey and hotkey.should_stop:
                print("\n🛑 Emergency stop (F12)")
                self.stop_event.set()
                break
            if hotkey and hotkey.should_skip:
                hotkey.clear_skip()
                print("\n⏭ Skipping current question (F10)")
                nr = self.click_next()
                if nr.get('success'):
                    self.interruptible_sleep(1)
                continue
            if hotkey and hotkey.is_paused:
                self.interruptible_sleep(0.5)
                continue

            # Check if recording window was closed (user signal to stop)
            if self._recording_window_closed:
                print("\nRecording window closed - stopping script")
                break

            step += 1
            if step > max_steps:
                print("\nSafety limit reached (%d steps)" % max_steps)
                break

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
                    consecutive_empty += 1
                    if consecutive_empty >= 10:
                        print("Too many unreachable pages, stopping.")
                        break
                    continue
                state = self.get_page_state()
                consecutive_empty = 0
            else:
                consecutive_empty = 0
                state = self.get_page_state()

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
                        continue
                    elif nr.get('reason') in ('disabled', 'next_icon hidden'):
                        # Button temporarily disabled / hidden — check sidebar then wait
                        if self._all_sidebar_correct():
                            print("Exam completed (all questions correct)")
                            break
                        self.debug("  Next disabled/hidden after choices, waiting...")
                        if self._wait_for_next(max_wait_loops=30, wait_sec=2, label="choices"):
                            continue
                        print("Exam completed (next disabled/hidden after choices)")
                        break
                    elif nr.get('reason') == 'not found':
                        # Page may still be loading
                        self.debug("  Next button not found after choices, waiting...")
                        if self._wait_for_next(max_wait_loops=10, wait_sec=2, label="choices-load"):
                            continue
                        print("Exam completed (next not found after choices)")
                        break
                    else:
                        print("Exam completed")
                        break
                if not any_new:
                    consecutive_empty += 1
                    nr = self.click_next()
                    if nr.get('success'):
                        self.interruptible_sleep(0.6)
                        continue
                    elif nr.get('reason') in ('disabled', 'next_icon hidden'):
                        # Audio may still be playing / answer not yet selected — wait
                        self.debug("  Next disabled/hidden (no choice answer), waiting...")
                        if self._wait_for_next(max_wait_loops=30, wait_sec=2, label="choice-no-answer"):
                            consecutive_empty = 0
                            continue
                        print("Exam completed (next disabled/hidden, no answer)")
                        break
                    elif nr.get('reason') == 'not found':
                        if self._wait_for_next(max_wait_loops=10, wait_sec=2, label="choice-no-ans-load"):
                            consecutive_empty = 0
                            continue
                        consecutive_empty = 10
                    else:
                        consecutive_empty = 10
            elif state.get('hasInput'):
                any_new, has_fills = self.answer_fill()
                if has_fills and not any_new:
                    nr = self.click_next()
                    if nr.get('success'):
                        self.interruptible_sleep(0.6)
                        continue
                    elif nr.get('reason') == 'disabled':
                        # Button disabled during fill section (ETS replays audio)
                        self.debug("  Next disabled (fill audio replay), waiting for button...")
                        if self._wait_for_next(max_wait_loops=60, wait_sec=4, label="fill-audio"):
                            continue
                        print("Exam completed (fill section, next disabled too long)")
                        break
                    elif nr.get('reason') == 'not found':
                        if self._wait_for_next(max_wait_loops=10, wait_sec=2, label="fill-load"):
                            continue
                        print("Exam completed (fill section, next not found)")
                        break
                    continue
            else:
                # No choices AND no inputs — section transition
                consecutive_empty += 1
                self.debug("Section transition, waiting... (empty %d)" % consecutive_empty)
                if consecutive_empty >= 5:
                    print("Too many empty pages, stopping.")
                    break
                self.interruptible_sleep(2)
                continue

            self.interruptible_sleep(0.3)
            nr = self.click_next()
            if nr.get('success'):
                self.interruptible_sleep(0.6)
            elif nr.get('reason') in ('disabled', 'next_icon hidden'):
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
            if 'mockExamResult' in self.tab.get('url', ''):
                print("Note: You are on a completed exam result page.")
                print("Open a mock exam or homework page to auto-answer.")
        if self.stats['errors']:
            print("Errors: %d" % self.stats['errors'])

        # Fire on_complete callback
        if self._on_complete:
            try:
                self._on_complete(result)
            except Exception as e:
                self.debug("on_complete callback error: " + str(e))

        # Cleanup WebSocket
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

        # Unregister hotkeys
        if hotkey:
            try:
                hotkey.unregister()
            except Exception:
                pass

        return result

    def run(self, max_steps=999):
        """Run auto-answer loop. Stops when exam is done or recording window is closed.
        max_steps is a safety limit only — you should never need to set it."""
        print("ETS Auto")
        print("=" * 40)

        try:
            self.connect()
        except urllib.error.URLError as e:
            print("\n连接失败: %s" % e)
            print("请检查: 1) e听说PC端已启动  2) 调试端口 %d 正确" % self.port)
            return
        except Exception as e:
            print("\n连接失败: %s" % e)
            return

        if 'Result' in self.tab.get('url', ''):
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
        if self._on_connect:
            try:
                self._on_connect(self.ets_base, self.set_id, self.homework_mode, self.total_questions)
            except Exception as e:
                self.debug("on_connect callback error: " + str(e))

        # Show recording answers window upfront (if any)
        if self.recording_answers:
            print("Recording answers: %d types available" % len(self.recording_answers))
            # GUI must run on main thread; run business logic in worker thread
            import threading, queue
            result_q = queue.Queue()
            def _worker():
                try:
                    r = self._run_loop(max_steps)
                    result_q.put(r)
                except Exception as e:
                    result_q.put(e)
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            # Monitor worker thread from main thread: auto-destroy window when done
            def _poll_worker():
                if not t.is_alive():
                    # Worker finished — destroy window to exit mainloop
                    if self._tk_root and self._tk_root.winfo_exists():
                        self._tk_root.destroy()
                    return
                self._tk_root.after(500, _poll_worker)
            # _poll_worker is registered inside show_recording_answers_window
            # after self._tk_root is created, avoiding AttributeError
            self.show_recording_answers_window(poll_worker_fn=_poll_worker)  # blocks on mainloop
            t.join(timeout=5)
            if not result_q.empty():
                result = result_q.get_nowait()
                if isinstance(result, Exception):
                    raise result
                return result
            return {'total_answered': 0}
        else:
            print("No recording questions in this exam")

        return self._run_loop(max_steps)


class TeeOutput:
    """Tee output to both terminal and log file."""
    _shared_lock = threading.Lock()  # protect concurrent writes to same file

    def __init__(self, file_path, original_stream=None, mode='w', shared_handle=None):
        self.terminal = original_stream or sys.stdout
        if shared_handle is not None:
            self.log = shared_handle
            self._owns_handle = False
        else:
            self.log = open(file_path, mode, encoding='utf-8')
            self._owns_handle = True
    def write(self, message):
        with self._shared_lock:
            if self.terminal is not None:
                self.terminal.write(message)
            self.log.write(message)
    def flush(self):
        with self._shared_lock:
            if self.terminal is not None:
                self.terminal.flush()
            self.log.flush()
    def close(self):
        if self._owns_handle:
            self.log.close()
    # Standard text IO attributes (Bug 13: PyInstaller/pip may read these)
    @property
    def encoding(self):
        return self.terminal.encoding if self.terminal and hasattr(self.terminal, 'encoding') else 'utf-8'
    @property
    def errors(self):
        return self.terminal.errors if self.terminal and hasattr(self.terminal, 'errors') else 'replace'
    @property
    def mode(self):
        return 'w'
    @property
    def name(self):
        return self.log.name if hasattr(self.log, 'name') else None
    def fileno(self):
        return self.log.fileno()
    def isatty(self):
        return self.terminal.isatty() if self.terminal and hasattr(self.terminal, 'isatty') else False


if __name__ == "__main__":
    # Force UTF-8 on Windows terminals (GBK can't encode IPA/special chars)
    if sys.platform == 'win32':
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, LookupError):
            pass

    import argparse
    parser = argparse.ArgumentParser(description="ETS Exam Auto — e听说PC端套卷自动答题")
    parser.add_argument("--max", type=int, default=999, help="Safety limit (default: 999)")
    parser.add_argument("--debug", action="store_true", help="Verbose output for troubleshooting")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--show-answers", action="store_true", help="Show all answers without auto-answering")
    parser.add_argument("--log", type=str, default=None, metavar="FILE", help="Save all output to a log file")
    args = parser.parse_args()

    # Setup log file (tee stdout AND stderr to file)
    tee = None
    tee_err = None
    if args.log:
        tee = TeeOutput(args.log)  # opens file in 'w' mode
        sys.stdout = tee
        tee_err = TeeOutput(args.log, original_stream=sys.stderr, shared_handle=tee.log)
        sys.stderr = tee_err
    try:
        auto = ETSAutoAnswer(debug_mode=args.debug)
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
            result = auto.run(max_steps=args.max)
            if args.json and result:
                print(json.dumps(result, ensure_ascii=False))
    finally:
        # Cleanup: restore stdout/stderr BEFORE closing log file
        # (so any exception during close can still write to stderr)
        if tee_err:
            sys.stderr = tee_err.terminal
        if tee:
            sys.stdout = tee.terminal
            tee.close()
            print("Log saved to: " + args.log)
