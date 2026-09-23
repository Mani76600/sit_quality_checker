"""Item 3 (+ user follow-up #2): context_output_normalized/<sit>.json checks,
run for both Agreements and Disagreements.

- file exists (also covered by structure.py, re-verified here in context)
- record count vs. total doc count from export_summary.json, using a
  user-configurable ratio threshold (default 1.0x - the "6x" example in the
  original checklist did not hold on any real sample, so it is exposed as a
  tunable rather than a hardcoded magic number)
- every record's sit_category is non-empty and not "undetermined"
- every OTHER field is non-empty too, both top-level (sit_name, confidence,
  doc_path, value, doc_content_length, detected_sit_start/end_index,
  language, ground_truth, domain, file_format, document_name, doc_id) and
  within each context_100/500/2000/4000/6000/8000 sub-object (text,
  left/right_context_length, exact_content_length, detected_sit_start/
  end_index, language) - reported as one aggregated per-field table, not one
  line per record
- language-consistency: how many distinct languages were detected overall,
  and for any minority/contaminating language value, the exact document's
  full raw_doc file path (via the record's own document_name + ground_truth
  fields, not just a bare count) so it can be opened directly

IMPORTANT - streaming, not full materialization: real context_output_normalized
files routinely run 100-250MB (each record embeds up to 6 nested text windows,
each up to 8000 characters). Loading one of these via plain json.load() - and
this file used to be parsed TWICE (once here, once again in export_summary.py's
cross-check) - produces an in-memory object graph 3-5x the file's byte size,
which was confirmed to OOM-crash the hosted web app on a real (non-huge)
upload: the process log showed this exact check start and then go silent with
no traceback, a signature of the container being killed rather than a normal
Python exception. Fixed by streaming the file once via ijson (one record in
memory at a time) and caching only small, bounded aggregate statistics -
never the full record list - shared with export_summary.py's cross-check so
the file is only read from disk once per run, not twice.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import ijson

from qc.checks.inverted_index_scan import scan_inverted_index
from qc.context import FolderSet, VersionContext
from qc.jsonio import read_json, to_bool
from qc.models import CheckResult, Status
from qc.registry import register

CATEGORY = "3. context_output_normalized"

# Checked for non-emptiness at the top level of every record (sit_category
# and language get their own dedicated, more detailed checks below, but are
# also swept here for completeness).
TOP_LEVEL_FIELDS = [
    "sit_name", "confidence", "doc_path", "value", "doc_content_length",
    "detected_sit_start_index", "detected_sit_end_index", "language",
    "ground_truth", "sit_category", "domain", "file_format",
    "document_name", "doc_id",
]
CONTEXT_WINDOW_KEYS = [
    "context_100", "context_500", "context_2000", "context_4000",
    "context_6000", "context_8000",
]
CONTEXT_SUBFIELDS = [
    "text", "left_context_length", "right_context_length",
    "exact_content_length", "detected_sit_start_index",
    "detected_sit_end_index", "language",
]
MAX_FIELD_ROWS_SHOWN = 12
MAX_EXAMPLES = 8
FIELD_EXAMPLES_PER_ROW = 3
SIT_CATEGORY_EXAMPLES = 10
CONFIDENCE_EXAMPLES = 10

# Fields where "entirely absent from this file's schema" is a hard FAIL
# rather than the informational schema-variant note every other field gets
# (real samples have shown legitimate schema differences for other fields,
# e.g. document_name/doc_id missing entirely on some pipeline versions -
# but confidence is expected on every record regardless of schema version).
REQUIRED_ALWAYS_PRESENT_FIELDS = {"confidence"}


def _is_numeric_score(v: object) -> bool:
    """True if v is (or numerically parses as) a score like 65/75/85 - real
    samples store this as a numeric string (e.g. "85"), not a raw number, so
    both are accepted; free text ("high", "N/A", ...) is not."""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return False
        try:
            float(s)
            return True
        except ValueError:
            return False
    return False


def _get_counts(export_summary: dict) -> tuple[int | None, int | None, int | None]:
    counts = export_summary.get("counts", {}) if isinstance(export_summary, dict) else {}
    pos = counts.get("positive", counts.get("Positive"))
    neg = counts.get("negative", counts.get("Negative"))
    total = counts.get("total", counts.get("Total"))
    return pos, neg, total


def _is_empty(v: object) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _record_language(rec: dict) -> str | None:
    """The record's language, preferring the top-level field but falling
    back to any context window's nested language field if the top-level one
    is absent - schema variants have been seen with only one or the other."""
    lang = rec.get("language")
    if isinstance(lang, str) and lang.strip():
        return lang
    for wk in CONTEXT_WINDOW_KEYS:
        window = rec.get(wk)
        if isinstance(window, dict):
            lang = window.get("language")
            if isinstance(lang, str) and lang.strip():
                return lang
    return None


# ------------------------------------------------------------- streaming scan

@dataclass
class FileScan:
    """Small, bounded aggregate statistics for one context_output_normalized
    file - never holds the full record list. Shared (cached) between this
    module and export_summary.py so the file is only streamed once per run."""
    error: str | None = None
    not_a_list: bool = False
    record_count: int = 0
    ground_truth_true: int = 0
    ground_truth_false: int = 0
    observed_fields: set[str] = field(default_factory=set)
    bad_sit_category_indices: list[int] = field(default_factory=list)
    bad_confidence_count: int = 0
    bad_confidence_examples: list[tuple[int, object]] = field(default_factory=list)
    field_empty_counts: Counter = field(default_factory=Counter)
    field_empty_examples: dict[str, list[str]] = field(default_factory=dict)
    lang_counter: Counter = field(default_factory=Counter)
    lang_examples: dict[str, list[dict]] = field(default_factory=dict)


_SCAN_CACHE: dict[str, FileScan] = {}


def clear_scan_cache() -> None:
    _SCAN_CACHE.clear()


def _peek_top_level_is_array(path: Path) -> bool | None:
    """Cheap check of the top-level JSON type (first non-whitespace byte)
    without parsing the file - lets us report a clear type-mismatch error
    up front instead of ijson silently yielding zero items for a non-array
    root, which would look identical to "empty array"."""
    try:
        with path.open("rb") as f:
            while True:
                b = f.read(1)
                if not b:
                    return None  # empty file
                if b.isspace():
                    continue
                return b == b"["
    except OSError:
        return None


def _note_empty_field(scan: FileScan, field_key: str, doc_ref: str) -> None:
    scan.field_empty_counts[field_key] += 1
    ex = scan.field_empty_examples.setdefault(field_key, [])
    if len(ex) < FIELD_EXAMPLES_PER_ROW:
        ex.append(doc_ref)


def scan_file(path: Path) -> FileScan:
    """Stream path (a top-level JSON array) exactly once, computing every
    aggregate statistic every consumer needs, and cache the (small) result."""
    key = str(path)
    cached = _SCAN_CACHE.get(key)
    if cached is not None:
        return cached

    scan = FileScan()
    is_array = _peek_top_level_is_array(path)
    if is_array is None:
        scan.error = f"file not found or empty: {path}"
        _SCAN_CACHE[key] = scan
        return scan
    if not is_array:
        scan.not_a_list = True
        _SCAN_CACHE[key] = scan
        return scan

    try:
        with path.open("rb") as f:
            for i, rec in enumerate(ijson.items(f, "item")):
                if not isinstance(rec, dict):
                    continue
                scan.record_count += 1
                scan.observed_fields |= set(rec.keys())

                gt = str(rec.get("ground_truth", "")).strip().lower()
                if gt == "true":
                    scan.ground_truth_true += 1
                elif gt == "false":
                    scan.ground_truth_false += 1

                cat = rec.get("sit_category")
                if not cat or (isinstance(cat, str) and cat.strip().lower() == "undetermined"):
                    if len(scan.bad_sit_category_indices) < SIT_CATEGORY_EXAMPLES:
                        scan.bad_sit_category_indices.append(i)

                doc_ref = str(rec.get("document_name") or rec.get("doc_id") or rec.get("value") or "?")

                conf = rec.get("confidence")
                if not _is_empty(conf) and not _is_numeric_score(conf):
                    scan.bad_confidence_count += 1
                    if len(scan.bad_confidence_examples) < CONFIDENCE_EXAMPLES:
                        scan.bad_confidence_examples.append((i, conf))

                for f_name in TOP_LEVEL_FIELDS:
                    if _is_empty(rec.get(f_name)):
                        _note_empty_field(scan, f_name, doc_ref)

                for wk in CONTEXT_WINDOW_KEYS:
                    window = rec.get(wk)
                    if not isinstance(window, dict) or not window:
                        _note_empty_field(scan, wk, doc_ref)
                        continue
                    for sf in CONTEXT_SUBFIELDS:
                        if _is_empty(window.get(sf)):
                            _note_empty_field(scan, f"{wk}.{sf}", doc_ref)

                lang = _record_language(rec)
                if lang:
                    scan.lang_counter[lang] += 1
                    examples = scan.lang_examples.setdefault(lang, [])
                    if len(examples) < MAX_EXAMPLES:
                        examples.append({
                            "value": rec.get("value"),
                            "ground_truth": rec.get("ground_truth"),
                            "document_name": rec.get("document_name"),
                            "doc_path": rec.get("doc_path"),
                        })
    except Exception as exc:  # ijson parse errors, truncated files, etc.
        scan.error = f"invalid/truncated JSON in {path}: {exc}"

    _SCAN_CACHE[key] = scan
    return scan


# ------------------------------------------------------------------- checks

@register(category=CATEGORY)
def check_context_normalized(ctx: VersionContext, options: dict) -> list[CheckResult]:
    threshold = float(options.get("context_ratio_threshold", 1.0))
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        results.extend(_check_one(fs, threshold))
    return results


def _check_one(fs: FolderSet, threshold: float) -> list[CheckResult]:
    scope = fs.name
    results: list[CheckResult] = []

    json_files = fs.context_output_normalized_files()
    if not json_files:
        # structure.py already reports the missing-directory/file case; avoid
        # duplicate noise here.
        return results

    export_summary, err = read_json(fs.export_summary)
    total_docs = None
    if err:
        results.append(CheckResult(
            Status.WARN, CATEGORY, "3", "Could not read export_summary.json for ratio check",
            err, scope))
    else:
        pos, neg, total = _get_counts(export_summary)
        if total is None and pos is not None and neg is not None:
            total = pos + neg
        total_docs = total

    for jf in json_files:
        scan = scan_file(jf)
        if scan.error:
            results.append(CheckResult(
                Status.FAIL, CATEGORY, "3", f"{jf.name} is readable JSON", scan.error, scope,
                "Regenerate the normalized-context export - the file is truncated or malformed."))
            continue
        if scan.not_a_list:
            results.append(CheckResult(
                Status.FAIL, CATEGORY, "3", f"{jf.name} is a JSON array",
                "Top-level value is not a list of records.", scope))
            continue

        value_to_filenames = _build_value_to_filenames(fs)

        # Every check below is independent, even if an earlier one fails -
        # so a single pass surfaces every issue in this file at once instead
        # of one-at-a-time.
        results.extend(_check_record_count(jf, scan, total_docs, threshold, scope))
        results.extend(_check_sit_category(jf, scan, scope))
        results.extend(_check_field_completeness(jf, scan, scope))
        results.extend(_check_confidence_is_numeric(jf, scan, scope))
        results.extend(_check_languages(fs, jf, scan, value_to_filenames, scope))

    return results


def _check_record_count(jf, scan: FileScan, total_docs: int | None,
                         threshold: float, scope: str) -> list[CheckResult]:
    record_count = scan.record_count
    if total_docs is None:
        return [CheckResult(Status.INFO, CATEGORY, "3",
            f"{jf.name}: record count (no export_summary counts to compare)",
            f"{record_count} records", scope)]

    ratio = (record_count / total_docs) if total_docs else 0.0
    required = total_docs * threshold
    detail = (f"{record_count} records vs {total_docs} total docs "
              f"(ratio {ratio:.2f}x, threshold {threshold:.2f}x)")
    if record_count >= required:
        return [CheckResult(Status.PASS, CATEGORY, "3",
            f"{jf.name}: record count meets threshold", detail, scope)]
    return [CheckResult(Status.FAIL, CATEGORY, "3",
        f"{jf.name}: record count below threshold", detail, scope,
        f"Expected at least {required:.0f} records ({threshold}x doc count); "
        "investigate why fewer context records were exported, or lower the "
        "threshold in the sidebar if 1.0x is not the right expectation for this SIT.")]


def _check_sit_category(jf, scan: FileScan, scope: str) -> list[CheckResult]:
    if scan.bad_sit_category_indices:
        return [CheckResult(Status.FAIL, CATEGORY, "3",
            f"{jf.name}: sit_category populated on every record",
            f"Records missing/undetermined sit_category, first indices: "
            f"{scan.bad_sit_category_indices}", scope,
            "Backfill sit_category (difficulty + polarity, e.g. 'easy positive') for "
            "every normalized-context record; it must never be empty or 'undetermined'.")]
    return [CheckResult(Status.PASS, CATEGORY, "3",
        f"{jf.name}: sit_category populated on every record",
        f"All {scan.record_count} records have a non-empty, determined sit_category.", scope)]


def _check_field_completeness(jf, scan: FileScan, scope: str) -> list[CheckResult]:
    """Aggregated per-field emptiness audit across the whole file - one
    summary table, not one line per offending record.

    Schema-aware: some pipeline versions omit certain fields entirely (e.g.
    a real sample was found with no document_name/doc_id anywhere in the
    file at all - a genuine schema difference, not a per-record defect).
    A field only gets surfaced here if it's actually part of this file's
    observed schema; fields absent from every record are reported once as
    an informational schema note instead of a false "100% empty"."""
    observed = scan.observed_fields
    applicable_fields = [f for f in TOP_LEVEL_FIELDS if f in observed]
    skipped_fields = [f for f in TOP_LEVEL_FIELDS if f not in observed]
    applicable_windows = [w for w in CONTEXT_WINDOW_KEYS if w in observed]

    results: list[CheckResult] = []

    hard_missing = [f for f in skipped_fields if f in REQUIRED_ALWAYS_PRESENT_FIELDS]
    soft_skipped = [f for f in skipped_fields if f not in REQUIRED_ALWAYS_PRESENT_FIELDS]

    for f_name in hard_missing:
        results.append(CheckResult(Status.FAIL, CATEGORY, "3 (addition)",
            f"{jf.name}: {f_name} is present in the schema",
            f"'{f_name}' does not appear on any record in this file at all.", scope,
            f"'{f_name}' is required on every normalized-context record - regenerate this "
            f"file with '{f_name}' populated."))

    if soft_skipped:
        results.append(CheckResult(Status.INFO, CATEGORY, "3 (addition)",
            f"{jf.name}: fields not part of this file's schema",
            f"{soft_skipped} do not appear in any record of this file - likely a "
            "different pipeline/schema version for this SIT; not treated as a failure.",
            scope))

    def _applicable(key: str) -> bool:
        return key.split(".")[0] in observed

    applicable_empty = {k: n for k, n in scan.field_empty_counts.items() if _applicable(k)}
    total = scan.record_count

    if not applicable_empty:
        results.append(CheckResult(Status.PASS, CATEGORY, "3 (addition)",
            f"{jf.name}: every field is assigned (non-empty) on every record",
            f"Checked {total} record(s) across {len(applicable_fields)} top-level fields + "
            f"{len(applicable_windows)} context windows, none empty.", scope))
        return results

    ranked = Counter(applicable_empty).most_common(MAX_FIELD_ROWS_SHOWN)
    rows = [f"{field_key}: {n}/{total} empty (e.g. {scan.field_empty_examples.get(field_key, [])})"
            for field_key, n in ranked]
    more = "" if len(applicable_empty) <= MAX_FIELD_ROWS_SHOWN else \
        f" (+{len(applicable_empty) - MAX_FIELD_ROWS_SHOWN} more field(s) also affected)"
    results.append(CheckResult(Status.FAIL, CATEGORY, "3 (addition)",
        f"{jf.name}: every field is assigned (non-empty) on every record",
        " | ".join(rows) + more, scope,
        "Backfill or regenerate the listed fields - every field that IS part of this "
        "file's schema should be populated on every record."))
    return results


def _check_confidence_is_numeric(jf, scan: FileScan, scope: str) -> list[CheckResult]:
    """confidence must hold a numeric score (e.g. 65, 75, 85 - stored as a
    numeric string on real samples), never free text - checked only when
    confidence is actually part of this file's schema (its total absence is
    reported separately, as a hard FAIL, by _check_field_completeness)."""
    if "confidence" not in scan.observed_fields:
        return []
    if scan.bad_confidence_count:
        examples = [f"index {i}: {v!r}" for i, v in scan.bad_confidence_examples]
        return [CheckResult(Status.FAIL, CATEGORY, "3 (addition)",
            f"{jf.name}: confidence is a numeric score on every record",
            f"{scan.bad_confidence_count} record(s) have a non-numeric confidence value, "
            f"e.g. {examples}", scope,
            "confidence must be a numeric score (e.g. 65, 75, 85), not free text - fix the "
            "pipeline stage that writes this field.")]
    return [CheckResult(Status.PASS, CATEGORY, "3 (addition)",
        f"{jf.name}: confidence is a numeric score on every record",
        f"All {scan.record_count} record(s) with a confidence value hold a numeric score.",
        scope)]


def _build_value_to_filenames(fs: FolderSet) -> dict[str, list[str]]:
    """value -> [delivered raw_doc filename(s)], read from sit_inverted_index.json.

    This is the one identifier that reliably maps a context_output_normalized
    record back to its real delivered file regardless of schema version:
    document_name/doc_id aren't always present, and doc_path uses an
    internal generation path unrelated to the delivered filename - but
    'value' (the planted SIT value) is recorded consistently in both files,
    confirmed against real Sweden sample data.

    Streamed via scan_inverted_index() rather than a plain json.load(): the
    real file embeds every indexed chunk's full text and can run 50-100MB+,
    none of which this lookup needs."""
    scan = scan_inverted_index(fs.sit_inverted_index)
    if scan.error or scan.not_a_dict:
        return {}
    return scan.value_to_filenames


def _resolve_doc_full_path(fs: FolderSet, rec: dict, value_to_filenames: dict[str, list[str]]) -> str:
    """Best-effort full path to the actual raw_doc file this record refers
    to: prefer the sit_inverted_index.json value lookup (schema-independent,
    always accurate), then document_name (matches delivered filename when
    present), then doc_path's basename as a last resort (explicitly flagged
    as unverified - it uses an internal generation naming scheme, not the
    delivered filename, on at least one real sample)."""
    is_positive = to_bool(rec.get("ground_truth"))
    polarity = fs.positive if is_positive else fs.negative

    value = rec.get("value")
    filenames = value_to_filenames.get(value) if value else None
    if filenames:
        return "; ".join(str(polarity.raw_doc / fn) for fn in filenames)

    document_name = rec.get("document_name")
    if document_name:
        return str(polarity.raw_doc / document_name)

    doc_path = rec.get("doc_path")
    if doc_path:
        return f"(unverified - internal reference name only, not confirmed against raw_doc/) {Path(doc_path).name}"

    return "(no document identifier available on this record)"


def _check_languages(fs: FolderSet, jf, scan: FileScan, value_to_filenames: dict[str, list[str]],
                      scope: str) -> list[CheckResult]:
    lang_counter = scan.lang_counter

    if not lang_counter:
        # Never go silent - confirm the check ran even when this file's
        # schema carries no language field anywhere (top-level or nested),
        # so "nothing was reported" isn't mistaken for "nothing was checked".
        return [CheckResult(Status.INFO, CATEGORY, "3 (addition)",
            f"{jf.name}: language consistency",
            "No 'language' field found on any record (checked both the top-level field and "
            "every context_100/500/2000/4000/6000/8000 window) - this file's schema does not "
            "record language, so contamination cannot be checked for it.", scope)]

    results: list[CheckResult] = []
    total_lang = sum(lang_counter.values())
    majority_lang, majority_n = lang_counter.most_common(1)[0]
    minorities = {k: v for k, v in lang_counter.items() if k != majority_lang}

    dist_str = ", ".join(f"{k}={v}" for k, v in lang_counter.most_common())
    results.append(CheckResult(Status.INFO, CATEGORY, "3 (addition)",
        f"{jf.name}: languages detected",
        f"{len(lang_counter)} distinct language value(s) across {total_lang} record(s): "
        f"{dist_str}", scope))

    if not minorities:
        results.append(CheckResult(Status.PASS, CATEGORY, "3 (addition)",
            f"{jf.name}: language consistency (no contamination)",
            f"All {total_lang} records are language '{majority_lang}'.", scope))
        return results

    total_minority = sum(minorities.values())
    example_paths = [
        f"'{lang}': {_resolve_doc_full_path(fs, mini_rec, value_to_filenames)}"
        for lang, _n in sorted(minorities.items(), key=lambda kv: -kv[1])
        for mini_rec in scan.lang_examples.get(lang, [])
    ]
    results.append(CheckResult(Status.WARN, CATEGORY, "3 (addition)",
        f"{jf.name}: language consistency - contamination found",
        f"Majority language '{majority_lang}' ({majority_n}/{total_lang} records); "
        f"{total_minority} minority-language record(s) found, e.g. {example_paths}", scope,
        "Open the listed file(s) directly and verify they aren't mislabeled or "
        "cross-contaminated from another SIT/language run before shipping this corpus."))
    return results
