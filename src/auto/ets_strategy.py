#!/usr/bin/env python3
"""
ETS Strategy — Answer strategy layer for ETS_Auto.

数据层(ets_parser) → 策略层(ets_strategy) → 执行层(ets_auto)

Responsibilities:
  - Load all answer data for a given set_id from ETS cache
  - Build composite lookup keys: f"{structure_type}_{stid}_{qid}"
  - Provide answer lookup with fallback chain:
        local answer (exact match)
          → local answer (fuzzy title match)
          → DOM answer (passed in from ets_auto)
  - Generate answer instructions for the execution layer

Composite Key design (avoids index-based mismatch):
  For choose:  f"{structure_type}_{stid}_{xt_xh}"
  For fill:    f"{structure_type}_{stid}_{xth}"
  For role/dialogue: f"{structure_type}_{stid}_q{qi+1}"
  For picture/read:  f"{structure_type}_{stid}"

Usage:
  from ets_strategy import ETSStrategy
  strategy = ETSStrategy()
  strategy.load_set(set_id)   # pre-load all sections for this set
  ans = strategy.lookup('collector.choose', stid, qid='1')
"""
import sys, os, json, re, hashlib
from urllib.parse import urlparse, parse_qs

# ── Path setup ───────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    _BASE = sys._MEIPASS
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))

ETS_DATA_DIR = os.path.join(os.environ.get('APPDATA', ''), 'ETS')


def _read_json(path):
    """Read JSON file, trying UTF-8 then gb18030."""
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8', 'gb18030'):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return json.loads(raw.decode('utf-8', errors='replace'))


def _text_hash(text, length=8):
    """Generate a short hash of question text for composite key."""
    if not text:
        return ''
    cleaned = re.sub(r'\s+', '', text)
    return hashlib.md5(cleaned.encode('utf-8')).hexdigest()[:length]


