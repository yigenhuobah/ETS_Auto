#!/usr/bin/env python3
"""Read-Write (读写同步) mode helpers for ETSAutoAnswer.

Extracted from ets_auto. Mixin expects exam instance attributes used by RW
loops (answers, stats, reconnect_control, inject_bridge, etc.).
"""
import re
import time


class ETSReadWriteMixin:
    def _detect_rw_mode(self):
        """Detect if current page is read-write (读写同步) mode via URL hash.
        Sets self.rw_mode = True if on #/readingWritingDetails page."""
        try:
            url = self._tab_url()
            if 'readingWritingDetails' in url or 'readingWriting' in url:
                self.rw_mode = True
                self.debug("Read-Write mode detected (读写同步)")
                return True
        except Exception as e:
            self.debug("RW mode detection error: " + str(e))
        self.rw_mode = False
        return False

    _RW_CACHE_TTL = 30  # seconds before rw_show_data cache expires

    def get_rw_show_data(self):
        """Get showData from read-write iframe. Contains all questions + answers.
        Cached with 30s TTL to avoid stale data on AJAX page updates."""
        if self.rw_show_data and (time.time() - self._rw_cache_time < self._RW_CACHE_TTL):
            return self.rw_show_data
        js = '(function(){\n        try {\n        %s;\n            if (!iframe) return JSON.stringify({error: "no read-write iframe"});\n            var data = iframe.contentWindow.showData;\n            if (!data) return JSON.stringify({error: "no showData"});\n            return JSON.stringify(data);\n        } catch(e) { return JSON.stringify({error: e.message}); }\n        })()' % self._RW_IFRAME_FINDER
        parsed = self.parse_eval_json(self.eval_js(js))
        # Don't cache error responses — they must be retried
        if not parsed or parsed.get('error'):
            return None
        self.rw_show_data = parsed
        self._rw_cache_time = time.time()
        return self.rw_show_data

    def get_rw_page_state(self):
        """Get state from read-write page: li.pointer options grouped by question.
        Returns: {questions: [{qid, options: [{option, text, selected}], any_selected}]}"""
        js = '(function(){\n        try {\n        %s;\n            if (!iframe) return JSON.stringify({error: "no iframe"});\n            var doc = iframe.contentDocument;\n            if (!doc) return JSON.stringify({error: "no contentDocument"});\n            var result = {questions: []};\n            var uls = doc.querySelectorAll("ul[data-id]");\n            uls.forEach(function(ul){\n                var qid = ul.getAttribute("data-id").split("-").slice(1).join("-");\n                var opts = ul.querySelectorAll("li.pointer");\n                var qInfo = {qid: qid, options: [], any_selected: false};\n                opts.forEach(function(li){\n                    var opt = li.getAttribute("data-option") || "";\n                    var txt = (li.innerText || "").trim().substring(0, 50);\n                    var sel = li.classList.contains("on") || li.classList.contains("selected") || li.classList.contains("active");\n                    qInfo.options.push({option: opt, text: txt, selected: sel});\n                    if (sel) qInfo.any_selected = true;\n                });\n                result.questions.push(qInfo);\n            });\n            return JSON.stringify(result);\n        } catch(e) { return JSON.stringify({error: e.message}); }\n        })()' % self._RW_IFRAME_FINDER
        return self.parse_eval_json(self.eval_js(js))

    def answer_rw_choose(self, state=None):
        """Answer all visible choice questions in read-write mode.
        Uses showData for answers, clicks li.pointer[data-option].
        Matches questions by qid (data-id attribute) instead of index.

        state: optional pre-fetched get_rw_page_state() result to avoid a
        second CDP round-trip when the loop just read the page.
        """
        show_data = self.get_rw_show_data()
        if not show_data or show_data.get('error'):
            self.debug("RW: No showData available")
            return False, False

        questions = show_data.get('question', [])
        if not questions:
            self.debug("RW: No questions in showData")
            return False, False

        if state is None:
            state = self.get_rw_page_state()
        if state.get('error'):
            self.debug("RW state error: " + str(state.get('error')))
            return False, False

        page_questions = state.get('questions', [])
        if not page_questions:
            return False, False

        # Build answer dict from showData: qid → [answer_letter, ...]
        # Each question may have multiple sub-questions (info items)
        answer_dict = {}  # {qid: [letter1, letter2, ...]}
        for q in questions:
            qid = q.get('id', '')
            info_list = q.get('info', [])
            letters = []
            for info_item in info_list:
                if not isinstance(info_item, dict):
                    continue
                ans = info_item.get('answer', '')
                if ans:
                    letters.append(ans.upper())
            if letters:
                answer_dict[qid] = letters

        any_new = False
        all_done = True

        # Track per-qid sub-question index: how many siblings with same qid
        # have already been answered on this page.  Each ul[data-id] with the
        # same qid is a different sub-question, so we advance through letters[]
        # as we encounter repeats.
        qid_seen = {}  # {qid: count_of_occurrences_processed}

        # Match by qid: page_questions → answer_dict
        for i, pq in enumerate(page_questions):
            pqid = pq.get('qid', '')
            # Always advance sub-question index so later siblings stay aligned
            sub_idx = qid_seen.get(pqid, 0)
            qid_seen[pqid] = sub_idx + 1

            if pq.get('any_selected'):
                self.debug("RW Q#%d already selected" % (i + 1))
                continue
            all_done = False

            letters = answer_dict.get(pqid)
            if not letters:
                self.debug("RW Q#%d (qid:%s): no answer in showData" % (i + 1, pqid))
                continue

            # Use the sub-question's answer; fall back to last if index out of range
            if sub_idx < len(letters):
                answer = letters[sub_idx]
            else:
                answer = letters[-1]
                self.debug("RW Q#%d (qid:%s): sub_idx %d out of range (%d letters), using last" %
                           (i + 1, pqid, sub_idx, len(letters)))
            if not answer:
                continue

            # Validate: answer must be a single letter A-Z
            if not re.match(r'^[A-Z]$', answer):
                self.debug("RW Q#%d: invalid answer '%s', skipping" % (i + 1, answer))
                continue

            # Click the option matching the answer letter (escape for JS safety)
            safe_answer = self.js_escape(answer)
            js_click = '(function(){\n            try {\n            %s;\n                if (!iframe) return "no iframe";\n                var doc = iframe.contentDocument;\n                var uls = doc.querySelectorAll("ul[data-id]");\n                var ul = uls[%d];\n                if (!ul) return "no ul at index %d";\n                var li = ul.querySelector("li.pointer[data-option=\'%s\']");\n                if (!li) return "no li for option %s";\n                li.click();\n                return "clicked %s";\n            } catch(e) { return "error: " + e.message; }\n            })()' % (self._RW_IFRAME_FINDER, i, i, safe_answer, safe_answer, safe_answer)
            result = self.eval_js(js_click)
            self.debug("RW click Q#%d (qid:%s): %s" % (i + 1, pqid, str(result)))
            if 'clicked' in str(result):
                any_new = True
                self.stats['choose_answered'] += 1
                print("  RW Q#%d (id:%s) → %s" % (i + 1, pqid, answer))
                if self._on_question_answered:
                    try:
                        self._on_question_answered(str(pqid), answer, 'choose')
                    except Exception:
                        pass
                self._fire_question({
                    'type': 'choose', 'type_label': '选择题(RW)',
                    'qid': str(pqid), 'answer': answer,
                    'answered': self.stats['choose_answered'],
                    'total_questions': self.total_questions,
                })

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
        r = self.parse_eval_json(self.eval_js(js))
        if r.get('error') and 'success' not in r:
            return {"success": False, "reason": r.get('error')}
        if r.get('success'):
            self.stats['next_click'] += 1
            self.debug("RW Next: 下一步 clicked")
        return r

    # ── Answer Loading ────────────────────────────────────────

    def _build_rw_answers_from_showdata(self, show_data, verbose=True):
        """OPEN-H6: (re)build self.answers rw_* keys from iframe showData."""
        # Drop previous rw_ keys only; keep any non-rw entries if mixed
        self.answers = {k: v for k, v in self.answers.items()
                        if not str(k).startswith('rw_')}
        questions = (show_data or {}).get('question', []) or []
        rw_count = 0
        for q in questions:
            if not isinstance(q, dict):
                continue
            qid = q.get('id', '')
            info_list = q.get('info', [])
            if not isinstance(info_list, list):
                continue
            for idx_i, info_item in enumerate(info_list):
                if not isinstance(info_item, dict):
                    continue
                ans = info_item.get('answer', '')
                if ans:
                    key = 'rw_' + str(qid) + ('_%d' % idx_i if len(info_list) > 1 else '')
                    self.answers[key] = {'type': 'choose', 'answer': ans.upper()}
                    if verbose:
                        print("  Q:%s [%d] → %s" % (qid, idx_i, ans.upper()))
                    rw_count += 1
        self.total_questions = rw_count
        return rw_count

    def _rw_post_reconnect(self):
        """Shared RW reconnect tail: pinia, mode, bridge, cache, rebuild answers.

        Returns False if mode is lost or showData cannot be refreshed, so the
        caller does not resume with empty/stale RW answers.
        """
        self._read_pinia_config()
        self._detect_rw_mode()
        try:
            self.inject_bridge()
        except Exception as br_err:
            self.debug("RW inject_bridge after reconnect: %s" % br_err)
        self.rw_show_data = None
        self._rw_cache_time = 0
        if not self.rw_mode:
            return False
        # OPEN-H6: rebuild answer table from fresh showData; retry once if empty
        show_data = self.get_rw_show_data()
        if not show_data or show_data.get('error'):
            self.debug("RW: showData missing after reconnect, retrying once...")
            self.interruptible_sleep(0.5)
            self.rw_show_data = None
            self._rw_cache_time = 0
            show_data = self.get_rw_show_data()
        if not show_data or show_data.get('error'):
            self.debug("RW: showData still empty/failed after reconnect: %s" % (
                (show_data or {}).get('error', 'empty')))
            return False
        n = self._build_rw_answers_from_showdata(show_data, verbose=False)
        self.debug("RW: rebuilt %d answers after reconnect" % n)
        if n <= 0:
            self.debug("RW: rebuilt 0 answers after reconnect; refusing to resume")
            return False
        return True

    def _handle_rw_reconnect(self, conn_err, consecutive_conn_errors, *, label='RW'):
        """Shared RW reconnect control flow. Returns 'continue' | 'break'.

        Uses ETSBase.reconnect_control + _rw_post_reconnect fail-closed.
        """
        self.debug("%s connection error detail: %s" % (label, conn_err))
        return self.reconnect_control(
            consecutive_conn_errors,
            post_ok=self._rw_post_reconnect,
            label=label,
            max_errors=3,
            sleep_ok=1.0,
            sleep_fail=1.0,
        )


    def _run_rw_loop(self, max_steps=999):
        """Read-Write (读写同步) mode loop. Different DOM, different navigation."""

        # H3: hotkeys + reconnect (aligned with _run_loop, without huge rewrite)
        hotkey = None
        try:
            from ets_hotkey import ETSHotkey
            hotkey = ETSHotkey(on_stop=self._signal_stop)
            hotkey.register()
        except Exception as e:
            self.debug("RW hotkey init failed: %s" % e)
            hotkey = None

        try:
            # Get showData for answers
            show_data = self.get_rw_show_data()
            if not show_data or show_data.get('error'):
                print("ERROR: Cannot read showData from iframe: %s" % (show_data or {}).get('error', 'unknown'))
                print("Make sure the read-write page is fully loaded.")
                return {'total_answered': 0, 'mode': 'read-write', 'errors': 1}

            print("Questions in showData: %d" % len(show_data.get('question', []) or []))
            rw_count = self._build_rw_answers_from_showdata(show_data, verbose=True)
            print("Total RW answers: %d" % rw_count)

            step = 0
            consecutive_empty = 0
            consecutive_no_progress = 0  # pages where we couldn't answer anything
            consecutive_conn_errors = 0
            consecutive_next_fail = 0  # next not found / stuck on same page

            while True:
                if self.stop_event and self.stop_event.is_set():
                    break

                # H3: hotkey checks
                if hotkey and hotkey.should_stop:
                    print("\n🛑 Emergency stop (F12)")
                    self._signal_stop()
                    break
                if hotkey and hotkey.should_skip:
                    hotkey.clear_skip()
                    print("\n⏭ Skipping current RW page (F10)")
                    try:
                        nr = self.click_rw_next()
                    except (ConnectionError, TimeoutError) as conn_err:
                        consecutive_conn_errors += 1
                        action = self._handle_rw_reconnect(conn_err, consecutive_conn_errors)
                        if action == 'break':
                            break
                        continue
                    consecutive_conn_errors = 0
                    if nr.get('success'):
                        self.rw_show_data = None
                        consecutive_next_fail = 0
                        self.interruptible_sleep(1)
                    continue
                if hotkey and hotkey.is_paused:
                    self.interruptible_sleep(0.5)
                    continue

                step += 1
                if step > max_steps:
                    print("Safety limit reached (%d steps)" % max_steps)
                    break

                # H3: reconnect on ConnectionError/TimeoutError (state + answer + next)
                try:
                    state = self.get_rw_page_state()
                    consecutive_conn_errors = 0
                except (ConnectionError, TimeoutError) as conn_err:
                    consecutive_conn_errors += 1
                    action = self._handle_rw_reconnect(conn_err, consecutive_conn_errors)
                    if action == 'break':
                        break
                    continue

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
                        try:
                            nr = self.click_rw_next()
                        except (ConnectionError, TimeoutError) as conn_err:
                            consecutive_conn_errors += 1
                            action = self._handle_rw_reconnect(conn_err, consecutive_conn_errors)
                            if action == 'break':
                                break
                            continue
                        consecutive_conn_errors = 0
                        if nr.get('success'):
                            consecutive_empty = 0
                            consecutive_next_fail = 0
                            self.rw_show_data = None  # clear cache for next page group
                            self.interruptible_sleep(1)
                            continue
                        print("RW: No more questions visible.")
                        break
                    self.interruptible_sleep(1)
                    continue

                consecutive_empty = 0
                try:
                    any_new, all_done = self.answer_rw_choose(state=state)
                except (ConnectionError, TimeoutError) as conn_err:
                    consecutive_conn_errors += 1
                    action = self._handle_rw_reconnect(conn_err, consecutive_conn_errors)
                    if action == 'break':
                        break
                    continue

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
                try:
                    nr = self.click_rw_next()
                except (ConnectionError, TimeoutError) as conn_err:
                    consecutive_conn_errors += 1
                    action = self._handle_rw_reconnect(conn_err, consecutive_conn_errors)
                    if action == 'break':
                        break
                    continue
                if nr.get('success'):
                    method = nr.get('method', '')
                    if method == '提交':
                        print("RW: 提交 clicked, task complete.")
                        break
                    self.rw_show_data = None  # clear cache for next page group
                    consecutive_next_fail = 0
                    self.interruptible_sleep(1)
                elif nr.get('reason') == 'disabled':
                    # Still processing current step
                    self.debug("RW: Next disabled, waiting...")
                    nr2 = {'method': ''}
                    rw_finished = False
                    wait_conn_errors = 0
                    for _ in range(15):
                        self.interruptible_sleep(1)
                        try:
                            if self._all_sidebar_correct():
                                print("RW: All sidebar items correct, exam complete.")
                                rw_finished = True
                                break
                            nr2 = self.click_rw_next()
                        except (ConnectionError, TimeoutError) as conn_err:
                            wait_conn_errors += 1
                            consecutive_conn_errors += 1
                            self.debug("RW next-wait connection error #%d: %s" % (
                                wait_conn_errors, conn_err))
                            if consecutive_conn_errors >= 3 or wait_conn_errors >= 3:
                                print("\nRW: Connection lost during next-wait, stopping.")
                                rw_finished = True
                                break
                            action = self._handle_rw_reconnect(conn_err, consecutive_conn_errors)
                            if action == 'break':
                                rw_finished = True
                                break
                            continue
                        if nr2.get('success'):
                            method = nr2.get('method', '')
                            if method == '提交':
                                print("RW: 提交 clicked, task complete.")
                                rw_finished = True
                            else:
                                self.rw_show_data = None
                                consecutive_next_fail = 0
                                consecutive_conn_errors = 0
                            break
                    else:
                        # Final sidebar check before giving up
                        try:
                            if self._all_sidebar_correct():
                                print("RW: All sidebar items correct, exam complete.")
                            else:
                                print("RW: Next button disabled too long, may be complete.")
                        except (ConnectionError, TimeoutError):
                            print("RW: Next button disabled too long, may be complete.")
                        rw_finished = True
                    if rw_finished or nr2.get('method') == '提交':
                        break
                elif nr.get('reason') == 'not found':
                    # Maybe on last step or page changed
                    self.debug("RW: Next button not found")
                    consecutive_next_fail += 1
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
                    except Exception:
                        pass
                    if consecutive_next_fail >= 8:
                        print("RW: Next button not found repeatedly, stopping.")
                        break
                else:
                    consecutive_next_fail += 1
                    if consecutive_next_fail >= 8:
                        print("RW: Next failed repeatedly (%s), stopping." % nr.get('reason'))
                        break

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

            self._fire_complete(result)
            return result
        finally:
            if self.ws:
                try:
                    self.ws.close()
                except Exception:
                    pass
            if hotkey:
                try:
                    hotkey.unregister()
                except Exception:
                    pass

