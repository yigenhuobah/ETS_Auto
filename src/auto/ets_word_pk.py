#!/usr/bin/env python3
"""
ETS Word PK Auto Answer v5 — e听说单词PK自动答题
改进: 派生词自动生成 + 短语提取 + 选项反查(含词根回退)
"""
import json, os, time, re, sys
from ets_common import ETSBase


class ETSWordPK(ETSBase):
    def __init__(self, port=10086, debug_mode=False):
        super().__init__(port=port, debug_mode=debug_mode)
        self.ets_base = os.path.join(os.path.expandvars(r'%APPDATA%'), 'ETS')
        self.dict_path = os.path.join(self.ets_base, 'pc_xst_dict', 'pc_xst_dict.json')
        self.ecdict_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), 'ecdict_pk.json')
        self.extra_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), 'pk_extra.json')
        self.misses_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), 'pk_misses.json')
        self.word_trans = {}      # word.lower() -> full_trans
        self.trans_index = {}     # chinese_segment -> [word1, word2, ...]
        self.pk_extra = {}        # question_text -> correct_option (self-learned)
        self.stats = {'answered': 0, 'no_match': 0, 'errors': 0, 'learned': 0}

    # ── Dictionary Loading ──────────────────────────────────

    def load_dictionary(self):
        if not os.path.exists(self.dict_path):
            print("ERROR: Dictionary not found: " + self.dict_path)
            return False
        t0 = time.time()
        with open(self.dict_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for entry in data:
            word = entry.get('Word', '').strip()
            trans = entry.get('Trans', '').strip()
            if not word or not trans:
                continue
            self.word_trans[word.lower()] = trans
            for line in trans.split('\n'):
                line = line.strip()
                if not line:
                    continue
                cn = re.sub(r'^([a-z]+\.\s*(,\s*[a-z]+\.\s*)*)', '', line).strip()
                if cn:
                    self.trans_index.setdefault(cn, []).append(word)
        base_count = len(self.word_trans)

        # ── Load ECDICT PK supplement ──
        ecdict_count = 0
        if os.path.exists(self.ecdict_path):
            with open(self.ecdict_path, 'r', encoding='utf-8') as f:
                ecdict = json.load(f)
            for word, trans in ecdict.items():
                if word not in self.word_trans:
                    self.word_trans[word] = trans
                    cn = re.sub(r'^([a-z]+\.\s*(,\s*[a-z]+\.\s*)*)', '', trans).strip()
                    if cn:
                        self.trans_index.setdefault(cn, []).append(word)
                    ecdict_count += 1
            print("  ECDICT supplement: +%d (%d total)" % (ecdict_count, len(self.word_trans)))
        else:
            print("  ECDICT not found: %s (skip)" % self.ecdict_path)

        # ── Generate derivative words ──
        deriv_rules = [
            ('al',   'ally'),     # musical → musically
            ('ic',   'ically'),   # artistic → artistically
            ('ble',  'bly'),      # acceptable → acceptably
            ('ve',   'vely'),     # expressive → expressively
            ('te',   'tely'),     # delicate → delicately
            ('se',   'sely'),     # sparse → sparsely
            ('ous',  'ously'),    # mysterious → mysteriously
            ('ful',  'fully'),    # faithful → faithfully
            ('less', 'lessly'),   # merciless → mercilessly
            ('ing',  'ingly'),    # striking → strikingly
            ('ed',   'edly'),     # talented → talentedly
            ('ary',  'arily'),    # voluntary → voluntarily
            ('ory',  'orily'),    # obligatory → obligatorily
            ('ent',  'ently'),    # silent → silently
            ('ant',  'antly'),    # significant → significantly
            ('id',   'idly'),     # vivid → vividly
            ('le',   'ly'),       # gentle → gently
        ]
        new_deriv = {}
        for word, trans in list(self.word_trans.items()):
            wl = word.lower()
            for suffix, new_suffix in deriv_rules:
                if wl.endswith(suffix):
                    deriv = wl[:-len(suffix)] + new_suffix
                    if deriv not in self.word_trans and deriv not in new_deriv:
                        new_trans_lines = []
                        for line in trans.split('\n'):
                            line = line.strip()
                            if not line:
                                continue
                            new_line = re.sub(r'^(adj|v|n)\.', 'adv.', line)
                            new_trans_lines.append(new_line)
                        if new_trans_lines:
                            new_deriv[deriv] = '\n'.join(new_trans_lines)

        for deriv, dtrans in new_deriv.items():
            self.word_trans[deriv] = dtrans
            for line in dtrans.split('\n'):
                line = line.strip()
                if not line:
                    continue
                cn = re.sub(r'^([a-z]+\.\s*(,\s*[a-z]+\.\s*)*)', '', line).strip()
                if cn:
                    self.trans_index.setdefault(cn, []).append(deriv)

        # ── Extract phrases from Trans ──
        compound_count = 0
        for word, trans in list(self.word_trans.items()):
            for line in trans.split('\n'):
                line = line.strip()
                if not line:
                    continue
                phrases = re.findall(r'([a-z][a-z\-]+(?:\s[a-z][a-z\-]+)+)', line)
                for ph in phrases:
                    ph = ph.strip()
                    phl = ph.lower()
                    if ' ' in phl and phl not in self.word_trans and len(phl) <= 40:
                        self.word_trans[phl] = line
                        cn = re.sub(r'^([a-z]+\.\s*(,\s*[a-z]+\.\s*)*)', '', line).strip()
                        if cn:
                            self.trans_index.setdefault(cn, []).append(ph)
                            compound_count += 1

        # ── Load self-learned extra mappings ──
        extra_count = 0
        if os.path.exists(self.extra_path):
            with open(self.extra_path, 'r', encoding='utf-8') as f:
                self.pk_extra = json.load(f)
            for q_text, answer in self.pk_extra.items():
                # Inject into trans_index so find_answer can use it
                q_clean = re.sub(r'^([a-z]+\.\s*(,\s*[a-z]+\.\s*)*)', '', q_text).strip()
                if q_clean:
                    self.trans_index.setdefault(q_clean, []).insert(0, answer)
                    # Also add exact question text as key
                    self.trans_index.setdefault(q_text, []).insert(0, answer)
                extra_count += 1
            print("  Self-learned extra: +%d mappings" % extra_count)

        print("Dictionary: %d base + %d ecdict + %d deriv + %d compound + %d extra = %d total (%.1fs)" % (
            base_count, ecdict_count, len(new_deriv), compound_count, extra_count, len(self.word_trans), time.time() - t0))
        return True

    # ── Matching ────────────────────────────────────────────

    def get_stems(self, word):
        """Try to strip suffixes to find base form in dictionary"""
        w = word.lower().strip()
        candidates = [w]
        
        # British → American spelling conversion
        for brit, us in [('ise', 'ize'), ('isation', 'ization'), ('ising', 'izing'),
                         ('yse', 'yze'), ('our', 'or'), ('re', 'er'),
                         ('ogue', 'og'), ('ence', 'ense')]:
            if w.endswith(brit) and len(w) > len(brit) + 1:
                candidates.append(w[:-len(brit)] + us)
        if w.endswith('ly') and len(w) > 4:
            candidates.append(w[:-2])           # mentally → mental
            if w[:-3].endswith('al'):            # musically → music
                candidates.append(w[:-4])
            if w[:-3].endswith('ic'):            # artistically → artist
                candidates.append(w[:-4])
            if w[:-3].endswith('le'):            # gently → gentle
                candidates.append(w[:-2] + 'le')
        if w.endswith('ing') and len(w) > 5:
            candidates.append(w[:-3])            # talking → talk
            candidates.append(w[:-3] + 'e')      # making → make
        if w.endswith('ed') and len(w) > 4:
            candidates.append(w[:-2])            # played → play
            candidates.append(w[:-1])            # liked → like
            if w.endswith('ied'):
                candidates.append(w[:-3] + 'y')  # studied → study
        if w.endswith('es') and len(w) > 4:
            candidates.append(w[:-2])            # boxes → box
        if w.endswith('s') and len(w) > 3 and not w.endswith('ss'):
            candidates.append(w[:-1])            # cats → cat
        if w.endswith('er') and len(w) > 4:
            candidates.append(w[:-2])            # faster → fast
        if w.endswith('est') and len(w) > 5:
            candidates.append(w[:-3])            # fastest → fast
        return list(set(c for c in candidates if c))

    def get_opt_trans(self, opt):
        """Get translations for an option, including sub-phrase extraction."""
        stems = self.get_stems(opt)
        word_trans_parts = []
        for s in stems:
            if s in self.word_trans:
                word_trans_parts.append(self.word_trans[s])

        phrase_trans = []
        if ' ' in opt:
            words = opt.split()
            for n in range(len(words), 1, -1):
                for start in range(len(words) - n + 1):
                    sub = ' '.join(words[start:start+n])
                    if sub in self.word_trans:
                        phrase_trans.append(self.word_trans[sub])
                        break
                if phrase_trans:
                    break

        all_parts = phrase_trans + word_trans_parts
        combined = ' '.join(all_parts) if all_parts else ''
        return {
            'stems': stems,
            'word_trans_parts': word_trans_parts,
            'phrase_trans': phrase_trans,
            'combined': combined
        }

    def find_answer(self, question_text, options):
        """Four-tier matching: self-learned → option reverse → trans_index → loose"""
        q = question_text.strip()
        if not q:
            return -1

        # ── Strategy 0: Self-learned exact match ──
        if q in self.pk_extra:
            answer = self.pk_extra[q]
            for i, opt in enumerate(options):
                if opt.strip() == answer.strip():
                    self.debug("Learned: '%s' -> %s" % (q, answer))
                    return i

        q_clean = re.sub(r'^([a-z]+\.\s*(,\s*[a-z]+\.\s*)*)', '', q).strip()
        q_terms = re.split(r'[，,、；；]', q_clean)
        q_terms = [t.strip() for t in q_terms if t.strip()]

        # ── Strategy 0.5: English question → match options to word_trans ──
        q_lower = q.lower()
        q_stems = self.get_stems(q_lower)
        for stem in [q_lower] + q_stems:
            if stem in self.word_trans:
                trans_text = self.word_trans[stem]
                best_idx = -1
                best_score = 0
                for i, opt in enumerate(options):
                    opt_clean = re.sub(r'^([a-z]+\.\s*(,\s*[a-z]+\.\s*)*)', '', opt).strip()
                    score = 0
                    if opt_clean and opt_clean in trans_text:
                        score += 100 + len(opt_clean)
                    opt_terms = re.split(r'[，,、；；]', opt_clean)
                    for ot in opt_terms:
                        ot = ot.strip()
                        if ot and ot in trans_text:
                            score += len(ot)
                    if opt in trans_text:
                        score += 50
                    opt_cn = set(c for c in opt_clean if '\u4e00' <= c <= '\u9fff')
                    trans_cn = set(c for c in trans_text if '\u4e00' <= c <= '\u9fff')
                    overlap = opt_cn & trans_cn
                    if len(overlap) >= 2:
                        score += len(overlap) * 3
                    opt_cn_str = ''.join(c for c in opt_clean if '\u4e00' <= c <= '\u9fff')
                    for j in range(len(opt_cn_str) - 1):
                        bigram = opt_cn_str[j:j+2]
                        if bigram in trans_text:
                            score += 8
                    if score > best_score:
                        best_score = score
                        best_idx = i
                if best_idx >= 0 and best_score > 0:
                    self.debug("EnQ: '%s' (stem=%s) -> opt[%d]='%s' score=%d" % (q, stem, best_idx, options[best_idx], best_score))
                    return best_idx

        # ── Strategy 1: Option reverse lookup (with stem expansion) ──
        matches = []
        for i, opt in enumerate(options):
            trans_result = self.get_opt_trans(opt)
            combined_trans = trans_result['combined']
            if not combined_trans:
                continue
            score = 0
            if trans_result['phrase_trans']:
                score += 50
            if q_clean in combined_trans.replace(' ', ''):
                score += 100
            for term in q_terms:
                if term in combined_trans:
                    score += len(term) * 2
            if score == 0 and len(q_clean) >= 4:
                for start in range(0, len(q_clean) - 1):
                    sub2 = q_clean[start:start+2]
                    if sub2 in combined_trans:
                        score += 1
            if score > 0:
                matches.append((i, opt, score))
                self.debug("OptRev: '%s' (stems=%s) score=%d" % (
                    opt, trans_result['stems'][:3], score))
        if matches:
            matches.sort(key=lambda x: -x[2])
            return matches[0][0]

        # ── Strategy 2: trans_index ──
        candidates = []
        if q_clean in self.trans_index:
            candidates = self.trans_index[q_clean]
            self.debug("ExactIdx: '%s' -> %s" % (q_clean, candidates[:5]))
        if not candidates:
            for term in q_terms:
                if len(term) >= 2:
                    for cn_text, words in self.trans_index.items():
                        if term in cn_text:
                            candidates.extend(words)
                            self.debug("SubIdx: '%s' in '%s'" % (term, cn_text[:25]))
                            break
                    if candidates:
                        break

        if candidates:
            c_lower = set(c.lower().strip() for c in candidates)
            for i, opt in enumerate(options):
                if opt.lower().strip() in c_lower:
                    return i
            for i, opt in enumerate(options):
                stems = self.get_stems(opt)
                for s in stems:
                    if s in c_lower:
                        return i
            c_parts = set()
            for c in candidates:
                for part in c.lower().strip().split('-'):
                    if len(part) >= 3:
                        c_parts.add(part)
            for i, opt in enumerate(options):
                opt_words = set(w.lower().strip() for w in re.split(r'[-\s]+', opt) if len(w) >= 3)
                if opt_words & c_parts:
                    return i
            for i, opt in enumerate(options):
                stems = self.get_stems(opt)
                for s in stems:
                    if s in self.word_trans:
                        trans = self.word_trans[s]
                        for c in candidates:
                            c_words = c.lower().replace('-', ' ').split()
                            for cw in c_words:
                                if len(cw) >= 3 and cw in trans.lower():
                                    return i

        # ── Strategy 2.5: Char-overlap scoring ──
        if any('\u4e00' <= c <= '\u9fff' for c in q):
            q_cn_chars = set(c for c in q if '\u4e00' <= c <= '\u9fff')
            if len(q_cn_chars) >= 2:
                best_idx = -1
                best_score = 0
                for i, opt in enumerate(options):
                    trans_result = self.get_opt_trans(opt)
                    combined = trans_result['combined']
                    if not combined:
                        continue
                    trans_cn_chars = set(c for c in combined if '\u4e00' <= c <= '\u9fff')
                    overlap = q_cn_chars & trans_cn_chars
                    score = len(overlap) * 10
                    q_cn_str = ''.join(c for c in q if '\u4e00' <= c <= '\u9fff')
                    for j in range(len(q_cn_str) - 1):
                        bigram = q_cn_str[j:j+2]
                        if bigram in combined:
                            score += 15
                    if score > best_score:
                        best_score = score
                        best_idx = i
                        self.debug("CharOverlap: '%s' overlap=%d score=%d -> '%s'" % (
                            opt, len(overlap), score, combined[:40]))
                if best_idx >= 0 and best_score >= 5:
                    return best_idx

        # ── Strategy 2.8: Reverse-translation check ──
        if candidates:
            q_for_match = q_clean.replace(' ', '')
            for i, opt in enumerate(options):
                trans_result = self.get_opt_trans(opt)
                combined = trans_result['combined']
                if not combined:
                    continue
                if q_for_match and q_for_match in combined.replace(' ', ''):
                    self.debug("RevTrans: '%s' trans contains '%s'" % (opt, q_for_match))
                    return i
                opt_cn_chars = set(c for c in combined if '\u4e00' <= c <= '\u9fff')
                if any('\u4e00' <= c <= '\u9fff' for c in q):
                    q_cn_chars = set(c for c in q if '\u4e00' <= c <= '\u9fff')
                    overlap = q_cn_chars & opt_cn_chars
                    if len(overlap) >= 1 and len(q_cn_chars) <= 3:
                        self.debug("RevTrans: '%s' char-overlap=%d shared=%s" % (
                            opt, len(overlap), ''.join(overlap)))
                        return i

        # ── Strategy 3: Loose 2-char substring ──
        for i, opt in enumerate(options):
            stems = self.get_stems(opt)
            for s in stems:
                if s in self.word_trans:
                    trans = self.word_trans[s]
                    for term in q_terms:
                        if len(term) >= 2 and term in trans:
                            return i

        return -1

    # ── CDP (PK-specific) ───────────────────────────────────

    def get_pk_state(self):
        js = r'''(function(){
        var d=document,t=d.querySelector('.question-title'),
            items=d.querySelectorAll('.question-items-item'),
            timer=d.querySelector('.intro-center-time'),
            prog=d.querySelector('.intro-center-progress'),
            r={title:t?t.innerText.trim():'',options:[],timer:timer?parseInt(timer.innerText)||0:-1,
               progress:prog?prog.innerText.trim():'',hasQuestion:!!t&&items.length>=2};
        items.forEach(function(item){
            var c=item.querySelector('.select-item-content');
            r.options.push(c?c.innerText.trim():item.innerText.trim().split('\n')[0]);
        });
        return JSON.stringify(r);
        })()'''
        result = self.eval_js(js)
        try:
            return json.loads(result) if result else {}
        except:
            return {}

    def click_option(self, index):
        js = '''(function(){
        var items=document.querySelectorAll('.question-items-item');
        if(%d>=items.length) return JSON.stringify({e:'oor'});
        items[%d].click();
        return JSON.stringify({ok:true,i:%d});
        })()''' % (index, index, index)
        result = self.eval_js(js)
        try:
            return json.loads(result or "{}")
        except:
            return {"error": str(result)}

    # ── Self-Learning ─────────────────────────────────────────

    def capture_wrong_answer(self, clicked_idx):
        """After clicking an option, check if it was wrong and capture the correct answer"""
        js = r'''(function(){
        var items = document.querySelectorAll('.question-items-item');
        var clicked = items[%d];
        var clickedCls = clicked ? (clicked.className || '') : '';
        var isWrong = clickedCls.indexOf('wrong') >= 0 || clickedCls.indexOf('error') >= 0;
        var correctOpt = '';
        for (var i = 0; i < items.length; i++) {
            var cls = items[i].className || '';
            var html = items[i].innerHTML || '';
            if (i !== %d && (cls.indexOf('correct') >= 0 || cls.indexOf('right') >= 0 ||
                cls.indexOf('success') >= 0 || (html.indexOf('svg-icon') >= 0 && cls.indexOf('correct') >= 0))) {
                var c = items[i].querySelector('.select-item-content');
                correctOpt = c ? c.innerText.trim() : items[i].innerText.trim().split('\n')[0];
            }
        }
        return JSON.stringify({isWrong: isWrong, correctAnswer: correctOpt});
        })()''' % (clicked_idx, clicked_idx)
        result = self.eval_js(js)
        try:
            info = json.loads(result) if result else {}
            if info.get('isWrong') and info.get('correctAnswer'):
                return info['correctAnswer']
        except:
            pass
        return ''

    def learn_miss(self, question, correct_answer):
        """Record a learned mapping: question -> correct_answer"""
        q = question.strip()
        if not q or not correct_answer:
            return
        if q not in self.pk_extra or self.pk_extra[q] != correct_answer:
            self.pk_extra[q] = correct_answer
            self.trans_index.setdefault(q, []).insert(0, correct_answer)
            q_clean = re.sub(r'^([a-z]+\.\s*(,\s*[a-z]+\.\s*)*)', '', q).strip()
            if q_clean != q:
                self.trans_index.setdefault(q_clean, []).insert(0, correct_answer)
            with open(self.extra_path, 'w', encoding='utf-8') as f:
                json.dump(self.pk_extra, f, ensure_ascii=False, indent=2)
            print("  [LEARN] '%s' -> %s" % (q, correct_answer))

    def record_miss(self, question, options):
        """Record a miss for later manual review"""
        misses = []
        if os.path.exists(self.misses_path):
            try:
                with open(self.misses_path, 'r', encoding='utf-8') as f:
                    misses = json.load(f)
            except:
                misses = []
        misses.append({
            'question': question.strip(),
            'options': options,
            'time': time.strftime('%Y-%m-%d %H:%M:%S')
        })
        with open(self.misses_path, 'w', encoding='utf-8') as f:
            json.dump(misses, f, ensure_ascii=False, indent=2)

    # ── Main Loop ───────────────────────────────────────────

    def run(self, max_q=999):
        print("\nETS Word PK Auto v5 (Derivatives + Phrases)")
        print("=" * 45)
        self.connect()
        if not self.load_dictionary():
            return
        print("-" * 45)

        answered = 0
        no_match = 0
        last_progress = ''
        last_title = ''
        same_count = 0
        no_q_count = 0

        while answered + no_match < max_q:
            state = self.get_pk_state()

            if not state.get('hasQuestion'):
                no_q_count += 1
                if no_q_count >= 20:
                    print("\nPK ended (no more questions).")
                    break
                time.sleep(0.4)
                continue
            no_q_count = 0

            title = state.get('title', '')
            options = [opt for opt in state.get('options', []) if opt]
            progress = state.get('progress', '')

            if progress:
                last_progress = progress
            elif title == '' and not progress:
                time.sleep(0.3)
                continue

            if same_count > 0 and title == last_title and title != '':
                same_count += 1
                if same_count >= 5:
                    print("  (same question, moving on)")
                    no_match += 1
                    same_count = 0
                    last_title = ''
                    time.sleep(0.5)
                else:
                    time.sleep(0.3)
                continue

            same_count = 1
            last_title = title
            if not title or len(options) < 2:
                time.sleep(0.3)
                continue

            idx = self.find_answer(title, options)
            n = answered + no_match + 1

            if idx >= 0:
                source = 'learned' if (title in self.pk_extra and options[idx].strip() == self.pk_extra[title].strip()) else 'dict'
                print("  #%s -> %s [%s]" % (progress or n, options[idx], source))
                r = self.click_option(idx)
                if r.get('ok'):
                    answered += 1
                    if source == 'learned':
                        self.stats['learned'] += 1
                else:
                    self.stats['errors'] += 1
                # Check if answer was wrong → try to capture correct answer
                time.sleep(0.5)
                correct = self.capture_wrong_answer(idx)
                if correct:
                    print("  [LEARN] '%s' -> %s" % (title, correct))
                    self.learn_miss(title, correct)
            else:
                print("  #%s -> ??? [%s]" % (progress or n, ' / '.join(options)))
                no_match += 1
                self.record_miss(title, options)

            time.sleep(0.8)

        total = answered + no_match
        rate = (answered * 100 / total) if total > 0 else 0
        print("\n" + "=" * 45)
        print("Done: %d hit / %d total = %.0f%% | %d miss | %d err | %d learned" % (
            answered, total, rate, no_match, self.stats['errors'], self.stats['learned']))
        if self.stats['learned'] > 0:
            print("Self-learned hits: %d" % self.stats['learned'])


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="ETS Word PK Auto v5")
    p.add_argument("--max", type=int, default=999, help="Max questions")
    p.add_argument("--debug", action="store_true", help="Show debug info")
    p.add_argument("--port", type=int, default=10086, help="CDP port")
    a = p.parse_args()
    ETSWordPK(port=a.port, debug_mode=a.debug).run(max_q=a.max)
