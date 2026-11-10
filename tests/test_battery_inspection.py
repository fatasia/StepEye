import io
from PIL import Image
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from visual.battery_inspection import FACES, router, summarize_faces
from visual.equipment_results import EquipmentResults
from visual.equipment_results_types import EquipmentResultBody


def payload(face, **changes):
    return dict(device_id='six-side',sn='cell-1',product_id='model-A',inspection_id='run-1',
                face=face,observed_at='2026-11-03T12:00:00Z',result='OK',idempotency_key=face)|changes


def picture(index):
    output=io.BytesIO();Image.new('RGB',(40,40),(index*30,10,20)).save(output,'PNG')
    return [('face.png',output.getvalue())]


def test_six_faces_are_bound_to_product_serial_and_run(tmp_path):
    store=EquipmentResults(tmp_path/'equipment')
    app=FastAPI();app.include_router(router(store));client=TestClient(app)
    url='/api/battery-inspections?product_id=model-A&sn=cell-1&inspection_id=run-1'
    try:
        assert client.get(url).json()['missing_faces']==list(FACES)
        for index,face in enumerate(FACES):
            record,created=store.ingest(payload(face),picture(index),'operator')
            assert created
            # Idempotent retry must not introduce a conflicting second face.
            assert not store.ingest(payload(face),picture(index),'operator')[1]
        result=client.get(url).json()
        assert result['status']=='complete' and result['model_acceptance'] is False
        assert client.get(url.replace('model-A','model-B')).json()['status']=='pending'
        assert client.get(url.replace('run-1','run-2')).json()['status']=='pending'
        assert client.get(url.replace('cell-1','cell-2')).json()['status']=='pending'
        # A later duplicate image/face does not overwrite the original verdict.
        store.ingest(payload('front',idempotency_key='repeat',result='UNCERTAIN'),picture(6),'operator')
        assert client.get(url).json()['status']=='review'
        store.ingest(payload('front',idempotency_key='reject',result='NG'),picture(7),'operator')
        assert client.get(url).json()['status']=='NG'
    finally:store.close()


def test_missing_uncertain_and_copied_faces_cannot_complete():
    records=[payload(face)|{'images':[{'image_sha256':face}]} for face in FACES]
    assert summarize_faces(records[:-1],'p','s','r')['status']=='pending'
    records[0]['result']='UNCERTAIN'
    assert summarize_faces(records,'p','s','r')['status']=='review'
    records[0]['result']='OK';records[0]['images']=records[1]['images']
    report=summarize_faces(records,'p','s','r')
    assert report['status']=='review' and len(report['duplicate_image_faces'])==2


def test_partial_identity_rejected_and_legacy_payload_unchanged():
    value=payload('front');value.pop('inspection_id')
    with pytest.raises(ValueError):EquipmentResultBody.model_validate(value)
    for field in ('face','product_id'):value.pop(field)
    parsed=EquipmentResultBody.model_validate(value).model_dump(mode='json',exclude_none=True)
    assert 'face' not in parsed and 'inspection_id' not in parsed
