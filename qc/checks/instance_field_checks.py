"""Addition: field completeness for per-document metadata's instances[]
array - the richest, most diagnostic part of the schema (engine-match
verification, keyword proximity, validation status), previously almost
entirely unaudited (only expected_engine_match/actual_engine_match/
semantic_ground_truth/keyword_present/sit_extensions were checked
elsewhere, out of 33 real fields).

Verified against 5 real samples (South Africa, Sweden, Taiwan, France,
South Korea - ~65,000 instances total) before writing this, scoped per
polarity (Positive/Negative checked separately, never pooled) since some
fields are legitimately polarity-specific:

- REQUIRED_INSTANCE_FIELDS were 100% populated in every sample, every
  polarity, zero exceptions - safe to hard-require.
- SCHEMA_AWARE_INSTANCE_FIELDS are used inconsistently ACROSS pipeline
  versions (e.g. chunk_label: always empty in South Africa/Sweden/France/
  South Korea, always populated in Taiwan) but never PARTIALLY within a
  single (file, polarity) pool in any real sample - so a field entirely
  unused in a pool is a schema note (INFO), while a real partial gap
  (confirmed in a real France sample: entity_guid/engine_variant_
  expectations/sit_name empty on ~9.4% of instances even though clearly
  populated elsewhere in that same pool, with no correlating cause found)
  is a genuine FAIL.
- keyword / keyword_distance are deliberately excluded from both lists
  above - they have their own dedicated consistency check below, since
  they are correctly, intentionally empty exactly when keyword_present is
  False (a strict 1:1 correlation confirmed across all 65,000 real
  instances, zero exceptions - not "must never be empty").
- failure_reason was 100% empty in every real sample (apparently only
  populated on an actual generation failure, which no available sample
  exhibits) - left out of the hard-required list since there's no real
  example to verify a "must be non-empty" rule against, but still safely
  covered by the schema-aware sweep (it naturally reports as a schema
  note rather than a false FAIL, since "used somewhere" requires at least
  one real non-empty value in that polarity's pool).
"""

from __future__ import annotations

from qc.checks._common import iter_instances as _iter_instances
from qc.checks._common import metadata_files as _metadata_files
from qc.checks._common import sample as _sample
from qc.context import FolderSet, PolarityOutputs, VersionContext
from qc.jsonio import read_json_cached, to_bool
from qc.models import CheckResult, Status
from qc.registry import register

CATEGORY = "Metadata Instance Fields (addition)"

REQUIRED_INSTANCE_FIELDS = [
    "actual_engine_confidence", "actual_engine_result", "allocation_key",
    "corpus_role", "difficulty", "doc_id", "expected_confidence",
    "expected_engine_outcome", "extraction_found", "file_name",
    "normalized_value", "pair_id", "pattern_branch", "proximity_bucket",
    "sample_uuid", "sit_key", "source_inventory_id", "split",
    "validation_status", "value", "value_end_offset", "value_kind",
]

SCHEMA_AWARE_INSTANCE_FIELDS = [
    "chunk_label", "entity_guid", "engine_variant_expectations", "sit_name",
    "failure_reason",
]

MAX_EXAMPLES = 5


def _is_empty(v: object) -> bool:
    return v is None or v == "" or v == [] or v == {}


@register(category=CATEGORY)
def check_instance_fields(ctx: VersionContext, options: dict) -> list[CheckResult]:
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

    instances: list[tuple[str, dict]] = []
    for path in sampled:
        doc, err = read_json_cached(path)
        if err:
            continue
        for inst in _iter_instances(doc):
            instances.append((path.name, inst))

    if not instances:
        return []

    results: list[CheckResult] = []
    results.extend(_check_required_fields(instances, scope, sample_note))
    results.extend(_check_schema_aware_fields(instances, scope, sample_note))
    results.extend(_check_keyword_consistency(instances, scope, sample_note))
    return results


