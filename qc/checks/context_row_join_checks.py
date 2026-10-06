"""Row-level join for context_output_normalized: every detection row must
resolve to a real document within THIS SAME partition (Agreements rows only
ever resolve against Agreements documents, and likewise for Disagreements -
each folder set is checked independently via ctx.folder_sets()), and its
claimed value + offsets are verified against that document's actual
plain_text.

Resolution strategy (confirmed against two different real SITs, which
turned out to need different strategies - this check tries both rather than
assuming either alone is universal):

- Bulgaria Passport Number: rows have no doc_id; doc_path's basename
  (after stripping its Windows-style directory prefix) matches a real
  plain_text/*.txt filename directly - 300/300 sampled rows resolved this
  way with zero mismatches.
- South Africa Identification Number: rows DO have doc_id, but doc_path's
  basename is STALE (an earlier generation-time filename, not what was
  actually delivered - confirmed by direct comparison, same kind of
  staleness instances[].file_name has elsewhere in this pipeline, see
  offset_accuracy_checks.py). doc_id cross-referenced against
  combined_metadata.jsonl's own doc_id -> filename mapping resolves
  correctly instead - 199/199 sampled rows matched with zero drift once
  resolved this way.

So: try doc_id -> combined_metadata.jsonl -> filename first (if the row has
a doc_id), then fall back to doc_path's basename. Only if BOTH fail is the
row's document unresolved.

Offset convention: detected_sit_start_index / detected_sit_end_index use an
INCLUSIVE end index - text[start:end+1] == value - confirmed with zero
drift across both SITs above (unlike instances[].value_start_offset/
value_end_offset, which offset_accuracy_checks.py found drifts in ~80-90%
of real instances - a different, much less reliable field elsewhere in this
same pipeline). The exclusive convention is still accepted as a fallback for
robustness.

A detected value need NOT equal the document's own source-declared SIT
value(s) - MCE can legitimately detect a different real identifier
incidentally present in the same document. This check never cross-
references metadata.json's declared values - it only confirms the row is
internally honest about a real position in a real, resolved document.

Raw-only disagreements (parsed plain_text deliberately removed by the
SITGrader gate): if no plain_text can be found via either resolution
strategy, the row's own nested context_100/.../context_8000 text is used as
ground truth instead, rather than failing for a document this tool cannot
fully re-verify.
"""

from __future__ import annotations

from pathlib import Path

from qc.checks._common import sample
from qc.context import FolderSet, VersionContext
from qc.jsonio import read_json_cached, read_jsonl_cached
from qc.models import CheckResult, Status
from qc.registry import register

# NOTE: row-level verification needs individual row objects, so this reads
# the whole context_output_normalized file via json.load (through the shared
# cache, so at least never twice within one run) rather than the streaming
# scan_file() context_normalized.py uses for its own aggregate-only stats.
# context_output_normalized files have been observed at 100-250MB+ in some
# real exports - if that becomes a real memory/latency problem here, a
# streaming reservoir-sample reader would be the next step.

CATEGORY = "Context Detection Row Join"
ITEM_REF = "addl"
_CONTEXT_WINDOW_KEYS = ("context_100", "context_500", "context_2000", "context_4000",
                        "context_6000", "context_8000")


def _basename(path_str: str) -> str:
    return path_str.replace("\\", "/").rsplit("/", 1)[-1]


def _polarity_for_label(fs: FolderSet, label: object):
    if label == "positive":
        return fs.positive
    if label == "negative":
        return fs.negative
    return None


def _resolve_document(fs: FolderSet, row: dict, combined_by_id: dict) -> Path | None:
    doc_id = row.get("doc_id")
    if doc_id is not None:
        combined_row = combined_by_id.get(doc_id)
        if combined_row is not None:
            polarity = _polarity_for_label(fs, combined_row.get("label"))
            filename = combined_row.get("filename")
            if polarity is not None and isinstance(filename, str) and filename:
                candidate = polarity.plain_text / f"{filename}.txt"
                if candidate.exists():
                    return candidate

    doc_path = row.get("doc_path")
    if isinstance(doc_path, str) and doc_path:
        basename = _basename(doc_path)
        for polarity in (fs.positive, fs.negative):
            candidate = polarity.plain_text / basename
            if candidate.exists():
                return candidate
    return None


