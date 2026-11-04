"""Fixed-shape VideoMAEv2 ONNX adapter, isolated from the real-time vision process.

Only RGB uint8 clips [16,224,224,3] enter the persistent worker. No checkpoint pickle,
model download, label remapping, or preprocessing enhancement happens at runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from typing import Sequence

import numpy as np

INPUT_SHAPE = (1, 3, 16, 224, 224)
CLIP_SHAPE = (16, 224, 224, 3)
MEAN = np.asarray([.485, .456, .406], dtype=np.float32)
STD = np.asarray([.229, .224, .225], dtype=np.float32)
_MAX_LINE = 128 * 1024


class ActionPredictError(RuntimeError):
    pass


def model_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_frame(frame: np.ndarray) -> np.ndarray:
    """Official numpy-array path uses OpenCV INTER_LINEAR, not Pillow interpolation."""
    if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
        raise ActionPredictError("Action frames must be RGB uint8 HWC images")
    h, w = frame.shape[:2]
    if min(h, w) < 1 or max(h, w) > 16384:
        raise ActionPredictError("Invalid action frame dimensions")
    nh, nw = (int(256 * h / w), 256) if w < h else (256, int(256 * w / h))
    if max(nh, nw) > 4096:
        raise ActionPredictError("Action ROI aspect ratio exceeds the preprocessing bound")
    import cv2
    scaled = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
    y, x = int(round((nh - 224) / 2.)), int(round((nw - 224) / 2.))
    return np.ascontiguousarray(scaled[y:y + 224, x:x + 224])


def prepare_clip(frames_rgb: Sequence[np.ndarray]) -> np.ndarray:
    """Official validation spatial recipe: short side 256 bilinear, center 224.

    Temporal sampling belongs to the collector. Exactly 16 distinct timed observations
    are required there; this function never duplicates/pads a short clip.
    """
    if len(frames_rgb) != 16:
        raise ActionPredictError("Action inference requires exactly 16 sampled frames")
    return np.ascontiguousarray([prepare_frame(frame) for frame in frames_rgb], dtype=np.uint8)


def normalize_clip(clip: np.ndarray) -> np.ndarray:
    """RGB /255, ImageNet mean/std; returns contiguous float32 BCTHW."""
    if not isinstance(clip, np.ndarray) or clip.shape != CLIP_SHAPE or clip.dtype != np.uint8:
        raise ActionPredictError("Expected RGB uint8 clip [16,224,224,3]")
    normalized = (clip.astype(np.float32) / np.float32(255) - MEAN) / STD
    return np.ascontiguousarray(normalized.transpose(3, 0, 1, 2)[None])


def action_result(logits, class_names: list[str], sha256: str, inference_ms: float = 0) -> dict:
    values = np.asarray(logits)
    if (values.shape != (1, len(class_names)) or values.dtype.kind != "f" or not np.isfinite(values).all()
            or not class_names or len(class_names) > 2000):
        raise ActionPredictError("Action logits do not match the configured class map")
    values = values[0].astype(np.float64)
    scores = np.exp(values - values.max())
    scores /= scores.sum()
    order = np.argsort(-scores, kind="stable")[:min(5, len(scores))]
    best = int(order[0])
    return {"label": class_names[best], "class_id": best, "confidence": float(scores[best]),
            "model_sha256": sha256, "inference_ms": float(inference_ms),
            "top_k": [{"class_id": int(i), "label": class_names[i], "confidence": float(scores[i])} for i in order]}


class ActionPredictor:
    """One persistent worker, no silent provider fallback. Construct outside the frame loop.

    predict is serialized and bounded, including IPC. A timeout kills the worker; create
    a new predictor explicitly to retry. close is idempotent and kills an in-flight call.
    """
    def __init__(self, model: Path, class_names: list[str], timeout_s: float = 10,
                 *, python_executable: str = sys.executable, providers=("CPUExecutionProvider",),
                 expected_sha256: str | None = None, intra_op_threads: int = 2):
        self.model = Path(model).resolve()
        if not self.model.is_file() or self.model.suffix.lower() != ".onnx":
            raise ActionPredictError("Action ONNX model is unavailable; configure a local model")
        if (not isinstance(class_names, list) or not 1 <= len(class_names) <= 2000
                or not all(isinstance(v, str) and 0 < len(v) <= 100 for v in class_names)
                or len(set(class_names)) != len(class_names)):
            raise ActionPredictError("An explicit unique action class map is required")
        if (not math.isfinite(timeout_s) or not .05 <= timeout_s <= 120
                or type(intra_op_threads) is not int or not 1 <= intra_op_threads <= 16):
            raise ActionPredictError("Invalid action timeout/thread limit")
        if not providers or any(v not in ("CPUExecutionProvider", "CUDAExecutionProvider") for v in providers):
            raise ActionPredictError("Action provider must be explicit CPU or CUDA")
        self.timeout_s = timeout_s
        self.model_sha256 = model_sha256(self.model)
        if expected_sha256 is not None and self.model_sha256 != expected_sha256:
            raise ActionPredictError("Action model fingerprint differs from the approved model")
        from .model_signature import verify_model_policy
        verify_model_policy(self.model)
        self.class_names = list(class_names)
        self._lock = threading.Lock()
        self._closed = False
        self._temp = tempfile.TemporaryDirectory(prefix="stepeye-action-")
        self._clip = Path(self._temp.name) / "clip.npy"
        self._responses: queue.Queue = queue.Queue(maxsize=1)
        command = [str(python_executable), "-m", "visual.action_predict", "--worker"]
        try:
            self._process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            self._temp.cleanup()
            raise ActionPredictError("Action worker Python environment is unavailable") from None
        def read_responses():
            try:
                while True:
                    line = self._process.stdout.readline(_MAX_LINE + 1)
                    if not line or len(line) > _MAX_LINE:
                        raise ValueError
                    value = json.loads(line)
                    self._responses.put_nowait(value)
            except (OSError, ValueError, queue.Full):
                try:
                    self._responses.put_nowait({"error": "Action worker stopped or returned invalid output"})
                except queue.Full:
                    pass
        self._reader = threading.Thread(target=read_responses, name="action-results", daemon=True)
        self._reader.start()
        self._sequence = 0
        try:
            self._send({"model": str(self.model), "clip": str(self._clip), "classes": self.class_names,
                        "sha256": self.model_sha256, "providers": list(providers), "threads": intra_op_threads})
            ready = self._receive()
            if ready.get("ready") is not True or ready.get("model_sha256") != self.model_sha256:
                raise ActionPredictError(ready.get("error", "Action worker initialization failed"))
            self.providers = ready["providers"]
        except Exception:
            self.close()
            raise

    def _send(self, data):
        raw = (json.dumps(data, ensure_ascii=True) + "\n").encode("ascii")
        if len(raw) > _MAX_LINE:
            raise ActionPredictError("Action IPC request exceeds the size limit")
        done, failed = threading.Event(), []
        def write():
            try:
                self._process.stdin.write(raw); self._process.stdin.flush()
            except (OSError, ValueError):
                failed.append(True)
            finally:
                done.set()
        threading.Thread(target=write, daemon=True).start()
        if not done.wait(self.timeout_s) or failed:
            raise ActionPredictError("Action worker IPC timed out or is unavailable")

    def _receive(self, timeout=None):
        try:
            response = self._responses.get(timeout=self.timeout_s if timeout is None else timeout)
        except queue.Empty:
            raise ActionPredictError("Action worker timed out") from None
        if not isinstance(response, dict) or response.get("error"):
            raise ActionPredictError("Action worker rejected the model or inference request")
        return response

    def predict(self, clip: np.ndarray) -> dict:
        if not isinstance(clip, np.ndarray) or clip.shape != CLIP_SHAPE or clip.dtype != np.uint8:
            raise ActionPredictError("Expected RGB uint8 clip [16,224,224,3]")
        start = time.monotonic()
        if not self._lock.acquire(timeout=self.timeout_s):
            raise ActionPredictError("Action predictor is busy")
        try:
            if self._closed:
                raise ActionPredictError("Action predictor is closed")
            np.save(self._clip, clip, allow_pickle=False)
            self._sequence += 1
            self._send({"sequence": self._sequence})
            remaining = self.timeout_s - (time.monotonic() - start)
            if remaining <= 0:
                raise ActionPredictError("Action worker timed out")
            response = self._receive(remaining)
            result = response.get("result", {})
            if (response.get("sequence") != self._sequence or result.get("model_sha256") != self.model_sha256
                    or result.get("label") not in self.class_names or not math.isfinite(result.get("confidence", float("nan")))
                    or not 0 <= result["confidence"] <= 1):
                raise ActionPredictError("Action worker returned an invalid prediction")
            return {**result, "roundtrip_ms": (time.monotonic() - start) * 1000}
        except Exception:
            self.close()
            raise
        finally:
            self._lock.release()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._process.poll() is None:
                self._process.kill()
            self._process.wait(timeout=3)
        finally:
            self._reader.join(timeout=1)
            self._process.stdin.close(); self._process.stdout.close()
            self._temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _worker():
    try:
        import onnxruntime as ort
        line = sys.stdin.buffer.readline(_MAX_LINE + 1)
        if len(line) > _MAX_LINE:
            raise ValueError
        spec = json.loads(line)
        model = Path(spec["model"])
        if model_sha256(model) != spec["sha256"]:
            raise ValueError
        providers = spec["providers"]
        if any(v not in ort.get_available_providers() for v in providers):
            raise ValueError
        from .action_runtime import session_options
        options = session_options(ort, providers, spec["threads"])
        session = ort.InferenceSession(str(model), sess_options=options, providers=providers)
        session.disable_fallback()  # Runtime failures must not switch to CPU silently.
        if session.get_providers()[0] != providers[0] or model_sha256(model) != spec["sha256"]:
            raise ValueError
        inputs, outputs = session.get_inputs(), session.get_outputs()
        if (len(inputs) != 1 or inputs[0].type != "tensor(float)" or tuple(inputs[0].shape) != INPUT_SHAPE
                or len(outputs) != 1 or outputs[0].shape != [1, len(spec["classes"])]):
            raise ValueError
        print(json.dumps({"ready": True, "model_sha256": spec["sha256"], "providers": session.get_providers()}), flush=True)
        clip_path = Path(spec["clip"])
        for line in sys.stdin.buffer:
            if len(line) > _MAX_LINE:
                raise ValueError
            request = json.loads(line)
            if clip_path.stat().st_size > 3 * 1024 * 1024:
                raise ValueError
            clip = np.load(clip_path, allow_pickle=False)
            tensor = normalize_clip(clip)
            start = time.monotonic()
            logits = session.run(None, {inputs[0].name: tensor})[0]
            result = action_result(logits, spec["classes"], spec["sha256"], (time.monotonic() - start) * 1000)
            print(json.dumps({"sequence": request["sequence"], "result": result}, allow_nan=False), flush=True)
        return 0
    except Exception:
        print(json.dumps({"error": "Action ONNX worker failed"}), flush=True)
        return 2


def main():
    parser = argparse.ArgumentParser(description="StepEye isolated action worker; not a model downloader")
    parser.add_argument("--worker", action="store_true", required=True)
    parser.parse_args()
    return _worker()


if __name__ == "__main__":
    raise SystemExit(main())
