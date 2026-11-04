"""Geometric replay and authorization checks; no live-camera precision claims."""
import copy
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from visual.calibration import CalibrationStore
from visual.cross_camera import CrossCameraStore, GroupBody, associate
from visual.cross_camera_routes import router


@pytest.fixture
def setup(tmp_path):
    calibrations = CalibrationStore(tmp_path / 'c.db')
    def pair(x, y):
        return {'pixel': [x, y], 'physical': [x / 10, y / 10]}
    members, snaps = [], {}
    for station in ['a', 'b']:
        rec = calibrations.create({'scope_id': station, 'reference': 'fixed', 'image_size': [100, 100],
            'plane': 'ground', 'unit': 'm', 'points': [pair(0,0), pair(99,0), pair(99,99), pair(0,99)],
            'validation_points': [pair(25,25), pair(75,75)], 'tolerance': .001})
        members.append({'station': station, 'calibration_id': rec['id'], 'scope_id': station,
                        'reference': 'fixed', 'image_size': [100, 100]})
        snaps[station] = {'station': station, 'epoch': 'x', 'generation': 0, 'frame_seq': 1,
            'received_mono': 10., 'available': True, 'frame_size': [100,100],
            'source_type': 'rtsp', 'available_labels': ['person'],
            'tracks': [{'track_id': 1, 'label': 'person', 'xyxy': [38,20,42,50], 'confidence': .9}]}
    store = CrossCameraStore(tmp_path / 'groups.json', calibrations, snaps.get)
    body = GroupBody(name='Floor', coordinate_frame_id='floor-1', members=members, labels=['person'])
    store.save('g', body)
    yield store, body, snaps
    calibrations.close()


def test_persistence_and_candidates(setup):
    store, body, snaps = setup
    loaded = CrossCameraStore(store.path, store.calibrations, snaps.get)
    result = loaded.snapshot('g', now=10.1)
    assert result['status'] == 'candidates'
    assert result['candidate_groups'] == [[0,1]]
    assert result['visible_count'] == 2
    assert 'confirmed_count' not in result
    assert loaded.get('g') == body


@pytest.mark.parametrize('change,reason', [
    ({'available': False}, 'member_unknown'),
    ({'received_mono': 8}, 'member_unknown'),
    ({'received_mono': float('nan')}, 'member_unknown'),
    ({'received_mono': 10.15}, 'member_unknown'),
    ({'received_mono': 9.7}, 'receipt_skew'),
    ({'frame_size': [80,80]}, 'member_unknown'),
    ({'source_type': 'file'}, 'member_unknown'),
    ({'source_type': 'sim'}, 'member_unknown'),
    ({'available_labels': []}, 'member_unknown'),
])
def test_unknown(setup, change, reason):
    store, _, snaps = setup
    snaps['b'].update(change)
    result = store.snapshot('g', now=10.1)
    assert result['status'] == 'unknown' and result['reason'] == reason
    assert result['candidate_groups'] == []


def test_current_empty_is_zero_but_missing_is_unknown(setup):
    store, _, snaps = setup
    for snap in snaps.values():
        snap['tracks'] = []
    assert store.snapshot('g', now=10.1)['visible_count'] == 0
    assert store.snapshot('g', now=10.1)['status'] == 'candidates'
    del snaps['b']
    assert store.snapshot('g', now=10.1)['status'] == 'unknown'


def test_bad_binding_and_duplicate_station(setup):
    store, body, _ = setup
    data = body.model_dump()
    data['members'][1]['reference'] = 'moved'
    with pytest.raises(ValueError):
        store.save('other', GroupBody(**data))
    data['members'][1] = data['members'][0]
    with pytest.raises(ValueError):
        GroupBody(**data)


def test_outside_coverage_and_duplicate_track(setup):
    store, _, snaps = setup
    snaps['b']['tracks'][0]['xyxy'] = [20,30,40,110]
    assert store.snapshot('g', now=10.1)['status'] == 'unknown'
    snaps['b']['tracks'] = snaps['a']['tracks'] * 2
    assert store.snapshot('g', now=10.1)['status'] == 'unknown'


