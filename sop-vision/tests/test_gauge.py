import copy
import io
import math
from pathlib import Path
import subprocess
import sys
import threading
import time

from PIL import Image
from pydantic import ValidationError
import pytest

import visual.gauge as gauge

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION = {'min_value': 0, 'max_value': 10, 'unit': 'bar', 'direction': 'clockwise'}


def points(angles=(180, 0, 270), width=200, height=100):
    cx, cy, radius = width / 2, height / 2, min(width, height) / 3
    return [[(cx + radius * math.cos(math.radians(a))) / width,
             (cy + radius * math.sin(math.radians(a))) / height] for a in angles] + [[.5, .5]]


def detection():
    return {'bbox_xyxy_norm': [0, 0, 1, 1], 'confidence': .9,
            'keypoints': [{'name': name, 'x_norm': p[0], 'y_norm': p[1], 'confidence': .95}
                          for name, p in zip(gauge.KEYPOINTS, points())]}


def image():
    stream = io.BytesIO(); Image.new('RGB', (200, 100), 'white').save(stream, format='PNG')
    return stream.getvalue()


def test_geometry_requires_explicit_range_unit_and_direction():
    result = gauge.geometry(points(), 200, 100)
    assert result['status'] == 'uncalibrated' and result['reading'] is None
    assert result['ratio'] is None and result['unit'] is None
    result = gauge.geometry(points(), 200, 100, CALIBRATION)
    assert result['status'] == 'reading' and result['reading'] == pytest.approx(5)
    assert result['ratio'] == pytest.approx(.5)


def test_image_aspect_ratio_and_wraparound_are_not_lost_by_normalization():
    result = gauge.geometry(points((0, 90, 45)), 200, 100, CALIBRATION)
    assert result['reading'] == pytest.approx(5)
    result = gauge.geometry(points((350, 20, 5)), 200, 100, CALIBRATION)
    assert result['sweep_degrees'] == pytest.approx(30)
    assert result['reading'] == pytest.approx(5)
    result = gauge.geometry(points((0, 180, 270)), 200, 100,
                            CALIBRATION | {'direction': 'counterclockwise'})
    assert result['reading'] == pytest.approx(5)


@pytest.mark.parametrize('angles,reason', [
    ((0, 90, 180), 'needle_outside_configured_sweep'),
    ((0, 0, 0), 'degenerate_scale_span'), ((0, 2, 1), 'degenerate_scale_span'),
])
def test_unknown_geometry_never_clamps_to_endpoint(angles, reason):
    result = gauge.geometry(points(angles), 200, 100, CALIBRATION)
    assert result['status'] == 'unknown' and result['reading'] is None
    assert result['ratio'] is None and result['reason'] == reason


def test_invalid_or_degenerate_points_rejected():
    for bad in [None, [], [[float('nan'), .5]] * 4, [[1.1, .5]] * 4,
                [[True, .5]] * 4]:
        assert gauge.geometry(bad, 200, 100, CALIBRATION)['status'] == 'unknown'
    p = points(); p[2] = [.5, .5]
    assert gauge.geometry(p, 200, 100, CALIBRATION)['reason'] == 'degenerate_keypoint_radius'


@pytest.mark.parametrize('change', [
    {'max_value': 0}, {'min_value': float('nan')}, {'unit': '  '},
    {'direction': 'auto'}, {'unit': '<script>'}, {'max_value': True}, {'scale_from_ocr': True},
])
def test_calibration_never_guesses_or_accepts_invalid_value(change):
    with pytest.raises(ValidationError):
        gauge.GaugeCalibration(**(CALIBRATION | change))
    with pytest.raises(ValidationError):
        gauge.GaugeCalibration(min_value=0, max_value=10)


def test_service_preserves_pose_and_separates_uncalibrated_and_ambiguous(tmp_path, monkeypatch):
    service = gauge.GaugeService(tmp_path)
    monkeypatch.setattr(gauge, 'pinned_model', lambda *_: None)
    rows = [detection()]
    monkeypatch.setattr(service, '_run', lambda *_: (copy.deepcopy(rows), 1.2))
    result = service.trial(image())
    assert result['task'] == 'gauge_pose' and result['status'] == 'uncalibrated'
    assert len(result['detections'][0]['keypoints']) == 4 and result['reading'] is None
    assert 'targets' not in result and not result['alarm_generated']
    assert service.trial(image(), CALIBRATION)['reading'] == pytest.approx(5)
    rows.append(detection())
    result = service.trial(image(), CALIBRATION)
    assert result['status'] == 'multiple_gauges' and result['reading'] is None
    rows.clear()
    assert service.trial(image(), CALIBRATION)['status'] == 'no_detection'
    service.close()


