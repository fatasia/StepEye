"""Real homography geometry and SQLite tests; no physical camera accuracy claim."""
import copy

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from visual.calibration import CalibrationStore
from visual.calibration_routes import router


def specimen(unit="mm"):
    scale = 1 if unit == "mm" else .01
    def pair(x, y):
        return {"pixel": [x, y], "physical": [x * scale, y * scale]}
    return {"scope_id": "camera-a", "reference": "fixed-lens-v1", "image_size": [640, 480],
            "plane": "workpiece" if unit == "mm" else "ground", "unit": unit,
            "points": [pair(100, 100), pair(500, 100), pair(500, 400), pair(100, 400)],
            "validation_points": [pair(200, 200), pair(400, 300)], "tolerance": .001}


def query():
    return {"scope_id": "camera-a", "reference": "fixed-lens-v1", "image_size": [640, 480],
            "points": [[200, 200], [500, 400]]}


@pytest.fixture
def store(tmp_path):
    instance = CalibrationStore(tmp_path / "calibration.db")
    yield instance
    instance.close()


@pytest.mark.parametrize("unit,scale", [("mm", 1), ("m", .01)])
def test_measure_and_versions_persist(tmp_path, unit, scale):
    path = tmp_path / "c.db"
    store = CalibrationStore(path)
    first = store.create(specimen(unit))
    second = store.create(specimen(unit))
    assert first["version"] == 1 and second["version"] == 2
    assert first["validated"] and first["validation_max"] < 1e-10
    result = store.measure(first["id"], query())
    assert result["distance"] == pytest.approx(np.hypot(300, 200) * scale)
    store.close()
    reopened = CalibrationStore(path)
    assert reopened.get(first["id"]) == first
    assert len(reopened.list("camera-a")) == 2
    assert reopened.list("other") == []
    reopened.close()


def test_real_projective_mapping(store):
    body = specimen()
    h = np.array([[2., .1, 30], [.2, 3, 40], [.0002, .0003, 1]])
    def transform(p):
        q = h @ np.array([*p, 1])
        return (q[:2] / q[2]).tolist()
    for pair in body["points"] + body["validation_points"]:
        pair["physical"] = transform(pair["pixel"])
    result = store.create(body)
    measured = store.measure(result["id"], query())
    expected = np.linalg.norm(np.array(transform([200, 200])) - transform([500, 400]))
    assert measured["distance"] == pytest.approx(expected)


@pytest.mark.parametrize("change", ["few", "collinear", "duplicate", "nonfinite", "outside", "unit", "reuse", "duplicate_check", "check_outside"])
def test_reject_bad_geometry(store, change):
    body = specimen()
    if change == "few":
        body["points"].pop()
    elif change == "collinear":
        for i, point in enumerate(body["points"]):
            point["pixel"] = [100 + i * 20, 100]
    elif change == "duplicate":
        body["points"][1] = copy.deepcopy(body["points"][0])
    elif change == "nonfinite":
        body["points"][0]["physical"][0] = float("nan")
    elif change == "outside":
        body["points"][0]["pixel"][0] = 640
    elif change == "unit":
        body["unit"] = "m"
    elif change == "reuse":
        body["validation_points"][0] = copy.deepcopy(body["points"][0])
    elif change == "duplicate_check":
        body["validation_points"][1] = copy.deepcopy(body["validation_points"][0])
    else:
        body["validation_points"][0]["pixel"] = [10, 10]
    with pytest.raises(ValueError):
        store.create(body)
    assert not store.list()


def test_failed_independent_validation_retained_but_not_usable(store):
    body = specimen()
    body["validation_points"][0]["physical"][0] += 10
    result = store.create(body)
    assert not result["validated"] and result["validation_max"] == pytest.approx(10)
    with pytest.raises(ValueError, match="未通过"):
        store.measure(result["id"], query())


@pytest.mark.parametrize("field,value", [("scope_id", "other"), ("reference", "moved"),
                                         ("image_size", [1280, 960]), ("points", [[0, 0], [200, 200]]),
                                         ("points", [[-1, 200], [200, 200]]),
                                         ("points", [[float("inf"), 200], [200, 200]])])
def test_measure_rejects_mismatches(store, field, value):
    record = store.create(specimen())
    body = query()
    body[field] = value
    with pytest.raises(ValueError):
        store.measure(record["id"], body)


def test_http_roundtrip_and_missing(store):
    app = FastAPI()
    app.include_router(router(store))
    client = TestClient(app)
    record = client.post("/api/calibrations", json=specimen())
    assert record.status_code == 201
    cid = record.json()["id"]
    assert client.get("/api/calibrations").json()[0]["id"] == cid
    assert client.get(f"/api/calibrations/{cid}").status_code == 200
    assert client.post(f"/api/calibrations/{cid}/measure", json=query()).status_code == 200
    assert client.get("/api/calibrations/missing").status_code == 404
    body = query()
    body["reference"] = "changed"
    assert client.post(f"/api/calibrations/{cid}/measure", json=body).status_code == 422
    assert client.put(f"/api/calibrations/{cid}", json=specimen()).status_code == 405
