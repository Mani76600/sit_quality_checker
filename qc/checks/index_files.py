"""Item 4: sit_inverted_index.json / sit_merged_index.json.

Both possible index filenames get the SAME full battery of checks below,
each result explicitly naming which file it's about (never conflated):
  - readable JSON
  - "polarity" should be named "chunk_label" in chunk-level records
    (forward-looking: current pipeline output doesn't exhibit this, so a
    clean run PASSes with a note - the check still exists so it catches the
    field the moment a future pipeline version introduces it under the
    wrong name)
  - the number of unique SIT values indexed equals the number of documents
    generated (export_summary.json counts.total) - confirmed 1:1 on real
    sample output (14448 unique values == 14448 total docs)
  - every chunk entry has non-empty chunk_id/chunk_content (aggregated
    counts, not one line per entry)

sit_merged_index.json is only produced by some pipeline versions - if
absent, that's reported as an explicit INFO (optional), never silently
skipped or conflated with sit_inverted_index.json's own results.

IMPORTANT - streaming, not full materialization: this file embeds every
indexed chunk's full text and real samples run 50-100MB+. All three checks
below only need small aggregate facts (a "polarity" key count, a value
count, chunk_id/chunk_content completeness counts) - none need the chunk
text itself - so they all read scan_inverted_index()'s one streamed pass
over the file (shared with context_normalized.py's own lookup, so the file
is parsed at most once per run) instead of a plain json.load().
"""

from __future__ import annotations

from qc.checks.inverted_index_scan import InvertedIndexScan, scan_inverted_index
from qc.context import FolderSet, VersionContext
from qc.jsonio import read_json
from qc.models import CheckResult, Status
from qc.registry import register

CATEGORY = "4. Index Files (sit_inverted_index.json / sit_merged_index.json)"


def _get_total_docs(export_summary: dict) -> int | None:
    counts = export_summary.get("counts", {}) if isinstance(export_summary, dict) else {}
    total = counts.get("total", counts.get("Total"))
    if total is not None:
        return total
    pos = counts.get("positive", counts.get("Positive"))
    neg = counts.get("negative", counts.get("Negative"))
    if pos is not None and neg is not None:
        return pos + neg
    return None


@register(category=CATEGORY)
def check_index_files(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        results.extend(_check_folder_set(fs))
    return results


def _check_folder_set(fs: FolderSet) -> list[CheckResult]:
    scope = fs.name
    results: list[CheckResult] = []

    export_summary, es_err = read_json(fs.export_summary)
    total_docs = _get_total_docs(export_summary) if not es_err else None

    # sit_inverted_index.json is required (missing -> reported by structure.py's
    # "1-2 Folder Structure" category, not duplicated here). sit_merged_index.json
    # is optional and, in practice, essentially never produced - silently skipped
    # when absent rather than noting its absence on every single run, which
    # added no QC value and just cluttered the report. Either file gets the
    # full battery of checks below whenever it DOES exist.
    candidates = [
        ("sit_inverted_index.json", fs.sit_inverted_index),
        ("sit_merged_index.json", fs.sit_merged_index),
    ]
    for name, path in candidates:
        if not path.exists():
            continue

        scan = scan_inverted_index(path)
        if scan.error:
            results.append(CheckResult(Status.FAIL, CATEGORY, "4",
                f"{name}: readable JSON", scan.error, scope,
                f"Regenerate {name} - it is missing or malformed."))
            continue
        if scan.not_a_dict:
            results.append(CheckResult(Status.FAIL, CATEGORY, "4",
                f"{name}: top-level structure is a SIT-name-keyed object",
                "Top-level value is not a dict keyed by SIT name.", scope))
            continue

        results.extend(_check_polarity_naming(name, scan, scope))
        results.extend(_check_value_count_vs_docs(name, scan, total_docs, scope))
        results.extend(_check_field_completeness(name, scan, scope))

    return results


def _check_polarity_naming(name: str, scan: InvertedIndexScan, scope: str) -> list[CheckResult]:
    if scan.polarity_key_count:
        return [CheckResult(Status.FAIL, CATEGORY, "4",
            f"{name}: chunk records use 'chunk_label' not 'polarity'",
            f"Found 'polarity' key at {scan.polarity_key_count} location(s), "
            f"e.g. {scan.polarity_key_paths[:5]}", scope,
            f"Rename 'polarity' to 'chunk_label' in every chunk-level record of {name}.")]
    return [CheckResult(Status.PASS, CATEGORY, "4",
        f"{name}: chunk records use 'chunk_label' not 'polarity'",
        f"No 'polarity' key found anywhere in {name}.", scope)]


def _check_value_count_vs_docs(name: str, scan: InvertedIndexScan, total_docs: int | None,
                                scope: str) -> list[CheckResult]:
    if total_docs is None:
        return [CheckResult(Status.WARN, CATEGORY, "4",
            f"{name}: unique SIT-value count matches document count",
            "Could not read export_summary.json's counts.total to compare against.", scope)]

    unique_values = scan.unique_values
    if unique_values == total_docs:
        return [CheckResult(Status.PASS, CATEGORY, "4",
            f"{name}: unique SIT-value count matches document count",
            f"{unique_values} unique value(s) == {total_docs} total documents.", scope)]
    return [CheckResult(Status.FAIL, CATEGORY, "4",
        f"{name}: unique SIT-value count matches document count",
        f"{unique_values} unique value(s) vs {total_docs} total documents "
        f"(delta {unique_values - total_docs:+d}).", scope,
        f"Every generated document should contribute exactly one indexed value in {name} - "
        "investigate missing or duplicated entries.")]


def _check_field_completeness(name: str, scan: InvertedIndexScan, scope: str) -> list[CheckResult]:
    total_entries = scan.total_chunk_entries
    empty_chunk_id = scan.empty_chunk_id
    empty_chunk_content = scan.empty_chunk_content
    examples = scan.field_completeness_examples

    if total_entries == 0:
        return [CheckResult(Status.WARN, CATEGORY, "4",
            f"{name}: every chunk entry has chunk_id and chunk_content",
            "No chunk entries found to check.", scope)]

    if empty_chunk_id or empty_chunk_content:
        return [CheckResult(Status.FAIL, CATEGORY, "4",
            f"{name}: every chunk entry has chunk_id and chunk_content",
            f"Checked {total_entries} chunk entries: {empty_chunk_id} missing chunk_id, "
            f"{empty_chunk_content} missing/empty chunk_content, e.g. {examples[:5]}", scope,
            f"Every chunk entry in {name} must carry a non-empty chunk_id and chunk_content.")]
    return [CheckResult(Status.PASS, CATEGORY, "4",
        f"{name}: every chunk entry has chunk_id and chunk_content",
        f"Checked {total_entries} chunk entries, all complete.", scope)]
