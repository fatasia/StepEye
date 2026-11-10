"""Pack original MCC5-THU feature caches into the mechanism laboratory upload format."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual.mechanism_motor import FEATURE_HELPER_SHA, MotorBody, pinned_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="One original features[N,114] NPZ per complete record")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    bundle = pinned_bundle(root)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf8"))
    records = []
    for path in args.inputs:
        with np.load(path, allow_pickle=False) as packet:
            values = packet["features"]
            if values.ndim != 2 or values.shape[1] != 114:
                raise ValueError("每条记录须为原版 [N,114] 特征")
            records.append({"id": path.stem, "complete_record": True, "features": values.tolist()})
    body = MotorBody(title="电机记录分析", dataset="MCC5-THU-Motor", feature_helper_sha256=FEATURE_HELPER_SHA,
                     feature_names=manifest["feature_names"], records=records)
    with args.output.open("x", encoding="utf8") as stream:
        json.dump(body.model_dump(), stream, ensure_ascii=False, allow_nan=False)
    print(json.dumps({"records": len(records), "output": str(args.output)}))


if __name__ == "__main__":
    main()
