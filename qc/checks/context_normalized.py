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
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

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


def _get_counts(export_summary: dict) -> tuple[int | None, int | None, int | None]:
    counts = export_summary.get("counts", {}) if isinstance(export_summary, dict) else {}
    pos = counts.get("positive", counts.get("Positive"))
    neg = counts.get("negative", counts.get("Negative"))
    total = counts.get("total", counts.get("Total"))
    return pos, neg, total


def _is_empty(v: object) -> bool:
    return v is None or v == "" or v == [] or v == {}


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
        data, err = read_json(jf)
        if err:
            results.append(CheckResult(
                Status.FAIL, CATEGORY, "3", f"{jf.name} is readable JSON", err, scope,
                "Regenerate the normalized-context export - the file is truncated or malformed."))
            continue
        if not isinstance(data, list):
            results.append(CheckResult(
                Status.FAIL, CATEGORY, "3", f"{jf.name} is a JSON array",
                f"Top-level type is {type(data).__name__}, expected a list of records.", scope))
            continue

        value_to_filenames = _build_value_to_filenames(fs)

        # Every check below runs independently over the same records, even if
        # an earlier one fails - so a single pass surfaces every issue in
        # this file at once instead of one-at-a-time.
        results.extend(_check_record_count(fs, jf, data, total_docs, threshold, scope))
        results.extend(_check_sit_category(jf, data, scope))
        results.extend(_check_field_completeness(jf, data, scope))
        results.extend(_check_languages(fs, jf, data, value_to_filenames, scope))

    return results


def _check_record_count(fs: FolderSet, jf, data: list, total_docs: int | None,
                         threshold: float, scope: str) -> list[CheckResult]:
    record_count = len(data)
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


def _check_sit_category(jf, data: list, scope: str) -> list[CheckResult]:
    bad_category = []
    for i, rec in enumerate(data):
        cat = rec.get("sit_category") if isinstance(rec, dict) else None
        if not cat or (isinstance(cat, str) and cat.strip().lower() == "undetermined"):
            if len(bad_category) < 10:
                bad_category.append(i)

    if bad_category:
        return [CheckResult(Status.FAIL, CATEGORY, "3",
            f"{jf.name}: sit_category populated on every record",
            f"Records missing/undetermined sit_category, first indices: {bad_category}", scope,
            "Backfill sit_category (difficulty + polarity, e.g. 'easy positive') for "
            "every normalized-context record; it must never be empty or 'undetermined'.")]
    return [CheckResult(Status.PASS, CATEGORY, "3",
        f"{jf.name}: sit_category populated on every record",
        f"All {len(data)} records have a non-empty, determined sit_category.", scope)]


