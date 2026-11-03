import io
import tarfile
import zipfile

import pytest

from deploy.install_monibuca import extract, package_name


def test_native_package_selection():
    assert package_name("Windows", "AMD64") == "monibuca_v6_windows_amd64.zip"
    assert package_name("Linux", "aarch64") == "monibuca_v6_linux_arm64.tar.gz"
    with pytest.raises(ValueError):
        package_name("Windows", "arm64")


def test_extract_only_binary_not_archive_paths(tmp_path):
    archive = tmp_path / "test.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../../escape.txt", b"bad")
        z.writestr("bin/monibuca.exe", b"binary")
    assert extract(archive, tmp_path / "out").read_bytes() == b"binary"
    assert not (tmp_path / "escape.txt").exists()
    linux = tmp_path / "test.tar.gz"
    with tarfile.open(linux, "w:gz") as t:
        item = tarfile.TarInfo("bin/monibuca_linux_amd64"); item.size = 3
        t.addfile(item, io.BytesIO(b"elf"))
    assert extract(linux, tmp_path / "linux").read_bytes() == b"elf"
