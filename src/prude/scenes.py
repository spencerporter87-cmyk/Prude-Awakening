"""Watching the movie: sample frames, score them for nudity and for scene
descriptions (violence, gore, sexual content, your own descriptions), then turn
the scores into scenes snapped to the film's camera cuts."""

import json
from functools import lru_cache
from pathlib import Path

from . import models
from .media import Cancelled, ffmpeg, run, run_ffmpeg

SCENE_CATEGORIES = [
    ("nudity", "Nudity", "Exposed private parts (detected directly)"),
    ("sexual", "Sex scenes", "Sexual activity, sensual scenes in bed"),
    ("violence", "Violence", "Fights, shootings, stabbings, attacks"),
    ("gore", "Blood and gore", "Bloody wounds, gore, dead bodies"),
]
SCENE_NAMES = {k: n for k, n, _ in SCENE_CATEGORIES}

PROMPTS = {
    "sexual": ["a couple having sex in bed", "two naked people in bed together",
               "a sex scene in a movie", "people kissing passionately on a bed",
               "a woman in lingerie in a bedroom"],
    "violence": ["people in a violent fist fight", "a person being shot with a gun",
                 "a person being stabbed with a knife", "someone being beaten up",
                 "a violent brutal attack", "a shootout with guns firing"],
    "gore": ["a bloody wound", "a person covered in blood", "a gory dead body",
             "blood splattered everywhere", "a severed body part"],
}
NEUTRAL = ["people talking in a room", "a person walking outside", "a landscape view",
           "a car driving on a road", "people eating dinner at a table",
           "a close-up of a person's face", "a busy city street", "an office with people working",
           "white text on a black screen", "a dark night scene", "people dancing at a party",
           "a person holding a phone", "children playing", "a kitchen", "a living room",
           "people laughing together", "a person sleeping in bed alone", "a sports game",
           "an animated cartoon scene", "a crowd of people", "a person driving a car",
           "a couple hugging", "a beach with people in swimsuits", "a hospital room",
           "soldiers marching", "a person holding a gun", "a police officer"]
NUDE_CLASSES = {"FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED",
                "MALE_GENITALIA_EXPOSED", "BUTTOCKS_EXPOSED", "ANUS_EXPOSED"}
# sensitivity 1 (only obvious) .. 5 (flag anything suspicious)
NUDE_THRESH = {1: 0.75, 2: 0.6, 3: 0.45, 4: 0.35, 5: 0.25}
CLIP_THRESH = {1: 0.7, 2: 0.55, 3: 0.42, 4: 0.32, 5: 0.24}
FPS = 1.0


@lru_cache(maxsize=1)
def _filters():
    return run([ffmpeg(), "-hide_banner", "-filters"]).stdout


def extract_frames(info, frames_dir: Path, progress=None, cancel=None, fps=FPS):
    frames_dir.mkdir(parents=True, exist_ok=True)
    done = frames_dir / ".complete"
    if done.exists():
        return sorted(frames_dir.glob("*.jpg"))
    for old in frames_dir.glob("*.jpg"):
        old.unlink()
    vf = f"fps={fps},scale=384:-2"
    if info.is_hdr and " zscale " in _filters() and " tonemap " in _filters():
        vf += (",zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
               "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv")
    vf += ",format=yuvj420p"
    cmd = [ffmpeg(), "-y", "-hwaccel", "auto", "-i", str(info.path), "-map", "0:v:0",
           "-an", "-sn", "-dn", "-vf", vf, "-q:v", "5", str(frames_dir / "%06d.jpg")]
    try:
        run_ffmpeg(cmd, info.duration, progress, cancel, "Watching the movie")
    except Exception:
        if cancel is not None and cancel.is_set():
            raise
        cmd.remove("-hwaccel")
        cmd.remove("auto")
        run_ffmpeg(cmd, info.duration, progress, cancel, "Watching the movie")
    done.write_text("ok")
    return sorted(frames_dir.glob("*.jpg"))


def frame_time(path: Path, fps=FPS):
    return (int(path.stem) - 1) / fps


def shot_boundaries(frames, fps=FPS, progress=None, cancel=None):
    """Times where the picture changes a lot between samples (camera cuts)."""
    import cv2
    out, prev = [], None
    for k, p in enumerate(frames):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        img = cv2.imread(str(p))
        if img is None:
            continue
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        h = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(h, h)
        if prev is not None and cv2.compareHist(prev, h, cv2.HISTCMP_BHATTACHARYYA) > 0.45:
            out.append(frame_time(p, fps) - 0.5 / fps)
        prev = h
        if progress and k % 50 == 0:
            progress(k / max(1, len(frames)), "Finding camera cuts")
    return out


# ---------------------------------------------------------------- nudity

