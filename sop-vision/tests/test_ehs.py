import pytest
from visual.ehs import EhsCfg, EhsMonitor
from visual.tracking import ObjectTrack
from visual.dsl import Zone, StationConfig
from visual.perception.base import build_geoms


def monitor(kind='intrusion'):
    cfg=EhsCfg(enabled=True,rules=[dict(id='r',kind=kind,zone='z',labels=['person' if kind=='intrusion' else 'fire'],min_s=.2,min_frames=3)])
    return EhsMonitor(cfg,build_geoms([Zone(id='z',polygon=[[0,0],[1,0],[1,1],[0,1]])],100,100))


def track(ts,tid=1,label='person',box=(10,10,20,20)):
    return ObjectTrack(tid,label,box,.9,0,ts,1)


def test_intrusion_holds_same_id_and_one_alert_until_clear():
    m=monitor()
    assert not m.process([track(0)],0)
    assert not m.process([track(.1)],.1)
    assert len(m.process([track(.21)],.21))==1
    assert not m.process([track(.3)],.3)
    m.process([],.4)
    assert m.snapshot()['rules']['r']['status']=='clear'
    for ts in [.5,.6]: assert not m.process([track(ts)],ts)
    assert len(m.process([track(.71)],.71))==1


def test_unknown_and_gaps_clear_evidence():
    m=monitor()
    m.process([track(0)],0);m.process([track(.1)],.1)
    assert not m.process([track(1)],1)
    assert not m.process([track(1.1)],1.1)
    m.process([],1.2,available=False)
    assert m.snapshot()['rules']['r']=={'status':'unknown','track_ids':[]}
    assert not m.process([track(1.3)],1.3)


def test_stale_duplicate_and_changed_identity_do_not_accumulate():
    m=monitor()
    m.process([track(0)],0)
    for _ in range(4): assert not m.process([track(0)],0)
    m.process([track(.1,2)],.1)
    assert not m.process([track(.21,2)],.21)
    assert not m.process([track(.21,2)],.3)
    assert m.snapshot()['rules']['r']['track_ids']==[]


def test_fire_persistence_reports_area_trend_not_physical_growth():
    m=monitor('fire_smoke')
    for ts in [0,.1]: assert not m.process([track(ts,label='fire')],ts)
    alert=m.process([track(.21,label='fire',box=(10,10,30,20))],.21)[0]
    assert alert['kind']=='fire_smoke' and alert['area_change_ratio']==1


def test_config_rejects_unknown_zone_and_duplicate_id():
    with pytest.raises(ValueError): EhsCfg(rules=[dict(id='a',kind='intrusion',zone='z')]*2)
    with pytest.raises(ValueError): StationConfig(station='a',zones=[],ehs=dict(enabled=True,rules=[dict(id='a',kind='intrusion',zone='z')]))


def test_runtime_routes_ehs_to_existing_safety_listener(tmp_path):
    import numpy as np
    from visual.runtime import StationRuntime
    cfg=StationConfig(station='ehs',zones=[Zone(id='z',polygon=[[0,0],[1,0],[1,1],[0,1]])],
        source=dict(type='sim',width=100,height=100),hands=dict(enabled=False),quality=dict(enabled=False),
        ehs=dict(enabled=True,rules=[dict(id='r',kind='intrusion',zone='z',min_s=.2,min_frames=3)]))
    rt=StationRuntime(cfg,tmp_path)
    class Detector:
        mot=True
        class_names=["person"]
        def process(self,frame,ts,ctx):ctx.object_tracks.append(track(ts));return []
        def reset_observations(self):pass
        def close(self):pass
    rt.detector=Detector();seen=[];rt.listeners.append(lambda kind,payload:seen.append((kind,payload)))
    try:
        for ts in [0,.1,.21]:rt.process_frame(ts,np.full((100,100,3),100,np.uint8))
        assert any(k=='safety' and p['rule']=='r' for k,p in seen)
        assert rt.alerts[0]['source']=='ehs_tracks'
        rt._reset_visual_observations()
        assert rt.state()['ehs']['rules']['r']['status']=='unknown'
    finally:rt.close()


def test_unavailable_label_is_unknown_not_clear():
    m=monitor('fire_smoke')
    assert not m.process([],0,available_labels={'person'})
    assert m.snapshot()['rules']['r']['status']=='unknown'
