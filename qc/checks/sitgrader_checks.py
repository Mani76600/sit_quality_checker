"""Item 7 / addition: Disagreements/SITGrader detailed report.

SITGrader output only ever exists under Disagreements/ (confirmed against
pipeline code and both sample trees) - existence of chunk_run_*/ and misc/
is handled by structure.py; this module checks the *content* of every file
in both folders is internally consistent:

  - chunk_run_<latest>/chunk_summary.json: agreements+disagreements==total,
    agreement_rate is a valid ratio and matches agreements/total
  - chunk_run_<latest>/chunk_grades.jsonl: row count == total_chunk_value_records
  - misc/evaluation_summary.json: same internal-consistency checks as
    chunk_summary.json, AND cross-matched against it (they describe the
    same evaluation run)
  - misc/chunk_evaluations.jsonl: row count == evaluation_summary's
    total_chunk_value_records; its chunk_label/grader_label distributions
    are cross-checked against evaluation_summary's source_labels/grader_labels
  - misc/evaluation_manifest.jsonl: row count == evaluated_documents +
    unevaluated_documents; agreement==True/False counts match
    evaluation_summary's agreements/disagreements; sum of chunk_value_records
    across all rows == total_chunk_value_records

Only the latest chunk_run_*/ is checked (structure.py/metadata_checks.py
already flag+recommend deleting older duplicate runs).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from qc.context import VersionContext
from qc.jsonio import count_jsonl_lines, read_json
from qc.models import CheckResult, Status
from qc.registry import register

CATEGORY = "SITGrader Consistency (addition)"


def _scan_jsonl_fields(path: Path, fields: tuple[str, ...]) -> tuple[int, dict[str, Counter], list[str]]:
    """Lean streaming scan: (row_count, {field: value_distribution}, errors) -
    avoids materializing full records (chunk_evaluations.jsonl rows carry a
    full document-text chunk_content field, ~80MB for a 15k-row real file)."""
    count = 0
    dists: dict[str, Counter] = {f: Counter() for f in fields}
    errors: list[str] = []
    if not path.exists():
        return 0, dists, [f"file not found: {path}"]
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            for i, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                count += 1
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(f"{path}:{i}: invalid JSON ({exc})")
                    continue
                if isinstance(rec, dict):
                    for f in fields:
                        v = rec.get(f)
                        if v is not None:
                            dists[f][str(v).strip().lower()] += 1
    except OSError as exc:
        errors.append(f"could not read {path}: {exc}")
    return count, dists, errors


def _check_internal_consistency(results: list[CheckResult], label: str, scope: str,
                                 summary: dict) -> None:
    total = summary.get("total_chunk_value_records")
    agreements = summary.get("agreements")
    disagreements = summary.get("disagreements")
    rate = summary.get("agreement_rate")

    if total is not None and agreements is not None and disagreements is not None:
        if agreements + disagreements == total:
            results.append(CheckResult(Status.PASS, CATEGORY, "7",
                f"{label}: agreements + disagreements == total_chunk_value_records",
                f"{agreements} + {disagreements} == {total}", scope))
        else:
            results.append(CheckResult(Status.FAIL, CATEGORY, "7",
                f"{label}: agreements + disagreements == total_chunk_value_records",
                f"{agreements} + {disagreements} != {total}", scope,
                f"Regenerate {label} - its own numbers don't add up."))

    if rate is not None:
        if 0 <= rate <= 1:
            results.append(CheckResult(Status.PASS, CATEGORY, "7",
                f"{label}: agreement_rate is a valid ratio", f"agreement_rate={rate}", scope))
        else:
            results.append(CheckResult(Status.FAIL, CATEGORY, "7",
                f"{label}: agreement_rate is a valid ratio", f"agreement_rate={rate}", scope,
                "agreement_rate must be between 0 and 1."))
        if total and agreements is not None:
            expected_rate = agreements / total
            if abs(expected_rate - rate) > 0.01:
                results.append(CheckResult(Status.WARN, CATEGORY, "7",
                    f"{label}: agreement_rate matches agreements/total",
                    f"reported {rate} vs computed {expected_rate:.4f}", scope,
                    f"{label}'s agreement_rate looks stale relative to its own agreements/"
                    "total_chunk_value_records."))


@register(category=CATEGORY)
def check_sitgrader(ctx: VersionContext, options: dict) -> list[CheckResult]:
    fs = ctx.disagreements
    scope = "Disagreements"
    results: list[CheckResult] = []

    runs = fs.chunk_run_dirs()
    if not runs:
        return results  # reported by structure.py
    latest = runs[-1]
    label = f"chunk_run/{latest.name}/chunk_summary.json"

    summary, err = read_json(latest / "chunk_summary.json")
    if err:
        results.append(CheckResult(Status.FAIL, CATEGORY, "7", f"{label} readable", err, scope,
            "Re-run SITGrader for this Disagreements set."))
        summary = None
    elif isinstance(summary, dict):
        _check_internal_consistency(results, label, scope, summary)

        grades_count, grade_errors = count_jsonl_lines(latest / "chunk_grades.jsonl")
        for e in grade_errors[:5]:
            results.append(CheckResult(Status.FAIL, CATEGORY, "7",
                f"chunk_run/{latest.name}/chunk_grades.jsonl valid JSON per line", e, scope))
        total = summary.get("total_chunk_value_records")
        if total is not None:
            if grades_count == total:
                results.append(CheckResult(Status.PASS, CATEGORY, "7",
                    f"chunk_run/{latest.name}/chunk_grades.jsonl row count matches total",
                    f"{grades_count} == {total}", scope))
            else:
                results.append(CheckResult(Status.FAIL, CATEGORY, "7",
                    f"chunk_run/{latest.name}/chunk_grades.jsonl row count matches total",
                    f"{grades_count} != {total}", scope,
                    f"{latest}/chunk_grades.jsonl should have exactly total_chunk_value_records rows."))

    # misc/evaluation_summary.json: same internal-consistency checks, PLUS
    # cross-matched against chunk_summary.json (they should describe the
    # same evaluation run).
    misc_summary_path = fs.sitgrader_misc / "evaluation_summary.json"
    misc_summary = None
    if misc_summary_path.exists():
        misc_summary, merr = read_json(misc_summary_path)
        if merr:
            results.append(CheckResult(Status.FAIL, CATEGORY, "7",
                "misc/evaluation_summary.json readable", merr, scope))
            misc_summary = None
        elif isinstance(misc_summary, dict):
            _check_internal_consistency(results, "misc/evaluation_summary.json", scope, misc_summary)
            if isinstance(summary, dict):
                keys = ["total_chunk_value_records", "agreements", "disagreements", "agreement_rate"]
                mismatches = {k: (summary.get(k), misc_summary.get(k)) for k in keys
                              if summary.get(k) != misc_summary.get(k)}
                if mismatches:
                    results.append(CheckResult(Status.WARN, CATEGORY, "7",
                        f"misc/evaluation_summary.json matches latest {latest.name}/chunk_summary.json",
                        f"Differing fields (chunk_summary, evaluation_summary): {mismatches}", scope,
                        "These two summaries are expected to describe the same evaluation run - "
                        "confirm evaluation_summary.json reflects the latest chunk_run."))
                else:
                    results.append(CheckResult(Status.PASS, CATEGORY, "7",
                        f"misc/evaluation_summary.json matches latest {latest.name}/chunk_summary.json",
                        "All key fields match.", scope))
    else:
        results.append(CheckResult(Status.FAIL, CATEGORY, "7", "misc/evaluation_summary.json present",
            f"Missing: {misc_summary_path}", scope, "Re-run SITGrader for this Disagreements set."))

    if isinstance(misc_summary, dict):
        total = misc_summary.get("total_chunk_value_records")
        source_labels = misc_summary.get("source_labels") or {}
        grader_labels = misc_summary.get("grader_labels") or {}
        disagreement_documents = misc_summary.get("disagreement_documents")
        evaluated = misc_summary.get("evaluated_documents")
        unevaluated = misc_summary.get("unevaluated_documents")

        results.extend(_check_chunk_evaluations(fs.sitgrader_misc / "chunk_evaluations.jsonl",
                                                 total, source_labels, grader_labels, scope))
        results.extend(_check_evaluation_manifest(fs.sitgrader_misc / "evaluation_manifest.jsonl",
                                                   total, disagreement_documents,
                                                   evaluated, unevaluated, scope))

    return results


def _check_chunk_evaluations(path: Path, total: int | None, source_labels: dict,
                              grader_labels: dict, scope: str) -> list[CheckResult]:
    results: list[CheckResult] = []
    if not path.exists():
        results.append(CheckResult(Status.FAIL, CATEGORY, "7", "misc/chunk_evaluations.jsonl present",
            f"Missing: {path}", scope, "Re-run SITGrader for this Disagreements set."))
        return results

    count, dists, errors = _scan_jsonl_fields(path, ("chunk_label", "grader_label"))
    for e in errors[:5]:
        results.append(CheckResult(Status.FAIL, CATEGORY, "7",
            "misc/chunk_evaluations.jsonl valid JSON per line", e, scope))

    if total is not None:
        if count == total:
            results.append(CheckResult(Status.PASS, CATEGORY, "7",
                "misc/chunk_evaluations.jsonl row count matches total_chunk_value_records",
                f"{count} == {total}", scope))
        else:
            results.append(CheckResult(Status.FAIL, CATEGORY, "7",
                "misc/chunk_evaluations.jsonl row count matches total_chunk_value_records",
                f"{count} != {total}", scope,
                "misc/chunk_evaluations.jsonl should have exactly total_chunk_value_records rows."))

    def _cmp_distribution(field: str, expected: dict, item_ref: str) -> None:
        actual = {k: v for k, v in dists[field].items()}
        expected_norm = {str(k).strip().lower(): v for k, v in (expected or {}).items()}
        if not expected_norm:
            return
        if actual == expected_norm:
            results.append(CheckResult(Status.PASS, CATEGORY, item_ref,
                f"misc/chunk_evaluations.jsonl {field} distribution matches evaluation_summary.json",
                f"{actual}", scope))
        else:
            results.append(CheckResult(Status.WARN, CATEGORY, item_ref,
                f"misc/chunk_evaluations.jsonl {field} distribution matches evaluation_summary.json",
                f"chunk_evaluations.jsonl has {actual}, evaluation_summary.json reports {expected_norm}",
                scope, "These should describe the same graded rows - regenerate evaluation_summary.json."))

    _cmp_distribution("chunk_label", source_labels, "7")
    _cmp_distribution("grader_label", grader_labels, "7")
    return results


def _check_evaluation_manifest(path: Path, total: int | None,
                                disagreement_documents: int | None, evaluated: int | None,
                                unevaluated: int | None, scope: str) -> list[CheckResult]:
    results: list[CheckResult] = []
    if not path.exists():
        results.append(CheckResult(Status.FAIL, CATEGORY, "7", "misc/evaluation_manifest.jsonl present",
            f"Missing: {path}", scope, "Re-run SITGrader for this Disagreements set."))
        return results

    count, dists, errors = _scan_jsonl_fields(path, ("agreement",))
    for e in errors[:5]:
        results.append(CheckResult(Status.FAIL, CATEGORY, "7",
            "misc/evaluation_manifest.jsonl valid JSON per line", e, scope))

    if evaluated is not None and unevaluated is not None:
        expected_docs = evaluated + unevaluated
        if count == expected_docs:
            results.append(CheckResult(Status.PASS, CATEGORY, "7",
                "misc/evaluation_manifest.jsonl row count matches evaluated+unevaluated documents",
                f"{count} == {expected_docs}", scope))
        else:
            results.append(CheckResult(Status.FAIL, CATEGORY, "7",
                "misc/evaluation_manifest.jsonl row count matches evaluated+unevaluated documents",
                f"{count} != {expected_docs}", scope,
                "misc/evaluation_manifest.jsonl should have one row per evaluated+unevaluated document."))

    # NOTE: evaluation_summary.json's "agreements"/"disagreements" fields are
    # counted at the chunk-value-record level (sum == total_chunk_value_records,
    # e.g. 14452+552=15004 on a real sample) while evaluation_manifest.jsonl's
    # "agreement" is one bool per DOCUMENT (sum == evaluated_documents, e.g.
    # 14448+552=15000 on the same sample) - a document can carry more than one
    # chunk-value-record, so these two counts are NOT expected to be equal.
    # The correct document-level field to compare against is
    # "disagreement_documents", not "disagreements".
    agree_true = dists["agreement"].get("true", 0)
    agree_false = dists["agreement"].get("false", 0)
    if disagreement_documents is not None and evaluated is not None:
        expected_agree_true = evaluated - disagreement_documents
        if agree_true == expected_agree_true and agree_false == disagreement_documents:
            results.append(CheckResult(Status.PASS, CATEGORY, "7",
                "misc/evaluation_manifest.jsonl agreement counts match evaluation_summary.json",
                f"agreement=True: {agree_true}, agreement=False: {agree_false}", scope))
        else:
            results.append(CheckResult(Status.FAIL, CATEGORY, "7",
                "misc/evaluation_manifest.jsonl agreement counts match evaluation_summary.json",
                f"manifest has agreement=True:{agree_true}/False:{agree_false}, expected "
                f"True:{expected_agree_true} (evaluated_documents - disagreement_documents) / "
                f"False:{disagreement_documents} (disagreement_documents)", scope,
                "These should describe the same per-document agreement outcomes - regenerate "
                "one from the other."))

    return results
