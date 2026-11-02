"""Where things live: bundled tools, app data, models, projects."""

import os
import shutil
import sys
from pathlib import Path

from . import APP_ID

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"
IS_FROZEN = bool(getattr(sys, "frozen", False))
# Windows: stop every ffmpeg call from flashing a console window.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def bundle_dir() -> Path:
    """Folder holding bundled extras (ffmpeg) - next to the .exe when packaged,
    the repository's vendor/ folder when running from source."""
    if IS_FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return PACKAGE_DIR.parent.parent / "vendor"


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") \
        or str(Path.home() / ".local" / "share")
    d = Path(os.environ.get("PRUDE_DATA_DIR") or Path(base) / APP_ID)
    d.mkdir(parents=True, exist_ok=True)
    return d


def projects_dir() -> Path:
    d = app_data_dir() / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_models_dir() -> Path:
    return app_data_dir() / "models"


def find_tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    candidates = []
    if os.environ.get("PRUDE_FFMPEG_DIR"):
        candidates.append(Path(os.environ["PRUDE_FFMPEG_DIR"]))
    b = bundle_dir()
    candidates += [b / "ffmpeg" / "bin", b / "ffmpeg", b]
    for folder in candidates:
        if (folder / exe).exists():
            return str(folder / exe)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        f"{name} was not found. Reinstall {APP_ID} or install FFmpeg.")


def free_space_gb(path: Path) -> float:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free / 1e9
    except OSError:
        return 0.0
