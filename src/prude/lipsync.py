"""Lip-sync for replaced words (EXPERIMENTAL, beta).

Uses MuseTalk (MIT-licensed code and weights, free for any use) that you install yourself:
it needs an NVIDIA card, PyTorch and several GB of models, so it can't ship inside this app.
Only the short stretch of picture around each replaced word is sent to MuseTalk; the result
is laid back over the movie. If anything goes wrong the audio-replaced copy is kept as it is."""

import shutil
import subprocess
from pathlib import Path

from .media import MediaError, ffmpeg, probe, run, run_ffmpeg
from .paths import NO_WINDOW

CONTEXT = 0.3                  # seconds of picture on each side of the word
TIMEOUT = 20 * 60


def install(settings):
    """(python, folder) when a MuseTalk install is set up, else None."""
    d = Path(settings.musetalk_dir) if getattr(settings, "musetalk_dir", "") else None
    if not d or not (d / "scripts" / "inference.py").exists():
        return None
    return (settings.musetalk_python or "python"), d


def face_present(video, t):
    """Is there a face on screen at time t? (Face finder that ships with OpenCV.)"""
    try:
        import cv2
        import numpy as np
        r = subprocess.run([ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{max(0, t):.3f}", "-i", str(video),
                            "-frames:v", "1", "-vf", "scale=640:-2,format=gray", "-f", "rawvideo", "-"],
                           capture_output=True, creationflags=NO_WINDOW)
        if not r.stdout:
            return False
        probe_info = probe(video)
        w = 640
        h = len(r.stdout) // w
        img = np.frombuffer(r.stdout[:w * h], np.uint8).reshape(h, w)
        det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        return len(det.detectMultiScale(img, 1.1, 5, minSize=(40, 40))) > 0
    except Exception:                                      # noqa: BLE001
        return True                                        # can't tell: let MuseTalk decide


def run_musetalk(py, folder, clip, wav, work, log=print):
    cfg = work / "task.yaml"
    cfg.write_text(f'task_0:\n  video_path: "{Path(clip).as_posix()}"\n  audio_path: "{Path(wav).as_posix()}"\n',
                   encoding="utf-8")
    result_dir = work / "result"
    result_dir.mkdir(exist_ok=True)
    cmd = [py, "-m", "scripts.inference", "--inference_config", str(cfg), "--result_dir", str(result_dir),
           "--version", "v15"]
    r = subprocess.run(cmd, cwd=str(folder), capture_output=True, text=True, timeout=TIMEOUT,
                       creationflags=NO_WINDOW)
    outs = [p for p in result_dir.rglob("*.mp4")]
    if r.returncode != 0 or not outs:
        raise MediaError(f"MuseTalk failed: {(r.stderr or r.stdout)[-300:]}")
    return outs[0]


def apply(info, project, filters, settings, res, progress=None, cancel=None, log=print, face=None, runner=None):
    """Lip-sync the replaced words in the finished copy (res['output']). Never makes things worse:
    returns res with a note, or the untouched copy if it can't."""
    from .media import Cancelled
    from .render import video_encoder_args
    face = face or face_present
    runner = runner or run_musetalk
    out = Path(res["output"])
    warn = res.setdefault("warnings", [])
    inst = install(settings)
    if inst is None:
        warn.append("Lip-sync (experimental) isn't set up (Settings → Advanced Editing), so the words were "
                    "replaced without matching the lips.")
        return res
    slots = [tuple(s) for s in res.get("replacement_slots", [])]
    if not slots:
        return res
    work = out.parent / f".{out.stem}.prude-lips"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        got = probe(out)
        pieces = []
        for n, (a, b) in enumerate(slots):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if progress:
                progress(n / len(slots), f"Matching lips ({n + 1} of {len(slots)}, experimental)")
            wa, wb = max(0.0, a - CONTEXT), min(got.duration, b + CONTEXT)
            if not face(out, (a + b) / 2):
                log(f"No face on screen at {a:.1f} s; lips left as they are.")
                continue
            d = work / f"w{n}"
            d.mkdir()
            clip, wav = d / "clip.mp4", d / "audio.wav"
            r = run([ffmpeg(), "-v", "error", "-nostdin", "-y", "-ss", f"{wa:.3f}", "-t", f"{wb - wa:.3f}",
                     "-i", str(out), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264", "-crf", "12",
                     "-preset", "fast", "-c:a", "aac", str(clip)])
            if r.returncode != 0:
                continue
            run([ffmpeg(), "-v", "error", "-nostdin", "-y", "-i", str(clip), "-vn", "-ac", "1", "-ar", "16000",
                 str(wav)])
            try:
                made = runner(*inst, clip, wav, d, log)
            except MediaError as e:
                log(str(e))
                continue
            pieces.append((wa, wb, made))
        if not pieces:
            warn.append("Lip-sync (experimental) matched no words; the copy has replaced audio only.")
            return res
        inputs, graph, cur = [], [], "[0:v:0]"
        for k, (wa, wb, made) in enumerate(pieces, start=1):
            inputs += ["-i", str(made)]
            graph.append(f"[{k}:v:0]scale={got.width}:{got.height},setsar=1,setpts=PTS-STARTPTS+{wa:.3f}/TB[l{k}]")
            graph.append(f"{cur}[l{k}]overlay=0:0:enable='between(t,{wa:.3f},{wb:.3f})':eof_action=pass[o{k}]")
            cur = f"[o{k}]"
        partial = out.with_name(f"{out.stem}.lips{out.suffix}")
        vargs, enc = video_encoder_args(got, "high", settings.device)
        cmd = [ffmpeg(), "-y", "-i", str(out), *inputs, "-filter_complex", ";".join(graph), "-map", cur,
               "-map", "0:a?", "-map", "0:s?", "-c", "copy", *vargs, "-map_metadata", "0", str(partial)]
        run_ffmpeg(cmd, got.duration, (lambda f, m: progress(f, "Putting the lips back in the movie")) if progress
                   else None, cancel, "Lip-sync", fps=got.fps)
        import os
        os.replace(partial, out)
        res["size_bytes"] = out.stat().st_size
        res["how"] += " + lip-sync (experimental)"
        warn.append(f"Lip-sync (experimental): {len(pieces)} of {len(slots)} replaced word(s) matched; the video "
                    f"was re-encoded on the {enc}.")
    except Cancelled:
        raise
    except Exception as e:                                 # noqa: BLE001 - keep the audio-replaced copy
        log(f"Lip-sync failed ({e}); keeping the copy with replaced audio only.")
        warn.append("Lip-sync (experimental) failed; the copy has replaced audio only.")
        Path(out.with_name(f"{out.stem}.lips{out.suffix}")).unlink(missing_ok=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return res
