"""Extract the pinned PAINE checkpoint without deserializing FastCore objects."""
from __future__ import annotations

import hashlib
import pickletools
import zipfile
from pathlib import Path

WEIGHT_SHA = "5d45d71d311e3d4d95e01bf6beced419b95df435cd649fbbd28fd3a589534d2e"
GLOBALS = {
    "collections OrderedDict", "torch._utils _rebuild_tensor_v2",
    "torch FloatStorage", "torch LongStorage", "fastcore.foundation L",
    "numpy._core.multiarray scalar", "numpy dtype", "_codecs encode",
}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class OptimizerList:
    """Inert replacement: only receive the discarded optimizer's item list."""

    def __setstate__(self, state):
        if not isinstance(state, dict) or set(state) != {"items"}:
            raise ValueError("Unexpected optimizer container state")
        if not isinstance(state["items"], list):
            raise ValueError("Unexpected optimizer items")
        self.items = state["items"]


def optimizer_scalar(dtype, raw):
    import numpy as np
    if dtype != np.dtype("<f8") or not isinstance(raw, bytes) or len(raw) != 8:
        raise ValueError("Unexpected optimizer scalar")
    return np.frombuffer(raw, dtype="<f8")[0].item()


def audit(path):
    """Reject any different file before widening the weights-only type set."""
    if digest(path) != WEIGHT_SHA:
        raise ValueError("Pinned PAINE checkpoint SHA mismatch")
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith("/data.pkl")]
        if len(names) != 1:
            raise ValueError("Expected one checkpoint pickle")
        ops = list(pickletools.genops(archive.read(names[0])))
    if any(op.name == "STACK_GLOBAL" for op, _, _ in ops):
        raise ValueError("Unexpected dynamic globals")
    globals_found = {arg for op, arg, _ in ops if op.name == "GLOBAL"}
    if globals_found != GLOBALS:
        raise ValueError("Checkpoint globals differ from static audit")
    return sorted(globals_found)


def extract(path):
    audit(path)
    import numpy as np
    import torch
    # The FastCore class is never imported; optimizer state is discarded.
    allowed = [
        (OptimizerList, "fastcore.foundation.L"),
        (optimizer_scalar, "numpy._core.multiarray.scalar"),
        np.dtype, type(np.dtype("float64")),
    ]
    with torch.serialization.safe_globals(allowed):
        payload = torch.load(path, map_location="cpu", weights_only=True, mmap=True)
    state = payload["model"]
    if not isinstance(state, dict) or not state:
        raise ValueError("Missing model state")
    if any(not isinstance(k, str) or type(v) is not torch.Tensor for k, v in state.items()):
        raise ValueError("Model state must contain only named tensors")
    return state
