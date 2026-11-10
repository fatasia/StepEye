"""One frozen-feature industrial action experiment; no runtime registration."""

import sys, json, csv, hashlib, argparse
from pathlib import Path
import numpy as np
import cv2
import torch

ROOT_DIR = Path(__file__).resolve().parents[1]
ROOT = None
sys.path.insert(0, str(ROOT_DIR / "tools"))
sys.path.insert(0, str(ROOT_DIR))
from action_export_v2 import load_model, WEIGHT_SHA
from industreal_subset import validate_subject_splits
from visual.action_predict import prepare_frame, normalize_clip

CLASSES = ["align_objects", "tighten_nut", "fit_nut", "loosen_nut"]


def extract_features(root, selected, allow_missing):
    model = (
        load_model(ROOT_DIR / ".v0/videomaev2-small.pth", ROOT_DIR / ".v0/videomaev2-review")
        .cuda()
        .eval()
    )
    features = []
    labels = []
    splits = []
    records = []
    media = []
    skipped = []
    for split, recs in selected.items():
        rows = list(csv.reader((root / (split + ".csv")).read_text().splitlines()))
        for rec in recs:
            video = root / (rec + ".mp4")
            if not video.exists():
                if allow_missing:
                    continue
                raise FileNotFoundError(video)
            key = hashlib.sha256(
                (
                    WEIGHT_SHA
                    + hashlib.sha256(video.read_bytes()).hexdigest()
                    + hashlib.sha256((root / (split + ".csv")).read_bytes()).hexdigest()
                    + hashlib.sha256(
                        (ROOT_DIR / "visual/action_predict.py").read_bytes()
                    ).hexdigest()
                    + json.dumps(CLASSES)
                    + "linspace-inclusive-16-v1"
                ).encode()
            ).hexdigest()
            cache = root / ("features-" + key + ".npz")
            if cache.exists():
                saved = np.load(cache, allow_pickle=False)
                features.extend(saved["features"])
                labels.extend(saved["labels"])
                splits.extend([split] * len(saved["labels"]))
                records.extend(saved["records"].tolist())
                print(rec, "cached", flush=True)
                continue
            offset = len(features)
            cap = cv2.VideoCapture(str(video))
            count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            media.append({"recording": rec, "frames": count, "fps": fps})
            if not cap.isOpened() or abs(fps - 10) > 0.01:
                raise ValueError(f"Unexpected video alignment {rec} {fps}")
            todo = []
            for row in rows:
                if row[0] != rec or row[2] not in CLASSES:
                    continue
                start, end = int(row[3][:-4]), int(row[4][:-4])
                if not 0 <= start <= end < count:
                    raise ValueError("frame bounds")
                if end - start + 1 < 16:
                    skipped.append(row)
                    continue
                todo.append((row, np.linspace(start, end, 16).astype(int)))
            required = set(int(i) for _, idx in todo for i in idx)
            frames = {}
            i = 0
            while required:
                ok, frame = cap.read()
                if not ok:
                    raise ValueError("truncated video")
                if i in required:
                    frames[i] = prepare_frame(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    required.remove(i)
                i += 1
            cap.release()
            for row, idx in todo:
                clip = normalize_clip(np.stack([frames[int(i)] for i in idx]))
                with torch.inference_mode():
                    f = (
                        model.forward_features(torch.from_numpy(clip).cuda())
                        .cpu()
                        .numpy()[0]
                    )
                features.append(f)
                labels.append(CLASSES.index(row[2]))
                splits.append(split)
                records.append(row)
            np.savez(
                cache,
                features=np.asarray(features[offset:]),
                labels=np.asarray(labels[offset:]),
                records=np.asarray(records[offset:]),
            )
            print(rec, len(todo), flush=True)
    del model
    torch.cuda.empty_cache()
    return features, labels, splits, records


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--extract-only", action="store_true")
    args = parser.parse_args()
    ROOT = args.data.resolve()
    if (ROOT / "experiment.json").exists():
        raise FileExistsError("Experiment evidence already exists")
    torch.manual_seed(42)
    np.random.seed(42)
    selected = json.loads((ROOT / "selection.json").read_text())
    validate_subject_splits(selected)
    features, labels, splits, records = extract_features(
        ROOT, selected, args.extract_only
    )
    if args.extract_only:
        return
    X = np.asarray(features, dtype=np.float32)
    y = np.asarray(labels)
    sp = np.asarray(splits)
    np.savez(ROOT / "features.npz", features=X, labels=y, splits=sp)
    # Fit normalization on training subjects only; validation chooses a single head checkpoint.
    mu = X[sp == "train"].mean(0)
    sd = np.maximum(X[sp == "train"].std(0), 1e-6)
    xx = torch.tensor((X - mu) / sd)
    yy = torch.tensor(y, dtype=torch.long)
    head = torch.nn.Linear(384, 4)
    opt = torch.optim.AdamW(head.parameters(), lr=0.001, weight_decay=0.01)
    masks = {s: torch.tensor(sp == s) for s in selected}
    best = None
    best_loss = float("inf")
    for epoch in range(1, 201):
        head.train()
        opt.zero_grad()
        loss = torch.nn.functional.cross_entropy(
            head(xx[masks["train"]]), yy[masks["train"]]
        )
        loss.backward()
        opt.step()
        with torch.no_grad():
            val = float(
                torch.nn.functional.cross_entropy(
                    head(xx[masks["val"]]), yy[masks["val"]]
                )
            )
        if val < best_loss:
            best_loss = val
            best = {k: v.detach().clone() for k, v in head.state_dict().items()}
            best_epoch = epoch
    head.load_state_dict(best)
    media = []
    for recs in selected.values():
        for rec in recs:
            cap = cv2.VideoCapture(str(ROOT / (rec + ".mp4")))
            media.append(
                {
                    "recording": rec,
                    "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
                    "fps": cap.get(cv2.CAP_PROP_FPS),
                }
            )
            cap.release()
    report = {
        "classes": CLASSES,
        "base_sha256": WEIGHT_SHA,
        "seed": 42,
        "epochs": 200,
        "selected_epoch": best_epoch,
        "media": media,
        "short_segments_skipped": sum(
            1
            for split, recs in selected.items()
            for row in csv.reader((ROOT / (split + ".csv")).read_text().splitlines())
            if row[0] in recs
            and row[2] in CLASSES
            and int(row[4][:-4]) - int(row[3][:-4]) + 1 < 16
        ),
        "scope": "research subset, manually bounded clips, no streaming action-boundary validation",
    }
    with torch.no_grad():
        for split, mask in masks.items():
            truth = yy[mask].numpy()
            pred = head(xx[mask]).argmax(1).numpy()
            cm = np.zeros((4, 4), dtype=int)
            for a, b in zip(truth, pred):
                cm[a, b] += 1
            report[split] = {
                "n": len(truth),
                "accuracy": float(np.mean(truth == pred)),
                "confusion": cm.tolist(),
            }
    np.savez(
        ROOT / "linear-head.npz",
        weight=best["weight"].numpy(),
        bias=best["bias"].numpy(),
        mean=mu,
        std=sd,
        classes=np.asarray(CLASSES),
    )
    (ROOT / "experiment.json").write_text(json.dumps(report, indent=2))
    (ROOT / "segments.json").write_text(json.dumps(records, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
