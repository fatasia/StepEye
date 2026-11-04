"""胶路两类 YOLOv4-Tiny：直接缩放，分数已含 objectness，不使用 letterbox。"""
from __future__ import annotations

import math

import numpy as np

from .detector_registry import Detection

CLASSES = ['discontinuity', 'excess']


def preprocess(frame):
    import cv2
    if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
            or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size):
        raise ValueError('胶路输入须为非空uint8 BGR图像')
    return cv2.dnn.blobFromImage(frame, 1 / 255, (416, 416), swapRB=True, crop=False)


def decode(prediction, width, height, threshold=.25, max_results=500):
    from .onnx_detector import postprocess_detections
    if prediction.shape != (1, 2535, 7) or not np.isfinite(prediction).all():
        raise ValueError('胶路输出须为有限的[1,2535,7]')
    rows = prediction[0]
    if np.any(rows[:, 4:] < 0) or np.any(rows[:, 4:] > 1):
        raise ValueError('胶路概率超出范围')
    # Drop objectness because exported class scores already contain it.
    # Reuse the existing per-class NMS and clipping without scaling a second time.
    compact = np.concatenate([rows[:, :4] * [width, height, width, height], rows[:, 5:]], axis=1)
    hits = postprocess_detections(compact.T[None], threshold, CLASSES, (height, width), 1., (0, 0))
    return [Detection(name, tuple(map(float, box)), score) for name, box, score in hits[:max_results]]


class GlueYoloV4Backend:
    adapter_name = 'glue_yolov4'
    input_size = 416

    def __init__(self, model_path, classes, threshold=.25, max_results=500):
        import onnxruntime as ort
        if (list(classes) != CLASSES or isinstance(threshold, bool) or not math.isfinite(threshold)
                or not 0 < threshold <= 1 or type(max_results) is not int or not 1 <= max_results <= 1000):
            raise ValueError('胶路类别或阈值无效')
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(str(model_path), sess_options=options, providers=['CPUExecutionProvider'])
        inputs, outputs = session.get_inputs(), session.get_outputs()
        if (len(inputs) != 1 or inputs[0].name != 'images' or inputs[0].type != 'tensor(float)'
                or inputs[0].shape != [1, 3, 416, 416] or len(outputs) != 1
                or outputs[0].name != 'predictions' or outputs[0].type != 'tensor(float)'
                or outputs[0].shape != [1, 2535, 7]
                or session.get_modelmeta().custom_metadata_map.get('adapter') != self.adapter_name):
            raise ValueError('胶路模型输入输出契约不匹配')
        self.session, self.classes = session, list(CLASSES)
        self.threshold, self.max_results = threshold, max_results

    def detect(self, frame):
        if self.session is None:
            raise RuntimeError('胶路检测器已关闭')
        result = self.session.run(['predictions'], {'images': preprocess(frame)})[0]
        return decode(result, frame.shape[1], frame.shape[0], self.threshold, self.max_results)

    def close(self):
        self.session = None
