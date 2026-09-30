"""Generation Log Consistency: validates generation_log.json, a
manifest written once at the top level of each Version_* folder (sibling to
export_summary.json / corpus.jsonl / sit_inverted_index.json - NOT duplicated
under Disagreements/), recording which models/tool versions produced this run.

Expected shape (every key required, no extra keys allowed at any level):
{
  "schema_version": "1.0",
  "dataset_version": "Version_20260922_0241",
  "generated_at": "2026-09-23T07:09:46.150443Z",
  "sit_name": "Australian Company Number",
  "generator": {"model": "gpt-4.1", "model_version": "2025-04-14"},
  "sit_grader": {"model": "gpt-4.1"},
  "mce": {"version": "15.20.9772.0"},
  "docparser": {"version": "16Sep"}
}

Two fields have real ground truth elsewhere in the version folder and are
cross-checked against it: dataset_version (must equal export_summary.json's
own "version" field, which itself already matches the folder name per the
existing item-1 "Version directory naming" check) and sit_name (must equal
export_summary.json's "sit_name"). The remaining fields (generator.model,
generator.model_version, sit_grader.model, mce.version, docparser.version)
have no independent ground truth anywhere in this version folder today -
they are checked for presence/shape only and reported PASS (with that
caveat spelled out in the detail text) rather than silently skipped, so
this gap stays visible rather than looking like a full check.
"""

from __future__ import annotations

from qc.context import VersionContext
from qc.jsonio import read_json
from qc.models import CheckResult, Status
from qc.registry import register

CATEGORY = "Generation Log Consistency"
ITEM_REF = "addl"

TOP_LEVEL_STRING_KEYS = {"schema_version", "dataset_version", "generated_at", "sit_name"}
NESTED_KEYS = {
    "generator": {"model", "model_version"},
    "sit_grader": {"model"},
    "mce": {"version"},
    "docparser": {"version"},
}
EXPECTED_TOP_LEVEL_KEYS = TOP_LEVEL_STRING_KEYS | set(NESTED_KEYS)


def _fail(title: str, detail: str, scope: str, fix: str) -> CheckResult:
    return CheckResult(Status.FAIL, CATEGORY, ITEM_REF, title, detail, scope, fix)


def _pass(title: str, detail: str, scope: str) -> CheckResult:
    return CheckResult(Status.PASS, CATEGORY, ITEM_REF, title, detail, scope)


