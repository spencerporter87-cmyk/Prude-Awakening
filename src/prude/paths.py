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


def write_atomic(path, text: str):
    """Write a file so a crash or power cut mid-save can't leave half a file behind."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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


def fixed_drives():
    """Windows hard drives (C:\\, D:\\ ...) - not USB sticks, network or CD drives."""
    if os.name != "nt":
        return []
    import ctypes
    import string
    out = []
    for d in string.ascii_uppercase:
        root = f"{d}:\\"
        try:
            if ctypes.windll.kernel32.GetDriveTypeW(root) == 3 and os.path.exists(root):
                out.append(Path(root))
        except Exception:                                   # noqa: BLE001
            pass
    return out


def roomiest_models_dir(need_gb=15.0):
    """First run: the AI models (a few GB) go on the drive with the most free space when the
    usual place (on C:) is short of room. Returns None to keep the usual place."""
    usual = default_models_dir()
    if usual.exists() and any(usual.iterdir()):
        return None                                         # already has models: leave them
    here = free_space_gb(usual)
    if here >= need_gb:
        return None
    best = max(((free_space_gb(d), d) for d in fixed_drives()), default=(0.0, None))
    if best[1] is None or best[0] < max(need_gb, here * 2) or \
            Path(best[1]).drive.lower() == Path(usual).drive.lower():
        return None
    return Path(best[1]) / "Prude Awakening Models"


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
