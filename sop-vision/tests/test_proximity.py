"""Known planar geometry replay; no live camera or field precision claim."""
from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from visual.calibration import CalibrationStore
from visual.proximity import ProximityCfg, ProximityMonitor
from visual.proximity_routes import router
from visual.tracking import ObjectTrack
from visual.dsl import StationConfig
from visual.runtime import StationRuntime


@pytest.fixture
def bound(tmp_path):
    store = CalibrationStore(tmp_path / 'c.db')
    def pair(x, y):
        return {'pixel': [x, y], 'physical': [x / 10, y / 10]}
    record = store.create({'scope_id': 'cam', 'reference': 'fixed', 'image_size': [100, 100],
        'plane': 'ground', 'unit': 'm', 'points': [pair(0,0), pair(99,0), pair(99,99), pair(0,99)],
        'validation_points': [pair(25,25), pair(75,75)], 'tolerance': .001})
    cfg = ProximityCfg(enabled=True, calibration_id=record['id'], scope_id='cam', reference='fixed',
        image_size=(100,100), near_m=1.2, speed_mps=.5, min_s=.2, speed_window_s=.2)
    monitor = ProximityMonitor(cfg, (100,100))
    monitor.bind_calibrations(store)
    yield store, cfg, monitor
    store.close()


def track(ts, x, label='forklift', tid=1):
    return ObjectTrack(tid, label, (x-2, 20, x+2, 50), .9, 0, ts, 10)


def tracks(ts):
    return [track(ts, 40 + ts*10), track(ts, 50, 'person', 2)]


def test_ground_speed_proximity_confirm_and_one_shot(bound):
    _, _, m = bound
    alerts = []
    for ts in [0,.1,.2,.3,.4,.5]:
        alerts += m.process(tracks(ts), ts)
    near = [a for a in alerts if a['kind'] == 'vehicle_proximity']
    speed = [a for a in alerts if a['kind'] == 'vehicle_speed']
    assert len(near) == len(speed) == 1
    assert near[0]['distance_m'] == pytest.approx(.8)
    assert speed[0]['speed_mps'] == pytest.approx(1.)
    assert m.snapshot()['pairs'][0]['relative_speed_mps'] == pytest.approx(1.)
    assert m.snapshot()['status'] == 'alert'


@pytest.mark.parametrize('failure', ['outside','stale','labels','gap','reverse','duplicate','outlier','unavailable','size'])
def test_invalid_evidence_clears_holds_and_speeds(bound, failure):
    _, _, m = bound
    m.process(tracks(0),0)
    m.process(tracks(.1),.1)
    ts, current, kwargs = .2, tracks(.2), {}
    if failure == 'outside':
        current[0] = track(ts,101)
    elif failure == 'stale':
        current = tracks(.1)
    elif failure == 'labels':
        kwargs['available_labels'] = {'person'}
    elif failure == 'gap':
        ts = 3
        current = tracks(ts)
    elif failure == 'reverse':
        ts = .05
        current = tracks(ts)
    elif failure == 'duplicate':
        ts = .1
        current = tracks(ts)
    elif failure == 'outlier':
        current[0] = track(ts,90)
    elif failure == 'unavailable':
        kwargs['available'] = False
    else:
        kwargs['frame_size'] = (200,200)
    assert not m.process(current,ts,**kwargs)
    assert m.snapshot()['status'] in ('unknown','clear')
    assert not m._holds
    assert not m.process(tracks(.3),.3)


def test_missing_wrong_scale_binding_unknown(bound):
    store, cfg, _ = bound
    for update in [{'calibration_id': 'missing'}, {'reference': 'moved'}, {'image_size': (200,200)}]:
        bad = ProximityMonitor(cfg.model_copy(update=update), (100,100))
        bad.bind_calibrations(store)
        assert not bad.process(tracks(0),0)
        assert bad.snapshot()['status'] == 'unknown'


def test_identity_change_restarts_speed_window(bound):
    _, _, m = bound
    for ts in [0,.1]:
        m.process(tracks(ts),ts)
    changed = [track(.2,42,tid=3), track(.2,50,'person',2)]
    assert not m.process(changed,.2)
    assert m.snapshot()['vehicles'][0]['speed_mps'] is None


def configuration(cfg):
    return StationConfig(station='cam', zones=[], source=dict(type='sim',width=100,height=100),
        hands=dict(enabled=False), detector=dict(enabled=False,classes=['person','forklift']),
        proximity=cfg)


def test_real_runtime_safety_events_and_reset(bound, tmp_path):
    store, cfg, _ = bound
    rt = StationRuntime(configuration(cfg), tmp_path/'runtime')
    class Detector:
        mot = True
        class_names = ['person','forklift']
        def process(self, frame, ts, ctx):
            ctx.object_tracks.extend(tracks(ts))
            return []
        def reset_observations(self):
            pass
        def close(self):
            pass
    rt.detector = Detector()
    rt.bind_calibrations(store)
    events=[]
    rt.listeners.append(lambda kind,payload: events.append((kind,payload)))
    try:
        for ts in [0,.1,.2,.3,.4]:
            rt.process_frame(ts,np.full((100,100,3),100,np.uint8))
        assert {p['kind'] for k,p in events if k == 'safety'} == {'vehicle_proximity','vehicle_speed'}
        assert rt.alerts[0]['calibration_id'] == cfg.calibration_id
        rt._reset_visual_observations()
        assert rt.state()['proximity']['status'] == 'unknown'
    finally:
        rt.close()


def test_config_api_persists_and_validates_binding(bound, tmp_path):
    store, cfg, _ = bound
    rt = StationRuntime(configuration(cfg),tmp_path/'http-runtime')
    rt.bind_calibrations(store)
    path=tmp_path/'station.yaml'
    path.write_text(yaml.safe_dump(rt.cfg.model_dump(mode='json')),encoding='utf-8')
    def reload(sid):
        rt.cfg = StationConfig.model_validate(yaml.safe_load(path.read_text(encoding='utf-8')))
        rt.proximity = ProximityMonitor(rt.cfg.proximity,(100,100))
    manager=SimpleNamespace(paths={'cam':path},runtimes={'cam':rt},reload=reload)
    app=FastAPI()
    app.include_router(router(manager,store))
    client=TestClient(app)
    try:
        response=client.put('/api/stations/cam/proximity',json=cfg.model_dump(mode='json'))
        assert response.status_code == 200, response.text
        assert yaml.safe_load(path.read_text(encoding='utf-8'))['proximity']['calibration_id'] == cfg.calibration_id
        assert client.get('/api/stations/cam/proximity').json()['config']['enabled']
        invalid=cfg.model_dump(mode='json');invalid['reference']='moved'
        assert client.put('/api/stations/cam/proximity',json=invalid).status_code == 422
        rt.engine.cycle=object()
        assert client.put('/api/stations/cam/proximity',json=cfg.model_dump(mode='json')).status_code == 409
    finally:
        rt.engine.cycle=None
        rt.close()
