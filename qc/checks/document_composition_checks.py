"""Four document-composition checks, all derived from the same canonical
per-document combined_metadata.jsonl file already used elsewhere in this
package (corpus_field_checks.py, export_summary.py):

1. Label distribution: easy/hard x positive/negative counts, from each
   record's sit_category ("easy positive", "hard positive", "easy negative",
   "hard negative" - confirmed verbatim in real combined_metadata.jsonl
   output).
2. Format distribution: document count per file_format (pdf, docx, ...),
   cross-checked against export_summary.json's own reported total - PASS
   unless that sum drifts from the total.
3. Business Context Balance: count distribution across the ontology
   dimensions domain, department, function, workflow, process, persona,
   role, document_type (all confirmed present verbatim in real output) -
   WARN if one value dominates a dimension, matching the same
   concentration-flag idea pipeline_metrics.py already uses for format/
   archetype diversity.
4. Effective hard diversity: whether any planted SIT value (sit_values for
   positive records, lookalike_values for negative records) is reused
   across more than one document - mirrors pipeline_metrics.py's own
   _hard_diversity formula (1.0 - reused/total), computed separately per
   polarity exactly like that function's by_polarity breakdown.
"""

from __future__ import annotations

import re
from collections import Counter

from qc.context import FolderSet, VersionContext
from qc.jsonio import read_json, read_jsonl_cached
from qc.models import CheckResult, Status
from qc.registry import register

CATEGORY_LABEL = "Label Distribution (Easy/Hard x Positive/Negative) (addition)"
CATEGORY_FORMAT = "Document Format Distribution (addition)"
CATEGORY_CONTEXT = "Business Context Balance (addition)"
CATEGORY_DIVERSITY = "Effective Hard Diversity (SIT Value Reuse) (addition)"
ITEM_REF = "addl"

_LABEL_BUCKETS = ("easy positive", "hard positive", "easy negative", "hard negative")
_CONTEXT_DIMENSIONS = (
    "domain", "department", "function", "workflow", "process",
    "persona", "role", "document_type",
)
_CONCENTRATION_WARN_THRESHOLD = 0.8  # a single value covering >80% of records
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _sit_category(rec: dict) -> str:
    value = str(rec.get("sit_category") or "").strip().lower()
    if value in _LABEL_BUCKETS:
        return value
    # Fall back to the nested sit_details shape if the flattened field is
    # absent (e.g. an older export layout).
    details = rec.get("sit_details")
    if isinstance(details, dict):
        for polarity_bucket in details.values():
            if not isinstance(polarity_bucket, dict):
                continue
            for sit in polarity_bucket.values():
                if not isinstance(sit, dict):
                    continue
                for entry in sit.get("values") or []:
                    if not isinstance(entry, dict):
                        continue
                    candidate = str(entry.get("sit_category") or "").strip().lower()
                    if candidate:
                        return candidate
    return "unknown"


