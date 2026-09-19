import sys
import tarfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import py7zr
import pytest

from upload_utils import (
    UnsafeArchiveError,
    UnsupportedArchiveError,
    detect_format,
    safe_extract,
)


def _make_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def _make_tar(path: Path, entries: dict[str, bytes], mode: str = "w") -> None:
    import io
    with tarfile.open(path, mode) as tf:
        for name, content in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))


def test_detect_format_common_extensions():
    assert detect_format("output.zip") == "zip"
    assert detect_format("output.7z") == "7z"
    assert detect_format("output.tar") == "tar"
    assert detect_format("output.tar.gz") == "tar"
    assert detect_format("output.tgz") == "tar"


def test_detect_format_rar_raises_clear_error():
    with pytest.raises(UnsupportedArchiveError):
        detect_format("output.rar")


def test_detect_format_unknown_extension_returns_none():
    assert detect_format("output.txt") is None


def test_zip_extracts_successfully(tmp_path):
    zip_path = tmp_path / "good.zip"
    _make_zip(zip_path, {
        "SIT Name/Version_20260101_0000/export_summary.json": b"{}",
        "SIT Name/Version_20260101_0000/corpus.jsonl": b"",
    })
    extract_to = tmp_path / "extracted_zip"
    count = safe_extract(zip_path, extract_to, "good.zip")
    assert count == 2
    assert (extract_to / "SIT Name" / "Version_20260101_0000" / "export_summary.json").exists()


def test_tar_gz_extracts_successfully(tmp_path):
    tar_path = tmp_path / "good.tar.gz"
    _make_tar(tar_path, {"SIT Name/export_summary.json": b"{}"}, mode="w:gz")
    extract_to = tmp_path / "extracted_tar"
    count = safe_extract(tar_path, extract_to, "good.tar.gz")
    assert count == 1
    assert (extract_to / "SIT Name" / "export_summary.json").exists()


def test_7z_extracts_successfully(tmp_path):
    src = tmp_path / "export_summary.json"
    src.write_text("{}")
    archive = tmp_path / "good.7z"
    with py7zr.SevenZipFile(archive, "w") as z:
        z.write(src, "SIT Name/Version_20260101_0000/export_summary.json")
    extract_to = tmp_path / "extracted_7z"
    count = safe_extract(archive, extract_to, "good.7z")
    assert count == 1
    assert (extract_to / "SIT Name" / "Version_20260101_0000" / "export_summary.json").exists()


def test_7z_slip_path_traversal_is_rejected(tmp_path):
    src = tmp_path / "evil.txt"
    src.write_text("pwned")
    archive = tmp_path / "malicious.7z"
    with py7zr.SevenZipFile(archive, "w") as z:
        z.write(src, "../../evil.txt")
    extract_to = tmp_path / "extracted"
    with pytest.raises(UnsafeArchiveError):
        safe_extract(archive, extract_to, "malicious.7z")
    assert not (tmp_path.parent.parent / "evil.txt").exists()


def test_zip_slip_path_traversal_is_rejected(tmp_path):
    zip_path = tmp_path / "malicious.zip"
    _make_zip(zip_path, {"../../evil.txt": b"pwned"})
    extract_to = tmp_path / "extracted"
    with pytest.raises(UnsafeArchiveError):
        safe_extract(zip_path, extract_to, "malicious.zip")
    assert not (tmp_path.parent.parent / "evil.txt").exists()


def test_tar_slip_path_traversal_is_rejected(tmp_path):
    tar_path = tmp_path / "malicious.tar"
    _make_tar(tar_path, {"../../evil.txt": b"pwned"})
    extract_to = tmp_path / "extracted"
    with pytest.raises(UnsafeArchiveError):
        safe_extract(tar_path, extract_to, "malicious.tar")
    assert not (tmp_path.parent.parent / "evil.txt").exists()


def test_unrecognized_extension_raises(tmp_path):
    fake = tmp_path / "notes.txt"
    fake.write_text("hello")
    with pytest.raises(UnsupportedArchiveError):
        safe_extract(fake, tmp_path / "out", "notes.txt")
