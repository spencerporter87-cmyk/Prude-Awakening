"""Word lists by category, matching transcript words, censoring subtitle text."""

import fnmatch
import re
from dataclasses import dataclass

from .paths import DATA_DIR

CATEGORIES = [
    ("strong", "Strong language", "F-word, S-word and other hard swearing"),
    ("blasphemy", "Taking God's name in vain", "\"Oh my God\", \"Jesus Christ\", \"God damn\"…"),
    ("sexual", "Sexual words", "Crude sexual terms and references"),
    ("slurs", "Slurs", "Racial, ethnic and anti-gay slurs"),
    ("mild", "Mild language", "Damn, hell, crap, ass…"),
]
CATEGORY_NAMES = {k: n for k, n, _ in CATEGORIES} | {"custom": "Your words"}
STARS = "****"


@dataclass
class Pattern:
    label: str
    tokens: list
    category: str


def normalize(word: str) -> str:
    w = word.lower().replace("’", "'")
    w = re.sub(r"[^a-z0-9'*]", "", w)
    return w.strip("'")


def category_groups(key: str) -> list:
    """[(label, [patterns])] for a category. File lines: 'label: pat, pat' or a bare pattern."""
    path = DATA_DIR / "words" / f"{key}.txt"
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" in line:
            label, pats = line.split(":", 1)
            pats = [p.strip() for p in pats.split(",") if p.strip()]
        else:
            label, pats = line, [line]
        out.append((label.strip(), pats))
    return out


def group_key(category, label):
    return f"{category}:{label}"


def category_words(key: str) -> list:
    return [p for _label, pats in category_groups(key) for p in pats]


def build_patterns(categories, custom_text="", allow_text="", excluded=()) -> list:
    """categories: category keys switched on. excluded: group keys ("strong:shit") switched off.
    custom_text: one word/phrase per line (or comma separated). allow_text: never filter."""
    allow = {normalize(w) for w in re.split(r"[\n,]+", allow_text or "") if normalize(w)}
    excluded = set(excluded or ())
    seen, patterns = set(), []

    def add(line, cat):
        tokens = [normalize(t) for t in line.split()]
        tokens = [t for t in tokens if t]
        if not tokens or (len(tokens) == 1 and tokens[0] in allow):
            return
        key = " ".join(tokens)
        if key not in seen:
            seen.add(key)
            patterns.append(Pattern(line, tokens, cat))

    for line in re.split(r"[\n,]+", custom_text or ""):
        add(line.strip(), "custom")
    for cat in categories:
        for label, pats in category_groups(cat):
            if group_key(cat, label) in excluded:
                continue
            for line in pats:
                add(line, cat)
    patterns.sort(key=lambda p: -len(p.tokens))  # longest phrase wins
    return patterns


def _scan(tokens, patterns, allow=frozenset()):
    """tokens: list of normalized strings. Yields (start_i, end_i_exclusive, pattern)."""
    i = 0
    while i < len(tokens):
        for p in patterns:
            n = len(p.tokens)
            if i + n <= len(tokens) and all(
                    fnmatch.fnmatchcase(tokens[i + k], p.tokens[k]) for k in range(n)):
                if n == 1 and tokens[i] in allow:
                    continue
                yield i, i + n, p
                i += n
                break
        else:
            i += 1


def find_matches(words, patterns, pad_before=0.15, pad_after=0.15, allow_text=""):
    """words: [{"w","s","e"}]. Returns list of hits with start/end/words/category/context."""
    allow = frozenset(normalize(w) for w in re.split(r"[\n,]+", allow_text or "") if normalize(w))
    toks = [(normalize(w["w"]), w) for w in words]
    toks = [t for t in toks if t[0]]
    norm = [t[0] for t in toks]
    hits = []
    matched = set()
    for a, b, p in _scan(norm, patterns, allow):
        first, last = toks[a][1], toks[b - 1][1]
        hits.append({"start": max(0.0, first["s"] - pad_before), "end": last["e"] + pad_after,
                     "words": " ".join(t[1]["w"].strip() for t in toks[a:b]),
                     "category": p.category, "rule": p.label, "_i": a})
        matched.update(range(a, b))
    for i, (n, w) in enumerate(toks):  # Whisper sometimes writes "f***" itself
        if i not in matched and "*" in w["w"]:
            hits.append({"start": max(0.0, w["s"] - pad_before), "end": w["e"] + pad_after,
                         "words": w["w"].strip(), "category": "strong",
                         "rule": "(bleeped in transcript)", "_i": i})
    for h in hits:
        a, b = max(0, h["_i"] - 7), min(len(toks), h["_i"] + 9)
        h["context"] = " ".join(t[1]["w"].strip() for t in toks[a:b])
        del h["_i"]
    return sorted(hits, key=lambda h: h["start"])


def merge_intervals(items, gap=0.25):
    items = sorted(items, key=lambda m: m["start"])
    merged = []
    for m in items:
        if merged and m["start"] <= merged[-1]["end"] + gap:
            last = merged[-1]
            last["end"] = max(last["end"], m["end"])
            if m["words"] not in last["words"]:
                last["words"] += " / " + m["words"]
        else:
            merged.append(dict(m))
    return merged


def matched_word_ids(words, patterns):
    toks = [(i, normalize(w["w"])) for i, w in enumerate(words)]
    toks = [t for t in toks if t[1]]
    ids = set()
    for a, b, _p in _scan([t[1] for t in toks], patterns):
        ids.update(t[0] for t in toks[a:b])
    for i, w in enumerate(words):
        if "*" in w["w"]:
            ids.add(i)
    return ids


_WORD_RE = re.compile(r"[A-Za-z0-9'’*]+(?:-[A-Za-z0-9'’]+)*")


def censor_text(text, patterns):
    toks = [(m.start(), m.end(), normalize(m.group())) for m in _WORD_RE.finditer(text)]
    toks = [t for t in toks if t[2]]
    spans = [(toks[a][0], toks[b - 1][1]) for a, b, _p in _scan([t[2] for t in toks], patterns)]
    for a, b in reversed(spans):
        text = text[:a] + STARS + text[b:]
    return text, len(spans)
