"""instances[].value_start_offset/value_end_offset accuracy against the
actual delivered plain_text.

Real-data investigation (300 real Bulgaria Passport Number instances, 150
Positive + 150 Negative) found these offsets exactly select their own
instances[].value in only ~18% (positive) / ~9% (negative) of cases - but in
every sampled mismatch, the value WAS still present in the text, just at a
different position (typical drift: a handful to a few hundred characters).
This means the offsets were very likely computed against an earlier/different
text representation than the final parsed plain_text.txt (e.g. before some
docparser normalization step), not that the planted value itself is missing
or wrong - a real, but non-actionable, staleness in this one piece of
metadata, not a content-integrity defect.

Given that, this is NOT implemented as a FAIL-on-mismatch gate (it would
false-fail the large majority of real, correct documents) - only the
genuinely broken case (value not found anywhere in the text at all) is a
FAIL. The exact-offset-match rate is still measured and reported (as a PASS
with the finding spelled out in the detail), so the drift stays visible
instead of silently unverified.

Also confirmed: instances[].file_name routinely points at an unrelated
document (not this one) - instances[] is a corpus-wide value-placement pool,
already treated this way by instance_field_checks.py. instances[].doc_id,
by contrast, was confirmed to always equal the owning metadata.json's own
doc_id across every sample checked - that is the reliable key used here to
decide "does this instance actually describe a value in THIS document",
and this document's own plain_text (via its own filename) is what's checked
against, never instance.file_name.
"""

from __future__ import annotations

from qc.checks._common import metadata_files, sample
from qc.context import FolderSet, PolarityOutputs, VersionContext
from qc.jsonio import read_json_cached
from qc.models import CheckResult, Status
from qc.registry import register

CATEGORY = "Instance Value Offset Accuracy"
ITEM_REF = "addl"


def _check_one(fs: FolderSet, polarity_name: str, polarity: PolarityOutputs,
                options: dict) -> list[CheckResult]:
    scope = f"{fs.name} / {polarity_name}"
    files = metadata_files(polarity)
    if not files:
        return []
    sampled = sample(files, options)

    exact, drifted, not_found, checked = 0, 0, 0, 0
    not_found_examples: list[str] = []
    for path in sampled:
        doc, err = read_json_cached(path)
        if err or not isinstance(doc, dict):
            continue
        filename = doc.get("filename")
        own_doc_id = doc.get("doc_id")
        if not isinstance(filename, str) or not filename:
            continue
        text_path = polarity.plain_text / f"{filename}.txt"
        if not text_path.exists():
            continue
        text = None
        for inst in doc.get("instances") or []:
            if not isinstance(inst, dict) or inst.get("doc_id") != own_doc_id:
                continue
            value = inst.get("value")
            start, end = inst.get("value_start_offset"), inst.get("value_end_offset")
            if value is None or start is None or end is None:
                continue
            try:
                start, end = int(start), int(end)
            except (TypeError, ValueError):
                continue
            if text is None:
                text = text_path.read_text(encoding="utf-8")
            checked += 1
            if 0 <= start <= end <= len(text) and text[start:end] == str(value):
                exact += 1
            elif str(value) in text:
                drifted += 1
            else:
                not_found += 1
                if len(not_found_examples) < 5:
                    not_found_examples.append(f"{path.name}: value {value!r} not found anywhere")

    if not checked:
        return []
    sample_note = f" (sampled {len(sampled)}/{len(files)} docs, {checked} instance(s) checked)"
    title = f"instances[].value is present in plain_text{sample_note}"
    if not_found:
        return [CheckResult(Status.FAIL, CATEGORY, ITEM_REF, title,
            f"{not_found}/{checked} instance(s) have a value that is NOT present anywhere in "
            f"the document's plain_text, e.g.: {'; '.join(not_found_examples)}", scope,
            "Investigate these document(s) - the planted value appears to be genuinely "
            "missing from the delivered text, not just at a drifted offset.")]
    drift_note = (
        f"{exact}/{checked} instance(s) have value_start_offset/value_end_offset that exactly "
        f"select the value in plain_text; the remaining {drifted} have the value present "
        "elsewhere in the text at a different position (offsets likely computed against an "
        "earlier text representation than the final parsed plain_text - not a content defect, "
        "every value is still confirmed present)."
    )
    return [CheckResult(Status.PASS, CATEGORY, ITEM_REF, title, drift_note, scope)]


@register(category=CATEGORY)
def check_instance_offset_accuracy(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        for polarity_name, polarity in (("Positive", fs.positive), ("Negative", fs.negative)):
            results.extend(_check_one(fs, polarity_name, polarity, options))
    return results
