"""设备原判和独立复判的输入契约。设备提交不是平台人工真值。"""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EquipmentResultBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(min_length=1,max_length=96,pattern=r"^[A-Za-z0-9_.-]+$")
    sn: str = Field(min_length=1,max_length=160)
    observed_at: datetime
    result: Literal["OK","NG","UNCERTAIN"]
    product_id: str | None = Field(default=None,min_length=1,max_length=100)
    inspection_id: str | None = Field(default=None,min_length=1,max_length=100)
    face: Literal['front','back','left','right','top','bottom'] | None = None
    defects: list[str] = Field(default_factory=list,max_length=64)
    source_kind: Literal["AOI","AXI","line_scan","xray","ct","other"] = "other"
    idempotency_key: str = Field(min_length=1,max_length=100)

    @field_validator("observed_at")
    @classmethod
    def aware(cls,value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("设备时间必须显式包含时区")
        return value.astimezone(timezone.utc)

    @field_validator("sn","idempotency_key")
    @classmethod
    def clean(cls,value):
        if value!=value.strip() or any(ord(c)<32 for c in value): raise ValueError("字段含空白边界或控制字符")
        return value

    @field_validator("defects")
    @classmethod
    def categories(cls,values):
        if len(set(values))!=len(values) or any(not v.strip() or v!=v.strip() or len(v)>100 or any(ord(c)<32 for c in v) for v in values):
            raise ValueError("缺陷类别须为不重复的1–100字名称")
        return values

    @field_validator('product_id','inspection_id')
    @classmethod
    def group_name(cls,value):
        return cls.clean(value) if value is not None else value

    @model_validator(mode='after')
    def complete_face_identity(self):
        supplied=[self.product_id is not None,self.inspection_id is not None,self.face is not None]
        if any(supplied) and not all(supplied):
            raise ValueError('六面检查必须同时提供产品、检测批次和面别')
        return self


class EquipmentReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["PASS","NG","UNCERTAIN"]
    reason: str = Field(min_length=1,max_length=2000)
    idempotency_key: str = Field(min_length=1,max_length=100)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls,value):
        if not value.strip():raise ValueError("复判原因不能为空白")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def valid_key(cls,value):return EquipmentResultBody.clean(value)


class EquipmentRecheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(min_length=1,max_length=100)
    idempotency_key: str = Field(min_length=1,max_length=100)

    @field_validator("scenario_id","idempotency_key")
    @classmethod
    def valid_name(cls,value):return EquipmentResultBody.clean(value)
