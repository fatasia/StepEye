"""Versioned planar homography, not lens calibration or 3-D metrology."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class Correspondence(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    pixel: tuple[float, float]
    physical: tuple[float, float]


class CalibrationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    scope_id: str = Field(min_length=1, max_length=160)
    reference: str = Field(min_length=1, max_length=200)
    image_size: tuple[int, int]
    plane: Literal["ground", "workpiece"]
    unit: Literal["m", "mm"]
    points: list[Correspondence] = Field(min_length=4, max_length=100)
    validation_points: list[Correspondence] = Field(min_length=2, max_length=100)
    tolerance: float = Field(gt=0, le=1000)


class MeasureBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    scope_id: str
    reference: str
    image_size: tuple[int, int]
    points: tuple[tuple[float, float], tuple[float, float]]


def _pixels(points, size):
    width, height = size
    if not (2 <= width <= 100000 and 2 <= height <= 100000):
        raise ValueError("图像尺寸无效")
    values = np.asarray(points, dtype=np.float64)
    if not np.isfinite(values).all() or np.any(values < 0) or np.any(values > [width - 1, height - 1]):
        raise ValueError("像素坐标超出图像范围")
    return values


def _normalize(points):
    center = points.mean(axis=0)
    scale = np.sqrt(2) / max(float(np.linalg.norm(points - center, axis=1).mean()), 1e-15)
    matrix = np.array([[scale, 0, -scale * center[0]], [0, scale, -scale * center[1]], [0, 0, 1.]])
    return (points - center) * scale, matrix


def _fit(source, target):
    """Normalized DLT rejects rank-deficient and badly conditioned correspondences."""
    rows = []
    src, a = _normalize(source)
    dst, b = _normalize(target)
    for (x, y), (u, v) in zip(src, dst):
        rows.extend([[-x, -y, -1, 0, 0, 0, u*x, u*y, u],
                     [0, 0, 0, -x, -y, -1, v*x, v*y, v]])
    _, singular, vectors = np.linalg.svd(rows, full_matrices=True)
    # Four pairs have eight singular values; larger sets have a null ninth value.
    if singular[7] < singular[0] * 1e-8:
        raise ValueError("标定点退化或过于接近共线")
    h = np.linalg.inv(b) @ vectors[-1].reshape(3, 3) @ a
    if not np.isfinite(h).all() or np.linalg.matrix_rank(h) < 3:
        raise ValueError("标定变换退化")
    return h / np.linalg.norm(h)


def _project(points, h):
    mapped = np.column_stack([points, np.ones(len(points))]) @ h.T
    if np.any(np.abs(mapped[:, 2]) < 1e-12):
        raise ValueError("点位位于透视奇异区域")
    result = mapped[:, :2] / mapped[:, 2:]
    if not np.isfinite(result).all():
        raise ValueError("变换结果无效")
    return result


def _within(points, hull):
    contour = np.asarray(hull, dtype=np.float32)
    if any(cv2.pointPolygonTest(contour, (float(p[0]), float(p[1])), False) < 0 for p in points):
        raise ValueError("点位超出标定覆盖区域，禁止外推")


class CalibrationStore:
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS plane_calibrations "
                         "(id TEXT PRIMARY KEY, scope TEXT NOT NULL, version INTEGER NOT NULL, "
                         "body TEXT NOT NULL, UNIQUE(scope,version))")
        self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()

    def create(self, body: CalibrationBody | dict):
        body = CalibrationBody.model_validate(body)
        if (body.plane, body.unit) not in (("ground", "m"), ("workpiece", "mm")):
            raise ValueError("地面使用 m，工件平面使用 mm")
        if not body.scope_id.strip() or not body.reference.strip():
            raise ValueError("来源与参考状态不能为空")
        physical_values = np.array([p.physical for p in body.points + body.validation_points])
        if np.any(np.abs(physical_values) > 1e9):
            raise ValueError("物理坐标超出支持范围")
        source = _pixels([p.pixel for p in body.points], body.image_size)
        target = np.array([p.physical for p in body.points])
        if len(np.unique(source, axis=0)) != len(source) or len(np.unique(target, axis=0)) != len(target):
            raise ValueError("标定点不得重复")
        h = _fit(source, target)
        hull = cv2.convexHull(source.astype(np.float32)).reshape(-1, 2)
        if len(hull) < 3 or cv2.contourArea(hull) < 1:
            raise ValueError("标定覆盖区域退化")
        denominators = np.column_stack([hull, np.ones(len(hull))]) @ h[2]
        if np.min(denominators) * np.max(denominators) <= 0:
            raise ValueError("标定区域穿过透视奇异线")
        checks = _pixels([p.pixel for p in body.validation_points], body.image_size)
        _within(checks, hull)
        if len(np.unique(checks, axis=0)) != len(checks) or any(np.linalg.norm(source - p, axis=1).min() < 1e-6 for p in checks):
            raise ValueError("验证点必须独立且不得重复使用标定点")
        errors = np.linalg.norm(_project(checks, h) - np.array([p.physical for p in body.validation_points]), axis=1)
        fit_errors = np.linalg.norm(_project(source, h) - target, axis=1)
        record = body.model_dump(mode="json")
        record.update(id=uuid.uuid4().hex, created=datetime.now(timezone.utc).isoformat(),
                      matrix=h.tolist(), coverage=hull.tolist(), validation_errors=errors.tolist(),
                      validation_rmse=float(np.sqrt(np.mean(errors**2))), validation_max=float(errors.max()),
                      fit_max=float(fit_errors.max()),
                      validated=bool(max(errors.max(), fit_errors.max()) <= body.tolerance))
        # A transaction allocates each scope version atomically, including across store instances.
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                version = self._db.execute("SELECT COALESCE(MAX(version),0)+1 FROM plane_calibrations WHERE scope=?", (body.scope_id,)).fetchone()[0]
                record["version"] = version
                self._db.execute("INSERT INTO plane_calibrations VALUES(?,?,?,?)", (record["id"], body.scope_id, version, json.dumps(record, allow_nan=False)))
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        return record

    def get(self, calibration_id):
        with self._lock:
            row = self._db.execute("SELECT body FROM plane_calibrations WHERE id=?", (calibration_id,)).fetchone()
        if not row:
            raise KeyError("标定版本不存在")
        return json.loads(row[0])

    def list(self, scope_id=None):
        with self._lock:
            rows = self._db.execute("SELECT body FROM plane_calibrations WHERE (? IS NULL OR scope=?) ORDER BY scope,version DESC", (scope_id, scope_id)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def measure(self, calibration_id, body: MeasureBody | dict):
        body = MeasureBody.model_validate(body)
        record = self.get(calibration_id)
        if not record["validated"]:
            raise ValueError("标定未通过独立验证，不能测量")
        if (body.scope_id != record["scope_id"] or body.reference != record["reference"]
                or list(body.image_size) != record["image_size"]):
            raise ValueError("画面来源、参考状态或尺寸与标定不匹配")
        points = _pixels(body.points, body.image_size)
        _within(points, record["coverage"])
        physical = _project(points, np.array(record["matrix"]))
        return {"calibration_id": calibration_id, "version": record["version"],
                "plane": record["plane"], "unit": record["unit"], "physical_points": physical.tolist(),
                "distance": float(np.linalg.norm(physical[1] - physical[0])),
                "validation_max": record["validation_max"], "validation_rmse": record["validation_rmse"],
                "scope_id": record["scope_id"], "reference": record["reference"], "planar_only": True}
