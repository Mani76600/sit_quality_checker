import json

from qc.checks.document_composition_checks import (
    check_business_context_balance,
    check_effective_hard_diversity,
    check_format_distribution,
    check_label_distribution,
)
from qc.context import VersionContext
from qc.models import Status


def _make_ctx(tmp_path, version_name="Version_20260101_0000"):
    version_dir = tmp_path / version_name
    return VersionContext(version_dir=version_dir, sit_name="Test SIT", language=None, layout="english")


def _write_combined_metadata(ctx, records, subdir=""):
    root = ctx.version_dir / subdir if subdir else ctx.version_dir
    root.mkdir(parents=True, exist_ok=True)
    with (root / "combined_metadata.jsonl").open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _write_export_summary(ctx, total, subdir=""):
    root = ctx.version_dir / subdir if subdir else ctx.version_dir
    root.mkdir(parents=True, exist_ok=True)
    (root / "export_summary.json").write_text(
        json.dumps({"counts": {"total": total}}), encoding="utf-8"
    )


def _record(doc_id, *, sit_category, file_format, value, polarity, **dims):
    field_name = "sit_values" if polarity == "positive" else "lookalike_values"
    base = {
        "doc_id": doc_id,
        "sit_category": sit_category,
        "file_format": file_format,
        "ground_truth": polarity,
        field_name: {"us_ssn": [value]},
    }
    base.update(dims)
    return base


_DIMS = dict(
    domain="Healthcare", department="Claims", function="Intake",
    workflow="review", process="verification", persona="Analyst",
    role="reviewer", document_type="memo",
)


def test_label_distribution_passes_and_reports_all_four_buckets(tmp_path):
    ctx = _make_ctx(tmp_path)
    records = [
        _record("doc_1", sit_category="easy positive", file_format="pdf", value="111", polarity="positive", **_DIMS),
        _record("doc_2", sit_category="hard positive", file_format="pdf", value="222", polarity="positive", **_DIMS),
        _record("doc_3", sit_category="easy negative", file_format="docx", value="333", polarity="negative", **_DIMS),
        _record("doc_4", sit_category="hard negative", file_format="docx", value="444", polarity="negative", **_DIMS),
    ]
    _write_combined_metadata(ctx, records)

    results = check_label_distribution(ctx, {})

    passes = [r for r in results if r.status == Status.PASS]
    assert len(passes) == 1  # only Agreements has data; Disagreements folder is absent
    assert "easy positive=1" in passes[0].detail
    assert "hard positive=1" in passes[0].detail
    assert "easy negative=1" in passes[0].detail
    assert "hard negative=1" in passes[0].detail
    assert "total=4" in passes[0].detail


def test_label_distribution_flags_unresolvable_sit_category(tmp_path):
    ctx = _make_ctx(tmp_path)
    records = [
        _record("doc_1", sit_category="easy positive", file_format="pdf", value="111", polarity="positive", **_DIMS),
        {**_record("doc_2", sit_category="", file_format="pdf", value="222", polarity="positive", **_DIMS), "sit_category": ""},
    ]
    _write_combined_metadata(ctx, records)

    results = check_label_distribution(ctx, {})

    warns = [r for r in results if r.status == Status.WARN]
    assert len(warns) == 1
    assert "1 of 2" in warns[0].detail


def test_format_distribution_passes_when_it_matches_export_summary_total(tmp_path):
    ctx = _make_ctx(tmp_path)
    records = [
        _record("doc_1", sit_category="easy positive", file_format="pdf", value="111", polarity="positive", **_DIMS),
        _record("doc_2", sit_category="easy positive", file_format="pdf", value="222", polarity="positive", **_DIMS),
        _record("doc_3", sit_category="easy negative", file_format="docx", value="333", polarity="negative", **_DIMS),
    ]
    _write_combined_metadata(ctx, records)
    _write_export_summary(ctx, total=3)

    results = check_format_distribution(ctx, {})

    passes = [r for r in results if r.status == Status.PASS]
    assert len(passes) == 1
    assert "pdf=2" in passes[0].detail
    assert "docx=1" in passes[0].detail
    assert "total=3, export_summary total=3" in passes[0].detail


