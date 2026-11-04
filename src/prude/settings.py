"""User settings and filter presets, saved as JSON in the app data folder."""

import json
from dataclasses import asdict, dataclass, field

from .paths import app_data_dir, default_models_dir


@dataclass
class FilterOptions:
    words: list = field(default_factory=lambda: ["strong", "blasphemy", "sexual", "slurs"])
    excluded_words: list = field(default_factory=list)   # group keys switched off, e.g. "strong:shit"
    custom_words: str = ""
    allow_words: str = ""
    padding: float = 0.15
    advanced: dict = field(default_factory=lambda: {"enabled": False, "mode": "mute", "engine": "windows"})
    mute_fade: bool = False           # soften the edges of each silence (30 ms)
    burned_subs: str = "auto"         # subtitles in the picture: auto (foreign films) | on | off
    star_style: str = "stars"         # subtitles: stars "****" | first "f***" | word "freaking"
    mute_mode: str = "all"            # all | center
    scenes: list = field(default_factory=list)   # scene type keys, see scenes.SCENE_TYPES
    scene_descriptions: str = ""      # one per line
    sensitivity: int = 3              # 1..5, default for groups without their own
    scene_action: str = "cut"         # default action: cut | black | blur | mute
    scene_sensitivity: dict = field(default_factory=dict)   # group -> 1..5
    scene_actions: dict = field(default_factory=dict)       # group -> action

    @classmethod
    def from_dict(cls, d):
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        f = cls(**known)
        legacy = {"nudity": ["nude_f_breasts", "nude_f_genitals", "nude_m_genitals", "nude_buttocks"],
                  "sexual": ["sex_nude", "sex_implied"]}
        out = []
        for k in f.scenes:
            for t in legacy.get(k, [k]):
                if t not in out:
                    out.append(t)
        f.scenes = out
        return f


NUDITY = ["nude_f_breasts", "nude_f_genitals", "nude_m_genitals", "nude_buttocks"]
BUILTIN_PRESETS = {
    "Family night": FilterOptions(words=["strong", "blasphemy", "sexual", "slurs"],
                                  scenes=NUDITY + ["sex_nude", "sex_implied"], sensitivity=3),
    "Kids": FilterOptions(words=["strong", "blasphemy", "sexual", "slurs", "mild"],
                          scenes=NUDITY + ["sex_nude", "sex_implied", "kissing", "suggestive",
                                           "violence", "gore", "scary"], sensitivity=4),
    "Language only": FilterOptions(words=["strong", "blasphemy", "sexual", "slurs"], scenes=[]),
    "Everything": FilterOptions(words=["strong", "blasphemy", "sexual", "slurs", "mild"],
                                scenes=NUDITY + ["nude_m_chest", "sex_nude", "sex_implied", "kissing",
                                                 "suggestive", "swimwear", "violence", "gore", "scary"],
                                sensitivity=5),
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
    check_updates: bool = True
    last_update_check: float = 0.0
    elevenlabs_key: str = ""                   # Advanced Editing (beta); stays on this PC
    musetalk_dir: str = ""                     # Advanced Editing (beta): a MuseTalk install you made
    musetalk_python: str = ""
    last_preset: str = "Family night"
    filters: dict = field(default_factory=lambda: asdict(BUILTIN_PRESETS["Family night"]))
    presets: dict = field(default_factory=dict)  # user presets name -> FilterOptions dict
    recent: list = field(default_factory=list)
    export: dict = field(default_factory=dict)   # last ExportOptions used
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
