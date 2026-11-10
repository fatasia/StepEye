"""Download a bounded Apache-2.0 IndustReal RGB subset using ZIP byte ranges."""

import argparse, csv, hashlib, io, json, urllib.request, zipfile, zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import httpx

BASE = "https://data.4tu.nl/file/b008dd74-020d-4ea4-a8ba-7bb60769d224/"
RGB = "cf21602e-4424-42a4-b05d-9897957082ad"
LABELS = "778d7d5f-6631-4cb6-9451-f88c574c7991"
SIZE = 4960644152


class RemoteZip(io.RawIOBase):
    """ZIP seeks fetch only requested bytes; cache makes an interrupted run resumable."""

    def __init__(self, cache):
        self.pos = 0
        self.cache = cache
        self.session = httpx.Client()
        cache.mkdir(parents=True, exist_ok=True)

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, n, whence=0):
        self.pos = n if whence == 0 else self.pos + n if whence == 1 else SIZE + n
        return self.pos

    def read(self, n=-1):
        n = min(n if n >= 0 else SIZE - self.pos, SIZE - self.pos)
        out = []
        while n > 0:
            length = min(n, 1024 * 1024)
            end = self.pos + length - 1
            p = self.cache / f"{self.pos}-{end}.bin"
            common = self.cache.parent / "range-cache" / p.name
            if not p.exists() and common.exists():
                data = common.read_bytes()
                p.write_bytes(data)
            if p.exists():
                data = p.read_bytes()
            else:
                r = self.session.get(
                    BASE + RGB, headers={"Range": f"bytes={self.pos}-{end}"}, timeout=30
                )
                if (
                    r.status_code != 206
                    or r.headers.get("Content-Range")
                    != f"bytes {self.pos}-{end}/{SIZE}"
                ):
                    raise ValueError("Unexpected byte-range response")
                data = r.content
                if len(data) != length:
                    raise ValueError("Incomplete range")
                p.write_bytes(data)
            if len(data) != length:
                raise ValueError("Invalid cached range")
            out.append(data)
            self.pos += length
            n -= length
        return b"".join(out)


def validate_subject_splits(selection):
    """Keep all recordings from one person in one official split."""
    subjects = {
        split: {rec.split("_")[0] for rec in recs} for split, recs in selection.items()
    }
    for left, right in [("train", "val"), ("train", "test"), ("val", "test")]:
        if subjects[left] & subjects[right]:
            raise ValueError("Subject leakage across splits")


def verify_video(path, info):
    data = path.read_bytes()
    if len(data) != info.file_size or zlib.crc32(data) != info.CRC:
        raise ValueError(f"ZIP size/CRC mismatch: {path.name}")
    return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    root = a.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    label_path = root / "action_recognition_labels.zip"
    if not label_path.exists():
        with urllib.request.urlopen(BASE + LABELS, timeout=30) as r:
            label_path.write_bytes(r.read())
    with zipfile.ZipFile(label_path) as labels:
        for split in ("train", "val", "test"):
            (root / (split + ".csv")).write_bytes(labels.read(split + ".csv"))
    archive = zipfile.ZipFile(RemoteZip(root / "range-cache"))
    index = {x.filename: x for x in archive.infolist()}
    selection = {}
    for split, n in [("train", 6), ("val", 2), ("test", 2)]:
        rows = list(csv.reader((root / (split + ".csv")).read_text().splitlines()))
        recs = {x[0] for x in rows if x[0] + ".mp4" in index}
        chosen = []
        people = set()
        for rec in sorted(recs, key=lambda x: (index[x + ".mp4"].file_size, x)):
            subject = rec.split("_")[0]
            if subject not in people:
                chosen.append(rec)
                people.add(subject)
            if len(chosen) == n:
                break
        selection[split] = chosen
    validate_subject_splits(selection)
    (root / "selection.json").write_text(json.dumps(selection, indent=2))

    def acquire(split, rec):
        name = rec + ".mp4"
        path = root / name
        info = index[name]
        if not path.exists():
            # Each download owns its seek cursor and cache, never a shared ZIP handle.
            with zipfile.ZipFile(RemoteZip(root / ("range-cache-" + rec))) as own:
                data = own.read(name)
            temporary = path.with_suffix(".mp4.part")
            temporary.write_bytes(data)
            temporary.replace(path)
        data = verify_video(path, info)
        row = {
            "split": split,
            "recording": rec,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        (root / (rec + ".download.json")).write_text(json.dumps(row, indent=2))
        print(json.dumps(row), flush=True)
        return row

    manifest = []
    failures = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [
            pool.submit(acquire, split, rec)
            for split in ("val", "test", "train")
            for rec in selection[split]
        ]
        for job in as_completed(jobs):
            try:
                row = job.result()
            except Exception as exc:
                failures.append({"type": type(exc).__name__, "error": str(exc)})
                (root / "download-failures.json").write_text(
                    json.dumps(failures, indent=2)
                )
                print(f"Download failed: {type(exc).__name__}: {exc}", flush=True)
                continue
            manifest.append(row)
            (root / "download-manifest.json").write_text(json.dumps(manifest, indent=2))

    if failures:
        raise RuntimeError(
            f"{len(failures)} videos incomplete; see download-failures.json"
        )


if __name__ == "__main__":
    main()
