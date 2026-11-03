"""Independent image-classification catalog and disposable CPU trials, never box labels."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from .label_jobs import LabelEngines, fingerprint
from .labels import LabelError, MAX_IMAGE_BYTES
from .model_routes import ModelTrials

PREPROCESSING = {"gray224", "imagenet_center224"}
log = logging.getLogger(__name__)


class ClassifierRegistry(LabelEngines):
    """Separate instance, not an annotation engine or a detection-training registry."""
    def register(self, engine_id, model_path, classes, name="", *, preprocessing, metadata=None):
        if preprocessing not in PREPROCESSING:
            raise LabelError("分类模型必须明确指定 gray224 或 imagenet_center224")
        if (not isinstance(classes, list) or not 2 <= len(classes) <= 1000
                or any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in classes)
                or len(set(classes)) != len(classes) or Path(model_path).suffix.lower() != ".onnx"):
            raise LabelError("分类模型类别或ONNX路径无效")
        super().register(engine_id, Path(model_path), classes, name, metadata={
            **(metadata or {}), "preprocessing": preprocessing, "task": "classify",
            "usage": "evaluation_only", "commercial_review_required": True})


def register_public_classifiers(classifiers: ClassifierRegistry, root: Path) -> int:
    """Only local pinned classification-models.json; no session, download or worker at boot."""
    root = Path(root).resolve(); manifest = root / "models/public/classification-models.json"
    if not manifest.is_file(): return 0
    try:
        data = json.loads(manifest.read_text(encoding="utf8"))
        if data["schema_version"] != 1 or not isinstance(data["models"], list):
            raise ValueError("unsupported classification manifest")
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("Public classification manifest invalid"); return 0
    count = 0
    for item in data["models"]:
        try:
            if item["task"] != "classify": continue
            path = (root / item["model_path"]).resolve()
            if not path.is_relative_to(root / "models/public") or path.suffix != ".onnx":
                raise ValueError("classifier outside public model directory")
            if not path.is_file(): continue
            if path.stat().st_size != item["bytes"] or fingerprint(path) != item["sha256"]:
                log.warning("Public classifier fingerprint mismatch"); continue
            classifiers.register(item["id"], path, item["classes"], item["name"],
                preprocessing=item["preprocessing"], metadata={"expected_sha256": item["sha256"],
                "license_status": item["license_status"], "source": item["source"]})
            count += 1
        except (OSError, ValueError, KeyError, TypeError, LabelError):
            log.warning("Public classifier entry invalid")
    return count


class ClassifierTrials:
    def __init__(self, classifiers: ClassifierRegistry, timeout_s: float = 30, slot=None):
        if not math.isfinite(timeout_s) or timeout_s <= 0: raise ValueError("invalid timeout")
        self.classifiers, self.timeout_s = classifiers, timeout_s
        self._slot = slot if slot is not None else threading.Lock()

    def catalog(self):
        return [{**m, "task": "classify", "runtime": "onnx", "validation": "unverified"}
                for m in self.classifiers.list()]

    def registered(self, engine_id):
        item = next((m for m in self.catalog() if m["id"] == engine_id), None)
        if item is None: raise LabelError("分类模型未登记", 404)
        return item

    def trial(self, engine_id, upload):
        if not self._slot.acquire(False): raise LabelError("已有模型试测正在运行，请稍后重试", 429)
        started = time.monotonic()
        try:
            item = self.registered(engine_id)
            if not item["available"]: raise LabelError(item["reason"], 409)
            raw = upload.read(MAX_IMAGE_BYTES + 1)
            if not raw or len(raw) > MAX_IMAGE_BYTES: raise LabelError("图片不能为空且最多20MB", 413)
            model = self.classifiers.snapshot(engine_id)
            with tempfile.TemporaryDirectory(prefix="stepeye-classify-trial-") as folder:
                image_path = Path(folder) / "image.png"
                width, height = ModelTrials._image(raw, image_path)
                spec = {**model, "image_path": str(image_path)}
                with tempfile.TemporaryFile() as output:
                    process = subprocess.Popen([sys.executable, "-m", "visual.classification_predict"],
                        cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                        stdout=output, stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    try:
                        process.communicate(json.dumps(spec).encode(), timeout=self.timeout_s)
                    except subprocess.TimeoutExpired:
                        raise LabelError("分类试测超时，进程已停止", 504) from None
                    finally:
                        if process.poll() is None: process.kill()
                        process.communicate()
                    output.seek(0); out = output.read(256 * 1024 + 1)
                if process.returncode or len(out) > 256 * 1024:
                    raise LabelError("分类试测失败，请检查模型输入、类别与预处理", 422)
                if fingerprint(Path(model["model_path"])) != model["model_sha256"]:
                    raise LabelError("试测期间模型发生变化", 409)
                try:
                    probabilities = json.loads(out)["probabilities"]
                    if not isinstance(probabilities, list) or len(probabilities) != len(model["classes"]): raise ValueError
                    for row, label in zip(probabilities, model["classes"]):
                        if (not isinstance(row, dict) or set(row) != {"label", "score"} or row["label"] != label
                                or type(row["score"]) not in (float, int) or not math.isfinite(row["score"])
                                or not 0 <= row["score"] <= 1): raise ValueError
                    if abs(sum(r["score"] for r in probabilities) - 1) > 1e-6: raise ValueError
                except (ValueError, TypeError, KeyError):
                    raise LabelError("分类试测返回无效概率或类别", 422) from None
            top = max(probabilities, key=lambda row: row["score"])
            return {"task": "classify", "model_id": engine_id, "model_sha256": model["model_sha256"],
                "classes": model["classes"], "preprocessing": model["preprocessing"],
                "probabilities": probabilities, "predicted_label": top["label"], "confidence": top["score"],
                "width": width, "height": height, "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "provider": "CPUExecutionProvider", "validation": "unverified"}
        except OSError:
            raise LabelError("无法启动分类试测，请检查本机运行环境", 503) from None
        finally:
            self._slot.release()
