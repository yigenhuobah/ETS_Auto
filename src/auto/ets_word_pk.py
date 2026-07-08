#!/usr/bin/env python3
"""
ETS Word PK Auto Answer v5 — e听说单词PK自动答题
改进: 派生词自动生成 + 短语提取 + 选项反查(含词根回退)
"""
import json, os, time, re, sys
from urllib.error import URLError
from ets_common import ETSBase, force_utf8_stdio
from ets_hotkey import ETSHotkey

# Version constant — keep in sync with ets_gui.py APP_VERSION
__version__ = "0.6.4"


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
    def __init__(self, port=10086, debug_mode=False, stop_event=None):
        super().__init__(port=port, debug_mode=debug_mode, stop_event=stop_event)
        self.ets_base = os.path.join(os.path.expandvars(r'%APPDATA%'), 'ETS')
        # ETS client changed dict location/format around 2026-06:
        #   Old: pc_xst_dict/pc_xst_dict.json  (pure JSON [{Word, Trans}])
        #   New: common/material/word/worddict_data.json  (JS var + Base64 trans)
        # Try new path first, fall back to old.
        self.dict_path_new = os.path.join(self.ets_base, 'common', 'material', 'word', 'worddict_data.json')
        self.dict_path = os.path.join(self.ets_base, 'pc_xst_dict', 'pc_xst_dict.json')
        if os.path.exists(self.dict_path_new):
            self.dict_path = self.dict_path_new
        # Read-only: bundled inside exe via --add-data (PyInstaller _MEIPASS)
        self.ecdict_path = _resource_path('ecdict_pk.json')
        # User-writable: must live next to the exe, not inside the bundle
        self.extra_path = _exe_dir_path('pk_extra.json')
        self.misses_path = _exe_dir_path('pk_misses.jsonl')
        self.word_trans = {}      # word.lower() -> full_trans
        self.trans_index = {}     # chinese_segment -> [word1, word2, ...]
        self.cn_seg_index = {}    # chinese_sub_phrase -> [word1, word2, ...] (finer-grained)
        self.pk_extra = {}        # question_text -> correct_option (self-learned)
        self.stats = {'answered': 0, 'no_match': 0, 'errors': 0, 'learned': 0}

    # ── Dictionary Loading ──────────────────────────────────

    def _load_dict_new_format(self, path):
        """Load new ETS dict format: JS variable assignment with Base64-encoded trans.

        File format:  wordsTranslateArr = `[{word: {word, trans(b64), ...}}, ...]`
        Each entry is a single-key dict: {the_word: {word, trans, url_us, ...}}
        """
        import base64 as _b64
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        m = re.search(r'`\[.*\]`', content, re.DOTALL)
        if not m:
            raise ValueError("Cannot extract JSON from new-format dict file")
        json_str = m.group(0)[1:-1]  # strip leading/trailing backticks
        arr = json.loads(json_str)
        for entry in arr:
            if not isinstance(entry, dict) or not entry:
                continue
            # Each entry: {word: {word, trans(b64), ...}}
            key = next(iter(entry))
            val = entry[key]
            if not isinstance(val, dict):
                continue
            word = (val.get('word') or key).strip()
            trans_b64 = val.get('trans', '').strip()
            if not word or not trans_b64:
                continue
            try:
                trans = _b64.b64decode(trans_b64).decode('utf-8').strip()
            except Exception:
                continue
            if not trans:
                continue
            self.word_trans[word.lower()] = trans
            for line in trans.split('\n'):
                line = line.strip()
                if not line:
                    continue
                cn = re.sub(r'^([a-z]+\.\s*(,\s*[a-z]+\.\s*)*)', '', line).strip()
                if cn:
                    self.trans_index.setdefault(cn, []).append(word)
        return len(arr)

    def load_dictionary(self):
        if not os.path.exists(self.dict_path):
            print("ERROR: Dictionary not found: " + self.dict_path)
            return False
        t0 = time.time()
        is_new_format = 'worddict_data' in os.path.basename(self.dict_path)
        if is_new_format:
            count = self._load_dict_new_format(self.dict_path)
            base_count = len(self.word_trans)
            print("  New-format dict loaded: %d entries" % count)
        else:
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

        # ── Build cn_seg_index (sub-phrase index for Chinese→English lookup) ──
        seg_count = self._build_cn_seg_index()
        print("  CN sub-phrase index: %d segments" % seg_count)

        return True

    def _cn_split(self, cn_text):
        """Split a Chinese translation line into meaningful sub-phrases.
        Handles formats like: '分析，剖析', '在押/入狱', '彻底的;完全的', etc."""
        segs = []
        # Split on Chinese/English punctuation and slashes
        parts = re.split(r'[，,、；;/／]', cn_text)
        for p in parts:
            p = p.strip().strip('〔【(（·〕】)）').strip()
            # Skip noise: too short, pure digits, pure English, HTML fragments
            if len(p) < 2:
                continue
            if re.match(r'^[0-9.]+$', p):
                continue
            if re.match(r'^[a-zA-Z.\-/]+$', p):
                continue
            # Must contain at least one CJK character
            if not any('\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf' for c in p):
                continue
            segs.append(p)
        return segs

    def _build_cn_seg_index(self):
        """Build a finer-grained Chinese→English index by splitting translation lines
        into sub-phrases. This enables matching '监禁' → 'behind bars' even when the
        trans_index key is '在押/入狱'."""
        count = 0
        seen = set()  # avoid duplicate entries
        MAX_PER_KEY = 10  # limit candidates per sub-phrase to control memory
        for word, trans in self.word_trans.items():
            wl = word  # word_trans keys are already lowercased for single words
            # Handle Chinese→English entries from pk_extra (word is Chinese, trans is English)
            word_is_cn = any('\u4e00' <= c <= '\u9fff' for c in word)
            if word_is_cn:
                # word=Chinese key, trans=English answer
                # Split the Chinese key into sub-phrases and map each to the English word
                segs = self._cn_split(word)
                for seg in segs:
                    key = (seg, trans)
                    if key in seen:
                        continue
                    seen.add(key)
                    lst = self.cn_seg_index.setdefault(seg, [])
                    if len(lst) < MAX_PER_KEY:
                        lst.append(trans)
                        count += 1
                continue
            for line in trans.split('\n'):
                line = line.strip()
                if not line:
                    continue
                cn = re.sub(r'^([a-z]+\.\s*(,\s*[a-z]+\.\s*)*)', '', line).strip()
                if not cn:
                    continue
                segs = self._cn_split(cn)
                for seg in segs:
                    # Skip if seg == cn (already in trans_index)
                    if seg == cn:
                        continue
                    key = (seg, wl)
                    if key in seen:
                        continue
                    seen.add(key)
                    lst = self.cn_seg_index.setdefault(seg, [])
                    if len(lst) < MAX_PER_KEY:
                        lst.append(word)
                        count += 1
        return count


    # ── Matching ────────────────────────────────────────────

    def get_stems(self, word):
        """Try to strip suffixes to find base form in dictionary.
        Order: stem first, then British→American conversion on each candidate.
        This avoids 'organising' → 'organizing' → strip 'ing' → 'organiz' (wrong)
        instead: 'organising' → strip 'ing' → 'organis' → Brit→US → 'organiz' → 'organize'."""
        w = word.lower().strip()
        candidates = [w]

        # ── Step 1: Suffix stripping (on original form) ──
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

        # ── Step 2: British → American conversion (applied to ALL candidates) ──
        # After stemming, so 'organising' → stem → 'organis' → Brit→US → 'organize'
        expanded = []
        brit_us = [('isation', 'ization'), ('ising', 'izing'), ('ogue', 'og'),
                   ('ence', 'ense'), ('ise', 'ize'), ('yse', 'yze'),
                   ('our', 'or'), ('re', 'er')]
        for c in candidates:
            for brit, us in brit_us:
                if c.endswith(brit) and len(c) > len(brit) + 1:
                    expanded.append(c[:-len(brit)] + us)
        candidates.extend(expanded)

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
        # Reverse lookup A: Chinese question matches pk_extra KEY (cn→en records)
        # Reverse lookup B: Chinese question matches pk_extra VALUE (en→cn records)
        if self._is_chinese(q):
            q_clean2 = re.sub(r'[^\u4e00-\u9fff]', '', q)
            if len(q_clean2) >= 2:
                best_idx = -1
                best_score = 0
                best_answer = ''
                best_source = ''
                for pk_q, pk_a in self.pk_extra.items():
                    # A: match against pk_extra key (e.g. pk_q="在押/入狱", pk_a="behind bars")
                    pk_q_clean = re.sub(r'[^\u4e00-\u9fff]', '', pk_q)
                    # B: match against pk_extra value (e.g. pk_q="behind bars", pk_a="在押/入狱")
                    pk_a_clean = re.sub(r'[^\u4e00-\u9fff]', '', pk_a)
                    for text_clean, match_side in [(pk_q_clean, 'key'), (pk_a_clean, 'val')]:
                        if not text_clean or len(text_clean) < 2:
                            continue
                        overlap = 0
                        for j in range(len(q_clean2) - 1):
                            if q_clean2[j:j+2] in text_clean:
                                overlap += 1
                        for j in range(len(text_clean) - 1):
                            if text_clean[j:j+2] in q_clean2:
                                overlap += 1
                        if overlap > best_score:
                            # key side matched → answer is pk_a; val side matched → answer is pk_q
                            candidate = pk_a if match_side == 'key' else pk_q
                            cand_s = candidate.strip().lower()
                            for i, opt in enumerate(options):
                                opt_s = opt.strip().lower()
                                if opt_s == cand_s or (opt_s and cand_s and (opt_s in cand_s or cand_s in opt_s)):
                                    best_score = overlap
                                    best_idx = i
                                    best_answer = candidate
                                    best_source = match_side
                                    break
                if best_idx >= 0 and best_score >= 2:
                    self.debug("Learned(reverse-%s): '%s' ~ '%s' -> %s" % (best_source, q, best_answer, options[best_idx]))
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

        # ── Strategy 1.5: CN sub-phrase index (Chinese question → English options) ──
        # Handles cases like: question='监禁', trans_index key='在押/入狱',
        # cn_seg_index has '在押'→['behind bars'], '入狱'→['behind bars']
        if self._is_chinese(q):
            seg_candidates = []
            # O(1) exact lookup — no full-table scan
            if q_clean in self.cn_seg_index:
                seg_candidates.extend(self.cn_seg_index[q_clean])
                self.debug("CNSeg(exact): '%s' -> %s" % (q_clean, self.cn_seg_index[q_clean][:5]))
            elif q in self.cn_seg_index:
                seg_candidates.extend(self.cn_seg_index[q])
                self.debug("CNSeg(exact-q): '%s' -> %s" % (q, self.cn_seg_index[q][:5]))
            # Try each question term against sub-phrase index
            if not seg_candidates:
                for term in q_terms:
                    if len(term) >= 2 and term in self.cn_seg_index:
                        seg_candidates.extend(self.cn_seg_index[term])
                        self.debug("CNSeg(term): '%s' -> %s" % (term, self.cn_seg_index[term][:5]))
            # Try substring match: only if still no candidates
            # Uses a bounded scan with early exit
            if not seg_candidates:
                for term in q_terms:
                    if len(term) >= 3:  # require longer term to reduce false positives
                        found = False
                        for seg_key in self.cn_seg_index:
                            if term in seg_key or seg_key in term:
                                seg_candidates.extend(self.cn_seg_index[seg_key])
                                self.debug("CNSeg(sub): '%s' ~ '%s'" % (term, seg_key))
                                found = True
                                break
                        if found:
                            break
            if seg_candidates:
                # Deduplicate while preserving order
                seen = set()
                unique = []
                for w in seg_candidates:
                    wl = w.lower().strip()
                    if wl not in seen:
                        seen.add(wl)
                        unique.append(wl)
                # Match unique candidates against options
                for i, opt in enumerate(options):
                    if opt.lower().strip() in unique:
                        self.debug("CNSeg hit: opt[%d]='%s'" % (i, opt))
                        return i
                # Stem match
                for i, opt in enumerate(options):
                    stems = self.get_stems(opt)
                    for s in stems:
                        if s in unique:
                            self.debug("CNSeg stem: opt[%d]='%s' stem=%s" % (i, opt, s))
                            return i
                # Partial match: option word in candidates
                for i, opt in enumerate(options):
                    opt_words = set(w.lower().strip() for w in re.split(r'[-\s]+', opt) if len(w) >= 3)
                    if opt_words & set(unique):
                        self.debug("CNSeg partial: opt[%d]='%s'" % (i, opt))
                        return i

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
                if best_idx >= 0 and best_score >= 20:
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
                    if len(overlap) >= 2 or (len(overlap) >= 1 and len(q_cn_chars) <= 2):
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
        except Exception:
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
        except Exception:
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
        except Exception:
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
        Also updates trans_index and cn_seg_index in the correct direction."""
        q = question.strip()
        if not q or not correct_answer:
            return
        if q not in self.pk_extra or self.pk_extra[q] != correct_answer:
            self.pk_extra[q] = correct_answer
            # trans_index direction: chinese_key -> [english_words]
            # Determine which is Chinese and insert in the correct direction
            if self._is_chinese(q):
                # q=Chinese, correct_answer=English → correct direction
                _idx_list = self.trans_index.setdefault(q, [])
                if correct_answer not in _idx_list:
                    _idx_list.insert(0, correct_answer)
                # Also update cn_seg_index for sub-phrase matching
                _seg_list = self.cn_seg_index.setdefault(q, [])
                if correct_answer not in _seg_list:
                    _seg_list.insert(0, correct_answer)
                q_clean = re.sub(r'^([a-z]+\.\s*(,\s*[a-z]+\.\s*)*)', '', q).strip()
                if q_clean != q:
                    _idx_list2 = self.trans_index.setdefault(q_clean, [])
                    if correct_answer not in _idx_list2:
                        _idx_list2.insert(0, correct_answer)
                    _seg_list2 = self.cn_seg_index.setdefault(q_clean, [])
                    if correct_answer not in _seg_list2:
                        _seg_list2.insert(0, correct_answer)
                # Also split q_clean into sub-phrases and add to cn_seg_index
                for seg in self._cn_split(q_clean):
                    if seg not in self.cn_seg_index or correct_answer not in self.cn_seg_index[seg]:
                        self.cn_seg_index.setdefault(seg, []).insert(0, correct_answer)
            else:
                # q=English, correct_answer=Chinese → reverse: cn_key -> [en_word]
                _idx_list = self.trans_index.setdefault(correct_answer, [])
                if q not in _idx_list:
                    _idx_list.insert(0, q)
                _seg_list = self.cn_seg_index.setdefault(correct_answer, [])
                if q not in _seg_list:
                    _seg_list.insert(0, q)
                cn_clean = re.sub(r'^([a-z]+\.\s*(,\s*[a-z]+\.\s*)*)', '', correct_answer).strip()
                if cn_clean != correct_answer:
                    _idx_list2 = self.trans_index.setdefault(cn_clean, [])
                    if q not in _idx_list2:
                        _idx_list2.insert(0, q)
                    _seg_list2 = self.cn_seg_index.setdefault(cn_clean, [])
                    if q not in _seg_list2:
                        _seg_list2.insert(0, q)
                # Split cn_clean into sub-phrases
                for seg in self._cn_split(cn_clean):
                    if seg not in self.cn_seg_index or q not in self.cn_seg_index[seg]:
                        self.cn_seg_index.setdefault(seg, []).insert(0, q)
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
        print("ETS Word PK Auto v5 (Derivatives + Phrases)")
        print("=" * 45)
        try:
            self.connect()
        except URLError as e:
            print("\n❌ 连接失败: %s" % e)
            print("诊断：")
            print("  1. e听说PC端是否已启动？")
            print("  2. 调试端口 %d 是否正确？" % self.port)
            return
        except ConnectionRefusedError:
            print("\n❌ 连接被拒绝 (端口 %d)" % self.port)
            print("诊断：e听说PC端可能未启动，或端口不匹配")
            return
        except Exception as e:
            print("\n❌ 连接失败: %s" % e)
            print("诊断：请确认 e听说PC端已启动且调试端口 %d 正确" % self.port)
            return
        if not self.load_dictionary():
            return

        # Register global hotkeys (F9=Pause, F10=Skip, F12=Emergency Stop)
        self._hotkey = ETSHotkey()
        self._hotkey.register()

        print("-" * 45)

        answered = 0
        no_match = 0
        last_progress = ''
        last_question_hash = ''
        same_count = 0
        no_q_count = 0

        try:
            while answered + no_match < max_q:
                # Check stop signal (hotkey F12 or external stop_event)
                if (self.stop_event and self.stop_event.is_set()) or (self._hotkey and self._hotkey.should_stop):
                    print("\n  🛑 Stopped by user")
                    break

                # Check pause (F9)
                if self._hotkey and self._hotkey.is_paused:
                    self.interruptible_sleep(0.3)
                    continue

                state = self.get_pk_state()

                if not state.get('hasQuestion'):
                    no_q_count += 1
                    if no_q_count >= 20:
                        print("\nPK ended (no more questions).")
                        break
                    self.interruptible_sleep(0.4)
                    continue
                no_q_count = 0

                title = state.get('title', '')
                options = [opt for opt in state.get('options', []) if opt]
                progress = state.get('progress', '')

                # Compound question hash: title + sorted options
                # Prevents false "same question" detection when different questions share same title
                import hashlib as _hl
                question_hash = _hl.md5((title + '|' + '|'.join(sorted(options))).encode()).hexdigest()[:12]

                if progress:
                    last_progress = progress
                elif title == '' and not progress:
                    self.interruptible_sleep(0.3)
                    continue

                if same_count != 0 and question_hash == last_question_hash and title != '':
                    # same_count > 0: counting repeats; same_count < 0: cooling down
                    same_count += 1
                    if same_count >= 5:
                        print("  (same question, moving on)")
                        # Don't add no_match here — already counted when first encountered
                        same_count = -8  # cooldown: skip 8 cycles then re-check
                        self.interruptible_sleep(0.5)
                    elif same_count > 0:
                        self.interruptible_sleep(min(0.3 * (2 ** (same_count - 1)), 2.0))  # exponential backoff
                    else:
                        same_count += 1  # count towards zero
                        if same_count >= 0:
                            same_count = 0  # cooldown done, reset for re-check
                            last_question_hash = ''  # force re-evaluate next cycle
                        self.interruptible_sleep(0.4)
                    continue

                # New or different question — reset tracker
                same_count = 1
                last_question_hash = question_hash
                if not title or len(options) < 2:
                    self.interruptible_sleep(0.3)
                    continue

                idx = self.find_answer(title, options)
                n = answered + no_match + 1

                if idx >= 0:
                    source = 'learned' if (title in self.pk_extra and options[idx].strip() == self.pk_extra[title].strip()) else 'dict'
                    print("  #%s -> %s [%s]" % (progress or n, options[idx], source))
                    self._fire_question({'type': 'pk', 'type_label': '单词PK',
                                         'index': n, 'answer': options[idx],
                                         'source': source, 'title': title})
                    r = self.click_option(idx)
                    if r.get('ok'):
                        answered += 1
                        if source == 'learned':
                            self.stats['learned'] += 1
                    else:
                        self.stats['errors'] += 1
                    # Check if answer was wrong → try to capture correct answer
                    self.interruptible_sleep(0.5)
                    correct = self.capture_wrong_answer(idx, current_options=options)
                    if correct:
                        self.learn_miss(title, correct)
                else:
                    print("  #%s -> ??? [%s]" % (progress or n, ' / '.join(options)))
                    self._fire_question({'type': 'pk', 'type_label': '单词PK',
                                         'index': n, 'answer': None,
                                         'source': 'miss', 'title': title})
                    no_match += 1
                    self.record_miss(title, options)

                self.interruptible_sleep(0.8)

        except (ConnectionError, TimeoutError) as e:
            print("\nConnection lost: %s" % e)

        # Cleanup hotkey
        if hasattr(self, '_hotkey') and self._hotkey:
            self._hotkey.unregister()

        total = answered + no_match
        rate = (answered * 100 / total) if total > 0 else 0
        print("\n" + "=" * 45)
        print("Done: %d hit / %d total = %.0f%% | %d miss | %d err | %d learned" % (
            answered, total, rate, no_match, self.stats['errors'], self.stats['learned']))
        if self.stats['learned'] > 0:
            print("Self-learned hits: %d" % self.stats['learned'])

        # Fire on_complete callback
        self._fire_complete({'answered': answered, 'total': total,
                             'rate': rate, 'miss': no_match,
                             'errors': self.stats.get('errors', 0),
                             'learned': self.stats.get('learned', 0)})

        # Cleanup WebSocket
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass


if __name__ == "__main__":
    # Force UTF-8 on Windows terminals (GBK can't encode IPA/special chars)
    force_utf8_stdio()

    import argparse
    p = argparse.ArgumentParser(description="ETS Word PK Auto v5")
    p.add_argument("--max", type=int, default=999, help="Max questions")
    p.add_argument("--debug", action="store_true", help="Show debug info")
    p.add_argument("--port", type=int, default=10086, help="CDP port")
    a = p.parse_args()
    ETSWordPK(port=a.port, debug_mode=a.debug).run(max_q=a.max)