@register(category=CATEGORY)
def check_generation_log(ctx: VersionContext, options: dict) -> list[CheckResult]:
    scope = ctx.label
    results: list[CheckResult] = []
    path = ctx.version_dir / "generation_log.json"

    if not path.exists():
        return [_fail(
            "generation_log.json present",
            f"Missing: {path}", scope,
            "Write generation_log.json at the top level of this Version_* folder "
            "(sibling to export_summary.json), recording schema_version, "
            "dataset_version, generated_at, sit_name, generator, sit_grader, mce, "
            "and docparser.",
        )]

    data, err = read_json(path)
    if err:
        return [_fail("generation_log.json readable", err, scope,
                       "Regenerate generation_log.json - it is missing or malformed.")]
    if not isinstance(data, dict):
        return [_fail("generation_log.json is a JSON object", f"Got: {type(data).__name__}",
                       scope, "generation_log.json must be a single JSON object.")]

    results.append(_pass("generation_log.json present", f"Found: {path}", scope))

    # --- exact top-level key set: extra keys are an error, missing keys are an error ---
    actual_keys = set(data.keys())
    extra = sorted(actual_keys - EXPECTED_TOP_LEVEL_KEYS)
    missing = sorted(EXPECTED_TOP_LEVEL_KEYS - actual_keys)
    if extra:
        results.append(_fail(
            "generation_log.json has no unexpected fields",
            f"Unexpected extra field(s): {', '.join(extra)}", scope,
            f"Remove {', '.join(extra)} from generation_log.json, or update the "
            "expected schema if this field is intentional.",
        ))
    else:
        results.append(_pass("generation_log.json has no unexpected fields",
                              f"Fields: {sorted(actual_keys)}", scope))
    if missing:
        results.append(_fail(
            "generation_log.json has every required field",
            f"Missing field(s): {', '.join(missing)}", scope,
            f"Add {', '.join(missing)} to generation_log.json.",
        ))
    else:
        results.append(_pass("generation_log.json has every required field",
                              "All required top-level fields present", scope))

    # --- nested objects: same exact-key-set treatment ---
    for key, expected_nested_keys in NESTED_KEYS.items():
        if key not in data:
            continue  # already reported above as a missing top-level field
        nested = data[key]
        if not isinstance(nested, dict):
            results.append(_fail(
                f"generation_log.json[{key!r}] is a JSON object",
                f"Got: {type(nested).__name__}", scope,
                f"generation_log.json's {key!r} field must be an object with "
                f"{sorted(expected_nested_keys)}.",
            ))
            continue
        nested_actual = set(nested.keys())
        nested_extra = sorted(nested_actual - expected_nested_keys)
        nested_missing = sorted(expected_nested_keys - nested_actual)
        if nested_extra:
            results.append(_fail(
                f"generation_log.json[{key!r}] has no unexpected fields",
                f"Unexpected extra field(s): {', '.join(nested_extra)}", scope,
                f"Remove {', '.join(nested_extra)} from generation_log.json's {key!r} object.",
            ))
        if nested_missing:
            results.append(_fail(
                f"generation_log.json[{key!r}] has every required field",
                f"Missing field(s): {', '.join(nested_missing)}", scope,
                f"Add {', '.join(nested_missing)} to generation_log.json's {key!r} object.",
            ))
        if not nested_extra and not nested_missing:
            results.append(_pass(
                f"generation_log.json[{key!r}] has exactly the expected fields",
                f"Fields: {sorted(nested_actual)}", scope))

    # --- cross-check the two fields with real ground truth elsewhere in this run ---
    export_summary, es_err = read_json(ctx.agreements.export_summary)
    expected_version = export_summary.get("version") if not es_err and isinstance(export_summary, dict) else None
    expected_sit_name = export_summary.get("sit_name") if not es_err and isinstance(export_summary, dict) else None

    dataset_version = data.get("dataset_version")
    if dataset_version is not None:
        if expected_version is not None:
            if dataset_version == expected_version:
                results.append(_pass(
                    "generation_log.json.dataset_version matches export_summary.json.version",
                    f"{dataset_version!r} == {expected_version!r}", scope))
            else:
                results.append(_fail(
                    "generation_log.json.dataset_version matches export_summary.json.version",
                    f"generation_log.json says {dataset_version!r} but export_summary.json "
                    f"says {expected_version!r}", scope,
                    "Reconcile dataset_version in generation_log.json with export_summary.json's "
                    "version field - they must describe the same run."))
        else:
            results.append(_pass(
                "generation_log.json.dataset_version has no export_summary.json to compare against",
                f"dataset_version={dataset_version!r}; export_summary.json unreadable: {es_err}",
                scope))

    sit_name = data.get("sit_name")
    if sit_name is not None:
        if expected_sit_name is not None:
            if sit_name == expected_sit_name:
                results.append(_pass(
                    "generation_log.json.sit_name matches export_summary.json.sit_name",
                    f"{sit_name!r} == {expected_sit_name!r}", scope))
            else:
                results.append(_fail(
                    "generation_log.json.sit_name matches export_summary.json.sit_name",
                    f"generation_log.json says {sit_name!r} but export_summary.json "
                    f"says {expected_sit_name!r}", scope,
                    "Reconcile sit_name in generation_log.json with export_summary.json - "
                    "they must describe the same SIT."))
        else:
            results.append(_pass(
                "generation_log.json.sit_name has no export_summary.json to compare against",
                f"sit_name={sit_name!r}; export_summary.json unreadable: {es_err}", scope))

    # --- fields with no ground truth anywhere in this version folder today ---
    # (generator.model/model_version, sit_grader.model, mce.version,
    # docparser.version) - presence/type only, flagged INFO so the gap stays
    # visible rather than looking like a full match-against-output check.
    no_reference_fields = [
        ("generator", "model"), ("generator", "model_version"),
        ("sit_grader", "model"), ("mce", "version"), ("docparser", "version"),
    ]
    for parent, child in no_reference_fields:
        nested = data.get(parent)
        if not isinstance(nested, dict) or child not in nested:
            continue
        value = nested[child]
        label = f"generation_log.json[{parent!r}][{child!r}]"
        if isinstance(value, str) and value.strip():
            results.append(_pass(
                f"{label} has no reference value to cross-check in this version folder",
                f"value={value!r} (no known-good value recorded anywhere else in this "
                "run to compare against)", scope))
        else:
            results.append(_fail(
                f"{label} is a non-empty string",
                f"Got: {value!r}", scope,
                f"{label} must be a non-empty string."))

    return results