def _check_required_fields(instances: list[tuple[str, dict]], scope: str,
                            sample_note: str) -> list[CheckResult]:
    total = len(instances)
    empty_counts: dict[str, int] = {}
    empty_examples: dict[str, list[str]] = {}
    for doc_name, inst in instances:
        for field in REQUIRED_INSTANCE_FIELDS:
            if _is_empty(inst.get(field)):
                empty_counts[field] = empty_counts.get(field, 0) + 1
                ex = empty_examples.setdefault(field, [])
                if len(ex) < MAX_EXAMPLES:
                    ex.append(doc_name)

    if not empty_counts:
        return [CheckResult(Status.PASS, CATEGORY, "addl",
            f"Every required instances[] field is assigned on every instance{sample_note}",
            f"Checked {total} instance(s) across {len(REQUIRED_INSTANCE_FIELDS)} fields, "
            "none empty.", scope)]

    rows = [f"{field} ({n}/{total} empty, e.g. {empty_examples[field]})"
            for field, n in sorted(empty_counts.items(), key=lambda kv: -kv[1])]
    return [CheckResult(Status.FAIL, CATEGORY, "addl",
        f"Every required instances[] field is assigned on every instance{sample_note}",
        "; ".join(rows), scope,
        "Backfill or regenerate the listed instances[] fields - they must never be "
        "empty/null on any instance.")]


def _check_schema_aware_fields(instances: list[tuple[str, dict]], scope: str,
                                sample_note: str) -> list[CheckResult]:
    total = len(instances)
    results: list[CheckResult] = []
    for field in SCHEMA_AWARE_INSTANCE_FIELDS:
        used_somewhere = any(not _is_empty(inst.get(field)) for _doc_name, inst in instances)
        if not used_somewhere:
            results.append(CheckResult(Status.INFO, CATEGORY, "addl",
                f"instances[].{field} is part of this schema{sample_note}",
                f"'{field}' is empty/absent on all {total} instance(s) checked - likely a "
                "different pipeline/schema version for this SIT; not treated as a failure.",
                scope))
            continue

        bad = [doc_name for doc_name, inst in instances if _is_empty(inst.get(field))]
        if bad:
            results.append(CheckResult(Status.FAIL, CATEGORY, "addl",
                f"instances[].{field} is assigned on every instance{sample_note}",
                f"{len(bad)}/{total} instance(s) have an empty '{field}' even though it's "
                f"populated elsewhere in this same file, e.g. {bad[:MAX_EXAMPLES]}", scope,
                f"'{field}' is clearly used in this file's instances[] - backfill it on "
                "every instance, or confirm why it's legitimately missing on the ones "
                "listed."))
        else:
            results.append(CheckResult(Status.PASS, CATEGORY, "addl",
                f"instances[].{field} is assigned on every instance{sample_note}",
                f"Checked {total} instance(s), all have '{field}' populated.", scope))
    return results


def _check_keyword_consistency(instances: list[tuple[str, dict]], scope: str,
                                sample_note: str) -> list[CheckResult]:
    total = len(instances)
    bad: list[str] = []
    checked = 0
    for doc_name, inst in instances:
        present = to_bool(inst.get("keyword_present"))
        if present is None:
            continue
        checked += 1
        kw_empty = _is_empty(inst.get("keyword"))
        dist_empty = _is_empty(inst.get("keyword_distance"))
        if present and (kw_empty or dist_empty):
            if len(bad) < MAX_EXAMPLES:
                bad.append(f"{doc_name}: keyword_present=True but keyword/keyword_distance empty")
        elif not present and not (kw_empty and dist_empty):
            if len(bad) < MAX_EXAMPLES:
                bad.append(f"{doc_name}: keyword_present=False but keyword/keyword_distance non-empty")

    if checked == 0:
        return []
    if bad:
        return [CheckResult(Status.FAIL, CATEGORY, "addl",
            f"instances[].keyword / keyword_distance match keyword_present{sample_note}",
            f"Inconsistent on some instance(s), e.g. {bad}", scope,
            "keyword/keyword_distance should be populated exactly when keyword_present is "
            "True, and both empty when it's False - confirmed as a strict 1:1 rule across "
            "real sample data (65,000+ instances, zero exceptions).")]
    return [CheckResult(Status.PASS, CATEGORY, "addl",
        f"instances[].keyword / keyword_distance match keyword_present{sample_note}",
        f"Checked {checked} instance(s) with a keyword_present value, all consistent.",
        scope)]
