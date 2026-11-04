"""Updates: look for a newer installer on the project's GitHub page, download it, run it.

Only talks to GitHub, only when the app starts (at most every 6 hours) and only if "Check for
updates" is on in Settings. Nothing is installed until you press Update; the installer is checked
against GitHub's published checksum when there is one."""

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__
from .media import Cancelled
from .paths import NO_WINDOW

REPO = "spencerporter87-cmyk/Prude-Awakening"
API = f"https://api.github.com/repos/{REPO}/releases/tags/latest-build"
ASSET = re.compile(r"^Prude-Awakening-Setup-(\d+(?:\.\d+){1,3})\.exe$")


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4])


def check(current=__version__, get=None, timeout=10):
    """{"version", "url", "size", "sha256", "name"} when a newer installer exists, else None.
    Any problem (offline, rate limit...) returns None: updates are never worth an error message."""
    try:
        import requests
        get = get or requests.get
        r = get(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "PrudeAwakening"},
                timeout=timeout)
        if r.status_code != 200:
            return None
        for a in r.json().get("assets", []):
            m = ASSET.match(a.get("name", ""))
            if m and vtuple(m.group(1)) > vtuple(current):
                digest = str(a.get("digest") or "")
                return {"version": m.group(1), "url": a["browser_download_url"], "size": int(a.get("size") or 0),
                        "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else "",
                        "name": a["name"]}
    except Exception:                                      # noqa: BLE001
        return None
    return None


def download(info, progress=None, cancel=None, log=print, get=None):
    """Download the installer to a temporary folder. Returns its path."""
    import requests
    get = get or requests.get
    dest = Path(tempfile.gettempdir()) / "PrudeAwakeningUpdate"
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / info["name"]
    part = out.with_suffix(".part")
    h = hashlib.sha256()
    got = 0
    with get(info["url"], stream=True, timeout=30, headers={"User-Agent": "PrudeAwakening"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or info.get("size") or 0)
        with open(part, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                if cancel is not None and cancel.is_set():
                    f.close()
                    part.unlink(missing_ok=True)
                    raise Cancelled()
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress and total:
                    progress("update", got / total, f"Downloading the update… {int(100 * got / total)}%")
    if info.get("size") and got != info["size"]:
        part.unlink(missing_ok=True)
        raise RuntimeError("The download was incomplete. Try again.")
    if info.get("sha256") and h.hexdigest().lower() != info["sha256"].lower():
        part.unlink(missing_ok=True)
        raise RuntimeError("The downloaded file didn't match GitHub's checksum, so it was not used.")
    os.replace(part, out)
    return str(out)


def run_installer(path):
    """Start the installer quietly (it closes this app, installs, and reopens it)."""
    if not sys.platform.startswith("win"):
        raise RuntimeError("Automatic updates only work on Windows.")
    subprocess.Popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                      "/RESTARTAPPLICATIONS"], creationflags=NO_WINDOW, close_fds=True)
