"""External detection-result evaluation; does not turn submitted metrics into approval."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .detection_eval import evaluate_boxes


class EvaluationBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    classes: list[str] = Field(min_length=1, max_length=100)
    frames: list[dict] = Field(min_length=1, max_length=500)
    iou_threshold: float = Field(default=.5, gt=0, le=1, allow_inf_nan=False)
    confidence_threshold: float = Field(default=.25, gt=0, le=1, allow_inf_nan=False)


def router():
    api = APIRouter()

    @api.post('/api/evaluations/detection')
    def evaluate(body: EvaluationBody):
        try:
            return evaluate_boxes(body.classes, body.frames, iou_threshold=body.iou_threshold,
                                  confidence_threshold=body.confidence_threshold)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None

    return api
