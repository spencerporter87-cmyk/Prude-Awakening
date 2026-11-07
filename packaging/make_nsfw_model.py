"""Turn Marqo/nsfw-image-detection-384 (Apache-2.0) into a plain ONNX file the app can run
without PyTorch. Run by the build; the result is published once as the 'models-v1' release.

usage: python packaging/make_nsfw_model.py OUT_DIR"""

import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
import timm
import torch

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
model = timm.create_model("hf_hub:Marqo/nsfw-image-detection-384", pretrained=True).eval()
cfg = timm.data.resolve_model_data_config(model)
size = int(cfg["input_size"][1])
names = [str(n) for n in model.pretrained_cfg["label_names"]]
nsfw_index = next(i for i, n in enumerate(names) if n.upper() == "NSFW")
print("labels", names, "nsfw index", nsfw_index, "size", size, "mean", cfg["mean"], "std", cfg["std"])

x = torch.randn(2, 3, size, size)
path = out / "marqo-nsfw-384.onnx"
kw = dict(input_names=["input"], output_names=["logits"],
          dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}}, opset_version=17)
try:
    torch.onnx.export(model, x, str(path), dynamo=False, **kw)
except TypeError:                                   # older torch: no 'dynamo' option
    torch.onnx.export(model, x, str(path), **kw)

sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
for batch in (1, 5):
    probe = torch.randn(batch, 3, size, size)
    want = model(probe).detach().numpy()
    got = sess.run(None, {"input": probe.numpy()})[0]
    assert got.shape == want.shape and np.allclose(got, want, atol=1e-3), "ONNX output differs from the original"
(out / "marqo-nsfw-384.json").write_text(json.dumps(
    {"size": size, "mean": list(cfg["mean"]), "std": list(cfg["std"]), "labels": names,
     "nsfw_index": nsfw_index, "source": "Marqo/nsfw-image-detection-384 (Apache-2.0)"}), encoding="utf-8")
print("ok", path.stat().st_size, "bytes")
