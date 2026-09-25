import json

from qc.checks.instance_field_checks import check_instance_fields
from qc.context import VersionContext
from qc.models import Status


def _write_metadata(polarity_dir, stem, instances):
    meta_dir = polarity_dir / "outputs" / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / f"{stem}.json").write_text(
        json.dumps({"doc_id": stem, "instances": instances}), encoding="utf-8")


def _base_instance(**overrides):
    inst = {
        "actual_engine_confidence": 0.9, "actual_engine_result": "match",
        "allocation_key": "k1", "corpus_role": "detector_triggering_semantic_ground_truth",
        "difficulty": "easy", "doc_id": "d1", "expected_confidence": 0.9,
        "expected_engine_outcome": "match", "extraction_found": True,
        "file_name": "f1.docx", "normalized_value": "123", "pair_id": "p1",
        "pattern_branch": "b1", "proximity_bucket": "near", "sample_uuid": "u1",
        "sit_key": "k", "source_inventory_id": "s1", "split": "unsplit",
        "validation_status": "passed", "value": "123", "value_end_offset": 10,
        "value_kind": "digits", "keyword_present": True, "keyword": "passport",
        "keyword_distance": 5,
    }
    inst.update(overrides)
    return inst


def _ctx(tmp_path) -> VersionContext:
    return VersionContext(version_dir=tmp_path, sit_name="Test SIT", language=None, layout="english")


def test_all_required_fields_present_passes(tmp_path):
    ctx = _ctx(tmp_path)
    _write_metadata(ctx.agreements.positive.root, "doc1", [_base_instance()])
    results = check_instance_fields(ctx, {})
    required_result = next(r for r in results if "Every required instances" in r.title
                           and r.scope == "Agreements / Positive")
    assert required_result.status == Status.PASS


def test_missing_required_field_fails(tmp_path):
    ctx = _ctx(tmp_path)
    _write_metadata(ctx.agreements.positive.root, "doc1", [_base_instance(validation_status="")])
    results = check_instance_fields(ctx, {})
    required_result = next(r for r in results if "Every required instances" in r.title
                           and r.scope == "Agreements / Positive")
    assert required_result.status == Status.FAIL
    assert "validation_status" in required_result.detail


def test_schema_aware_field_entirely_absent_is_info(tmp_path):
    ctx = _ctx(tmp_path)
    _write_metadata(ctx.agreements.positive.root, "doc1", [_base_instance(chunk_label=None)])
    _write_metadata(ctx.agreements.positive.root, "doc2", [_base_instance(chunk_label="")])
    results = check_instance_fields(ctx, {})
    chunk_label_result = next(r for r in results if "chunk_label" in r.title
                              and r.scope == "Agreements / Positive")
    assert chunk_label_result.status == Status.INFO


def test_schema_aware_field_partially_empty_fails(tmp_path):
    ctx = _ctx(tmp_path)
    _write_metadata(ctx.agreements.negative.root, "doc1", [_base_instance(chunk_label="Negative")])
    _write_metadata(ctx.agreements.negative.root, "doc2", [_base_instance(chunk_label=None)])
    results = check_instance_fields(ctx, {})
    chunk_label_result = next(r for r in results if "chunk_label" in r.title
                              and r.scope == "Agreements / Negative")
    assert chunk_label_result.status == Status.FAIL
    assert "doc2" in chunk_label_result.detail


def test_schema_aware_field_fully_populated_passes(tmp_path):
    ctx = _ctx(tmp_path)
    _write_metadata(ctx.agreements.negative.root, "doc1", [_base_instance(chunk_label="Negative")])
    _write_metadata(ctx.agreements.negative.root, "doc2", [_base_instance(chunk_label="Negative")])
    results = check_instance_fields(ctx, {})
    chunk_label_result = next(r for r in results if "chunk_label" in r.title
                              and r.scope == "Agreements / Negative")
    assert chunk_label_result.status == Status.PASS


def test_positive_and_negative_pools_never_mixed(tmp_path):
    # chunk_label used in Negative but never in Positive - Positive pool must
    # report INFO (not used here), not a FAIL contaminated by Negative's usage.
    ctx = _ctx(tmp_path)
    _write_metadata(ctx.agreements.positive.root, "doc1", [_base_instance(chunk_label=None)])
    _write_metadata(ctx.agreements.negative.root, "doc2", [_base_instance(chunk_label="Negative")])
    results = check_instance_fields(ctx, {})
    pos_result = next(r for r in results if "chunk_label" in r.title
                      and r.scope == "Agreements / Positive")
    neg_result = next(r for r in results if "chunk_label" in r.title
                      and r.scope == "Agreements / Negative")
    assert pos_result.status == Status.INFO
    assert neg_result.status == Status.PASS


def test_keyword_consistency_pass(tmp_path):
    ctx = _ctx(tmp_path)
    _write_metadata(ctx.agreements.positive.root, "doc1", [
        _base_instance(keyword_present=True, keyword="x", keyword_distance=3),
        _base_instance(keyword_present=False, keyword="", keyword_distance=""),
    ])
    results = check_instance_fields(ctx, {})
    kw_result = next(r for r in results if "keyword_present" in r.title
                     and r.scope == "Agreements / Positive")
    assert kw_result.status == Status.PASS


def test_keyword_consistency_fails_when_present_but_empty(tmp_path):
    ctx = _ctx(tmp_path)
    _write_metadata(ctx.agreements.positive.root, "doc1", [
        _base_instance(keyword_present=True, keyword="", keyword_distance=""),
    ])
    results = check_instance_fields(ctx, {})
    kw_result = next(r for r in results if "keyword_present" in r.title
                     and r.scope == "Agreements / Positive")
    assert kw_result.status == Status.FAIL


def test_keyword_consistency_fails_when_absent_but_populated(tmp_path):
    ctx = _ctx(tmp_path)
    _write_metadata(ctx.agreements.positive.root, "doc1", [
        _base_instance(keyword_present=False, keyword="x", keyword_distance=5),
    ])
    results = check_instance_fields(ctx, {})
    kw_result = next(r for r in results if "keyword_present" in r.title
                     and r.scope == "Agreements / Positive")
    assert kw_result.status == Status.FAIL


def test_no_metadata_files_returns_no_results(tmp_path):
    ctx = _ctx(tmp_path)
    results = check_instance_fields(ctx, {})
    assert results == []
