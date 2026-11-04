"""Quota checks cover expanded files, concurrent admission, API errors and cleanup."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import io
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from PIL import Image
import pytest
from visual.documents import DocumentStore, DocumentError
from visual.documents_routes import router
from visual.equipment_results import EquipmentResults
from visual.labels import LabelStore, LabelError
from visual.storage_governance import usage_report
from visual.storage_quota import StorageQuota, QuotaExceeded


def picture():
    out = io.BytesIO()
    Image.new('RGB', (40, 30), 'red').save(out, format='PNG')
    return out.getvalue()


def used(store):
    return usage_report(store.root)['total_bytes']


def test_env_defaults_validation_and_zero(monkeypatch, tmp_path):
    monkeypatch.delenv('STEPEYE_STORAGE_LIMITS_JSON', raising=False)
    assert StorageQuota.from_env(tmp_path).limits == {}
    monkeypatch.setenv('STEPEYE_STORAGE_LIMITS_JSON', '{"documents": 0}')
    with pytest.raises(QuotaExceeded):
        StorageQuota.from_env(tmp_path).check(tmp_path/'documents', 1)
    for raw in ('[]', '{"documents":true}', '{"documents":-1}', '{"unknown":5}'):
        monkeypatch.setenv('STEPEYE_STORAGE_LIMITS_JSON', raw)
        with pytest.raises(ValueError): StorageQuota.from_env(tmp_path)


def test_parallel_admission(tmp_path):
    quota = StorageQuota(tmp_path, {'documents': 10})
    root = tmp_path/'documents'; root.mkdir()
    def write(i):
        try:
            with quota.operation(root):
                quota.check(root, 10)
                (root/str(i)).write_bytes(b'x'*10)
                return True
        except QuotaExceeded: return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(write, range(4))) == 1
    assert usage_report(root)['total_bytes'] == 10


def test_labels_count_normalized_and_cleanup(tmp_path, monkeypatch):
    quota = StorageQuota(tmp_path, {'labeling': 10**7})
    store = LabelStore(tmp_path/'labeling', quota)
    try:
        pid = store.create_project('parts', ['part'])['id']; raw = picture()
        quota.limits['labeling'] = used(store) + len(raw)
        with pytest.raises(LabelError) as exc: store.add_asset(pid, raw, 'part.png')
        assert exc.value.status == 413 and store.list_assets(pid)['total'] == 0
        assert not list((store.root/'originals').iterdir())
        quota.limits['labeling'] = 10**7
        @contextmanager
        def broken_write():
            raise OSError('write failed')
            yield
        with monkeypatch.context() as patch:
            patch.setattr(store, '_write', broken_write)
            with pytest.raises(OSError): store.add_asset(pid, raw, 'part.png')
        assert not list((store.root/'images').iterdir())
        assert not list((store.root/'originals').iterdir())
        assert store.add_asset(pid, raw, 'part.png')['id']
    finally: store.close()


def test_document_api_and_parse_cleanup(tmp_path, monkeypatch):
    quota = StorageQuota(tmp_path, {'documents': 0})
    store = DocumentStore(tmp_path/'documents', quota)
    app = FastAPI(); app.include_router(router(store))
    @app.middleware('http')
    async def actor(request: Request, call_next):
        request.state.user = {'username':'op','role':'operator'}
        return await call_next(request)
    try:
        with TestClient(app) as client:
            response = client.post('/api/documents', files={'file':('manual.md',b'manual')},data={'title':'manual'})
        assert response.status_code == 413, response.text
        assert not list(store.files.iterdir())
        quota.limits['documents'] = 10**7
        with monkeypatch.context() as patch:
            def bad(_): raise DocumentError('bad document')
            patch.setattr('visual.documents.parse_file', bad)
            with pytest.raises(DocumentError): store.upload('manual.md',b'manual',{'title':'manual'},'operator','op')
        assert not list(store.files.iterdir())
        assert store.upload('manual.md',b'manual',{'title':'manual'},'operator','op')['id']
    finally: store.close()


def test_equipment_expansion_rolls_back_and_retry_is_allowed(tmp_path):
    quota = StorageQuota(tmp_path, {'equipment-results':10**7})
    store = EquipmentResults(tmp_path/'equipment-results', quota)
    payload = {'device_id':'AOI-1','sn':'P1','observed_at':'2026-11-04T10:00:00Z',
               'result':'NG','defects':['short'],'source_kind':'AOI','idempotency_key':'a1'}
    try:
        raw = picture(); quota.limits['equipment-results'] = used(store) + len(raw)
        with pytest.raises(LabelError) as exc: store.ingest(payload,[('a.png',raw)],'op')
        assert exc.value.status == 413 and store.list()['total'] == 0
        assert not list(store.staging.iterdir()) and not list(store.records.iterdir())
        quota.limits['equipment-results'] = 10**7
        first, created = store.ingest(payload,[('a.png',raw)],'op')
        assert created
        quota.limits['equipment-results'] = 0
        again, created = store.ingest(payload,[('a.png',raw)],'op')
        assert not created and again['id'] == first['id']
    finally: store.close()


def test_version_export_obeys_quota_and_existing_export_stays_readable(tmp_path):
    quota = StorageQuota(tmp_path, {'labeling':10**7})
    store = LabelStore(tmp_path/'labeling', quota)
    try:
        pid = store.create_project('parts', ['part'])['id']
        asset = store.add_asset(pid,picture(),'a.png')
        store.confirm(asset['id'],0,'op',True)
        quota.limits['labeling'] = used(store)
        with pytest.raises(LabelError) as exc: store.create_version(pid,42,.2,'op')
        assert exc.value.status == 413 and not store.list_versions(pid)
        assert not list((store.root/'versions').iterdir())
        quota.limits['labeling'] = 10**7
        version = store.create_version(pid,42,.2,'op')
        quota.limits['labeling'] = 0
        assert store.create_version(pid,42,.2,'op')['id'] == version['id']
    finally: store.close()


def test_real_server_uses_same_limits_for_upload_and_report(tmp_path, monkeypatch):
    from visual.server import build_app
    monkeypatch.setenv('STEPEYE_STORAGE_LIMITS_JSON', '{"documents":0}')
    app = build_app([], tmp_path/'data')
    with TestClient(app) as client:
        report = client.get('/api/operations/storage')
        assert report.status_code == 200
        assert report.json()['quota']['limits'] == {'documents':0}
        upload = client.post('/api/documents',files={'file':('a.md',b'text')},data={'title':'a'})
        assert upload.status_code == 413
