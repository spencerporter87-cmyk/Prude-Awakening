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
   Subtitles are cleaned (`****`, `f***` or a cleaner word) and new subtitles are written
   from the dialogue. Picture-based subtitles (Blu-ray/DVD) are read with offline text
   recognition and cleaned too. In English movies the subtitles double-check the listening
   and catch words it missed.
   Subtitles burned into the picture are read too (automatically for foreign-language films
   without subtitle tracks); swear words there are blurred.
5. **Fast cuts** (optional): scenes are removed without re-encoding the picture; each cut lands
   on the next keyframe. HDR10 movies keep their HDR brightness information when re-encoded.
6. **Advanced Editing (beta, off by default)**: a silenced word can be replaced by a clean spoken one
   ("hell" → "heck") in a ready-made voice matched to the speaker's pitch and loudness. The silence
   is always made first, so a failure never brings back the original word. Optional ElevenLabs voices
   (your key, confirmed before spending) and experimental MuseTalk lip-sync (your own install).
7. **Foreign-language films** (e.g. Apocalypto): the dialogue is left alone, the English
   subtitles are cleaned and set to show automatically.

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

Every push runs the unit tests on Linux (`.github/workflows/tests.yml`, a few minutes). The full Windows
build (installer, packaged self-test, end-to-end test with real speech and models, screenshot tour) is slower,
so it runs only on request: Actions → *Build and test (Windows)* → *Run workflow*, or push a tag like `v3.6.0`
to publish a release.

To build the installer on your own PC instead (no GitHub minutes), install Python 3.12 and Inno Setup 6, then run
`.\packaging\build-local.ps1` in PowerShell. The result is `dist\Prude-Awakening-Setup-<version>.exe`.

Only use Prude Awakening on video files you own that aren't copy-protected, for your own
household's viewing.
