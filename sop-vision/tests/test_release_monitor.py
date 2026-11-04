import hashlib
from types import SimpleNamespace
import pytest
from visual.model_release import ModelReleases
from visual.release_monitor import ReleaseMonitor

@pytest.fixture
def rig(tmp_path):
    ledger=ModelReleases(tmp_path/'releases.sqlite');reports={};clock=[100.]
    def create(name):
        path=tmp_path/(name+'.onnx');path.write_bytes(name.encode());sha=hashlib.sha256(path.read_bytes()).hexdigest()
        record=ledger.create(engine_id=name,model_sha256=sha,evaluation_id='HE-'+name,adapter='yolo_raw',classes=['part'],actor='human',model_path=str(path))
        reports['HE-'+name]={'kind':'detection_validation','publication':'not_requested','engine_id':name,'model_sha256':sha,'adapter':'yolo_raw','evaluation':{'classes':{'part':{}}}}
        ledger.approve(record['id'],'admin');return ledger.activate(record['id'],'station','admin')
    old=create('old');new=create('new');db=tmp_path/'monitor.sqlite'
    monitor=ReleaseMonitor(db,ledger,SimpleNamespace(get=lambda k:reports[k]),clock=lambda:clock[0])
    yield SimpleNamespace(m=monitor,ledger=ledger,old=old,new=new,clock=clock,reports=reports,db=db)
    monitor.close();ledger.close()


def arm(r,enabled=True):
    return r.m.arm('station',r.new['id'],dict(enabled=enabled,window_s=5,min_samples=3,max_frame_age_s=1,max_error_delta=2),actor='admin')

def sample(r,n,*,bad=False,release=None,epoch='epoch'):
    r.clock[0]=100+n
    return r.m.record('station',runtime_release_id=release or r.new['id'],epoch=epoch,running=True,error_total=n if bad else 0,last_frame_at=r.clock[0])

def test_default_disabled_and_wrong_runtime(rig):
    r=rig;arm(r,False);assert sample(r,10,bad=True)['status']=='disabled'
    arm(r);assert sample(r,11,bad=True,release=r.old['id'])['samples']==[]


def test_actual_ledger_rollback_once_pending_ack(rig):
    r=rig;arm(r)
    for n in range(6):result=sample(r,n,bad=True)
    assert result['status']=='pending_runtime_reload' and not result['runtime_applied']
    assert r.ledger.active_for('station')['id']==r.old['id']
    rev=r.ledger.get(r.new['id'])['revision'];sample(r,20,bad=True)
    assert r.ledger.get(r.new['id'])['revision']==rev
    with pytest.raises(ValueError):r.m.runtime_applied('station',r.new['id'])
    assert r.m.runtime_applied('station',r.old['id'])['status']=='rolled_back'


def test_hash_corruption_blocks_rollback(rig):
    r=rig;arm(r)
    from pathlib import Path
    Path(r.old['model_path']).write_bytes(b'changed')
    for n in range(6):result=sample(r,n,bad=True)
    assert result['status']=='blocked' and r.ledger.active_for('station')['id']==r.new['id']


def test_restart_clears_window_and_epoch(rig):
    r=rig;arm(r)
    for n in range(4):sample(r,n,bad=True)
    r.m.close();r.m=ReleaseMonitor(r.db,r.ledger,SimpleNamespace(get=lambda k:r.reports[k]),clock=lambda:r.clock[0])
    # Fixture closes its original monitor; sqlite close is idempotent.
    try:
        assert r.m.get('station')['samples']==[]
        assert sample(r,5,bad=True)['status']=='monitoring'
        assert len(sample(r,6,bad=True,epoch='new-epoch')['samples'])==1
    finally:r.m.close()


def test_missing_frames_needs_entire_bad_window(rig):
    r=rig;arm(r)
    for n in range(10):
        r.clock[0]=100+n
        state=r.m.record('station',runtime_release_id=r.new['id'],epoch='x',running=True,error_total=0,last_frame_at=None)
    assert state['reason']=='frame_availability' and state['status']=='pending_runtime_reload'


def test_healthy_and_duplicate_samples_do_not_trigger(rig):
    r=rig;arm(r)
    for n in range(20):
        state=sample(r,n)
        before=len(state['samples']);assert len(sample(r,n,bad=True)['samples'])==before
    assert state['status']=='monitoring' and r.ledger.active_for('station')['id']==r.new['id']


def test_missing_evaluation_blocks(rig):
    r=rig;arm(r);del r.reports[r.old['evaluation_id']]
    for n in range(6):state=sample(r,n,bad=True)
    assert state['status']=='blocked'
    with pytest.raises(ValueError):arm(r)


def test_pending_survives_restart_without_second_rollback(rig):
    r=rig;arm(r)
    for n in range(6):sample(r,n,bad=True)
    revision=r.ledger.get(r.new['id'])['revision']
    r.m.close();replacement=ReleaseMonitor(r.db,r.ledger,SimpleNamespace(get=lambda k:r.reports[k]),clock=lambda:r.clock[0])
    try:
        assert replacement.get('station')['status']=='pending_runtime_reload'
        assert replacement.record('station',runtime_release_id=r.new['id'],epoch='e',running=False,error_total=100,last_frame_at=None)['status']=='pending_runtime_reload'
        assert r.ledger.get(r.new['id'])['revision']==revision
    finally:replacement.close()


def test_active_scenes_empty_disabled_and_monitoring(rig):
    r=rig
    assert r.m.active_scenes()==[]
    arm(r,False);assert r.m.active_scenes()==[]
    arm(r);assert r.m.active_scenes()==['station']
    for n in range(6):sample(r,n,bad=True)
    assert r.m.active_scenes()==['station']
    r.m.runtime_applied('station',r.old['id']);assert r.m.active_scenes()==[]