class NudityScorer:
    def __init__(self):
        from nudenet import NudeDetector
        self.det = NudeDetector()

    def score(self, path):
        best, label = 0.0, ""
        for d in self.det.detect(str(path)) or []:
            if d.get("class") in NUDE_CLASSES and d.get("score", 0) > best:
                best, label = float(d["score"]), d["class"]
        return best, label


# ---------------------------------------------------------------- CLIP descriptions

class ClipScorer:
    MEAN = (0.48145466, 0.4578275, 0.40821073)
    STD = (0.26862954, 0.26130258, 0.27577711)

    def __init__(self, model_dir: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        prov = ["CPUExecutionProvider"]
        self.vision = ort.InferenceSession(str(model_dir / "onnx" / "vision_model.onnx"), opts, providers=prov)
        self.text = ort.InferenceSession(str(model_dir / "onnx" / "text_model.onnx"), opts, providers=prov)
        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))

    @staticmethod
    def _norm(x):
        import numpy as np
        return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-8)

    def _out(self, sess, feeds, want):
        names = [o.name for o in sess.get_outputs()]
        name = want if want in names else names[0]
        return sess.run([name], feeds)[0]

    def embed_texts(self, prompts):
        import numpy as np
        vecs = []
        in_names = {i.name for i in self.text.get_inputs()}
        for p in prompts:
            ids = self.tok.encode(p).ids[:77]
            feeds = {"input_ids": np.array([ids], dtype=np.int64)}
            if "attention_mask" in in_names:
                feeds["attention_mask"] = np.ones((1, len(ids)), dtype=np.int64)
            vecs.append(self._out(self.text, feeds, "text_embeds")[0])
        return self._norm(np.stack(vecs).astype(np.float32))

    def _prep(self, path):
        import cv2
        import numpy as np
        img = cv2.imread(str(path))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w = img.shape[:2]
        s = 224 / min(h, w)
        img = cv2.resize(img, (max(224, round(w * s)), max(224, round(h * s))), interpolation=cv2.INTER_CUBIC)
        h, w = img.shape[:2]
        y, x = (h - 224) // 2, (w - 224) // 2
        img = img[y:y + 224, x:x + 224].astype(np.float32) / 255.0
        img = (img - np.array(self.MEAN, np.float32)) / np.array(self.STD, np.float32)
        return img.transpose(2, 0, 1)

    def embed_images(self, paths):
        import numpy as np
        batch = np.stack([self._prep(p) for p in paths]).astype(np.float32)
        return self._norm(self._out(self.vision, {"pixel_values": batch}, "image_embeds").astype(np.float32))


def describe_scores(image_embs, scorer, categories, custom_lines):
    """Per-frame probability for each description category (softmax over all prompts)."""
    import numpy as np
    groups = {k: PROMPTS[k] for k in categories if k in PROMPTS}
    for line in custom_lines:
        groups[f"custom:{line}"] = [line, f"a movie scene of {line}"]
    if not groups or image_embs is None or len(image_embs) == 0:
        return {}
    prompts, owner = [], []
    for k, ps in groups.items():
        prompts += ps
        owner += [k] * len(ps)
    prompts += NEUTRAL
    owner += [None] * len(NEUTRAL)
    text = scorer.embed_texts(prompts)
    logits = 100.0 * image_embs @ text.T
    logits -= logits.max(axis=1, keepdims=True)
    probs = np.exp(logits)
    probs /= probs.sum(axis=1, keepdims=True)
    out = {}
    for k in groups:
        cols = [i for i, o in enumerate(owner) if o == k]
        out[k] = probs[:, cols].sum(axis=1)
    return out


# ---------------------------------------------------------------- scenes from scores

def build_segments(times, scores, threshold, boundaries, duration, min_hits=1, strong=None,
                   gap=3.0, fps=FPS):
    """times/scores per frame -> [(start, end, peak_score, peak_index)]."""
    hits = [i for i, s in enumerate(scores) if s >= threshold]
    if min_hits > 1 and strong is not None:
        keep = []
        for i in hits:
            near = sum(1 for j in hits if abs(j - i) <= 1)
            if near >= min_hits or scores[i] >= strong:
                keep.append(i)
        hits = keep
    segs = []
    for i in hits:
        t = times[i]
        if segs and t - segs[-1][1] <= gap:
            segs[-1][1] = t
            if scores[i] > segs[-1][2]:
                segs[-1][2], segs[-1][3] = scores[i], i
        else:
            segs.append([t, t, scores[i], i])
    out = []
    half = 0.5 / fps
    for a, b, peak, idx in segs:
        a, b = a - half - 0.5, b + half + 0.5
        before = [x for x in boundaries if a - 2.5 <= x <= a + half]
        after = [x for x in boundaries if b - half <= x <= b + 2.5]
        if before:
            a = min(a, max(before))
        if after:
            b = max(b, min(after))
        out.append((max(0.0, round(a, 2)), min(duration, round(b, 2)), float(peak), idx))
    # merge overlaps after snapping
    merged = []
    for s in sorted(out):
        if merged and s[0] <= merged[-1][1]:
            m = merged[-1]
            merged[-1] = (m[0], max(m[1], s[1]), max(m[2], s[2]), m[3] if m[2] >= s[2] else s[3])
        else:
            merged.append(s)
    return merged


