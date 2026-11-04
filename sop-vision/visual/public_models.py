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
    count = 0
    for name in ("safety-models.json","quality-models.json","specialized-detection-models.json","water-models.json"):
        manifest=root/"models/public"/name
        if not manifest.is_file(): continue
        try:
            data=json.loads(manifest.read_text(encoding="utf-8"))
            if data["schema_version"]!=1 or not isinstance(data["models"],list):
                raise ValueError("unsupported public-model manifest")
            for item in data["models"]:
                # Historical manifests omit task; new non-detection candidates must
                # never appear in a catalog whose trial pipeline decodes YOLO boxes.
                if item.get("task", "detect") != "detect": continue
                path=(root/item["model_path"]).resolve()
                if not path.is_relative_to(root/"models/public") or path.suffix!=".onnx":
                    raise ValueError("public model path outside asset directory")
                adapter=item.get("adapter","yolo_raw")
                if adapter not in {"yolo_raw","yolo_e2e","kairos_rtdetr"}: raise ValueError("unknown adapter")
                shape=item.get("native_output")
                if shape is not None:
                    if (not isinstance(shape,list) or len(shape)!=3 or shape[0]!=1 or
                        (adapter=="yolo_e2e" and shape[2]!=6) or
                        (adapter=="yolo_raw" and not any(d in (4+len(item["classes"]),5+len(item["classes"])) for d in shape[1:]))):
                        continue
                if not item.get("adapter_compatible") or not path.is_file(): continue
                if fingerprint(path)!=item["sha256"]:
                    log.warning("Public model fingerprint mismatch: %s",item["id"])
                    continue
                # Available means runnable for evaluation, not licensed/validated for deployment.
                engines.register(item["id"],path,item["classes"],item["name"],metadata={
                    "expected_sha256":item["sha256"],"usage":"evaluation_only","adapter":adapter,
                    "license_status":item["license_status"],"commercial_review_required":True,"source":item["source"]})
                count+=1
        except (OSError,ValueError,KeyError,TypeError):
            log.warning("Public model manifest is invalid: %s",name)
    return count