@register(category=CATEGORY_LABEL)
def check_label_distribution(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        results.extend(_check_label_distribution_one(fs))
    return results


def _check_label_distribution_one(fs: FolderSet) -> list[CheckResult]:
    scope = fs.name
    records, _errors = read_jsonl_cached(fs.combined_metadata)
    if not records:
        return []
    total = len(records)
    counts = Counter(_sit_category(rec) for rec in records)
    unknown = counts.get("unknown", 0)
    breakdown = ", ".join(f"{label}={counts.get(label, 0)}" for label in _LABEL_BUCKETS)
    detail = f"{breakdown} (total={total}" + (f", unknown={unknown}" if unknown else "") + ")"

    accounted = sum(counts.get(label, 0) for label in _LABEL_BUCKETS)
    if accounted + unknown == total:
        results = [CheckResult(Status.PASS, CATEGORY_LABEL, ITEM_REF,
            "easy/hard x positive/negative distribution", detail, scope)]
    else:
        results = [CheckResult(Status.FAIL, CATEGORY_LABEL, ITEM_REF,
            "easy/hard x positive/negative distribution", detail, scope,
            "Distribution counts do not sum to the total record count - investigate "
            "combined_metadata.jsonl for corrupted sit_category values.")]
    if unknown:
        results.append(CheckResult(Status.WARN, CATEGORY_LABEL, ITEM_REF,
            "records with a resolvable sit_category",
            f"{unknown} of {total} record(s) had no easy/hard positive/negative label", scope,
            "Check combined_metadata.jsonl for records missing sit_details/sit_category."))
    return results


def _export_summary_total(fs: FolderSet) -> int | None:
    data, err = read_json(fs.export_summary)
    if err or not isinstance(data, dict):
        return None
    counts = data.get("counts", {})
    if not isinstance(counts, dict):
        return None
    total = counts.get("total", counts.get("Total"))
    if total is not None:
        return total
    pos = counts.get("positive", counts.get("Positive"))
    neg = counts.get("negative", counts.get("Negative"))
    return pos + neg if pos is not None and neg is not None else None


@register(category=CATEGORY_FORMAT)
def check_format_distribution(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        results.extend(_check_format_distribution_one(fs))
    return results


def _check_format_distribution_one(fs: FolderSet) -> list[CheckResult]:
    scope = fs.name
    records, _errors = read_jsonl_cached(fs.combined_metadata)
    if not records:
        return []
    counts = Counter(str(rec.get("file_format") or "unknown").strip().lower() for rec in records)
    record_total = len(records)
    breakdown = ", ".join(
        f"{fmt}={n}" for fmt, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    expected_total = _export_summary_total(fs)
    if expected_total is None:
        detail = f"{breakdown} (total={record_total}; export_summary.json total unavailable)"
        return [CheckResult(Status.INFO, CATEGORY_FORMAT, ITEM_REF,
            "document count per format", detail, scope)]
    detail = f"{breakdown} (total={record_total}, export_summary total={expected_total})"
    if record_total == expected_total:
        return [CheckResult(Status.PASS, CATEGORY_FORMAT, ITEM_REF,
            "document count per format", detail, scope)]
    return [CheckResult(Status.FAIL, CATEGORY_FORMAT, ITEM_REF,
        "document count per format", detail, scope,
        "Per-format counts do not sum to export_summary.json's reported total - "
        "combined_metadata.jsonl has drifted from the actual delivered document count.")]


@register(category=CATEGORY_CONTEXT)
def check_business_context_balance(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        results.extend(_check_business_context_balance_one(fs))
    return results


def _check_business_context_balance_one(fs: FolderSet) -> list[CheckResult]:
    scope = fs.name
    records, _errors = read_jsonl_cached(fs.combined_metadata)
    if not records:
        return []
    total = len(records)
    results: list[CheckResult] = []
    for dimension in _CONTEXT_DIMENSIONS:
        counts = Counter(str(rec.get(dimension) or "").strip() or "(missing)" for rec in records)
        top_value, top_count = counts.most_common(1)[0]
        shown = counts.most_common(8)
        breakdown = ", ".join(f"{value}={n}" for value, n in shown)
        remaining = len(counts) - len(shown)
        detail = (
            f"{breakdown}" + (f", +{remaining} more distinct value(s)" if remaining > 0 else "")
            + f" (total={total}, distinct={len(counts)})"
        )
        share = top_count / total if total else 0.0
        if share >= _CONCENTRATION_WARN_THRESHOLD:
            results.append(CheckResult(Status.WARN, CATEGORY_CONTEXT, ITEM_REF,
                f"{dimension} distribution balance", detail, scope,
                f"'{top_value}' accounts for {share:.0%} of documents - broaden {dimension} "
                "coverage so the corpus isn't dominated by one value."))
        else:
            results.append(CheckResult(Status.INFO, CATEGORY_CONTEXT, ITEM_REF,
                f"{dimension} distribution", detail, scope))
    return results


def _normalize_value(value: str) -> str:
    return _NON_ALNUM.sub("", value.casefold())


@register(category=CATEGORY_DIVERSITY)
def check_effective_hard_diversity(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        results.extend(_check_effective_hard_diversity_one(fs))
    return results


def _check_effective_hard_diversity_one(fs: FolderSet) -> list[CheckResult]:
    scope = fs.name
    records, _errors = read_jsonl_cached(fs.combined_metadata)
    if not records:
        return []
    results: list[CheckResult] = []
    for polarity, field_name in (("positive", "sit_values"), ("negative", "lookalike_values")):
        normalized_to_docs: dict[str, list[str]] = {}
        total_values = 0
        for rec in records:
            values_by_sit = rec.get(field_name)
            if not isinstance(values_by_sit, dict):
                continue
            doc_id = str(rec.get("doc_id") or rec.get("stem") or "?")
            for values in values_by_sit.values():
                if not isinstance(values, list):
                    continue
                for value in values:
                    total_values += 1
                    normalized_to_docs.setdefault(_normalize_value(str(value)), []).append(doc_id)
        if not total_values:
            continue
        duplicated = {norm: docs for norm, docs in normalized_to_docs.items() if len(docs) > 1}
        reused_instances = sum(len(docs) - 1 for docs in duplicated.values())
        effective_hard_diversity = 1.0 - (reused_instances / total_values)
        detail = (
            f"{total_values} {polarity} value instance(s) across {len(normalized_to_docs)} "
            f"distinct value(s); {len(duplicated)} value(s) reused "
            f"({reused_instances} redundant instance(s)); "
            f"effective_hard_diversity={effective_hard_diversity:.3f}"
        )
        if duplicated:
            examples = [
                f"{docs[0]} & {docs[1]}" + (f" (+{len(docs) - 2} more)" if len(docs) > 2 else "")
                for docs in list(duplicated.values())[:5]
            ]
            results.append(CheckResult(Status.WARN, CATEGORY_DIVERSITY, ITEM_REF,
                f"{polarity} SIT value reuse (effective_hard_diversity)", detail, scope,
                "Duplicate planted values reduce corpus diversity - examples: " + "; ".join(examples),
                evidence=[doc for docs in duplicated.values() for doc in docs[:2]][:10]))
        else:
            results.append(CheckResult(Status.PASS, CATEGORY_DIVERSITY, ITEM_REF,
                f"{polarity} SIT value reuse (effective_hard_diversity)", detail, scope))
    return results
