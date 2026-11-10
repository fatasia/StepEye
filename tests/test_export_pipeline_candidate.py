import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('pipeline_export', Path(__file__).parents[1]/'tools/export_pipeline_candidate.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_unreviewed_checkpoint_rejected_before_deserialization(tmp_path):
    source = tmp_path/'weights.pt'; source.write_bytes(b'not the reviewed source')
    with pytest.raises(ValueError, match='SHA mismatch'):
        module.inspect(source)


def test_existing_output_is_not_overwritten(tmp_path):
    output = tmp_path/'candidate.onnx'; output.write_bytes(b'keep')
    with pytest.raises(ValueError, match='new ONNX'):
        module.export(tmp_path/'absent.pt', output)
    assert output.read_bytes() == b'keep'


def test_five_classes_not_readme_two_class_claim():
    assert module.CLASSES == ['crack', 'gas', 'leak', 'no leak', 'water']
