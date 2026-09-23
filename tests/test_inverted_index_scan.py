import json
import tracemalloc

import pytest

from qc.checks.inverted_index_scan import clear_scan_cache, scan_inverted_index


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_scan_cache()
    yield
    clear_scan_cache()


def _write(path, obj) -> None:
    path.write_text(json.dumps(obj), encoding="utf-8")


def _sample():
    return {
        "Test SIT": {
            "123-456": {
                "doc_a.docx": [
                    {"chunk_id": 0, "chunk_content": "hello world", "chunk_label": True},
                    {"chunk_id": 1, "chunk_content": "more text", "chunk_label": False},
                ],
            },
            "789-012": {
                "doc_b.docx": [
                    {"chunk_id": 0, "chunk_content": "another chunk", "chunk_label": True},
                ],
            },
        }
    }


def test_value_to_filenames_and_unique_values(tmp_path):
    jf = tmp_path / "idx.json"
    _write(jf, _sample())
    scan = scan_inverted_index(jf)
    assert scan.error is None
    assert scan.not_a_dict is False
    assert scan.value_to_filenames == {"123-456": ["doc_a.docx"], "789-012": ["doc_b.docx"]}
    assert scan.unique_values == 2


def test_field_completeness_all_present(tmp_path):
    jf = tmp_path / "idx.json"
    _write(jf, _sample())
    scan = scan_inverted_index(jf)
    assert scan.total_chunk_entries == 3
    assert scan.empty_chunk_id == 0
    assert scan.empty_chunk_content == 0
    assert scan.field_completeness_examples == []


def test_field_completeness_detects_empty_fields(tmp_path):
    jf = tmp_path / "idx.json"
    _write(jf, {
        "SIT": {
            "v1": {
                "f.docx": [
                    {"chunk_id": None, "chunk_content": "ok"},
                    {"chunk_id": 1, "chunk_content": ""},
                    {"chunk_id": "", "chunk_content": None},
                ]
            }
        }
    })
    scan = scan_inverted_index(jf)
    assert scan.total_chunk_entries == 3
    assert scan.empty_chunk_id == 2
    assert scan.empty_chunk_content == 2
    assert len(scan.field_completeness_examples) == 3
    assert "value='v1' file='f.docx'" in scan.field_completeness_examples[0]


def test_polarity_key_detected_anywhere(tmp_path):
    jf = tmp_path / "idx.json"
    _write(jf, {
        "SIT": {
            "v1": {
                "f.docx": [
                    {"chunk_id": 0, "chunk_content": "x", "polarity": "Positive"},
                    {"chunk_id": 1, "chunk_content": "y", "chunk_label": True},
                ]
            }
        }
    })
    scan = scan_inverted_index(jf)
    assert scan.polarity_key_count == 1
    assert len(scan.polarity_key_paths) == 1


def test_no_polarity_key_passes_clean(tmp_path):
    jf = tmp_path / "idx.json"
    _write(jf, _sample())
    scan = scan_inverted_index(jf)
    assert scan.polarity_key_count == 0
    assert scan.polarity_key_paths == []


def test_non_dict_filemap_value_excluded_from_lookup(tmp_path):
    # A "value" whose own contents are a list, not a dict, must never be
    # added to value_to_filenames - matches the original json.load()-based
    # code's isinstance(filemap, dict) guard exactly.
    jf = tmp_path / "idx.json"
    _write(jf, {
        "SIT": {
            "v1": ["not", "a", "dict"],
            "v2": {"f.docx": [{"chunk_id": 0, "chunk_content": "x"}]},
        }
    })
    scan = scan_inverted_index(jf)
    assert "v1" not in scan.value_to_filenames
    assert scan.value_to_filenames == {"v2": ["f.docx"]}


def test_top_level_not_a_dict(tmp_path):
    jf = tmp_path / "idx.json"
    jf.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    scan = scan_inverted_index(jf)
    assert scan.not_a_dict is True
    assert scan.error is None


def test_missing_file(tmp_path):
    scan = scan_inverted_index(tmp_path / "missing.json")
    assert scan.error is not None


def test_malformed_json(tmp_path):
    jf = tmp_path / "idx.json"
    jf.write_text('{"SIT": {"v1": {', encoding="utf-8")
    scan = scan_inverted_index(jf)
    assert scan.error is not None


def test_cached_by_path(tmp_path):
    jf = tmp_path / "idx.json"
    _write(jf, _sample())
    first = scan_inverted_index(jf)
    _write(jf, {"SIT": {}})
    second = scan_inverted_index(jf)
    assert second is first
    clear_scan_cache()
    third = scan_inverted_index(jf)
    assert third.unique_values == 0


def test_large_file_scan_uses_bounded_memory(tmp_path):
    """Regression test for the second real production OOM contributor:
    sit_inverted_index.json embeds full chunk text and was previously
    json.load()'d in full by two separate check modules. Peak memory while
    scanning must stay a small, bounded fraction of the file size."""
    jf = tmp_path / "large_idx.json"
    n = 8000
    filler = "x" * 3000  # mimic real chunk_content text
    with jf.open("w", encoding="utf-8") as f:
        f.write('{"SIT": {')
        for i in range(n):
            if i:
                f.write(",")
            value = f'"v{i}"'
            chunk = json.dumps([{"chunk_id": 0, "chunk_content": filler, "chunk_label": True}])
            f.write(f'{value}: {{"doc_{i}.docx": {chunk}}}')
        f.write("}}")

    file_size = jf.stat().st_size
    assert file_size > 20_000_000

    clear_scan_cache()
    tracemalloc.start()
    scan = scan_inverted_index(jf)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert scan.error is None
    assert scan.unique_values == n
    assert scan.total_chunk_entries == n
    assert peak < file_size * 0.15, (
        f"peak traced memory {peak} was not small relative to file size {file_size} - "
        "scan_inverted_index() may be holding the full file in memory instead of streaming it")
