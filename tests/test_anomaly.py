"""AnomalyDINO 式免训练异常检测测试:合成 OK/NG 图判别、阈值、序列化。零权重依赖(网格特征)。"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from visual.anomaly import AnomalyEngine, PatchMemory

np.random.seed(7)


def _ok_img() -> np.ndarray:
    """好品:均匀蓝底 + 固定白色方块(稳定工艺特征)。"""
    img = np.full((112, 112, 3), (180, 120, 60), np.uint8)  # BGR 蓝底
    img[20:60, 30:80] = (240, 240, 240)
    return img


def _ng_img(kind: str = "scratch") -> np.ndarray:
    """坏品:好品底 + 明显异常(划痕/色斑)。"""
    img = _ok_img()
    if kind == "scratch":
        cv2.line(img, (5, 5), (107, 100), (0, 0, 0), 6)  # 黑划痕
    elif kind == "stain":
        cv2.circle(img, (70, 70), 18, (0, 0, 200), -1)  # 红斑
    return img


def test_memory_nearest_neighbor_distance():
    m = PatchMemory()
    bank = np.random.RandomState(0).normal(0, 1, (20, 8)).astype(np.float32)
    m.add(bank)
    near, _ = m.score(bank[:5])  # 自身特征:距离应≈0
    assert near < 0.05
    far, fmax = m.score(np.random.RandomState(1).normal(0, 1, (6, 8)).astype(np.float32))
    assert far > 0.5  # 随机新特征距离大


def test_engine_pass_vs_ng_separated(tmp_path):
    eng = AnomalyEngine(threshold=0.05)
    for seed in range(4):
        img = _ok_img().copy()
        noise = np.random.RandomState(seed).normal(0, 6, img.shape).astype(np.float32)
        eng.add_ok(np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8))
    ok_out = eng.inspect(_ok_img())
    assert ok_out["verdict"] == "PASS" and ok_out["n_refs"] == 64  # 4图×16网格patch
    ng_out = eng.inspect(_ng_img("scratch"))
    assert ng_out["verdict"] == "NG" and ng_out["score"] > ok_out["score"]
    ng2 = eng.inspect(_ng_img("stain"))
    assert ng2["verdict"] == "NG"


def test_engine_empty_memory_is_uncertain(tmp_path):
    eng = AnomalyEngine()
    out = eng.inspect(_ng_img())
    assert out["verdict"] == "UNCERTAIN"  # 没登记 OK 图:不硬判


def test_engine_save_load_roundtrip(tmp_path):
    eng = AnomalyEngine(threshold=0.05)
    eng.add_ok(_ok_img())
    eng.add_ok(_ok_img())
    eng.save(tmp_path)
    eng2 = AnomalyEngine.load(tmp_path)
    assert eng2.n_refs == eng.n_refs and eng2.threshold == eng.threshold
    a, b = eng.inspect(_ng_img("scratch")), eng2.inspect(_ng_img("scratch"))
    assert a["score"] == b["score"] and a["verdict"] == b["verdict"]  # 序列化后判定一致


def test_exposure_correction_is_bounded_and_original_unchanged():
    from visual.anomaly import normalize_exposure
    dark=np.full((112,112,3),35,np.uint8)
    original=dark.copy()
    corrected,info=normalize_exposure(dark)
    assert np.array_equal(dark,original)
    assert corrected.mean()>dark.mean()
    assert .5<=info['gain']<=2.0 and not info['unusable']
    bright=np.full((112,112,3),230,np.uint8)
    corrected,info=normalize_exposure(bright)
    assert corrected.mean()<bright.mean() and info['gain']>=.5
    assert normalize_exposure(np.zeros_like(dark))[1]['unusable']


def test_exposure_mode_roundtrip_and_unusable_image(tmp_path):
    eng=AnomalyEngine(lighting='auto')
    eng.add_ok(_ok_img());eng.save(tmp_path)
    restored=AnomalyEngine.load(tmp_path)
    assert restored.lighting=='auto'
    assert restored.inspect(np.zeros_like(_ok_img()))['verdict']=='UNCERTAIN'
    assert restored.inspect(_ok_img())['verdict']=='PASS'


def test_dinov2_exposure_on_local_video_if_available(monkeypatch):
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    model=root/'models/dinov2-vits14-patches.onnx'
    video=root/'test-videos/FpTmB9IQdVo.mp4'
    if not model.is_file() or not video.is_file():pytest.skip('本地 DINOv2 模型或样例视频未安装')
    monkeypatch.setenv('STEPEYE_DINOV2_ONNX',str(model))
    cap=cv2.VideoCapture(str(video))
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES,40);ok,frame=cap.read()
    finally:cap.release()
    assert ok
    frame=cv2.resize(frame,(224,224));dark=np.clip(frame.astype(np.float32)*.55,0,255).astype(np.uint8)
    baseline=AnomalyEngine(backend='dinov2',lighting='off');baseline.add_ok(frame)
    corrected=AnomalyEngine(backend='dinov2',lighting='auto');corrected.add_ok(frame)
    assert corrected.inspect(dark)['score'] < baseline.inspect(dark)['score'] * .5


def test_dinov2_onnx_pipeline_and_model_binding(tmp_path, monkeypatch):
    """以真实 ONNX Runtime 验证接口与指纹；模型为合成测试图，不是 DINOv2 权重。"""
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from onnx import TensorProto, helper, numpy_helper
    from visual.anomaly import _model_cache
    image = helper.make_tensor_value_info("image", TensorProto.FLOAT, [1, 3, 224, 224])
    output = helper.make_tensor_value_info("patch_tokens", TensorProto.FLOAT, [1, 256, 384])
    weights = numpy_helper.from_array(np.ones((1, 256, 384), np.float32), name="weights")
    nodes = [helper.make_node("ReduceMean", ["image"], ["mean"], keepdims=0),
             helper.make_node("Mul", ["mean", "weights"], ["patch_tokens"])]
    model = helper.make_model(helper.make_graph(nodes, "fixture", [image], [output], [weights]),
                              opset_imports=[helper.make_opsetid("", 17)], ir_version=8)
    path = tmp_path / "test.onnx"
    onnx.save(model, path)
    monkeypatch.setenv("STEPEYE_DINOV2_ONNX", str(path))
    eng = AnomalyEngine(backend="dinov2")
    assert eng.add_ok(_ok_img()) == 256
    assert eng.inspect(_ok_img())["verdict"] == "PASS"
    eng.save(tmp_path / "bank")
    restored = AnomalyEngine.load(tmp_path / "bank")
    assert restored.backend == "dinov2" and restored.inspect(_ok_img())["backend"] == "dinov2"
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="模型文件已变化"):
        restored.inspect(_ok_img())
    _model_cache.clear()


def test_missing_dinov2_and_legacy_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("STEPEYE_DINOV2_ONNX", str(tmp_path / "missing.onnx"))
    with pytest.raises(ValueError, match="模型未配置"):
        AnomalyEngine(backend="dinov2")
    eng = AnomalyEngine()
    eng.add_ok(_ok_img())
    eng.save(tmp_path)
    import json
    meta = json.loads((tmp_path / "engine.json").read_text(encoding="utf-8"))
    meta.pop("backend"); meta.pop("model_sha")
    (tmp_path / "engine.json").write_text(json.dumps(meta), encoding="utf-8")
    assert AnomalyEngine.load(tmp_path).backend == "grid"


def test_anomaly_api_register_and_inspect(tmp_path):
    """API 全流程:登记 OK 图 → 判定 → 影子库落样 + 告警入库。"""
    from fastapi.testclient import TestClient

    from visual.server import build_app
    from visual.shadow import ShadowStore
    data = tmp_path / "d"
    store = ShadowStore(data / "shadow.db")
    store.close()
    ok, buf_ok = cv2.imencode(".jpg", _ok_img())
    _, buf_ng = cv2.imencode(".jpg", _ng_img())
    with TestClient(build_app([], data)) as c:
        r = c.post("/api/anomaly/scratch/register",
                   files=[("files", ("o1.jpg", buf_ok.tobytes(), "image/jpeg")),
                          ("files", ("o2.jpg", buf_ok.tobytes(), "image/jpeg"))],
                   data={"threshold": "0.05"})
        assert r.status_code == 201 and r.json()["total_refs"] > 0
        conflict = c.post("/api/anomaly/scratch/register", files=[("files", ("o.jpg", buf_ok.tobytes(), "image/jpeg"))], data={"backend": "dinov2"})
        assert conflict.status_code == 409
        r2 = c.post("/api/anomaly/scratch/inspect",
                    files={"image": ("t.jpg", buf_ng.tobytes(), "image/jpeg")})
        assert r2.status_code == 201, r2.text
        out = r2.json()
        assert out["verdict"] in ("NG", "PASS")
        assert len(c.get("/api/alerts").json()) == 1  # 判定结果已入告警中心
        # 未登记场景 → 409 明示
        bad = c.post("/api/anomaly/nos/inspect", files={"image": ("t.jpg", buf_ok.tobytes(), "image/jpeg")})
        assert bad.status_code == 409
