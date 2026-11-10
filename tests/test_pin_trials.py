"""Segmentation HTTP contracts, disposal and binary-mask semantics; no accuracy claims."""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import threading

import numpy as np
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from visual.auth import AuthStore
from visual.labels import LabelError
from visual.pin_trials import PinTrials
from visual.server import build_app


def upload(size=(517, 13)):
    stream = io.BytesIO()
    Image.new('RGB', size, 'white').save(stream, 'PNG')
    stream.seek(0)
    return stream


def graph(tmp_path):
    onnx = pytest.importorskip('onnx')
    pytest.importorskip('onnxruntime')
    values = np.zeros((1, 2, 512, 512), dtype=np.float32)
    values[:, 1, :5, :7] = 1
    node = onnx.helper.make_node('Constant', [], ['logits'], value=onnx.numpy_helper.from_array(values))
    graph = onnx.helper.make_graph([node], 'pin-fixture',
        [onnx.helper.make_tensor_value_info('images', onnx.TensorProto.FLOAT, [1, 3, 512, 512])],
        [onnx.helper.make_tensor_value_info('logits', onnx.TensorProto.FLOAT, [1, 2, 512, 512])])
    model = onnx.helper.make_model(graph, opset_imports=[onnx.helper.make_opsetid('', 13)])
    model.ir_version = 8
    path = tmp_path / 'pin.onnx'
    onnx.save(model, path)
    return path


def register(service, path):
    service.engines.register('pin-fixture', path, ['background', 'pin'], 'Fixture',
        metadata={'expected_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'usage': 'evaluation_only'})


def test_real_worker_mask_shape_stitching_and_no_boxes(tmp_path):
    service = PinTrials(tmp_path)
    register(service, graph(tmp_path))
    result = service.trial('pin-fixture', upload())
    mask = Image.open(io.BytesIO(base64.b64decode(result['mask_png_base64'])))
    assert mask.mode == 'L' and mask.size == (517, 13)
    assert result['mask_encoding'] == 'binary_png_0_255'
    assert result['foreground_pixels'] == 60 and result['patch_count'] == 2
    assert result['defect_verdict'] is None and 'targets' not in result
    assert result['task'] == 'segment' and service._process is None
    assert service._slot.acquire(False)
    service._slot.release()
    service.close()
    with pytest.raises(LabelError) as error:
        service.trial('pin-fixture', upload())
    assert error.value.status == 503


def test_shared_busy_sha_and_image_limits(tmp_path):
    slot = threading.Lock()
    service = PinTrials(tmp_path, slot=slot)
    path = graph(tmp_path)
    register(service, path)
    slot.acquire()
    with pytest.raises(LabelError) as error:
        service.trial('pin-fixture', upload())
    assert error.value.status == 429
    slot.release()
    with pytest.raises(LabelError) as error:
        service.trial('pin-fixture', upload((9000, 1)))
    assert error.value.status == 413 and service._process is None
    with pytest.raises(LabelError):
        service.trial('pin-fixture', io.BytesIO(b'not an image'))
    path.write_bytes(b'changed')
    with pytest.raises(LabelError, match='指纹'):
        service.trial('pin-fixture', upload())


def test_timeout_kills_worker(tmp_path, monkeypatch):
    service = PinTrials(tmp_path, timeout_s=.1)
    register(service, graph(tmp_path))
    original = subprocess.Popen
    children = []
    def slow(args, **kwargs):
        child = original([sys.executable, '-c', 'import time;time.sleep(60)'], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr('visual.pin_trials.subprocess.Popen', slow)
    with pytest.raises(LabelError) as error:
        service.trial('pin-fixture', upload())
    assert error.value.status == 504 and children[0].poll() is not None
    assert service._process is None


def test_close_terminates_only_owned_worker(tmp_path, monkeypatch):
    service = PinTrials(tmp_path)
    register(service, graph(tmp_path))
    original = subprocess.Popen
    started = threading.Event()
    children, errors = [], []
    def slow(args, **kwargs):
        child = original([sys.executable, '-c', 'import time;time.sleep(60)'], **kwargs)
        children.append(child)
        started.set()
        return child
    monkeypatch.setattr('visual.pin_trials.subprocess.Popen', slow)
    def trial():
        try:
            service.trial('pin-fixture', upload())
        except LabelError as error:
            errors.append(error.status)
    thread = threading.Thread(target=trial)
    thread.start()
    assert started.wait(5)
    service.close()
    thread.join(5)
    assert not thread.is_alive() and errors == [503] and children[0].poll() is not None


def test_bad_mask_rejected(tmp_path, monkeypatch):
    service = PinTrials(tmp_path)
    register(service, graph(tmp_path))
    def invalid(spec):
        Image.new('L', (517, 13), 123).save(spec['mask_path'])
        return {'foreground_pixels': 0, 'patch_count': 2, 'inference_ms': 1}
    monkeypatch.setattr(service, '_run', invalid)
    with pytest.raises(LabelError, match='无效掩膜'):
        service.trial('pin-fixture', upload())


def test_public_manifest_missing_asset_stays_visible(tmp_path):
    public = tmp_path / 'models/public'
    public.mkdir(parents=True)
    entry = {'id': 'pin-fixture', 'task': 'pin_segmentation', 'classes': ['background', 'pin'],
             'model_path': 'models/public/missing.onnx', 'name': 'Pin', 'sha256': 'a' * 64,
             'license_status': 'test', 'source': {}}
    manifest = public / 'pin-models.json'
    manifest.write_text(json.dumps({'schema_version': 1, 'models': [entry]}))
    row = PinTrials(tmp_path).catalog()[0]
    assert not row['available'] and row['task'] == 'segment' and 'model_path' not in row
    entry['model_path'] = '../outside.onnx'
    manifest.write_text(json.dumps({'schema_version': 1, 'models': [entry]}))
    with pytest.raises(ValueError):
        PinTrials(tmp_path)


def test_actual_app_permissions_catalog_and_detection_isolation(tmp_path):
    auth = AuthStore(tmp_path / 'auth.db')
    for role in ('viewer', 'operator'):
        auth.set_user(role, 'test-password', role)
    app = build_app([], tmp_path / 'data', auth=auth)
    manager = app.state.manager
    register(manager.pin_trials, graph(tmp_path))
    try:
        with TestClient(app) as client:
            assert client.get('/api/models').status_code == 401
            client.post('/api/auth/login', json={'username': 'viewer', 'password': 'test-password'})
            rows = client.get('/api/models').json()
            assert next(row for row in rows if row['id'] == 'pin-fixture')['task'] == 'segment'
            url = '/api/models/pin-fixture/trial'
            assert client.post(url, files={'file': ('pin.png', upload().getvalue())}).status_code == 403
            client.post('/api/auth/login', json={'username': 'operator', 'password': 'test-password'})
            result = client.post(url, files={'file': ('pin.png', upload().getvalue())})
            assert result.status_code == 200 and result.json()['foreground_pixels'] == 60
            assert client.post('/api/models/pin-fixture/project', json={}).status_code == 409
            assert not manager.labels.list_projects()
            assert 'pin-fixture' not in [row['id'] for row in manager.label_engines.list()]
    finally:
        auth.close()
    assert manager.pin_trials._closed
