"""Byte-range acquisition contracts without network downloads."""

import io
from types import SimpleNamespace
import pytest
from tools import industreal_subset as module


class Response(io.BytesIO):
    status_code = 206

    def __init__(self, data, content_range):
        super().__init__(data)
        self.content = data
        self.headers = {"Content-Range": content_range}


def test_cache_resume(tmp_path, monkeypatch):
    calls = []

    def fetch(url, headers, timeout):
        calls.append(headers["Range"])
        return Response(b"abcd", f"bytes 12-15/{module.SIZE}")

    monkeypatch.setattr(module.httpx, "Client", lambda: SimpleNamespace(get=fetch))
    reader = module.RemoteZip(tmp_path)
    reader.seek(12)
    assert reader.read(4) == b"abcd"
    reader.seek(12)
    assert reader.read(4) == b"abcd"
    assert len(calls) == 1


def test_wrong_range(tmp_path, monkeypatch):
    monkeypatch.setattr(
        module.httpx,
        "Client",
        lambda: SimpleNamespace(get=lambda *a, **kw: Response(b"abcd", "bytes 0-3/4")),
    )
    with pytest.raises(ValueError, match="byte-range"):
        module.RemoteZip(tmp_path).read(4)
    assert not list(tmp_path.glob("*.bin"))


def test_seek_and_short_cache(tmp_path):
    reader = module.RemoteZip(tmp_path)
    assert reader.seek(-4, 2) == module.SIZE - 4
    reader.seek(0)
    (tmp_path / "0-3.bin").write_bytes(b"a")
    with pytest.raises(ValueError, match="cached"):
        reader.read(4)


def test_subject_overlap_is_rejected():
    with pytest.raises(ValueError, match="Subject leakage"):
        module.validate_subject_splits(
            {"train": ["01_a"], "val": ["02_a"], "test": ["01_b"]}
        )
    module.validate_subject_splits(
        {"train": ["01_a"], "val": ["02_a"], "test": ["03_a"]}
    )


def test_same_size_corruption_is_rejected(tmp_path):
    import zlib

    path = tmp_path / "video.mp4"
    path.write_bytes(b"bad!")
    info = SimpleNamespace(file_size=4, CRC=zlib.crc32(b"good"))
    with pytest.raises(ValueError, match="CRC"):
        module.verify_video(path, info)
