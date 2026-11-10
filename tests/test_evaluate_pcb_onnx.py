import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('evaluate_pcb', Path(__file__).parents[1]/'tools/evaluate_pcb_onnx.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def data():
    return {'categories': [{'id': n, 'name': c} for n, c in enumerate(module.CLASSES, 1)],
        'images': [{'id': 1, 'width': 226, 'height': 226}],
        'annotations': [{'image_id': 1, 'category_id': 9, 'bbox': [200, 200, 26.0113, 26]}]}


def test_original_box_clipping_is_counted():
    result, clipped = module.truths(data())
    assert clipped == 1 and result[1][0]['label'] == 'BMFO'
    assert result[1][0]['box'] == pytest.approx([200/226, 200/226, 1, 1])


def test_invalid_geometry_and_classes_rejected():
    value = data(); value['annotations'][0]['bbox'][2] = 30
    with pytest.raises(ValueError, match='geometry'): module.truths(value)
    value = data(); value['categories'][0]['name'] = 'wrong'
    with pytest.raises(ValueError, match='class order'): module.truths(value)


def test_unpinned_archive_never_starts_inference(tmp_path):
    archive = tmp_path/'bad.zip'; archive.write_bytes(b'wrong')
    with pytest.raises(ValueError, match='archive SHA'):
        module.run(tmp_path/'model.onnx', archive, tmp_path/'report.json')


def test_all_2051_frames_aggregate_counts_not_batch_rates():
    frames = [{'id': str(i), 'source_sha256': 'a'*64, 'reviewed': True,
               'truth': [{'label': 'SH', 'box': [.1, .1, .2, .2]}],
               'predictions': ([{'label': 'SH', 'box': [.1, .1, .2, .2], 'confidence': .9}]
                               if i < 500 else [])} for i in range(2051)]
    report = module.evaluate_all(frames)
    assert report['frames'] == len(report['details']) == 2051
    assert report['summary']['tp'] == 500 and report['summary']['fn'] == 1551
    assert report['summary']['recall'] == pytest.approx(500/2051)
    assert report['missed_frames'] == 1551
    assert report['classes']['SH'] == report['summary']