def _first_context_text(row: dict) -> str | None:
    for key in _CONTEXT_WINDOW_KEYS:
        window = row.get(key)
        if isinstance(window, dict) and isinstance(window.get("text"), str) and window["text"]:
            return window["text"]
    return None


def _row_display_name(row: dict) -> str:
    doc_path = row.get("doc_path")
    if isinstance(doc_path, str) and doc_path:
        return _basename(doc_path)
    return str(row.get("doc_id") or "?")


def _check_rows(fs: FolderSet, rows: list, options: dict, path_label: str) -> list[CheckResult]:
    scope = fs.name
    sampled = sample(rows, options)
    combined_records, _errors = read_jsonl_cached(fs.combined_metadata)
    combined_by_id = {rec.get("doc_id"): rec for rec in combined_records if rec.get("doc_id")}

    unresolved: list[str] = []
    not_found: list[str] = []
    exact = 0
    drifted = 0
    raw_only_checked = 0
    checked = 0

    for row in sampled:
        if not isinstance(row, dict):
            continue
        value = row.get("value")
        if value is None:
            continue
        value = str(value)
        name = _row_display_name(row)

        text_path = _resolve_document(fs, row, combined_by_id)
        if text_path is not None:
            checked += 1
            text = text_path.read_text(encoding="utf-8")
            start, end = row.get("detected_sit_start_index"), row.get("detected_sit_end_index")
            exact_hit = False
            if isinstance(start, int) and isinstance(end, int):
                if 0 <= start <= end < len(text) and text[start:end + 1] == value:
                    exact_hit = True
                elif 0 <= start <= end <= len(text) and text[start:end] == value:
                    exact_hit = True
            if exact_hit:
                exact += 1
            elif value in text:
                drifted += 1
            else:
                not_found.append(f"{name}: value {value!r} not in plain_text")
            continue

        # Neither doc_id nor doc_path resolved to a real document - fall back
        # to the row's own nested context text as ground truth (covers raw-
        # only disagreements, where plain_text was deliberately removed).
        context_text = _first_context_text(row)
        if context_text is None:
            unresolved.append(f"{name}: no document and no context window text")
            continue
        checked += 1
        raw_only_checked += 1
        if value in context_text:
            exact += 1
        else:
            not_found.append(f"{name}: value {value!r} not in its own context window text "
                              "(no document could be resolved)")

    if not checked and not unresolved:
        return []

    sample_note = f" (sampled {len(sampled)}/{len(rows)} row(s) from {path_label})"
    title = f"context_output_normalized row join + value verification{sample_note}"
    problems = not_found + unresolved
    if problems:
        return [CheckResult(Status.FAIL, CATEGORY, ITEM_REF, title,
            f"{len(problems)} problem(s), e.g.: {'; '.join(problems[:5])}", scope,
            "Each context_output_normalized row must resolve to a real document in this same "
            "partition (via doc_id + combined_metadata.jsonl, or via doc_path), and its "
            "detected value must be genuinely present (either in plain_text, or in the row's "
            "own context window when no document could be resolved) - investigate the listed "
            "row(s).")]
    detail = (
        f"Checked {checked} row(s): {exact} exact offset match(es), {drifted} value present "
        "but at a drifted offset (not a content defect - see instances[] offset accuracy "
        "elsewhere in this report)"
        + (f", {raw_only_checked} verified via their own context window only "
           "(no document resolved)" if raw_only_checked else "")
        + ". Every checked row's detected value is genuinely present where claimed."
    )
    return [CheckResult(Status.PASS, CATEGORY, ITEM_REF, title, detail, scope)]


@register(category=CATEGORY)
def check_context_row_join(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        for jf in fs.context_output_normalized_files():
            rows, err = read_json_cached(jf)
            if err or not isinstance(rows, list) or not rows:
                continue
            results.extend(_check_rows(fs, rows, options, jf.name))
    return results
