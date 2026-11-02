"""SOP 过程引擎：把原子事件转成“步骤完成 / 漏步 / 错序 / 超时 / 周期结果”。

设计要点
- 时间完全由事件时间戳驱动（可回放、可测试），与墙钟无关。
- 每个工件周期是一个状态机实例；步骤构成 DAG，前驱完成后才“激活”。
- 动作证据（hand_dwell）只统计步骤激活之后的动作；结果证据（obj_state）读取当前稳定状态。
- 看不清时不判错：画质劣化期间出现的违规会把结果降级为 UNCERTAIN，进入人工复核。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .dsl import CompiledStep, Cond, Program
from .events import HAND_ENTER, HAND_EXIT, OBJ_STATE, PERSON_COUNT, PERSON_ENTER, PERSON_EXIT, QUALITY, SIGNAL, Event

NEG_INF = float("-inf")
PENDING, ACTIVE, DONE = "pending", "active", "done"


@dataclass
class StepRt:
    spec: CompiledStep
    status: str = PENDING
    armed_ts: float | None = None
    done_ts: float | None = None
    latched: set[int] = field(default_factory=set)
    seq_i: int = 0
    flagged_order: bool = False
    timeout_flagged: bool = False
    early: bool = False  # 先于前驱完成


@dataclass
class Cycle:
    id: str
    no: int
    start_ts: float
    steps: list[StepRt]
    violations: list[dict[str, Any]] = field(default_factory=list)
    sn: str | None = None
    baseline: set[str] = field(default_factory=set)
    quality_bad_s: float = 0.0
    quality_since: float | None = None


@dataclass
class World:
    hands: dict[str, float] = field(default_factory=dict)
    zone_state: dict[str, tuple[str, float]] = field(default_factory=dict)
    persons: dict[str, float] = field(default_factory=dict)  # zone -> 最近一次 person_enter
    person_absent_since: dict[str, float] = field(default_factory=dict)  # zone -> 确认无人起始
    person_count: dict[str, int] = field(default_factory=dict)
    quality_bad: bool = False
    quality_reason: str = ""


Subscriber = Callable[[str, dict[str, Any]], None]


class Engine:
    def __init__(self, program: Program, epoch_offset: float = 0.0):
        self.p = program
        self.epoch_offset = epoch_offset
        self.world = World()
        self.cycle: Cycle | None = None
        self.now = 0.0
        self.counter = 0
        self._need_rearm = False
        self._subs: list[Subscriber] = []
        self._by_id = {s.id: s for s in program.steps}

    def subscribe(self, fn: Subscriber) -> None:
        self._subs.append(fn)

    def _emit(self, event: str, /, **payload: Any) -> None:
        payload.setdefault("ts", self.now)
        for fn in self._subs:
            fn(event, payload)

    def _wall(self, ts: float) -> str:
        return datetime.fromtimestamp(self.epoch_offset + ts, tz=timezone.utc).astimezone().isoformat(timespec="milliseconds")

    # ---------------------------------------------------------------- 输入
    def process(self, ev: Event) -> None:
        w = self.world
        ts = max(ev.ts, self.now)
        if ev.type == HAND_ENTER and ev.zone:
            w.hands[ev.zone] = ev.ts
        elif ev.type == HAND_EXIT and ev.zone:
            w.hands.pop(ev.zone, None)
        elif ev.type == PERSON_ENTER and ev.zone:
            w.persons[ev.zone] = ev.ts
            w.person_absent_since.pop(ev.zone, None)
        elif ev.type == PERSON_EXIT and ev.zone:
            w.persons.pop(ev.zone, None)
            w.person_absent_since[ev.zone] = ev.ts
        elif ev.type == PERSON_COUNT and ev.zone:
            n = int(ev.value or 0)
            w.person_count[ev.zone] = n
            if n > 0:  # 检出人但边沿事件被去抖吞掉的兜底
                w.persons.setdefault(ev.zone, ev.ts)
                w.person_absent_since.pop(ev.zone, None)
            elif ev.zone not in w.persons:  # 首次确认无人:无人时钟从这一刻起算
                w.person_absent_since.setdefault(ev.zone, ev.ts)
        elif ev.type == OBJ_STATE and ev.zone and ev.state:
            cur = w.zone_state.get(ev.zone)
            if cur is None or cur[0] != ev.state:
                w.zone_state[ev.zone] = (ev.state, ev.ts)
                self._emit("zone_state", zone=ev.zone, state=ev.state, ts=ts)
        elif ev.type == SIGNAL and ev.name == "sn" and self.cycle:
            self.cycle.sn = str(ev.value)
        elif ev.type == QUALITY:
            self._on_quality(ev, ts)
        self._advance(ts, ev)

    def tick(self, ts: float) -> None:
        self._advance(max(ts, self.now), None)

    def _on_quality(self, ev: Event, ts: float) -> None:
        w, c = self.world, self.cycle
        bad = ev.state == "degraded"
        if bad and not w.quality_bad:
            if c:
                c.quality_since = ts
        elif not bad and w.quality_bad and c and c.quality_since is not None:
            c.quality_bad_s += ts - max(c.quality_since, c.start_ts)
            c.quality_since = None
        w.quality_bad = bad
        w.quality_reason = str(ev.value or "") if bad else ""
        self._emit("quality", state=ev.state, reason=w.quality_reason, ts=ts)

    # ---------------------------------------------------------------- 条件
    def _cond_true(self, cond: Cond, ts: float, ev: Event | None = None, floor: float = NEG_INF) -> bool:
        w = self.world
        if cond.hand_dwell:
            enter = w.hands.get(cond.hand_dwell.zone)
            return enter is not None and ts - max(enter, floor) >= cond.hand_dwell.min_ms / 1000
        if cond.person_present:
            p = cond.person_present
            enter = w.persons.get(p.zone)
            return enter is not None and ts - max(enter, floor) >= p.min_ms / 1000
        if cond.person_absent:
            p = cond.person_absent
            since = w.person_absent_since.get(p.zone)
            return since is not None and ts - max(since, floor) >= p.min_ms / 1000
        if cond.person_count:
            p = cond.person_count
            n = w.person_count.get(p.zone, 0)
            return n >= p.count if p.op == "ge" else n <= p.count
        if cond.obj_state:
            zs = w.zone_state.get(cond.obj_state.zone)
            return zs is not None and zs[0] == cond.obj_state.state and ts - zs[1] >= cond.obj_state.min_ms / 1000
        s = cond.signal
        return bool(ev and ev.type == SIGNAL and ev.name == s.name and (s.value is None or ev.value == s.value))

    def _shadow_satisfied(self, st: StepRt, ts: float) -> bool:
        conds = st.spec.evidence.conds
        if not conds or not all(c.kind == "obj_state" for c in conds):
            return False
        res = [self._cond_true(c, ts, None) for c in conds]
        return any(res) if st.spec.evidence.mode == "any" else all(res)

    def _step_satisfied(self, st: StepRt, ts: float, ev: Event | None) -> bool:
        evd = st.spec.evidence
        floor = st.armed_ts if st.armed_ts is not None else NEG_INF
        conds = evd.conds
        if evd.mode == "seq":
            while st.seq_i < len(conds) and self._cond_true(conds[st.seq_i], ts, ev, floor):
                st.seq_i += 1
            return st.seq_i == len(conds)
        for i, c in enumerate(conds):
            if i not in st.latched and self._cond_true(c, ts, ev, floor):
                st.latched.add(i)
        return bool(st.latched) if evd.mode == "any" else len(st.latched) == len(conds)

    # ---------------------------------------------------------------- 状态机
    def _advance(self, ts: float, ev: Event | None) -> None:
        self.now = ts
        if self.cycle is None:
            start_ok = self._cond_true(self.p.cycle.start, ts, ev)
            if not start_ok:
                self._need_rearm = False
            elif not self._need_rearm:
                self._start_cycle(ts)
            if self.cycle is None:
                return
        c = self.cycle
        progressed = True
        while progressed:
            progressed = False
            for st in c.steps:
                if st.status == ACTIVE and self._step_satisfied(st, ts, ev):
                    self._complete(c, st, ts)
                    progressed = True
        for st in c.steps:
            if st.status == PENDING and not st.flagged_order:
                self._check_wrong_order(c, st, ts)
            elif st.status == ACTIVE:
                self._check_timeout(c, st, ts)
        if self._cond_true(self.p.cycle.end, ts, ev, c.start_ts) and ts > c.start_ts:
            self._finish(c, ts, "end")
        elif ts - c.start_ts > self.p.cycle.max_cycle_s:
            self._finish(c, ts, "max_cycle")

    def _start_cycle(self, ts: float) -> None:
        self.counter += 1
        cid = f"{self.p.station}-{datetime.now():%Y%m%d-%H%M%S}-{self.counter:04d}"
        rts = [StepRt(s) for s in self.p.steps]
        c = Cycle(cid, self.counter, ts, rts)
        if self.world.quality_bad:
            c.quality_since = ts
        for st in rts:
            if self._shadow_satisfied(st, ts):
                c.baseline.add(st.spec.id)
        self.cycle = c
        self._emit("cycle_start", cycle_id=cid, start_wall=self._wall(ts))
        for st in rts:
            if not st.spec.eff_preds:
                self._arm(st, ts)

    def _arm(self, st: StepRt, ts: float) -> None:
        st.status, st.armed_ts = ACTIVE, ts
        self._emit("step_active", step=st.spec.id, name=st.spec.name)

    def _complete(self, c: Cycle, st: StepRt, ts: float) -> None:
        st.status, st.done_ts = DONE, ts
        self._emit("step_done", cycle_id=c.id, step=st.spec.id, name=st.spec.name,
                   duration_s=round(ts - (st.armed_ts or ts), 3), early=st.early)
        done = {s.spec.id for s in c.steps if s.status == DONE}
        for nxt in c.steps:
            if nxt.status == PENDING and all(p in done for p in nxt.spec.eff_preds):
                self._arm(nxt, ts)

    def _violation(self, c: Cycle, kind: str, step: StepRt | None, ts: float, msg: str) -> None:
        v = {"kind": kind, "severity": self.p.severity.get(kind, "error"), "step": step.spec.id if step else None,
             "ts": round(ts - c.start_ts, 3), "message": msg,
             "quality_degraded": self.world.quality_bad}
        c.violations.append(v)
        self._emit("violation", cycle_id=c.id, **v)

    def _check_wrong_order(self, c: Cycle, st: StepRt, ts: float) -> None:
        if st.spec.id in c.baseline or ts - c.start_ts < self.p.cycle.grace_ms / 1000:
            return
        if self._shadow_satisfied(st, ts):
            st.flagged_order = st.early = True
            missing = [p for p in st.spec.eff_preds if self._rt(c, p).status != DONE]
            self._violation(c, "wrong_order", st, ts,
                            f"步骤 {st.spec.name} 先于 {', '.join(self._rt(c, p).spec.name for p in missing)} 完成")

    def _check_timeout(self, c: Cycle, st: StepRt, ts: float) -> None:
        t = st.spec.timeout_s
        if t and not st.timeout_flagged and st.armed_ts is not None and ts - st.armed_ts > t:
            st.timeout_flagged = True
            self._violation(c, "step_timeout", st, ts, f"步骤 {st.spec.name} 超时（>{t:g}s）")

    @staticmethod
    def _rt(c: Cycle, sid: str) -> StepRt:
        return next(s for s in c.steps if s.spec.id == sid)

    def _finish(self, c: Cycle, ts: float, reason: str) -> None:
        cy = self.p.cycle
        for st in c.steps:
            if st.status != DONE and not st.spec.optional:
                self._violation(c, "missing_step", st, ts, f"漏步：{st.spec.name}")
        dur = ts - c.start_ts
        if cy.takt_s and dur > cy.takt_s * cy.overtime_ratio:
            self._violation(c, "cycle_overtime", None, ts, f"周期 {dur:.1f}s 超过节拍 {cy.takt_s:g}s")
        if self.world.quality_bad and c.quality_since is not None:
            c.quality_bad_s += ts - max(c.quality_since, c.start_ts)
        errors = [v for v in c.violations if v["severity"] == "error"]
        result = "PASS"
        if errors:
            result = "UNCERTAIN" if c.quality_bad_s >= cy.uncertain_quality_s else "FAIL"
        if reason == "max_cycle":
            result = "UNCERTAIN"
        steps = [{"id": s.spec.id, "name": s.spec.name, "status": s.status, "optional": s.spec.optional,
                  "early": s.early,
                  "armed_s": None if s.armed_ts is None else round(s.armed_ts - c.start_ts, 3),
                  "done_s": None if s.done_ts is None else round(s.done_ts - c.start_ts, 3),
                  "duration_s": None if s.done_ts is None else round(s.done_ts - (s.armed_ts or s.done_ts), 3)}
                 for s in c.steps]
        out = {"cycle_id": c.id, "station": self.p.station, "result": result, "end_reason": reason, "sn": c.sn,
               "start_wall": self._wall(c.start_ts), "end_wall": self._wall(ts), "duration_s": round(dur, 3),
               "takt_s": cy.takt_s, "quality_degraded_s": round(c.quality_bad_s, 3),
               "steps": steps, "violations": c.violations, "rules_hash": self.p.rules_hash}
        self.cycle = None
        self._need_rearm = True
        self._emit("cycle_end", **out)

    # ---------------------------------------------------------------- 快照（UI）
    def snapshot(self) -> dict[str, Any]:
        c = self.cycle
        return {
            "station": self.p.station,
            "rules_hash": self.p.rules_hash,
            "now": round(self.now, 3),
            "quality": {"degraded": self.world.quality_bad, "reason": self.world.quality_reason},
            "zones": {z: s for z, (s, _) in self.world.zone_state.items()},
            "hands": sorted(self.world.hands),
            "cycle": None if c is None else {
                "id": c.id, "no": c.no, "elapsed_s": round(self.now - c.start_ts, 2), "sn": c.sn,
                "takt_s": self.p.cycle.takt_s,
                "violations": c.violations,
                "steps": [{"id": s.spec.id, "name": s.spec.name, "status": s.status, "optional": s.spec.optional,
                           "early": s.early,
                           "armed_s": None if s.armed_ts is None else round(s.armed_ts - c.start_ts, 2),
                           "done_s": None if s.done_ts is None else round(s.done_ts - c.start_ts, 2),
                           "duration_s": None if s.done_ts is None else round(s.done_ts - (s.armed_ts or s.done_ts), 2)}
                          for s in c.steps]},
        }
