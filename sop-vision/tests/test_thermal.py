import pytest
from visual.events import Event,SIGNAL
from visual.thermal import ThermalCfg,ThermalMonitor,ThermalRule
from visual.fusion import SignalAuditStore,SignalInbox


def monitor():
    return ThermalMonitor(ThermalCfg(enabled=True,rules=[
        ThermalRule(id='hot',source='thermal-cam',name='roi1',kind='temperature',threshold=60,min_s=.2,min_samples=3),
        ThermalRule(id='rise',source='thermal-cam',name='roi1',kind='rise_rate',threshold=5,min_s=.2,min_samples=3),
        ThermalRule(id='area',source='thermal-cam',name='roi1',kind='hotspot_area',threshold=.2,
                    hotspot_above_c=60,min_s=.2,min_samples=3)]))


def signal(ts,seq,temp=70,**overrides):
    data={'sequence':seq,'observed_at':1000+ts,'valid_until_ts':ts+1,'quality':'ok'}
    value={'temperature':temp,'temperature_unit':'C','hotspot_area':.3,'area_unit':'ratio','hotspot_above_c':60}
    value.update(overrides)
    return Event(ts,SIGNAL,name='roi1',source='thermal-cam',value=value,data=data)


def test_temperature_rate_area_confirm_and_dedupe():
    m=monitor();alerts=[]
    for i,ts in enumerate([0,.1,.2,.3,.4,.5]):
        alerts+=m.process([signal(ts,i,70+ts*10)],ts)
    assert {a['kind'] for a in alerts}=={'thermal_temperature','thermal_rise_rate','thermal_hotspot_area'}
    assert len(alerts)==3
    rise=next(a for a in alerts if a['kind']=='thermal_rise_rate')
    assert rise['unit']=='C/s' and rise['value']==pytest.approx(10)
    assert rise['device_source']=='thermal-cam'
    assert not m.process([],1.6)
    assert all(v['status']=='unknown' for v in m.snapshot()['rules'].values())


@pytest.mark.parametrize('kind',['unit','nan','quality','sequence','expiry','backward','jump','area_unit','area_threshold'])
def test_invalid_samples_never_confirm(kind):
    m=monitor();m.process([signal(0,0)],0);m.process([signal(.1,1)],.1)
    event=signal(.2,2)
    if kind=='unit':event.value['temperature_unit']='F'
    elif kind=='nan':event.value['temperature']=float('nan')
    elif kind=='quality':event.data['quality']='uncertain'
    elif kind=='sequence':event.data['sequence']=1
    elif kind=='expiry':event.data['valid_until_ts']=.1
    elif kind=='backward':event.data['observed_at']=999
    elif kind=='jump':event.value['temperature']=300
    elif kind=='area_unit':event.value['area_unit']='m2'
    else:event.value['hotspot_above_c']=50
    alerts=m.process([event],.2)
    if kind.startswith('area_'):
        assert m.snapshot()['rules']['area']['status']=='unknown'
        assert all(a['kind']!='thermal_hotspot_area' for a in alerts)
    else:
        assert not alerts and all(v['status']=='unknown' for v in m.snapshot()['rules'].values())


def test_source_and_frame_clock_guards():
    m=monitor();event=signal(0,0);event.source='other'
    assert not m.process([event],0)
    assert m.snapshot()['rules']['hot']['status']=='unknown'
    m.process([signal(.1,1)],.1)
    m.process([],0)
    assert m.snapshot()['rules']['hot']['reason']=='invalid_frame_time'


def test_same_frame_batch_uses_device_observation_clock():
    m=monitor();events=[]
    for i in range(4):
        e=signal(i*.1,i,70+i)
        e.ts=1
        e.data['valid_until_ts']=2
        events.append(e)
    alerts=m.process(events,1)
    assert any(a['kind']=='thermal_rise_rate' and a['value']==pytest.approx(10) for a in alerts)


def test_real_signal_inbox_contract(tmp_path):
    now=[1000.]
    store=SignalAuditStore(tmp_path/'signals.db')
    inbox=SignalInbox(store,'station',clock=lambda:now[0])
    inbox.bind_consumer();m=monitor();alerts=[]
    try:
        for i in range(4):
            now[0]=1000+i*.1
            e=signal(i*.1,i,70+i)
            inbox.push({'source':e.source,'name':e.name,'sequence':i,'observed_at':now[0],
                        'ttl_ms':1000,'quality':'ok','value':e.value})
            alerts+=m.process(inbox.drain(i*.1),i*.1)
        assert {a['kind'] for a in alerts}=={'thermal_temperature','thermal_rise_rate','thermal_hotspot_area'}
    finally:
        inbox.close();store.close()


def test_invalid_configuration():
    with pytest.raises(ValueError):ThermalCfg(enabled=True)
    with pytest.raises(ValueError):ThermalRule(id='r',source='legacy-http',name='t',kind='temperature',threshold=20)
    with pytest.raises(ValueError):ThermalRule(id='r',source='device',name='t',kind='hotspot_area',threshold=.2)
