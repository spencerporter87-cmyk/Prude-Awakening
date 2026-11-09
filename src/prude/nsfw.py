"""A second opinion on nudity: a small image classifier that looks at the whole frame.

NudeNet finds body parts; this model judges the picture as a whole ("is this actually
explicit?"). Skin-colored objects, statues and close-ups fool a body-part finder far more
easily than they fool a whole-picture judge, so a flag needs both to agree.
The model is Marqo/nsfw-image-detection-384 (Apache-2.0), converted to ONNX by our build."""

import json
from pathlib import Path


class NsfwClassifier:
    def __init__(self, onnx_path, meta_path=None):
        import onnxruntime as ort
        onnx_path = Path(onnx_path)
        meta_path = Path(meta_path) if meta_path else onnx_path.with_suffix(".json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        self.size = int(meta.get("size", 384))
        self.mean = meta.get("mean", [0.5, 0.5, 0.5])
        self.std = meta.get("std", [0.5, 0.5, 0.5])
        self.nsfw_index = int(meta.get("nsfw_index", 0))
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.sess = ort.InferenceSession(str(onnx_path), opts, providers=["CPUExecutionProvider"])
        self.input = self.sess.get_inputs()[0].name

    def _prep(self, path):
        """A file path, or a picture already in memory (BGR, as OpenCV reads it)."""
        import cv2
        import numpy as np
        img = path if isinstance(path, np.ndarray) else cv2.imread(str(path))
        if img is None:
            return np.zeros((3, self.size, self.size), np.float32)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        # the whole frame, squeezed to a square: nothing at the edges is cropped away
        img = cv2.resize(img, (self.size, self.size), interpolation=cv2.INTER_AREA)
        x = img.astype(np.float32) / 255.0
        x = (x - np.array(self.mean, np.float32)) / np.array(self.std, np.float32)
        return x.transpose(2, 0, 1)

    def probs(self, paths):
        """Probability (0..1) that each picture is NSFW."""
        import numpy as np
        batch = np.stack([self._prep(p) for p in paths]).astype(np.float32)
        logits = self.sess.run(None, {self.input: batch})[0].astype(np.float64)
        logits -= logits.max(axis=1, keepdims=True)
        e = np.exp(logits)
        return (e / e.sum(axis=1, keepdims=True))[:, self.nsfw_index].astype(np.float32)
