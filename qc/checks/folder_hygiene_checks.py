"""Folder/file hygiene: flags anything that shouldn't be in an export folder,
complementing structure.py (which only ever asserts required things are
*present*, never that extras are *absent*).

Allowlists are intentionally generous - confirmed against real export output
plus reasonable generic artifact-naming patterns (mce_*, *_report.json, *.log,
...) a future pipeline stage could legitimately add. The goal is catching a
genuinely stray file (an editor backup, a half-written temp file, a
mis-copied folder) landing in a delivered export, not policing every new file
type this pipeline might ever produce.
"""

from __future__ import annotations

import fnmatch

from qc import file_cache
from qc.context import FolderSet, VersionContext
from qc.models import CheckResult, Status
from qc.registry import register

CATEGORY = "Folder/File Hygiene"
ITEM_REF = "addl"

# Confirmed real top-level dirs: Positive, Negative, Disagreements,
# context_output_normalized. SITGrader is also allowed here even though
# structure.py notes it's only ever expected under Disagreements - that's
# already surfaced there as its own (non-failing) note, not re-flagged here.
ALLOWED_TOP_LEVEL_DIRS = {
    "Positive", "Negative", "Disagreements",
    "context_output_normalized", "context_output_normalised",
    "SITGrader",
}
ALLOWED_TOP_LEVEL_FILE_PATTERNS = (
    "generation_log.json", "export_summary.json", "combined_metadata.jsonl",
    "corpus.jsonl", "sit_inverted_index.json", "mce_*.jsonl", "mce_*.csv",
    "semantic_*.jsonl", "sit_grader_*.csv", "*_sit_values.csv", "*.html",
    "*_report.json", "*.log",
)
METADATA_SIDECARS = {"index.jsonl"}


def _pass(title: str, scope: str, detail: str) -> CheckResult:
    return CheckResult(Status.PASS, CATEGORY, ITEM_REF, title, detail, scope)


def _fail(title: str, scope: str, detail: str, fix: str) -> CheckResult:
    return CheckResult(Status.FAIL, CATEGORY, ITEM_REF, title, detail, scope, fix)


def _unexpected_dirs(fs: FolderSet) -> list[str]:
    return sorted(
        p.name for p in file_cache.list_dir_dirs(fs.root)
        if p.name not in ALLOWED_TOP_LEVEL_DIRS and not p.name.startswith(("_", "."))
    )


def _unexpected_files(fs: FolderSet) -> list[str]:
    return sorted(
        p.name for p in file_cache.list_dir_files(fs.root)
        if not p.name.startswith(("_", "."))
        and not any(fnmatch.fnmatch(p.name, pat) for pat in ALLOWED_TOP_LEVEL_FILE_PATTERNS)
    )


def _stray_metadata_files(polarity) -> list[str]:
    return sorted(
        p.name for p in file_cache.list_dir_files(polarity.metadata)
        if not p.name.endswith(".json") and p.name not in METADATA_SIDECARS
    )


@register(category=CATEGORY)
def check_folder_hygiene(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        if not fs.root.exists():
            continue
        scope = fs.name

        unexpected_dirs = _unexpected_dirs(fs)
        if unexpected_dirs:
            results.append(_fail(
                "no unexpected top-level folders", scope,
                f"Unexpected folder(s): {', '.join(unexpected_dirs)}",
                f"Remove or relocate {', '.join(unexpected_dirs)} from {fs.root}, or add it "
                "to this tool's allowlist if it's a legitimate new pipeline artifact."))
        else:
            results.append(_pass(
                "no unexpected top-level folders", scope,
                f"Only recognized folder(s) present under {fs.root}"))

        unexpected_files = _unexpected_files(fs)
        if unexpected_files:
            results.append(_fail(
                "no unexpected top-level files", scope,
                f"Unexpected file(s): {', '.join(unexpected_files)}",
                f"Remove or relocate {', '.join(unexpected_files)} from {fs.root}, or add it "
                "to this tool's allowlist if it's a legitimate new pipeline artifact."))
        else:
            results.append(_pass(
                "no unexpected top-level files", scope,
                f"Only recognized file(s) present under {fs.root}"))

        for polarity_name, polarity in (("Positive", fs.positive), ("Negative", fs.negative)):
            if not polarity.metadata.exists():
                continue
            stray = _stray_metadata_files(polarity)
            p_scope = f"{scope} / {polarity_name}"
            if stray:
                results.append(_fail(
                    "metadata/ holds only .json files", p_scope,
                    f"Non-.json file(s) in metadata/: {', '.join(stray[:10])}"
                    + (f" (+{len(stray) - 10} more)" if len(stray) > 10 else ""),
                    f"Remove stray file(s) from {polarity.metadata} - only per-document "
                    "*.json (plus index.jsonl) belong there."))
            else:
                results.append(_pass(
                    "metadata/ holds only .json files", p_scope,
                    f"No stray files in {polarity.metadata}"))
    return results
