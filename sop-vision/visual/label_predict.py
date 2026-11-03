"""单图 CPU 预标注子进程。stdin 配置，stdout 仅一个 JSON；无去抖、无训练依赖。"""
from __future__ import annotations

import json
import sys


def predict(spec: dict) -> list[dict]:
    import cv2
    import numpy as np
    import onnxruntime as ort
    from .onnx_detector import letterbox, postprocess_detections, native_input_size

    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(spec["model_path"], sess_options=options, providers=["CPUExecutionProvider"])
    inputs = session.get_inputs()
    input_size = native_input_size(inputs)
    if len(session.get_outputs()) != 1:
        raise ValueError("不支持多输出模型，请安装对应适配器")
    frame = cv2.imdecode(np.fromfile(spec["image_path"], dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("标注资源无法解码")
    h, w = frame.shape[:2]
    blob, r, pad = letterbox(frame, input_size)
    x = blob[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255
    outputs = session.run(None, {inputs[0].name: x})
    if len(outputs) != 1:
        raise ValueError("不支持多输出模型，请安装对应适配器")
    hits = postprocess_detections(outputs[0], spec["threshold"], spec["classes"], (h, w), r, pad)
    labels = spec["project_classes"]
    return [{"label_id": labels.index(name), "xyxy_norm": [float(box[0] / w), float(box[1] / h), float(box[2] / w), float(box[3] / h)],
             "confidence": score} for name, box, score in hits if name in labels][:500]


if __name__ == "__main__":
    try:
        result = predict(json.loads(sys.stdin.buffer.read(64 * 1024)))
        print(json.dumps({"targets": result}, allow_nan=False))
    except Exception as e:
        # 父进程只显示类别，不透传文件路径/底层运行库诊断。
        print(json.dumps({"error": type(e).__name__}))
        raise SystemExit(1)
