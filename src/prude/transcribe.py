"""Speech recognition (Whisper) with word timestamps, progress and cancel."""

import time
import wave
from pathlib import Path

from . import models
from .media import Cancelled, channel_layouts, ffmpeg, run_ffmpeg


def extract_dialogue_wav(info, track, dest: Path, progress=None, cancel=None):
    """16 kHz mono WAV of the chosen track; the center channel only when there is one
    (cleaner dialogue = better recognition)."""
    t = info.audio[track]
    chans = channel_layouts().get(t.layout, [])
    cmd = [ffmpeg(), "-y", "-i", str(info.path), "-map", f"0:a:{track}", "-vn", "-sn", "-dn"]
    if "FC" in chans and len(chans) > 1:
        cmd += ["-af", "pan=mono|c0=FC"]
    cmd += ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest)]
    run_ffmpeg(cmd, info.duration, progress, cancel, "Pulling out the dialogue")
    return dest


def load_wav(path):
    import numpy as np
    with wave.open(str(path), "rb") as wf:
        data = wf.readframes(wf.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def default_model(device):
    return "large-v3" if device == "cuda" else "small.en"


def detect_language(wav, duration, models_dir, device="auto", samples=8, progress=None, cancel=None,
                    log=print):
    """Which language is spoken? Listens to a few 30-second samples spread through the movie
    with a small multilingual model. Returns (main_language, english_share, per_sample list)."""
    models.add_gpu_dll_dirs(models_dir)
    from faster_whisper import WhisperModel
    audio = load_wav(wav)
    path = models.ensure_whisper("tiny", models_dir, progress, cancel)
    dev = "cuda" if device != "cpu" and cuda_available() else "cpu"
    try:
        model = WhisperModel(path, device=dev, compute_type="float16" if dev == "cuda" else "int8")
    except Exception:
        model = WhisperModel(path, device="cpu", compute_type="int8")
    sr, n = 16000, len(audio)
    win = 30 * sr
    found = []
    for k in range(samples):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        start = int((k + 0.5) / samples * max(0, n - win))
        chunk = audio[start:start + win]
        if len(chunk) < sr * 5 or float(abs(chunk).mean()) < 1e-4:
            continue
        try:
            _segs, info = model.transcribe(chunk, language=None, vad_filter=True, beam_size=1)
        except Exception as e:
            log(f"Language check skipped a sample: {e}")
            continue
        if info.language and info.language_probability >= 0.5:
            found.append(info.language)
        if progress:
            progress((k + 1) / samples, "Checking which language is spoken")
    if not found:
        return "en", 1.0, found
    share = found.count("en") / len(found)
    main = max(set(found), key=found.count)
    return main, share, found


def transcribe(wav, duration, models_dir, language="en", model_name="", device="auto",
               progress=None, cancel=None, log=print):
    """Returns (words, model_used, device_used). words = [{"w","s","e","p"}]."""
    models.add_gpu_dll_dirs(models_dir)
    from faster_whisper import WhisperModel

    if device == "auto":
        device = "cuda" if cuda_available() else "cpu"
    audio = load_wav(wav)

    def go(dev):
        name = model_name or default_model(dev)
        if language != "en" and name.endswith(".en"):
            name = name[:-3]
        path = models.ensure_whisper(name, models_dir,
                                     (lambda f, m: progress(f, m)) if progress else None, cancel)
        if progress:
            progress(0.0, f"Loading speech model ({'graphics card' if dev == 'cuda' else 'processor'})…")
        model = WhisperModel(path, device=dev, compute_type="float16" if dev == "cuda" else "int8")
        segments, _ = model.transcribe(audio, language=language, word_timestamps=True,
                                       vad_filter=True, condition_on_previous_text=False,
                                       beam_size=5)
        words, t0 = [], time.time()
        for seg in segments:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            for w in (seg.words or []):
                words.append({"w": w.word, "s": round(w.start, 3), "e": round(w.end, 3),
                              "p": round(w.probability, 3)})
            if progress and duration:
                frac = min(1.0, seg.end / duration)
                elapsed = time.time() - t0
                left = elapsed / max(frac, 1e-3) * (1 - frac)
                progress(frac, f"Listening… about {max(1, int(left // 60))} min left")
        return words, name

    try:
        words, name = go(device)
        return words, name, device
    except Cancelled:
        raise
    except models.DownloadError:
        raise
    except Exception as e:
        if device == "cuda":
            log(f"Graphics card speech recognition failed ({str(e)[:200]}); using the processor.")
            words, name = go("cpu")
            return words, name, "cpu"
        raise
