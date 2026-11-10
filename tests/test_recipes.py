from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from visual.recipes import RecipeStore, RecipeService, RecipeBody, VersionBody
from visual.recipe_routes import router


@pytest.fixture
def service(tmp_path):
    path = tmp_path / "station.yaml"
    path.write_text(yaml.safe_dump({"station": "a", "zones": [{"id": "zone", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}],
        "safety": {"enabled": True, "rules": [{"id": "idle", "kind": "person_absent", "zone": "zone"}]}}))
    calls = []
    manager = SimpleNamespace(paths={"a": path}, runtimes={"a": SimpleNamespace(engine=SimpleNamespace(cycle=None))},
                              reload=lambda sid: calls.append(sid))
    instance = RecipeService(RecipeStore(tmp_path / "r.db"), manager)
    yield instance, calls
    instance.store.close()


def create(service):
    return service.create(RecipeBody(name="A配方", model_key="SKU-A", station_id="a"), "admin")


def test_versions_switch_rollback_and_external_drift(service):
    svc, calls = service
    recipe = create(svc)
    first = recipe["latest_version"]
    second = svc.add_version(recipe["id"], VersionBody(patch={"quality": {"enabled": True, "blur_min": 20}}), "admin")
    assert second["version"] == 2
    assert not first["fields"]["quality"]["enabled"]
    svc.switch("a", second["id"], "admin")
    assert svc.status("a")["active"]["version_id"] == second["id"]
    svc.switch("a", first["id"], "admin")
    assert svc.status("a")["active"]["version_id"] == first["id"]
    assert len(calls) == 2
    with svc.manager.paths["a"].open("a") as out:
        out.write("\n# edited elsewhere\n")
    assert svc.status("a")["active"] is None
    # 已有 applied 记录但文件不匹配：必须标记 unsynced，运维才能区分中断与从未切换。
    assert svc.status("a")["unsynced"] is True


def test_status_unsynced_false_before_any_switch(service):
    svc, _ = service
    create(svc)
    status = svc.status("a")
    assert status["active"] is None and status["unsynced"] is False


@pytest.mark.parametrize("patch", [{"source": {}}, {"station": "other"}, {"detector": {"model": "x"}},
                                   {"zones": []}, {"safety": {"rules": []}}])
def test_reject_invalid_or_device_patch(service, patch):
    svc, _ = service
    with pytest.raises(ValueError):
        svc.create(RecipeBody(name="A", model_key="A", station_id="a", patch=patch), "admin")
    assert not svc.store.list()


def test_cycle_and_wrong_station_refused(service):
    svc, calls = service
    vid = create(svc)["latest_version"]["id"]
    svc.manager.runtimes["a"].engine.cycle = object()
    before = svc.manager.paths["a"].read_bytes()
    with pytest.raises(ValueError, match="周期"):
        svc.switch("a", vid, "admin")
    with pytest.raises(ValueError, match="不属于"):
        svc.switch("b", vid, "admin")
    assert not calls and svc.manager.paths["a"].read_bytes() == before


@pytest.mark.parametrize("restore_fails", [True, False])
def test_failed_reload_restore_report(service, restore_fails):
    svc, _ = service
    vid = create(svc)["latest_version"]["id"]
    original = svc.manager.paths["a"].read_bytes()
    calls = []
    def reload(sid):
        calls.append(sid)
        if len(calls) == 1 or restore_fails:
            raise RuntimeError("failure")
    svc.manager.reload = reload
    with pytest.raises(ValueError, match="恢复失败" if restore_fails else "已恢复"):
        svc.switch("a", vid, "admin")
    assert svc.manager.paths["a"].read_bytes() == original
    history = svc.store.history("a")
    assert history[0]["restored"] is not restore_fails
    assert history[0]["status"] == "failed"


def test_http(service):
    svc, _ = service
    app = FastAPI()
    @app.middleware("http")
    async def user(request, call_next):
        request.state.user = {"username": "test"}
        return await call_next(request)
    app.include_router(router(svc))
    client = TestClient(app)
    response = client.post("/api/recipes", json={"name": "A", "model_key": "SKU-A", "station_id": "a"})
    assert response.status_code == 201, response.text
    recipe = response.json()
    assert len(client.get("/api/recipes").json()) == 1
    assert client.get(f"/api/recipes/{recipe['id']}/versions").status_code == 200
    assert client.post("/api/stations/a/recipe", json={"version_id": recipe["latest_version"]["id"]}).status_code == 200
    assert client.get("/api/stations/a/recipe").json()["active"]
    assert client.get("/api/recipes/missing/versions").status_code == 404
    changed = yaml.safe_load(svc.manager.paths["a"].read_text())
    changed["quality"] = {"enabled": True, "blur_min": 42}
    svc.manager.paths["a"].write_text(yaml.safe_dump(changed))
    snapshot = client.post(f"/api/recipes/{recipe['id']}/versions", json={"snapshot_current": True})
    assert snapshot.status_code == 201
    assert snapshot.json()["fields"]["quality"]["blur_min"] == 42


def test_persistence(service, tmp_path):
    svc, _ = service
    recipe = create(svc)
    reopened = RecipeStore(tmp_path / "r.db")
    assert reopened.get(recipe["id"])["model_key"] == "SKU-A"
    assert reopened.versions(recipe["id"])[0] == recipe["latest_version"]
    reopened.close()


@pytest.mark.parametrize("active", [True, False])
def test_manager_reload_refuses_active_cycle_or_live_old_thread(service, active):
    import threading
    from visual.server import StationManager
    from fastapi import HTTPException
    svc, _ = service
    stopped = []
    old = svc.manager.runtimes["a"]
    old.stop = lambda: stopped.append(True)
    old.engine.cycle = object() if active else None
    # Keep real reload method dispatch and its lock, without starting a service.
    manager = StationManager.__new__(StationManager)
    manager.__dict__.update(_runtime_change_lock=threading.RLock(),
                              runtimes={"a": old}, paths=svc.manager.paths,
                              _threads={"a": SimpleNamespace(join=lambda timeout: None, is_alive=lambda: True)},
                              _apply_active_release=lambda cfg: cfg,
                              hub=SimpleNamespace(publish=lambda *a, **k: None))
    with pytest.raises(HTTPException) as caught:
        StationManager.reload(manager, "a")
    assert caught.value.status_code == 409
    assert bool(stopped) is not active


def test_snapshot_current_captures_changes_without_mutating_versions(service):
    svc, calls = service
    recipe = create(svc)
    path = svc.manager.paths["a"]
    raw = yaml.safe_load(path.read_text())
    raw["quality"] = {"enabled": True, "blur_min": 31}
    path.write_text(yaml.safe_dump(raw))
    original = path.read_bytes()
    legacy = svc.add_version(recipe["id"], VersionBody(), "operator")
    assert not legacy["fields"]["quality"]["enabled"]
    snapshot = svc.add_version(recipe["id"], VersionBody(snapshot_current=True), "operator")
    assert snapshot["fields"]["quality"]["blur_min"] == 31
    assert snapshot["fields"]["quality"]["enabled"]
    assert "source" not in snapshot["fields"] and "detector" not in snapshot["fields"]
    assert svc.store.version(recipe["latest_version"]["id"]) == recipe["latest_version"]
    assert path.read_bytes() == original and not calls
    with pytest.raises(ValueError, match="模型路径"):
        svc.add_version(recipe["id"], VersionBody(snapshot_current=True, patch={"detector": {}}), "operator")
