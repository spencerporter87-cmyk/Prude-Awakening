"""End-to-end test on Windows with real AI models:
Windows text-to-speech says some bad words over a test video, then Prude Awakening
listens (Whisper), finds them, watches the video (NudeNet + CLIP), and renders."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import level  # noqa: E402
from prude import models  # noqa: E402
from prude.cli import main as cli  # noqa: E402
from prude.media import ffmpeg, probe  # noqa: E402

SPEECH = ("Hello there, my friend. <silence msec='700'/> What the hell are you doing? "
          "<silence msec='700'/> I said, god damn it. <silence msec='700'/> "
          "That is some bullshit. <silence msec='700'/> Have a wonderful day.")


def tts(wav: Path):
    ps = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = -1
$s.SetOutputToWaveFile('{wav}')
$s.SpeakSsml("<speak version='1.0' xml:lang='en-US'>{SPEECH}</speak>")
$s.Dispose()
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def main(out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp())
    wav = work / "speech.wav"
    tts(wav)
    movie = out_dir / "e2e movie.mkv"
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=1280x720:r=24:d=30", "-i", str(wav), "-af", "apad",
                    "-map", "0:v", "-map", "1:a", "-shortest", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "22", "-c:a", "ac3", "-b:a", "384k", "-metadata:s:a:0", "language=eng",
                    str(movie)], check=True)
    summary = out_dir / "e2e-summary.json"
    rc = cli([str(movie), "--words", "strong,blasphemy,mild", "--model", "tiny.en", "--device", "cpu",
              "--scenes", "nudity,violence", "--describe", "a colorful television test pattern",
              "--action", "black", "--models-dir", str(out_dir.parent / "models"),
              "--summary-json", str(summary)])
    assert rc == 0, rc
    s = json.loads(summary.read_text())
    words = " ".join(m["words"].lower() for m in s["mutes"])
    print("MUTES:", words)
    print("SCENES:", [(x["label"], x["start"], x["end"], x["score"]) for x in s["scenes"]])
    hits = sum(w in words for w in ("hell", "damn", "bullshit"))
    assert s["transcript_words"] >= 15, s["transcript_words"]
    assert hits >= 2, f"expected bad words to be found, got: {words}"
    assert not [x for x in s["scenes"] if x["kind"] == "nudity"], "test pattern flagged as nudity"
    assert [x for x in s["scenes"] if x["kind"].startswith("custom:")], "CLIP didn't find the test pattern"
    out = Path(s["result"]["output"])
    assert out.exists()
    info = probe(out)
    assert abs(info.duration - probe(movie).duration) < 0.6
    m = s["mutes"][0]
    mid = (m["start"] + m["end"]) / 2
    assert level(out, mid - 0.05, 0.1) < -80, "word not silenced"
    plan = models.gpu_pack_plan()
    assert len(plan) == 3 and all(p[1].endswith("win_amd64.whl") for p in plan), plan
    print("GPU pack plan:", [(p[1], round(p[2] / 1e6)) for p in plan])
    print("E2E OK")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out/e2e")
