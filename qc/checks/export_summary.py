"""Items 5, 6, 7: export_summary.json ground-truth/count reconciliation, and
corpus.jsonl / combined_metadata.jsonl count + label-distribution matching.

Run against both the Agreements export_summary.json (whole-run counts) and
the Disagreements export_summary.json (its own, separately reported counts -
Disagreements is a partitioned subset, not the same totals as Agreements).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from qc.context import FolderSet, VersionContext
from qc.jsonio import read_json, read_jsonl_cached
from qc.models import CheckResult, Status
from qc.registry import register
from qc.checks.context_normalized import scan_file as _scan_context_normalized_file

CATEGORY_5 = "5. export_summary.json ground truth"
CATEGORY_67 = "6-7. corpus.jsonl / combined_metadata.jsonl counts"


def _get_counts(export_summary: dict) -> dict:
    counts = export_summary.get("counts", {}) if isinstance(export_summary, dict) else {}
    return {
        "positive": counts.get("positive", counts.get("Positive")),
        "negative": counts.get("negative", counts.get("Negative")),
        "total": counts.get("total", counts.get("Total")),
    }


def _get_ground_truth(export_summary: dict) -> tuple[int | None, int | None, int | None]:
    nc = export_summary.get("normalized_context", {}) if isinstance(export_summary, dict) else {}
    gt = nc.get("ground_truth", {}) if isinstance(nc, dict) else {}
    return gt.get("true"), gt.get("false"), nc.get("records")


def _label_value(record: dict) -> str | None:
    """Normalize whichever polarity-ish field a record carries to 'positive'/'negative'."""
    for key in ("label", "polarity", "ground_truth"):
        v = record.get(key)
        if isinstance(v, str) and v.strip().lower() in ("positive", "negative", "true", "false"):
            v = v.strip().lower()
            return {"true": "positive", "false": "negative"}.get(v, v)
        if isinstance(v, bool):
            return "positive" if v else "negative"
    return None


def _scan_jsonl(path: Path) -> tuple[int, Counter, list[str]]:
    """(line_count, label_distribution, parse_errors), from the shared cached
    parse - corpus.jsonl/combined_metadata.jsonl are also read by
    scenario_id_checks.py, so read_jsonl_cached() ensures each file is only
    parsed once per run rather than once per check module."""
    records, errors = read_jsonl_cached(path)
    count = len(records)
    labels: Counter = Counter()
    for rec in records:
        lbl = _label_value(rec)
        if lbl:
            labels[lbl] += 1
    return count, labels, errors


@register(category=CATEGORY_5)
def check_export_summary_ground_truth(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        results.extend(_check_ground_truth_one(fs))
    return results


def _check_ground_truth_one(fs: FolderSet) -> list[CheckResult]:
    scope = fs.name
    results: list[CheckResult] = []

    data, err = read_json(fs.export_summary)
    if err:
        results.append(CheckResult(Status.FAIL, CATEGORY_5, "5",
            "export_summary.json readable", err, scope,
            "Regenerate export_summary.json - it is missing or malformed."))
        return results

    counts = _get_counts(data)
    pos, neg, total = counts["positive"], counts["negative"], counts["total"]

    if pos is not None and neg is not None and total is not None:
        if pos + neg == total:
            results.append(CheckResult(Status.PASS, CATEGORY_5, "5",
                "counts.total == positive + negative",
                f"{pos} + {neg} == {total}", scope))
        else:
            results.append(CheckResult(Status.FAIL, CATEGORY_5, "5",
                "counts.total == positive + negative",
                f"{pos} + {neg} != {total}", scope,
                "Recompute counts.total in export_summary.json."))
    else:
        results.append(CheckResult(Status.WARN, CATEGORY_5, "5",
            "counts block complete", f"counts: {counts}", scope,
            "export_summary.json is missing one of counts.positive/negative/total."))

    gt_true, gt_false, nc_records = _get_ground_truth(data)
    if gt_true is not None and pos is not None:
        if gt_true == pos:
            results.append(CheckResult(Status.PASS, CATEGORY_5, "5",
                "normalized_context.ground_truth.true == counts.positive",
                f"{gt_true} == {pos}", scope))
        else:
            results.append(CheckResult(Status.FAIL, CATEGORY_5, "5",
                "normalized_context.ground_truth.true == counts.positive",
                f"{gt_true} != {pos} (delta {gt_true - pos:+d})", scope,
                "Reconcile normalized_context.ground_truth.true with counts.positive in "
                "export_summary.json - they must describe the same document set."))
    if gt_false is not None and neg is not None:
        if gt_false == neg:
            results.append(CheckResult(Status.PASS, CATEGORY_5, "5",
                "normalized_context.ground_truth.false == counts.negative",
                f"{gt_false} == {neg}", scope))
        else:
            results.append(CheckResult(Status.FAIL, CATEGORY_5, "5",
                "normalized_context.ground_truth.false == counts.negative",
                f"{gt_false} != {neg} (delta {gt_false - neg:+d})", scope,
                "Reconcile normalized_context.ground_truth.false with counts.negative in "
                "export_summary.json - they must describe the same document set."))

    if gt_true is not None and gt_false is not None and nc_records is not None:
        expected = gt_true + gt_false
        if expected == nc_records:
            results.append(CheckResult(Status.PASS, CATEGORY_5, "5",
                "normalized_context.records == ground_truth.true + ground_truth.false",
                f"{gt_true} + {gt_false} == {nc_records}", scope))
        else:
            results.append(CheckResult(Status.FAIL, CATEGORY_5, "5",
                "normalized_context.records == ground_truth.true + ground_truth.false",
                f"{gt_true} + {gt_false} != {nc_records}", scope,
                "export_summary.json's own self-reported record/ground-truth numbers "
                "disagree with each other - regenerate the summary."))

    # Cross-check against the *actual* context_output_normalized file content,
    # not just export_summary's self-reported numbers. Uses the SAME cached
    # streaming scan as context_normalized.py's own check - this file can be
    # 100-250MB+ in real samples, so it must never be fully materialized
    # (via json.load) twice; scan_file() streams it once and both checks
    # share the cached, small aggregate result.
    for jf in fs.context_output_normalized_files():
        scan = _scan_context_normalized_file(jf)
        if scan.error or scan.not_a_list:
            continue
        actual_count = scan.record_count
        actual_true = scan.ground_truth_true
        actual_false = scan.ground_truth_false
        if nc_records is not None:
            if actual_count == nc_records:
                results.append(CheckResult(Status.PASS, CATEGORY_5, "5",
                    f"export_summary.normalized_context.records matches actual {jf.name}",
                    f"{nc_records} == {actual_count}", scope))
            else:
                results.append(CheckResult(Status.FAIL, CATEGORY_5, "5",
                    f"export_summary.normalized_context.records matches actual {jf.name}",
                    f"export_summary claims {nc_records} records but {jf.name} actually "
                    f"contains {actual_count} (delta {actual_count - nc_records:+d})", scope,
                    "export_summary.json's record count has drifted from the real "
                    "context_output_normalized file - regenerate the summary from the "
                    "actual file contents."))
        if gt_true is not None and gt_false is not None:
            if actual_true == gt_true and actual_false == gt_false:
                results.append(CheckResult(Status.PASS, CATEGORY_5, "5",
                    f"export_summary.ground_truth matches actual {jf.name} true/false split",
                    f"true={actual_true}, false={actual_false}", scope))
            else:
                results.append(CheckResult(Status.FAIL, CATEGORY_5, "5",
                    f"export_summary.ground_truth matches actual {jf.name} true/false split",
                    f"export_summary claims true={gt_true}/false={gt_false} but {jf.name} "
                    f"actually has true={actual_true}/false={actual_false}", scope,
                    "export_summary.json's ground_truth split has drifted from the real "
                    "context_output_normalized file - regenerate the summary."))

    return results


@register(category=CATEGORY_67)
def check_corpus_and_combined_metadata_counts(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        results.extend(_check_counts_one(fs))
    return results


def _check_counts_one(fs: FolderSet) -> list[CheckResult]:
    scope = fs.name
    results: list[CheckResult] = []

    data, err = read_json(fs.export_summary)
    if err:
        return results  # already reported by the item-5 check
    counts = _get_counts(data)
    pos, neg, total = counts["positive"], counts["negative"], counts["total"]

    for label, path, item_ref in (
        ("corpus.jsonl", fs.corpus, "6"),
        ("combined_metadata.jsonl", fs.combined_metadata, "7"),
    ):
        if not path.exists():
            continue  # reported by structure.py
        line_count, labels, errors = _scan_jsonl(path)
        for e in errors[:10]:
            results.append(CheckResult(Status.FAIL, CATEGORY_67, item_ref,
                f"{label}: valid JSON per line", e, scope,
                f"Fix or regenerate the malformed line(s) in {path}."))

        if total is not None:
            if line_count >= total:
                results.append(CheckResult(Status.PASS, CATEGORY_67, item_ref,
                    f"{label}: record count >= export_summary total",
                    f"{line_count} lines >= {total} total", scope))
            else:
                results.append(CheckResult(Status.FAIL, CATEGORY_67, item_ref,
                    f"{label}: record count >= export_summary total",
                    f"{line_count} lines < {total} total (missing {total - line_count})", scope,
                    f"{path} has fewer rows than export_summary.json's counts.total - "
                    "some documents were dropped before export."))

        if pos is not None and neg is not None and labels:
            lp, ln = labels.get("positive", 0), labels.get("negative", 0)
            if lp == pos and ln == neg:
                results.append(CheckResult(Status.PASS, CATEGORY_67, item_ref,
                    f"{label}: positive/negative label distribution matches counts",
                    f"positive={lp}, negative={ln}", scope))
            else:
                results.append(CheckResult(Status.FAIL, CATEGORY_67, item_ref,
                    f"{label}: positive/negative label distribution matches counts",
                    f"file has positive={lp}/negative={ln}, export_summary counts "
                    f"positive={pos}/negative={neg}", scope,
                    f"Reconcile the label/polarity distribution in {path} with "
                    "export_summary.json's counts."))

    return results
