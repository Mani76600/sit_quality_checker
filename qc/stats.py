"""Aggregate, whole-run statistics computed once per report and displayed as
prominent visual summary cards (Streamlit + HTML/PDF), separate from the
PASS/FAIL/WARN/INFO checklist itself. Lives outside qc/checks/ (and does not
import qc.registry) specifically so qc/registry.py can import this module to
populate RunReport.stats without a circular import - every file under
qc/checks/ imports qc.registry to self-register, so registry.py can never
import from a checks/*.py module.

Both functions below always scan exhaustively (every chunk/metadata file,
never sampled) regardless of the exhaustive_chunk_scan option, because the
one thing both of these specific numbers must never be is approximate: a
"was every document actually run through MCE" percentage or an easy/hard x
positive/negative headline count that's silently based on a sample would be
actively misleading in exactly the place meant to build confidence in the
run.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from qc import file_cache
from qc.context import FolderSet, VersionContext
from qc.jsonio import read_json_cached, read_jsonl_cached

_LABEL_BUCKETS = ("easy positive", "hard positive", "easy negative", "hard negative")


def sit_category(rec: dict) -> str:
    """Same resolution order as document_composition_checks.py's own
    _sit_category - duplicated here (not imported) because that module
    imports qc.registry and this one must not."""
    value = str(rec.get("sit_category") or "").strip().lower()
    if value in _LABEL_BUCKETS:
        return value
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


def compute_label_distribution(ctx: VersionContext) -> dict:
    """{"easy positive": N, "hard positive": N, "easy negative": N,
    "hard negative": N, "unknown": N, "total": N} summed across both
    Agreements and Disagreements folder sets."""
    counts: Counter[str] = Counter()
    for fs in ctx.folder_sets():
        records, _errors = read_jsonl_cached(fs.combined_metadata)
        for rec in records:
            counts[sit_category(rec)] += 1
    total = sum(counts.values())
    return {
        **{label: counts.get(label, 0) for label in _LABEL_BUCKETS},
        "unknown": counts.get("unknown", 0),
        "total": total,
    }


def compute_corpus_breakdown(ctx: VersionContext) -> dict:
    """Same easy/hard x positive/negative buckets as compute_label_distribution,
    but split by folder set instead of summed - so "how many were generated"
    (Agreements + Disagreements) never hides "how many were actually
    accepted" (Agreements alone) and "how many disagreed" (Disagreements
    alone) behind a single combined number. Powers the Corpus Breakdown
    table (Accepted / Generated / Disagreed / Rate per bucket)."""
    def _bucket_counts(fs: FolderSet) -> Counter:
        counts: Counter[str] = Counter()
        records, _errors = read_jsonl_cached(fs.combined_metadata)
        for rec in records:
            counts[sit_category(rec)] += 1
        return counts

    accepted_counts = _bucket_counts(ctx.agreements)
    disagreed_counts = _bucket_counts(ctx.disagreements)
    buckets = []
    total_accepted = total_disagreed = total_generated = 0
    for label in _LABEL_BUCKETS:
        accepted = accepted_counts.get(label, 0)
        disagreed = disagreed_counts.get(label, 0)
        generated = accepted + disagreed
        rate = (accepted / generated * 100) if generated else 0.0
        buckets.append({"label": label, "accepted": accepted, "disagreed": disagreed,
                         "generated": generated, "rate": rate})
        total_accepted += accepted
        total_disagreed += disagreed
        total_generated += generated
    total_rate = (total_accepted / total_generated * 100) if total_generated else 0.0
    return {
        "buckets": buckets,
        "total_accepted": total_accepted,
        "total_disagreed": total_disagreed,
        "total_generated": total_generated,
        "total_rate": total_rate,
    }


def _extract_snippets(obj, found: list | None = None) -> list[dict]:
    if found is None:
        found = []
    if isinstance(obj, dict):
        if "label" in obj and ("sit_found" in obj or "snippet" in obj):
            found.append(obj)
        else:
            for v in obj.values():
                _extract_snippets(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _extract_snippets(item, found)
    return found


def _polarity_coverage(root: Path) -> tuple[int, int]:
    """(docs_with_detection, docs_checked) for one Positive/ or Negative/
    outputs/parsed_raw_doc/chunks directory - a document counts as "MCE
    detected" if at least one of its chunk snippets carries sit_found==True
    (the same per-document signal metadata_checks.py already relies on for
    its Positive-only label check, generalized here to both polarities and
    counted as a plain coverage fraction instead of a pass/fail threshold)."""
    chunk_files = file_cache.list_dir(root, "*.json")
    detected = 0
    checked = 0
    for path in chunk_files:
        data, err = read_json_cached(path)
        if err:
            continue
        snippets = _extract_snippets(data)
        if not snippets:
            continue
        checked += 1
        if any(s.get("sit_found") is True for s in snippets):
            detected += 1
    return detected, checked


_COMPOSITION_FIELDS = {
    "format": "file_format",
    "domain": "domain",
    "department": "department",
    "function": "function",
    "workflow": "workflow",
    "process": "process",
    "persona": "persona",
    "role": "role",
    "document_type": "document_type",
}
_TOP_N = 8


def compute_composition_distributions(ctx: VersionContext) -> dict:
    """"Most common counts" for the report's visual bar charts - one entry
    per dimension in _COMPOSITION_FIELDS, each {"total": N, "distinct": N,
    "top": [(value, count, pct), ...] (first _TOP_N, for the always-visible
    bars), "all": [(value, count, pct), ...] (every distinct value, by count
    descending, for a "show all" expand/dropdown in the UI rather than a
    dead-end "+N more" label)}. Pooled across Agreements + Disagreements
    (same convention as compute_label_distribution/compute_mce_coverage
    above) since the report shows one whole-run headline, not a
    per-partition breakdown, for these. Exhaustive (every
    combined_metadata.jsonl record, via the same process-wide
    read_jsonl_cached already shared with every other check that reads this
    file) - counts/percentages are exactly what document_composition_checks.py
    computes per-category-item, just pooled and reshaped for charting."""
    counters: dict[str, Counter] = {key: Counter() for key in _COMPOSITION_FIELDS}
    for fs in ctx.folder_sets():
        records, _errors = read_jsonl_cached(fs.combined_metadata)
        for rec in records:
            for key, field in _COMPOSITION_FIELDS.items():
                if key == "format":
                    value = str(rec.get(field) or "unknown").strip().lower()
                else:
                    value = str(rec.get(field) or "").strip() or "(missing)"
                counters[key][value] += 1

    result: dict[str, dict] = {}
    for key, counter in counters.items():
        total = sum(counter.values())
        if not total:
            continue
        all_sorted = counter.most_common()
        all_with_pct = [(value, count, count / total * 100.0) for value, count in all_sorted]
        result[key] = {
            "total": total,
            "distinct": len(counter),
            "top": all_with_pct[:_TOP_N],
            "all": all_with_pct,
        }
    return result


_LENGTH_MAX_BINS = 28
_NICE_BIN_SIZES = (10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000)


def _nice_bin_size(span: float, max_bins: int) -> int:
    raw = span / max_bins if max_bins else span
    for size in _NICE_BIN_SIZES:
        if size >= raw:
            return size
    return _NICE_BIN_SIZES[-1] * max(1, round(raw / _NICE_BIN_SIZES[-1]))


def compute_document_length_distribution(ctx: VersionContext) -> dict:
    """Real character-count distribution (not the categorical length_bucket
    field in combined_metadata.jsonl, which has no numeric granularity) -
    sourced from context_output_normalized's doc_content_length field,
    confirmed on real data to carry one value per real document (14725
    rows for 14725 documents on a real Bulgaria Passport Number run, zero
    duplicates). Returns {} if unavailable. Exhaustive - every row read, no
    sampling - same reasoning as the other headline stats in this module:
    this chart is specifically meant to show the real shape of the corpus,
    so it must never be built from a partial sample.

    Binning is adaptive, not a fixed 100-char width: real data has a long
    tail (e.g. one real run had documents from 201 to 47,039 characters,
    median ~3052) that would otherwise produce hundreds of near-empty bins
    and crush the readable part of the chart. The histogram covers up to
    the 99th percentile with a "nice" round bin size chosen to stay under
    _LENGTH_MAX_BINS bins; anything beyond that is folded into one final
    overflow bucket (open-ended) rather than silently dropped, so the
    real max is never hidden - just not given its own dozens of bins."""
    lengths: list[int] = []
    for fs in ctx.folder_sets():
        for jf in fs.context_output_normalized_files():
            rows, err = read_json_cached(jf)
            if err or not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                length = row.get("doc_content_length")
                if isinstance(length, int) and length > 0:
                    lengths.append(length)
    if not lengths:
        return {}
    lengths.sort()
    n = len(lengths)
    median = (lengths[n // 2] if n % 2 else (lengths[n // 2 - 1] + lengths[n // 2]) / 2)
    min_len, max_len = lengths[0], lengths[-1]

    p99 = lengths[min(n - 1, int(n * 0.99))]
    chart_max = max(p99, min_len + 1)
    bin_size = _nice_bin_size(chart_max - min_len, _LENGTH_MAX_BINS)
    start_bin = (min_len // bin_size) * bin_size
    end_bin = (chart_max // bin_size) * bin_size

    counts: Counter[int] = Counter()
    overflow = 0
    for length in lengths:
        if length > end_bin + bin_size:
            overflow += 1
            continue
        counts[min((length // bin_size) * bin_size, end_bin)] += 1
    histogram = [
        (b, b + bin_size, counts.get(b, 0))
        for b in range(start_bin, end_bin + bin_size, bin_size)
    ]
    return {
        "total": n, "min": min_len, "median": median, "max": max_len,
        "histogram": histogram, "overflow": overflow, "overflow_from": end_bin + bin_size,
    }


def compute_dominant_language(ctx: VersionContext) -> dict:
    """The single most common "language" value in combined_metadata.jsonl,
    pooled across Agreements + Disagreements - in practice this is ~100%
    uniform per run (one SIT/language per Version_* folder), so "dominant"
    just means "the" value while still being robust to a handful of stray
    rows. Returns {} if no rows carry a language field at all."""
    counts: Counter[str] = Counter()
    for fs in ctx.folder_sets():
        records, _errors = read_jsonl_cached(fs.combined_metadata)
        for rec in records:
            value = rec.get("language") or rec.get("locale")
            if isinstance(value, str) and value.strip():
                counts[value.strip()] += 1
    if not counts:
        return {}
    code, count = counts.most_common(1)[0]
    return {"code": code, "total": sum(counts.values()), "count": count}


def compute_mce_coverage(ctx: VersionContext) -> dict:
    """Overall + per-polarity "was this document actually run through MCE
    detection" coverage, exhaustive across every chunk file in both
    Agreements and Disagreements. Returns:
    {"detected": N, "checked": M, "pct": float,
     "positive": {"detected": N, "checked": M, "pct": float},
     "negative": {"detected": N, "checked": M, "pct": float}}
    pct is None (not 0.0) when checked==0, so callers can distinguish
    "nothing to check" from "checked and 0% detected"."""
    totals = {"positive": [0, 0], "negative": [0, 0]}
    for fs in ctx.folder_sets():
        for polarity_name, polarity in (("positive", fs.positive), ("negative", fs.negative)):
            d, c = _polarity_coverage(polarity.chunks)
            totals[polarity_name][0] += d
            totals[polarity_name][1] += c

    def _pct(detected: int, checked: int) -> float | None:
        return (detected / checked * 100.0) if checked else None

    pos_detected, pos_checked = totals["positive"]
    neg_detected, neg_checked = totals["negative"]
    overall_detected = pos_detected + neg_detected
    overall_checked = pos_checked + neg_checked
    return {
        "detected": overall_detected,
        "checked": overall_checked,
        "pct": _pct(overall_detected, overall_checked),
        "positive": {"detected": pos_detected, "checked": pos_checked,
                     "pct": _pct(pos_detected, pos_checked)},
        "negative": {"detected": neg_detected, "checked": neg_checked,
                     "pct": _pct(neg_detected, neg_checked)},
    }
