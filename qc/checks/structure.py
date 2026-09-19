"""Mandatory items 1-2: folder structure / required files.

Validates the layout described in the pipeline spec: every Version_* dir must
carry the four top-level export files + context_output_normalized/ at both
the Agreements (root) and Disagreements level, plus the Positive/Negative
outputs/{raw_doc,metadata,parsed_raw_doc/{chunks,plain_text}} subtree at
each level. SITGrader is required only under Disagreements, matching what the
pipeline actually produces (confirmed against real sample output - it is
never created at the Agreements/top level).
"""

from __future__ import annotations

from pathlib import Path

from qc.context import FolderSet, VersionContext
from qc.discovery import VERSION_DIR_RE
from qc.models import CheckResult, Status
from qc.registry import register

CATEGORY = "1-2. Folder Structure"


def _exists(path: Path, title: str, fix: str, scope: str, item_ref: str) -> CheckResult:
    if path.exists():
        return CheckResult(Status.PASS, CATEGORY, item_ref, title, f"Found: {path}", scope)
    return CheckResult(Status.FAIL, CATEGORY, item_ref, title, f"Missing: {path}", scope, fix)


@register(category=CATEGORY)
def check_structure(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []

    results.append(CheckResult(
        Status.INFO, CATEGORY, "1", "SIT name identification",
        f"SIT name: '{ctx.sit_name}'" + (f", language: '{ctx.language}'" if ctx.language else "")
        + f". Source: {ctx.sit_name_source}."
        + (f" {ctx.discovery_note}" if ctx.discovery_note else ""),
        ctx.label))

    if VERSION_DIR_RE.match(ctx.version_dir.name):
        results.append(CheckResult(
            Status.PASS, CATEGORY, "1", "Version directory naming",
            f"'{ctx.version_dir.name}' matches Version_YYYYMMDD_HHMM", ctx.label))
    else:
        results.append(CheckResult(
            Status.FAIL, CATEGORY, "1", "Version directory naming",
            f"'{ctx.version_dir.name}' does not match Version_YYYYMMDD_HHMM", ctx.label,
            "Rename the run directory to the Version_YYYYMMDD_HHMM convention."))

    for fs in ctx.folder_sets():
        results.extend(_check_folder_set(fs))
    return results


def _check_folder_set(fs: FolderSet) -> list[CheckResult]:
    results: list[CheckResult] = []
    scope = fs.name

    top_files = [
        (fs.combined_metadata, "combined_metadata.jsonl present"),
        (fs.corpus, "corpus.jsonl present"),
        (fs.export_summary, "export_summary.json present"),
        (fs.sit_inverted_index, "sit_inverted_index.json present"),
    ]
    for path, title in top_files:
        results.append(_exists(
            path, title,
            f"Regenerate/copy the missing file into {fs.root}", scope, "1"))

    if fs.context_output_normalized_dir.exists():
        json_files = fs.context_output_normalized_files()
        if json_files:
            results.append(CheckResult(
                Status.PASS, CATEGORY, "1", "context_output_normalized/ has JSON output",
                f"{len(json_files)} file(s): {', '.join(p.name for p in json_files)}", scope))
        else:
            results.append(CheckResult(
                Status.FAIL, CATEGORY, "1", "context_output_normalized/ has JSON output",
                f"Directory exists but contains no .json files: {fs.context_output_normalized_dir}",
                scope, "Re-run the normalized-context export step for this SIT."))
    else:
        results.append(CheckResult(
            Status.FAIL, CATEGORY, "1", "context_output_normalized/ present",
            f"Missing: {fs.context_output_normalized_dir}", scope,
            "Re-run the normalized-context export step for this SIT."))

    for polarity_name, polarity in (("Positive", fs.positive), ("Negative", fs.negative)):
        p_scope = f"{scope} / {polarity_name}"
        subpaths = [
            (polarity.raw_doc, "raw_doc/ present"),
            (polarity.metadata, "metadata/ present"),
            (polarity.parsed_raw_doc, "parsed_raw_doc/ present"),
            (polarity.chunks, "parsed_raw_doc/chunks/ present"),
            (polarity.plain_text, "parsed_raw_doc/plain_text/ present"),
        ]
        for path, title in subpaths:
            results.append(_exists(
                path, title, f"Re-run doc generation/parsing for {p_scope}.", p_scope, "2"))

    if fs.name == "Disagreements":
        if fs.sitgrader_dir.exists():
            chunk_runs = fs.chunk_run_dirs()
            if chunk_runs:
                results.append(CheckResult(
                    Status.PASS, CATEGORY, "1", "SITGrader/chunk_run_*/ present",
                    f"{len(chunk_runs)} run(s): {', '.join(p.name for p in chunk_runs)}", scope))
            else:
                results.append(CheckResult(
                    Status.FAIL, CATEGORY, "1", "SITGrader/chunk_run_*/ present",
                    f"SITGrader/ exists but has no chunk_run_* subdirectory: {fs.sitgrader_dir}",
                    scope, "Re-run SITGrader for this Disagreements set."))
            results.append(_exists(
                fs.sitgrader_misc, "SITGrader/misc/ present",
                f"Re-run SITGrader for this Disagreements set.", scope, "1"))
        else:
            results.append(CheckResult(
                Status.FAIL, CATEGORY, "1", "SITGrader/ present under Disagreements",
                f"Missing: {fs.sitgrader_dir}", scope,
                "Run SITGrader against this Disagreements set."))
    else:
        # Informational: SITGrader is expected only under Disagreements, per
        # confirmed pipeline behavior - a top-level one is not an error either
        # way, just noted so nobody assumes it is required here.
        if fs.sitgrader_dir.exists():
            results.append(CheckResult(
                Status.INFO, CATEGORY, "1", "Unexpected SITGrader/ at Agreements level",
                f"Found {fs.sitgrader_dir} - the pipeline normally only writes SITGrader "
                "output under Disagreements/. Not treated as a failure.", scope))

    return results
