import math
import sqlite3
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from visual.mechanism import MechanismBody,analyze,import_cycles
from visual.mechanism_core import cycle_features,life_features,reference_z
from visual.mechanism_routes import MechanismReports,router


def data(degrade=True,constant=False):
    lines=['cycle,value']
    for cycle in range(40):
        scale=1+.1*math.sin(cycle) if cycle<20 or not degrade else 2+(cycle-20)*.15
        for value in [0,1,0,-1,0,1,0,-1]:
            lines.append(f'{cycle},{1 if constant else value*scale}')
    return '\n'.join(lines)


def test_known_core_feature_and_slope_values():
    f=cycle_features(np.array([[0],[1],[0],[-1]],float))
    assert f[0,0]==pytest.approx(math.sqrt(.5))
    assert f[0,1]==0 and f[0,2]==1 and f[0,3]==1
    v=life_features(np.array([[[1],[3],[5],[7]]],float))[0]
    assert v[3]==pytest.approx(2)


def test_fixed_reference_deviation_crossing_and_forecast():
    result=analyze(MechanismBody(csv_text=data()))
    assert result['reference_count']==20 and result['status']=='review'
    assert result['first_persistent_crossing_cycle']==20
    assert result['trend']['slope_per_cycle']>0
    assert len(result['forecast'])==5 and result['forecast'][0]['cycle']==40
    assert result['rul_cycles'] is None and result['diagnosis']=='reference_deviation_only'
    assert analyze(MechanismBody(csv_text=data(False)))['status']=='within_reference'


def test_constant_reference_unknown_not_healthy():
    result=analyze(MechanismBody(csv_text=data(constant=True)))
    assert result['status']=='unknown' and result['forecast']==[]


@pytest.mark.parametrize('text',[
    'cycle,value\n0,nan', 'cycle,value\n0,inf','cycle,value\n0,1e30',
    'cycle,value\n0,1\n2,2','cycle,value\n0,1\n1,2\n0,3',
    'cycle,value\n0,1,2', 'value,cycle\n1,0', 'cycle,value\n0,1',
    'cycle,value\n0,1\n1,', 'cycle,value,value\n0,1,2'])
def test_invalid_csv_rejected(text):
    with pytest.raises(ValueError):import_cycles(text)


def test_real_http_csv_report_persistence_export_and_busy(tmp_path):
    path=tmp_path/'mechanism.sqlite';store=MechanismReports(path)
    app=FastAPI();app.include_router(router(store));client=TestClient(app)
    assert client.get('/api/mechanism/capabilities').json()['supports_rul'] is False
    response=client.post('/api/mechanism/analyses',json={'csv_text':data(),'title':'motor-current','unit':'A'})
    assert response.status_code==201
    report=response.json();rid=report['id']
    assert report['result']['first_persistent_crossing_cycle']==20
    assert client.get(f'/api/mechanism/analyses/{rid}/export').json()==report
    assert client.get('/api/mechanism/analyses').json()['total']==1
    assert client.get('/api/mechanism/analyses/no').status_code==404
    assert client.post('/api/mechanism/analyses',json={'csv_text':'x'}).status_code==400
    assert client.post('/api/mechanism/analyses',json={'csv_text':data(),'horizon':0}).status_code==422
    store._slots.acquire()
    try:assert client.post('/api/mechanism/analyses',json={'csv_text':data()}).status_code==429
    finally:store._slots.release()
    with pytest.raises(sqlite3.IntegrityError):store._db.execute('DELETE FROM mechanism_reports')
    store.close();store=MechanismReports(path)
    assert store.get(rid)==report
    store.close()
