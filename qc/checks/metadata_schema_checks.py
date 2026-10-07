"""Per-document metadata schema self-consistency, plus combined_metadata.jsonl
vs individual metadata.json field agreement.

Schema confirmed against real Bulgaria Passport Number / South Africa
Identification Number output (not assumed from an external tool meant for a
different pipeline): every metadata.json / combined_metadata.jsonl row
carries its own doc_id, stem, filename, file_ext, file_format, sit_name,
sit_category, label, language, sit_values, lookalike_values - a flat,
self-contained record. There is no source_filename/source_stem concept in
this pipeline's real output (unlike some external schemas), so that aliasing
isn't checked here.

Deliberately NOT implemented here: validating instances[].value against
instances[].file_name's plain_text via value_start_offset/value_end_offset.
Real sample data shows instances[].file_name routinely differs from the
metadata.json's own filename - instances[] is a corpus-wide value-placement
pool (already treated this way by instance_field_checks.py, which never
joins instance.file_name back to "this" document), not per-document data.
Joining instance -> its own file_name's plain_text would require first
confirming that join is even valid pipeline-wide, which no existing check
does - attempting it here without that confirmation risked mass false
failures across real output, so it's left for a follow-up once that's
resolved.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from qc.checks._common import metadata_files, sample
from qc.context import FolderSet, PolarityOutputs, VersionContext
from qc.jsonio import read_json_cached, read_jsonl_cached
from qc.models import CheckResult, Status
from qc.registry import register

CATEGORY_SELF = "Metadata Schema Self-Consistency"
CATEGORY_JOIN = "combined_metadata.jsonl vs Individual metadata.json Join"
ITEM_REF = "addl"

# A handful of file_format values legitimately don't match their real file
# extension one-for-one - fixed_width is a plain-text representation, so its
# genuinely correct delivered extension is .txt, not .fixed_width. Confirmed
# a false positive on real Argentina DNI output (file_format=fixed_width,
# file_ext=.txt flagged as a mismatch when it's actually correct) - .txt is
# the expected extension for this format, not a pipeline defect.
_FORMAT_TO_EXPECTED_EXT = {
    "fixed_width": "txt",
}

# Fields confirmed present on both combined_metadata.jsonl rows and
# individual metadata.json files, cheap/meaningful to cross-check for exact
# agreement (identity + classification fields - not every single one of the
# ~45 real fields, to keep this focused on what a divergence would actually
# matter for).
JOIN_FIELDS = (
    "sit_name", "sit_category", "label", "language", "filename", "stem",
    "file_ext", "file_format", "entity_guid",
)


def _basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _check_one_metadata_schema(doc: dict, doc_name: str, scope: str) -> list[CheckResult]:
    problems = []
    for key in ("doc_id", "stem", "filename"):
        value = doc.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{doc_name}: {key!r} is missing/blank")

    filename = doc.get("filename")
    if isinstance(filename, str) and filename.strip():
        if _basename(filename) != filename:
            problems.append(f"{doc_name}: filename {filename!r} is not a bare basename")
        stem = doc.get("stem")
        if isinstance(stem, str) and PurePosixPath(filename).stem != stem:
            problems.append(
                f"{doc_name}: filename stem {PurePosixPath(filename).stem!r} "
                f"!= stem field {stem!r}")
        file_ext = doc.get("file_ext")
        if isinstance(file_ext, str) and file_ext.strip():
            actual_suffix = PurePosixPath(filename).suffix
            if actual_suffix != file_ext and actual_suffix != "." + file_ext.lstrip("."):
                problems.append(
                    f"{doc_name}: filename extension {actual_suffix!r} != file_ext {file_ext!r}")
            file_format = doc.get("file_format")
            if isinstance(file_format, str) and file_format.strip():
                format_key = file_format.strip().lower()
                expected_ext = _FORMAT_TO_EXPECTED_EXT.get(format_key, format_key)
                if expected_ext != file_ext.strip().lstrip(".").lower():
                    problems.append(
                        f"{doc_name}: file_format {file_format!r} != file_ext {file_ext!r}")
    return problems


def _check_schema_one(fs: FolderSet, polarity_name: str, polarity: PolarityOutputs,
                       options: dict) -> list[CheckResult]:
    scope = f"{fs.name} / {polarity_name}"
    files = metadata_files(polarity)
    if not files:
        return []
    sampled = sample(files, options)
    problems: list[str] = []
    for path in sampled:
        doc, err = read_json_cached(path)
        if err or not isinstance(doc, dict):
            continue  # reported by integrity_checks.py
        problems.extend(_check_one_metadata_schema(doc, path.name, scope))

    sample_note = f" (sampled {len(sampled)}/{len(files)} docs)" if len(sampled) < len(files) else ""
    title = f"metadata.json self-consistency (doc_id/stem/filename/file_ext/file_format){sample_note}"
    if problems:
        return [CheckResult(Status.FAIL, CATEGORY_SELF, ITEM_REF, title,
            f"{len(problems)} issue(s), e.g.: " + "; ".join(problems[:5]), scope,
            "Regenerate the affected metadata.json file(s) so filename/stem/file_ext/"
            "file_format agree with each other.")]
    return [CheckResult(Status.PASS, CATEGORY_SELF, ITEM_REF, title,
        f"Checked {len(sampled)} file(s), all internally consistent.", scope)]


@register(category=CATEGORY_SELF)
def check_metadata_self_consistency(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        for polarity_name, polarity in (("Positive", fs.positive), ("Negative", fs.negative)):
            results.extend(_check_schema_one(fs, polarity_name, polarity, options))
    return results


def _polarity_for(fs: FolderSet, label: object) -> PolarityOutputs | None:
    if label == "positive":
        return fs.positive
    if label == "negative":
        return fs.negative
    return None


def _check_join_one(fs: FolderSet, options: dict) -> list[CheckResult]:
    scope = fs.name
    combined_records, _errors = read_jsonl_cached(fs.combined_metadata)
    if not combined_records:
        return []
    combined_by_id = {
        rec.get("doc_id"): rec for rec in combined_records if rec.get("doc_id")
    }
    sampled_ids = sample(sorted(combined_by_id), options)

    mismatches: list[str] = []
    checked = 0
    for doc_id in sampled_ids:
        combined_row = combined_by_id[doc_id]
        polarity = _polarity_for(fs, combined_row.get("label"))
        stem = combined_row.get("stem")
        if polarity is None or not isinstance(stem, str) or not stem:
            continue
        path = polarity.metadata / f"{stem}.json"
        doc, err = read_json_cached(path)
        if err or not isinstance(doc, dict):
            continue
        checked += 1
        for field in JOIN_FIELDS:
            if field not in doc and field not in combined_row:
                continue
            if doc.get(field) != combined_row.get(field):
                mismatches.append(
                    f"{path.name} ({doc_id}): {field}={doc.get(field)!r} in metadata.json "
                    f"but {combined_row.get(field)!r} in combined_metadata.jsonl")

    if not checked:
        return []
    sample_note = f" (sampled {checked}/{len(combined_by_id)} docs)" if checked < len(combined_by_id) else ""
    title = f"combined_metadata.jsonl row matches individual metadata.json{sample_note}"
    if mismatches:
        return [CheckResult(Status.FAIL, CATEGORY_JOIN, ITEM_REF, title,
            f"{len(mismatches)} field mismatch(es), e.g.: " + "; ".join(mismatches[:5]), scope,
            "combined_metadata.jsonl has drifted from the individual metadata.json file(s) it "
            "was aggregated from - regenerate combined_metadata.jsonl from the current "
            "individual metadata files.")]
    return [CheckResult(Status.PASS, CATEGORY_JOIN, ITEM_REF, title,
        f"Checked {checked} doc(s) across {len(JOIN_FIELDS)} shared field(s), all agree.", scope)]


@register(category=CATEGORY_JOIN)
def check_combined_individual_join(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        results.extend(_check_join_one(fs, options))
    return results
