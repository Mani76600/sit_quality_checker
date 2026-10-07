import json

from qc.checks.metadata_schema_checks import check_metadata_self_consistency
from qc.context import VersionContext
from qc.models import Status


def _make_ctx(tmp_path, version_name="Version_20260101_0000"):
    return VersionContext(version_dir=tmp_path / version_name, sit_name="Test SIT",
                           language=None, layout="english")


def _write_metadata(ctx, polarity, stem, *, file_format, file_ext):
    metadata_dir = ctx.version_dir / polarity.capitalize() / "outputs" / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "doc_id": stem, "stem": stem, "filename": f"{stem}{file_ext}",
        "file_ext": file_ext, "file_format": file_format,
    }
    (metadata_dir / f"{stem}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_fixed_width_format_with_txt_extension_is_not_a_mismatch(tmp_path):
    """Real Argentina DNI output flagged file_format='fixed_width' /
    file_ext='.txt' as a FAIL - but fixed_width is a plain-text
    representation, so .txt is its genuinely correct extension, not a
    pipeline defect."""
    ctx = _make_ctx(tmp_path)
    _write_metadata(ctx, "positive", "doc1", file_format="fixed_width", file_ext=".txt")

    results = check_metadata_self_consistency(ctx, {})

    assert len(results) == 1
    assert results[0].status == Status.PASS


def test_genuinely_mismatched_format_and_extension_still_fails(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write_metadata(ctx, "positive", "doc1", file_format="pdf", file_ext=".docx")

    results = check_metadata_self_consistency(ctx, {})

    assert len(results) == 1
    assert results[0].status == Status.FAIL
    assert "file_format 'pdf' != file_ext '.docx'" in results[0].detail


def test_ordinary_matching_format_and_extension_passes(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write_metadata(ctx, "positive", "doc1", file_format="docx", file_ext=".docx")

    results = check_metadata_self_consistency(ctx, {})

    assert len(results) == 1
    assert results[0].status == Status.PASS
