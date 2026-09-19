"""Item 5: corpus.jsonl (and combined_metadata.jsonl, same relevant fields)
per-record field completeness, plus the positive/negative value-field rule:
Positive records carry their planted value under sit_values (lookalike_values
empty); Negative records carry it under lookalike_values (sit_values empty).
Confirmed 1:1 across the full South Africa sample (4459 Positive / 9989
Negative records, zero exceptions either direction) before writing this check.

temperature_used, sit_values, lookalike_values, and has_table are excluded
from the generic "every field must be non-empty" sweep: temperature_used is
legitimately always null in real output, sit_values/lookalike_values are
conditionally empty by design (covered by the dedicated rule below instead),
and has_table is a boolean where False is a valid, non-"empty" value.

Uses the shared read_jsonl_cached() - corpus.jsonl is already parsed once by
export_summary.py's label-distribution check, so this reuses that same
in-memory parse rather than reading the file again.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from qc.context import FolderSet, VersionContext
from qc.jsonio import read_jsonl_cached
from qc.models import CheckResult, Status
from qc.registry import register

CATEGORY = "5. corpus.jsonl Field Completeness"

EXCLUDED_FIELDS = {"temperature_used", "sit_values", "lookalike_values", "has_table"}


def _is_empty(v: object) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _label_of(rec: dict) -> str:
    v = rec.get("label")
    if not v:
        v = rec.get("ground_truth")
    return str(v or "").strip().lower()


@register(category=CATEGORY)
def check_corpus_fields(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        results.extend(_check_file(fs, "corpus.jsonl", fs.corpus))
        results.extend(_check_file(fs, "combined_metadata.jsonl", fs.combined_metadata))
    return results


def _check_file(fs: FolderSet, name: str, path: Path) -> list[CheckResult]:
    scope = fs.name
    records, _errors = read_jsonl_cached(path)
    if not records:
        return []
    total = len(records)

    field_empty_counts: Counter = Counter()
    field_examples: dict[str, list[str]] = {}

    pos_missing_sit_values: list[str] = []
    pos_nonempty_lookalike: list[str] = []
    neg_nonempty_sit_values: list[str] = []
    neg_missing_lookalike: list[str] = []

    for rec in records:
        doc_id = str(rec.get("doc_id", "?"))
        for k, v in rec.items():
            if k in EXCLUDED_FIELDS:
                continue
            if _is_empty(v):
                field_empty_counts[k] += 1
                examples = field_examples.setdefault(k, [])
                if len(examples) < 5:
                    examples.append(doc_id)

        label = _label_of(rec)
        sv = rec.get("sit_values") or {}
        lv = rec.get("lookalike_values") or {}
        if label in ("positive", "true"):
            if not sv and len(pos_missing_sit_values) < 15:
                pos_missing_sit_values.append(doc_id)
            if lv and len(pos_nonempty_lookalike) < 15:
                pos_nonempty_lookalike.append(doc_id)
        elif label in ("negative", "false"):
            if sv and len(neg_nonempty_sit_values) < 15:
                neg_nonempty_sit_values.append(doc_id)
            if not lv and len(neg_missing_lookalike) < 15:
                neg_missing_lookalike.append(doc_id)

    results: list[CheckResult] = []
    if field_empty_counts:
        rows = [f"{k} ({n}/{total} empty, e.g. {field_examples[k][:3]})"
                for k, n in field_empty_counts.most_common()]
        results.append(CheckResult(Status.FAIL, CATEGORY, "5",
            f"{name}: every field is assigned (non-empty) per record",
            "; ".join(rows), scope,
            f"Backfill or regenerate the listed fields in {name} - they must never be "
            "empty/null."))
    else:
        results.append(CheckResult(Status.PASS, CATEGORY, "5",
            f"{name}: every field is assigned (non-empty) per record",
            f"Checked {total} record(s) across all fields, none empty.", scope))

    def add(bad: list[str], title: str, fix: str) -> None:
        if bad:
            results.append(CheckResult(Status.FAIL, CATEGORY, "5", f"{name}: {title}",
                f"{len(bad)} doc_id(s), e.g. {bad[:5]}", scope, fix))
        else:
            results.append(CheckResult(Status.PASS, CATEGORY, "5", f"{name}: {title}",
                f"Checked {total} record(s), no issues.", scope))

    add(pos_missing_sit_values, "Positive records have non-empty sit_values",
        f"Every Positive-labeled record in {name} must carry at least one value under "
        "sit_values.")
    add(pos_nonempty_lookalike, "Positive records have empty lookalike_values",
        f"A Positive-labeled record in {name} should not carry lookalike_values - that's "
        "a Negative-only field.")
    add(neg_nonempty_sit_values, "Negative records have empty sit_values",
        f"A Negative-labeled record in {name} should not carry sit_values - that's a "
        "Positive-only field.")
    add(neg_missing_lookalike, "Negative records have non-empty lookalike_values",
        f"Every Negative-labeled record in {name} must carry at least one value under "
        "lookalike_values.")

    return results
