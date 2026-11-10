"""单图 CPU 预标注子进程。stdin 配置，stdout 仅一个 JSON；无去抖、无训练依赖。"""
from __future__ import annotations

import json
import sys


def predict(spec: dict) -> list[dict]:
    import cv2
    import numpy as np
    from .detector_registry import create_backend

    frame = cv2.imdecode(np.fromfile(spec["image_path"], dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("标注资源无法解码")
    h, w = frame.shape[:2]
    backend = create_backend(spec["model_path"], spec["classes"], spec["threshold"],
                             adapter=spec.get("adapter","yolo_raw"))
    try:
        hits = backend.detect(frame)
    finally:
        backend.close()
    labels = spec["project_classes"]
    targets = []
    for item in hits:
        if item.label not in labels:
            continue
        x0, y0, x1, y1 = item.xyxy
        targets.append({"label_id": labels.index(item.label),
                        "xyxy_norm": [x0 / w, y0 / h, x1 / w, y1 / h],
                        "confidence": item.confidence})
    return targets



if __name__ == "__main__":
    try:
        result = predict(json.loads(sys.stdin.buffer.read(64 * 1024)))
        print(json.dumps({"targets": result}, allow_nan=False))
    except Exception as e:
        # 父进程只显示类别，不透传文件路径/底层运行库诊断。
        print(json.dumps({"error": type(e).__name__}))
        raise SystemExit(1)
