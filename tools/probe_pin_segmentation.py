"""Run the registered PAINE segmentation candidate on one image, without Torch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from pin_checkpoint import digest

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    started = time.perf_counter()
    manifest = json.loads((ROOT / "models/public/pin-models.json").read_text(encoding="utf-8"))
    entry = manifest["models"][0]
    model = ROOT / entry["model_path"]
    if digest(model) != entry["sha256"]:
        raise ValueError("Pin segmentation model SHA mismatch")
    import onnxruntime as ort
    from PIL import Image
    sys.path.insert(0, str(ROOT))
    from visual.pin_segmentation import segment
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model), options, providers=["CPUExecutionProvider"])
    image = Image.open(args.image).convert("RGB")
    mask, stats = segment(image, session)
    args.output.mkdir(parents=True)
    Image.fromarray(mask * 255).save(args.output / "mask.png")
    stats.update({"model_id": entry["id"], "model_sha256": entry["sha256"],
                  "image_sha256": digest(args.image), "image_size": list(image.size),
                  "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                  "defect_verdict": None, "usage": "evaluation_only"})
    (args.output / "report.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
