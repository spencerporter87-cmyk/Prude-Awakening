import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PRUDE_DATA_DIR", str(tmp_path / "appdata"))


def ff(*args):
    from prude.media import ffmpeg
    r = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    srt = d / "dirty.srt"
    srt.write_text("1\n00:00:04,800 --> 00:00:05,600\nWhat the hell?\n\n"
                   "2\n00:00:09,800 --> 00:00:11,000\nGod damn it, get the\nshit out of here!\n\n"
                   "3\n00:00:14,000 --> 00:00:15,000\nHello there.\n")
    tones = []
    for i, f in enumerate([300, 400, 1000, 60, 500, 600]):
        tones += ["-f", "lavfi", "-i", f"sine=f={f}:d=20"]
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=20", *tones, "-i", str(srt),
       "-filter_complex", "[1][2][3][4][5][6]join=inputs=6:channel_layout=5.1[a]",
       "-map", "0:v", "-map", "[a]", "-map", "7", "-c:v", "libx264", "-preset", "ultrafast",
       "-crf", "23", "-c:a", "ac3", "-b:a", "448k", "-c:s", "srt",
       "-metadata:s:a:0", "language=eng", "-metadata:s:s:0", "language=eng", str(d / "movie51.mkv"))
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=20", "-f", "lavfi", "-i", "sine=f=1000:d=20",
       "-ac", "2", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "libmp3lame", "-b:a", "192k",
       str(d / "old.avi"))
    # Blu-ray style picture subtitles (PGS), English
    from pgs import write_sup
    write_sup(d / "pic.sup", PIC_CUES)
    ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=24:d=20", "-f", "lavfi", "-i", "sine=f=500:d=20",
       "-i", str(d / "pic.sup"), "-map", "0:v", "-map", "1:a", "-map", "2", "-c:v", "libx264",
       "-preset", "ultrafast", "-c:a", "aac", "-c:s", "copy", "-copyts",
       "-metadata:s:s:0", "language=eng", "-metadata:s:a:0", "language=spa", str(d / "pgs.mkv"))
    return d


PIC_CUES = [(2.0, 4.0, "Run, they are coming!"), (6.0, 8.5, "What the fuck is that?"),
            (12.0, 14.0, "Get the shit\nout of here!")]


WORDS = [("Well", 1.0, 1.3), (" what", 4.8, 5.0), (" the", 5.0, 5.1), (" hell?", 5.1, 5.5),
         (" God", 9.8, 10.1), (" damn", 10.1, 10.4), (" it!", 10.4, 10.6), (" class", 12.0, 12.4),
         (" Hello,", 14.0, 14.3), (" shitty", 15.0, 15.4), (" Oh", 17.0, 17.1), (" my", 17.2, 17.3),
         (" god.", 17.4, 17.7), (" hello", 18.5, 18.8)]


@pytest.fixture
def fake_words():
    return [{"w": a, "s": b, "e": c, "p": 0.9} for a, b, c in WORDS]


def level(path, t, dur=0.1, pan=None):
    """RMS dB of the audio at time t (whole mix or one channel)."""
    from prude.media import ffmpeg
    af = (f"pan=mono|c0={pan}," if pan else "") + "astats"
    r = subprocess.run([ffmpeg(), "-hide_banner", "-ss", str(t), "-t", str(dur), "-i", str(path),
                        "-map", "0:a:0", "-af", af, "-f", "null", "-"], capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "RMS level dB" in line:
            v = line.split(":")[-1].strip()
            return -200.0 if v == "-inf" else float(v)
    return None


def video_md5(path):
    from prude.media import ffmpeg
    r = subprocess.run([ffmpeg(), "-v", "error", "-i", str(path), "-map", "0:v:0", "-c", "copy",
                        "-f", "md5", "-"], capture_output=True, text=True)
    return r.stdout.strip()
