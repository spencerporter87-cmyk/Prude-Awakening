"""Watching the movie: sample frames, score them for nudity and for scene
descriptions (violence, gore, sexual content, your own descriptions), then turn
the scores into scenes snapped to the film's camera cuts."""

import json
import shutil
from functools import lru_cache
from pathlib import Path

from . import models
from .media import Cancelled, ffmpeg, run, run_ffmpeg
from .nsfw import NsfwClassifier

# ---------------------------------------------------------------- what can be found
# Groups each have their own "how picky" slider and default action.
SCENE_GROUPS = [
    ("nudity", "Nudity", "Detected body part by body part. Swimsuits and underwear count as covered."),
    ("sexual", "Sex and romance", "Sex scenes are split by whether nudity is shown."),
    ("violence", "Violence and scary scenes", ""),
    ("custom", "Your descriptions", ""),
]
SCENE_TYPES = [   # key, group, name, description, on by default in "everything"
    ("nude_f_breasts", "nudity", "Female breasts", "Bare female breasts"),
    ("nude_f_genitals", "nudity", "Female genitals", "Full-frontal female nudity"),
    ("nude_m_genitals", "nudity", "Male genitals", "Full-frontal male nudity"),
    ("nude_buttocks", "nudity", "Bare buttocks", "Male or female"),
    ("nude_m_chest", "nudity", "Shirtless men", "Bare male chest (beach, sports…)"),
    ("sex_nude", "sexual", "Sex scenes with nudity", "Sex where nudity is shown"),
    ("sex_implied", "sexual", "Sex scenes without nudity", "Implied: under covers, clothed, cut away"),
    ("kissing", "sexual", "Passionate kissing", "Making out, heavy kissing"),
    ("suggestive", "sexual", "Suggestive scenes", "Lingerie, undressing, stripping"),
    ("swimwear", "sexual", "Swimsuits and bikinis", "Beach and pool scenes"),
    ("violence", "violence", "Violence", "Fights, shootings, stabbings, attacks"),
    ("gore", "violence", "Blood and gore", "Bloody wounds, gore, dead bodies"),
    ("scary", "violence", "Frightening scenes", "Horror, monsters, jump scares"),
]
TYPE_GROUP = {k: g for k, g, _n, _d in SCENE_TYPES}
TYPE_NAME = {k: n for k, _g, n, _d in SCENE_TYPES}
LEGACY = {"nudity": ["nude_f_breasts", "nude_f_genitals", "nude_m_genitals", "nude_buttocks"],
          "sexual": ["sex_nude", "sex_implied"]}
SCENE_NAMES = dict(TYPE_NAME, nudity="Nudity")
# kept for older callers
SCENE_CATEGORIES = [(k, n, d) for k, _g, n, d in SCENE_TYPES]

NUDE_TYPES = {
    "nude_f_breasts": ["FEMALE_BREAST_EXPOSED"],
    "nude_f_genitals": ["FEMALE_GENITALIA_EXPOSED"],
    "nude_m_genitals": ["MALE_GENITALIA_EXPOSED"],
    "nude_buttocks": ["BUTTOCKS_EXPOSED", "ANUS_EXPOSED"],
    "nude_m_chest": ["MALE_BREAST_EXPOSED"],
}
NUDE_CLASS_ORDER = ["FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
                    "BUTTOCKS_EXPOSED", "ANUS_EXPOSED", "MALE_BREAST_EXPOSED"]
NUDE_SHORT = {"nude_f_breasts": "female breasts", "nude_f_genitals": "female genitals",
              "nude_m_genitals": "male genitals", "nude_buttocks": "bare buttocks",
              "nude_m_chest": "shirtless man"}

PROMPTS = {
    "sex": ["a couple having sex in bed", "two naked people in bed together",
            "a sex scene in a movie", "lovers having sex under the bed sheets",
            "a couple in bed making love"],
    "kissing": ["two people kissing passionately", "a couple making out",
                "lovers kissing intensely", "a couple kissing on a bed"],
    "suggestive": ["a woman in lingerie", "a person undressing", "a striptease dancer",
                   "a person posing seductively in underwear", "a man taking off his clothes"],
    "swimwear": ["a woman in a bikini", "people in swimsuits at the beach",
                 "a man in swim trunks at the pool", "people sunbathing in swimwear"],
    "violence": ["people in a violent fist fight", "a person being shot with a gun",
                 "a person being stabbed with a knife", "someone being beaten up",
                 "a violent brutal attack", "a shootout with guns firing"],
    "gore": ["a bloody wound", "a person covered in blood", "a gory dead body",
             "blood splattered everywhere", "a severed body part"],
    "scary": ["a terrifying monster", "a scary scene from a horror movie", "a frightening ghost",
              "a person screaming in terror", "a creepy demon"],
}
CLIP_FOR = {"sex_nude": "sex", "sex_implied": "sex", "kissing": "kissing", "suggestive": "suggestive",
            "swimwear": "swimwear", "violence": "violence", "gore": "gore", "scary": "scary"}
