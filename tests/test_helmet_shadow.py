import threading,time
import numpy as np
from visual.helmet_shadow import HelmetShadow,HelmetSampleMonitor
from visual.helmet_probe import HelmetProbeCfg,HelmetObservation
from visual.tracking import ObjectTrack
from visual.ppe import PPECfg
from visual.dsl import Zone
from visual.perception.base import build_geoms


def person(ts,tid=1):return ObjectTrack(tid,'person',(0,0,100,200),.9,ts,ts,1)
def frame():return np.zeros((240,320,3),np.uint8)
def await_condition(fn):
    deadline=time.monotonic()+2
    while not fn() and time.monotonic()<deadline:time.sleep(.005)
    assert fn()


class Probe:
    def __init__(self,*a):self.calls=[];self.closed=False
    def reset_observations(self):pass
    def process(self,image,ts,people):
        self.calls.append(ts)
        return {'status':'sampled','frame_ts':ts,'sampled_person_ids':[p.track_id for p in people],
                'observations':[HelmetObservation(-1,'Without Helmet',(10,0,90,50),.9,ts,ts,1,parent_track_id=people[0].track_id)]}
    def close(self):self.closed=True


def test_latest_one_nonblocking_and_idle_release():
    started=threading.Event();release=threading.Event();made=[]
    class Blocking(Probe):
        def process(self,*args):
            if not self.calls:started.set();assert release.wait(2)
            return super().process(*args)
    def factory(*args):p=Blocking();made.append(p);return p
    s=HelmetShadow(HelmetProbeCfg(enabled=True),factory=factory,clock=lambda:0,idle_s=.05,ttl_s=1)
    assert s.submit(frame(),0,[person(0)],1);assert started.wait(1)
    assert s.submit(frame(),.25,[person(.25)],1)
    assert s.submit(frame(),.5,[person(.5)],1)
    assert s.dropped==1 and s._pending.ts==.5
    release.set();await_condition(lambda:s._result is not None and s._result[0].ts==.5)
    r=s.drain(1,.5,[person(.5)])
    assert r['frame_ts']==.5 and r['observations'][0].last_seen==.5
    assert s.drain(1,.5,[person(.5)]) is None
    await_condition(lambda:s._thread is None)
    assert made[0].closed and made[0].calls==[0,.5]
    assert s.close()


def test_epoch_id_and_ttl_invalidations():
    for change in ['epoch','person','ttl','invalidate']:
        now=[0.];s=HelmetShadow(HelmetProbeCfg(enabled=True),factory=Probe,clock=lambda:now[0],idle_s=.05)
        s.submit(frame(),0,[person(0)],1);await_condition(lambda:s._result is not None)
        if change=='ttl':now[0]=1
        if change=='invalidate':s.invalidate()
        result=s.drain(2 if change=='epoch' else 1,.1,[person(.1,2 if change=='person' else 1)])
        assert result is None;s.close()


def test_disabled_never_starts():
    s=HelmetShadow(HelmetProbeCfg())
    assert not s.submit(frame(),0,[person(0)],1) and s._thread is None
    assert s.close()


def test_sample_monitor_counts_only_actual_samples_and_deduplicates():
    now=[0.];g=build_geoms([Zone(id='z',polygon=[[0,0],[1,0],[1,1],[0,1]])],320,240)
    m=HelmetSampleMonitor(PPECfg(enabled=True,min_s=.4,min_frames=3),g,clock=lambda:now[0])
    def result(ts,tid=1):
        return {'epoch':1,'frame_ts':ts,'captured_mono':ts,'status':'sampled','person_tracks':[person(ts,tid)],
                'observations':[HelmetObservation(-tid,'Without Helmet',(10,0,90,50),.9,ts,ts,1,parent_track_id=tid)]}
    r=result(0);assert not m.update(r,1,0,[person(0)])
    for ts in [.05,.1]:
        now[0]=ts;assert not m.update(None,1,ts,[person(ts)])
    now[0]=.25;r=result(.25);assert not m.update(r,1,.25,[person(.25)])
    assert not m.update(r,1,.25,[person(.25)])
    now[0]=.5;assert len(m.update(result(.5),1,.5,[person(.5)]))==1
    now[0]=1.1;assert not m.update(None,1,1.1,[person(1.1)])
    assert m.snapshot()['sampled_persons']==[]
    m.reset();assert m.snapshot()['epoch'] is None

def test_failed_prediction_reports_unavailable_and_cleanup_failure_is_visible():
    class Broken(Probe):
        def process(self,*args):raise RuntimeError('inference failure')
        def close(self):raise RuntimeError('cleanup failure')
    s=HelmetShadow(HelmetProbeCfg(enabled=True),factory=Broken,idle_s=.05)
    s.submit(frame(),0,[person(0)],1)
    await_condition(lambda:s._result is not None)
    r=s.drain(1,.1,[person(.1)])
    assert r['status']=='unavailable' and r['observations']==[]
    await_condition(lambda:s._thread is None)
    assert s.close_error and not s.close()


def test_sample_monitor_drops_wall_expired_evidence_with_slow_frame_clock():
    now=[0.];g=build_geoms([Zone(id='z',polygon=[[0,0],[1,0],[1,1],[0,1]])],320,240)
    m=HelmetSampleMonitor(PPECfg(enabled=True),g,clock=lambda:now[0])
    r={'epoch':1,'frame_ts':0,'captured_mono':0,'status':'sampled',
       'person_tracks':[person(0)],'observations':[]}
    m.update(r,1,0,[person(0)])
    assert len(m.snapshot()['sampled_persons'])==1
    now[0]=1
    assert m.snapshot()['sampled_persons']==[]
    m.update(None,1,.01,[person(.01)])
    assert m._people=={}
