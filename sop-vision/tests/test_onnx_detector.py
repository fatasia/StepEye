"""ONNX 检测适配测试:合成模型全链路 + postprocess 纯函数(v8/v5 布局、NMS、坐标映射),零权重依赖。"""
from __future__ import annotations

import numpy as np
import pytest

onnx = pytest.importorskip("onnx")

from visual.dsl import DetectorCfg, Zone
from visual.perception.base import Context, build_geoms
from visual.onnx_detector import OnnxObjectDetector, create_detector, letterbox, postprocess

ZONE = Zone(id="area", polygon=[[0, 0], [1, 0], [1, 1], [0, 1]])


def _pred_v8(boxes_cxcywh_cls_conf, nc=80, n_pad=0) -> np.ndarray:
    """构造 (4+nc, N) 预测:给定的 (cx,cy,w,h,cls,conf) 放在前若干列,其余置低分。"""
    layout = 4 + nc
    arr = np.zeros((layout, len(boxes_cxcywh_cls_conf) + n_pad), np.float32)
    for i, (cx, cy, w, h, cls, conf) in enumerate(boxes_cxcywh_cls_conf):
        arr[:4, i] = (cx, cy, w, h)
        arr[4 + cls, i] = conf
    return arr


def test_postprocess_v8_layout_maps_to_original_coords():
    # 网络输入 640:一个 person(conf .9),letterbox r=0.5, pad=(0,80) → 原图坐标 ×2, y-80
    pred = _pred_v8([(320.0, 400.0, 100.0, 200.0, 0, 0.9)], n_pad=100)
    out = postprocess(pred, 0.5, ["person"] * 80, (2000, 2000), 0.5, (0, 80))
    assert len(out) == 1 and out[0][0] == "person"
    x0, y0, x1, y1 = out[0][1]
    assert abs((x1 - x0) - 200.0) < 1.0 and abs((y1 - y0) - 400.0) < 1.0  # ÷r=×2
    assert abs(y0 - 440.0) < 1.0 and abs(x0 - 540.0) < 1.0  # 左上角:网络坐标减 pad 后 ÷r


def test_postprocess_v5_layout_multiplies_objectness():
    # v5:行4=obj,行5+=cls 分数——obj×cls 才是最终置信
    pred = np.zeros((5 + 2, 1), np.float32)
    pred[:4, 0] = (320, 320, 50, 50)
    pred[4, 0] = 0.8   # obj
    pred[5, 0] = 0.5   # person 分数 → 最终 0.4
    out = postprocess(pred, 0.3, ["person", "dog"], (100, 100), 1.0, (0, 0))
    assert len(out) == 1 and out[0][0] == "person"  # obj=0.8×0.5=0.4 ≥0.3


def test_postprocess_nms_removes_overlap_and_low_conf():
    pred = _pred_v8([(320, 320, 60, 60, 0, 0.9), (322, 322, 60, 60, 0, 0.85),  # 高度重叠:留最高分
                     (100, 100, 30, 30, 0, 0.4)])                              # 低分:被阈值滤掉
    out = postprocess(pred, 0.5, ["person"] * 80, (640, 640), 1.0, (0, 0))
    assert len(out) == 1 and abs(out[0][1][0] - 290) < 1.0


def test_letterbox_restores_geometry():
    frame = np.zeros((480, 640, 3), np.uint8)
    blob, r, pad = letterbox(frame, 640)
    assert blob.shape == (640, 640, 3) and abs(r - 1.0) < 1e-6 and pad == (0, 80)
    tall = np.zeros((1280, 640, 3), np.uint8)
    _, r2, pad2 = letterbox(tall, 640)
    assert abs(r2 - 0.5) < 1e-6 and pad2 == (160, 0)  # 竖图左右垫


def _synthetic_model(tmp_path: Path, boxes) -> Path:
    """恒定输出的迷你 ONNX:输入占位,输出 (1, 4+80, N) = 预置检测,验证全链路(无需真权重)。"""
    pred = _pred_v8(boxes, n_pad=50)
    tensor = onnx.helper.make_tensor("pred", onnx.TensorProto.FLOAT, pred.shape, pred.flatten().tolist())
    node = onnx.helper.make_node("Constant", [], ["out"], value=tensor)
    inp = onnx.helper.make_tensor_value_info("images", onnx.TensorProto.FLOAT, [1, 3, 640, 640])
    outp = onnx.helper.make_tensor_value_info("out", onnx.TensorProto.FLOAT, list(pred.shape))
    graph = onnx.helper.make_graph([node], "g", [inp], [outp])
    model = onnx.helper.make_model(graph, opset_imports=[onnx.helper.make_opsetid("", 13)])
    model.ir_version = 9
    path = tmp_path / "fake_yolo.onnx"
    path.write_bytes(model.SerializeToString())
    return path


def test_onnx_detector_end_to_end(tmp_path):
    model = _synthetic_model(tmp_path, [(320.0, 400.0, 100.0, 200.0, 0, 0.9)])
    cfg = DetectorCfg(enabled=True, model=str(model), classes=["person"],
                      class_names=["person"] + [f"c{i}" for i in range(79)])
    det = create_detector(cfg, build_geoms([ZONE], 1000, 1000), base_dir=tmp_path)
    assert isinstance(det, OnnxObjectDetector)  # 按扩展名选引擎
    ctx = Context(build_geoms([ZONE], 1000, 1000))
    frame = np.zeros((1000, 640, 3), np.uint8)
    evs1 = det.process(frame, 0.033, ctx)
    assert evs1 == []  # 去抖:单帧不稳
    assert len(ctx.object_boxes) == 1 and ctx.object_boxes[0][0] == "person"
    evs2 = det.process(frame, 0.066, ctx)  # 第二帧稳定 → 进入+计数
    assert any(e.type == "object_enter" and e.name == "person" for e in evs2)
    assert any(e.type == "object_count" and e.value == 1 for e in evs2)
    det.close()


def test_missing_model_fails_fast(tmp_path):
    cfg = DetectorCfg(enabled=True, model="models/nope.onnx", classes=["person"])
    with pytest.raises(FileNotFoundError):
        create_detector(cfg, build_geoms([ZONE], 100, 100), base_dir=tmp_path)
