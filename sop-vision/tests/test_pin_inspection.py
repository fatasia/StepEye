import io
import json

import numpy as np
import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from visual.pin_inspection import inspect_pins
from visual.pin_inspection_routes import router


def image(centers=(40, 80, 120, 160)):
    array = np.zeros((100, 200, 3), np.uint8)
    for c in centers:
        array[10:90, c-5:c+5] = 255
    return array


def spec(**changes):
    return dict(roi=[20, 20, 180, 80], axis='x', polarity='bright', expected_count=4,
                position_tolerance_px=3, spacing_tolerance_px=3, **changes)


def test_known_count_spacing_missing_and_offsets():
    ref = image()
    matched = inspect_pins(ref, ref, spec())
    assert matched['status'] == 'matched'
    assert matched['observed']['spacing_px'] == [40, 40, 40]
    missing = inspect_pins(ref, image((40, 120, 160)), spec())
    assert missing['status'] == 'deviation'
    assert missing['missing'] == [{'index':2, 'center':79.5}]
    assert missing['review_required'] and missing['unit'] == 'px'
    moved = inspect_pins(ref, image((40, 85, 120, 160)), spec())
    assert moved['status'] == 'deviation'
    assert moved['offsets'][1]['delta_px'] == 5
    assert not moved['spacing_checks'][0]['within_tolerance']
    extra = inspect_pins(ref, image((40, 80, 100, 120, 160)), spec())
    assert extra['status'] == 'deviation' and extra['extra'] == [99.5]


def test_vertical_dark_and_low_contrast_review():
    a = image().transpose(1, 0, 2).copy()
    body = spec();body.update(roi=[20, 20, 80, 180], axis='y', polarity='dark')
    assert inspect_pins(255-a, 255-a, body)['status'] == 'matched'
    blank = np.zeros_like(image())
    assert inspect_pins(image(), blank, spec())['reason'] == 'low_contrast'
    body = spec();body['expected_count'] = 5
    assert inspect_pins(image(), image(), body)['reason'] == 'reference_count_mismatch'


def test_partial_border_and_invalid_inputs():
    a = image();a[20:55, 75:85] = 0
    assert inspect_pins(image(), a, spec())['status'] == 'review'
    body = spec();body['roi'] = [35, 20, 180, 80]
    assert inspect_pins(image(), image(), body)['reason'] == 'pin_touches_roi_border'
    body = spec();body['roi'] = [-1, 20, 180, 80]
    with pytest.raises(ValueError):inspect_pins(image(), image(), body)
    with pytest.raises(ValueError):inspect_pins(image(), image()[:90], spec())
    body=spec();body['position_tolerance_px']=float('nan')
    with pytest.raises(ValueError):inspect_pins(image(),image(),body)


def test_real_multipart_and_roles():
    app = FastAPI()
    role = ['operator']
    @app.middleware('http')
    async def user(request, call_next):
        request.state.user = {'role':role[0]}
        return await call_next(request)
    app.include_router(router())
    buffer=io.BytesIO();Image.fromarray(image()).save(buffer,format='PNG')
    files={name:(name+'.png',buffer.getvalue(),'image/png') for name in ['reference','file']}
    with TestClient(app) as client:
        response=client.post('/api/quality/pins/inspect',files=files,data={'spec':json.dumps(spec())})
        assert response.status_code==200 and response.json()['status']=='matched'
        role[0]='viewer'
        assert client.post('/api/quality/pins/inspect',files=files,data={'spec':json.dumps(spec())}).status_code==403
