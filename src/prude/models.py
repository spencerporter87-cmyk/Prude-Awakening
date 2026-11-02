"""Downloading the AI models (once) with progress, resume and reuse of earlier downloads."""

import glob
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from .media import Cancelled
from .paths import NO_WINDOW, free_space_gb

HF = "https://huggingface.co"

WHISPER_REPOS = {
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "base.en": "Systran/faster-whisper-base.en",
    "small.en": "Systran/faster-whisper-small.en",
    "medium.en": "Systran/faster-whisper-medium.en",
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
}
WHISPER_FILES = ["config.json", "preprocessor_config.json", "model.bin",
                 "tokenizer.json", "vocabulary.json", "vocabulary.txt"]
WHISPER_SIZES_GB = {"tiny.en": 0.08, "base.en": 0.15, "small.en": 0.5, "medium.en": 1.5,
                    "large-v3": 3.1, "large-v3-turbo": 1.6, "distil-large-v3": 1.5}

CLIP_REPO = "Xenova/clip-vit-base-patch32"
CLIP_FILES = ["onnx/vision_model.onnx", "onnx/text_model.onnx", "tokenizer.json"]

GPU_WHEELS = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12", "nvidia-cuda-runtime-cu12"]


class DownloadError(Exception):
    pass


def _session():
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = "PrudeAwakening/2"
    return s


def _download(url, dest: Path, progress, cancel, label, done_bytes=0, total_bytes=0):
    """Stream url to dest with resume. Reports progress over total_bytes if given."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    s = _session()
    headers = {"Range": f"bytes={have}-"} if have else {}
    with s.get(url, stream=True, headers=headers, timeout=60, allow_redirects=True) as r:
        if r.status_code == 416:  # already complete
            part.rename(dest)
            return have
        if r.status_code not in (200, 206):
            raise DownloadError(f"Download failed ({r.status_code}) for {url}")
        if r.status_code == 200:
            have = 0
        size = have + int(r.headers.get("Content-Length") or 0)
        mode = "ab" if have else "wb"
        got = have
        with open(part, mode) as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                f.write(chunk)
                got += len(chunk)
                if progress:
                    tot = total_bytes or size
                    progress(min(1.0, (done_bytes + got) / tot) if tot else 0.0,
                             f"{label}: {(done_bytes + got) / 1e9:.2f} of {tot / 1e9:.2f} GB")
    part.replace(dest)
    return got


def _repo_files(repo):
    s = _session()
    r = s.get(f"{HF}/api/models/{repo}", params={"blobs": "true"}, timeout=30)
    r.raise_for_status()
    out = {}
    for sib in r.json().get("siblings", []):
        out[sib["rfilename"]] = int(sib.get("size") or (sib.get("lfs") or {}).get("size") or 0)
    return out


def fetch_repo(repo, wanted, target: Path, progress=None, cancel=None, label="Downloading"):
    """Download the wanted files of a Hugging Face repo into target/ (once)."""
    marker = target / ".complete"
    if marker.exists():
        return target
    try:
        listing = _repo_files(repo)
    except Exception as e:
        raise DownloadError(f"Couldn't reach the model server. Check your internet connection.\n({e})")
    files = [f for f in wanted if f in listing]
    total = sum(listing[f] for f in files)
    need_gb = total / 1e9 + 0.2
    if free_space_gb(target) < need_gb:
        raise DownloadError(
            f"Not enough free space for this download ({need_gb:.1f} GB needed) in\n{target}\n"
            "Free up space, or choose another models folder in Settings.")
    done = 0
    for f in files:
        dest = target / f
        if dest.exists() and listing[f] and dest.stat().st_size == listing[f]:
            done += listing[f]
            continue
        _download(f"{HF}/{repo}/resolve/main/{f}", dest, progress, cancel, label, done, total)
        done += listing[f]
    marker.write_text("ok")
    return target


# ---------------------------------------------------------------- Whisper

def _old_whisper_copy(name):
    """Earlier CleanCut versions stored models in the Hugging Face cache layout.
    Reuse them so nobody downloads 3 GB twice."""
    repo = WHISPER_REPOS.get(name, "")
    folder = "models--" + repo.replace("/", "--")
    roots = []
    la = os.environ.get("LOCALAPPDATA")
    if la:
        roots.append(Path(la) / "CleanCut" / "models")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    if os.name == "nt":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            roots.append(Path(f"{letter}:\\CleanCut Models"))
    for root in roots:
        try:
            for snap in glob.glob(str(root / folder / "snapshots" / "*")):
                p = Path(snap)
                if (p / "model.bin").exists() and (p / "config.json").exists() \
                        and (p / "tokenizer.json").exists():
                    return p
        except OSError:
            continue
    return None


def whisper_ready(name, models_dir) -> bool:
    return (Path(models_dir) / "whisper" / name / ".complete").exists() or bool(_old_whisper_copy(name))


def ensure_whisper(name, models_dir, progress=None, cancel=None) -> str:
    if Path(name).is_dir():
        return name
    local = Path(models_dir) / "whisper" / name
    if (local / ".complete").exists():
        return str(local)
    old = _old_whisper_copy(name)
    if old:
        return str(old)
    repo = WHISPER_REPOS.get(name)
    if not repo:
        raise DownloadError(f"Unknown speech model '{name}'.")
    fetch_repo(repo, WHISPER_FILES, local, progress, cancel, "Downloading speech model")
    return str(local)


# ---------------------------------------------------------------- CLIP (scene descriptions)

def clip_ready(models_dir) -> bool:
    return (Path(models_dir) / "clip" / ".complete").exists()


def ensure_clip(models_dir, progress=None, cancel=None) -> Path:
    return fetch_repo(CLIP_REPO, CLIP_FILES, Path(models_dir) / "clip", progress, cancel,
                      "Downloading scene model")


# ---------------------------------------------------------------- NVIDIA GPU pack

def nvidia_gpu_present() -> bool:
    try:
        r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True,
                           timeout=10, creationflags=NO_WINDOW)
        return r.returncode == 0 and "GPU" in r.stdout
    except Exception:
        return False


def gpu_pack_dir(models_dir) -> Path:
    return Path(models_dir) / "gpu"


def gpu_pack_ready(models_dir) -> bool:
    return (gpu_pack_dir(models_dir) / ".complete").exists()


def gpu_pack_plan():
    """[(url, filename, size)] of NVIDIA's Windows packages to fetch."""
    s = _session()
    wheels = []
    for pkg in GPU_WHEELS:
        meta = s.get(f"https://pypi.org/pypi/{pkg}/json", timeout=30).json()
        urls = meta.get("urls", [])
        if pkg == "nvidia-cudnn-cu12" and not meta["info"]["version"].startswith("9."):
            rel = sorted((v for v in meta["releases"] if v.startswith("9.")),
                         key=lambda v: [int(x) for x in v.split(".") if x.isdigit()])
            urls = meta["releases"][rel[-1]] if rel else urls
        win = [u for u in urls if u["filename"].endswith("win_amd64.whl")]
        if not win:
            raise DownloadError(f"No Windows package found for {pkg}.")
        wheels.append((win[0]["url"], win[0]["filename"], int(win[0].get("size") or 0)))
    return wheels


