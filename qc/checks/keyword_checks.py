"""Item 10: optional/mandatory keyword handling + semantic_ground_truth.

- Positive instances: semantic_ground_truth == True; if
  sit_extensions.keyword_requirement == "mandatory", keyword_present must
  also be True.
- Negative instances: semantic_ground_truth == False. The mandatory-keyword
  constraint is not enforced symmetrically for negatives (a mandatory
  keyword can legitimately be absent from a true negative), so it is only
  reported informationally there.
"""

from __future__ import annotations

from qc.context import FolderSet, PolarityOutputs, VersionContext
from qc.jsonio import read_json_cached, to_bool
from qc.registry import register
from qc.models import CheckResult, Status
from qc.checks._common import metadata_files as _metadata_files
from qc.checks._common import iter_instances as _iter_instances
from qc.checks._common import sample as _sample

CATEGORY = "10. Keyword Requirement / semantic_ground_truth"


@register(category=CATEGORY)
def check_keyword_requirements(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        results.extend(_check_one(fs, "Positive", fs.positive, options))
        results.extend(_check_one(fs, "Negative", fs.negative, options))
    return results


def _check_one(fs: FolderSet, polarity_name: str, polarity: PolarityOutputs,
                options: dict) -> list[CheckResult]:
    scope = f"{fs.name} / {polarity_name}"
    files = _metadata_files(polarity)
    if not files:
        return []
    sampled = _sample(files, options)
    sample_note = f" (sampled {len(sampled)}/{len(files)} docs)" if len(sampled) < len(files) else ""

    expected_sgt = polarity_name == "Positive"
    sgt_mismatches: list[str] = []
    mandatory_keyword_missing: list[str] = []
    total_instances = 0

    for path in sampled:
        doc, err = read_json_cached(path)
        if err:
            continue
        for inst in _iter_instances(doc):
            total_instances += 1
            sgt = to_bool(inst.get("semantic_ground_truth"))
            if sgt is not None and sgt != expected_sgt and len(sgt_mismatches) < 15:
                sgt_mismatches.append(path.name)

            ext = inst.get("sit_extensions") or {}
            requirement = str(ext.get("keyword_requirement", "")).strip().lower()
            keyword_present = to_bool(inst.get("keyword_present"))
            if polarity_name == "Positive" and requirement == "mandatory" and keyword_present is False:
                if len(mandatory_keyword_missing) < 15:
                    mandatory_keyword_missing.append(path.name)

    if total_instances == 0:
        return []

    results: list[CheckResult] = []
    if sgt_mismatches:
        results.append(CheckResult(Status.FAIL, CATEGORY, "10",
            f"semantic_ground_truth == {expected_sgt} for {polarity_name} instances{sample_note}",
            f"{len(sgt_mismatches)} doc(s) with unexpected semantic_ground_truth, e.g. "
            f"{sgt_mismatches[:5]}", scope,
            f"Every {polarity_name} instance must have semantic_ground_truth == {expected_sgt}."))
    else:
        results.append(CheckResult(Status.PASS, CATEGORY, "10",
            f"semantic_ground_truth == {expected_sgt} for {polarity_name} instances{sample_note}",
            f"Checked {total_instances} instance(s) across {len(sampled)} doc(s).", scope))

    if polarity_name == "Positive":
        if mandatory_keyword_missing:
            results.append(CheckResult(Status.FAIL, CATEGORY, "10",
                f"mandatory keyword present for Positive instances{sample_note}",
                f"{len(mandatory_keyword_missing)} doc(s) with keyword_requirement=mandatory "
                f"but keyword_present=False, e.g. {mandatory_keyword_missing[:5]}", scope,
                "A mandatory keyword must be present in every Positive instance that "
                "requires it - investigate placement/generation for the listed docs."))
        else:
            results.append(CheckResult(Status.PASS, CATEGORY, "10",
                f"mandatory keyword present for Positive instances{sample_note}",
                "No mandatory-keyword-but-absent instances found.", scope))

    return results
