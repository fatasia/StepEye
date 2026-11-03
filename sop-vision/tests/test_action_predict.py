"""Adapter contracts; the tiny ONNX fixture is not a VideoMAE benchmark."""
import base64
from pathlib import Path

import numpy as np
import pytest

from visual.action_predict import (ActionPredictError, ActionPredictor, CLIP_SHAPE,
                                   action_result, normalize_clip, prepare_clip, prepare_frame)


@pytest.fixture
def model(tmp_path):
    # ReduceMean over temporal/spatial axes, RGB means -> three logits.
    path = tmp_path / "fixture.onnx"
    path.write_bytes(base64.b64decode("CAg6hwEKPAoEY2xpcBIGc2NvcmVzIgpSZWR1Y2VNZWFuKg8KBGF4ZXNAAkADQASgAQcqDwoIa2VlcGRpbXMYAKABAhIHZml4dHVyZVokCgRjbGlwEhwKGggBEhYKAggBCgIIAwoCCBAKAwjgAQoDCOABYhgKBnNjb3JlcxIOCgwIARIICgIIAQoCCANCBAoAEA0="))
    return path


def test_preprocess_preserves_rgb_and_fixed_shape():
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    frame[..., 0] = 255
    clip = prepare_clip([frame] * 16)
    assert clip.shape == CLIP_SHAPE and clip.flags.c_contiguous
    tensor = normalize_clip(clip)
    assert tensor.shape == (1, 3, 16, 224, 224)
    assert tensor.dtype == np.float32 and tensor.flags.c_contiguous
    np.testing.assert_allclose(tensor[0, :, 0, 0, 0], [(1-.485)/.229, -.456/.224, -.406/.225], rtol=1e-6)


@pytest.mark.parametrize("frame", [np.zeros((0, 2, 3), np.uint8), np.zeros((20, 20), np.uint8),
                                  np.zeros((20, 20, 3), np.float32), np.zeros((1, 1000, 3), np.uint8)])
def test_bad_frame_rejected(frame):
    with pytest.raises(ActionPredictError):
        prepare_frame(frame)


def test_no_temporal_padding():
    with pytest.raises(ActionPredictError):
        prepare_clip([np.zeros((224, 224, 3), np.uint8)] * 15)
    with pytest.raises(ActionPredictError):
        normalize_clip(np.zeros(CLIP_SHAPE, np.float32))


def test_logits_stable_and_strict():
    result = action_result(np.array([[10000., 9999.]]), ["take", "put"], "sha")
    assert result["label"] == "take" and 0.73 < result["confidence"] < 0.74
    for logits in [np.array([[np.nan, 1.]]), np.array([1., 2.]), np.array([[1, 2]])]:
        with pytest.raises(ActionPredictError):
            action_result(logits, ["a", "b"], "sha")


def test_invalid_configuration_before_process(model):
    for kwargs in [{"expected_sha256": "wrong"}, {"providers": ["unknown"]}, {"timeout_s": float("nan")}]:
        with pytest.raises(ActionPredictError):
            ActionPredictor(model, ["a", "b", "c"], **kwargs)
    with pytest.raises(ActionPredictError):
        ActionPredictor(model, ["a", "a"])


def test_real_worker_roundtrip_and_close(model):
    pytest.importorskip("onnxruntime")
    with ActionPredictor(model, ["red", "green", "blue"], timeout_s=20) as predictor:
        clip = np.zeros(CLIP_SHAPE, np.uint8)
        clip[..., 0] = 255
        result = predictor.predict(clip)
        assert result["label"] == "red" and result["confidence"] > .9
        assert predictor.predict(clip)["model_sha256"] == result["model_sha256"]
        process, temp = predictor._process, Path(predictor._temp.name)
    predictor.close()
    assert process.poll() is not None and not temp.exists()
    assert not predictor._reader.is_alive()
    with pytest.raises(ActionPredictError):
        predictor.predict(clip)


def test_worker_rejects_wrong_output_class_count(model):
    pytest.importorskip("onnxruntime")
    with pytest.raises(ActionPredictError):
        ActionPredictor(model, ["one", "two"], timeout_s=20)


def test_inference_failure_releases_process(model, monkeypatch):
    pytest.importorskip("onnxruntime")
    predictor = ActionPredictor(model, ["a", "b", "c"], timeout_s=20)
    def timeout(*args):
        raise ActionPredictError("Action worker timed out")
    monkeypatch.setattr(predictor, "_receive", timeout)
    with pytest.raises(ActionPredictError, match="timed out"):
        predictor.predict(np.zeros(CLIP_SHAPE, np.uint8))
    assert predictor._process.poll() is not None
    assert not predictor._reader.is_alive()
    assert not Path(predictor._temp.name).exists()
