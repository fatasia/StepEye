"""GenTL protocol fakes verify adapter lifecycle; not hardware/CTI acceptance."""
from types import SimpleNamespace, ModuleType
import sys
import threading

import numpy as np
import pytest

from visual.dsl import SourceCfg, IndustrialCameraCfg
from visual.industrial_camera import IndustrialCamera
from visual.sources import create_source


class Node:
    def __init__(self, value, writable=True):
        self.value, self.writable = value, writable
        self.executions = 0

    def execute(self):
        self.executions += 1


class Buffer:
    def __init__(self, fmt='RGB8', dtype=np.uint8):
        component = SimpleNamespace(width=2, height=2, data_format=fmt,
                                    data=np.arange(12, dtype=dtype))
        self.payload = SimpleNamespace(components=[component])
        self.timestamp_ns = 987654321
        self.frame_id = 7
        self.queued = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.queue()

    def queue(self):
        self.queued += 1


@pytest.fixture
def camera(tmp_path, monkeypatch):
    cti = tmp_path / 'test.cti'
    cti.write_bytes(b'protocol fixture, not a GenTL library')
    nodes = SimpleNamespace(**{name: Node(value) for name, value in {
        'TriggerSelector': 'FrameStart', 'TriggerMode': 'Off', 'TriggerSource': 'Software',
        'TriggerSoftware': None, 'ExposureAuto': 'Continuous', 'ExposureTime': 100,
        'GainAuto': 'Continuous', 'Gain': 0, 'PixelFormat': 'RGB8'}.items()})
    buffer = Buffer()
    calls = []
    ia = SimpleNamespace(remote_device=SimpleNamespace(node_map=nodes),
                         start=lambda: calls.append('start'), stop=lambda: calls.append('stop'),
                         destroy=lambda: calls.append('destroy'),
                         try_fetch=lambda timeout: buffer)
    class Harvester:
        def __init__(self):
            calls.append('construct')
        def add_file(self, path):
            assert path == str(cti)
        def update(self):
            calls.append('update')
        def create(self, filters):
            assert filters == {'serial_number': 'SN1'}
            return ia
        def reset(self):
            calls.append('reset')
    core = ModuleType('harvesters.core')
    core.Harvester = Harvester
    genicam = ModuleType('genicam')
    genicam.genapi = SimpleNamespace(is_readable=lambda node: True,
                                     is_writable=lambda node: node.writable)
    monkeypatch.setitem(sys.modules, 'harvesters', ModuleType('harvesters'))
    monkeypatch.setitem(sys.modules, 'harvesters.core', core)
    monkeypatch.setitem(sys.modules, 'genicam', genicam)
    cfg = IndustrialCameraCfg(cti=str(cti), serial_number='SN1', exposure_us=250, gain=2)
    cam = IndustrialCamera(cfg)
    yield cam, nodes, buffer, calls, ia
    cam.close()


def test_lazy_acquisition_readback_copy_and_release(camera):
    cam, nodes, buffer, calls, _ = camera
    assert not calls and not cam.capabilities()['opened']
    ts, frame = cam.read()
    assert ts >= 0 and frame.shape == (2, 2, 3)
    assert frame[0, 0].tolist() == [2, 1, 0]
    assert buffer.queued == 1
    assert nodes.ExposureAuto.value == 'Off' and nodes.ExposureTime.value == 250
    assert nodes.GainAuto.value == 'Off' and nodes.Gain.value == 2
    buffer.payload.components[0].data[:] = 0
    assert frame[0, 0].tolist() == [2, 1, 0]
    cap = cam.capabilities()
    assert cap['last_frame']['hardware_timestamp_ns'] == 987654321
    assert cap['nodes']['ExposureTime']['value'] == 250
    cam.close()
    cam.close()
    assert calls[-3:] == ['stop', 'destroy', 'reset']
    assert calls.count('destroy') == 1 and cam.read() is None


def test_software_trigger(camera):
    cam, nodes, _, _, _ = camera
    cam.config.trigger = 'software'
    with pytest.raises(ValueError):
        cam.software_trigger()
    cam.read()
    assert cam.software_trigger() == {"queued": True}
    with pytest.raises(RuntimeError):
        cam.software_trigger()
    cam.read()
    assert nodes.TriggerSoftware.executions == 1
    assert nodes.TriggerMode.value == 'On'


def test_unsupported_node_cleanup(camera):
    cam, nodes, _, calls, _ = camera
    nodes.ExposureTime.writable = False
    with pytest.raises(ValueError, match='ExposureTime'):
        cam.read()
    assert calls[-2:] == ['destroy', 'reset']
    assert 'start' not in calls


def test_timeout_is_unknown_not_fake_image(camera):
    cam, _, _, _, ia = camera
    ia.try_fetch = lambda timeout: None
    _, frame = cam.read()
    assert frame.shape == (2, 2, 3)  # runtime recognizes this existing gap sentinel


def test_stop_during_fetch_returns_buffer(camera):
    cam, _, buffer, _, ia = camera
    def fetch(timeout):
        assert timeout == .2
        cam.stop_event.set()
        return buffer
    ia.try_fetch = fetch
    assert cam.read() is None and buffer.queued == 1


@pytest.mark.parametrize('kind', ['format', 'depth', 'size', 'multipart'])
def test_bad_frame_returns_buffer_and_releases(camera, kind):
    cam, _, buffer, calls, _ = camera
    component = buffer.payload.components[0]
    if kind == 'format':
        component.data_format = 'Mono12Packed'
    elif kind == 'depth':
        component.data = component.data.astype(np.uint16)
    elif kind == 'size':
        component.width = 500000
    else:
        buffer.payload.components.append(component)
    with pytest.raises(ValueError):
        cam.read()
    assert buffer.queued == 1 and calls[-3:] == ['stop', 'destroy', 'reset']


def test_factory_and_required_config(tmp_path):
    with pytest.raises(ValueError):
        SourceCfg(type='industrial')
    source = create_source(SourceCfg(type='industrial', industrial={
        'cti': str(tmp_path / 'absent.cti'), 'serial_number': 'SN1'}))
    with pytest.raises(ValueError, match='CTI'):
        source.read()
    source.close()


def test_cleanup_continues_after_stop_error(camera):
    cam, _, _, calls, ia = camera
    cam.read()
    def failure():
        raise RuntimeError('failed stop')
    ia.stop = failure
    cam.close()
    assert calls[-2:] == ['destroy', 'reset']
    assert cam.last_metadata['release_errors'] == ['RuntimeError']
