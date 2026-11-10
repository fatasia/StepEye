"""Export the pinned PAINE ResNet34 U-Net and check a public image once.

Install FastAI 2.8.6 and FastProgress 1.0.5 in an isolated export environment.
No training dependencies are added to the StepEye runtime.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from pin_checkpoint import audit, digest, extract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; preserve earlier evidence")

    import numpy as np
    import torch
    from PIL import Image
    from fastai.vision.learner import create_unet_model
    from torchvision.models import resnet34
    import onnx
    import onnxruntime as ort
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from visual.pin_segmentation import segment, tiles

    torch.set_num_threads(2)
    globals_found = audit(args.checkpoint)
    state = extract(args.checkpoint)
    model = create_unet_model(resnet34, 2, (512, 512), pretrained=False).eval()
    model.load_state_dict(state, strict=True)
    image = Image.open(args.image).convert("RGB")
    first = next(tiles(image))[2]
    args.output.mkdir(parents=True)
    model_path = args.output / "paine-pin-segmentation.onnx"
    # Retain a tensor-only copy for future exports without optimizer containers.
    torch.save(dict(state), args.output / "model-state.pt")
    with torch.inference_mode():
        torch.onnx.export(
            model, torch.from_numpy(first), str(model_path), dynamo=False,
            input_names=["images"], output_names=["logits"], opset_version=17,
        )
    graph = onnx.load(model_path)
    onnx.checker.check_model(graph)
    if any(t.data_location == onnx.TensorProto.EXTERNAL for t in graph.graph.initializer):
        raise ValueError("Expected self-contained ONNX")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])

    # Compare every native-scale tile of one image, not a resolution sweep.
    max_error = 0.
    mismatches = pixels = 0
    pt_seconds = 0.
    with torch.inference_mode():
        for _, _, array in tiles(image):
            started = time.perf_counter()
            expected = model(torch.from_numpy(array)).numpy()
            pt_seconds += time.perf_counter() - started
            actual = session.run(None, {"images": array})[0]
            max_error = max(max_error, float(np.abs(expected - actual).max()))
            mismatches += int(np.count_nonzero(expected.argmax(1) != actual.argmax(1)))
            pixels += int(expected.shape[2] * expected.shape[3])
    mask, stats = segment(image, session)
    Image.fromarray(mask * 255).save(args.output / "mask.png")
    report = {
        "source_sha256": digest(args.checkpoint), "pickle_globals": globals_found,
        "weights_only": True, "fastcore_checkpoint_class_executed": False,
        "state_tensors": len(state), "strict_state_match": True,
        "parameters": sum(p.numel() for p in model.parameters()),
        "onnx_sha256": digest(model_path), "onnx_bytes": model_path.stat().st_size,
        "image_sha256": digest(args.image), "image_size": list(image.size),
        "input_shape": [1, 3, 512, 512], "output_shape": [1, 2, 512, 512],
        "logits_max_abs_error": max_error, "argmax_mismatch_pixels": mismatches,
        "compared_pixels_including_padding": pixels,
        "pytorch_inference_ms": round(pt_seconds * 1000, 3),
        "runtime": stats, "accuracy_evaluation": False,
        "torch_version": torch.__version__, "ort_version": ort.__version__,
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if max_error > 1e-3 or mismatches / pixels > 1e-4:
        raise ValueError("ONNX parity tolerance exceeded; do not register")


if __name__ == "__main__":
    main()
