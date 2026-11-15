"""Edit lists: your choices for one movie in a small file you can keep, move to another PC or share.

An edit list holds only times, words and shapes: the mutes and scenes you left ticked, your painted blurs,
what you removed. It never holds the movie, pictures, or the dialogue transcript, so it is a few KB. Opening it on
a copy of the same movie reproduces your edit without scanning.

The same "snapshot of your choices" is what a viewer profile (profiles.py) stores for each viewer."""

import json
import time
from copy import deepcopy
from pathlib import Path

from . import __version__
from .paths import write_atomic

FORMAT = "prude-edit"
VERSION = 1
EXT = ".prudeedit"
EDIT_KEYS = ("mutes", "scenes", "subtitle_edits", "burned_edits", "paint")
LOCAL_ONLY = ("frame", "thumb", "thumb_blur")        # picture files that live on this PC only


class EditListError(Exception):
    pass


# ------------------------------------------------------------------ one viewer's choices
def snapshot(project, filters=None, portable=False):
    """Copy of the choices now in the project. portable=True also drops what only makes sense on this PC."""
    snap = {k: deepcopy(getattr(project, k, [])) for k in EDIT_KEYS}
    snap["filters"] = dict(filters) if filters else dict(getattr(project, "scan_filters", {}) or {})
    if portable:
        for s in snap["scenes"]:
            for k in LOCAL_ONLY:
                s.pop(k, None)
    return snap


def restore(project, snap, merge=False):
    """Put a snapshot's choices into the project (merge: keep what is there and add what isn't)."""
    for k in EDIT_KEYS:
        new = deepcopy(snap.get(k) or [])
        if merge:
            have = {x.get("id") for x in getattr(project, k)}
            new = [x for x in new if x.get("id") not in have]
            setattr(project, k, getattr(project, k) + new)
        else:
            setattr(project, k, new)
    for k in ("mutes", "scenes", "paint"):
        getattr(project, k).sort(key=lambda x: float(x.get("start", 0)))
    if not merge and snap.get("filters"):
        project.scan_filters = dict(snap["filters"])


def shift(snap, seconds):
    """Move every time in a snapshot (for a copy of the movie that starts earlier or later)."""
    if not seconds:
        return snap
    for k in EDIT_KEYS:
        for x in snap.get(k) or []:
            for t in ("start", "end"):
                if t in x:
                    x[t] = round(float(x[t]) + seconds, 3)
            if "cue_key" in x:                              # subtitle edits are matched by their cue's start
                x["cue_key"] = round(float(x["cue_key"]) + seconds, 1)
            for key in x.get("keys") or []:                # painted blurs: key-frame times
                if "t" in key:
                    key["t"] = round(float(key["t"]) + seconds, 3)
    return snap


# ------------------------------------------------------------------ the file
def movie_facts(path, info):
    from .project import content_id
    return {"name": Path(path).stem, "size": Path(path).stat().st_size, "duration": round(info.duration, 3),
            "fps": round(info.fps, 3) if info.video else 0, "content_id": content_id(path)}


def build(project, info, profiles, active):
    """profiles: {name: snapshot}. The active name is the one that opens first."""
    return {"format": FORMAT, "version": VERSION, "app": __version__, "created": time.strftime("%Y-%m-%d %H:%M"),
            "movie": movie_facts(project.source, info), "active": active,
            "profiles": {n: s for n, s in profiles.items()}}


def write(dest, doc):
    dest = Path(dest)
    if dest.suffix.lower() != EXT:
        dest = dest.with_name(dest.name + EXT)
    write_atomic(dest, json.dumps(doc, indent=1))
    return dest


