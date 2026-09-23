import json
import tracemalloc

import pytest

from qc.checks.context_normalized import clear_scan_cache, scan_file


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_scan_cache()
    yield
    clear_scan_cache()


def _write(path, records) -> None:
    path.write_text(json.dumps(records), encoding="utf-8")


def test_basic_counts(tmp_path):
    jf = tmp_path / "sit.json"
    _write(jf, [
        {"value": "1", "ground_truth": "true", "sit_category": "easy positive",
         "sit_name": "X", "language": "en"},
        {"value": "2", "ground_truth": "false", "sit_category": "easy negative",
         "sit_name": "X", "language": "en"},
        {"value": "3", "ground_truth": "false", "sit_category": "hard negative",
         "sit_name": "X", "language": "en"},
    ])
    scan = scan_file(jf)
    assert scan.error is None
    assert scan.not_a_list is False
    assert scan.record_count == 3
    assert scan.ground_truth_true == 1
    assert scan.ground_truth_false == 2


def test_undetermined_and_missing_sit_category_detected(tmp_path):
    jf = tmp_path / "sit.json"
    _write(jf, [
        {"value": "1", "sit_category": "easy positive"},
        {"value": "2", "sit_category": "undetermined"},
        {"value": "3"},  # missing entirely
    ])
    scan = scan_file(jf)
    assert scan.bad_sit_category_indices == [1, 2]


def test_field_never_present_is_not_counted_as_empty_field_here(tmp_path):
    # A field that's entirely absent from the file's schema (e.g. no record
    # anywhere has "document_name") is a schema difference, not a defect -
    # scan_file() still records the raw empty-count (document_name missing
    # on every record), but observed_fields correctly excludes it so the
    # check layer (not scan_file itself) can distinguish the two cases.
    jf = tmp_path / "sit.json"
    _write(jf, [{"value": "1"}, {"value": "2"}])
    scan = scan_file(jf)
    assert "document_name" not in scan.observed_fields
    assert scan.field_empty_counts.get("document_name") == 2  # both "empty" by rec.get()
    assert scan.record_count == 2


def test_field_present_in_schema_but_empty_on_some_records(tmp_path):
    jf = tmp_path / "sit.json"
    _write(jf, [
        {"value": "1", "domain": "Finance"},
        {"value": "2", "domain": ""},
        {"value": "3", "domain": None},
    ])
    scan = scan_file(jf)
    assert "domain" in scan.observed_fields
    assert scan.field_empty_counts["domain"] == 2


def test_context_window_missing_vs_empty_subfield(tmp_path):
    jf = tmp_path / "sit.json"
    _write(jf, [
        {"value": "1", "context_100": {"text": "hello", "language": "en"}},
        {"value": "2", "context_100": {"text": "", "language": "en"}},
        {"value": "3"},  # no context_100 key at all
    ])
    scan = scan_file(jf)
    assert scan.field_empty_counts["context_100.text"] == 1
    assert scan.field_empty_counts["context_100"] == 1  # the missing-window record


def test_confidence_numeric_string_is_accepted(tmp_path):
    jf = tmp_path / "sit.json"
    _write(jf, [
        {"value": "1", "confidence": "85"},
        {"value": "2", "confidence": "75"},
        {"value": "3", "confidence": 96},
    ])
    scan = scan_file(jf)
    assert scan.bad_confidence_count == 0
    assert scan.bad_confidence_examples == []


def test_confidence_free_text_is_flagged(tmp_path):
    jf = tmp_path / "sit.json"
    _write(jf, [
        {"value": "1", "confidence": "85"},
        {"value": "2", "confidence": "high"},
        {"value": "3", "confidence": "N/A"},
    ])
    scan = scan_file(jf)
    assert scan.bad_confidence_count == 2
    indices = [i for i, _ in scan.bad_confidence_examples]
    assert indices == [1, 2]


def test_confidence_empty_is_not_double_counted_as_bad_format(tmp_path):
    # An empty/missing confidence is already covered by field_empty_counts -
    # the numeric-format check should only fire when a value IS present.
    jf = tmp_path / "sit.json"
    _write(jf, [{"value": "1", "confidence": ""}, {"value": "2"}])
    scan = scan_file(jf)
    assert scan.bad_confidence_count == 0


def test_language_counter_and_examples(tmp_path):
    jf = tmp_path / "sit.json"
    _write(jf, [
        {"value": "1", "language": "sv", "ground_truth": "true", "document_name": "a.docx"},
        {"value": "2", "language": "sv", "ground_truth": "true", "document_name": "b.docx"},
        {"value": "3", "language": "no", "ground_truth": "false", "document_name": "c.docx"},
    ])
    scan = scan_file(jf)
    assert scan.lang_counter == {"sv": 2, "no": 1}
    assert scan.lang_examples["no"][0]["document_name"] == "c.docx"


def test_non_list_top_level_is_flagged(tmp_path):
    jf = tmp_path / "sit.json"
    jf.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    scan = scan_file(jf)
    assert scan.not_a_list is True
    assert scan.error is None


def test_malformed_json_is_flagged_as_error(tmp_path):
    jf = tmp_path / "sit.json"
    jf.write_text('[{"value": "1"}, {"value": ', encoding="utf-8")  # truncated
    scan = scan_file(jf)
    assert scan.error is not None


def test_missing_file_is_flagged_as_error(tmp_path):
    scan = scan_file(tmp_path / "does_not_exist.json")
    assert scan.error is not None


def test_scan_is_cached_by_path(tmp_path):
    jf = tmp_path / "sit.json"
    _write(jf, [{"value": "1", "ground_truth": "true"}])
    first = scan_file(jf)
    jf.write_text(json.dumps([{"value": "1"}, {"value": "2"}]), encoding="utf-8")
    second = scan_file(jf)  # should still be the cached first result
    assert second is first
    assert second.record_count == 1
    clear_scan_cache()
    third = scan_file(jf)
    assert third.record_count == 2


def test_large_file_scan_uses_bounded_memory_not_proportional_to_file_size(tmp_path):
    """Regression test for the real production OOM: a naive json.load()
    holds the whole parsed structure (and, transiently, even more) in
    memory. scan_file() must use memory that stays roughly constant
    regardless of record count, not memory proportional to the file's
    record count/size - confirmed by measuring peak allocations while
    scanning a file with a non-trivial number of records with sizeable
    text fields (mimicking real context_100/500/... windows)."""
    jf = tmp_path / "large_sit.json"
    n = 20000
    filler = "x" * 2000  # mimic a real context window's text field
    with jf.open("w", encoding="utf-8") as f:
        f.write("[")
        for i in range(n):
            if i:
                f.write(",")
            rec = {
                "value": str(i), "ground_truth": "true" if i % 2 == 0 else "false",
                "sit_category": "easy positive", "language": "en",
                "context_2000": {"text": filler, "language": "en"},
            }
            f.write(json.dumps(rec))
        f.write("]")

    file_size = jf.stat().st_size
    assert file_size > 30_000_000  # sanity check this is a genuinely large file (~30MB+)

    clear_scan_cache()
    tracemalloc.start()
    scan = scan_file(jf)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert scan.error is None
    assert scan.record_count == n

    # A full json.load() of this file would hold a Python object graph
    # multiple times the file's byte size (every one of the 20,000 filler
    # strings + dict/list overhead retained simultaneously). Streaming
    # should use only a small, bounded fraction of the file size, since at
    # most one record is ever fully materialized at a time.
    assert peak < file_size * 0.1, (
        f"peak traced memory {peak} was not small relative to file size {file_size} - "
        "scan_file() may be holding the full file in memory instead of streaming it")