SWIM_NEUTRAL = ["people swimming in a pool", "a family at the beach", "kids playing at a water park",
                "a person swimming laps", "surfers riding waves"]
NEUTRAL = ["people talking in a room", "a person walking outside", "a landscape view",
           "a car driving on a road", "people eating dinner at a table",
           "a close-up of a person's face", "a busy city street", "an office with people working",
           "white text on a black screen", "a dark night scene", "people dancing at a party",
           "a person holding a phone", "children playing", "a kitchen", "a living room",
           "people laughing together", "a person sleeping in bed alone", "a sports game",
           "an animated cartoon scene", "a crowd of people", "a person driving a car",
           "a couple hugging", "a hospital room", "soldiers marching", "a person holding a gun",
           "a police officer", "a couple talking in bed fully clothed", "a doctor examining a patient",
           "people hugging goodbye", "a quick kiss on the cheek", "a wedding ceremony",
           "a person getting dressed for work", "a fashion show", "a gym workout"]
NUDE_CLASSES = set(NUDE_CLASS_ORDER) - {"MALE_BREAST_EXPOSED"}
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


def exact_cuts(info, start, length, cache=None):
    """Camera cuts between start and start+length, to the frame (full frame rate, small size)."""
    import re
    key = f"{start:.2f}:{length:.2f}"
    if cache is not None and key in cache:
        return cache[key]
    from .media import ffmpeg, run
    r = run([ffmpeg(), "-hide_banner", "-nostdin", "-copyts", "-ss", f"{max(0.0, start):.3f}",
             "-i", str(info.path), "-t", f"{length:.3f}", "-map", f"0:{info.video['index']}", "-an", "-sn",
             "-vf", "scale=192:-2,select='gt(scene,0.30)',showinfo", "-f", "null", "-"], timeout=120)
    found = [round(float(m) - info.format_start, 3)
             for m in re.findall(r"pts_time:\s*([0-9.]+)", r.stderr or "")]
    if cache is not None:
        cache[key] = found
    return found


def refine_edges(info, a, b, cache=None):
    """Move a scene's start/end onto the exact camera cut near the 1-per-second estimate.
    Never shrinks past a frame that was flagged: the estimate is padded by about a second."""
    try:
        cuts = exact_cuts(info, a - 1.0, 2.0, cache)
        if cuts:
            a = max(cuts)
        cuts = exact_cuts(info, b - 1.0, 2.0, cache)
        if cuts:
            b = min(cuts)
    except Exception:                       # noqa: BLE001 - keep the estimate
        pass
    return round(max(0.0, a), 3), round(min(info.duration, b), 3)


# ---------------------------------------------------------------- nudity

class NudityScorer:
    """NudeNet. Without a model path: the small 320-pixel model that ships with the package.
    With the larger 640m model: slower, far better at telling skin from nudity."""
    MIN_AREA = 0.003          # a "body part" smaller than this share of the picture is noise

    def __init__(self, model_path=None, resolution=320):
        from nudenet import NudeDetector
        if model_path:
            try:
                self.det = NudeDetector(model_path=str(model_path), inference_resolution=resolution)
            except TypeError:                 # an older NudeNet without inference_resolution
                self.det = NudeDetector(model_path=str(model_path))
        else:
            self.det = NudeDetector()

    def scores(self, path):
        """Best confidence per class in NUDE_CLASS_ORDER."""
        out = [0.0] * len(NUDE_CLASS_ORDER)
        dets = self.det.detect(str(path)) or []
        area = None
        for d in dets:
            c = d.get("class")
            if c not in NUDE_CLASS_ORDER:
                continue
            box = d.get("box")
            if box and len(box) == 4:
                if area is None:
                    try:
                        from PIL import Image
                        with Image.open(path) as im:
                            area = float(im.size[0] * im.size[1])
                    except Exception:         # noqa: BLE001
                        area = 0.0
                if area and (float(box[2]) * float(box[3])) / area < self.MIN_AREA:
                    continue
            i = NUDE_CLASS_ORDER.index(c)
            out[i] = max(out[i], float(d.get("score", 0)))
        return out

    def score(self, path):
        sc = self.scores(path)
        best = max((v, c) for v, c in zip(sc, NUDE_CLASS_ORDER) if c in NUDE_CLASSES)
        return best


