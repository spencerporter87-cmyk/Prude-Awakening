"""One movie's work: its transcript, the mutes and scenes found, and your choices.
Kept in the app data folder so the movie's own folder only gets the clean copy."""

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .paths import projects_dir, write_atomic


SCHEMA = 2


def migrate(d: dict) -> dict:
    """Bring an older saved project up to the current layout without losing anything."""
    import uuid
    for kind in ("mutes", "scenes", "subtitle_edits", "burned_edits"):
        for item in d.get(kind) or []:
            item.setdefault("id", uuid.uuid4().hex[:12])
            item.setdefault("on", True)
            item.setdefault("origin", "manual" if item.get("manual") else "auto")
    d["schema"] = SCHEMA
    return d


def _plain(o):
    """numpy numbers and paths -> plain JSON values."""
    if hasattr(o, "item"):
        return o.item()
    if hasattr(o, "tolist"):
        return o.tolist()
    return str(o)


def _hand(items):
    return [x for x in items or [] if x.get("manual") or x.get("origin") == "manual"]


def _by_hand(d: dict) -> dict:
    """The parts of a saved project that came from the person, not from a scan."""
    keep = {"source": d.get("source", ""), "paint": d.get("paint") or []}
    keep["scenes"] = _hand(d.get("scenes"))
    keep["mutes"] = _hand(d.get("mutes"))
    if d.get("profiles"):                                   # the other viewers' hand-made choices too
        keep["profile"] = d.get("profile", "")
        keep["profiles"] = {n: {"filters": sn.get("filters"), "paint": sn.get("paint") or [],
                                "scenes": _hand(sn.get("scenes")), "mutes": _hand(sn.get("mutes")),
                                "subtitle_edits": [], "burned_edits": []}
                            for n, sn in d["profiles"].items()}
    return keep


def _clear_dir(folder: Path):
    """Empty one project's folder but keep painted blurs and scenes/words you added yourself."""
    keep = {}
    f = Path(folder) / "project.json"
    try:
        if f.exists():
            keep = _by_hand(json.loads(f.read_text(encoding="utf-8")))
    except Exception:                                   # noqa: BLE001 - unreadable: nothing to keep
        keep = {}
    shutil.rmtree(folder, ignore_errors=True)
    if any(keep.get(k) for k in ("paint", "scenes", "mutes", "profiles")):
        Path(folder).mkdir(parents=True, exist_ok=True)
        write_atomic(Path(folder) / "project.json", json.dumps(keep))


def clear_all_scans():
    """Clear every saved scan; hand-made work (painted blurs, scenes and words you added) stays."""
    root = projects_dir()
    for d in list(root.iterdir()) if root.exists() else []:
        if d.is_dir():
            _clear_dir(d)
        else:
            d.unlink(missing_ok=True)
    projects_dir()


def project_key(path: Path) -> str:
    st = path.stat()
    h = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8"))
    return h.hexdigest()[:16]


def content_id(path) -> str:
    """Who this file is, by what is in it (its size and three small samples): the same movie keeps the same
    id when it is moved, renamed or touched, and two different movies practically never share one."""
    p = Path(path)
    size = p.stat().st_size
    h = hashlib.sha1(str(size).encode())
    sample = 1 << 18
    with open(p, "rb") as f:
        for off in (0, max(0, size // 2 - sample // 2), max(0, size - sample)):
            f.seek(off)
            h.update(f.read(sample))
    return h.hexdigest()[:20]


def _index_path() -> Path:
    return projects_dir() / "_content.json"


def _index() -> dict:
    try:
        return json.loads(_index_path().read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return {}


@dataclass
class Project:
    source: str
    audio_track: int = 0
    language: str = "en"
    transcript: dict = field(default_factory=dict)      # {"track", "model", "words": [...]}
    mutes: list = field(default_factory=list)           # {id,start,end,words,context,category,on,manual}
    scenes: list = field(default_factory=list)          # {id,start,end,kind,label,score,thumb,on}
    scan_filters: dict = field(default_factory=dict)
    last_output: str = ""
    exports: list = field(default_factory=list)          # summaries of finished exports
    subtitle_edits: list = field(default_factory=list)   # filtered words found in the subtitles
    dialogue: str = "auto"                                # auto | en | foreign
    spoken_language: str = ""                             # detected, e.g. "en", "yua"
    english_share: float = 1.0
    lang_track: int = -1
    burned_edits: list = field(default_factory=list)     # words in subtitles burned into the picture
    paint: list = field(default_factory=list)            # hand-painted blurs (see paint.py)
    spend_ok: bool = False                               # ElevenLabs spending confirmed for this render
    burned_check: object = None                          # None = not checked; True/False
    leaks: dict = field(default_factory=dict)            # what the check of the finished copy found (leakcheck.py)
    profile: str = ""                                    # the viewer whose choices are in mutes/scenes/... right now
    profiles: dict = field(default_factory=dict)         # the OTHER viewers' saved choices (see profiles.py)
    schema: int = SCHEMA

    @property
    def dir(self) -> Path:
        d = projects_dir() / project_key(Path(self.source))
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self):
        write_atomic(self.dir / "project.json", json.dumps(asdict(self), default=_plain))
        try:                                                # remember who this is, so moving the file keeps the work
            cid = getattr(self, "_cid", None) or content_id(self.source)
            self._cid = cid
            idx = _index()
            if idx.get(cid) != self.dir.name:
                idx[cid] = self.dir.name
                write_atomic(_index_path(), json.dumps(idx))
        except OSError:
            pass

    @classmethod
    def open(cls, source) -> "Project":
        source = str(Path(source).resolve())
        p = projects_dir() / project_key(Path(source)) / "project.json"
        if p.exists():
            try:
                d = migrate(json.loads(p.read_text(encoding="utf-8")))
                d["source"] = source
                return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
            except Exception:
                pass
        else:                                               # not under this path/date: maybe the same movie, moved
            try:
                old = projects_dir() / _index().get(content_id(source), "") / "project.json"
                if old.is_file():
                    d = migrate(json.loads(old.read_text(encoding="utf-8")))
                    d["source"] = source
                    proj = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
                    proj.save()
                    return proj
            except Exception:                               # noqa: BLE001 - start fresh
                pass
        return cls(source=source)

    def has_transcript(self, track) -> bool:
        t = self.transcript
        return t.get("track") == track and (bool(t.get("words")) or bool(t.get("skipped")))

    def clear_cache(self):
        """Forget the scan (transcript, frames, found words and scenes) but keep what you made by hand."""
        _clear_dir(self.dir)

    def active_mutes(self):
        return [m for m in self.mutes if m.get("on", True)]

    def active_scenes(self):
        return [s for s in self.scenes if s.get("on", True)]

    def export_edits(self, path):
        """Human-readable list (for people who like editing in Notepad)."""
        Path(path).write_text(json.dumps({"mutes": self.mutes, "scenes": self.scenes}, indent=1),
                              encoding="utf-8")
