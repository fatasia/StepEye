"""Checkpoint compatibility accepts no executable framework container."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location(
    "pin_checkpoint", Path(__file__).parents[1] / "tools/pin_checkpoint.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_unrecognized_checkpoint_rejected_before_load(tmp_path):
    path = tmp_path / "untrusted.pth"
    path.write_bytes(b"not the registered file")
    with pytest.raises(ValueError, match="SHA mismatch"):
        module.extract(path)


def test_optimizer_container_has_only_items():
    container = module.OptimizerList()
    container.__setstate__({"items": [{"lr": 0.1}]})
    assert container.items == [{"lr": 0.1}]
    with pytest.raises(ValueError):
        container.__setstate__({"items": [], "callback": "unknown"})
    with pytest.raises(ValueError):
        container.__setstate__({"items": "not a list"})


def test_optimizer_scalar_only_fixed_float64():
    raw = np.array([0.1], dtype="<f8").tobytes()
    assert module.optimizer_scalar(np.dtype("<f8"), raw) == 0.1
    with pytest.raises(ValueError):
        module.optimizer_scalar(np.dtype("O"), raw)
    with pytest.raises(ValueError):
        module.optimizer_scalar(np.dtype("<f8"), raw + b"x")
