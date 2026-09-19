"""Additions: whole-tree file integrity - zero-byte files, sampled JSON
validity for per-document files, and a one-time note about the pipeline's
mixed boolean encodings (real bool vs "True"/"False" string) so future
maintainers of this checker aren't caught out by it.

By default this samples per-document files the same way every other check
does (bounded by chunk_sample_size) rather than statting every file in the
tree - on a ~14k-document corpus a full rglob() zero-byte scan alone took
~40s in testing, which read as "the app is frozen" with no progress
feedback. Check "Exhaustive per-document scan" in the sidebar to fall back
to a full, unsampled scan of every file.
"""

from __future__ import annotations

from pathlib import Path

from qc import file_cache
from qc.context import PolarityOutputs, VersionContext
from qc.jsonio import read_json_cached
from qc.models import CheckResult, Status
from qc.registry import register
from qc.checks._common import sample as _sample

CATEGORY = "Integrity (addition)"

MAX_ZERO_BYTE_REPORTED = 20


@register(category=CATEGORY)
def check_integrity(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    exhaustive = bool(options.get("exhaustive_chunk_scan", False))

    if exhaustive:
        results.append(_full_zero_byte_scan(ctx))
    else:
        results.append(_top_level_zero_byte_scan(ctx))

    for fs in ctx.folder_sets():
        for polarity_name, polarity in (("Positive", fs.positive), ("Negative", fs.negative)):
            results.extend(_check_polarity_files(fs.name, polarity_name, polarity, options))

    results.append(CheckResult(Status.INFO, CATEGORY, "addl",
        "Boolean-field type note",
        "This pipeline mixes real JSON booleans (e.g. chunk snippet 'label'/'sit_found') with "
        "string-typed booleans (e.g. instance 'expected_engine_match', 'semantic_ground_truth' "
        "are \"True\"/\"False\" strings, not JSON true/false). All checks in this tool normalize "
        "via qc.jsonio.to_bool - be aware of this when adding new checks.", ctx.label))

    return results


def _full_zero_byte_scan(ctx: VersionContext) -> CheckResult:
    zero_byte: list[str] = []
    total_files = 0
    if ctx.version_dir.exists():
        for p in ctx.version_dir.rglob("*"):
            if p.is_file():
                total_files += 1
                try:
                    if p.stat().st_size == 0:
                        if len(zero_byte) < MAX_ZERO_BYTE_REPORTED:
                            zero_byte.append(str(p))
                except OSError:
                    continue
    return _zero_byte_result(zero_byte, total_files, ctx.label, exhaustive=True)


def _top_level_zero_byte_scan(ctx: VersionContext) -> CheckResult:
    """Cheap default: only the handful of top-level export files per folder set."""
    zero_byte: list[str] = []
    total_files = 0
    for fs in ctx.folder_sets():
        candidates = [
            fs.combined_metadata, fs.corpus, fs.export_summary,
            fs.sit_inverted_index, fs.sit_merged_index,
        ] + fs.context_output_normalized_files()
        for p in candidates:
            if p.exists() and p.is_file():
                total_files += 1
                try:
                    if p.stat().st_size == 0:
                        zero_byte.append(str(p))
                except OSError:
                    continue
    return _zero_byte_result(zero_byte, total_files, ctx.label, exhaustive=False)


def _zero_byte_result(zero_byte: list[str], total_files: int, label: str, exhaustive: bool) -> CheckResult:
    scope_note = "" if exhaustive else " (top-level export files only; enable 'exhaustive' for a full scan)"
    if zero_byte:
        more = "" if len(zero_byte) < MAX_ZERO_BYTE_REPORTED else " (+more, list truncated)"
        return CheckResult(Status.FAIL, CATEGORY, "addl",
            f"No zero-byte files{scope_note}",
            f"{len(zero_byte)} zero-byte file(s) found{more}: {zero_byte[:10]}", label,
            "Zero-byte files indicate a truncated write - regenerate them.")
    return CheckResult(Status.PASS, CATEGORY, "addl",
        f"No zero-byte files{scope_note}",
        f"Scanned {total_files} file(s).", label)


def _check_polarity_files(fs_name: str, polarity_name: str, polarity: PolarityOutputs,
                           options: dict) -> list[CheckResult]:
    scope = f"{fs_name} / {polarity_name}"
    results: list[CheckResult] = []
    exhaustive = bool(options.get("exhaustive_chunk_scan", False))

    groups: list[tuple[str, list[Path], bool]] = [
        ("raw_doc/*", file_cache.list_dir_files(polarity.raw_doc), False),
        ("metadata/*.json", file_cache.list_dir(polarity.metadata, "*.json"), True),
        ("parsed_raw_doc/*.json", file_cache.list_dir(polarity.parsed_raw_doc, "*.json"), True),
        ("parsed_raw_doc/chunks/*.json", file_cache.list_dir(polarity.chunks, "*.json"), True),
        ("parsed_raw_doc/plain_text/*", file_cache.list_dir_files(polarity.plain_text), False),
    ]
    for label, files, is_json in groups:
        if not files:
            continue
        sampled = files if exhaustive else _sample(files, options)
        sample_note = f" (sampled {len(sampled)}/{len(files)})" if len(sampled) < len(files) else ""

        zero_byte = []
        bad_json = []
        for path in sampled:
            try:
                if path.stat().st_size == 0:
                    zero_byte.append(path.name)
                    continue
            except OSError:
                continue
            if is_json:
                _, err = read_json_cached(path)
                if err:
                    bad_json.append(err)

        if zero_byte:
            results.append(CheckResult(Status.FAIL, CATEGORY, "addl",
                f"{label}: no zero-byte files{sample_note}",
                f"{len(zero_byte)} zero-byte file(s), e.g. {zero_byte[:5]}", scope,
                "Zero-byte files indicate a truncated write - regenerate them."))
        else:
            results.append(CheckResult(Status.PASS, CATEGORY, "addl",
                f"{label}: no zero-byte files{sample_note}",
                f"Checked {len(sampled)} file(s), none empty.", scope))

        if is_json:
            if bad_json:
                results.append(CheckResult(Status.FAIL, CATEGORY, "addl",
                    f"{label}: all files parse as valid JSON{sample_note}",
                    f"{len(bad_json)} file(s) failed to parse, e.g. {bad_json[:5]}", scope,
                    "Regenerate the malformed/truncated file(s)."))
            else:
                results.append(CheckResult(Status.PASS, CATEGORY, "addl",
                    f"{label}: all files parse as valid JSON{sample_note}",
                    f"Checked {len(sampled)} file(s), all valid.", scope))

    return results