# What the whole picture says. A flag is trusted less when the classifier and the scene
# description both say "not really nudity"; a bare chest is judged by the detector alone.
CHEST = NUDE_CLASS_ORDER.index("MALE_BREAST_EXPOSED")
GATE = 0.2                  # frames below this on every detector never get a second look
NSFW_GATE = 0.3
MAX_SECOND_LOOKS = 1500
VETO_NUDE = ["a naked woman with bare breasts", "a nude man", "a naked person", "bare buttocks",
             "a woman topless", "two naked people in bed", "full frontal nudity"]
VETO_SAFE = ["a marble statue", "a classical painting", "a person fully dressed", "a close-up of a face",
             "a woman in a bikini", "a shirtless man", "a woman in a low-cut dress", "a cartoon character",
             "a person in a skin-colored outfit", "a close-up of skin or a hand", "a muscular man in shorts",
             "a woman in a swimsuit at the beach", "people wearing clothes", "a baby", "a sunset landscape",
             "a wooden object", "a person in a bathrobe", "dancers in costumes"]


def clip_nude_prob(embs, scorer):
    """Per frame: how much of the CLIP probability lands on 'naked' descriptions rather than
    on lookalikes (statues, bikinis, close-ups, clothed people)."""
    import numpy as np
    prompts = VETO_NUDE + VETO_SAFE
    text = scorer.embed_texts(prompts)
    logits = 100.0 * np.asarray(embs, np.float32) @ text.T
    logits -= logits.max(axis=1, keepdims=True)
    e = np.exp(logits)
    e /= e.sum(axis=1, keepdims=True)
    return e[:, :len(VETO_NUDE)].sum(axis=1).astype(np.float32)


def combine_nudity(det, nsfw_p=None, clip_p=None):
    """det: (frames x classes) detector scores. nsfw_p / clip_p: per-frame arrays, negative
    where that check wasn't run. Returns the scores to trust: a body-part hit is cut down when
    the whole-picture checks disagree (never below a fifth, so very obvious hits still show)."""
    import numpy as np
    det = np.asarray(det, np.float32)
    f = np.ones(len(det), np.float32)
    if nsfw_p is not None:
        p = np.asarray(nsfw_p, np.float32)
        f *= np.where(p >= 0, 0.35 + 0.65 * np.minimum(1.0, p / 0.4), 1.0)
    if clip_p is not None:
        c = np.asarray(clip_p, np.float32)
        f *= np.where(c >= 0, 0.5 + 0.5 * np.minimum(1.0, c / 0.25), 1.0)
    f = np.maximum(0.2, f)
    out = det * f[:, None]
    out[:, CHEST] = det[:, CHEST]          # shirtless men: the detector alone decides
    return out


def hires_frames(info, times, dest: Path, width=960, cancel=None, workers=4):
    """Sharper copies of just the frames that need a second look. Returns {index: path}."""
    import concurrent.futures as cf
    dest.mkdir(parents=True, exist_ok=True)
    vf = f"scale={width}:-2"
    if info.is_hdr and " zscale " in _filters() and " tonemap " in _filters():
        vf = (f"zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
              f"zscale=t=bt709:m=bt709:r=tv,{vf}")
    vf += ",format=yuvj420p"

    def one(item):
        i, t = item
        out = dest / f"{i:06d}.jpg"
        if cancel is not None and cancel.is_set():
            return i, None
        r = run([ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(info.path),
                 "-map", "0:v:0", "-frames:v", "1", "-an", "-sn", "-vf", vf, "-q:v", "3", str(out)])
        return i, (out if r.returncode == 0 and out.exists() else None)

    found = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, path in ex.map(one, list(times.items())):
            if path:
                found[i] = path
    return found


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


