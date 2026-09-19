"""Item 9: raw_doc count parity across raw_doc/metadata/parsed_raw_doc, plus
the SIT-identity check.

Per the user's decision: real pipeline filenames never embed a SIT number
(content-slug + random hex only), so the "SIT number prefix/suffix in
filename" check is reported as INFO, not PASS/FAIL. In its place, the
substantive check verifies SIT identity through metadata linkage
(sit_name/entity_guid inside each doc's metadata json), which is the same
underlying intent (does this doc really belong to this SIT) via a field
that actually carries that information in the real pipeline output.
"""

from __future__ import annotations

import re
from pathlib import Path

from qc import file_cache
from qc.context import FolderSet, PolarityOutputs, VersionContext
from qc.jsonio import count_jsonl_lines, read_json_cached, read_jsonl_cached
from qc.models import CheckResult, Status
from qc.registry import register

CATEGORY = "9. raw_doc Parity & SIT Identity"


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


@register(category=CATEGORY)
def check_raw_doc_parity(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        for polarity_name, polarity in (("Positive", fs.positive), ("Negative", fs.negative)):
            results.extend(_check_parity(fs, polarity_name, polarity))
            results.extend(_check_sit_identity(ctx, fs, polarity_name, polarity))
    results.extend(_check_agreements_disagreements_disjoint(ctx))
    return results


def _check_agreements_disagreements_disjoint(ctx: VersionContext) -> list[CheckResult]:
    """Agreements and Disagreements are two different partitions of the same
    run - a raw_doc filename should never appear in both (confirmed against
    real sample output: zero overlap across all polarity combinations)."""
    results: list[CheckResult] = []
    agr, dis = ctx.agreements, ctx.disagreements

    for label_a, polarity_a in (("Positive", agr.positive), ("Negative", agr.negative)):
        for label_b, polarity_b in (("Positive", dis.positive), ("Negative", dis.negative)):
            a_files = {p.name for p in file_cache.list_dir_files(polarity_a.raw_doc)}
            b_files = {p.name for p in file_cache.list_dir_files(polarity_b.raw_doc)}
            if not a_files or not b_files:
                continue
            overlap = a_files & b_files
            scope = f"Agreements/{label_a} vs Disagreements/{label_b}"
            if overlap:
                sample = sorted(overlap)[:10]
                results.append(CheckResult(Status.FAIL, CATEGORY, "3",
                    "Agreements and Disagreements raw_doc filenames are disjoint",
                    f"{len(overlap)} filename(s) appear in both, e.g. {sample}", scope,
                    "A document should belong to exactly one of Agreements or "
                    "Disagreements, never both - investigate how these files ended up "
                    "duplicated across the partition."))
            else:
                results.append(CheckResult(Status.PASS, CATEGORY, "3",
                    "Agreements and Disagreements raw_doc filenames are disjoint",
                    f"No overlap between {len(a_files)} Agreements/{label_a} and "
                    f"{len(b_files)} Disagreements/{label_b} filenames.", scope))
    return results


def _report_set_diff(results: list[CheckResult], item_ref: str, title: str, scope: str,
                      expected: set[str], actual: set[str], expected_label: str,
                      actual_label: str, fix: str) -> None:
    missing = expected - actual   # in expected (e.g. raw_doc) but not in actual (e.g. metadata)
    extra = actual - expected     # orphans present in actual but with no matching expected entry
    if not missing and not extra:
        results.append(CheckResult(Status.PASS, CATEGORY, item_ref, title,
            f"{len(expected)} {expected_label} <-> {len(actual)} {actual_label}: exact match.",
            scope))
        return
    parts = []
    if missing:
        parts.append(f"{len(missing)} {expected_label} with no matching {actual_label}, "
                      f"e.g. {sorted(missing)[:5]}")
    if extra:
        parts.append(f"{len(extra)} orphaned {actual_label} with no matching {expected_label}, "
                      f"e.g. {sorted(extra)[:5]}")
    results.append(CheckResult(Status.FAIL, CATEGORY, item_ref, title, "; ".join(parts), scope, fix))


def _check_parity(fs: FolderSet, polarity_name: str, polarity: PolarityOutputs) -> list[CheckResult]:
    scope = f"{fs.name} / {polarity_name}"
    results: list[CheckResult] = []

    if not polarity.root.exists():
        return results  # reported by structure.py

    raw_files = file_cache.list_dir_files(polarity.raw_doc)
    raw_stems = {p.stem for p in raw_files}         # e.g. "doc-abc123"
    raw_full_names = {p.name for p in raw_files}     # e.g. "doc-abc123.docx"

    metadata_stems = {p.stem for p in file_cache.list_dir(polarity.metadata, "*.json")}
    parsed_top_stems = {p.stem for p in file_cache.list_dir(polarity.parsed_raw_doc, "*.json")}
    # chunks/*.json and plain_text/* are named "<raw filename with ext>.json"/".txt" -
    # stripping just that one suffix gives back the raw_doc's full filename (incl. ext),
    # not its stem (confirmed against real samples: "doc.docx.json".stem == "doc.docx").
    chunk_source_names = {p.stem for p in file_cache.list_dir(polarity.chunks, "*.json")}
    plain_text_source_names = {p.stem for p in file_cache.list_dir_files(polarity.plain_text)}

    # Item 6: metadata/index.jsonl must exist and cover exactly the same
    # documents as the individual metadata/*.json files (matched by 'stem').
    if polarity.metadata_index.exists():
        index_count, index_errors = count_jsonl_lines(polarity.metadata_index)
        for e in index_errors[:5]:
            results.append(CheckResult(Status.FAIL, CATEGORY, "6",
                "metadata/index.jsonl valid JSON per line", e, scope,
                "Fix or regenerate the malformed line(s)."))
        index_records, _ = read_jsonl_cached(polarity.metadata_index)
        index_stems = {r.get("stem") for r in index_records if isinstance(r, dict) and r.get("stem")}
        _report_set_diff(results, "6", "metadata/index.jsonl covers every metadata/*.json doc",
                          scope, metadata_stems, index_stems,
                          "metadata/*.json file(s)", "metadata/index.jsonl row(s)",
                          "Every per-doc metadata.json should have exactly one corresponding "
                          "row in metadata/index.jsonl, matched by 'stem' - regenerate the index.")
    else:
        results.append(CheckResult(Status.FAIL, CATEGORY, "6", "metadata/index.jsonl present",
            f"Missing: {polarity.metadata_index}", scope,
            "metadata/index.jsonl is required alongside the individual per-doc metadata "
            "json files."))

    # Item 8: exact set-based cross-referencing (not just counts) across every
    # pipeline stage, so a genuine orphan/missing file surfaces by name.
    _report_set_diff(results, "8", "raw_doc/ <-> metadata/*.json", scope,
                      raw_stems, metadata_stems, "raw_doc file(s)", "metadata json(s)",
                      "Every raw_doc file must have exactly one metadata/<stem>.json, and "
                      "vice versa - regenerate the missing side or remove the orphan.")
    _report_set_diff(results, "8", "raw_doc/ <-> parsed_raw_doc/*.json", scope,
                      raw_stems, parsed_top_stems, "raw_doc file(s)", "parsed_raw_doc json(s)",
                      "Every raw_doc file must have exactly one parsed_raw_doc/<stem>.json, and "
                      "vice versa - regenerate the missing side or remove the orphan.")
    _report_set_diff(results, "8", "raw_doc/ <-> parsed_raw_doc/chunks/*.json", scope,
                      raw_full_names, chunk_source_names, "raw_doc file(s)", "chunk json(s)",
                      "Every raw_doc file must have exactly one parsed_raw_doc/chunks/<filename>."
                      "json, and vice versa - regenerate the missing side or remove the orphan.")
    _report_set_diff(results, "8", "raw_doc/ <-> parsed_raw_doc/plain_text/*", scope,
                      raw_full_names, plain_text_source_names, "raw_doc file(s)", "plain_text file(s)",
                      "Every raw_doc file must have exactly one parsed_raw_doc/plain_text/"
                      "<filename>.txt, and vice versa - regenerate the missing side or remove "
                      "the orphan.")

    return results


def _check_sit_identity(ctx: VersionContext, fs: FolderSet, polarity_name: str,
                         polarity: PolarityOutputs) -> list[CheckResult]:
    scope = f"{fs.name} / {polarity_name}"
    results: list[CheckResult] = []

    if not polarity.raw_doc.exists():
        return results

    results.append(CheckResult(Status.INFO, CATEGORY, "9 (filename)",
        "SIT number as filename prefix/suffix",
        "N/A for this pipeline: raw_doc filenames are a content-slug + random hex "
        "(e.g. 'academic-record-...-65fd3ec6cf55.docx') and never embed a SIT number. "
        "SIT identity is verified via metadata linkage instead (see next check).", scope))

    files = file_cache.list_dir(polarity.metadata, "*.json")
    if not files:
        return results
    sample = files[:200]

    sit_names = set()
    entity_guids = set()
    for path in sample:
        doc, err = read_json_cached(path)
        if err or not isinstance(doc, dict):
            continue
        sn = doc.get("sit_name")
        if isinstance(sn, str):
            sit_names.add(sn)
        eg = doc.get("entity_guid")
        if isinstance(eg, str):
            entity_guids.add(eg)

    sample_note = f" (sampled {len(sample)}/{len(files)} docs)" if len(sample) < len(files) else ""

    if len(sit_names) > 1 or len(entity_guids) > 1:
        results.append(CheckResult(Status.FAIL, CATEGORY, "9",
            f"Docs belong to a single, consistent SIT{sample_note}",
            f"Multiple distinct sit_name/entity_guid values found in metadata: "
            f"sit_name={sit_names}, entity_guid={entity_guids}", scope,
            "This folder's documents should all belong to one SIT - investigate cross-"
            "contamination from another SIT's generation run."))
    elif sit_names or entity_guids:
        sit_name = next(iter(sit_names), None)
        if sit_name and _normalize(sit_name) != _normalize(ctx.sit_name):
            results.append(CheckResult(Status.WARN, CATEGORY, "9",
                f"Metadata sit_name matches folder SIT name{sample_note}",
                f"Metadata sit_name '{sit_name}' vs folder-derived SIT name "
                f"'{ctx.sit_name}'.", scope,
                "Confirm this is intentional (e.g. a display-name vs folder-name "
                "difference) rather than a misplaced/misnamed output folder."))
        else:
            results.append(CheckResult(Status.PASS, CATEGORY, "9",
                f"Docs consistently linked to their SIT via metadata{sample_note}",
                f"sit_name={sit_names or '(n/a)'}, entity_guid consistent "
                f"({len(entity_guids)} distinct value(s)).", scope))
    else:
        results.append(CheckResult(Status.WARN, CATEGORY, "9",
            f"Docs consistently linked to their SIT via metadata{sample_note}",
            "No sit_name/entity_guid field found in sampled metadata to verify against.",
            scope))

    return results
