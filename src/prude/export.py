"""Where and how the clean copy is saved: folder, format, size, quality and Plex naming."""

import re
from dataclasses import asdict, dataclass
from pathlib import Path

NAMING = {
    "plex_edition": "Plex edition (shows up next to the original as an edition)",
    "plex_library": "Plex, separate library (plain \"Title (Year)\")",
    "simple": "Simple (\"Movie (clean)\")",
}
RESOLUTIONS = {"source": None, "2160": 2160, "1080": 1080, "720": 720}
_BAD = r'[<>:"/\\|?*{}]'


@dataclass
class ExportOptions:
    folder: str = ""                 # "" = same folder as the movie
    container: str = "mkv"           # mkv | mp4
    resolution: str = "source"       # source | 2160 | 1080 | 720
    quality: str = "high"            # high | max
    naming: str = "plex_edition"
    edition: str = "Family Edit"
    own_folder: bool = True          # put it in a "Title (Year)" folder when saving elsewhere

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})

    def to_dict(self):
        return asdict(self)


def clean_name(text):
    text = re.sub(_BAD, "", str(text)).strip().strip(".")
    return re.sub(r"\s+", " ", text)


def parse_title(src: Path):
    """("Pretty Woman", "1990") from 'Pretty Woman (1990).mkv', 'Pretty.Woman.1990.1080p.mkv'
    or a 'Pretty Woman (1990)' folder."""
    for name in (src.stem, src.parent.name):
        n = re.sub(r"\{edition-[^}]*\}", "", name)
        m = re.match(r"^(.*?)[\s._\-\[(]*((?:19|20)\d{2})(?:[\s._\-\])]|$)", n)
        if m and m.group(1).strip(" ._-"):
            title = re.sub(r"[._]+", " ", m.group(1)).strip(" -")
            return clean_name(title), m.group(2)
    return clean_name(re.sub(r"[._]+", " ", src.stem)), ""


EPISODE = re.compile(r"(?i)\bS(\d{1,2})[ ._-]*E(\d{1,3})\b|\b(\d{1,2})x(\d{2,3})\b")
SEASON_DIR = re.compile(r"(?i)^(season|series|staffel|saison)[ ._-]*(\d{1,2})$|^s(\d{1,2})$")


def parse_episode(src: Path):
    """For TV episodes: (show_folder, show_name, year, season) - otherwise None.
    Understands 'Show (2005)/Season 01/Show - S01E02 - Title.mkv' and 'Show.S01E02.mkv'."""
    m = EPISODE.search(src.stem)
    if not m:
        return None
    season = int(m.group(1) or m.group(3))
    parent = src.parent
    if SEASON_DIR.match(parent.name.strip()):
        show_dir = parent.parent
        show_title, year = parse_title(Path(show_dir.name + ".mkv"))
    else:
        before = src.stem[:m.start()]
        show_title, year = parse_title(Path((before.strip(" ._-") or parent.name) + ".mkv"))
        folder_title, _y = parse_title(Path(parent.name + ".mkv"))
        # inside the show's own folder -> save beside it; loose in e.g. Downloads -> save right there
        if folder_title.lower() == show_title.lower():
            show_dir, show_title, year = parent, folder_title, year or _y
        else:
            show_dir = parent / "_"
    return show_dir, show_title, year, season


def output_path(src: Path, opts: ExportOptions) -> Path:
    src = Path(src)
    ext = ".mp4" if opts.container == "mp4" else ".mkv"
    ep = parse_episode(src) if opts.naming != "simple" else None
    if ep:   # TV: Plex puts editions on the show folder ("Show (2005) {edition-Family Edit}")
        show_dir, show, year, season = ep
        show_base = f"{show} ({year})" if year else show
        if opts.naming == "plex_edition":
            show_base += f" {{edition-{clean_name(opts.edition) or 'Family Edit'}}}"
        root = Path(opts.folder) if opts.folder else show_dir.parent
        out = root / show_base / f"Season {season:02d}" / f"{src.stem}{ext}"
        if out.resolve() == src.resolve():
            out = out.with_name(f"{src.stem} (clean){ext}")
        return out
    title, year = parse_title(src)
    base = f"{title} ({year})" if year else title
    if opts.naming == "plex_edition":
        edition = clean_name(opts.edition) or "Family Edit"
        name = f"{base} {{edition-{edition}}}"
    elif opts.naming == "plex_library":
        name = base
    else:
        name = f"{src.stem} (clean)"
    folder = Path(opts.folder) if opts.folder else src.parent
    if opts.folder and opts.own_folder and opts.naming != "simple" and folder.name != base:
        folder = folder / base
    out = folder / f"{name}{ext}"
    if out.resolve() == src.resolve():                    # never overwrite the original
        out = folder / f"{name} (clean){ext}"
    return out


def target_height(info, opts: ExportOptions):
    """Height to scale down to, or None to keep the original size."""
    h = RESOLUTIONS.get(opts.resolution)
    if not h or not info.height or info.height <= h + 8:
        return None
    return h


def estimate_size(info, needs_reencode, opts: ExportOptions, removed_seconds=0.0):
    """Very rough size in bytes, for showing "about 4.2 GB" before saving."""
    dur = max(1.0, info.duration - removed_seconds)
    try:
        total = Path(info.path).stat().st_size
    except OSError:
        total = 0
    if not needs_reencode:
        return int(total * dur / max(1.0, info.duration))
    h = target_height(info, opts) or info.height or 1080
    mbps = 24 if h >= 2000 else 9 if h >= 1000 else 4.5 if h >= 700 else 2.5
    if opts.quality == "max":
        mbps *= 1.5
    audio = sum((int(t.raw.get("bit_rate") or 640000) for t in info.audio[:1]))
    return int(dur * (mbps * 1e6 + audio) / 8)


def human_size(n):
    n = float(n or 0)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("bytes", "KB") else f"{n:.1f} {unit}"
        n /= 1024
