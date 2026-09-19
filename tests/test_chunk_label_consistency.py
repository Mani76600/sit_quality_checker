import json

from qc.checks.metadata_checks import _check_chunk_label_consistency
from qc.context import FolderSet, PolarityOutputs
from qc.models import Status


def _write_chunk(path, snippets):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"value": [{"snippets": snippets}]}), encoding="utf-8")


def _polarity(tmp_path) -> PolarityOutputs:
    return FolderSet("Agreements", tmp_path).positive


def _opts():
    return {"exhaustive_chunk_scan": True, "chunk_sample_size": 200}


def test_positive_sit_found_true_implies_label_true_passes(tmp_path):
    polarity = _polarity(tmp_path)
    _write_chunk(polarity.chunks / "a.json", [{"index": 0, "label": True, "sit_found": True}])
    results = _check_chunk_label_consistency(FolderSet("Agreements", tmp_path), "Positive", polarity, _opts())
    consistency = [r for r in results if "implies label" in r.title]
    assert consistency and consistency[0].status == Status.PASS


def test_positive_sit_found_true_with_label_false_fails(tmp_path):
    # This exact combination never occurred in 10,000+ real chunk files
    # checked (South Africa, Sweden, Taiwan) - it's a genuine violation.
    polarity = _polarity(tmp_path)
    _write_chunk(polarity.chunks / "bad.json", [{"index": 0, "label": False, "sit_found": True}])
    results = _check_chunk_label_consistency(FolderSet("Agreements", tmp_path), "Positive", polarity, _opts())
    consistency = [r for r in results if "implies label" in r.title]
    assert consistency and consistency[0].status == Status.FAIL
    assert "bad.json" in consistency[0].detail


def test_negative_sit_found_true_requires_label_false(tmp_path):
    polarity = FolderSet("Agreements", tmp_path).negative
    _write_chunk(polarity.chunks / "hardneg.json", [{"index": 0, "label": False, "sit_found": True}])
    results = _check_chunk_label_consistency(FolderSet("Agreements", tmp_path), "Negative", polarity, _opts())
    consistency = [r for r in results if "implies label" in r.title]
    assert consistency and consistency[0].status == Status.PASS


def test_missing_label_field_is_detected(tmp_path):
    polarity = _polarity(tmp_path)
    _write_chunk(polarity.chunks / "nolabel.json", [{"index": 0, "sit_found": True}])
    results = _check_chunk_label_consistency(FolderSet("Agreements", tmp_path), "Positive", polarity, _opts())
    missing = [r for r in results if "has a 'label' field" in r.title]
    assert missing and missing[0].status == Status.FAIL
    assert "nolabel.json" in missing[0].detail


def test_multiple_sit_found_true_reported_as_info_not_failure(tmp_path):
    polarity = _polarity(tmp_path)
    _write_chunk(polarity.chunks / "multi.json", [
        {"index": 0, "label": True, "sit_found": True},
        {"index": 1, "label": True, "sit_found": True},
    ])
    results = _check_chunk_label_consistency(FolderSet("Agreements", tmp_path), "Positive", polarity, _opts())
    multi = [r for r in results if "more than one sit_found" in r.title]
    assert multi and multi[0].status == Status.INFO
    assert "multi.json" in multi[0].detail
    # and the consistency check itself still passes - multiple matching
    # snippets is not itself a violation
    consistency = [r for r in results if "implies label" in r.title]
    assert consistency[0].status == Status.PASS
