import pytest
from visual.ppe import PPEMonitor,PPECfg
from visual.tracking import ObjectTrack
from visual.dsl import Zone
from visual.perception.base import build_geoms


def t(label,box,ts=0,tid=1,score=.9):return ObjectTrack(tid,label,box,score,0,ts,1)
def person(ts=0,tid=1,box=(0,0,100,200)):return t('person',box,ts,tid)
def monitor(**kw):
    cfg=PPECfg(enabled=True,min_s=.2,min_frames=3,**kw)
    return PPEMonitor(cfg,build_geoms([Zone(id='z',polygon=[[0,0],[1,0],[1,1],[0,1]])],1000,1000))
def gear(m,name):return m.snapshot()['persons'][0]['gear'][name]


def test_positive_association_and_no_absence_inference():
    m=monitor();m.process([person(),t('helmet',(20,0,60,30),tid=2),t('safety vest',(20,50,70,100),tid=3)],0)
    assert gear(m,'helmet')['status']=='observed'
    assert gear(m,'vest')['status']=='observed'
    assert gear(m,'gloves')['status']=='unknown'
    for ts in [.1,.21,.3]:assert not m.process([person(ts)],ts)
    assert gear(m,'helmet')['status']=='unknown'


def test_negative_requires_region_coverage_confidence_and_persistence():
    m=monitor()
    for ts in [0,.1]:assert not m.process([person(ts),t('Without Helmet',(10,0,90,50),ts,2)],ts)
    alert=m.process([person(.21),t('Without Helmet',(10,0,90,50),.21,2)],.21)[0]
    assert alert['kind']=='ppe_missing' and alert['track_ids']==[1]
    assert gear(m,'helmet')['status']=='missing'
    assert not m.process([person(.3),t('Without Helmet',(10,0,90,50),.3,2)],.3)
    m.process([person(.4),t('Without Helmet',(10,0,90,50),.4,2,score=.1)],.4)
    assert gear(m,'helmet')['status']=='unknown'
    m.process([person(.5),t('Without Helmet',(0,0,5,5),.5,2)],.5)
    assert gear(m,'helmet')['status']=='unknown'


def test_ambiguous_people_and_conflicts_never_missing():
    m=monitor()
    for ts in [0,.1,.21]:
        assert not m.process([person(ts),person(ts,3),t('Without Helmet',(10,0,90,50),ts,2)],ts)
    assert all(p['gear']['helmet']['reason']=='ambiguous_person' for p in m.snapshot()['persons'])
    m.reset()
    for ts in [0,.1,.21]:
        assert not m.process([person(ts),t('Without Helmet',(10,0,90,50),ts,2),t('With Helmet',(20,0,60,30),ts,4)],ts)
    assert gear(m,'helmet')['reason']=='conflicting_evidence'


def test_glove_count_is_configurable_and_duplicates_not_two_hands():
    left=t('gloves',(0,100,10,110),tid=2)
    m=monitor(gloves_required=1);m.process([person(),left],0)
    assert gear(m,'gloves')['status']=='observed'
    m=monitor(gloves_required=2);m.process([person(),left],0)
    assert gear(m,'gloves')['status']=='unknown'
    duplicate=t('gloves',(0,100,10,110),tid=3)
    m.reset();m.process([person(),left,duplicate],0)
    assert gear(m,'gloves')['status']=='unknown'
    right=t('gloves',(80,100,90,110),tid=4)
    m.reset();m.process([person(),left,right],0)
    assert gear(m,'gloves')['status']=='observed'


def test_stale_gap_reset_duplicate_and_small_people():
    m=monitor();negative=t('Without Helmet',(10,0,90,50),tid=2)
    m.process([person(),negative],0)
    for _ in range(3):assert not m.process([person(),negative],0)
    m.process([person(.1),negative],.1)
    assert gear(m,'helmet')['status']=='unknown'
    m.reset();assert m.snapshot()['status']=='unknown' and not m.snapshot()['persons']
    m.process([person(2,box=(0,0,20,40))],2)
    assert gear(m,'helmet')['reason']=='person_too_small'
    m.process([],2.1,available=False)
    assert m.snapshot()['status']=='unknown'


