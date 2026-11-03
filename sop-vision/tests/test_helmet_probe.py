from types import SimpleNamespace
import hashlib
import numpy as np
from visual.helmet_probe import HelmetROIProbe,HelmetProbeCfg
from visual.detector_registry import Detection
from visual.tracking import ObjectTrack


def person(ts=0,tid=1,x=20):return ObjectTrack(tid,'person',(x,20,x+80,220),.9,0,ts,1)


def probe(tmp_path,**kw):
    path=tmp_path/'helmet';path.write_bytes(b'test')
    clock=[0.];made=[]
    class Backend:
        input_size=640
        def __init__(self,*a):
            self.closed=False;self.calls=0;made.append(self)
            self.session=SimpleNamespace(get_modelmeta=lambda:SimpleNamespace(custom_metadata_map={'names':"{0: 'With Helmet', 1: 'Without Helmet'}"}),get_outputs=lambda:[SimpleNamespace(shape=[1,6,8400])])
        def detect(self,frame):
            self.calls+=1
            return [Detection('Without Helmet',(5.,5.,70.,55.),.9)]
        def close(self):self.closed=True
    cfg=HelmetProbeCfg(enabled=True,model=str(path),sha256=hashlib.sha256(b'test').hexdigest(),**kw)
    return HelmetROIProbe(cfg,backend_factory=Backend,clock=lambda:clock[0]),clock,made


def test_lazy_bounded_reuse_remap_and_idle(tmp_path):
    p,clock,made=probe(tmp_path,max_rois=1,idle_s=2)
    frame=np.zeros((300,500,3),np.uint8)
    assert not made
    r=p.process(frame,0,[person(),person(tid=2,x=200)])
    assert len(made)==1 and made[0].calls==1 and r['sampled_person_ids']==[1]
    hit=r['observations'][0]
    assert hit.parent_track_id==1 and hit.last_seen==0 and hit.xyxy==(21.,15.,86.,65.)
    assert p.process(frame,.1,[person(.1)])['status']=='not_sampled'
    r=p.process(frame,.21,[person(.21),person(.21,tid=2,x=200)])
    assert r['sampled_person_ids']==[2] and len(made)==1
    clock[0]=3;p.tick();assert made[0].closed
    p.close();assert p._backend is None


def test_stale_people_ambiguity_and_disabled_do_not_load(tmp_path):
    p,_,made=probe(tmp_path)
    frame=np.zeros((300,500,3),np.uint8)
    assert p.process(frame,1,[person(0)])['status']=='no_person'
    assert p.process(frame,1,[person(1),person(1,tid=2)])['status']=='ambiguous_or_small'
    p.cfg.enabled=False
    assert p.process(frame,1,[person(1)])['status']=='disabled'
    assert not made


def test_hash_failure_backoff_and_no_old_outputs(tmp_path):
    p,clock,made=probe(tmp_path);p.path.write_bytes(b'changed');frame=np.zeros((300,500,3),np.uint8)
    assert p.process(frame,0,[person()])['status']=='unavailable'
    assert p.process(frame,1,[person(1)])['status']=='cooldown'
    assert not made


def test_reset_closes_session_and_timestamp_is_original(tmp_path):
    p,_,made=probe(tmp_path);frame=np.zeros((300,500,3),np.uint8)
    r=p.process(frame,10,[person(10)])
    assert r['frame_ts']==10 and all(t.last_seen==10 for t in r['observations'])
    p.reset_observations();assert made[0].closed
    p.process(frame,0,[person(0)]);assert len(made)==2
    p.close()
