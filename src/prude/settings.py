"""User settings and filter presets, saved as JSON in the app data folder."""

import json
from dataclasses import asdict, dataclass, field

from .paths import app_data_dir, default_models_dir


@dataclass
class FilterOptions:
    words: list = field(default_factory=lambda: ["strong", "blasphemy", "sexual", "slurs"])
    custom_words: str = ""
    allow_words: str = ""
    padding: float = 0.15
    mute_mode: str = "all"            # all | center
    scenes: list = field(default_factory=list)   # nudity, sexual, violence, gore
    scene_descriptions: str = ""      # one per line
    sensitivity: int = 3              # 1..5
    scene_action: str = "cut"         # cut | black

    @classmethod
    def from_dict(cls, d):
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)


BUILTIN_PRESETS = {
    "Family night": FilterOptions(words=["strong", "blasphemy", "sexual", "slurs"],
                                  scenes=["nudity", "sexual"], sensitivity=3),
    "Kids": FilterOptions(words=["strong", "blasphemy", "sexual", "slurs", "mild"],
                          scenes=["nudity", "sexual", "violence", "gore"], sensitivity=4),
    "Language only": FilterOptions(words=["strong", "blasphemy", "sexual", "slurs"], scenes=[]),
    "Everything": FilterOptions(words=["strong", "blasphemy", "sexual", "slurs", "mild"],
                                scenes=["nudity", "sexual", "violence", "gore"], sensitivity=5),
}


@dataclass
class Settings:
    output_dir: str = ""                       # "" = next to the movie
    models_dir: str = ""                       # "" = app data
    device: str = "auto"                       # auto | cuda | cpu
    speech_model: str = ""                     # "" = best for device
    video_quality: str = "high"                # high | max
    keep_subtitles: bool = True
    make_subtitles: bool = True
    blur_thumbnails: bool = True
    gpu_pack_declined: bool = False
    last_preset: str = "Family night"
    filters: dict = field(default_factory=lambda: asdict(BUILTIN_PRESETS["Family night"]))
    presets: dict = field(default_factory=dict)  # user presets name -> FilterOptions dict
    recent: list = field(default_factory=list)
    window: list = field(default_factory=list)   # x, y, w, h

    @property
    def models_path(self):
        from pathlib import Path
        return Path(self.models_dir) if self.models_dir else default_models_dir()

    def all_presets(self):
        out = {k: asdict(v) for k, v in BUILTIN_PRESETS.items()}
        out.update(self.presets)
        return out

    def save(self):
        (app_data_dir() / "settings.json").write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls):
        p = app_data_dir() / "settings.json"
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        except Exception:
            return cls()