def describe_scores(image_embs, scorer, clip_keys, custom_lines):
    """Per-frame probability for each prompt group (softmax over all prompts, with many
    everyday scenes - including swimming and beach scenes - as competition)."""
    import numpy as np
    groups = {k: PROMPTS[k] for k in clip_keys if k in PROMPTS}
    for line in custom_lines:
        groups[f"custom:{line}"] = [line, f"a movie scene of {line}"]
    if not groups or image_embs is None or len(image_embs) == 0:
        return {}
    prompts, owner = [], []
    for k, ps in groups.items():
        prompts += ps
        owner += [k] * len(ps)
    neutral = list(NEUTRAL) + ([] if "swimwear" in groups else SWIM_NEUTRAL + PROMPTS["swimwear"])
    if "kissing" not in groups:
        neutral += PROMPTS["kissing"][:2]
    prompts += neutral
    owner += [None] * len(neutral)
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
    """times/scores per frame -> [(start, end, peak_score, peak_index)] as plain Python numbers."""
    times = [float(t) for t in times]
    scores = [float(s) for s in scores]
    boundaries = [float(b) for b in boundaries]
    duration = float(duration)
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
        out.append((max(0.0, round(float(a), 2)), min(duration, round(float(b), 2)), float(peak), int(idx)))
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


def expand_types(keys):
    out = []
    for k in keys or []:
        for t in LEGACY.get(k, [k]):
            if t not in out:
                out.append(t)
    return out


def _window(times, a, b):
    return [i for i, t in enumerate(times) if a <= t <= b]