def _num(v):
    """A finite number or None (an edit list from somewhere else may hold anything)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _clean_patch(x):
    """A painted blur whose shape data is complete: odd entries are dropped, missing numbers get their usual value."""
    a = x.get("anchor")
    x["anchor"] = [_num(a[0]), _num(a[1])] if isinstance(a, (list, tuple)) and len(a) == 2 and \
        _num(a[0]) is not None and _num(a[1]) is not None else [0.5, 0.5]
    strokes = []
    for st in x.get("strokes") or []:
        pts = [[_num(q[0]), _num(q[1])] for q in (st.get("pts") or []) if isinstance(q, (list, tuple)) and len(q) == 2
               and _num(q[0]) is not None and _num(q[1]) is not None] if isinstance(st, dict) else []
        r = _num(st.get("r")) if isinstance(st, dict) else None
        if pts and r is not None:
            strokes.append({"r": r, "pts": pts})
    x["strokes"] = strokes
    keys = []
    for k in x.get("keys") or []:
        t = _num(k.get("t")) if isinstance(k, dict) else None
        if t is not None:
            keys.append({"t": t, "dx": _num(k.get("dx")) or 0.0, "dy": _num(k.get("dy")) or 0.0,
                         "sc": max(0.05, _num(k.get("sc", k.get("scale"))) or 1.0)})
    x["keys"] = keys
    x["mode"] = "clear" if x.get("mode") == "clear" else "blur"
    x["on"] = bool(x.get("on", True))
    return x


def _clean_snapshot(snap):
    """Keep only well-formed items: dicts with real numbers for their times. Picture files that
    lived on the PC that made the list are dropped (they mean nothing here)."""
    import uuid
    out = {"filters": dict(snap.get("filters") or {}) if isinstance(snap.get("filters"), dict) else {}}
    for k in EDIT_KEYS:
        items = []
        for x in snap.get(k) or []:
            if not isinstance(x, dict):
                continue
            x = dict(x)
            ok = True
            for t in ("start", "end"):
                if t in x or (k in ("mutes", "scenes", "paint")):
                    v = _num(x.get(t))
                    if v is None:
                        ok = False
                        break
                    x[t] = v
            if not ok:
                continue
            if k == "scenes":
                for key in LOCAL_ONLY:
                    x.pop(key, None)
            if k in ("scenes", "paint"):
                for pad in ("pad_before", "pad_after"):
                    if pad in x:
                        x[pad] = _num(x[pad]) or 0.0
            if k == "paint":
                x = _clean_patch(x)
            x.setdefault("id", uuid.uuid4().hex[:12])
            items.append(x)
        out[k] = items
    if isinstance(snap.get("leaks"), dict):
        out["leaks"] = {}
    return out


def read(path):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise EditListError(f"This file can't be read as an edit list ({e}).")
    if not isinstance(doc, dict) or doc.get("format") != FORMAT or not isinstance(doc.get("profiles"), dict) \
            or not doc["profiles"]:
        raise EditListError("This isn't a Prude Awakening edit list.")
    try:
        newer = int(doc.get("version", 1)) > VERSION
    except (TypeError, ValueError):
        raise EditListError("This edit list's version isn't readable.")
    if newer:
        raise EditListError("This edit list was made by a newer version of Prude Awakening. Update the app and try again.")
    doc["profiles"] = {str(n): _clean_snapshot(sn) for n, sn in doc["profiles"].items() if isinstance(sn, dict)}
    if not doc["profiles"]:
        raise EditListError("This edit list has no readable choices in it.")
    if not isinstance(doc.get("movie"), dict):
        doc["movie"] = {}
    if doc.get("active") not in doc["profiles"]:
        doc["active"] = next(iter(doc["profiles"]))
    return doc


def compare(doc, path, info):
    """Is this the movie the list was made for? {"match": exact | same_length | different, "message": ...}"""
    from .project import content_id
    m = doc.get("movie") or {}
    try:
        same = content_id(path) == m.get("content_id")
    except OSError:
        same = False
    if same:
        return {"match": "exact", "message": "This is the same file the edit list was made for."}
    dur, fps = _num(m.get("duration")) or 0.0, _num(m.get("fps")) or 0.0
    if dur and abs(dur - info.duration) <= 0.6 and (not fps or not info.video or abs(fps - info.fps) < 0.05):
        return {"match": "same_length", "message": "Same length as the movie the list was made for - the times line up."}
    return {"match": "different",
            "message": f"The list was made for a version that runs {_hms(dur)}; this one runs {_hms(info.duration)}. "
                       "The times may not line up (a different cut, or a different intro). Check it in the review."}


def _hms(sec):
    sec = int(round(float(sec or 0)))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def apply(project, doc, profile=None, merge=False, offset=0.0):
    """Put one profile of an edit list into the project. Returns (name, filters dict)."""
    name = profile or doc.get("active") or next(iter(doc["profiles"]))
    if name not in doc["profiles"]:
        raise EditListError(f"The edit list has no viewer called “{name}”.")
    snap = shift(deepcopy(doc["profiles"][name]), float(offset or 0))
    restore(project, snap, merge=merge)
    if not merge:
        project.profile = name
    return name, dict(snap.get("filters") or {})