@pytest.mark.parametrize('bad', [None, {'bbox_xyxy_norm': [0, 0, 1, 1]},
                               {'bbox_xyxy_norm': [0, 0, float('nan'), 1], 'confidence': .9, 'keypoints': []}])
def test_bad_worker_pose_is_controlled_error(tmp_path, monkeypatch, bad):
    service = gauge.GaugeService(tmp_path)
    monkeypatch.setattr(gauge, 'pinned_model', lambda *_: None)
    monkeypatch.setattr(service, '_run', lambda *_: ([bad], 1))
    with pytest.raises(gauge.GaugeError) as error:
        service.trial(image())
    assert error.value.status == 503
    service.close()


def test_missing_asset_bad_image_busy_and_closed(tmp_path, monkeypatch):
    service = gauge.GaugeService(tmp_path)
    assert not service.capabilities()['available']
    with pytest.raises(gauge.GaugeError) as error: service.trial(image())
    assert error.value.status == 503
    monkeypatch.setattr(gauge, 'pinned_model', lambda *_: None)
    with pytest.raises(gauge.GaugeError): service.trial(b'invalid image')
    service._slot.acquire()
    try:
        with pytest.raises(gauge.GaugeError) as error: service.trial(image())
        assert error.value.status == 429
    finally: service._slot.release()
    service.close(); service.close()
    with pytest.raises(gauge.GaugeError) as error: service.trial(image())
    assert error.value.status == 503


def test_real_sleeping_child_times_out_without_stdin_deadlock(tmp_path, monkeypatch):
    real_popen, processes = subprocess.Popen, []
    def sleeping(*args, **kwargs):
        process = real_popen([sys.executable, '-c', 'import time;time.sleep(60)'], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(gauge, 'pinned_model', lambda *_: None)
    monkeypatch.setattr(gauge.subprocess, 'Popen', sleeping)
    service = gauge.GaugeService(tmp_path, timeout_s=.05)
    started = time.monotonic()
    with pytest.raises(gauge.GaugeError) as error: service.trial(image())
    assert error.value.status == 504 and time.monotonic() - started < 3
    assert processes[0].poll() is not None and service._process is None
    assert not service._slot.locked()
    service.close()


def test_close_stops_inflight_owned_process(tmp_path, monkeypatch):
    real_popen, processes, started = subprocess.Popen, [], threading.Event()
    def sleeping(*args, **kwargs):
        process = real_popen([sys.executable, '-c', 'import time;time.sleep(60)'], **kwargs)
        processes.append(process); started.set()
        return process
    monkeypatch.setattr(gauge, 'pinned_model', lambda *_: None)
    monkeypatch.setattr(gauge.subprocess, 'Popen', sleeping)
    service, errors = gauge.GaugeService(tmp_path), []
    def trial():
        try: service.trial(image())
        except gauge.GaugeError as error: errors.append(error.status)
    thread = threading.Thread(target=trial)
    thread.start()
    assert started.wait(3)
    service.close(); thread.join(3)
    assert not thread.is_alive() and errors == [503] and processes[0].poll() is not None


def test_real_fixed_photo_child_preserves_keypoints_without_inventing_scale():
    source = ROOT.parent / '.v0/quality-gaps/gauge-input-meter.jpg'
    service = gauge.GaugeService(ROOT)
    if not source.exists() or not service.capabilities()['available']:
        pytest.skip('optional pinned gauge ONNX and source photo not installed')
    try:
        result = service.trial(source.read_bytes())
        assert result['status'] == 'uncalibrated' and result['reading'] is None
        assert len(result['detections']) == 1 and result['detections'][0]['confidence'] > .9
        assert [p['name'] for p in result['detections'][0]['keypoints']] == list(gauge.KEYPOINTS)
        assert service._process is None
    finally: service.close()


def test_import_and_idle_constructor_do_not_start_runtime():
    code = ('import sys,threading;before=set(threading.enumerate());'
            'from visual.gauge import GaugeService;g=GaugeService(".");g.capabilities();'
            'assert "onnxruntime" not in sys.modules and "torch" not in sys.modules;'
            'assert set(threading.enumerate())==before;g.close()')
    process = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, timeout=15)
    assert process.returncode == 0, process.stderr.decode(errors='replace')