def ensure_gpu_pack(models_dir, progress=None, cancel=None) -> Path:
    """NVIDIA's CUDA libraries (cuBLAS, cuDNN) that speech recognition needs on the GPU.
    Fetched from NVIDIA's official Python packages; only the DLLs are kept."""
    target = gpu_pack_dir(models_dir)
    if (target / ".complete").exists():
        return target
    wheels = gpu_pack_plan()
    total = sum(w[2] for w in wheels)
    if free_space_gb(target) < total / 1e9 * 2 + 0.5:
        raise DownloadError(f"Not enough free space for the GPU pack in {target}.")
    target.mkdir(parents=True, exist_ok=True)
    done = 0
    for url, fname, size in wheels:
        tmp = target / fname
        _download(url, tmp, progress, cancel, "Downloading GPU speed-up pack", done, total)
        done += size
        with zipfile.ZipFile(tmp) as z:
            for member in z.namelist():
                if member.lower().endswith(".dll") and "/bin/" in member.replace("\\", "/"):
                    with z.open(member) as src, open(target / Path(member).name, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        tmp.unlink(missing_ok=True)
    (target / ".complete").write_text(json.dumps([w[1] for w in wheels]))
    return target


def add_gpu_dll_dirs(models_dir):
    """Make the GPU pack (and any pip-installed NVIDIA libs) visible to CTranslate2."""
    if os.name != "nt":
        return
    dirs = [gpu_pack_dir(models_dir)]
    try:
        import site
        for root in list(site.getsitepackages()) + [site.getusersitepackages()]:
            dirs += list((Path(root) / "nvidia").glob("*/bin"))
    except Exception:
        pass
    for d in dirs:
        if Path(d).is_dir():
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
