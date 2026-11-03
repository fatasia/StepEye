import io,json
import numpy as np
import pytest
from PIL import Image
from visual.calibration import CalibrationStore
from visual.measurement import measure_edges


@pytest.fixture
def calibrated(tmp_path):
    s=CalibrationStore(tmp_path/'c.db')
    pairs=lambda ps:[{'pixel':p,'physical':[v*.5 for v in p]} for p in ps]
    c=s.create(dict(scope_id='camera-a',reference='fixed',image_size=[240,160],plane='workpiece',unit='mm',
        points=pairs([[0,0],[239,0],[239,159],[0,159]]),validation_points=pairs([[30,30],[200,130]]),tolerance=.01))
    yield s,c
    s.close()


def image():
    a=np.zeros((160,240,3),np.uint8);a[:,60:160]=255;return a


def spec():return dict(scope_id='camera-a',reference='fixed',rois=[[45,15,75,145],[145,15,175,145]],edge_direction='vertical')


def test_known_geometry_50mm_and_overlay(calibrated):
    s,c=calibrated;r=measure_edges(s,c['id'],image(),spec())
    assert r['status']=='measured' and r['distance']==pytest.approx(50,abs=.01)
    assert r['unit']=='mm' and r['version']==c['version'] and len(r['measurement_line'])==2
    assert len(r['edges'])==2 and r['edges'][0]['support']>.9


def test_blank_and_multiple_edges_review(calibrated):
    s,c=calibrated
    assert measure_edges(s,c['id'],np.zeros_like(image()),spec())['status']=='review'
    body=spec();body['rois'][0]=[40,15,180,145]
    r=measure_edges(s,c['id'],image(),body)
    assert r['reason']=='multiple_edges' and r['distance'] is None


def test_wrong_reference_roi_and_frame_rejected(calibrated):
    s,c=calibrated
    body=spec();body['reference']='camera moved'
    with pytest.raises(ValueError):measure_edges(s,c['id'],image(),body)
    body=spec();body['rois'][0]=[-1,0,30,50]
    with pytest.raises(ValueError):measure_edges(s,c['id'],image(),body)
    with pytest.raises(ValueError):measure_edges(s,c['id'],image()[:150],spec())


def test_horizontal_edge_measurement(calibrated):
    s,c=calibrated;a=np.zeros((160,240,3),np.uint8);a[40:120,:]=255
    body=spec();body.update(edge_direction='horizontal',rois=[[15,25,220,55],[15,105,220,135]])
    assert measure_edges(s,c['id'],a,body)['distance']==pytest.approx(40,abs=.01)


def test_single_image_http_protocol(calibrated):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from visual.measurement_routes import router
    s,c=calibrated;app=FastAPI();app.include_router(router(s));client=TestClient(app)
    b=io.BytesIO();Image.fromarray(image()).save(b,format='PNG')
    response=client.post(f"/api/calibrations/{c['id']}/measure-edges",files={'file':('part.png',b.getvalue(),'image/png')},data={'spec':json.dumps(spec())})
    assert response.status_code==200 and response.json()['distance']==pytest.approx(50,abs=.01)


def test_non_parallel_edges_require_review(calibrated):
    s,c=calibrated;a=np.zeros_like(image())
    for y in range(160):a[y,60:150+int(y*.1)]=255
    body=spec();body['rois'][1]=[140,15,180,145]
    r=measure_edges(s,c['id'],a,body)
    assert r['status']=='review' and r['reason']=='non_parallel_edges'
    assert r['distance'] is None
