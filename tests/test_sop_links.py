"""SOP 文档-步骤关联：草稿生成、编辑、确认门禁、影响提示与 HTTP 权限。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from visual.documents import DocumentStore, DocumentError
from visual.sop_links import SopLinkStore, SopLinkError, document_paragraph_snapshot
from visual.sop_link_routes import router


DOC_V1 = ("作业员从料盒取料并上料到位。\n"
          "使用扭矩扳手将螺母拧紧至规定扭矩。\n"
          "完成后目视检查外观有无划伤。\n")


def spaced_doc(*sentences, gap=15):
    """句子行号对齐 4 + 16k（mod 8 = 4），保证每句只落进一个检索段落。"""
    parts = ["占位说明。"] * 4
    for i, sentence in enumerate(sentences):
        parts.append(sentence)
        if i < len(sentences) - 1:
            parts.extend(["工艺背景说明。"] * gap)
    return "\n".join(parts)


def manual_paragraphs(*texts):
    return [{"paragraph": i + 1, "text": t} for i, t in enumerate(texts)]


@pytest.fixture
def docs(tmp_path):
    store = DocumentStore(tmp_path / 'docs')
    yield store
    store.close()


@pytest.fixture
def store(tmp_path):
    instance = SopLinkStore(tmp_path / 'sop-links.db')
    yield instance
    instance.close()


def upload(store, text=DOC_V1, doc=None):
    return store.upload('manual.md', text.encode(),
                        {'title': '装配手册', 'station': 's1', 'device': '', 'product': ''},
                        'operator', 'op', doc)


def make_draft(docs, link, station='s1'):
    version = upload(docs)
    paragraphs = manual_paragraphs(*[line for line in DOC_V1.splitlines() if line.strip()])
    return link.create(version['id'], version['version'], version['document_id'],
                       station, paragraphs, 'operator', 'op')


def test_generate_steps_zone_placeholder_and_order(store, docs):
    draft = make_draft(docs, store)
    assert [s['id'] for s in draft['steps']] == ['load', 'fasten', 'visual-check']
    assert draft['steps'][1]['after'] == ['load']
    assert draft['steps'][2]['after'] == ['fasten']
    evidence = draft['steps'][0]['evidence']['all']
    assert evidence[0]['obj_state']['zone'].startswith('待映射:')
    assert draft['status'] == 'draft' and draft['version_number'] == 1


def test_generate_no_match_rejected(store, docs):
    version = upload(docs, '本节描述部件外形尺寸与运输要求。')
    paragraphs = manual_paragraphs('本节描述部件外形尺寸与运输要求。')
    with pytest.raises(SopLinkError, match='没有匹配'):
        store.create(version['id'], 1, version['document_id'], 's1', paragraphs, 'operator', 'op')


def test_duplicate_skeleton_ids_deduped(store, docs):
    version = upload(docs, '先目视检查来料外观。再目视检查成品外观。\n上料。')
    paragraphs = manual_paragraphs('先目视检查来料外观。', '再目视检查成品外观。', '上料。')
    draft = store.create(version['id'], 1, version['document_id'], 's1',
                         paragraphs, 'operator', 'op')
    ids = [s['id'] for s in draft['steps']]
    assert ids == ['visual-check', 'visual-check-2', 'load']


def test_update_steps_validates(store, docs):
    draft = make_draft(docs, store)
    steps = draft['steps']
    with pytest.raises(ValueError):
        store.update_steps(draft['id'], [{'id': 'x', 'evidence': {'all': [], 'any': []}}],
                           'operator', 'op')
    edited = [{'id': 'fasten', 'after': [], 'timeout_s': 90,
               'evidence': {'all': [{'hand_dwell': {'zone': 'z2', 'min_ms': 500}}]}}]
    updated = store.update_steps(draft['id'], edited, 'operator', 'op')
    assert updated['steps'][0]['timeout_s'] == 90 and updated['status'] == 'draft'


def test_confirm_requires_real_zones(store, docs, tmp_path):
    draft = make_draft(docs, store)
    with pytest.raises(SopLinkError, match='占位符'):
        store.confirm(draft['id'], 'operator', 'op', ['z1', 'z2'])
    steps = [{'id': 'fasten', 'after': [], 'timeout_s': 90,
              'evidence': {'all': [{'hand_dwell': {'zone': 'zX', 'min_ms': 500}}]}}]
    store.update_steps(draft['id'], steps, 'operator', 'op')
    with pytest.raises(SopLinkError, match='不存在'):
        store.confirm(draft['id'], 'operator', 'op', ['z1', 'z2'])
    steps[0]['evidence']['all'][0]['hand_dwell']['zone'] = 'z2'
    store.update_steps(draft['id'], steps, 'operator', 'op')
    confirmed = store.confirm(draft['id'], 'operator', 'op', ['z1', 'z2'])
    assert confirmed['status'] == 'confirmed' and confirmed['confirm_actor'] == 'op'
    with pytest.raises(SopLinkError):
        store.discard(draft['id'], 'operator')
    with pytest.raises(SopLinkError, match='仅草稿状态'):
        store.update_steps(draft['id'], steps, 'operator', 'op')


def test_discard_draft_allowed(store, docs):
    draft = make_draft(docs, store)
    discarded = store.discard(draft['id'], 'operator')
    assert discarded['status'] == 'discarded'


def test_recipe_patch_requires_confirmed(store, docs):
    draft = make_draft(docs, store)
    with pytest.raises(SopLinkError):
        store.recipe_patch(draft['id'], 'operator')
    steps = draft['steps']
    for s in steps:
        for cond in (s['evidence']['all'] or s['evidence'].get('any') or s['evidence']['seq']):
            (kind, body), = cond.items()
            if 'zone' in body:
                body['zone'] = 'z1'
    store.update_steps(draft['id'], steps, 'operator', 'op')
    store.confirm(draft['id'], 'operator', 'op', ['z1'])
    patch = store.recipe_patch(draft['id'], 'operator')
    assert [s['id'] for s in patch['steps']] == ['load', 'fasten', 'visual-check']
    assert patch['version_id'] == draft['version_id']


def test_impact_detects_changed_paragraph(store, docs):
    draft = make_draft(docs, store)
    steps = draft['steps']
    for s in steps:
        for cond in s['evidence'].get('all') or s['evidence'].get('any') or s['evidence']['seq']:
            (kind, body), = cond.items()
            if 'zone' in body:
                body['zone'] = 'z1'
    store.update_steps(draft['id'], steps, 'operator', 'op')
    store.confirm(draft['id'], 'operator', 'op', ['z1'])
    doc_id = draft['document_id']
    sentences = ('作业员从料盒取料并上料到位。', '使用扭矩扳手将螺母拧紧至规定扭矩。',
                 '完成后目视检查外观有无划伤。')
    # 版本2 没改内容：不影响已确认草稿。
    v2 = upload(docs, spaced_doc(*sentences), doc_id)
    docs.set_status(v2['id'], 'published', 'admin')
    report = store.impact('operator', doc_id, docs.list('operator', doc_id),
                          lambda vid: docs.paragraphs(vid, 'operator'))
    assert report['affected'] == [] and report['latest_version'] == 2
    # 版本3 改掉拧紧段落：提示受影响，不静默修改。
    changed = spaced_doc(sentences[0], '将紧固件放入孔位后手工预固定。', sentences[2])
    v3 = upload(docs, changed, doc_id)
    docs.set_status(v3['id'], 'published', 'admin')
    report = store.impact('operator', doc_id, docs.list('operator', doc_id),
                          lambda vid: docs.paragraphs(vid, 'operator'))
    assert len(report['affected']) == 1
    hit = report['affected'][0]
    assert hit['step_ids'] == ['fasten'] and hit['similarity'] < 0.6


def test_viewer_cannot_write(store, docs):
    draft = make_draft(docs, store)
    with pytest.raises(DocumentError):
        store.discard(draft['id'], 'viewer')


def test_paragraph_snapshot_missing_segment(docs):
    version = upload(docs)
    with pytest.raises(DocumentError):
        document_paragraph_snapshot(docs, version['id'], 'operator', [99])


@pytest.fixture
def client(tmp_path):
    station = tmp_path / 'station.yaml'
    station.write_text('station: s1\nzones:\n  - id: z1\n  - id: z2\n', encoding='utf-8')
    manager = SimpleNamespace(paths={'s1': station})
    app = FastAPI()
    docs = DocumentStore(tmp_path / 'docs')
    link = SopLinkStore(tmp_path / 'sop-links.db')
    app.include_router(router(link, docs, manager))

    async def fake_user(request, call_next):
        request.state.user = {'username': 'op', 'role': 'operator'}
        return await call_next(request)

    app.middleware('http')(fake_user)
    yield TestClient(app), docs, link
    docs.close()
    link.close()


def test_http_draft_flow_and_zone_gate(client):
    http, docs, link = client
    version = upload(docs, spaced_doc(*(
        '作业员从料盒取料并上料到位。', '使用扭矩扳手将螺母拧紧至规定扭矩。',
        '完成后目视检查外观有无划伤。')))
    resp = http.post('/api/sop/links/drafts',
                     json={'version_id': version['id'], 'station_id': 's1'})
    assert resp.status_code == 201, resp.text
    draft = resp.json()
    assert len(draft['steps']) == 3
    # 占位符未映射：HTTP 确认必须 422。
    resp = http.post(f"/api/sop/links/drafts/{draft['id']}/confirm", json={})
    assert resp.status_code == 422, resp.text
    listing = http.get('/api/sop/links/drafts', params={'document_id': version['document_id']})
    assert listing.status_code == 200 and len(listing.json()['items']) == 1


def test_http_impact_endpoint(client):
    http, docs, link = client
    version = upload(docs)
    docs.set_status(version['id'], 'published', 'admin')
    resp = http.get('/api/sop/links/impact', params={'document_id': version['document_id']})
    assert resp.status_code == 200
    assert resp.json()['affected'] == []
    missing = http.get('/api/sop/links/impact', params={'document_id': 'nope'})
    assert missing.status_code == 404
