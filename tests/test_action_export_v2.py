import importlib.util
from pathlib import Path
import hashlib
import pytest

spec=importlib.util.spec_from_file_location('v2_export',Path(__file__).parents[1]/'tools/action_export_v2.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_checkpoint_reject_before_torch_load(tmp_path):
    p=tmp_path/'bad.pth';p.write_bytes(b'not weights')
    with pytest.raises(ValueError,match='checkpoint hash'):m.load_model(p,tmp_path)


def test_labels_pinned_hash_reject(tmp_path):
    p=tmp_path/'labels';p.write_text('invented class',encoding='utf-8')
    with pytest.raises(ValueError,match='label map'):m.verified_labels(p)


def test_labels_keep_author_order(tmp_path,monkeypatch):
    p=tmp_path/'labels';p.write_text('\n'.join(f'c{i}' for i in range(710)),encoding='utf-8')
    monkeypatch.setattr(m,'LABEL_SHA',m.text_digest(p))
    assert m.verified_labels(p)[30]=='c30'


@pytest.mark.parametrize('names', [['duplicate']*710,['one']])
def test_incomplete_or_duplicate_labels_rejected(tmp_path,monkeypatch,names):
    p=tmp_path/'labels';p.write_text('\n'.join(names),encoding='utf-8')
    monkeypatch.setattr(m,'LABEL_SHA',m.text_digest(p))
    with pytest.raises(ValueError):m.verified_labels(p)


def test_source_hash_portable_line_endings(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b'
    a.write_bytes(b'one\ntwo\n');b.write_bytes(b'one\r\ntwo\r\n')
    assert m.text_digest(a)==m.text_digest(b)
