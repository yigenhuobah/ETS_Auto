#!/usr/bin/env python3
"""
ETS Word PK Auto Answer v5 — e听说单词PK自动答题
改进: 派生词自动生成 + 短语提取 + 选项反查(含词根回退)
"""
import json, os, time, re, sys
from ets_common import ETSBase


def _edit_dist(a, b):
    """Levenshtein distance — lightweight tie-breaker (no numpy needed)."""
    la, lb = len(a), len(b)
    if la == 0: return lb
    if lb == 0: return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        curr = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i-1] == b[j-1] else 1
            curr[j] = min(curr[j-1] + 1, prev[j] + 1, prev[j-1] + cost)
        prev = curr
    return prev[lb]


def _same_script(a, b):
    """Check if both strings share at least one script (CJK, Latin, etc).
    Cross-script edit distance is meaningless (Chinese vs English),
    so we use this to gate tie-breaking."""
    def _has_cjk(s):
        return any('\u4e00' <= c <= '\u9fff' for c in s)
    def _has_latin(s):
        return any('a' <= c.lower() <= 'z' for c in s)
    a_cjk, b_cjk = _has_cjk(a), _has_cjk(b)
    a_lat, b_lat = _has_latin(a), _has_latin(b)
    return (a_cjk and b_cjk) or (a_lat and b_lat)


def _tie_breaker(a, b, reference):
    """Prefer the string closer to reference by edit distance.
    Falls back to length similarity when scripts don't match."""
    if _same_script(a, reference) and _same_script(b, reference):
        return _edit_dist(a, reference) < _edit_dist(b, reference)
    # Cross-script: prefer shorter edit distance on shared characters
    # or just length similarity as a weak signal
    return abs(len(a) - len(reference)) < abs(len(b) - len(reference))