def point(station, x, label='person'):
    return {'station': station, 'ground_m': [x,0], 'label': label}


def test_no_same_camera_or_label_merge():
    assert associate([point('a',0), point('a',0)], .5) == ([], [], [0,1])
    assert associate([point('a',0), point('b',0,'forklift')], .5) == ([], [], [0,1])


def test_many_to_one_and_chain_are_ambiguous():
    for points in ([point('a',0),point('a',.1),point('b',0)],
                   [point('a',0),point('b',.4),point('c',.8)]):
        assert associate(points,.5) == ([], [0,1,2], [])
    assert associate([point('a',0),point('b',.1),point('c',.2)],.5) == ([[0,1,2]],[],[])


def test_routes_all_member_scope_and_roles(setup):
    store, body, snaps = setup
    user = {'role': 'viewer', 'stations': {'a'}}
    app = FastAPI()
    @app.middleware('http')
    async def auth(request, call_next):
        request.state.user = user or None
        return await call_next(request)
    app.include_router(router(store, lambda u: u.get('stations')))
    with TestClient(app) as client:
        assert client.get('/api/cross-camera/groups').json() == []
        assert client.get('/api/cross-camera/groups/g/snapshot').status_code == 403
        assert client.get('/api/cross-camera/groups/g').status_code == 403
        assert client.put('/api/cross-camera/groups/g', json=body.model_dump()).status_code == 403
        user['stations'] = {'a','b'}
        assert len(client.get('/api/cross-camera/groups').json()) == 1
        for snap in snaps.values():
            snap['received_mono'] = time.monotonic()
        assert client.get('/api/cross-camera/groups/g/snapshot').json()['status'] == 'candidates'
        user['role'] = 'admin'
        assert client.put('/api/cross-camera/groups/g', json=body.model_dump()).status_code == 200
        user.clear()
        assert client.get('/api/cross-camera/groups').status_code == 401


def test_scoped_admin_cannot_replace_foreign_group(setup):
    store, body, _ = setup
    app = FastAPI()
    @app.middleware('http')
    async def auth(request, call_next):
        request.state.user = {'role': 'admin'}
        return await call_next(request)
    app.include_router(router(store, lambda _: {'c', 'd'}))
    data = body.model_dump()
    for m, station in zip(data['members'], ['c','d']):
        m['station'] = m['scope_id'] = station
    with TestClient(app) as client:
        assert client.put('/api/cross-camera/groups/g', json=data).status_code == 403
    assert store.get('g') == body


def test_observation_limit(setup):
    store, _, snaps = setup
    for snap in snaps.values():
        template = snap['tracks'][0]
        snap['tracks'] = [{**template, 'track_id': i} for i in range(257)]
    result = store.snapshot('g', now=10.1)
    assert result['status'] == 'unknown' and result['reason'] == 'observation_limit'
    assert result['visible_count'] == 514 and result['candidate_groups'] == []


def test_authorized_snapshot_never_refetches_group(setup, monkeypatch):
    store, body, _ = setup
    def changed_group(*args):
        raise AssertionError('snapshot must use authorized configuration')
    monkeypatch.setattr(store, 'get', changed_group)
    assert store.snapshot('g', now=10.1, body=body)['status'] == 'candidates'


def test_save_authorizes_current_previous_under_lock(setup):
    store, body, _ = setup
    original = store.path.read_bytes()
    def refuse(previous):
        assert store._lock._is_owned()
        assert previous == body
        raise PermissionError('other member scope')
    with pytest.raises(PermissionError):
        store.save('g', body, authorize_previous=refuse)
    assert store.path.read_bytes() == original


def test_delete_authorization_and_persistence(setup):
    store, body, snaps = setup
    original = store.path.read_bytes()
    def deny(previous):
        assert store._lock._is_owned() and previous == body
        raise PermissionError('scope')
    with pytest.raises(PermissionError):
        store.delete('g', authorize=deny)
    assert store.path.read_bytes() == original
    assert store.delete('g', authorize=lambda _: None) == {'deleted':'g'}
    assert CrossCameraStore(store.path, store.calibrations, snaps.get).list() == []
