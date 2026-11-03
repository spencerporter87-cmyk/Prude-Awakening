"""Sound for the built-in player.

The player's video component can't decode the sound formats movies use (Dolby Digital, DTS,
TrueHD...), so it plays pictures silently. Instead the movie's sound is converted once into a
small stereo AAC file (a couple of minutes for a long movie, then remembered) and played next to the
picture. The converted file is made from the same audio track the clean copy has, so what you
hear is what the file contains."""

import hashlib
import os
import time
from pathlib import Path

from .media import MediaError, ffmpeg, run
from .paths import app_data_dir


def _dir():
    d = app_data_dir() / "previews"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tidy(folder, days=14):
    cut = time.time() - days * 86400
    for f in folder.glob("*.m4a"):
        try:
            if f.stat().st_mtime < cut:
                f.unlink()
        except OSError:
            pass


def audio_proxy(path, track=0):
    """Stereo AAC copy of one audio track (cached). Returns its path."""
    path = Path(path)
    st = path.stat()
    key = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}|{track}".encode()).hexdigest()[:20]
    folder = _dir()
    out = folder / f"{key}.m4a"
    if out.exists() and out.stat().st_size > 1000:
        return out
    _tidy(folder)
    tmp = folder / f"{key}.part.m4a"
    r = run([ffmpeg(), "-v", "error", "-nostdin", "-y", "-i", str(path), "-map", f"0:a:{track}", "-vn", "-sn",
             "-ac", "2", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(tmp)])
    if r.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise MediaError(f"Couldn't prepare the sound for preview: {(r.stderr or '')[-200:]}")
    os.replace(tmp, out)
    return out