def test_glove_config_and_unknown_zone_rejected():
    with pytest.raises(ValueError):PPECfg(gloves_required=3)
    with pytest.raises(ValueError):PPEMonitor(PPECfg(zones=['missing']),{})


def test_roi_evidence_cannot_move_to_another_parent():
    from visual.helmet_probe import HelmetObservation
    m=monitor()
    for ts in [0,.1,.21]:
        observation=HelmetObservation(-1,'Without Helmet',(10,0,90,50),.9,ts,ts,1,parent_track_id=99)
        assert not m.process([person(ts),observation],ts)
    assert gear(m,'helmet')['status']=='unknown'


def test_new_model_positive_names_and_per_person_regions():
    m=monitor()
    hits=[t('hardhat',(20,0,60,30),tid=2),t('goggles',(25,10,70,30),tid=3),
          t('safety shoes',(10,170,30,195),tid=4),t('safety shoes',(65,170,85,195),tid=5)]
    m.process([person(),*hits,person(tid=9,box=(200,0,300,200))],0)
    assert gear(m,'helmet')['status']=='observed'
    assert gear(m,'goggles')['status']=='observed'
    assert gear(m,'shoes')['status']=='observed'
    other=m.snapshot()['persons'][1]['gear']
    assert other['goggles']['status']==other['shoes']['status']=='unknown'
    assert m.snapshot()['shoes_required']==2


@pytest.mark.parametrize('label,expected,box',[
    ('no-hardhat','helmet',(10,0,90,50)),
    ('no-safety vest','vest',(20,50,80,130)),
    ('no-gloves','gloves',(0,100,10,115)),
    ('no-goggles','goggles',(25,8,75,32)),
    ('no-shoes','shoes',(10,160,40,190)),
    ('no-safety-shoes','shoes',(10,160,40,190)),
])
def test_explicit_negative_names_have_persistent_person_bound_evidence(label,expected,box):
    m=monitor()
    for ts in (0,.1):
        assert not m.process([person(ts),t(label,box,ts,2)],ts)
        assert gear(m,expected)['status']=='pending'
    alerts=m.process([person(.21),t(label,box,.21,2)],.21)
    assert len(alerts)==1 and alerts[0]['gear']==expected
    assert alerts[0]['track_ids']==[1] and alerts[0]['evidence_track_ids']==[2]
    assert gear(m,expected)['status']=='missing'
    assert not m.process([person(.3),t(label,box,.3,2)],.3)


@pytest.mark.parametrize('label,expected,box',[
    ('no-goggles','goggles',(20,10,24,14)), # fragment, not a visible eye region
    ('no-gloves','gloves',(20,100,24,104)),
    ('no-shoes','shoes',(20,180,24,184)),
    ('goggles','goggles',(20,160,60,180)), # footwear region cannot prove glasses
    ('safety shoes','shoes',(20,10,60,30)),
    ('no-goggles','goggles',(0,50,100,150)),
])
def test_new_gear_rejects_tiny_or_wrong_region_boxes(label,expected,box):
    m=monitor()
    for ts in (0,.1,.21): assert not m.process([person(ts),t(label,box,ts,2)],ts)
    assert gear(m,expected)['status']=='unknown'


def test_shoes_pair_count_and_duplicates_never_imply_missing():
    m=monitor();left=t('safety_shoe',(10,170,30,195),tid=2)
    for ts in (0,.1,.21):
        assert not m.process([person(ts),t(left.label,left.xyxy,ts,2),t(left.label,left.xyxy,ts,3)],ts)
        assert gear(m,'shoes')['status']=='unknown'
    m=monitor(shoes_required=1);m.process([person(),left],0)
    assert gear(m,'shoes')['status']=='observed'
    for ts in (.1,.21,.3): assert not m.process([person(ts)],ts)
    assert gear(m,'shoes')['status']==gear(m,'goggles')['status']=='unknown'


