import json

from qc.checks.sitgrader_checks import _check_evaluation_manifest
from qc.models import Status


def test_manifest_agreement_compared_against_document_level_field(tmp_path):
    """Regression test: evaluation_summary.json's "agreements"/"disagreements"
    are counted at the chunk-value-record level, while
    evaluation_manifest.jsonl's "agreement" is one bool per document - a real
    sample had agreements=14452/disagreements=552 (sum 15004, chunk-level)
    but manifest counts of True=14448/False=552 (sum 15000, document-level).
    The correct field to compare against is "disagreement_documents", not
    "disagreements" - comparing against the wrong one previously produced a
    FAIL on every real sample tested, even though the data was fine."""
    manifest = tmp_path / "evaluation_manifest.jsonl"
    rows = [{"document_name": f"doc{i}", "chunk_value_records": 1, "agreement": True}
            for i in range(14448)]
    rows += [{"document_name": f"doc{i}", "chunk_value_records": 1, "agreement": False}
             for i in range(552)]
    with manifest.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    results = _check_evaluation_manifest(
        manifest, total=15004, disagreement_documents=552,
        evaluated=15000, unevaluated=0, scope="Disagreements")

    agreement_results = [r for r in results if "agreement counts match" in r.title]
    assert agreement_results, "expected an agreement-count comparison result"
    assert agreement_results[0].status == Status.PASS, agreement_results[0].detail
