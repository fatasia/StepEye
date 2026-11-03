import numpy as np
import pytest
from visual.ehs import EhsCfg,EhsMonitor
from visual.dsl import Zone,StationConfig
from visual.perception.base import build_geoms
from visual.tracking import ObjectTrack


def track(ts,tid=1,box=(10,10,20,20)):
    return ObjectTrack(tid,'box',box,.9,0,ts,1)


def monitor(kind='object_count',**limits):
    cfg=EhsCfg(enabled=True,rules=[dict(id='stock',kind=kind,zone='z',labels=['box'],min_s=.2,min_frames=3,**limits)])
    return EhsMonitor(cfg,build_geoms([Zone(id='z',polygon=[[0,0],[1,0],[1,1],[0,1]])],100,100))


def run(m,times,objects):
    alerts=[]
    for ts in times:alerts+=m.process(objects(ts),ts,available_labels={'box'})
    return alerts


def test_lower_zero_continuous_confirm_and_rearm():
    m=monitor(min_count=1)
    assert not run(m,[0,.1,.21,.31],lambda t:[])
    alerts=run(m,[.42],lambda t:[])
    assert len(alerts)==1 and alerts[0]['count']==0 and alerts[0]['reason']=='below'
    assert not run(m,[.5],lambda t:[])
    run(m,[.6,.7,.81],lambda t:[track(t)])
    assert m.snapshot()['rules']['stock']['status']=='clear'
    assert len(run(m,[.9,1,1.11],lambda t:[]))==1


def test_upper_dedup_not_accumulated_inventory():
    m=monitor(max_count=1)
    alerts=run(m,[0,.1,.21,.31,.42],lambda t:[track(t),track(t),track(t,2)])
    assert len(alerts)==1 and alerts[0]['count']==2 and alerts[0]['track_ids']==[1,2]
    run(m,[.5],lambda t:[])
    snap=m.snapshot()['rules']['stock']
    assert snap['count']==0 and snap['scope']=='single_camera_visible_snapshot'


def test_unstable_stale_and_unsupported_unknown():
    m=monitor(min_count=1)
    for i,t in enumerate([0,.1,.21,.31,.42]):
        assert not m.process([track(t,i)],t,available_labels={'box'})
        assert m.snapshot()['rules']['stock']['count'] is None
    m.process([],.5,available_labels={'person'})
    assert m.snapshot()['rules']['stock']['status']=='unknown'
    assert not run(m,[.6,.7,.81],lambda t:[track(.42)])
    assert not m.process([],.82,available_labels=None)
    assert m.snapshot()['rules']['stock']['status']=='unknown'


def test_duplicate_gap_reset_and_disabled():
    m=monitor(max_count=0)
    assert not run(m,[0]*10,lambda t:[track(t)])
    assert m.snapshot()['rules']['stock']['count'] is None
    assert not run(m,[1,1.1,1.21],lambda t:[track(t)])
    m.reset()
    assert m.snapshot()['rules']['stock']['status']=='unknown'
    m.cfg.enabled=False
    assert not run(m,[1.3,1.4,1.51,1.61,1.72],lambda t:[track(t)])
    assert m.snapshot()['rules']['stock']['status']=='unknown'


def test_aisle_center_and_persistence():
    m=monitor('aisle_occupied')
    assert not run(m,[0,.1,.21],lambda t:[track(t,box=(110,10,130,30))])
    alerts=run(m,[.3,.4,.51],lambda t:[track(t)])
    assert len(alerts)==1 and alerts[0]['kind']=='aisle_occupied'
    assert not run(m,[.6],lambda t:[track(t)])
    run(m,[.7],lambda t:[])
    assert m.snapshot()['rules']['stock']['status']=='clear'


@pytest.mark.parametrize('limits',[{},dict(min_count=3,max_count=1),dict(max_count=-1),dict(min_count=True)])
def test_invalid_limits(limits):
    with pytest.raises(ValueError):monitor(**limits)


def test_runtime_existing_listener_and_reset(tmp_path):
    from visual.runtime import StationRuntime
    cfg=StationConfig(station='warehouse',zones=[Zone(id='z',polygon=[[0,0],[1,0],[1,1],[0,1]])],
        source=dict(type='sim',width=100,height=100),hands=dict(enabled=False),quality=dict(enabled=False),
        detector=dict(classes=['box']),ehs=dict(enabled=True,rules=[dict(id='stock',kind='object_count',
            zone='z',labels=['box'],max_count=0,min_s=.2,min_frames=3)]))
    rt=StationRuntime(cfg,tmp_path)
    class Detector:
        mot=True
        class_names=['box']
        def process(self,frame,ts,ctx):ctx.object_tracks.append(track(ts));return []
        def reset_observations(self):pass
        def close(self):pass
    rt.detector=Detector();seen=[];rt.listeners.append(lambda k,p:seen.append((k,p)))
    try:
        for ts in [0,.1,.21,.31,.42]:rt.process_frame(ts,np.full((100,100,3),100,np.uint8))
        alerts=[p for k,p in seen if k=='safety' and p.get('kind')=='object_count']
        assert len(alerts)==1 and alerts[0]['count']==1
        rt._reset_visual_observations()
        assert rt.state()['ehs']['rules']['stock']['status']=='unknown'
    finally:rt.close()