def test_format_distribution_fails_when_it_drifts_from_export_summary_total(tmp_path):
    ctx = _make_ctx(tmp_path)
    records = [
        _record("doc_1", sit_category="easy positive", file_format="pdf", value="111", polarity="positive", **_DIMS),
    ]
    _write_combined_metadata(ctx, records)
    _write_export_summary(ctx, total=5)  # deliberately wrong

    results = check_format_distribution(ctx, {})

    fails = [r for r in results if r.status == Status.FAIL]
    assert len(fails) == 1
    assert "total=1, export_summary total=5" in fails[0].detail


def test_format_distribution_is_informational_without_export_summary(tmp_path):
    ctx = _make_ctx(tmp_path)
    records = [
        _record("doc_1", sit_category="easy positive", file_format="pdf", value="111", polarity="positive", **_DIMS),
    ]
    _write_combined_metadata(ctx, records)

    results = check_format_distribution(ctx, {})

    assert len(results) == 1
    assert results[0].status == Status.INFO


def test_business_context_balance_reports_every_dimension(tmp_path):
    ctx = _make_ctx(tmp_path)
    records = [
        _record("doc_1", sit_category="easy positive", file_format="pdf", value="111", polarity="positive", **_DIMS),
        _record("doc_2", sit_category="easy positive", file_format="pdf", value="222", polarity="positive",
                **{**_DIMS, "domain": "Finance"}),
    ]
    _write_combined_metadata(ctx, records)

    results = check_business_context_balance(ctx, {})

    dimensions_seen = {r.title.split(" distribution")[0] for r in results}
    assert dimensions_seen == {
        "domain", "department", "function", "workflow", "process", "persona", "role", "document_type",
    }
    domain_result = next(r for r in results if r.title.startswith("domain"))
    assert domain_result.status == Status.INFO
    assert "Healthcare=1" in domain_result.detail
    assert "Finance=1" in domain_result.detail


def test_business_context_balance_warns_on_dominant_value(tmp_path):
    ctx = _make_ctx(tmp_path)
    records = [
        _record(f"doc_{i}", sit_category="easy positive", file_format="pdf", value=str(i), polarity="positive", **_DIMS)
        for i in range(10)
    ]
    _write_combined_metadata(ctx, records)

    results = check_business_context_balance(ctx, {})

    domain_result = next(r for r in results if r.title.startswith("domain"))
    assert domain_result.status == Status.WARN
    assert "100%" in domain_result.fix


def test_effective_hard_diversity_passes_when_no_values_are_reused(tmp_path):
    ctx = _make_ctx(tmp_path)
    records = [
        _record("doc_1", sit_category="easy positive", file_format="pdf", value="111-11-1111", polarity="positive", **_DIMS),
        _record("doc_2", sit_category="hard positive", file_format="pdf", value="222-22-2222", polarity="positive", **_DIMS),
        _record("doc_3", sit_category="easy negative", file_format="pdf", value="999-99-9999", polarity="negative", **_DIMS),
    ]
    _write_combined_metadata(ctx, records)

    results = check_effective_hard_diversity(ctx, {})

    assert all(r.status == Status.PASS for r in results)
    positive_result = next(r for r in results if "positive" in r.title)
    assert "effective_hard_diversity=1.000" in positive_result.detail


def test_effective_hard_diversity_warns_on_reused_value(tmp_path):
    ctx = _make_ctx(tmp_path)
    records = [
        # Same value planted twice (formatting differs, but normalizes the same).
        _record("doc_1", sit_category="easy positive", file_format="pdf", value="111-22-3333", polarity="positive", **_DIMS),
        _record("doc_2", sit_category="hard positive", file_format="pdf", value="111223333", polarity="positive", **_DIMS),
        _record("doc_3", sit_category="hard positive", file_format="pdf", value="444-55-6666", polarity="positive", **_DIMS),
    ]
    _write_combined_metadata(ctx, records)

    results = check_effective_hard_diversity(ctx, {})

    positive_result = next(r for r in results if "positive" in r.title)
    assert positive_result.status == Status.WARN
    assert "doc_1 & doc_2" in positive_result.fix
    assert "effective_hard_diversity=0.667" in positive_result.detail


def test_all_checks_return_nothing_when_combined_metadata_is_absent(tmp_path):
    ctx = _make_ctx(tmp_path)
    assert check_label_distribution(ctx, {}) == []
    assert check_format_distribution(ctx, {}) == []
    assert check_business_context_balance(ctx, {}) == []
    assert check_effective_hard_diversity(ctx, {}) == []
