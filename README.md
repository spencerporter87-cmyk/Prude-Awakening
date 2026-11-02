# Prude Awakening

<img src="assets/icon-512.png" width="128" align="right" alt="Prude Awakening icon">

Mutes bad language and removes scenes you'd rather not see — automatically, on your own PC.

1. **Choose** what to filter: language categories, your own words, nudity, sex scenes,
   violence, gore, or anything you describe in plain English ("smoking", "a large spider").
2. **Scan**: the app listens to the dialogue (Whisper speech recognition) and watches the
   picture (NudeNet + CLIP), all offline after a one-time model download.
3. **Review** every mute and scene, with ▶ preview, before anything is changed.
4. **Save** a clean copy. With only mutes, the video is copied bit-for-bit (no quality loss);
   cut or blacked-out scenes are re-encoded at very high quality (NVIDIA NVENC when available).
   Subtitles are cleaned (★★★★) and new subtitles are written from the dialogue.

Your movies, transcripts and choices never leave your computer.

## Install

Download **Prude-Awakening-Setup-x.y.z.exe** from the latest
[Actions build](../../actions) (artifact *Prude-Awakening-Setup*) or from
[Releases](../../releases), run it, and open Prude Awakening from the Start menu or desktop.
Windows may show "Windows protected your PC" because the app isn't code-signed:
click **More info → Run anyway**.

The first scan downloads the speech model (0.5–3 GB). On PCs with an NVIDIA card the app
offers a one-time 1.2 GB "speed-up pack" (NVIDIA cuBLAS/cuDNN) that makes scanning ~10× faster.

## For developers

```
pip install -r requirements.txt
python -m prude.gui              # the app   (run from src/)
python -m prude movie.mkv        # command line
python -m pytest tests
```

Every push builds the Windows installer on GitHub Actions, runs unit tests, an end-to-end test
with real speech and real models, and a screenshot tour of every screen.

Only use Prude Awakening on video files you own that aren't copy-protected, for your own
household's viewing.
