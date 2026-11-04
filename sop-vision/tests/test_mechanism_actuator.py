import copy
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from visual.mechanism_actuator import ActuatorBody,ActuatorService,CHANNELS
from visual.mechanism_actuator_routes import actuator_router
from visual.mechanism_routes import MechanismReports


def body():
    x=np.sin(np.linspace(0,2*np.pi,400));values=np.stack([x,x*.1,x*.2],axis=1)
    ev=values.copy();ev[:,1]+=1;ev[:,2]+=.5
    def w(id,values):return dict(id=id,source_group=id,context='same',values=values.tolist())
    return dict(channels=CHANNELS,reference_confirmed_healthy=True,reference_source='reviewed test geometry',
                reference_windows=[w('reference',values)],evaluation_windows=[w('evaluation',ev)])


def test_known_residual_and_no_rul():
    result=ActuatorService().analyze(body())
    row=result['results'][0]
    assert row['error_residual_rms_mm']==pytest.approx(1,abs=.001)
    assert row['current_residual_rms_A']==pytest.approx(.5,abs=.001)
    assert result['supports_rul'] is False and result['status']=='evidence_only'


@pytest.mark.parametrize('fault',['samegroup','sameimage','context','unhealthy','length'])
def test_contract_rejects(fault):
    b=body();ev=b['evaluation_windows'][0]
    if fault=='samegroup':ev['source_group']='reference'
    if fault=='sameimage':ev['values']=copy.deepcopy(b['reference_windows'][0]['values'])
    if fault=='context':ev['context']='other'
    if fault=='unhealthy':b['reference_confirmed_healthy']=False
    if fault=='length':ev['values'].pop()
    with pytest.raises(ValueError):ActuatorBody.model_validate(b)


def test_unknown_position_and_busy_close():
    b=body();b['evaluation_windows'][0]['values'][0][0]=10
    service=ActuatorService()
    assert service.analyze(b)['results'][0]['status']=='unknown'
    service._lock.acquire()
    with pytest.raises(RuntimeError):service.analyze(body())
    assert service.close() is False
    service._lock.release()


def test_real_api_report(tmp_path):
    store=MechanismReports(tmp_path/'reports.sqlite');service=ActuatorService()
    app=FastAPI();app.include_router(actuator_router(store,service))
    try:
        with TestClient(app) as client:
            assert client.get('/api/mechanism/actuator/capabilities').json()['available']
            response=client.post('/api/mechanism/actuator/analyses',json=body())
            assert response.status_code==201,response.text
            report=response.json();assert store.get(report['id'])['kind']=='actuator_motion_residual'
    finally:service.close();store.close()
