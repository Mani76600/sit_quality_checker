"""Archive upload handling for the web app: safe extraction (zip-slip
path-traversal protection) into a fresh temp directory per upload, with
cleanup helpers so uploaded data doesn't linger on the server disk longer
than the session needs it.

Supported formats: .zip, .7z, .tar, .tar.gz/.tgz, .tar.bz2 - all handled
without needing an external system binary, so this works unmodified on a
locked-down host like Streamlit Community Cloud. .rar is deliberately NOT
supported: reading it needs the external `unrar` (or `unar`) binary, which
generally isn't installable on managed hosting - rather than silently fail
there, uploads of that type are rejected up front with an explanation.

This module is intentionally separate from the qc/ package (which stays
byte-identical to the local-path QCChecker app) since it's upload-specific
and only exists in this web variant.
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

import py7zr

SUPPORTED_EXTENSIONS = [".zip", ".7z", ".tar", ".tar.gz", ".tgz", ".tar.bz2"]


class UnsafeArchiveError(Exception):
    """Raised when an archive entry would extract outside the target
    directory (a "zip-slip" path-traversal attempt) - a required guard for
    any app that accepts uploads from untrusted users, which a publicly
    hosted app must always assume, regardless of archive format."""


class UnsupportedArchiveError(Exception):
    """Raised for a recognized-but-unhandleable format (currently just .rar)."""


def detect_format(filename: str) -> str | None:
    name = filename.lower()
    if name.endswith(".tar.gz"):
        return "tar"
    if name.endswith(".tar.bz2"):
        return "tar"
    for ext in (".zip", ".7z", ".tar", ".tgz"):
        if name.endswith(ext):
            return {"zip": "zip", "7z": "7z", "tar": "tar", "tgz": "tar"}[ext.lstrip(".")]
    if name.endswith(".rar"):
        raise UnsupportedArchiveError(
            ".rar archives need an external 'unrar' binary that isn't available on this "
            "host - please re-zip your output folder as .zip, .7z, or .tar.gz instead.")
    return None


def _is_within_directory(directory: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def _check_member_paths(extract_to: Path, names: list[str]) -> None:
    for name in names:
        if not _is_within_directory(extract_to, extract_to / name):
            raise UnsafeArchiveError(f"Unsafe path in archive entry: {name}")


def safe_extract_zip(archive_path: Path, extract_to: Path) -> int:
    with zipfile.ZipFile(archive_path) as zf:
        members = zf.infolist()
        _check_member_paths(extract_to, [m.filename for m in members])
        zf.extractall(extract_to)
        return sum(1 for m in members if not m.is_dir())


def safe_extract_7z(archive_path: Path, extract_to: Path) -> int:
    with py7zr.SevenZipFile(archive_path, mode="r") as zf:
        names = zf.getnames()
        _check_member_paths(extract_to, names)
        zf.extractall(path=extract_to)
        return len(names)


def safe_extract_tar(archive_path: Path, extract_to: Path) -> int:
    with tarfile.open(archive_path) as tf:
        members = tf.getmembers()
        _check_member_paths(extract_to, [m.name for m in members])
        try:
            tf.extractall(extract_to, filter="data")  # Python 3.12+ built-in hardening
        except TypeError:
            tf.extractall(extract_to)  # older Python without the `filter` kwarg
        return sum(1 for m in members if m.isfile())


_EXTRACTORS = {"zip": safe_extract_zip, "7z": safe_extract_7z, "tar": safe_extract_tar}


def safe_extract(archive_path: Path, extract_to: Path, filename_hint: str | None = None) -> int:
    """Extract any supported archive format into extract_to. Returns the
    number of files extracted. filename_hint lets callers pass the original
    uploaded filename when archive_path itself is a generic temp name."""
    fmt = detect_format(filename_hint or archive_path.name)
    if fmt is None:
        raise UnsupportedArchiveError(
            f"Unrecognized archive type for '{filename_hint or archive_path.name}' - "
            f"supported: {', '.join(SUPPORTED_EXTENSIONS)}.")
    extract_to.mkdir(parents=True, exist_ok=True)
    return _EXTRACTORS[fmt](archive_path, extract_to)


def new_temp_dir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def cleanup_dir(path: Path | str | None) -> None:
    if not path:
        return
    path = Path(path)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
