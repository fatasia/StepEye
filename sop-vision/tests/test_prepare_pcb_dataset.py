import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('prepare_pcb',Path(__file__).parents[1]/'tools/prepare_pcb_dataset.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_rounding_clip_is_recorded():
    values,changed=m.clipped_box([200,10,26.0113,20],226,226)
    assert changed and values[0]+values[2]/2==pytest.approx(1)
    assert m.clipped_box([10,10,20,20],226,226)[1] is False

def test_large_or_invalid_box_rejected():
    for box in ([200,10,30,20],[10,10,-1,20],[float('nan'),1,2,3]):
        with pytest.raises(ValueError):m.clipped_box(box,226,226)

def test_unpinned_archive_and_existing_output_rejected(tmp_path):
    path=tmp_path/'source.zip';path.write_bytes(b'not pinned')
    with pytest.raises(ValueError,match='SHA'):m.prepare(path,tmp_path/'out')
    assert not (tmp_path/'out').exists()
    (tmp_path/'out').mkdir()
    with pytest.raises(ValueError,match='exists'):m.prepare(path,tmp_path/'out')
