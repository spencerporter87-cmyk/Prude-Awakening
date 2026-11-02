"""Clean subtitles: star out filtered words, generate subtitles from the dialogue,
and shift timings when scenes are cut out."""

import re
from pathlib import Path

from .media import ffmpeg, run
from .words import STARS, censor_text, matched_word_ids


def srt_time(t):
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt_time(s):
    h, m, rest = s.strip().replace(".", ",").split(":")
    sec, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms[:3].ljust(3, "0")) / 1000


def read_srt(path):
    raw = Path(path).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = block.split("\n")
        t = next((k for k, ln in enumerate(lines) if "-->" in ln), None)
        if t is None:
            continue
        a, b = lines[t].split("-->")
        try:
            cues.append([parse_srt_time(a), parse_srt_time(b.split()[0]), "\n".join(lines[t + 1:])])
        except (ValueError, IndexError):
            continue
    return cues


def write_srt(path, cues):
    out = [f"{i + 1}\n{srt_time(a)} --> {srt_time(b)}\n{text}" for i, (a, b, text) in enumerate(cues)]
    Path(path).write_text("\n\n".join(out) + "\n", encoding="utf-8")


class TimeMap:
    """Maps original movie time -> time in the cut version."""

    def __init__(self, cuts):
        self.cuts = sorted((a, b) for a, b in cuts if b > a)

    def __call__(self, t):
        removed = 0.0
        for a, b in self.cuts:
            if t >= b:
                removed += b - a
            elif t > a:
                return None  # inside a cut
            else:
                break
        return t - removed

    def cue(self, a, b):
        ma, mb = self(a), self(b)
        if ma is None:
            nxt = next((cb for ca, cb in self.cuts if ca < a < cb), None)
            if nxt is None or nxt >= b:
                return None
            ma = self(nxt)
        if mb is None:
            prev = next((ca for ca, cb in self.cuts if ca < b < cb), None)
            mb = self(prev) if prev is not None else None
        if ma is None or mb is None or mb - ma < 0.2:
            return None
        return ma, mb


def shift_cues(cues, cuts):
    if not cuts:
        return cues
    tm = TimeMap(cuts)
    out = []
    for a, b, text in cues:
        r = tm.cue(a, b)
        if r:
            out.append([r[0], r[1], text])
    return out


def extract_text_sub(src, stream_index, dest):
    r = run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
             "-map", f"0:{stream_index}", "-c:s", "srt", str(dest)])
    return r.returncode == 0 and Path(dest).exists()


def clean_cues(cues, patterns):
    total = 0
    for c in cues:
        c[2], n = censor_text(c[2], patterns)
        total += n
    return total


def generate_cues(words, mutes, patterns):
    """Subtitles written from what was heard. Muted words become ****: listed words
    inside a mute, or every word inside a mute added by hand."""
    listed = matched_word_ids(words, patterns)
    starred = set()
    for m in mutes:
        inside = [i for i, w in enumerate(words) if m["start"] <= (w["s"] + w["e"]) / 2 <= m["end"]]
        hits = [i for i in inside if i in listed]
        starred.update(hits if hits else inside)

    def shown(i):
        w = words[i]["w"]
        if i in starred:
            lead = w[:len(w) - len(w.lstrip())]
            trail = re.search(r"[.,!?;:\"')\]]*$", w.strip()).group()
            return lead + STARS + trail
        return w

    groups, cur = [], []
    for i, w in enumerate(words):
        if cur:
            text = "".join(words[j]["w"] for j in cur).strip()
            if (w["s"] - words[cur[-1]]["e"] > 0.8 or len(text) + len(w["w"]) > 84
                    or w["e"] - words[cur[0]]["s"] > 6.0
                    or (text.endswith((".", "?", "!")) and len(text) >= 30)):
                groups.append(cur)
                cur = []
        cur.append(i)
    if cur:
        groups.append(cur)

    cues = []
    for n, g in enumerate(groups):
        text = "".join(shown(i) for i in g).strip()
        if len(text) > 42 and " " in text:
            mid = len(text) // 2
            cut = min((i for i, ch in enumerate(text) if ch == " "), key=lambda i: abs(i - mid))
            text = text[:cut] + "\n" + text[cut + 1:]
        start = words[g[0]]["s"]
        end = max(words[g[-1]]["e"] + 0.4, start + 1.0)
        if n + 1 < len(groups):
            end = min(end, words[groups[n + 1][0]]["s"] - 0.05)
        cues.append([start, max(end, start + 0.3), text])
    return cues
