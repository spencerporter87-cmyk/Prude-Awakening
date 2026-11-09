"""Advanced Editing (BETA): say something cleaner where a word was silenced.

How it stays safe: the silence is always made first. A replacement is only laid over it, so if a
voice can't be made (or anything else goes wrong) the word simply stays silent. Unfiltered
dialogue is never put back.

What "matching the actor" means in this version: the replacement voice is picked by male/female
pitch, shifted to the pitch the actor speaks at in that scene, fitted to the length of the gap,
and set to the same loudness. It does NOT copy a real person's voice (no voice cloning)."""

import hashlib
import json
import re
import subprocess
import wave
from pathlib import Path

import numpy as np

from .media import MediaError, ffmpeg, run
from .paths import NO_WINDOW, app_data_dir
from .words import STARS, normalize, star_word

BETA = True
SR = 24000
ENGINES = {"windows": "Windows voice (built in, works offline)",
           "elevenlabs": "ElevenLabs (online, uses your own credits)"}
# Ready-made library voices (not copies of anyone), one lower and one higher.
ELEVEN_VOICES = {"male": "pNInz6obpgDQGcFmaJgB", "female": "21m00Tcm4TlvDq8ikWAM"}
MODES = {"mute": "Silence (default)", "replace": "Replace with a spoken clean word",
         "replace_lipsync": "Replace + match the lips (experimental)"}


# ------------------------------------------------------------------ what to say
def clean_label(words: str) -> str:
    """'hell? (heard as "hello")' -> 'hell'"""
    w = re.sub(r"\s*\(.*?\)\s*", " ", str(words or ""))
    return " ".join(w.split()).strip(" .,!?;:\"'")


def suggest(words: str) -> str:
    """A cleaner word for the matched word ('' when there isn't an obvious one)."""
    label = clean_label(words)
    if not label:
        return ""
    sub = star_word(label, "word")
    return "" if sub == STARS else sub


def text_for(mute) -> str:
    """What to say for a mute: the user's own text wins; "" means leave it silent."""
    if "replace_text" in mute:
        return (mute.get("replace_text") or "").strip()
    return suggest(mute.get("words", ""))


# ------------------------------------------------------------------ measuring the voice
def read_wav(path):
    with wave.open(str(path), "rb") as w:
        n, ch, sr, width = w.getnframes(), w.getnchannels(), w.getframerate(), w.getsampwidth()
        raw = w.readframes(n)
    if width != 2:
        raise MediaError("Unexpected audio format from the voice.")
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def write_wav(path, x, sr):
    y = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(y.tobytes())


def pitch_and_level(x, sr):
    """(median pitch in Hz or 0, loudness in dB RMS of the speaking parts)."""
    x = np.asarray(x, np.float32)
    n = int(sr * 0.04)
    hop = n // 2
    lo, hi = int(sr / 400), int(sr / 70)
    pitches, rms = [], []
    for i in range(0, max(0, len(x) - n), hop):
        f = x[i:i + n] - x[i:i + n].mean()
        e = float(np.sqrt(np.mean(f ** 2)))
        if e < 0.01:
            continue
        rms.append(e)
        ac = np.correlate(f, f, "full")[len(f) - 1:]
        if ac[0] <= 0 or hi >= len(ac):
            continue
        seg = ac[lo:hi]
        k = int(np.argmax(seg))
        if seg[k] / ac[0] > 0.45:
            pitches.append(sr / (lo + k))
    f0 = float(np.median(pitches)) if len(pitches) >= 5 else 0.0
    db = 20 * np.log10(np.sqrt(np.mean(np.square(rms))) + 1e-9) if rms else -40.0
    return f0, float(db)


def actor_voice(info, track, start, end, mutes=(), window=6.0):
    """Pitch, loudness and gender guess of the speaker around (start, end), skipping silenced words."""
    a = max(0.0, start - window)
    r = subprocess.run([ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{a:.3f}",
                        "-t", f"{end + window - a:.3f}", "-i", str(info.path), "-map", f"0:a:{track}",
                        "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                       capture_output=True, creationflags=NO_WINDOW)
    x = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0 if r.stdout else np.zeros(0, np.float32)
    keep = np.ones(len(x), bool)
    for ms, me in mutes:
        keep[max(0, int((ms - a) * 16000)):max(0, int((me - a) * 16000))] = False
    x = x[keep] if keep.any() else x
    f0, db = pitch_and_level(x, 16000) if len(x) > 8000 else (0.0, -30.0)
    return {"f0": f0, "db": db, "gender": "female" if f0 >= 165 else "male"}


# ------------------------------------------------------------------ voices
def _cache_dir():
    d = app_data_dir() / "tts-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


def synth_windows(text, gender, out):
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"try {{ $s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::{gender.capitalize()}) }} catch {{}}; "
          "$s.Rate = 0; "
          f"$s.SetOutputToWaveFile('{out}'); $s.Speak([Console]::In.ReadToEnd()); $s.Dispose()")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], input=text, capture_output=True,
                       text=True, timeout=60, creationflags=NO_WINDOW)
    if r.returncode != 0 or not Path(out).exists():
        raise MediaError(f"The Windows voice couldn't speak: {r.stderr.strip()[:200]}")


def synth_elevenlabs(text, gender, out, api_key, post=None):
    import requests
    post = post or requests.post
    r = post(f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICES[gender]}",
             params={"output_format": "pcm_24000"},
             headers={"xi-api-key": api_key, "Content-Type": "application/json"},
             json={"text": text, "model_id": "eleven_multilingual_v2"}, timeout=60)
    if r.status_code != 200:
        raise MediaError(f"ElevenLabs refused ({r.status_code}): {r.text[:160]}")
    write_wav(out, np.frombuffer(r.content, np.int16).astype(np.float32) / 32768.0, 24000)


