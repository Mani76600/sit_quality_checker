import json

from qc.checks.context_normalized import (
    _check_confidence_is_numeric,
    _check_field_completeness,
    scan_file,
)
from qc.models import Status


def _write(path, records) -> None:
    path.write_text(json.dumps(records), encoding="utf-8")


def test_confidence_entirely_missing_from_schema_is_a_hard_fail(tmp_path):
    jf = tmp_path / "sit.json"
    _write(jf, [{"value": "1"}, {"value": "2"}])  # no confidence field anywhere
    scan = scan_file(jf)
    results = _check_field_completeness(jf, scan, "Agreements")
    hard_fail = [r for r in results if r.status == Status.FAIL and "confidence" in r.title.lower()]
    assert hard_fail, "expected a hard FAIL for confidence missing from the schema entirely"

    info_note = [r for r in results if r.status == Status.INFO]
    for r in info_note:
        assert "confidence" not in r.detail  # confidence must not also appear in the soft schema-note


def test_other_missing_fields_still_only_info_not_fail(tmp_path):
    # document_name/doc_id being entirely absent is a known real schema
    # variant (confirmed on the Sweden sample) - must stay INFO, unaffected
    # by the new confidence-specific hard-FAIL rule.
    jf = tmp_path / "sit.json"
    _write(jf, [{"value": "1", "confidence": "85"}, {"value": "2", "confidence": "75"}])
    scan = scan_file(jf)
    results = _check_field_completeness(jf, scan, "Agreements")
    assert not any(r.status == Status.FAIL for r in results)
    info_note = [r for r in results if r.status == Status.INFO]
    assert info_note and "document_name" in info_note[0].detail


def test_check_confidence_is_numeric_pass(tmp_path):
    jf = tmp_path / "sit.json"
    _write(jf, [{"value": "1", "confidence": "85"}, {"value": "2", "confidence": "75"}])
    scan = scan_file(jf)
    results = _check_confidence_is_numeric(jf, scan, "Agreements")
    assert len(results) == 1
    assert results[0].status == Status.PASS


def test_check_confidence_is_numeric_fail_on_free_text(tmp_path):
    jf = tmp_path / "sit.json"
    _write(jf, [{"value": "1", "confidence": "high"}, {"value": "2", "confidence": "85"}])
    scan = scan_file(jf)
    results = _check_confidence_is_numeric(jf, scan, "Agreements")
    assert len(results) == 1
    assert results[0].status == Status.FAIL
    assert "high" in results[0].detail


def test_check_confidence_is_numeric_silent_when_field_absent(tmp_path):
    # Absence is reported by _check_field_completeness's hard FAIL instead -
    # this check must not also emit a contradictory PASS/FAIL of its own.
    jf = tmp_path / "sit.json"
    _write(jf, [{"value": "1"}, {"value": "2"}])
    scan = scan_file(jf)
    results = _check_confidence_is_numeric(jf, scan, "Agreements")
    assert results == []