def _resource_path(filename):
    """Resolve bundled resource path — works both in dev and PyInstaller --onefile.

    Read-only data files (ecdict_pk.json) are bundled inside the exe via
    sys._MEIPASS.  User-writable files (pk_extra.json, pk_misses.jsonl)
    must live NEXT TO the exe, so they are NOT resolved through _MEIPASS.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, filename)
    # Dev mode: project root = 3 levels up from src/auto/ets_word_pk.py
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), filename)


def _exe_dir_path(filename):
    """Resolve user-writable file path next to the executable (or script dir in dev).

    pk_extra.json and pk_misses.jsonl must be writable and persist across runs,
    so they go next to the exe, NOT inside the PyInstaller bundle.
    """
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), filename)
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), filename)


class ETSWordPK(ETSBase):
    def __init__(self, port=10086, debug_mode=False):
        super().__init__(port=port, debug_mode=debug_mode)
        self.ets_base = os.path.join(os.path.expandvars(r'%APPDATA%'), 'ETS')
        self.dict_path = os.path.join(self.ets_base, 'pc_xst_dict', 'pc_xst_dict.json')
        # Read-only: bundled inside exe via --add-data (PyInstaller _MEIPASS)
        self.ecdict_path = _resource_path('ecdict_pk.json')
        # User-writable: must live next to the exe, not inside the bundle
        self.extra_path = _exe_dir_path('pk_extra.json')
        self.misses_path = _exe_dir_path('pk_misses.jsonl')
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
                phrases = re.findall(r'([a-z][a-z\-]*(?:\s[a-z][a-z\-]*)+)', line)
                for ph in phrases:
                    ph = ph.strip()
                    phl = ph.lower()
                    # Skip noise: grammar abbreviations, single-letter fragments, sth/sb/etc
                    # Use startswith to also catch possessive forms: sb's, sth's, etc.
                    _skip = False
                    for _tok in phl.split():
                        if _tok in ('etc', 'ie', 'eg', 'vs', 'cf', 'al'):
                            _skip = True
                            break
                        if _tok.startswith('sb') or _tok.startswith('sth'):
                            _skip = True
                            break
                        # Allow single-letter words like 'a' in 'deal with a problem'
                        if _tok and not _tok[0].isalpha():
                            _skip = True
                            break
                    if _skip:
                        continue
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
                # Also add to word_trans so total count is correct
                self.word_trans.setdefault(q_text, answer)
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
        
        # British → American spelling conversion (longest suffix first!)
        for brit, us in [('isation', 'ization'), ('ising', 'izing'), ('ogue', 'og'),
                         ('ence', 'ense'), ('ise', 'ize'), ('yse', 'yze'),
                         ('our', 'or'), ('re', 'er')]:
            if w.endswith(brit) and len(w) > len(brit) + 1:
                candidates.append(w[:-len(brit)] + us)
        if w.endswith('ly') and len(w) > 4:
            candidates.append(w[:-2])           # mentally → mental
            if w[:-2].endswith('al'):            # musically → music → musical → music
                candidates.append(w[:-4])  # strip 'ally' to get base
            if w[:-2].endswith('ic'):            # artistically → artistic → artist
                candidates.append(w[:-4])  # strip 'ically' to get base
            if w[:-2].endswith('le'):            # gently → gentle
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
            if w.endswith('ies'):
                candidates.append(w[:-3] + 'y')  # studies → study
        if w.endswith('s') and len(w) > 3 and not w.endswith('ss'):
            candidates.append(w[:-1])            # cats → cat
        if w.endswith('er') and len(w) > 4:
            candidates.append(w[:-2])            # faster → fast
        if w.endswith('est') and len(w) > 5:
            candidates.append(w[:-3])            # fastest → fast
        return list(set(c for c in candidates if c))

    def get_opt_trans(self, opt):
        """Get translations for an option, including sub-phrase extraction.
        Finds ALL non-overlapping matching sub-phrases (longest first),
        not just the first match — so "deal with a problem" can match
        both "deal with" and "problem" instead of losing the tail."""
        stems = self.get_stems(opt)
        word_trans_parts = []
        for s in stems:
            if s in self.word_trans:
                word_trans_parts.append(self.word_trans[s])

        phrase_trans = []
        if ' ' in opt:
            words = opt.split()
            matched = set()  # indices already covered by a longer match
            for n in range(len(words), 0, -1):  # include single words (n=1)
                for start in range(len(words) - n + 1):
                    # Skip if any word in this range is already matched
                    if any(i in matched for i in range(start, start + n)):
                        continue
                    sub = ' '.join(words[start:start+n])
                    if sub in self.word_trans:
                        phrase_trans.append(self.word_trans[sub])
                        for i in range(start, start + n):
                            matched.add(i)
        elif opt in self.word_trans:
            # Single word option — direct lookup
            phrase_trans.append(self.word_trans[opt])

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

        # ── Strategy 0: Self-learned exact + fuzzy match ──
        if q in self.pk_extra:
            answer = self.pk_extra[q]
            ans_s = answer.strip().lower()
            # Exact match
            for i, opt in enumerate(options):
                if opt.strip().lower() == ans_s:
                    self.debug("Learned: '%s' -> %s" % (q, answer))
                    return i
            # Fuzzy: answer contained in option or vice versa
            for i, opt in enumerate(options):
                opt_s = opt.strip().lower()
                if opt_s and ans_s and (opt_s in ans_s or ans_s in opt_s):
                    self.debug("Learned(fuzzy): '%s' -> %s ~ %s" % (q, answer, opt))
                    return i
        # Reverse lookup: Chinese question not in pk_extra but matches a pk_extra key substring
        if self._is_chinese(q):
            q_clean2 = re.sub(r'[^\u4e00-\u9fff]', '', q)
            if len(q_clean2) >= 2:
                best_idx = -1
                best_score = 0
                best_answer = ''
                for pk_q, pk_a in self.pk_extra.items():
                    pk_q_clean = re.sub(r'[^\u4e00-\u9fff]', '', pk_q)
                    if not pk_q_clean:
                        continue
                    # Overlap scoring
                    overlap = 0
                    for j in range(len(q_clean2) - 1):
                        if q_clean2[j:j+2] in pk_q_clean:
                            overlap += 1
                    for j in range(len(pk_q_clean) - 1):
                        if pk_q_clean[j:j+2] in q_clean2:
                            overlap += 1
                    if overlap > best_score:
                        # Check if pk_a matches any option
                        pk_a_s = pk_a.strip().lower()
                        for i, opt in enumerate(options):
                            opt_s = opt.strip().lower()
                            if opt_s == pk_a_s or (opt_s and pk_a_s and (opt_s in pk_a_s or pk_a_s in opt_s)):
                                best_score = overlap
                                best_idx = i
                                best_answer = pk_a
                                break
                if best_idx >= 0 and best_score >= 2:
                    self.debug("Learned(reverse): '%s' ~ '%s' -> %s" % (q, best_answer, options[best_idx]))
                    return best_idx

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
                best_clean = ''
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
                    if score > best_score or (score == best_score and score > 0 and
                            _tie_breaker(opt_clean, best_clean, q_clean)):
                        best_score = score
                        best_idx = i
                        best_clean = opt_clean
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
                best_combined = ''
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
                    if score > best_score or (score == best_score and score > 0 and
                            _tie_breaker(combined, best_combined, q)):
                        best_score = score
                        best_idx = i
                        best_combined = combined
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
            var text=c?c.innerText.trim():item.innerText.trim();
            /* Extract last non-empty line — skip prefixes like "A.", icons, etc. */
            var lines=text.split(/\n/).map(function(l){return l.trim();}).filter(function(l){return l;});
            var picked=lines.length?lines[lines.length-1]:text;
            /* Strip leading ordinal prefixes: "A.", "A)", "1.", bullets */
            picked=picked.replace(/^[A-Z][.)]\s*/, '').replace(/^\d+[.)]\s*/, '').trim();
            r.options.push(picked||text);
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

    def capture_wrong_answer(self, clicked_idx, current_options=None):
        """After clicking an option, check if it was wrong and capture the correct answer.
        current_options: list of current option texts for validation (Bug 18: prevents
        learning stale data from a page transition)."""
        js = r'''(function(){
        var items = document.querySelectorAll('.question-items-item');
        var clicked = items[%d];
        var clickedCls = clicked ? (clicked.className || '') : '';
        var isWrong = clickedCls.indexOf('wrong') >= 0 || clickedCls.indexOf('error') >= 0;
        var correctOpt = '';
        var allOpts = [];
        for (var i = 0; i < items.length; i++) {
            var cls = items[i].className || '';
            var html = items[i].innerHTML || '';
            var c = items[i].querySelector('.select-item-content');
            var text = c ? c.innerText.trim() : items[i].innerText.trim();
            allOpts.push(text);
            if (i !== %d && (cls.indexOf('correct') >= 0 || cls.indexOf('right') >= 0 ||
                cls.indexOf('success') >= 0 || (html.indexOf('svg-icon') >= 0 && cls.indexOf('correct') >= 0))) {
                var lines = text.split(/\n/).map(function(l){return l.trim();}).filter(function(l){return l;});
                correctOpt = lines.length ? lines[lines.length-1] : text;
                correctOpt = correctOpt.replace(/^[A-Z][.)]\s*/, '').replace(/^\d+[.)]\s*/, '').trim() || text;
            }
        }
        return JSON.stringify({isWrong: isWrong, correctAnswer: correctOpt, allOpts: allOpts});
        })()''' % (clicked_idx, clicked_idx)
        result = self.eval_js(js)
        try:
            info = json.loads(result) if result else {}
            if info.get('isWrong') and info.get('correctAnswer'):
                captured = info['correctAnswer']
                # Bug 18: Validate captured answer is among current options
                # If page transitioned, the DOM options won't match our known options
                if current_options is not None:
                    dom_opts = info.get('allOpts', [])
                    # Check if DOM options match what we expected
                    if len(dom_opts) != len(current_options):
                        self.debug("capture_wrong_answer: DOM option count mismatch (%d vs %d), skipping" % (len(dom_opts), len(current_options)))
                        return ''
                    # Content validation: prevent stale-page pollution when option count matches
                    # (e.g. both old and new questions have 4 options)
                    expected_texts = set(o.strip().lower() for o in current_options)
                    actual_texts = set(o.strip().lower() for o in dom_opts)
                    if expected_texts != actual_texts:
                        self.debug("capture_wrong_answer: DOM option content mismatch, page likely transitioned, skipping")
                        return ''
                return captured
        except:
            pass
        return ''

    @staticmethod
    def _is_chinese(text):
        """Check if text contains CJK characters (Chinese/Japanese/Korean)"""
        for ch in text:
            if '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf':
                return True
        return False

    def learn_miss(self, question, correct_answer):
        """Record a learned mapping: question -> correct_answer
        Also updates trans_index in the correct direction (cn -> [en_words])."""
        q = question.strip()
        if not q or not correct_answer:
            return
        if q not in self.pk_extra or self.pk_extra[q] != correct_answer:
            self.pk_extra[q] = correct_answer
            # trans_index direction: chinese_key -> [english_words]
            # Determine which is Chinese and insert in the correct direction
            if self._is_chinese(q):
                # q=Chinese, correct_answer=English → correct direction
                self.trans_index.setdefault(q, []).insert(0, correct_answer)
                q_clean = re.sub(r'^([a-z]+\.\s*(,\s*[a-z]+\.\s*)*)', '', q).strip()
                if q_clean != q:
                    self.trans_index.setdefault(q_clean, []).insert(0, correct_answer)
            else:
                # q=English, correct_answer=Chinese → reverse: cn_key -> [en_word]
                self.trans_index.setdefault(correct_answer, []).insert(0, q)
                cn_clean = re.sub(r'^([a-z]+\.\s*(,\s*[a-z]+\.\s*)*)', '', correct_answer).strip()
                if cn_clean != correct_answer:
                    self.trans_index.setdefault(cn_clean, []).insert(0, q)
            # Atomic write: temp file + os.replace() — never leaves a 0-byte file on crash
            import tempfile
            dir_name = os.path.dirname(self.extra_path) or '.'
            tmp_path = ''
            try:
                fd, tmp_path = tempfile.mkstemp(suffix='.json', dir=dir_name)
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(self.pk_extra, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self.extra_path)
                tmp_path = ''  # success — nothing to clean up
            except Exception:
                if tmp_path:
                    try: os.unlink(tmp_path)
                    except Exception: pass
                raise
            print("  [LEARN] '%s' -> %s" % (q, correct_answer))

    def record_miss(self, question, options):
        """Record a miss for later manual review (JSONL append — O(1) disk I/O)"""
        record = {
            'question': question.strip(),
            'options': options,
            'time': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        try:
            with open(self.misses_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        except Exception as e:
            self.debug("record_miss write error: %s" % e)

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

        try:
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

                if same_count != 0 and title == last_title and title != '':
                    # same_count > 0: counting repeats; same_count < 0: cooling down
                    same_count += 1
                    if same_count >= 5:
                        print("  (same question, moving on)")
                        # Don't add no_match here — already counted when first encountered
                        same_count = -8  # cooldown: skip 8 cycles then re-check
                        time.sleep(0.5)
                    elif same_count > 0:
                        time.sleep(min(0.3 * (2 ** (same_count - 1)), 2.0))  # exponential backoff
                    else:
                        same_count += 1  # count towards zero
                        if same_count >= 0:
                            same_count = 0  # cooldown done, reset for re-check
                            last_title = ''  # force re-evaluate next cycle
                        time.sleep(0.4)
                    continue

                # New or different question — reset tracker
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
                    correct = self.capture_wrong_answer(idx, current_options=options)
                    if correct:
                        self.learn_miss(title, correct)
                else:
                    print("  #%s -> ??? [%s]" % (progress or n, ' / '.join(options)))
                    no_match += 1
                    self.record_miss(title, options)

                time.sleep(0.8)

        except (ConnectionError, TimeoutError) as e:
            print("\nConnection lost: %s" % e)

        total = answered + no_match
        rate = (answered * 100 / total) if total > 0 else 0
        print("\n" + "=" * 45)
        print("Done: %d hit / %d total = %.0f%% | %d miss | %d err | %d learned" % (
            answered, total, rate, no_match, self.stats['errors'], self.stats['learned']))
        if self.stats['learned'] > 0:
            print("Self-learned hits: %d" % self.stats['learned'])

        # Cleanup WebSocket
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass


if __name__ == "__main__":
    # Force UTF-8 on Windows terminals (GBK can't encode IPA/special chars)
    if sys.platform == 'win32':
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, LookupError):
            pass

    import argparse
    p = argparse.ArgumentParser(description="ETS Word PK Auto v5")
    p.add_argument("--max", type=int, default=999, help="Max questions")
    p.add_argument("--debug", action="store_true", help="Show debug info")
    p.add_argument("--port", type=int, default=10086, help="CDP port")
    a = p.parse_args()
    ETSWordPK(port=a.port, debug_mode=a.debug).run(max_q=a.max)
