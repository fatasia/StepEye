import threading,time
import numpy as np
import pytest
from visual.dsl import StationConfig,Zone
from visual.runtime import StationRuntime
from visual.helmet_shadow import HelmetShadow
from visual.helmet_probe import HelmetObservation
from visual.tracking import ObjectTrack


def wait_for(fn):
    until=time.monotonic()+2
    while not fn() and time.monotonic()<until:time.sleep(.005)
    assert fn()


class Probe:
    def __init__(self,*args):self.closed=False
    def reset_observations(self):pass
    def process(self,frame,ts,people):
        return {'status':'sampled','frame_ts':ts,'sampled_person_ids':[1],
                'observations':[HelmetObservation(-1,'Without Helmet',(10,0,90,50),.9,ts,ts,1,parent_track_id=1)]}
    def close(self):self.closed=True


class Detector:
    mot=True
    class_names=['person']
    def process(self,frame,ts,ctx):
        ctx.object_tracks.append(ObjectTrack(1,'person',(0,0,100,200),.9,ts,ts,1));return []
    def reset_observations(self):pass
    def close(self):pass


def build(tmp_path,monkeypatch,enabled=True,factory=Probe):
    from visual import runtime as module
    monkeypatch.setattr(module,'HelmetShadow',lambda cfg,base,**kw:HelmetShadow(cfg,base,factory=factory,**kw))
    cfg=StationConfig(station='helmet',zones=[Zone(id='z',polygon=[[0,0],[1,0],[1,1],[0,1]])],
        source=dict(type='sim',width=320,height=240,realtime=False),hands=dict(enabled=False),quality=dict(enabled=False),
        ppe=dict(enabled=True,min_s=.4,min_frames=3,helmet_probe=dict(enabled=enabled)))
    rt=StationRuntime(cfg,tmp_path);rt.detector=Detector();return rt


def test_runtime_real_queue_emits_safety_and_never_injects_old_context(tmp_path,monkeypatch):
    rt=build(tmp_path,monkeypatch);seen=[];rt.listeners.append(lambda k,p:seen.append((k,p)))
    frame=np.full((240,320,3),100,np.uint8)
    try:
        for ts in [0,.25,.5]:
            rt.process_frame(ts,frame)
            wait_for(lambda:rt.helmet_shadow._result is not None)
            rt.process_frame(ts+.01,frame)
            assert all(t.label=='person' for t in rt.ctx.object_tracks)
        alerts=[p for k,p in seen if k=='safety' and p.get('kind')=='ppe_missing']
        assert len(alerts)==1 and alerts[0]['ts']==.5 and alerts[0]['track_ids']==[1]
        assert rt.state()['helmet_probe']['frame_ts']==.5
        rt.stop();assert rt.state()['helmet_probe']['close_complete'] is True
    finally:rt.close()


def test_default_disabled_and_eof_closes(tmp_path,monkeypatch):
    rt=build(tmp_path,monkeypatch,enabled=False)
    assert rt.helmet_shadow is None and rt.state()['helmet_probe']['status']=='disabled'
    rt.close()
    rt=build(tmp_path/'enabled',monkeypatch)
    source=iter([(0,np.full((240,320,3),100,np.uint8)),None])
    rt.source.read=lambda:next(source)
    rt.run()
    assert rt.state()['helmet_probe']['status']=='closed'
    rt.close()


def test_quality_reset_discards_inflight_and_close_reports_pending(tmp_path,monkeypatch):
    started=threading.Event();release=threading.Event();made=[]
    class Blocked(Probe):
        def __init__(self,*args):super().__init__();made.append(self)
        def process(self,*args):started.set();assert release.wait(3);return super().process(*args)
    rt=build(tmp_path,monkeypatch,factory=Blocked)
    try:
        rt.process_frame(0,np.full((240,320,3),100,np.uint8));assert started.wait(1)
        rt.cfg.quality.enabled=True
        monkeypatch.setattr(rt.quality,'snapshot',lambda:{'observation':'bad','raw_reason':'test'})
        blocked,_=rt._quality_gate(.01)
        assert blocked and rt._helmet_candidate['status']=='unknown'
        assert rt.helmet_samples.snapshot()['sampled_persons']==[]
        rt.stop()
        assert rt._helmet_candidate['status']=='closing' and rt._helmet_candidate['close_complete'] is False
        release.set();wait_for(lambda:rt.helmet_shadow._thread is None)
        assert made[0].closed and rt.helmet_shadow._result is None
        rt.stop();assert rt._helmet_candidate['close_complete'] is True
    finally:release.set();rt.close()