def save_cache(path: Path, **arrays):
    import numpy as np
    np.savez_compressed(path, **arrays)


def load_cache(path: Path):
    import numpy as np
    if not path.exists():
        return {}
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def make_thumb(frame: Path, dest: Path, blur=False):
    import cv2
    img = cv2.imread(str(frame))
    if img is None:
        return None
    if blur:
        img = cv2.GaussianBlur(img, (0, 0), sigmaX=14)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dest), img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return str(dest)


def watch(info, work_dir: Path, models_dir, categories, custom_lines, sensitivity=3,
          progress=None, cancel=None, log=print):
    """Full visual scan (cached). Returns list of scene dicts."""
    import numpy as np
    custom_lines = [c.strip() for c in custom_lines if c.strip()]
    want_clip = any(k in PROMPTS for k in categories) or bool(custom_lines)
    want_nude = "nudity" in categories
    if not (want_clip or want_nude):
        return []

    def stage(name, lo, hi):
        if not progress:
            return None
        return lambda f, m: progress(name, lo + (hi - lo) * f, m)

    if want_clip and not models.clip_ready(models_dir):
        models.ensure_clip(models_dir, stage("models", 0, 1), cancel)

    frames = extract_frames(info, work_dir / "frames", stage("frames", 0, 1), cancel)
    if not frames:
        return []
    times = np.array([frame_time(p) for p in frames], dtype=np.float32)
    cache_path = work_dir / "scene_cache.npz"
    cache = load_cache(cache_path)
    if len(cache.get("times", [])) != len(times):
        cache = {}

    if "bounds" not in cache:
        cache["bounds"] = np.array(shot_boundaries(frames, FPS, stage("analyze", 0, 0.1), cancel),
                                   dtype=np.float32)
    if want_nude and "nude" not in cache:
        scorer = NudityScorer()
        vals = np.zeros(len(frames), dtype=np.float32)
        report = stage("analyze", 0.1, 0.55 if want_clip and "clip" not in cache else 1.0)
        for i, p in enumerate(frames):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            vals[i] = scorer.score(p)[0]
            if report and i % 20 == 0:
                report(i / len(frames), f"Checking for nudity ({i} of {len(frames)} frames)")
        cache["nude"] = vals
    if want_clip and "clip" not in cache:
        scorer = ClipScorer(Path(models_dir) / "clip")
        embs = []
        report = stage("analyze", 0.55 if want_nude else 0.1, 1.0)
        B = 16
        for i in range(0, len(frames), B):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            embs.append(scorer.embed_images(frames[i:i + B]))
            if report:
                report(i / len(frames), f"Looking for described scenes ({i} of {len(frames)} frames)")
        cache["clip"] = np.concatenate(embs).astype(np.float16)
    cache["times"] = times
    save_cache(cache_path, **cache)

    bounds = list(cache["bounds"])
    scenes = []
    if want_nude:
        thr = NUDE_THRESH.get(int(sensitivity), 0.45)
        for a, b, peak, idx in build_segments(times, cache["nude"], thr, bounds, info.duration):
            scenes.append({"kind": "nudity", "label": "Nudity", "start": a, "end": b,
                           "score": round(peak, 2), "frame": str(frames[idx])})
    if want_clip:
        scorer = ClipScorer(Path(models_dir) / "clip")
        probs = describe_scores(cache["clip"].astype(np.float32), scorer, categories, custom_lines)
        thr = CLIP_THRESH.get(int(sensitivity), 0.42)
        for k, sc in probs.items():
            label = SCENE_NAMES.get(k) or ("“" + k.split(":", 1)[1] + "”")
            for a, b, peak, idx in build_segments(times, sc, thr, bounds, info.duration,
                                                   min_hits=2, strong=min(0.95, thr + 0.2)):
                scenes.append({"kind": k, "label": label, "start": a, "end": b,
                               "score": round(peak, 2), "frame": str(frames[idx])})
    for s in scenes:
        stem = f"{s['kind'].replace(':', '_').replace(' ', '_')[:40]}_{int(s['start'] * 10)}"
        s["thumb"] = make_thumb(Path(s["frame"]), work_dir / "thumbs" / f"{stem}.jpg")
        s["thumb_blur"] = make_thumb(Path(s["frame"]), work_dir / "thumbs" / f"{stem}_blur.jpg", blur=True)
    scenes.sort(key=lambda s: s["start"])
    return scenes
