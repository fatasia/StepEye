"""检测器中立契约：原图像素 xyxy、类别名称、[0,1] 置信度，不携带真值。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import math


@dataclass(frozen=True)
class Detection:
    label: str
    xyxy: tuple[float, float, float, float]
    confidence: float

    def __post_init__(self):
        if (not self.label or len(self.xyxy) != 4
                or not all(math.isfinite(v) for v in (*self.xyxy, self.confidence))
                or not 0 <= self.confidence <= 1
                or self.xyxy[0] < 0 or self.xyxy[1] < 0
                or self.xyxy[0] >= self.xyxy[2] or self.xyxy[1] >= self.xyxy[3]):
            raise ValueError("无效检测结果")


class DetectorBackend(Protocol):
    def detect(self, frame) -> list[Detection]: ...
    def close(self) -> None: ...


def create_backend(model_path, classes, threshold=.5, max_results=500, *, adapter="yolo_raw") -> DetectorBackend:
    """只接受登记的明确布局，不凭六列形状把任意ONNX猜成YOLO。"""
    if adapter=="yolo_raw":
        return YoloOnnxBackend(model_path,classes,threshold,max_results)
    if adapter=="yolo_e2e":
        from .yolo_e2e import YoloE2EOnnxBackend
        return YoloE2EOnnxBackend(model_path,classes,threshold,max_results)
    if adapter=="kairos_rtdetr":
        from .kairos_rtdetr import KairosRTDetrBackend
        return KairosRTDetrBackend(model_path,classes,threshold,max_results)
    raise ValueError("未登记的检测器输出适配器")


class YoloOnnxBackend:
    """现有 v5/v8 原始布局适配；不是任意 ONNX/YOLOX/DETR 适配器。"""
    adapter_name = "yolo_raw"
    def __init__(self, model_path, classes, threshold=.5, max_results=500):
        import onnxruntime as ort
        from .onnx_detector import native_input_size
        if (not classes or not 0 <= threshold <= 1 or type(max_results) is not int
                or not 1 <= max_results <= 1000):
            raise ValueError("无效检测器配置")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), sess_options=options,
                                            providers=["CPUExecutionProvider"])
        if (self.adapter_name=="yolo_raw" and
                self.session.get_modelmeta().custom_metadata_map.get("end2end","").lower()=="true"):
            self.close()
            raise ValueError("模型声明端到端输出，请显式登记yolo_e2e适配器")
        self.input_size = native_input_size(self.session.get_inputs())
        if len(self.session.get_outputs()) != 1:
            raise ValueError("不支持多输出模型，请安装对应适配器")
        self.input_name = self.session.get_inputs()[0].name
        self.classes = list(classes)
        self.threshold, self.max_results = threshold, max_results

    def detect(self, frame):
        import numpy as np
        from .onnx_detector import letterbox, postprocess_detections
        if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
                or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size):
            raise ValueError("检测输入须为非空 uint8 BGR 图像")
        if self.session is None:
            raise RuntimeError("检测器已关闭")
        blob, r, pad = letterbox(frame, self.input_size)
        tensor = blob[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255
        pred = self.session.run(None, {self.input_name: tensor})[0]
        hits = postprocess_detections(pred, self.threshold, self.classes, frame.shape[:2], r, pad)
        return [Detection(name, tuple(float(v) for v in box), score)
                for name, box, score in hits[:self.max_results]]

    def close(self):
        self.session = None
