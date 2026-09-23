"""Streaming scan of sit_inverted_index.json / sit_merged_index.json.

These files are commonly 50-100MB+ in real output because every indexed
chunk record embeds the full chunk_content text. Both consumers
(context_normalized.py's value->filename lookup, and index_files.py's
"polarity" key / field-completeness checks) previously called plain
json.load() independently - fully materializing the whole nested
structure, chunk_content text included, twice over. Neither actually
needs the chunk text: one needs a small value->[filenames] map, the other
needs only aggregate counts. This streams the file once via ijson.parse()
and extracts just those, so peak memory stays a small, bounded fraction of
the file size regardless of how large the file is.

Structure being streamed: {sit_name: {value: {filename: [chunk_record, ...]}}}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import ijson

_MAX_POLARITY_EXAMPLES = 20
_MAX_FIELD_EXAMPLES = 10


@dataclass
class InvertedIndexScan:
    error: str | None = None
    not_a_dict: bool = False
    value_to_filenames: dict[str, list[str]] = field(default_factory=dict)
    unique_values: int = 0
    polarity_key_count: int = 0
    polarity_key_paths: list[str] = field(default_factory=list)
    total_chunk_entries: int = 0
    empty_chunk_id: int = 0
    empty_chunk_content: int = 0
    field_completeness_examples: list[str] = field(default_factory=list)


_SCAN_CACHE: dict[str, InvertedIndexScan] = {}


def clear_scan_cache() -> None:
    _SCAN_CACHE.clear()


def scan_inverted_index(path: Path) -> InvertedIndexScan:
    key = str(path)
    cached = _SCAN_CACHE.get(key)
    if cached is not None:
        return cached
    scan = _scan(path)
    _SCAN_CACHE[key] = scan
    return scan


def _scan(path: Path) -> InvertedIndexScan:
    scan = InvertedIndexScan()
    path = Path(path)
    if not path.exists():
        scan.error = f"file not found: {path}"
        return scan
    try:
        if path.stat().st_size == 0:
            scan.error = f"file is empty (0 bytes): {path}"
            return scan
    except OSError as exc:
        scan.error = f"could not stat file: {path} ({exc})"
        return scan

    depth = 0
    first_event_seen = False
    current_value: str | None = None
    current_filenames: list[str] = []
    current_filename: str | None = None
    pending_key_at_5: str | None = None
    chunk_id_ok = False
    chunk_content_ok = False

    try:
        with path.open("rb") as f:
            for prefix, event, value in ijson.parse(f):
                if not first_event_seen:
                    first_event_seen = True
                    if event != "start_map":
                        scan.not_a_dict = True
                        break

                if event == "start_map":
                    depth += 1
                    if depth == 5:
                        chunk_id_ok = False
                        chunk_content_ok = False
                elif event == "start_array":
                    depth += 1
                elif event == "map_key":
                    if depth == 2:
                        current_value = value
                        current_filenames = []
                        scan.unique_values += 1
                    elif depth == 3:
                        current_filenames.append(value)
                        current_filename = value
                    elif depth == 5:
                        pending_key_at_5 = value
                        if value == "polarity":
                            scan.polarity_key_count += 1
                            if len(scan.polarity_key_paths) < _MAX_POLARITY_EXAMPLES:
                                scan.polarity_key_paths.append(prefix)
                elif event in ("string", "number", "boolean", "null"):
                    if depth == 5 and pending_key_at_5 is not None:
                        if pending_key_at_5 == "chunk_id":
                            chunk_id_ok = value is not None and value != ""
                        elif pending_key_at_5 == "chunk_content":
                            chunk_content_ok = bool(value)
                        pending_key_at_5 = None
                elif event in ("end_map", "end_array"):
                    if depth == 5 and event == "end_map":
                        scan.total_chunk_entries += 1
                        bad = False
                        if not chunk_id_ok:
                            scan.empty_chunk_id += 1
                            bad = True
                        if not chunk_content_ok:
                            scan.empty_chunk_content += 1
                            bad = True
                        if bad and len(scan.field_completeness_examples) < _MAX_FIELD_EXAMPLES:
                            scan.field_completeness_examples.append(
                                f"value='{current_value}' file='{current_filename}'")
                    depth -= 1
                    if depth == 2 and event == "end_map" and current_value is not None:
                        scan.value_to_filenames[current_value] = current_filenames
                        current_value = None
                        current_filenames = []
    except Exception as exc:  # malformed/truncated JSON, or any ijson parse error
        scan.error = f"invalid JSON in {path}: {exc}"
    return scan
