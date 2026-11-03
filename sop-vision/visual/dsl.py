"""SOP 工位配置 DSL：加载、校验、编译（foreach 展开、可选步骤的有效前驱）。"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Zone(BaseModel):
    id: str
    polygon: list[tuple[float, float]] = Field(min_length=3)  # 归一化坐标 0~1，与分辨率无关


class HandDwell(BaseModel):
    zone: str
    min_ms: int = 0


class ObjState(BaseModel):
    zone: str
    state: str
    min_ms: int = 0


class SignalCond(BaseModel):
    name: str
    value: Any = None
    source: str | None = None
    hold_ms: int = Field(default=0, ge=0, le=600000)


class PersonPresent(BaseModel):
    zone: str
    min_ms: int = 0


class PersonAbsent(BaseModel):
    zone: str
    min_ms: int = 0


class PersonCount(BaseModel):
    zone: str
    op: Literal["ge", "le"] = "ge"
    count: int = 1


class VlmCheck(BaseModel):
    """语言层步骤判定:VLM 对周期内抽帧判定提示词是否成立(泛化靠语言,不靠坐标)。"""
    prompt: str
    every_s: float = 2.0  # 抽帧间隔
    min_conf: float = 0.6

    @property
    def key(self) -> str:
        import hashlib
        return f"vlm:{hashlib.sha256(self.prompt.encode()).hexdigest()[:10]}"


class Cond(BaseModel):
    hand_dwell: HandDwell | None = None
    obj_state: ObjState | None = None
    signal: SignalCond | None = None
    person_present: PersonPresent | None = None
    person_absent: PersonAbsent | None = None
    person_count: PersonCount | None = None
    vlm_check: VlmCheck | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "Cond":
        n = sum(x is not None for x in (self.hand_dwell, self.obj_state, self.signal,
                                        self.person_present, self.person_absent, self.person_count,
                                        self.vlm_check))
        if n != 1:
            raise ValueError("条件必须且只能包含 hand_dwell / obj_state / signal / person_* / vlm_check 之一")
        return self

    @property
    def kind(self) -> str:
        if self.hand_dwell:
            return "hand_dwell"
        if self.person_present:
            return "person_present"
        if self.person_absent:
            return "person_absent"
        if self.person_count:
            return "person_count"
        if self.vlm_check:
            return "vlm_check"
        return "obj_state" if self.obj_state else "signal"

    @property
    def zone(self) -> str | None:
        if self.vlm_check:
            return self.vlm_check.key  # 伪区域:判定结果以 OBJ_STATE(key, ok) 事件进引擎
        for c in (self.hand_dwell, self.obj_state, self.person_present, self.person_absent, self.person_count):
            if c is not None:
                return c.zone
        return None

    def describe(self) -> str:
        if self.hand_dwell:
            d = self.hand_dwell
            return f"手在「{d.zone}」停留 ≥{d.min_ms}ms"
        if self.obj_state:
            o = self.obj_state
            return f"「{o.zone}」处于 {o.state}" + (f" ≥{o.min_ms}ms" if o.min_ms else "")
        if self.person_present:
            p = self.person_present
            return f"「{p.zone}」有人持续 ≥{p.min_ms}ms"
        if self.person_absent:
            p = self.person_absent
            return f"「{p.zone}」无人持续 ≥{p.min_ms}ms"
        if self.person_count:
            p = self.person_count
            return f"「{p.zone}」人数 {'≥' if p.op == 'ge' else '≤'}{p.count}"
        if self.vlm_check:
            return f"VLM 判定:{self.vlm_check.prompt}"
        return f"收到信号 {self.signal.name}" + (f"={self.signal.value}" if self.signal.value is not None else "")


class Evidence(BaseModel):
    all: list[Cond] = []
    any: list[Cond] = []
    seq: list[Cond] = []

    @model_validator(mode="after")
    def _exactly_one(self) -> "Evidence":
        if sum(bool(x) for x in (self.all, self.any, self.seq)) != 1:
            raise ValueError("evidence 必须且只能使用 all / any / seq 之一")
        return self

    @property
    def mode(self) -> Literal["all", "any", "seq"]:
        return "all" if self.all else "any" if self.any else "seq"

    @property
    def conds(self) -> list[Cond]:
        return self.all or self.any or self.seq


class Foreach(BaseModel):
    var: str
    values: list[str] = Field(min_length=1)
    order: Literal["strict", "any"] = "strict"


class Step(BaseModel):
    id: str
    name: str = ""
    after: list[str] | None = None  # None=紧跟上一步；[]=无前驱（可并行起步）
    evidence: Evidence
    optional: bool = False
    timeout_s: float | None = None
    foreach: Foreach | None = None


class CycleDef(BaseModel):
    start: Cond
    end: Cond
    takt_s: float | None = None
    overtime_ratio: float = 1.2
    max_cycle_s: float = 600.0
    grace_ms: int = 1500  # 周期开始后多久才开始检查“先于前驱完成”
    uncertain_quality_s: float = 1.0


class HandsCfg(BaseModel):
    enabled: bool = True
    model: str = "models/hand_landmarker.task"
    points: list[int] = [0, 4, 8, 12, 9]  # 手腕/拇指尖/食指尖/中指尖/中指根
    num_hands: int = 2
    min_conf: float = 0.5
    enter_frames: int = 2
    exit_frames: int = 4


class PersonsCfg(BaseModel):
    """人员感知(MediaPipe Pose,Apache-2.0):S16 无人值守 / S17 人员聚集 / S4 跌倒的条件来源。"""
    enabled: bool = False
    model: str = "models/pose_landmarker_lite.task"
    max_poses: int = 3
    min_conf: float = 0.5
    enter_frames: int = 2
    exit_frames: int = 6
    fall_enabled: bool = False  # S4:躯干倾角规则判倒地,免训练


class DetectorCfg(BaseModel):
    """目标检测:.onnx(当前仅 YOLO 风格布局,onnxruntime)或 .tflite(MediaPipe)。

    按扩展名自动选引擎;classes=要盯的类别,class_names=模型类别表(ONNX 必填,缺省 COCO80)。
    """
    enabled: bool = False
    model: str = "models/efficientdet_lite0.tflite"
    classes: list[str] = ["person"]  # 要盯的类别(须与模型标签一致)
    class_names: list[str] = []  # ONNX 模型的完整类别表(顺序=id);缺省按 COCO80
    max_results: int = 5
    min_conf: float = 0.5
    enter_frames: int = 2
    exit_frames: int = 6


class SafetyRuleCfg(BaseModel):
    id: str
    kind: Literal["person_absent", "person_count", "fall", "object_present"]
    zone: str
    min_s: float = 5.0
    count_ge: int = 1
    severity: Literal["warn", "error"] = "warn"
    name: str = ""
    target: str = ""  # object_present 专用:要盯的类别名(须与检测模型标签一致)


class SafetyCfg(BaseModel):
    """常驻安全规则:与生产周期无关的持续监控(S16/S17),由 SafetyMonitor 执行。"""
    enabled: bool = False
    rules: list[SafetyRuleCfg] = []


class RoiStateCfg(BaseModel):
    zone: str
    mode: Literal["diff_empty", "template"]
    positive_state: str = "present"
    negative_state: str = "absent"
    threshold: float | None = None
    min_frames: int = 5
    margin_px: int = 6


class QualityCfg(BaseModel):
    enabled: bool = False
    blur_min: float | None = None
    brightness: tuple[float, float] | None = None
    max_shift_px: float | None = None
    lighting_drift: float | None = 0.3  # 背景亮度相对漂移阈值:超过判"光照突变"并降级;持续后自适应
    every_n: int = Field(default=5, ge=1)
    bad_confirm: int = Field(default=3, ge=1)
    good_confirm: int = Field(default=5, ge=1)
    recovery_ratio: float = Field(default=0.8, gt=0, le=1)
    min_background_fraction: float = Field(default=0.05, ge=0, le=1)
    min_background_pixels: int = Field(default=64, ge=1)
    background_texture_min: float = Field(default=1.0, ge=0)
    max_dark_fraction: float | None = Field(default=0.35, ge=0, le=1)
    max_bright_fraction: float | None = Field(default=0.25, ge=0, le=1)
    dark_pixel: int = Field(default=8, ge=0, le=255)
    bright_pixel: int = Field(default=247, ge=0, le=255)
    tile_grid: int = Field(default=4, ge=1, le=16)
    max_tile_dark_fraction: float | None = Field(default=0.8, ge=0, le=1)
    max_tile_bright_fraction: float | None = Field(default=0.8, ge=0, le=1)
    tile_lighting_drift: float | None = Field(default=0.45, ge=0)
    max_drifted_tile_fraction: float = Field(default=0.15, ge=0, le=1)


class SourceCfg(BaseModel):
    type: Literal["sim", "file", "rtsp", "camera"] = "sim"
    uri: str | int | None = None
    preview_stream: str | None = Field(default=None, max_length=160,
                                       pattern=r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")
    fps: float = 25.0
    realtime: bool = True
    loop: bool = False
    width: int = 640
    height: int = 480
    shadow: bool = False  # 影子模式:判定照跑,结果只进影子库不进正式统计(上线验收用)
    scenarios: list[Literal["ok", "missing", "wrong_order"]] = ["ok", "missing", "wrong_order"]  # 仅 sim


class ActionShadowCfg(BaseModel):
    """本机管理员配置的动作旁路；不产生 SOP 条件或生产事件。"""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, protected_namespaces=())
    enabled: bool = False
    model: str = ""
    class_names: list[str] = Field(default_factory=list, max_length=2000)
    model_sha256: str = Field(default="", pattern=r"^(?:[a-fA-F0-9]{64})?$")
    roi: tuple[float, float, float, float] = (0., 0., 1., 1.)
    trigger_zone: str = ""
    active_steps: list[str] = Field(default_factory=list, max_length=128)
    min_conf: float = Field(default=.7, ge=0, le=1)
    ttl_ms: int = Field(default=1000, ge=100, le=10000)
    max_gap_ms: int = Field(default=300, ge=125, le=1000)
    timeout_s: float = Field(default=10., gt=0, le=60)
    idle_s: float = Field(default=60., ge=.05, le=3600)
    providers: list[Literal["CPUExecutionProvider", "CUDAExecutionProvider"]] = Field(
        default_factory=lambda: ["CPUExecutionProvider"], min_length=1, max_length=2)

    @model_validator(mode="after")
    def _validate_action(self) -> "ActionShadowCfg":
        x0, y0, x1, y1 = self.roi
        if not all(math.isfinite(v) for v in self.roi) or not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("action_shadow.roi 必须为有限归一化 xyxy")
        if any(not c.strip() or c != c.strip() or len(c) > 100 for c in self.class_names) or len(set(self.class_names)) != len(self.class_names):
            raise ValueError("action_shadow.class_names 必须非空且不重复")
        return self


class StationConfig(BaseModel):
    station: str
    name: str = ""
    line: str = ""
    source: SourceCfg = SourceCfg()
    signal_sources: list[str] = Field(default_factory=list, max_length=32)
    zones: list[Zone]
    hands: HandsCfg = HandsCfg()
    persons: PersonsCfg = PersonsCfg()
    detector: DetectorCfg = DetectorCfg()
    safety: SafetyCfg = SafetyCfg()
    roi_states: list[RoiStateCfg] = []
    quality: QualityCfg = QualityCfg()
    action_shadow: ActionShadowCfg = Field(default_factory=ActionShadowCfg)
    cycle: CycleDef | None = None  # 纯安全岗(仅常驻规则)可不定义周期
    steps: list[Step] = []
    violations: dict[str, Literal["error", "warn"]] = {}

    @model_validator(mode="after")
    def _check_refs(self) -> "StationConfig":
        if not self.steps and not self.safety.rules:
            raise ValueError("工位至少要有 SOP 步骤或常驻安全规则之一")
        if self.steps and self.cycle is None:
            raise ValueError("有 SOP 步骤就必须定义 cycle(周期开始/结束条件)")
        ids = [z.id for z in self.zones]
        if len(set(ids)) != len(ids):
            raise ValueError("zone id 重复")
        known = set(ids)
        if self.action_shadow.trigger_zone and self.action_shadow.trigger_zone not in known:
            raise ValueError("action_shadow.trigger_zone 引用了未定义的 zone")
        if any(sid not in {s.id for s in self.steps} for sid in self.action_shadow.active_steps):
            raise ValueError("action_shadow.active_steps 引用了未定义的 step")
        conds: list[Cond] = []
        if self.cycle is not None:
            conds += [self.cycle.start, self.cycle.end]
        for s in self.steps:
            conds += s.evidence.conds
        for c in conds:
            # foreach 模板变量在编译期替换后再校验;vlm_check 的伪区域由运行时判定产生,不要求预定义
            if c.zone and c.kind != "vlm_check" and "${" not in c.zone and c.zone not in known:
                raise ValueError(f"条件引用了未定义的 zone: {c.zone}")
        for r in self.roi_states:
            if r.zone not in known:
                raise ValueError(f"roi_states 引用了未定义的 zone: {r.zone}")
        return self


@dataclass(slots=True)
class CompiledStep:
    id: str
    name: str
    evidence: Evidence
    optional: bool
    timeout_s: float | None
    preds: list[str]
    eff_preds: list[str]  # 跳过可选步骤后的有效前驱


@dataclass(slots=True)
class Program:
    station: str
    steps: list[CompiledStep]
    cycle: CycleDef | None
    severity: dict[str, str]
    rules_hash: str

    @staticmethod
    def from_config(cfg: StationConfig) -> "Program":
        steps = compile_steps(cfg.steps)
        known = {z.id for z in cfg.zones}
        for st in steps:
            for c in st.evidence.conds:
                if c.zone and c.kind != "vlm_check" and c.zone not in known:
                    raise ValueError(f"步骤 {st.id} 引用了未定义的 zone: {c.zone}")
        payload = json.dumps(
            {
                "zones": [z.model_dump() for z in cfg.zones],
                "roi_states": [r.model_dump() for r in cfg.roi_states],
                "cycle": cfg.cycle.model_dump() if cfg.cycle else None,
                "steps": [s.model_dump() for s in cfg.steps],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        severity = {"missing_step": "error", "wrong_order": "error", "step_timeout": "warn", "cycle_overtime": "warn"}
        severity.update(cfg.violations)
        return Program(cfg.station, steps, cfg.cycle, severity, hashlib.sha256(payload.encode()).hexdigest()[:16])


def compile_steps(steps: list[Step]) -> list[CompiledStep]:
    out: list[CompiledStep] = []
    seen: set[str] = set()
    group_leaves: dict[str, list[str]] = {}
    prev_leaves: list[str] = []

    def resolve(refs: list[str]) -> list[str]:
        res: list[str] = []
        for r in refs:
            if r in group_leaves:
                res += group_leaves[r]
            elif r in seen:
                res.append(r)
            else:
                raise ValueError(f"after 引用了未定义或位于其后的步骤: {r}")
        return list(dict.fromkeys(res))

    for s in steps:
        if s.id in seen or s.id in group_leaves:
            raise ValueError(f"步骤 id 重复: {s.id}")
        base = resolve(s.after) if s.after is not None else list(prev_leaves)
        if s.foreach is None:
            out.append(CompiledStep(s.id, s.name or s.id, s.evidence, s.optional, s.timeout_s, base, []))
            seen.add(s.id)
            leaves = [s.id]
        else:
            f = s.foreach
            ids: list[str] = []
            raw = json.dumps(s.evidence.model_dump(exclude_none=True))
            for i, v in enumerate(f.values):
                sid = f"{s.id}.{v}"
                ev = Evidence.model_validate(json.loads(raw.replace("${" + f.var + "}", v)))
                preds = base if (f.order == "any" or i == 0) else [ids[-1]]
                out.append(CompiledStep(sid, f"{s.name or s.id} {v}", ev, s.optional, s.timeout_s, preds, []))
                seen.add(sid)
                ids.append(sid)
            leaves = [ids[-1]] if f.order == "strict" else ids
            group_leaves[s.id] = leaves
        prev_leaves = leaves

    by_id = {c.id: c for c in out}

    def eff(preds: list[str]) -> list[str]:
        res: list[str] = []
        for p in preds:
            res += eff(by_id[p].preds) if by_id[p].optional else [p]
        return list(dict.fromkeys(res))

    for c in out:
        c.eff_preds = eff(c.preds)
    return out


def load_config(path: str | Path) -> StationConfig:
    with open(path, "r", encoding="utf-8") as f:
        return StationConfig.model_validate(yaml.safe_load(f))