def synthesize(text, gender, engine, settings=None, spend_ok=False, log=print, hook=None):
    """Plain voice clip for the text (cached). Returns (wav path, engine really used)."""
    use = engine
    key_ok = bool(getattr(settings, "elevenlabs_key", "")) and spend_ok
    if use == "elevenlabs" and not key_ok:
        log("ElevenLabs wasn't confirmed or has no key; using the Windows voice instead.")
        use = "windows"
    out = _cache_dir() / f"{_key(use, gender, text.lower())}.wav"
    if out.exists():
        return out, use
    tmp = out.with_suffix(".tmp.wav")
    try:
        if hook:                                          # tests
            hook(text, gender, tmp)
        elif use == "elevenlabs":
            synth_elevenlabs(text, gender, tmp, settings.elevenlabs_key)
        else:
            synth_windows(text, gender, tmp)
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return out, use


# ------------------------------------------------------------------ fitting it in
def _atempo(r):
    parts = []
    while r > 2.0:
        parts.append(2.0)
        r /= 2.0
    while r < 0.5:
        parts.append(0.5)
        r /= 0.5
    parts.append(r)
    return ",".join(f"atempo={p:.4f}" for p in parts)


def fit(raw, voice, slot, out):
    """Shift the voice toward the actor's pitch, fit the gap, match loudness, soften the edges.
    Returns the length in seconds."""
    x, sr = read_wav(raw)
    spk_f0, _ = pitch_and_level(x, sr)
    ratio = 1.0
    if voice.get("f0") and spk_f0:
        ratio = float(np.clip(voice["f0"] / spk_f0, 0.75, 1.35))
    dur = len(x) / sr / ratio * 1.0
    tempo = float(np.clip(dur / max(0.15, slot), 1.0, 1.7)) if dur > slot else 1.0
    tmp = Path(out).with_suffix(".pre.wav")
    # pitch: change the playback rate, then undo the speed change with tempo
    filt = f"asetrate={int(sr * ratio)},aresample={SR},{_atempo(1.0 / ratio / tempo)},aresample={SR}"
    r = run([ffmpeg(), "-v", "error", "-nostdin", "-y", "-i", str(raw), "-af", filt, "-ac", "1", "-ar", str(SR),
             str(tmp)])
    if r.returncode != 0:
        raise MediaError(f"Couldn't fit the voice: {r.stderr[-200:]}")
    y, sr2 = read_wav(tmp)
    tmp.unlink(missing_ok=True)
    # trim silence at both ends
    idx = np.where(np.abs(y) > 0.01)[0]
    if len(idx):
        y = y[max(0, idx[0] - int(0.01 * sr2)):idx[-1] + int(0.02 * sr2)]
    _, have = pitch_and_level(y, sr2)
    gain = 10 ** (np.clip(voice.get("db", have) - have, -18, 12) / 20)
    y = np.clip(y * gain, -0.98, 0.98)
    f = int(0.012 * sr2)
    if len(y) > 2 * f:
        y[:f] *= np.linspace(0, 1, f)
        y[-f:] *= np.linspace(1, 0, f)
    write_wav(out, y, sr2)
    return len(y) / sr2


def prepare(project, info, filters, settings, work: Path, log=print, cancel=None, hook=None):
    """Make a clip for every ticked mute that has something to say.
    Returns (clips [{start, end, wav, text, id, engine}], failed [mute dicts], characters_spoken)."""
    from .media import Cancelled
    work.mkdir(parents=True, exist_ok=True)
    clips, failed, chars = [], [], 0
    active = project.active_mutes()
    ranges = [(m["start"], m["end"]) for m in active]
    cache = {}
    for m in active:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        text = text_for(m)
        if not text:
            continue
        try:
            key = (round(m["start"] / 12), )
            if key not in cache:
                cache[key] = actor_voice(info, project.audio_track, m["start"], m["end"], ranges)
            voice = cache[key]
            raw, used = synthesize(text, voice["gender"], getattr(filters, "advanced", {}).get("engine", "windows"),
                                   settings, getattr(project, "spend_ok", False), log, hook)
            if used == "elevenlabs":
                chars += len(text)
            slot = max(0.2, m["end"] - m["start"] - 0.05)
            wav = work / f"r{len(clips):04d}.wav"
            length = fit(raw, voice, slot, wav)
            start = m["start"] + max(0.0, (m["end"] - m["start"] - length) / 2)
            clips.append({"id": m.get("id"), "start": round(start, 3), "end": round(start + length, 3),
                          "wav": str(wav), "text": text, "engine": used})
        except Exception as e:                            # noqa: BLE001 - the silence stays
            if cancel is not None and cancel.is_set():
                raise
            log(f"Couldn't say '{text}' at {m['start']:.1f} s ({e}); it stays silent.")
            failed.append(m)
    return clips, failed, chars


def elevenlabs_characters(project):
    """Characters that would be sent (cached ones cost nothing)."""
    n = 0
    for m in project.active_mutes():
        t = text_for(m)
        if t and not any(_cache_dir().glob(f"{_key('elevenlabs', g, t.lower())}.wav") for g in ("male", "female")):
            n += len(t)
    return n


def preview(text, gender, engine, settings, spend_ok=False):
    """A quick sample (plain voice) for the Review window."""
    return synthesize(text, gender, engine, settings, spend_ok)[0]