@pytest.mark.parametrize('gear_name,positive,negative,positive_box,negative_box',[
    ('goggles','goggles','no-goggles',(25,10,70,30),(25,8,75,32)),
    ('shoes','safety shoes','no-shoes',(10,170,30,195),(10,160,40,190)),
    ('gloves','gloves','no-gloves',(0,100,10,115),(0,100,10,115)),
])
def test_positive_negative_conflict_stays_unknown(gear_name,positive,negative,positive_box,negative_box):
    m=monitor()
    for ts in (0,.1,.21):
        assert not m.process([person(ts),t(positive,positive_box,ts,2),t(negative,negative_box,ts,3)],ts)
    assert gear(m,gear_name)['reason']=='conflicting_evidence'


def test_new_gear_ambiguity_staleness_and_gap_never_accumulate_missing():
    m=monitor()
    for ts in (0,.1,.21):
        assert not m.process([person(ts),person(ts,3),t('no-goggles',(25,8,75,32),ts,2)],ts)
    assert all(p['gear']['goggles']['reason']=='ambiguous_person' for p in m.snapshot()['persons'])
    m.reset()
    m.process([person(),t('no-goggles',(25,8,75,32),tid=2)],0)
    stale=t('no-goggles',(25,8,75,32),tid=2)
    for ts in (.1,.21): assert not m.process([person(ts),stale],ts)
    assert gear(m,'goggles')['status']=='unknown'
    assert not m.process([person(2),t('no-goggles',(25,8,75,32),2,2)],2)
    assert gear(m,'goggles')['status']=='pending'
    m.process([],2.1,available=False)
    assert m.snapshot()['status']=='unknown' and not m.snapshot()['persons']


def test_plain_shoes_boots_and_esd_names_are_not_protective_or_electrical_evidence():
    m=monitor()
    for ts in (0,.1,.21):
        assert not m.process([person(ts),*[t(label,(10,170,40,195),ts,i+2)
            for i,label in enumerate(['shoes','shoe','boots','ESD shoes','ESD gloves'])]],ts)
    assert gear(m,'shoes')['status']==gear(m,'gloves')['status']=='unknown'
    with pytest.raises(ValueError):PPECfg(shoes_required=3)
    assert not PPECfg().enabled and not PPECfg().helmet_probe.enabled


def test_runtime_fresh_mot_tracks_reach_new_gear_alert_and_state(tmp_path):
    """Real runtime hook/MOT/state, deterministic detector fixture (not accuracy data)."""
    import numpy as np
    from visual.dsl import StationConfig
    from visual.runtime import StationRuntime
    from visual.tracking import ClassAwareIoUTracker
    from visual.detector_registry import Detection
    class FixtureDetector:
        class_names=['person','no-goggles','safety shoes']
        def __init__(self):self.mot=ClassAwareIoUTracker()
        def process(self,frame,ts,ctx):
            hits=[Detection('person',(0,0,100,190),.9),Detection('no-goggles',(25,8,75,32),.9),
                  Detection('safety shoes',(10,165,30,185),.9),Detection('safety shoes',(65,165,85,185),.9)]
            ctx.object_tracks.extend(self.mot.update(hits,ts));return []
        def reset_observations(self):self.mot.reset()
        close=reset_observations
    cfg=StationConfig.model_validate({'station':'ppe-gear-fixture','source':{'type':'sim','width':100,'height':200},
        'zones':[{'id':'z','polygon':[[0,0],[1,0],[1,1],[0,1]]}],'hands':{'enabled':False},
        'quality':{'enabled':False},'ppe':{'enabled':True,'min_s':.2,'min_frames':3}})
    runtime=StationRuntime(cfg,tmp_path)
    runtime.hands=None;runtime.detector=FixtureDetector()
    try:
        for ts in (0,.1,.21):runtime.process_frame(ts,np.full((200,100,3),128,np.uint8))
        state=runtime.state()['ppe']['persons'][0]['gear']
        assert state['goggles']['status']=='missing' and state['shoes']['status']=='observed'
        assert len(runtime.alerts)==1 and runtime.alerts[0]['gear']=='goggles'
    finally:runtime.close()
