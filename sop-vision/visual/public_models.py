"""Load reviewed local manifests, never fetch weights or start inference at boot."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .label_jobs import LabelEngines, fingerprint

log = logging.getLogger(__name__)


def register_public_models(engines: LabelEngines, root: Path) -> int:
    """Missing downloads are normal. Invalid or replaced assets are not offered."""
    root = root.resolve()
    manifest = root / "models/public/safety-models.json"
    if not manifest.is_file():
        return 0
    count = 0
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data["schema_version"] != 1 or not isinstance(data["models"], list):
            raise ValueError("unsupported public-model manifest")
        for item in data["models"]:
            path = (root / item["model_path"]).resolve()
            if not path.is_relative_to(root / "models/public") or path.suffix != ".onnx":
                raise ValueError("public model path outside asset directory")
            if not item.get("adapter_compatible") or not path.is_file():
                continue
            if fingerprint(path) != item["sha256"]:
                log.warning("Public model fingerprint mismatch: %s", item["id"])
                continue
            # Available means runnable for evaluation, not licensed/validated for deployment.
            engines.register(item["id"], path, item["classes"], item["name"], metadata={
                "expected_sha256": item["sha256"], "usage": "evaluation_only",
                "license_status": item["license_status"],
                "commercial_review_required": True, "source": item["source"],
            })
            count += 1
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("Public model manifest is invalid; check models/public/safety-models.json")
    return count