def _check_field_completeness(jf, data: list, scope: str) -> list[CheckResult]:
    """Aggregated per-field emptiness audit across the whole file - one
    summary table, not one line per offending record.

    Schema-aware: some pipeline versions omit certain fields entirely (e.g.
    a real sample was found with no document_name/doc_id anywhere in the
    file at all - a genuine schema difference, not a per-record defect).
    A field only gets checked for emptiness if it's actually part of this
    file's observed schema; fields absent from every record are reported
    once as an informational schema note instead of a false "100% empty"."""
    observed_fields: set[str] = set()
    for rec in data:
        if isinstance(rec, dict):
            observed_fields |= set(rec.keys())

    applicable_fields = [f for f in TOP_LEVEL_FIELDS if f in observed_fields]
    skipped_fields = [f for f in TOP_LEVEL_FIELDS if f not in observed_fields]
    applicable_windows = [w for w in CONTEXT_WINDOW_KEYS if w in observed_fields]

    results: list[CheckResult] = []
    if skipped_fields:
        results.append(CheckResult(Status.INFO, CATEGORY, "3 (addition)",
            f"{jf.name}: fields not part of this file's schema",
            f"{skipped_fields} do not appear in any record of this file - likely a "
            "different pipeline/schema version for this SIT; not treated as a failure.",
            scope))

    empty_counts: Counter = Counter()
    examples: dict[str, list[str]] = {}

    def _note(field: str, doc_ref: str) -> None:
        empty_counts[field] += 1
        ex = examples.setdefault(field, [])
        if len(ex) < 3:
            ex.append(doc_ref)

    total = len(data)
    for rec in data:
        if not isinstance(rec, dict):
            continue
        doc_ref = str(rec.get("document_name") or rec.get("doc_id") or rec.get("value") or "?")

        for field in applicable_fields:
            if _is_empty(rec.get(field)):
                _note(field, doc_ref)

        for wk in applicable_windows:
            window = rec.get(wk)
            if not isinstance(window, dict) or not window:
                _note(wk, doc_ref)
                continue
            for sf in CONTEXT_SUBFIELDS:
                if _is_empty(window.get(sf)):
                    _note(f"{wk}.{sf}", doc_ref)

    if not empty_counts:
        results.append(CheckResult(Status.PASS, CATEGORY, "3 (addition)",
            f"{jf.name}: every field is assigned (non-empty) on every record",
            f"Checked {total} record(s) across {len(applicable_fields)} top-level fields + "
            f"{len(applicable_windows)} context windows, none empty.", scope))
        return results

    rows = [f"{field}: {n}/{total} empty (e.g. {examples[field]})"
            for field, n in empty_counts.most_common(MAX_FIELD_ROWS_SHOWN)]
    more = "" if len(empty_counts) <= MAX_FIELD_ROWS_SHOWN else \
        f" (+{len(empty_counts) - MAX_FIELD_ROWS_SHOWN} more field(s) also affected)"
    results.append(CheckResult(Status.FAIL, CATEGORY, "3 (addition)",
        f"{jf.name}: every field is assigned (non-empty) on every record",
        " | ".join(rows) + more, scope,
        "Backfill or regenerate the listed fields - every field that IS part of this "
        "file's schema should be populated on every record."))
    return results


def _build_value_to_filenames(fs: FolderSet) -> dict[str, list[str]]:
    """value -> [delivered raw_doc filename(s)], read from sit_inverted_index.json.

    This is the one identifier that reliably maps a context_output_normalized
    record back to its real delivered file regardless of schema version:
    document_name/doc_id aren't always present, and doc_path uses an
    internal generation path unrelated to the delivered filename - but
    'value' (the planted SIT value) is recorded consistently in both files,
    confirmed against real Sweden sample data."""
    data, err = read_json(fs.sit_inverted_index)
    mapping: dict[str, list[str]] = {}
    if err or not isinstance(data, dict):
        return mapping
    for _sit_name, value_map in data.items():
        if not isinstance(value_map, dict):
            continue
        for value, filemap in value_map.items():
            if isinstance(filemap, dict):
                mapping[value] = list(filemap.keys())
    return mapping


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


def _check_languages(fs: FolderSet, jf, data: list, value_to_filenames: dict[str, list[str]],
                      scope: str) -> list[CheckResult]:
    lang_counter: Counter[str] = Counter()
    contaminated_paths: dict[str, list[str]] = {}

    for rec in data:
        if not isinstance(rec, dict):
            continue
        lang = _record_language(rec)
        if lang:
            lang_counter[lang] += 1

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

    for rec in data:
        if not isinstance(rec, dict):
            continue
        lang = _record_language(rec)
        if lang and lang != majority_lang:
            full_path = _resolve_doc_full_path(fs, rec, value_to_filenames)
            paths = contaminated_paths.setdefault(lang, [])
            if len(paths) < MAX_EXAMPLES:
                paths.append(full_path)

    total_minority = sum(minorities.values())
    example_paths = [f"'{lang}': {path}"
                      for lang, _n in sorted(minorities.items(), key=lambda kv: -kv[1])
                      for path in contaminated_paths.get(lang, [])]
    results.append(CheckResult(Status.WARN, CATEGORY, "3 (addition)",
        f"{jf.name}: language consistency - contamination found",
        f"Majority language '{majority_lang}' ({majority_n}/{total_lang} records); "
        f"{total_minority} minority-language record(s) found, e.g. {example_paths}", scope,
        "Open the listed file(s) directly and verify they aren't mislabeled or "
        "cross-contaminated from another SIT/language run before shipping this corpus."))
    return results
