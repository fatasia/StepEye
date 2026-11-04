import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('hairnet_export', Path(__file__).parents[1] / 'tools/export_hairnet_candidate.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_unreviewed_checkpoint_is_rejected_before_loading(tmp_path):
    source = tmp_path / 'bad.pt'
    source.write_bytes(b'not the reviewed model')
    with pytest.raises(ValueError, match='SHA mismatch'):
        module.checkpoint_globals(source)


def test_existing_output_is_not_overwritten(tmp_path):
    output = tmp_path / 'candidate.onnx'
    output.write_bytes(b'historical model')
    with pytest.raises(ValueError, match='new ONNX'):
        module.export(tmp_path / 'unused.pt', output)
    assert output.read_bytes() == b'historical model'
