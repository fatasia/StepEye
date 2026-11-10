from types import SimpleNamespace
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from visual.calibration import CalibrationStore
from visual.dsl import StationConfig
from visual.proximity_routes import router


def test_safety_patch_persists_preserves_other_config_and_rejects_bad_binding(tmp_path):
    raw={'station':'s','zones':[{'id':'z','polygon':[[0,0],[1,0],[1,1],[0,1]]}],
         'source':{'type':'sim'},'hands':{'enabled':False},
         'safety':{'enabled':True,'rules':[{'id':'idle','kind':'person_absent','zone':'z'}]}}
    path=tmp_path/'s.yaml';path.write_text(yaml.safe_dump(raw),encoding='utf-8')
    rt=SimpleNamespace(cfg=StationConfig.model_validate(raw),engine=SimpleNamespace(cycle=None),w=640,h=480,
                       bind_calibrations=lambda store:None)
    def reload(sid):rt.cfg=StationConfig.model_validate(yaml.safe_load(path.read_text(encoding='utf-8')))
    manager=SimpleNamespace(paths={'s':path},runtimes={'s':rt},reload=reload)
    store=CalibrationStore(tmp_path/'c.db');app=FastAPI();app.include_router(router(manager,store));client=TestClient(app)
    try:
        data=client.get('/api/stations/s/safety-config').json()
        body={key:data[key] for key in ('ppe','thermal','proximity')}
        body['ppe']['enabled']=True
        body['thermal']={'enabled':True,'rules':[{'id':'hot','source':'thermal-cam','name':'temperature','kind':'temperature','threshold':80}]}
        saved=client.put('/api/stations/s/safety-config',json=body)
        assert saved.status_code==200,saved.text
        actual=yaml.safe_load(path.read_text(encoding='utf-8'))
        assert actual['ppe']['enabled'] and actual['thermal']['rules'][0]['threshold']==80
        assert actual['source']==raw['source'] and actual['safety']==raw['safety']
        ehs={'enabled':True,'rules':[{'id':'stock','kind':'object_count','zone':'z','labels':['person'],'min_count':1,'max_count':3}]}
        endpoint='/api/stations/s/ehs-config'
        saved=client.put(endpoint,json=ehs)
        assert saved.status_code==200,saved.text
        assert saved.json()['config']['rules'][0]['max_count']==3
        assert yaml.safe_load(path.read_text(encoding='utf-8'))['ppe']['enabled']
        ehs['rules'][0]['zone']='missing'
        assert client.put(endpoint,json=ehs).status_code==422
        assert client.get(endpoint).json()['config']['rules'][0]['zone']=='z'
        body['proximity']['enabled']=True
        assert client.put('/api/stations/s/safety-config',json=body).status_code==422
        body['proximity']['enabled']=False
        rt.engine.cycle=object()
        assert client.put('/api/stations/s/safety-config',json=body).status_code==409
    finally:store.close()
