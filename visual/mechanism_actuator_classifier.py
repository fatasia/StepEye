"""Cranfield actuator classifier: complete 80-second records, CPU on demand."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL_SHA = "6aa820a3b980a55f09dc6f2ba6c912c198ebabceba59f8ada9a16c32c0eeb89d"
MANIFEST_SHA = "3c494546fc38b3e2334206571cbdbe914316d3136d9f4a7aa7ece4753ec4e46f"
CLASSES = ("normal", "backlash", "lack_lubrication", "spalling")
Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Sample = Annotated[list[Finite], Field(min_length=3, max_length=3)]


class ActuatorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    complete_record: Literal[True]
    load: Literal["20", "40", "neg40"]
    profile: Literal["sin", "trap"]
    values: list[Sample] = Field(min_length=2000, max_length=2000)


class ActuatorClassifierBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=200)
    dataset: Literal["Cranfield linear actuator"]
    sample_rate_hz: Literal[25]
    channels: tuple[Literal["setpoint_mm"], Literal["error_mm"], Literal["current_A"]]
    records: list[ActuatorRecord] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_records(self):
        if len({r.id for r in self.records}) != len(self.records):
            raise ValueError("执行器记录 ID 不能重复")
        return self


def pinned_bundle(root):
    bundle = Path(root) / "models/mechanism/actuator-ensemble"
    for name, expected in (("model.onnx", MODEL_SHA), ("manifest.json", MANIFEST_SHA)):
        path = bundle / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("执行器分类模型未安装")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("执行器分类模型指纹不一致")
    return bundle


class ActuatorClassifierService:
    def __init__(self, root):
        self.root = Path(root)
        self._slot = threading.Lock()

    def capabilities(self):
        try:
            pinned_bundle(self.root)
            return {"available": True, "classes": list(CLASSES), "sample_rate_hz": 25,
                    "channels": ["setpoint_mm", "error_mm", "current_A"], "samples_per_record": 2000,
                    "max_records": 8, "production_approval": False, "supports_rul": False}
        except ValueError as error:
            return {"available": False, "reason": str(error)}

    def analyze(self, body):
        if not self._slot.acquire(blocking=False):
            raise RuntimeError("执行器分类忙，请稍后重试")
        try:
            return self._analyze(body)
        finally:
            self._slot.release()

    def _analyze(self, body):
        import onnxruntime as ort
        from .model_signature import verify_model_policy

        body = body if isinstance(body, ActuatorClassifierBody) else ActuatorClassifierBody.model_validate(body)
        bundle = pinned_bundle(self.root)
        signature = verify_model_policy(bundle / "model.onnx")
        values = np.asarray([r.values for r in body.records], dtype=np.float32)
        if not np.isfinite(values).all():
            raise ValueError("信号超出 float32 范围")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        started = time.perf_counter()
        session = ort.InferenceSession(str(bundle / "model.onnx"), options, providers=["CPUExecutionProvider"])
        try:
            probabilities, candidates = session.run(["probabilities", "candidate_set"], {"values": values})
        finally:
            del session  # No resident model or GPU allocation between requests.
        if (probabilities.shape != (len(body.records), 4) or candidates.shape != probabilities.shape
                or not np.isfinite(probabilities).all()):
            raise ValueError("执行器模型输出无效")
        rows = []
        for record, probability, candidate in zip(body.records, probabilities, candidates):
            labels = [name for name, selected in zip(CLASSES, candidate) if selected]
            # Empty/multiple candidate sets require review; never default to normal.
            rows.append({"id": record.id, "status": "shadow_evidence" if len(labels) == 1 else "review",
                         "label": labels[0] if len(labels) == 1 else None,
                         "top_label": CLASSES[int(probability.argmax())], "candidate_set": labels,
                         "probabilities": probability.tolist()})
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf8"))
        return {"status": "shadow_evidence", "records": rows, "classes": list(CLASSES),
                "model_sha256": MODEL_SHA, "signature": signature,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
                "production_approval": False, "supports_rul": False,
                "cross_condition_generalization_passed": False,
                "scope": "Cranfield 已知六种负载/轨迹；上传记录的设备与工况未独立验证",
                "historical_protocol": manifest["protocol"]}
