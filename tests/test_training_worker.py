"""Training orchestration tests use a fake trainer, not claimed model training evidence."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest
import yaml

from visual.training_worker import fingerprint, inspect_training_dataset, run_training


def dataset(tmp_path):
    root = tmp_path / 'data'
    for split, color in [('train', 'red'), ('val', 'blue')]:
        (root / 'images' / split).mkdir(parents=True)
        (root / 'labels' / split).mkdir(parents=True)
        Image.new('RGB', (20, 20), color).save(root / 'images' / split / f'{split}.png')
        (root / 'labels' / split / f'{split}.txt').write_text('0 .5 .5 .4 .4\n')
    (root / 'dataset.yaml').write_text(yaml.safe_dump({'path': '.', 'train': 'images/train', 'val': 'images/val', 'nc': 1, 'names': ['part']}))
    (root / 'classes.txt').write_text('part\n')
    (root / 'metadata.json').write_text(json.dumps({'dataset_version_id': 'LV-test', 'reviewed_negatives': []}))
    return root / 'dataset.yaml'


def spec(tmp_path):
    data = dataset(tmp_path)
    base = tmp_path / 'approved.pt'
    base.write_bytes(b'fake trainer fixture; not model weights')
    return {'dataset_yaml': str(data), 'base_model': str(base), 'output_dir': str(tmp_path / 'output'),
            'epochs': 1, 'batch': 1, 'device': 'cpu', 'imgsz': 640, 'base_sha256': fingerprint(base),
            'dataset_version': 'LV-test', 'dataset_sha256': inspect_training_dataset(data)['dataset_sha256']}


class FakeModel:
    calls = []
    names = {0: 'part'}
    def __init__(self, path, task):
        self.path = Path(path)
        assert task == 'detect'
    def train(self, **kwargs):
        self.calls.append(('train', kwargs))
        best = Path(kwargs['project']) / 'train' / 'weights' / 'best.pt'
        best.parent.mkdir(parents=True)
        best.write_bytes(b'fake trained checkpoint')
        self.trainer = SimpleNamespace(best=best)
    def val(self, **kwargs):
        self.calls.append(('val', kwargs))
        return SimpleNamespace(results_dict={'metrics/mAP50(B)': .5})
    def export(self, **kwargs):
        self.calls.append(('export', kwargs))
        out = self.path.with_suffix('.onnx')
        out.write_bytes(b'fake export')
        return str(out)


def test_train_validate_export_contract_and_fingerprints(tmp_path):
    cfg = spec(tmp_path)
    FakeModel.calls = []
    checked = []
    result = run_training(cfg, model_factory=FakeModel, export_check=lambda p, c: checked.append((p, c)))
    assert result['status'] == 'candidate' and result['production_ready'] is False
    assert result['dataset_sha256'] == cfg['dataset_sha256']
    assert Path(result['model_path']).with_suffix('.pt') == Path(result['checkpoint_path'])
    assert result['model_sha256'] == fingerprint(Path(result['model_path']))
    assert checked[0][1] == ['part']
    assert [c[0] for c in FakeModel.calls] == ['train', 'val', 'export']
    train, val, export = [c[1] for c in FakeModel.calls]
    assert train['workers'] == 0 and train['amp'] is False and train['imgsz'] == 640
    assert val['split'] == 'val'
    assert export['dynamic'] is False and export['nms'] is False and export['batch'] == 1
    assert json.loads((tmp_path / 'output/result.json').read_text()) == result
    with pytest.raises(ValueError, match='历史训练结果'):
        run_training(cfg, model_factory=FakeModel)


@pytest.mark.parametrize('patch', [{'download': 'evil'}, {'train': '../escape'}, {'path': '/tmp'}, {'val': ['images/val']}])
def test_dataset_yaml_rejects_download_and_paths(tmp_path, patch):
    path = dataset(tmp_path)
    data = yaml.safe_load(path.read_text()) | patch
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError): inspect_training_dataset(path)


def test_identical_image_cannot_cross_split(tmp_path):
    path = dataset(tmp_path)
    (path.parent / 'images/val/val.png').write_bytes((path.parent / 'images/train/train.png').read_bytes())
    with pytest.raises(ValueError, match='相同SHA'): inspect_training_dataset(path)


def test_missing_labels_and_unreviewed_background_rejected(tmp_path):
    path = dataset(tmp_path)
    label = path.parent / 'labels/val/val.txt'
    label.write_text('')
    with pytest.raises(ValueError, match='人工确认'): inspect_training_dataset(path)
    label.unlink()
    with pytest.raises(ValueError, match='缺少配对标签'): inspect_training_dataset(path)


@pytest.mark.parametrize('key,value', [('epochs', 0), ('batch', -1), ('imgsz', 320), ('device', '0,1'), ('base_sha256', 'x'), ('epochs', True)])
def test_spec_parameter_bounds(tmp_path, key, value):
    cfg = spec(tmp_path) | {key: value}
    with pytest.raises(ValueError): run_training(cfg, model_factory=FakeModel)


def test_changed_dataset_blocks_before_trainer(tmp_path):
    cfg = spec(tmp_path)
    Path(cfg['dataset_yaml']).parent.joinpath('labels/train/train.txt').write_text('0 .5 .5 .2 .2')
    with pytest.raises(ValueError, match='指纹变化'): run_training(cfg, model_factory=FakeModel)
    assert not Path(cfg['output_dir']).exists()


def test_train_failure_never_writes_candidate_result(tmp_path):
    class Broken(FakeModel):
        def train(self, **kwargs): raise RuntimeError('fixture failure')
    cfg = spec(tmp_path)
    with pytest.raises(RuntimeError): run_training(cfg, model_factory=Broken)
    assert not Path(cfg['output_dir']).joinpath('result.json').exists()


def test_training_mutation_rejects_candidate(tmp_path):
    cfg = spec(tmp_path)
    class Changed(FakeModel):
        def export(self, **kwargs):
            out = super().export(**kwargs)
            Path(cfg['base_model']).write_bytes(b'changed')
            return out
    with pytest.raises(ValueError, match='训练期间'):
        run_training(cfg, model_factory=Changed, export_check=lambda *args: None)
    assert not Path(cfg['output_dir']).joinpath('result.json').exists()


def test_raw_onnx_export_shape_contract(tmp_path):
    onnx = pytest.importorskip('onnx')
    from visual.training_worker import _onnx_contract
    for shape, valid in [([1, 5, 8400], True), ([1, 300, 6], False)]:
        graph = onnx.helper.make_graph([], 'fixture',
            [onnx.helper.make_tensor_value_info('images', onnx.TensorProto.FLOAT, [1, 3, 640, 640])],
            [onnx.helper.make_tensor_value_info('output', onnx.TensorProto.FLOAT, shape)],
            [onnx.helper.make_tensor('output', onnx.TensorProto.FLOAT, shape, [0.] * (shape[1] * shape[2]))])
        model = onnx.helper.make_model(graph)
        file = tmp_path / 'model.onnx'; onnx.save(model, file)
        if valid: _onnx_contract(file, ['part'])
        else:
            with pytest.raises(ValueError): _onnx_contract(file, ['part'])

@pytest.mark.parametrize('mode', ['group_leak', 'synthetic_val', 'unconfirmed'])
def test_manifest_provenance_rejected(tmp_path, mode):
    path = dataset(tmp_path)
    assets, assignments = [], {}
    for split in ('train', 'val'):
        image = path.parent / 'images' / split / f'{split}.png'
        assets.append({'id': split, 'image_sha256': fingerprint(image),
                       'state': 'draft' if mode == 'unconfirmed' else 'confirmed',
                       'synthetic': mode == 'synthetic_val' and split == 'val'})
        assignments[split] = {'split': split, 'group': 'same' if mode == 'group_leak' else split}
    (path.parent / 'manifest.json').write_text(json.dumps({'assets': assets, 'assignments': assignments}))
    with pytest.raises(ValueError): inspect_training_dataset(path)


def test_dataset_preflight_does_not_import_torch_in_parent(tmp_path):
    import subprocess
    import sys
    path = dataset(tmp_path)
    code = 'import sys; from visual.training_worker import inspect_training_dataset; inspect_training_dataset(sys.argv[1]); assert "torch" not in sys.modules and "ultralytics" not in sys.modules'
    subprocess.run([sys.executable, '-c', code, str(path)], check=True, timeout=15)