def watch(info, work_dir: Path, models_dir, filters, progress=None, cancel=None, log=print):
    """Full visual scan (cached). Returns a list of scene dicts with kind, group, label,
    detail, start, end, score, action and thumbnails."""
    import numpy as np
    types = expand_types(filters.scenes)
    custom_lines = [c.strip() for c in (filters.scene_descriptions or "").splitlines() if c.strip()]
    sens = dict(filters.scene_sensitivity or {})
    acts = dict(filters.scene_actions or {})

    def s_of(group):
        return int(sens.get(group, filters.sensitivity))

    def act_of(group):
        return acts.get(group) or filters.scene_action

    nude_types = [t for t in types if t in NUDE_TYPES]
    sex_types = [t for t in types if t in ("sex_nude", "sex_implied")]
    clip_keys = {CLIP_FOR[t] for t in types if t in CLIP_FOR}
    want_clip = bool(clip_keys or custom_lines)
    want_nude = bool(nude_types or sex_types)
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
    cache.pop("nude", None)   # older single-score cache

    if "bounds" not in cache:
        cache["bounds"] = np.array(shot_boundaries(frames, FPS, stage("analyze", 0, 0.1), cancel),
                                   dtype=np.float32)
    import os
    import time
    if int(cache.get("nude_ver", [0])[0]) != 2:
        for k in ("nude_cls", "nsfw_p", "nude_hi", "veto_clip"):
            cache.pop(k, None)
    extras_ok = os.environ.get("PRUDE_NO_EXTRA_MODELS") != "1"

    def have(ready, ensure, what):
        """A helper model: use it if present, fetch it if allowed, otherwise carry on without."""
        if ready(models_dir):
            return True
        if not extras_ok:
            return False
        try:
            ensure(models_dir, stage("models", 0, 1), cancel)
            return True
        except Cancelled:
            raise
        except Exception as e:                       # noqa: BLE001
            log(f"{what} isn't available ({str(e)[:160]}); using the basic nudity check only.")
            return False

    use_nsfw = use_big = False
    if want_nude:
        use_nsfw = have(models.nsfw_ready, models.ensure_nsfw, "The nudity second opinion")
        use_big = have(models.nudenet_640_ready, models.ensure_nudenet_640, "The sharper nudity detector")
    if want_nude and "nude_cls" not in cache:
        scorer = NudityScorer()
        vals = np.zeros((len(frames), len(NUDE_CLASS_ORDER)), dtype=np.float32)
        report = stage("analyze", 0.1, 0.4 if want_clip and "clip" not in cache else 0.7)
        t0 = time.perf_counter()
        for i, p in enumerate(frames):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            vals[i] = scorer.scores(p)
            if report and i % 20 == 0:
                report(i / len(frames), f"Checking for nudity ({i} of {len(frames)} frames)")
        cache["nude_cls"] = vals
        log(f"Timing: quick nudity check {1000 * (time.perf_counter() - t0) / len(frames):.0f} ms per frame.")
    if want_nude and use_nsfw and "nsfw_p" not in cache:
        clf = NsfwClassifier(models.nsfw_dir(models_dir) / models.NSFW_FILES[0])
        probs = []
        report = stage("analyze", 0.4 if want_clip and "clip" not in cache else 0.7, 0.55 if want_clip and "clip" not in cache else 0.85)
        t0 = time.perf_counter()
        for i in range(0, len(frames), 16):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            probs.append(clf.probs(frames[i:i + 16]))
            if report:
                report(i / len(frames), f"Second opinion on the whole picture ({i} of {len(frames)} frames)")
        cache["nsfw_p"] = np.concatenate(probs).astype(np.float32)
        log(f"Timing: whole-picture second opinion {1000 * (time.perf_counter() - t0) / len(frames):.0f} ms per frame.")
    if want_nude:
        # frames where any detector smells something get a closer look
        N = len(frames)
        sus = cache["nude_cls"].max(axis=1)
        if "nsfw_p" in cache:
            sus = np.maximum(sus, np.where(cache["nsfw_p"] >= NSFW_GATE, 0.5, 0.0))
        cand = [int(i) for i in np.argsort(-sus)[:MAX_SECOND_LOOKS] if sus[i] >= GATE]
        cache.setdefault("nude_hi", np.full((N, len(NUDE_CLASS_ORDER)), -1.0, dtype=np.float32))
        cache.setdefault("veto_clip", np.full(N, -1.0, dtype=np.float32))
        todo = [i for i in cand if cache["nude_hi"][i, 0] < 0]
        if use_big and todo:
            report = stage("analyze", 0.85, 0.93)
            t0 = time.perf_counter()
            hi_dir = work_dir / "hi"
            if report:
                report(0.0, f"Taking a closer look at {len(todo)} frames")
            got = hires_frames(info, {i: float(times[i]) for i in todo}, hi_dir, cancel=cancel)
            big = NudityScorer(models.nudenet_640_path(models_dir), 640)
            for n, i in enumerate(todo):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                path = got.get(i, frames[i])
                cache["nude_hi"][i] = big.scores(path)
                if report and n % 5 == 0:
                    report(n / len(todo), f"Taking a closer look ({n} of {len(todo)} suspicious frames)")
            shutil.rmtree(hi_dir, ignore_errors=True)
            log(f"Timing: sharper detector ran on {len(todo)} of {N} frames "
                f"({1000 * (time.perf_counter() - t0) / max(1, len(todo)):.0f} ms each).")
    if want_nude and cand:
        # does the scene description agree it's a naked person (and not a statue, a bikini...)?
        todo_v = [i for i in cand if cache["veto_clip"][i] < 0]
        if todo_v and have(models.clip_ready, models.ensure_clip, "The scene model"):
            try:
                vs = ClipScorer(Path(models_dir) / "clip")
                for k in range(0, len(todo_v), 16):
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    chunk = todo_v[k:k + 16]
                    cache["veto_clip"][chunk] = clip_nude_prob(vs.embed_images([frames[i] for i in chunk]), vs)
            except Cancelled:
                raise
            except Exception as e:                   # noqa: BLE001 - the other checks still apply
                log(f"The description check on suspicious frames failed ({str(e)[:160]}); skipping it.")
    cache["nude_ver"] = np.array([2])
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
    tl = [float(t) for t in times]
    scenes = []

    trusted = None
    if want_nude:
        det = cache["nude_cls"].copy()
        hi = cache.get("nude_hi")
        if hi is not None:
            done = hi[:, 0] >= 0
            det[done] = hi[done]                    # the sharper detector's word wins where it looked
        vc = cache.get("veto_clip")
        trusted = combine_nudity(det, cache.get("nsfw_p"), vc if vc is not None and (vc >= 0).any() else None)

    def col(cls_names):
        idx = [NUDE_CLASS_ORDER.index(c) for c in cls_names]
        return trusted[:, idx].max(axis=1)

    def why(idx):
        """What each check said about the frame that decided the scene."""
        bits = [f"body-part detector {100 * float(cache['nude_cls'][idx].max()):.0f}%"]
        hi_ = cache.get("nude_hi")
        if hi_ is not None and hi_[idx, 0] >= 0:
            bits = [f"sharper body-part detector {100 * float(hi_[idx].max()):.0f}%"]
        if cache.get("nsfw_p") is not None:
            bits.append(f"whole-picture check {100 * float(cache['nsfw_p'][idx]):.0f}% explicit")
        v = cache.get("veto_clip")
        if v is not None and v[idx] >= 0:
            bits.append(f"description check {100 * float(v[idx]):.0f}% naked")
        return "; ".join(bits)

    def add(kind, group, label, a, b, peak, idx, detail="", thr=None, reason=""):
        scenes.append({"kind": kind, "group": group, "label": label, "detail": detail,
                       "start": a, "end": b, "score": round(float(peak), 2),
                       "threshold": None if thr is None else round(float(thr), 2),
                       "why": reason,
                       "frame": str(frames[idx]), "action": act_of(group)})

    # ---- nudity, body part by body part
    if nude_types:
        thr = NUDE_THRESH.get(s_of("nudity"), 0.45)
        per_type = {t: col(NUDE_TYPES[t]) for t in nude_types}
        combined = np.max(np.stack(list(per_type.values())), axis=0)
        # a flag needs two frames in a row (or one very sure one); at the most sensitive setting any single frame counts
        persist = s_of("nudity") < 5
        for a, b, peak, idx in build_segments(times, combined, thr, bounds, info.duration,
                                               min_hits=2 if persist else 1, strong=min(0.97, thr + 0.3)):
            win = _window(tl, a, b)
            found = [t for t in nude_types if win and float(per_type[t][win].max()) >= thr]
            names = [NUDE_SHORT[t] for t in found] or ["nudity"]
            label = "Shirtless man" if found == ["nude_m_chest"] else "Nudity"
            add("nudity", "nudity", label, a, b, peak, idx, ", ".join(names), thr, reason=why(idx))

    # ---- scene descriptions (CLIP)
    probs = {}
    if want_clip:
        scorer = ClipScorer(Path(models_dir) / "clip")
        probs = describe_scores(cache["clip"].astype(np.float32), scorer, clip_keys, custom_lines)
    explicit = col(sorted(NUDE_CLASSES)) if want_nude else None
    for key, sc in probs.items():
        if key.startswith("custom:"):
            group, kinds = "custom", [key]
        else:
            group = TYPE_GROUP.get(next((t for t in types if CLIP_FOR.get(t) == key), key), "sexual")
            kinds = [key]
        thr = CLIP_THRESH.get(s_of(group), 0.42)
        for a, b, peak, idx in build_segments(times, sc, thr, bounds, info.duration,
                                               min_hits=2, strong=min(0.95, thr + 0.2)):
            if key == "sex":
                win = _window(tl, a, b)
                nthr = NUDE_THRESH.get(s_of("sexual"), 0.45) - 0.05
                nude = explicit is not None and win and float(explicit[win].max()) >= nthr
                kind = "sex_nude" if nude else "sex_implied"
                if kind not in sex_types:
                    continue
                add(kind, "sexual", "Sex scene" + (" with nudity" if nude else " (no nudity shown)"),
                    a, b, peak, idx, thr=thr)
            elif key.startswith("custom:"):
                add(key, "custom", "“" + key.split(":", 1)[1] + "”", a, b, peak, idx, thr=thr)
            else:
                add(key, TYPE_GROUP.get(key, group), TYPE_NAME.get(key, key), a, b, peak, idx, thr=thr)

    # ---- a nudity flag inside a sex scene becomes part of that scene
    sexes = [x for x in scenes if x["kind"] in ("sex_nude", "sex_implied")]
    keep = []
    for x in scenes:
        if x["kind"] == "nudity":
            host = next((y for y in sexes if min(x["end"], y["end"]) - max(x["start"], y["start"])
                         > 0.5 * (x["end"] - x["start"])), None)
            if host:
                host["start"], host["end"] = min(host["start"], x["start"]), max(host["end"], x["end"])
                host["kind"], host["label"] = "sex_nude", "Sex scene with nudity"
                host["detail"] = ", ".join(filter(None, [host.get("detail"), x["detail"]]))
                continue
        keep.append(x)
    scenes = keep

    edge_path = work_dir / "edges.json"
    try:
        edges = json.loads(edge_path.read_text(encoding="utf-8"))
    except Exception:                       # noqa: BLE001
        edges = {}
    for n, s in enumerate(scenes):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if progress:
            progress("analyze", 0.95 + 0.05 * n / max(1, len(scenes)), "Lining up scene edges with camera cuts")
        s["start"], s["end"] = refine_edges(info, s["start"], s["end"], edges)
    try:
        edge_path.write_text(json.dumps(edges), encoding="utf-8")
    except OSError:
        pass
    for s in scenes:
        stem = f"{s['kind'].replace(':', '_').replace(' ', '_')[:40]}_{int(s['start'] * 10)}"
        s["thumb"] = make_thumb(Path(s["frame"]), work_dir / "thumbs" / f"{stem}.jpg")
        s["thumb_blur"] = make_thumb(Path(s["frame"]), work_dir / "thumbs" / f"{stem}_blur.jpg", blur=True)
    scenes.sort(key=lambda s: s["start"])
    return scenes
