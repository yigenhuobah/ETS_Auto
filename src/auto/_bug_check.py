#!/usr/bin/env python3
"""Deep bug analysis script for ETS_Auto codebase."""
import ast
import re

with open('ets_auto.py', encoding='utf-8') as f:
    src_auto = f.read()
with open('ets_common.py', encoding='utf-8') as f:
    src_common = f.read()
with open('ets_gui.py', encoding='utf-8') as f:
    src_gui = f.read()

print("=" * 60)
print("BUG ANALYSIS REPORT")
print("=" * 60)

# Bug 1: \n4. in print statements (should be \n + "4. ")
print("\n--- Bug 1: Escaped newline in print statements ---")
for i, line in enumerate(src_auto.split('\n'), 1):
    if '\\n4. ' in line:
        print(f"  Line {i}: {line.strip()}")
        print(f"  Issue: \\n4. is literal backslash-n-4, not a newline + list item")
        print(f"  Fix: Change to \\n4. → \\n 4. or \\n\"4. ...")

# Bug 2: eval_js_safe returns None but caller does json.loads without fallback
print("\n--- Bug 2: json.loads on eval_js result without None guard ---")
lines_auto = src_auto.split('\n')
for i, line in enumerate(lines_auto, 1):
    stripped = line.strip()
    if 'json.loads(self.eval_js(' in stripped:
        if "or '{}'" not in stripped and 'or "{}"' not in stripped:
            print(f"  Line {i}: {stripped}")

# Bug 3: answer_rw_choose sub-question indexing
print("\n--- Bug 3: RW answer sub-question index logic ---")
# In answer_rw_choose, letters[0] is always used, even for multi-sub-question pages
# The code doesn't track which sub-question index we're on
print("  In answer_rw_choose(): answer = letters[0] is always first answer")
print("  If a page has multiple sub-questions, only the first gets answered")
print("  The comment says 'determine which sub-question index this is by counting'")
print("  but the logic doesn't actually do that counting")

# Bug 4: reconnect in _run_loop doesn't reload answers
print("\n--- Bug 4: Reconnect doesn't reload strategy/answers ---")
print("  After reconnect, _read_pinia_config() + _detect_rw_mode() are called")
print("  But load_answers() is NOT called — if set_id changed, answers are stale")

# Bug 5: eval_js_safe masks connection errors
print("\n--- Bug 5: eval_js_safe silently returns None ---")
print("  eval_js_safe catches ConnectionError and returns None")
print("  Callers that don't check for None will get TypeError or silent failure")
print("  The main loop wraps page state reads in try/except, but eval_js_safe")
print("  inside get_page_state/get_rw_page_state will NOT trigger reconnect")

# Bug 6: _wait_for_next doesn't check stop_event properly
print("\n--- Bug 6: _wait_for_next stop_event check ---")
# Check if _wait_for_next checks stop_event
wait_src = '''
def _wait_for_next(self, max_wait_loops=30, wait_sec=2, label="next"):
    for _ in range(max_wait_loops):
        self.interruptible_sleep(wait_sec)
'''
if '_wait_for_next' in src_auto:
    idx = src_auto.index('def _wait_for_next')
    chunk = src_auto[idx:idx+500]
    if 'stop_event' not in chunk.split('def ')[0] or True:
        print("  _wait_for_next uses interruptible_sleep which raises InterruptedError")
        print("  But InterruptedError is NOT caught in _wait_for_next itself")
        print("  It propagates up to _run_loop which also doesn't catch it explicitly")
        print("  Only run() catches InterruptedError — but _run_loop is called from worker")

# Bug 7: GUI _on_start re-checks remote with sleep(0.3)
print("\n--- Bug 7: GUI _on_start blocks main thread with sleep(0.3) ---")
print("  _on_start calls _check_remote_async() then time.sleep(0.3)")
print("  This blocks the tkinter main loop for 300ms, causing UI freeze")
print("  The remote check is already non-blocking — the sleep is pointless")

# Bug 8: QueueWriter writes to original synchronously from worker thread
print("\n--- Bug 8: QueueWriter.original write from worker thread ---")
print("  QueueWriter.write() calls self.original.write(message)")
print("  If original is sys.stdout (default), and worker thread writes,")
print("  this could race with main thread tkinter updates")
print("  Actually: _original_stdout is captured before redirect, so it's the")
print("  real stdout. Writing to real stdout from worker is thread-safe enough.")
print("  But: if _restore_streams() sets sys.stdout back to original while")
print("  worker still holds a QueueWriter ref, worker writes to real stdout directly")

# Bug 9: _run_loop Unicode escape in print
print("\n--- Bug 9: Unicode escape in source strings ---")
for i, line in enumerate(src_auto.split('\n'), 1):
    # Look for actual literal \n followed by digit in string context
    if re.search(r'"\\n\d', line):
        print(f"  Line {i}: {line.strip()}")

# Bug 10: RW mode - rw_show_data cache not cleared on reconnect
print("\n--- Bug 10: RW cache stale after reconnect ---")
print("  After reconnect in _run_loop, rw_show_data cache is NOT cleared")
print("  _rw_cache_time still holds old timestamp → stale data used")
print("  Fix: set self.rw_show_data = None and self._rw_cache_time = 0 on reconnect")

# Bug 11: _find_latest_set_id called with ets_base that might not exist yet
print("\n--- Bug 11: _find_latest_set_id ets_base check ---")
print("  Code checks 'if not self.ets_base' before calling, but ets_base is set")
print("  to default path just before. The isdir check inside handles non-existent")
print("  dirs gracefully. This is OK.")

# Bug 12: load_answers doesn't handle set_id as int vs string
print("\n--- Bug 12: set_id type inconsistency ---")
print("  _read_pinia_config: hw_set = str(cfg.get('hw_set_id') or '') → string")
print("  _get_url_set_id: returns qs[key][0] → string")
print("  _find_latest_set_id: returns entry (dir name) → string")
print("  But load_answers: self.set_id used in os.path.join — OK if string")
print("  Strategy: self.strategy.load_set(self.set_id) — OK if string")
print("  No bug here, but worth noting.")

# Bug 13: answer_fill doesn't verify the fill was successful
print("\n--- Bug 13: answer_fill doesn't verify fill success ---")
print("  After setting value via JS, code checks r1 = json.loads(...)")
print("  But doesn't verify r1.get('filled') == True")
print("  If fill failed (element not found), stats still increment")

# Bug 14: click_next method 2 removes .none class
print("\n--- Bug 14: click_next force-shows hidden submit button ---")
print("  Method 2 removes 'none' class from parent to force-show next_icon")
print("  This could submit before audio finishes playing on pages where")
print("  answer was selected but audio is still required")

# Bug 15: GUI progress bar doesn't reset on error
print("\n--- Bug 15: GUI progress bar stuck on error ---")
print("  _run_finished sets progress to 1.0 only if not stopped")
print("  But if error occurs, _running=False, status='已完成' (not '错误')")
print("  Progress bar stays at whatever value it was at")

print("\n" + "=" * 60)
print("END OF REPORT")
