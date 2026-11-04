"""胶路固定输入和分数协议；不使用合成框声称模型准确率。"""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from visual.glue_yolov4 import CLASSES, GlueYoloV4Backend, decode, preprocess


def prediction():
    rows = np.zeros((1, 2535, 7), dtype=np.float32)
    rows[0, 0] = [.5, .5, .2, .4, .4, .3, .01]
    return rows


def test_resize_is_direct_rgb_without_letterbox():
    frame = np.full((80, 240, 3), [10, 20, 30], dtype=np.uint8)
    tensor = preprocess(frame)
    assert tensor.shape == (1, 3, 416, 416)
    np.testing.assert_allclose(tensor[0, :, 0, 0], np.array([30, 20, 10])/255, atol=1e-7)
    np.testing.assert_array_equal(tensor[0, :, 0, 0], tensor[0, :, -1, -1])


def test_scores_are_not_multiplied_by_objectness_twice():
    hits = decode(prediction(), 200, 100)
    assert len(hits) == 1 and hits[0].label == 'discontinuity'
    assert hits[0].confidence == pytest.approx(.3)
    assert hits[0].xyxy == pytest.approx((80, 30, 120, 70))


def test_classwise_nms_and_clipping():
    rows = prediction()
    rows[0, 1] = rows[0, 0]
    rows[0, 2] = [.5, .5, .2, .4, .8, .01, .7]
    hits = decode(rows, 200, 100)
    assert [h.label for h in hits] == ['excess', 'discontinuity']
    rows[0, 2, :4] = [.05, .05, .4, .4]
    assert decode(rows, 200, 100)[0].xyxy[:2] == (0., 0.)


def test_invalid_output_and_closed_session(tmp_path):
    rows = prediction(); rows[0, 0, 5] = np.nan
    with pytest.raises(ValueError):
        decode(rows, 200, 100)
    with pytest.raises(ValueError):
        decode(np.zeros((1, 7, 2535)), 200, 100)
    with pytest.raises(ValueError):
        GlueYoloV4Backend('not-loaded.onnx', ['wrong'])
    from onnx import TensorProto, helper, numpy_helper, save
    model = helper.make_model(helper.make_graph([
        helper.make_node('Constant', [], ['predictions'], value=numpy_helper.from_array(prediction()))],
        'test', [helper.make_tensor_value_info('images', TensorProto.FLOAT, [1, 3, 416, 416])],
        [helper.make_tensor_value_info('predictions', TensorProto.FLOAT, [1, 2535, 7])]),
        opset_imports=[helper.make_opsetid('', 13)])
    model.ir_version = 8; path = tmp_path/'fixed.onnx'; save(model, path)
    with pytest.raises(ValueError, match='契约'):
        GlueYoloV4Backend(path, CLASSES)
    helper.set_model_props(model, {'adapter': 'glue_yolov4'}); save(model, path)
    from visual.detector_registry import create_backend
    detector = create_backend(path, CLASSES, .25, adapter="glue_yolov4")
    assert len(detector.detect(np.zeros((100, 200, 3), dtype=np.uint8))) == 1
    detector.close()
    with pytest.raises(RuntimeError):
        detector.detect(np.zeros((100, 200, 3), dtype=np.uint8))


def test_export_refuses_different_source(tmp_path):
    spec = importlib.util.spec_from_file_location('export_glue', Path(__file__).parents[1]/'tools/export_glue_onnx.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    cfg = tmp_path/'cfg'; cfg.write_text('[net]\nwidth=416')
    weights = tmp_path/'weights'; weights.write_bytes(b'not the registered data')
    with pytest.raises(ValueError, match='SHA'):
        module.export(cfg, weights, tmp_path/'model.onnx')
    assert not (tmp_path/'model.onnx').exists()
