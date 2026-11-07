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


def project_key(path: Path) -> str:
    st = path.stat()
    h = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8"))
    return h.hexdigest()[:16]


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
    spend_ok: bool = False                               # ElevenLabs spending confirmed for this render
    burned_check: object = None                          # None = not checked; True/False
    schema: int = SCHEMA

    @property
    def dir(self) -> Path:
        d = projects_dir() / project_key(Path(self.source))
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self):
        write_atomic(self.dir / "project.json", json.dumps(asdict(self), default=_plain))

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
        return cls(source=source)

    def has_transcript(self, track) -> bool:
        t = self.transcript
        return t.get("track") == track and (bool(t.get("words")) or bool(t.get("skipped")))

    def clear_cache(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def active_mutes(self):
        return [m for m in self.mutes if m.get("on", True)]

    def active_scenes(self):
        return [s for s in self.scenes if s.get("on", True)]

    def export_edits(self, path):
        """Human-readable list (for people who like editing in Notepad)."""
        Path(path).write_text(json.dumps({"mutes": self.mutes, "scenes": self.scenes}, indent=1),
                              encoding="utf-8")
