"""Dataset conversion tests use synthetic files, not wrist accuracy evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('prepare_wrist', Path(__file__).parents[1] / 'tools/prepare_wrist_dataset.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def source(tmp_path):
    assets, rows = [], []
    for number in range(8):
        path = tmp_path / f'{number}.jpg'
        path.write_bytes(str(number).encode())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append({'id': number, 'created': f'2022-08-{number // 2 + 10:02d}T10:00:00Z'})
        assets.append({'id': number, 'split': 'testing' if number >= 6 else 'training',
                       'path': path.name, 'sha256': digest, 'pixel_sha256': digest,
                       'targets': [{'label': label, 'xyxy_norm': [.1, .2, .3, .4]} for label in module.CLASSES]})
    audit = tmp_path / 'audit.json'
    audit.write_text(json.dumps({'classes': module.CLASSES, 'assets': assets,
                                'source': 'test fixture', 'license': 'test fixture'}))
    rowfile = tmp_path / 'rows.json'
    rowfile.write_text(json.dumps(rows))
    return audit, rowfile, tmp_path / 'output'


def test_capture_groups_and_author_test_are_kept_separate(source):
    result = module.prepare(*source)
    by_split = {s: {i['id'] for i in result['images'] if i['split'] == s} for s in ['train', 'val', 'test']}
    assert by_split == {'train': {4, 5}, 'val': {0, 1, 2, 3}, 'test': {6, 7}}
    assert (source[2] / 'labels/train/4.txt').read_text().splitlines()[2].startswith('2 ')
    import yaml
    training = yaml.safe_load((source[2] / 'dataset.yaml').read_text())
    assert training['path'] == '.' and 'test' not in training
    assert training['train'] == 'images/train' and training['val'] == 'images/val'
    with pytest.raises(ValueError, match='exists'):
        module.prepare(*source)


def test_changed_image_is_rejected_before_creating_output(source):
    (source[0].parent / '4.jpg').write_bytes(b'changed')
    with pytest.raises(ValueError, match='SHA'):
        module.prepare(*source)
    assert not source[2].exists()


def test_exact_training_copy_of_holdout_is_removed(source):
    data = json.loads(source[0].read_text())
    data['assets'][4]['pixel_sha256'] = data['assets'][6]['pixel_sha256']
    source[0].write_text(json.dumps(data))
    result = module.prepare(*source)
    assert result['excluded_training_exact_duplicates'] == [4]
    assert not (source[2] / 'images/train/4.jpg').exists()
