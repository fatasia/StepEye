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


def native_input_size(inputs) -> int:
    """Use the registered graph's fixed square input; no runtime resolution search."""
    if len(inputs) != 1 or inputs[0].type != "tensor(float)":
        raise ValueError("仅支持单输入 float32 YOLO ONNX")
    shape = inputs[0].shape
    if (len(shape) != 4 or any(type(v) is not int for v in shape)
            or shape[:2] != [1, 3] or shape[2] != shape[3]
            or not 128 <= shape[2] <= 1280 or shape[2] % 32):
        raise ValueError("仅支持静态单图 NCHW 方形 YOLO 输入，边长128–1280且为32倍数")
    return shape[2]


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


def postprocess_detections(pred: np.ndarray, conf_th: float, class_names: list[str],
                           frame_shape: tuple[int, int], r: float, pad: tuple[int, int]) -> list[tuple[str, tuple, float]]:
    """YOLO 原始输出 → 类别、原图 xyxy、置信度。按类 NMS，不支持端到端 NMS/DETR 输出。"""
    if pred.ndim == 3 and pred.shape[0] == 1:
        pred = pred[0]
    if pred.ndim != 2 or not class_names:
        raise ValueError("需要单图 YOLO 原始输出和完整类别表")
    layouts = (4 + len(class_names), 5 + len(class_names))
    if pred.shape[0] not in layouts and pred.shape[1] in layouts:
        pred = pred.T
    if pred.shape[0] not in layouts:
        raise ValueError("ONNX 输出与类别表不匹配，仅支持 YOLO v5/v8 原始布局")
    pred = pred[:, np.isfinite(pred).all(axis=0)]
    if pred.shape[1] == 0:
        return []
    nc = pred.shape[0] - 4
    if nc == len(class_names) + 1:  # v5 布局:第 5 行是 objectness,分数=obj×cls(非原地,避免形状广播错)
        pred = np.concatenate([pred[:4], pred[5:] * pred[4:5]], axis=0)
        nc -= 1
    scores = pred[4:4 + nc]  # (nc, N)
    ids = scores.argmax(axis=0)
    confs = scores.max(axis=0)
    keep = (confs >= conf_th) & (confs <= 1) & (pred[2] > 0) & (pred[3] > 0)
    if not keep.any():
        return []
    boxes = pred[:4, keep].T  # (M, 4) cx,cy,w,h 网络输入坐标
    confs, ids = confs[keep], ids[keep]
    xy = boxes[:, :2] - boxes[:, 2:] / 2  # 左上
    xyxy = np.concatenate([xy, xy + boxes[:, 2:]], axis=1)
    # OpenCV 接口接收 xywh，不能传 xyxy；不同类别不互相抑制。
    xywh = np.concatenate([xy, boxes[:, 2:]], axis=1)
    idx = []
    for cls in np.unique(ids):
        members = np.flatnonzero(ids == cls)
        selected = cv2.dnn.NMSBoxes(xywh[members].tolist(), confs[members].tolist(), conf_th, NMS_IOU)
        idx.extend(members[np.asarray(selected, dtype=int).reshape(-1)].tolist())
    idx.sort(key=lambda i: -float(confs[i]))
    out = []
    for i in np.array(idx).reshape(-1):
        name = class_names[int(ids[i])] if int(ids[i]) < len(class_names) else str(int(ids[i]))
        x0, y0, x1, y1 = xyxy[i]
        ox, oy = pad
        box = ((x0 - ox) / r, (y0 - oy) / r, (x1 - ox) / r, (y1 - oy) / r)
        clipped = (max(0.0, box[0]), max(0.0, box[1]),
                   min(float(frame_shape[1]), box[2]), min(float(frame_shape[0]), box[3]))
        if clipped[0] < clipped[2] and clipped[1] < clipped[3]:
            out.append((name, clipped, float(confs[i])))
    return out


def postprocess(pred: np.ndarray, conf_th: float, class_names: list[str],
                frame_shape: tuple[int, int], r: float, pad: tuple[int, int]) -> list[tuple[str, tuple]]:
    """兼容实时跟踪器的无置信度接口。"""
    return [(name, box) for name, box, _ in postprocess_detections(pred, conf_th, class_names, frame_shape, r, pad)]


class OnnxObjectDetector:
    def __init__(self, cfg: DetectorCfg, geoms: dict[str, ZoneGeom], base_dir: Path | None = None):
        from .detector_registry import YoloOnnxBackend

        model = Path(cfg.model)
        if not model.is_absolute() and base_dir is not None:
            model = base_dir / model
        if not model.exists():
            raise FileNotFoundError(f"ONNX 检测模型不存在: {model}")
        self.class_names = list(cfg.class_names) or list(COCO80)
        self.backend = YoloOnnxBackend(model, self.class_names, cfg.min_conf, cfg.max_results)
        self.input_size = self.backend.input_size
        from .tracking import ClassAwareIoUTracker
        self.mot = ClassAwareIoUTracker(max_tracks=cfg.max_results)
        self._last_frame_ts = None
        self.cfg = cfg
        self.tracker = ZoneCountTracker(geoms, cfg.enter_frames, cfg.exit_frames)

    def process(self, frame: np.ndarray, ts: float, ctx: Context) -> list[Event]:
        if not np.isfinite(ts):
            raise ValueError("检测时间必须有限")
        if self._last_frame_ts is not None:
            if ts == self._last_frame_ts:
                return []
            if ts < self._last_frame_ts:
                self.reset_observations()
        detections = [item for item in self.detect(frame) if item.label in self.cfg.classes]
        tracks = self.mot.update(detections, ts)
        self._last_frame_ts = ts
        ctx.object_tracks.extend(tracks)
        hits = [(item.label, item.xyxy) for item in detections]
        for name, box in hits:
            ctx.object_boxes.append((name, box))
        return self.tracker.update(hits, ts)

    def detect(self, frame):
        """B0 中立检测接口，保留置信度，不更新区域事件。"""
        return self.backend.detect(frame)

    def reset_observations(self):
        """断流/光照门禁恢复时隔离旧轨迹与区域计数。"""
        self.mot.reset()
        self.tracker.reset()
        self._last_frame_ts = None

    def close(self) -> None:
        self.reset_observations()
        self.backend.close()


def create_detector(cfg: DetectorCfg, geoms: dict[str, ZoneGeom], base_dir: Path | None = None):
    """按模型扩展名选引擎:.onnx → onnxruntime;.tflite → MediaPipe。"""
    suffix = Path(cfg.model).suffix.lower()
    if suffix == ".onnx":
        return OnnxObjectDetector(cfg, geoms, base_dir)
    if suffix not in {".tflite", ".task"}:
        raise ValueError("不支持的检测模型格式")
    from .perception.objects import MediaPipeObjectDetector
    return MediaPipeObjectDetector(cfg, geoms, base_dir)
