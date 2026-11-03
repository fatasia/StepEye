"""YOLO 系 ONNX 检测适配(onnxruntime,零 torch 运行时依赖)。

用途:读取下述 YOLO 风格输出的 ONNX；D-FINE/RF-DETR 的多输入/输出尚需专用适配。
使用 onnxruntime 不改变权重本身的许可，详见 TRAINING-READINESS。与 MediaPipe .tflite
同一 DetectorCfg 接口,按扩展名自动选引擎(见 create_detector)。
支持布局:Ultralytics 导出 (1, 4+nc, N)——v8/v11 无 objectness;v5 (1, 5+nc, N) 自动识别乘回 obj。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .dsl import DetectorCfg
from .events import OBJECT_COUNT, OBJECT_ENTER, OBJECT_EXIT, Event
from .perception.base import Context, ZoneGeom
from .perception.objects import ZoneCountTracker

INPUT_SIZE = 640
NMS_IOU = 0.45

COCO80 = ("person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
          "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
          "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
          "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
          "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
          "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich",
          "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
          "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
          "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
          "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush")


def letterbox(frame: np.ndarray, size: int = INPUT_SIZE) -> tuple[np.ndarray, float, tuple[int, int]]:
    """保持长宽比缩放并垫灰边;返回(网络输入, 缩放系数, 垫边偏移)。"""
    h, w = frame.shape[:2]
    r = min(size / h, size / w)
    nh, nw = int(h * r), int(w * r)
    resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, np.uint8)
    dx, dy = (size - nw) // 2, (size - nh) // 2
    canvas[dy:dy + nh, dx:dx + nw] = resized
    return canvas, r, (dx, dy)


def postprocess(pred: np.ndarray, conf_th: float, class_names: list[str],
                frame_shape: tuple[int, int], r: float, pad: tuple[int, int]) -> list[tuple[str, tuple[float, float, float, float]]]:
    """(4+nc, N) 预测 → 过滤后的 [(类别名, xyxy), ...],坐标映射回原图。纯函数,离线可测。"""
    nc = pred.shape[0] - 4
    if nc == len(class_names) + 1:  # v5 布局:第 5 行是 objectness,分数=obj×cls(非原地,避免形状广播错)
        pred = np.concatenate([pred[:4], pred[5:] * pred[4:5]], axis=0)
        nc -= 1
    scores = pred[4:4 + nc]  # (nc, N)
    ids = scores.argmax(axis=0)
    confs = scores.max(axis=0)
    keep = confs >= conf_th
    if not keep.any():
        return []
    boxes = pred[:4, keep].T  # (M, 4) cx,cy,w,h 网络输入坐标
    confs, ids = confs[keep], ids[keep]
    xy = boxes[:, :2] - boxes[:, 2:] / 2  # 左上
    xyxy = np.concatenate([xy, xy + boxes[:, 2:]], axis=1)
    idx = cv2.dnn.NMSBoxes(xyxy.tolist(), confs.tolist(), conf_th, NMS_IOU)
    out: list[tuple[str, tuple[float, float, float, float]]] = []
    for i in np.array(idx).reshape(-1):
        name = class_names[int(ids[i])] if int(ids[i]) < len(class_names) else str(int(ids[i]))
        x0, y0, x1, y1 = xyxy[i]
        ox, oy = pad
        box = ((x0 - ox) / r, (y0 - oy) / r, (x1 - ox) / r, (y1 - oy) / r)
        out.append((name, (max(0.0, box[0]), max(0.0, box[1]),
                           min(float(frame_shape[1]), box[2]), min(float(frame_shape[0]), box[3]))))
    return out


class OnnxObjectDetector:
    def __init__(self, cfg: DetectorCfg, geoms: dict[str, ZoneGeom], base_dir: Path | None = None):
        import onnxruntime

        model = Path(cfg.model)
        if not model.is_absolute() and base_dir is not None:
            model = base_dir / model
        if not model.exists():
            raise FileNotFoundError(f"ONNX 检测模型不存在: {model}")
        self.session = onnxruntime.InferenceSession(str(model), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.class_names = list(cfg.class_names) or list(COCO80)
        self.cfg = cfg
        self.tracker = ZoneCountTracker(geoms, cfg.enter_frames, cfg.exit_frames)

    def process(self, frame: np.ndarray, ts: float, ctx: Context) -> list[Event]:
        blob, r, pad = letterbox(frame)
        x = blob[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0  # BGR→RGB, HWC→CHW
        pred = self.session.run(None, {self.input_name: x})[0]
        pred = np.squeeze(pred, axis=0) if pred.ndim == 3 else pred  # (1, 4+nc, N) → (4+nc, N)
        hits = [(name, box) for name, box in postprocess(pred, self.cfg.min_conf, self.class_names,
                                                          frame.shape[:2], r, pad)
                if name in self.cfg.classes]
        for name, box in hits:
            ctx.object_boxes.append((name, box))
        return self.tracker.update(hits, ts)

    def close(self) -> None:
        pass


def create_detector(cfg: DetectorCfg, geoms: dict[str, ZoneGeom], base_dir: Path | None = None):
    """按模型扩展名选引擎:.onnx → onnxruntime;.tflite → MediaPipe。"""
    suffix = Path(cfg.model).suffix.lower()
    if suffix == ".onnx":
        return OnnxObjectDetector(cfg, geoms, base_dir)
    from .objects import MediaPipeObjectDetector
    return MediaPipeObjectDetector(cfg, geoms, base_dir)
