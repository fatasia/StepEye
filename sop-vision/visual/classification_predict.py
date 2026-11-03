"""Single-use classifier worker: fixed preprocessing, one ORT CPU session, no detection labels."""
from __future__ import annotations

import json
from pathlib import Path
import sys


def prepare(image, preprocessing):
    import numpy as np
    from PIL import Image
    if preprocessing == "gray224":
        image = image.convert("L").resize((224, 224), Image.Resampling.BILINEAR)
        return np.ascontiguousarray(np.asarray(image, dtype=np.float32)[None, None] / 255)
    if preprocessing == "imagenet_center224":
        image = image.convert("RGB"); w, h = image.size
        size = (256, int(256*h/w)) if w <= h else (int(256*w/h), 256)
        if size[0] * size[1] > 16_000_000:
            raise ValueError("imagenet resize exceeds pixel budget; unsupported aspect ratio")
        image = image.resize(size, Image.Resampling.BICUBIC)
        x, y = (image.width-224)//2, (image.height-224)//2
        image = image.crop((x, y, x+224, y+224))
        array = np.asarray(image, dtype=np.float32)/255
        array = (array-np.array([.485,.456,.406],dtype=np.float32))/np.array([.229,.224,.225],dtype=np.float32)
        return np.ascontiguousarray(array.transpose(2,0,1)[None])
    raise ValueError("unsupported preprocessing")


def predict(spec):
    import numpy as np
    import onnxruntime as ort
    from PIL import Image
    from .label_jobs import fingerprint
    preprocessing = spec["preprocessing"]
    channels = {"gray224": 1, "imagenet_center224": 3}[preprocessing]
    classes = spec["classes"]
    path = Path(spec["model_path"])
    if (not 0 < path.stat().st_size <= 256*1024*1024 or fingerprint(path) != spec["model_sha256"]
            or not 2 <= len(classes) <= 1000): raise ValueError("invalid classifier asset")
    options = ort.SessionOptions(); options.intra_op_num_threads = 2; options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
    try:
        inputs, outputs = session.get_inputs(), session.get_outputs()
        if len(inputs) != 1 or len(outputs) != 1 or inputs[0].type != "tensor(float)":
            raise ValueError("classifier requires one float32 input and one output")
        shape = inputs[0].shape; output_shape = outputs[0].shape
        # Only batch may be dynamic (the pinned wetfloor export). Spatial/channel dims are fixed.
        batch = (lambda d: d == 1 or d is None or isinstance(d, str)) if preprocessing == "imagenet_center224" else (lambda d: d == 1)
        if (len(shape) != 4 or not batch(shape[0]) or shape[1:] != [channels,224,224]
                or len(output_shape) != 2 or not batch(output_shape[0]) or output_shape[1] != len(classes)
                or outputs[0].type != "tensor(float)"):
            raise ValueError("classifier shape/class count mismatch")
        with Image.open(spec["image_path"]) as image: tensor = prepare(image, preprocessing)
        logits = session.run(None, {inputs[0].name: tensor})[0]
        if logits.shape != (1, len(classes)) or not np.isfinite(logits).all(): raise ValueError("invalid logits")
        scores = logits[0].astype(np.float64); scores = np.exp(scores-scores.max()); scores /= scores.sum()
        return [{"label": label, "score": float(score)} for label, score in zip(classes, scores)]
    finally:
        del session


if __name__ == "__main__":
    try:
        result = predict(json.loads(sys.stdin.buffer.read(64*1024)))
        print(json.dumps({"probabilities": result}, allow_nan=False))
    except Exception as error:
        print(json.dumps({"error": type(error).__name__}))
        raise SystemExit(1)
