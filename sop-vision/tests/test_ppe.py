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
