"""MCC5-THU motor RF research inference; fixed engineered features, CPU only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import threading
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL_SHA = "54e8ef689293f3c1cb17c3cf578765e9757e622e9b071a79864fb763ebf2cee4"
MANIFEST_SHA = "c6acff26e53f3176f7314d67cc9bf4f31be3cb00be8a1bc3ca8e5f8e3892026e"
FEATURE_HELPER_SHA = "b46eb63529518484679440967045e66a176ad77ba59bdba6ad6801e75a9ab406"
Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
FeatureRow = Annotated[list[Finite], Field(min_length=114, max_length=114)]


class MotorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    complete_record: Literal[True]
    features: list[FeatureRow] = Field(default_factory=list, max_length=64)


class MotorBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    title: str = Field(default="", max_length=200)
    dataset: Literal["MCC5-THU-Motor"]
    feature_helper_sha256: Literal[FEATURE_HELPER_SHA]
    feature_names: list[str] = Field(min_length=114, max_length=114)
    records: list[MotorRecord] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_records(self):
        if len({r.id for r in self.records}) != len(self.records):
            raise ValueError("电机记录ID不能重复")
        return self


def pinned_bundle(root):
    bundle = Path(root) / "models/mechanism/motor-rf"
    for name, expected in (("model.onnx", MODEL_SHA), ("manifest.json", MANIFEST_SHA)):
        path = bundle / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("电机研究模型未安装")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("电机研究模型指纹不一致")
    return bundle


def aggregate(probabilities, lengths, ids, classes):
    """Same complete-record mean as weihu infer_motor_rf.aggregate; no health fallback."""
    rows, start = [], 0
    for count, identifier in zip(lengths, ids):
        row = {"id": identifier, "active_windows": count, "label": None, "probabilities": None}
        if count:
            mean = probabilities[start:start + count].mean(axis=0)
            row.update(label=classes[int(mean.argmax())], probabilities=mean.tolist(), status="shadow_evidence")
        else:
            row["status"] = "unsupported"
        rows.append(row)
        start += count
    return rows


class MotorService:
    def __init__(self, root):
        self.root = Path(root)
        self._slot = threading.Lock()

    def capabilities(self):
        try:
            bundle = pinned_bundle(self.root)
            manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf8"))
            return {"available": True, "dataset": "MCC5-THU-Motor", "classes": manifest["classes"],
                    "feature_names": manifest["feature_names"], "feature_helper_sha256": FEATURE_HELPER_SHA,
                    "max_records": 8, "max_windows_per_record": 64, "raw_waveform_input": False,
                    "production_approval": False, "supports_rul": False}
        except ValueError as error:
            return {"available": False, "reason": str(error)}

    def analyze(self, body):
        if not self._slot.acquire(blocking=False):
            raise RuntimeError("电机分析忙，请稍后重试")
        try:
            return self._analyze(body)
        finally:
            self._slot.release()

    def _analyze(self, body):
        import onnxruntime as ort
        from .model_signature import verify_model_policy

        body = body if isinstance(body, MotorBody) else MotorBody.model_validate(body)
        bundle = pinned_bundle(self.root)
        signature = verify_model_policy(bundle / "model.onnx")
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf8"))
        if body.feature_names != manifest["feature_names"]:
            raise ValueError("须使用原版114维特征定义与顺序")
        lengths = [len(r.features) for r in body.records]
        features = [row for r in body.records for row in r.features]
        started = time.perf_counter()
        if features:
            values = np.asarray(features, dtype=np.float32)
            if not np.isfinite(values).all():
                raise ValueError("特征超出float32范围")
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(str(bundle / "model.onnx"), options, providers=["CPUExecutionProvider"])
            try:
                probabilities = session.run([manifest["probability_output"]], {"features": values})[0]
            finally:
                del session
            if (probabilities.shape != (len(features), len(manifest["classes"]))
                    or not np.isfinite(probabilities).all()):
                raise ValueError("电机模型输出无效")
        else:
            probabilities = np.empty((0, len(manifest["classes"])), dtype=np.float32)
        rows = aggregate(probabilities, lengths, [r.id for r in body.records], manifest["classes"])
        return {"status": "shadow_evidence" if features else "unsupported", "records": rows,
                "classes": manifest["classes"], "model_sha256": MODEL_SHA, "signature": signature,
                "elapsed_ms": (time.perf_counter() - started) * 1000, "production_approval": False,
                "supports_rul": False, "cross_speed_generalization_passed": False,
                "input_scope": "uploaded engineered-feature and complete-record claims; not raw-sensor verification",
                "historical_protocol": manifest["protocol"],
                "scope": "MCC5-THU research candidate; failed 3000rpm transfer, no industrial admission"}
