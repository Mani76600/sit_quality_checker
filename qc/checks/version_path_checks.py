"""Addition: internal Version_* path references must match the actual
containing version folder.

Several files embed a full internal path that includes the run's own
Version_YYYYMMDD_HHMM segment - export_summary.json's output_dir, and each
per-doc metadata.json's raw_doc field plus docparser.raw_output/parsed/chunks
(confirmed exact field names against real Taiwan/South Africa/Sweden
samples). If pipeline output is ever copied, renamed, or merged from an
older run without regenerating these internal references, the embedded
Version_* stamp would silently go stale - this check catches that by
comparing the embedded stamp against the actual folder this file lives in.

No mismatch has been found in any real sample checked so far; this is a
forward-looking safety net, not a response to an observed defect.
"""

from __future__ import annotations

import re

from qc.context import FolderSet, PolarityOutputs, VersionContext
from qc.jsonio import read_json, read_json_cached
from qc.models import CheckResult, Status
from qc.registry import register
from qc.checks._common import metadata_files as _metadata_files
from qc.checks._common import sample as _sample

CATEGORY = "Version Path Consistency (addition)"

VERSION_RE = re.compile(r"Version_\d{8}_\d{4}")

# (human label, path to the value within the doc - dotted for nested fields)
PATH_FIELDS = [
    ("raw_doc", ("raw_doc",)),
    ("docparser.raw_output", ("docparser", "raw_output")),
    ("docparser.parsed", ("docparser", "parsed")),
    ("docparser.chunks", ("docparser", "chunks")),
]


def _get_nested(doc: dict, path: tuple[str, ...]):
    v = doc
    for key in path:
        if not isinstance(v, dict):
            return None
        v = v.get(key)
    return v


@register(category=CATEGORY)
def check_version_paths(ctx: VersionContext, options: dict) -> list[CheckResult]:
    expected_version = ctx.version_dir.name
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        results.extend(_check_export_summary(fs, expected_version))
        results.extend(_check_metadata(fs, "Positive", fs.positive, expected_version, options))
        results.extend(_check_metadata(fs, "Negative", fs.negative, expected_version, options))
    return results


def _check_export_summary(fs: FolderSet, expected_version: str) -> list[CheckResult]:
    scope = fs.name
    data, err = read_json(fs.export_summary)
    if err or not isinstance(data, dict):
        return []
    output_dir = data.get("output_dir")
    if not isinstance(output_dir, str):
        return []
    m = VERSION_RE.search(output_dir)
    if not m:
        return []
    if m.group(0) == expected_version:
        return [CheckResult(Status.PASS, CATEGORY, "addl",
            "export_summary.json's output_dir matches this version folder",
            f"{m.group(0)} == {expected_version}", scope)]
    return [CheckResult(Status.FAIL, CATEGORY, "addl",
        "export_summary.json's output_dir matches this version folder",
        f"output_dir references '{m.group(0)}' but this file lives under "
        f"'{expected_version}' - {output_dir}", scope,
        "Regenerate export_summary.json - output_dir is a stale reference to a "
        "different run.")]


def _check_metadata(fs: FolderSet, polarity_name: str, polarity: PolarityOutputs,
                     expected_version: str, options: dict) -> list[CheckResult]:
    scope = f"{fs.name} / {polarity_name}"
    files = _metadata_files(polarity)
    if not files:
        return []
    sampled = _sample(files, options)
    sample_note = f" (sampled {len(sampled)}/{len(files)} docs)" if len(sampled) < len(files) else ""

    mismatches: dict[str, list[str]] = {label: [] for label, _ in PATH_FIELDS}
    checked_any = False

    for path in sampled:
        doc, err = read_json_cached(path)
        if err or not isinstance(doc, dict):
            continue
        for label, field_path in PATH_FIELDS:
            value = _get_nested(doc, field_path)
            if not isinstance(value, str):
                continue
            m = VERSION_RE.search(value)
            if not m:
                continue
            checked_any = True
            if m.group(0) != expected_version and len(mismatches[label]) < 10:
                mismatches[label].append(f"{path.name}: found '{m.group(0)}', expected "
                                          f"'{expected_version}'")

    if not checked_any:
        return []

    results: list[CheckResult] = []
    for label, _ in PATH_FIELDS:
        bad = mismatches[label]
        if bad:
            results.append(CheckResult(Status.FAIL, CATEGORY, "addl",
                f"metadata '{label}' path matches this version folder{sample_note}",
                f"{len(bad)} doc(s) reference a different Version_* folder, e.g. {bad[:5]}", scope,
                f"'{label}' should always point into this same run's Version_* folder - "
                "regenerate the metadata for the listed docs, or investigate whether they "
                "were copied in from a different run."))
        else:
            results.append(CheckResult(Status.PASS, CATEGORY, "addl",
                f"metadata '{label}' path matches this version folder{sample_note}",
                f"All checked doc(s) reference '{expected_version}'.", scope))
    return results
