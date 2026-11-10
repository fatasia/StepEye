"""Single-shape action-worker latency measurement, not an accuracy evaluation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual.action_predict import ActionPredictor, CLIP_SHAPE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--classes", type=Path, required=True, help="JSON array matching model output order")
    parser.add_argument("--clip", type=Path, required=True, help="RGB uint8 NPY [16,224,224,3]")
    parser.add_argument("--sha256", required=True, help="Approved ONNX fingerprint")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--provider", choices=["CPUExecutionProvider", "CUDAExecutionProvider"], default="CPUExecutionProvider")
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not 5 <= args.runs <= 100:
        parser.error("runs must be 5..100")
    if args.out.exists():
        parser.error("output already exists; choose a new evidence file")
    clip = np.load(args.clip, allow_pickle=False)
    if clip.shape != CLIP_SHAPE or clip.dtype != np.uint8:
        parser.error("clip must be RGB uint8 [16,224,224,3]")
    classes = json.loads(args.classes.read_text(encoding="utf-8"))
    start = time.perf_counter()
    with ActionPredictor(args.model, classes, timeout_s=120, python_executable=args.python,
                         providers=[args.provider], expected_sha256=args.sha256) as worker:
        startup_ms = (time.perf_counter() - start) * 1000
        first = worker.predict(clip)
        results = [worker.predict(clip) for _ in range(args.runs)]
        def quantile(key):
            values = [r[key] for r in results]
            return {"p50_ms": float(np.percentile(values, 50)), "p95_ms": float(np.percentile(values, 95)),
                    "max_ms": max(values)}
        report = {"schema": 1, "platform": platform.platform(), "model_sha256": worker.model_sha256,
                  "providers": worker.providers, "shape": list(CLIP_SHAPE), "batch": 1, "runs": args.runs,
                  "startup_ms": startup_ms, "first_roundtrip_ms": first["roundtrip_ms"],
                  "first_prediction": {k: first[k] for k in ("label", "class_id", "confidence", "top_k")},
                  "warm_roundtrip": quantile("roundtrip_ms"), "warm_inference": quantile("inference_ms"),
                  "scope": "single preloaded clip; no capture, sampling window, queue, accuracy or multi-station measurement"}
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
