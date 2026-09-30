import json

from qc.checks.generation_log_checks import check_generation_log
from qc.context import VersionContext
from qc.models import Status


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data), encoding="utf-8")


def _make_ctx(tmp_path, version_name="Version_20260101_0000"):
    version_dir = tmp_path / version_name
    return VersionContext(version_dir=version_dir, sit_name="Test SIT", language=None, layout="english")


_VALID_LOG = {
    "schema_version": "1.0",
    "dataset_version": "Version_20260101_0000",
    "generated_at": "2026-01-01T00:00:00.000000Z",
    "sit_name": "Test SIT",
    "generator": {"model": "gpt-4.1", "model_version": "2025-04-14"},
    "sit_grader": {"model": "gpt-4.1"},
    "mce": {"version": "15.20.9772.0"},
    "docparser": {"version": "16Sep"},
}


def _write_export_summary(ctx, sit_name="Test SIT", version=None):
    _write(ctx.version_dir / "export_summary.json", {
        "version": version or ctx.version_dir.name,
        "sit_name": sit_name,
    })


def test_missing_file_fails(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write_export_summary(ctx)
    results = check_generation_log(ctx, {})
    assert len(results) == 1
    assert results[0].status == Status.FAIL
    assert "generation_log.json" in results[0].detail


def test_valid_log_passes_everything(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write_export_summary(ctx)
    _write(ctx.version_dir / "generation_log.json", _VALID_LOG)

    results = check_generation_log(ctx, {})
    fails = [r for r in results if r.status == Status.FAIL]
    assert not fails, f"unexpected failures: {fails}"
    assert any("dataset_version matches" in r.title for r in results if r.status == Status.PASS)
    assert any("sit_name matches" in r.title for r in results if r.status == Status.PASS)
    # the 5 no-ground-truth fields should be reported PASS (with the caveat
    # spelled out in the detail text), not silently skipped
    pass_titles = [
        r.title for r in results
        if r.status == Status.PASS and "no reference value to cross-check" in r.title
    ]
    assert any("mce" in t and "version" in t for t in pass_titles)
    assert any("docparser" in t and "version" in t for t in pass_titles)


def test_extra_top_level_field_fails(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write_export_summary(ctx)
    bad = dict(_VALID_LOG)
    bad["unexpected_field"] = "surprise"
    _write(ctx.version_dir / "generation_log.json", bad)

    results = check_generation_log(ctx, {})
    extra_field_results = [r for r in results if "no unexpected fields" in r.title and r.category.startswith("Generation Log") and "[" not in r.title]
    assert extra_field_results
    assert extra_field_results[0].status == Status.FAIL
    assert "unexpected_field" in extra_field_results[0].detail


def test_extra_nested_field_fails(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write_export_summary(ctx)
    bad = json.loads(json.dumps(_VALID_LOG))  # deep copy
    bad["generator"]["extra_nested"] = "oops"
    _write(ctx.version_dir / "generation_log.json", bad)

    results = check_generation_log(ctx, {})
    nested_fail = [r for r in results if "'generator'" in r.title and "no unexpected fields" in r.title]
    assert nested_fail
    assert nested_fail[0].status == Status.FAIL
    assert "extra_nested" in nested_fail[0].detail


def test_missing_required_field_fails(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write_export_summary(ctx)
    bad = dict(_VALID_LOG)
    del bad["schema_version"]
    _write(ctx.version_dir / "generation_log.json", bad)

    results = check_generation_log(ctx, {})
    missing_result = [r for r in results if "every required field" in r.title and "[" not in r.title]
    assert missing_result
    assert missing_result[0].status == Status.FAIL
    assert "schema_version" in missing_result[0].detail


def test_dataset_version_mismatch_fails(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write_export_summary(ctx, version="Version_20260101_0000")
    bad = dict(_VALID_LOG)
    bad["dataset_version"] = "Version_20250101_9999"
    _write(ctx.version_dir / "generation_log.json", bad)

    results = check_generation_log(ctx, {})
    mismatch = [r for r in results if "dataset_version matches" in r.title]
    assert mismatch
    assert mismatch[0].status == Status.FAIL


def test_sit_name_mismatch_fails(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write_export_summary(ctx, sit_name="Real SIT Name")
    bad = dict(_VALID_LOG)
    bad["sit_name"] = "Wrong SIT Name"
    _write(ctx.version_dir / "generation_log.json", bad)

    results = check_generation_log(ctx, {})
    mismatch = [r for r in results if "sit_name matches" in r.title]
    assert mismatch
    assert mismatch[0].status == Status.FAIL


def test_malformed_json_fails(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write_export_summary(ctx)
    _write(ctx.version_dir / "generation_log.json", "{not valid json")

    results = check_generation_log(ctx, {})
    assert len(results) == 1
    assert results[0].status == Status.FAIL


def test_empty_string_no_reference_field_fails(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write_export_summary(ctx)
    bad = json.loads(json.dumps(_VALID_LOG))
    bad["mce"]["version"] = ""
    _write(ctx.version_dir / "generation_log.json", bad)

    results = check_generation_log(ctx, {})
    mce_results = [r for r in results if "mce" in r.title and "version" in r.title and "non-empty string" in r.title]
    assert mce_results
    assert mce_results[0].status == Status.FAIL
