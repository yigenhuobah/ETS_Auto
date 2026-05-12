#!/usr/bin/env python3
"""
ETS Auto Answer v8 - Enhanced with Pinia introspection + setPCChoose2 + iframe next

Improvements over v7:
  1. Pinia appConfig.appDataPath  - dynamic base path (no hardcoded path)
  2. Pinia homeworkStore           - reliable mode detection (doHomework flag)
  3. setPCChoose2()                - ETS-native choice selection (cleaner than raw jQuery)
  4. iframe next()                 - iframe-native navigation (fallback to DOM button)
  5. Shadow DOM fallback           - fill input robustness (some ETS versions use shadow DOM)

Usage:
  python ets_v8.py              # Run auto-answer (default 80 steps)
  python ets_v8.py --max 50     # Limit steps
  python ets_v8.py --debug      # Verbose output for troubleshooting
"""
import json, urllib.request, websocket, os, time, sys
from urllib.parse import urlparse, parse_qs


class ETSAutoAnswer:
    def __init__(self, port=10086, debug_mode=False):
        self.port = port
        self.ws = None
        self.mid = 0
        self.debug_mode = debug_mode
        self.ets_base = None
        self.answers = {}
        self.set_id = None
        self.homework_mode = None
        self.homework_id = None
        self.answered_questions = []
        self.total_questions = 0
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

    def debug(self, msg):
        if self.debug_mode:
            print("  [D] " + msg)

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
        url = "http://localhost:%d/json" % self.port
        tabs = json.loads(urllib.request.urlopen(url, timeout=5).read())
        ets_tabs = [t for t in tabs if "ets100.com" in t.get("url", "")]
        if not ets_tabs:
            raise Exception("No ETS tab found")
        self.tab = ets_tabs[0]
        self.ws = websocket.create_connection(self.tab["webSocketDebuggerUrl"], timeout=15)
        print("ETS connected")
        self.debug("URL: " + self.tab['url'][:120])

        self._read_pinia_config()

        if not self.set_id:
            self.set_id = self._get_url_set_id()
            if self.set_id:
                self.debug("set_id from URL: " + self.set_id)

        if not self.ets_base:
            self.ets_base = os.path.expandvars(r'%APPDATA%').replace('\\', '/') + '/ETS'
            self.debug("ets_base (default): " + self.ets_base)

    def _get_url_set_id(self):
        try:
            parsed = urlparse(self.tab['url'])
            fragment = parsed.fragment
            if '?' in fragment:
                qs = parse_qs(fragment.split('?', 1)[1])
                return qs.get('set_id', [None])[0]
        except:
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
                if not path.endswith('/ETS'):
                    path += '/ETS'
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

    # ── CDP Helpers ───────────────────────────────────────────

    def eval_js(self, expr):
        self.mid += 1
        self.ws.send(json.dumps({
            "id": self.mid, "method": "Runtime.evaluate",
            "params": {"expression": expr, "returnByValue": True}
        }))
        resp = json.loads(self.ws.recv())
        if "error" in resp:
            self.debug("[WS ERROR] " + str(resp["error"]))
            return None
        result = resp.get("result", {}).get("result", {})
        return result.get("value")

    # ── Bridge Injection ──────────────────────────────────────

    def inject_bridge(self):
        """Wrap kttb_ReturnChoose / kttb_returnPcBlank.
        In homework mode: call native CEF function first, then record.
        In practice mode: record only (no native function exists)."""
        js = '''(function(){
        var win = document.querySelector("iframe").contentWindow;
        window.top.__ets_recorded = window.top.__ets_recorded || [];
        window.top.__ets_recorded_fill = window.top.__ets_recorded_fill || [];
        var hadNativeChoose = typeof win.kttb_ReturnChoose === 'function';
        var hadNativeFill = typeof win.kttb_returnPcBlank === 'function';
        var _origChoose = win.kttb_ReturnChoose;
        var _origBlank = win.kttb_returnPcBlank;
        win.kttb_ReturnChoose = function(data) {
            if (_origChoose && typeof _origChoose === 'function') {
                try { _origChoose(data); } catch(e) {}
            }
            window.top.__ets_recorded.push(data);
        };
        win.kttb_returnPcBlank = function(data) {
            if (_origBlank && typeof _origBlank === 'function') {
                try { _origBlank(data); } catch(e) {}
            }
            window.top.__ets_recorded_fill.push(data);
        };
        return JSON.stringify({
            nativeChoose: hadNativeChoose,
            nativeFill: hadNativeFill
        });
        })()'''
        result = self.eval_js(js)
        try:
            info = json.loads(result) if result else {}
            mode = "HOMEWORK" if info.get('nativeChoose') else "PRACTICE"
            self.debug("Bridge: " + mode)
            return info
        except:
            self.debug("Bridge result: " + str(result))
            return {}

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
                data = json.load(open(cj, 'r', encoding='utf-8'))
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
            except Exception as e:
                self.debug("Error loading %s: %s" % (d, e))

        self.total_questions = len(self.answers)
        print("Loaded %d answers (set_id=%s)" % (self.total_questions, self.set_id))
        return self.total_questions > 0

    # ── Page State ────────────────────────────────────────────

    def get_page_state(self):
        """Get current iframe state: choices grouped by question, fill inputs."""
        js = r'''(function(){
        var doc = document.querySelector("iframe").contentDocument || document.querySelector("iframe").contentWindow.document;
        if (!doc) return JSON.stringify({error: "no doc"});
        var choices = doc.querySelectorAll(".choose2");
        var groups = {};
        var choiceInfo = [];
        choices.forEach(function(c){
            var id = c.id || "";
            var parts = id.split("_");
            var qid = parts.slice(0, -1).join("_");
            if (!groups[qid]) groups[qid] = {qid: qid, choices: [], anySelected: false};
            var sel = c.classList.contains("choose_selected") || c.classList.contains("on");
            groups[qid].choices.push({id: id, selected: sel, text: c.textContent.trim().substring(0, 40)});
            if (sel) groups[qid].anySelected = true;
            choiceInfo.push({id: id, selected: sel, text: c.textContent.trim().substring(0, 40)});
        });
        var groupList = [];
        for (var k in groups) groupList.push(groups[k]);
        var inputs = doc.querySelectorAll(".fill_word_input, input.fill_word_input, input[type='text']");
        var inputInfo = [];
        inputs.forEach(function(inp){
            inputInfo.push({id: inp.id || "", value: inp.value || ""});
        });
        return JSON.stringify({
            choices: choiceInfo, question_groups: groupList, inputs: inputInfo,
            hasChoice: choices.length > 0, hasInput: inputs.length > 0
        });
        })()'''
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

        any_new = False
        all_already_done = True
        for g in groups:
            qid = g['qid']

            if g.get('anySelected'):
                self.debug("Q:%s already selected" % qid)
                self.stats['choose_skip'] += 1
                continue

            all_already_done = False
            ans = self.answers.get(qid, {})
            if ans.get('type') != 'choose':
                self.debug("Q:%s no answer in cache" % qid)
                continue

            answer_letter = ans['answer'].upper()
            answer_map = {'A': '1', 'B': '2', 'C': '3', 'D': '4', 'E': '5', 'F': '6', 'G': '7'}
            target_idx = answer_map.get(answer_letter, '1')
            target_id = qid + '_' + target_idx
            print("  Choose Q:%s -> %s" % (qid, answer_letter))

            # Click via setPCChoose2 → jQuery → native
            js_click = r'''(function(){
            var win = document.querySelector("iframe").contentWindow;
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
                var doc = document.querySelector("iframe").contentDocument || document.querySelector("iframe").contentWindow.document;
                var el = doc.getElementById("%s");
                return el ? el.classList.contains("choose_selected") : false;
                })()''' % (target_id)
                if self.eval_js(js_check):
                    selected = True
                    break
                time.sleep(0.15)

            if selected:
                js_collect = r'''(function(){
                var win = document.querySelector("iframe").contentWindow;
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

            value = ans['answer']

            if inp.get('value') and inp['value'].strip():
                if inp['value'].strip() == value:
                    self.debug("Already filled: %s = %s" % (inp_id, value))
                    self.stats['fill_skip'] += 1
                    continue

            print("  Fill %s = %s" % (inp_id, value))
            safe_val = value.replace("\\", "\\\\").replace("'", "\\'")

            js_fill = '''(function(){
            var doc = document.querySelector("iframe").contentDocument || document.querySelector("iframe").contentWindow.document;
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
            })()''' % (inp_id, inp_id, inp_id, safe_val)
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
            var win = document.querySelector("iframe").contentWindow;
            if(typeof win.kttb_getPcBlank === 'function'){
                try { win.kttb_getPcBlank(); } catch(e){}
            }
            return (window.top.__ets_recorded_fill || []).length;
            })()'''
            total = self.eval_js(js_collect) or 0
            self.debug("Fill recorded: %d" % total)

        return any_new, fill_count > 0

    # ── Navigation ────────────────────────────────────────────

    def click_next(self):
        """Advance to next question. Try iframe next() first, then DOM button."""
        js_iframe_next = '''(function(){
        var iframe = document.querySelector("iframe");
        if (iframe && iframe.contentWindow && typeof iframe.contentWindow.next === 'function') {
            try { iframe.contentWindow.next(); return true; } catch(e) {}
        }
        return false;
        })()'''
        if self.eval_js(js_iframe_next):
            self.stats['next_click'] += 1
            self.debug("Next: iframe.next()")
            return {'success': True, 'method': 'iframe.next()'}

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
        empty_count = 0
        while time.time() - start < timeout:
            state = self.get_page_state()
            if state.get('choices') or state.get('inputs'):
                return True, True
            empty_count += 1
            if empty_count >= 4:
                return False, False
            time.sleep(0.3)
        return False, False

    # ── Main Loop ─────────────────────────────────────────────

    def run(self, max_steps=999):
        """Run auto-answer loop. Stops naturally when exam is done (next button disabled).
        max_steps is a safety limit only — you should never need to set it."""
        print("\nETS Auto Answer v8")
        print("=" * 40)

        self.connect()

        # Fire on_connect callback (used by GUI to get exam info)
        if self._on_connect:
            try:
                self._on_connect(self.ets_base, self.set_id, self.homework_mode, self.total_questions)
            except Exception as e:
                self.debug("on_connect callback error: " + str(e))

        if 'Result' in self.tab.get('url', ''):
            print("Already on a result page — open a mock exam to auto-answer")
            return

        if not self.load_answers():
            print("Failed to load answers, aborting")
            return

        mode_str = "HOMEWORK" if self.homework_mode else "PRACTICE"
        print("Mode: %s | Questions: %d" % (mode_str, self.total_questions))
        print("-" * 40)

        consecutive_empty = 0
        step = 0

        while True:
            step += 1
            if step > max_steps:
                print("\nSafety limit reached (%d steps)" % max_steps)
                break

            ready, _ = self.wait_iframe_ready()
            self.inject_bridge()
            time.sleep(0.3)

            if not ready:
                consecutive_empty += 1
                self.debug("Step %d: no questions visible" % step)
                nr = self.click_next()
                if nr.get('success'):
                    time.sleep(1.5)
                    continue
                elif nr.get('reason') == 'disabled':
                    print("Exam completed (next disabled)")
                    break
                elif consecutive_empty >= 5:
                    print("Too many empty pages — exam appears done")
                    break
                else:
                    time.sleep(1)
                    nr2 = self.click_next()
                    if nr2.get('success'):
                        time.sleep(1.5)
                        continue
                    else:
                        print("Exam completed (no navigation available)")
                        break

            consecutive_empty = 0
            state = self.get_page_state()

            if state.get('hasChoice'):
                any_new, likely_done = self.answer_choose()
                if likely_done and not any_new:
                    nr = self.click_next()
                    if nr.get('success'):
                        time.sleep(0.6)
                        continue
                    elif nr.get('reason') == 'disabled':
                        print("Exam completed (next disabled)")
                        break
                    else:
                        print("Exam completed")
                        break
                if not any_new:
                    consecutive_empty += 1
                    nr = self.click_next()
                    if nr.get('success'):
                        time.sleep(0.6)
                        continue
                    else:
                        consecutive_empty = 10
            elif state.get('hasInput'):
                any_new, has_fills = self.answer_fill()
                if has_fills and not any_new:
                    nr = self.click_next()
                    if nr.get('success'):
                        time.sleep(0.6)
                        continue
                    elif nr.get('reason') == 'disabled':
                        print("Exam completed (next disabled)")
                        break
                    else:
                        print("Exam completed")
                        break
            else:
                consecutive_empty += 1
                self.debug("Step %d: section transition" % step)
                for retry in range(3):
                    time.sleep(2)
                    nr = self.click_next()
                    if nr.get('success'):
                        time.sleep(1)
                        break
                else:
                    print("Exam completed (no more sections)")
                    break

            time.sleep(0.3)
            nr = self.click_next()
            if nr.get('success'):
                time.sleep(0.6)
            elif nr.get('reason') == 'disabled':
                print("Exam completed (next disabled)")
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

        if self.ws:
            self.ws.close()
        return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ETS Auto Answer v8 - auto-answer ETS exams via CDP")
    parser.add_argument("--max", type=int, default=999, help="Safety limit (default: 999, exam auto-stops when done)")
    parser.add_argument("--debug", action="store_true", help="Verbose output for troubleshooting")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--show-answers", action="store_true", help="Show all answers without auto-answering")
    args = parser.parse_args()
    auto = ETSAutoAnswer(debug_mode=args.debug)
    if args.show_answers:
        auto.connect()
        auto.load_answers()
        auto.show_answers()
        if args.json:
            print(json.dumps(auto.get_all_answers(), ensure_ascii=False, indent=2))
    else:
        result = auto.run(max_steps=args.max)
        if args.json and result:
            print(json.dumps(result, ensure_ascii=False))