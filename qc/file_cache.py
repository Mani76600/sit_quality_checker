"""Process-local cache for directory listings, shared across check modules
within a single run_all() invocation.

Several check modules independently need the same directory listing (e.g.
Positive/outputs/metadata/*.json is listed separately by metadata_checks,
keyword_checks, integrity_checks, and raw_doc_checks) - without this, each
one re-stats the same thousands of files from disk. qc.registry.run_all()
clears this cache at the start of every run, so re-running a QC check after
fixing pipeline output never sees a stale listing.
"""

from __future__ import annotations

from pathlib import Path

_cache: dict[tuple[str, str], list[Path]] = {}


def list_dir(path: Path, pattern: str = "*.json") -> list[Path]:
    """Cached, sorted glob() of path/pattern. Missing dir -> []."""
    key = (str(path), pattern)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    result = sorted(path.glob(pattern)) if path.exists() else []
    _cache[key] = result
    return result


def list_dir_files(path: Path) -> list[Path]:
    """Cached, sorted list of direct child files (no subdirs). Missing dir -> []."""
    key = (str(path), "__files__")
    cached = _cache.get(key)
    if cached is not None:
        return cached
    result = sorted(p for p in path.iterdir() if p.is_file()) if path.exists() else []
    _cache[key] = result
    return result


def clear() -> None:
    _cache.clear()
