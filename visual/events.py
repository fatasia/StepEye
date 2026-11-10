from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# 感知层只产生原子事件，业务判定（SOP/安全/质量）统一由规则引擎完成。
HAND_ENTER = "hand_enter"
HAND_EXIT = "hand_exit"
PERSON_ENTER = "person_enter"
PERSON_EXIT = "person_exit"
PERSON_COUNT = "person_count"
PERSON_FALL = "person_fall"  # value: 1=倒地姿态持续 0=姿态恢复
OBJECT_ENTER = "object_enter"
OBJECT_EXIT = "object_exit"
OBJECT_COUNT = "object_count"  # name=类别, value=区域内数量
OBJ_STATE = "obj_state"
SIGNAL = "signal"
QUALITY = "quality"


@dataclass(slots=True)
class Event:
    ts: float
    type: str
    zone: str | None = None
    state: str | None = None
    name: str | None = None
    value: Any = None
    conf: float = 1.0
    source: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, {}, "")}
