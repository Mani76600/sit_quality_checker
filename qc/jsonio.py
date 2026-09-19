"""Safe JSON / JSONL readers.

Every reader returns (value, error) instead of raising, so a single malformed
or truncated file never crashes an entire QC run - it just shows up as an
``integrity`` finding for that file while every other check still executes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def read_json(path: Path | str) -> tuple[object | None, str | None]:
    path = Path(path)
    if not path.exists():
        return None, f"file not found: {path}"
    try:
        if path.stat().st_size == 0:
            return None, f"file is empty (0 bytes): {path}"
    except OSError as exc:
        return None, f"could not stat file: {path} ({exc})"
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON in {path}: {exc}"
    except OSError as exc:
        return None, f"could not read {path}: {exc}"


_json_cache: dict[str, tuple[object | None, str | None]] = {}


def read_json_cached(path: Path | str) -> tuple[object | None, str | None]:
    """Like read_json(), but parses each file at most once per run_all()
    invocation. Several check modules independently sample and read the
    same per-document metadata.json files (metadata_checks, keyword_checks,
    integrity_checks, content_verification, scenario_id_checks, ...) -
    without this cache each one re-parses the same files from disk.
    qc.registry.run_all() clears this via clear_json_cache() at the start
    of every run."""
    key = str(path)
    cached = _json_cache.get(key)
    if cached is not None:
        return cached
    result = read_json(path)
    _json_cache[key] = result
    return result


def clear_json_cache() -> None:
    _json_cache.clear()


def read_jsonl(path: Path | str, max_lines: int | None = None) -> tuple[list[object], list[str]]:
    """Return (records, errors). Errors are per-line parse failures, not fatal."""
    path = Path(path)
    records: list[object] = []
    errors: list[str] = []
    if not path.exists():
        return records, [f"file not found: {path}"]
    try:
        if path.stat().st_size == 0:
            return records, [f"file is empty (0 bytes): {path}"]
    except OSError as exc:
        return records, [f"could not stat file: {path} ({exc})"]
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            for i, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    errors.append(f"{path}:{i}: invalid JSON ({exc})")
                if max_lines is not None and len(records) >= max_lines:
                    break
    except OSError as exc:
        errors.append(f"could not read {path}: {exc}")
    return records, errors


def count_jsonl_lines(path: Path | str) -> tuple[int, list[str]]:
    """Count non-empty lines without holding the whole file in memory, plus parse errors."""
    path = Path(path)
    if not path.exists():
        return 0, [f"file not found: {path}"]
    n = 0
    errors: list[str] = []
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            for i, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                n += 1
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(f"{path}:{i}: invalid JSON ({exc})")
    except OSError as exc:
        errors.append(f"could not read {path}: {exc}")
    return n, errors


_jsonl_cache: dict[str, tuple[list[dict], list[str]]] = {}


def read_jsonl_cached(path: Path | str) -> tuple[list[dict], list[str]]:
    """Like read_jsonl(), but parses each file at most once per run_all()
    invocation. corpus.jsonl and combined_metadata.jsonl are large and get
    read by more than one check module (label-distribution counts,
    scenario_id cross-checks, ...) - without this cache each one re-parses
    the whole file from disk independently. qc.registry.run_all() clears
    this via clear_jsonl_cache() at the start of every run, so a re-run
    after fixing pipeline output never sees stale data. Non-dict lines are
    dropped (every consumer here expects JSON objects, not scalars/arrays)."""
    key = str(path)
    cached = _jsonl_cache.get(key)
    if cached is not None:
        return cached
    path = Path(path)
    records: list[dict] = []
    errors: list[str] = []
    if not path.exists():
        errors.append(f"file not found: {path}")
    else:
        try:
            with path.open("r", encoding="utf-8-sig") as fh:
                for i, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError as exc:
                        errors.append(f"{path}:{i}: invalid JSON ({exc})")
                        continue
                    if isinstance(rec, dict):
                        records.append(rec)
        except OSError as exc:
            errors.append(f"could not read {path}: {exc}")
    result = (records, errors)
    _jsonl_cache[key] = result
    return result


def clear_jsonl_cache() -> None:
    _jsonl_cache.clear()


def to_bool(value: object) -> bool | None:
    """Normalize the pipeline's mixed bool encodings (real bool vs 'True'/'False' string)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v == "true":
            return True
        if v == "false":
            return False
    return None


def iter_files(root: Path | str, pattern: str = "*"):
    root = Path(root)
    if not root.exists():
        return
    yield from root.glob(pattern)