def _html_to_text(html_str):
    """Strip HTML tags and unescape entities.

    Uses a conservative tag-matching pattern that requires tags to start
    with a letter (e.g. <br>, <p>, </div>) — this avoids false matches on
    math text like "x < y" or "a < 3" which are not HTML tags.
    """
    if not html_str:
        return ''
    import html
    text = html_str
    # Replace block-level break tags with space (not empty string)
    # to prevent word gluing: "word1<br>word2" → "word1 word2", not "word1word2"
    text = re.sub(r'<br\s*/?>', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'<p\s*/?>', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', ' ', text, flags=re.IGNORECASE)
    # Match only valid HTML tags: < followed by optional / then a letter
    # This avoids matching math/comparison operators like "x < y"
    text = re.sub(r'</?[a-zA-Z][^>]*>', '', text)
    text = html.unescape(text)
    # Collapse multiple spaces introduced by tag replacement
    text = re.sub(r' +', ' ', text)
    return text.strip()


class ETSStrategy:
    """
    Strategy layer: loads and indexes answer data for a set_id.

    After load_set(set_id), call:
      lookup(structure_type, stid, **kwargs) → answer dict or None

    The composite key is built internally from (structure_type, stid, qid/title).
    """

    _SET_CACHE_MAX = 20  # max cached sets; LRU eviction beyond this

    def __init__(self):
        self.set_id = None
        self.sections = []        # list of section dicts
        self.answer_index = {}    # composite_key → answer_dict
        self.recording_answers = []
        # Class-level LRU cache: set_id → (sections, answer_index, recording_answers)
        # Avoids re-reading content.json when switching between sets
        if not hasattr(self.__class__, '_set_cache'):
            self.__class__._set_cache = {}
        if not hasattr(self.__class__, '_set_cache_order'):
            self.__class__._set_cache_order = []  # list of set_ids, MRU at end

    # ── Public API ────────────────────────────────────────────

    def load_set(self, set_id):
        """
        Load all sections for a set_id from ETS cache.
        Returns True on success, False if no data found.
        Uses class-level cache to avoid re-reading content.json files.
        """
        set_id = str(set_id)

        # Check cache first (and update LRU order)
        cached = self.__class__._set_cache.get(set_id)
        if cached is not None:
            self.set_id = set_id
            self.sections, self.answer_index, self.recording_answers = cached
            # Move to end (most recently used)
            order = self.__class__._set_cache_order
            if set_id in order:
                order.remove(set_id)
            order.append(set_id)
            return len(self.sections) > 0

        self.set_id = set_id
        self.sections = []
        self.answer_index = {}
        self.recording_answers = []

        exam_dir = os.path.join(ETS_DATA_DIR, self.set_id)
        if not os.path.isdir(exam_dir):
            return False

        for d in sorted(os.listdir(exam_dir)):
            if not d.startswith('content_'):
                continue
            cj = os.path.join(exam_dir, d, 'content.json')
            if not os.path.exists(cj):
                continue
            try:
                data = _read_json(cj)
            except Exception:
                continue

            stype = data.get('structure_type', '')
            info = data.get('info', {})
            stid = str(info.get('stid', ''))

            section = {
                'dir': d,
                'type': stype,
                'stid': stid,
                'data': data,
            }
            self.sections.append(section)
            self._index_section(section)

        # Store in cache with LRU eviction
        if self.sections:
            cache = self.__class__._set_cache
            order = self.__class__._set_cache_order
            cache[set_id] = (self.sections, self.answer_index, self.recording_answers)
            if set_id in order:
                order.remove(set_id)
            order.append(set_id)
            # Evict oldest entries beyond max size
            while len(order) > self.__class__._SET_CACHE_MAX:
                old_id = order.pop(0)
                cache.pop(old_id, None)

        return len(self.sections) > 0

    def lookup(self, structure_type, stid, qid=None, title_text=None, dom_answer=None):
        """
        Look up answer for current question.

        Args:
          structure_type: e.g. 'collector.choose'
          stid:          section stid (string)
          qid:           question number/identifier (string), e.g. '1', '2'
          title_text:     question text for fuzzy matching (optional)
          dom_answer:    answer extracted from DOM (optional, for fallback)

        Returns:
          answer dict or None, e.g.
            {'type': 'choose', 'answer': 'C', 'source': 'local'}
            {'type': 'fill',   'answer': 'apple', 'source': 'local'}
            {'type': 'role',   'answer': '...text...', 'source': 'local'}
        """
        stid = str(stid)

        # 1) Exact composite key match
        if qid is not None:
            key = "%s_%s_%s" % (structure_type, stid, str(qid))
            if key in self.answer_index:
                result = dict(self.answer_index[key])
                result['source'] = 'local'
                return result

        # 2) Title-text fuzzy match (for out-of-order pages)
        if title_text:
            best = None
            best_score = 0
            clean_title = _html_to_text(title_text)[:40]
            for k, v in self.answer_index.items():
                if not k.startswith("%s_%s_" % (structure_type, stid)):
                    continue
                vt = v.get('title_text', '')
                if vt and clean_title and self._text_similarity(clean_title, vt) > 0.6:
                    score = self._text_similarity(clean_title, vt)
                    if score > best_score:
                        best_score = score
                        best = v
            if best:
                result = dict(best)
                result['source'] = 'local_fuzzy'
                result['match_score'] = round(best_score, 2)
                return result

        # 3) Fallback: return DOM answer if provided
        if dom_answer is not None:
            return {'type': structure_type.split('.')[-1],
                     'answer': dom_answer, 'source': 'dom'}

        # 4) Cold-start: no cache data loaded — return None gracefully
        # Caller (ets_auto) should handle None by skipping strategy lookup
        return None

    def get_recording_answers(self, stype=None):
        """Return recording answer list, optionally filtered by type."""
        if stype:
            return [r for r in self.recording_answers if r.get('type') == stype]
        return self.recording_answers

    def list_sections(self):
        """Return list of (stid, structure_type) for current set."""
        return [(s['stid'], s['type']) for s in self.sections]

    # ── Internal indexing ────────────────────────────────────

    def _index_section(self, section):
        """Build composite-key index for one section."""
        data = section['data']
        stype = section['type']
        stid = section['stid']
        info = data.get('info', {})

        if stype == 'collector.choose':
            for xt in info.get('xtlist', []):
                qid = str(xt.get('xt_xh', ''))
                answer = xt.get('answer', '')
                title = _html_to_text(xt.get('xt_nr', ''))
                key = "collector.choose_%s_%s" % (stid, qid)
                self.answer_index[key] = {
                    'type': 'choose',
                    'answer': answer,
                    'title_text': title,
                    'options': [xx.get('xx_nr', '') for xx in xt.get('xxlist', [])],
                }

        elif stype == 'collector.fill':
            for std in info.get('std', []):
                qid = str(std.get('xth', std.get('th', '')))
                answer = std.get('value', '')
                if '/' in answer:
                    answer = answer.split('/')[0].strip()
                key = "collector.fill_%s_%s" % (stid, qid)
                self.answer_index[key] = {
                    'type': 'fill',
                    'answer': answer,
                    'title_text': '',
                }

        elif stype in ('collector.role', 'collector.dialogue'):
            questions = info.get('question', [])
            for qi, q in enumerate(questions):
                qid = str(qi + 1)
                ask = _html_to_text(q.get('ask', ''))
                key = "%s_%s_q%s" % (stype, stid, qid)
                # Collect all acceptable answer variants
                variants = []
                for s in q.get('std', [])[:8]:
                    v = _html_to_text(s.get('value', ''))
                    if v and v not in variants:
                        variants.append(v)
                self.answer_index[key] = {
                    'type': 'oral',
                    'ask': ask,
                    'keywords': q.get('keywords', ''),
                    'variants': variants,
                    'title_text': ask,
                }
            # Also index std fill answers for dialogue inside fill loop
            for std in info.get('std', []):
                qid = str(std.get('xth', std.get('th', '')))
                answer = std.get('value', '')
                if '/' in answer:
                    answer = answer.split('/')[0].strip()
                if answer:
                    key = "collector.fill_%s_%s" % (stid, qid)
                    self.answer_index[key] = {
                        'type': 'fill',
                        'answer': answer,
                        'title_text': '',
                    }

        elif stype == 'collector.picture':
            # Picture has no per-question qid; index by stid only
            key = "collector.picture_%s" % stid
            ref_text = _html_to_text(info.get('value', ''))
            topic = info.get('topic', '')
            if not ref_text:
                ref_text = _html_to_text(info.get('keypoint', ''))
            if not ref_text:
                ref_text = '\n\n'.join([
                    _html_to_text(s.get('value', '')) for s in info.get('std', []) if s.get('value', '')
                ])
            self.answer_index[key] = {
                'type': 'picture',
                'answer': ref_text,
                'topic': topic,
                'title_text': topic,
            }
            self.recording_answers.append({
                'stid': stid,
                'type': 'picture',
                'topic': topic,
                'answer': ref_text,
            })

        elif stype == 'collector.read':
            key = "collector.read_%s" % stid
            ref_text = _html_to_text(info.get('value', ''))
            symbol = info.get('symbol', '')
            self.answer_index[key] = {
                'type': 'read',
                'answer': ref_text,
                'symbol': symbol,
                'title_text': ref_text[:40],
            }
            self.recording_answers.append({
                'stid': stid,
                'type': 'read',
                'answer': ref_text,
                'symbol': symbol,
            })

    # ── Text similarity (for fuzzy matching) ────────────────

    def _text_similarity(self, a, b):
        """Word-level similarity using difflib.SequenceMatcher.

        Uses word-level comparison (not character-level) to avoid
        anagram blindspots where 'bad credit' == 'credit bad'.
        Falls back to character-level for very short strings (<3 words)."""
        if not a or not b:
            return 0.0
        import difflib
        a_lower = a.lower().strip()
        b_lower = b.lower().strip()
        a_words = a_lower.split()
        b_words = b_lower.split()
        if len(a_words) >= 3 or len(b_words) >= 3:
            # Word-level: preserves word order, resists anagram tricks
            return difflib.SequenceMatcher(None, a_words, b_words).ratio()
        else:
            # Short strings: character-level SequenceMatcher (order-aware, not Jaccard)
            return difflib.SequenceMatcher(None, a_lower, b_lower).ratio()


# ── Standalone test ──────────────────────────────────────────

def main():
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ets_strategy.py <set_id> [qid]")
        sys.exit(1)

    set_id = sys.argv[1]
    strategy = ETSStrategy()
    ok = strategy.load_set(set_id)
    if not ok:
        print("ERROR: set_id=%s not found in cache" % set_id)
        sys.exit(1)

    print("Loaded set_id=%s, %d sections, %d indexed answers\n" % (
        set_id, len(strategy.sections), len(strategy.answer_index)))

    for stid, stype in strategy.list_sections():
        print("  [%s] %s" % (stype, stid))

    print()

    if len(sys.argv) >= 3:
        qid = sys.argv[2]
        # Try choose first
        ans = strategy.lookup('collector.choose', '82750', qid=qid)
        if ans:
            print("Choose Q%s: %s (source=%s)" % (qid, ans['answer'], ans['source']))
        else:
            print("No answer found for qid=%s" % qid)
    else:
        # Show first few indexed answers
        for i, (k, v) in enumerate(strategy.answer_index.items()):
            print("  %s → %s" % (k, v['answer'] if 'answer' in v else v.get('type', '')))
            if i >= 10:
                print("  ... (%d total)" % len(strategy.answer_index))
                break


if __name__ == '__main__':
    main()
