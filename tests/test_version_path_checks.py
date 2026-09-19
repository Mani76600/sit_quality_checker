import json

from qc.checks.version_path_checks import check_version_paths
from qc.context import VersionContext
from qc.models import Status


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_ctx(tmp_path, version_name="Version_20260101_0000"):
    version_dir = tmp_path / version_name
    return VersionContext(version_dir=version_dir, sit_name="Test SIT", language=None, layout="english")


def test_consistent_version_path_passes(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write(ctx.version_dir / "export_summary.json", {
        "output_dir": f"output\\Test SIT\\{ctx.version_dir.name}"})
    _write(ctx.version_dir / "Positive" / "outputs" / "metadata" / "doc1.json", {
        "raw_doc": f"output\\Test SIT\\{ctx.version_dir.name}\\Positive\\outputs\\raw_doc\\doc1.pdf",
        "docparser": {
            "raw_output": f"output\\Test SIT\\{ctx.version_dir.name}\\Positive\\outputs\\parsed_raw_doc\\doc1.json",
            "parsed": f"output\\Test SIT\\{ctx.version_dir.name}\\Positive\\outputs\\parsed_raw_doc\\plain_text\\doc1.pdf.txt",
            "chunks": f"output\\Test SIT\\{ctx.version_dir.name}\\Positive\\outputs\\parsed_raw_doc\\chunks\\doc1.pdf.json",
        },
    })

    results = check_version_paths(ctx, {"exhaustive_chunk_scan": True, "chunk_sample_size": 200})
    assert results
    assert all(r.status == Status.PASS for r in results if r.category.startswith("Version Path"))


def test_stale_version_path_in_metadata_fails(tmp_path):
    ctx = _make_ctx(tmp_path, version_name="Version_20260101_0000")
    old_version = "Version_20250101_0000"
    _write(ctx.version_dir / "export_summary.json", {
        "output_dir": f"output\\Test SIT\\{ctx.version_dir.name}"})
    _write(ctx.version_dir / "Positive" / "outputs" / "metadata" / "doc1.json", {
        "raw_doc": f"output\\Test SIT\\{old_version}\\Positive\\outputs\\raw_doc\\doc1.pdf",
    })

    results = check_version_paths(ctx, {"exhaustive_chunk_scan": True, "chunk_sample_size": 200})
    raw_doc_results = [r for r in results if "raw_doc" in r.title]
    assert raw_doc_results
    assert raw_doc_results[0].status == Status.FAIL
    assert old_version in raw_doc_results[0].detail


def test_stale_export_summary_output_dir_fails(tmp_path):
    ctx = _make_ctx(tmp_path, version_name="Version_20260101_0000")
    old_version = "Version_20250101_0000"
    _write(ctx.version_dir / "export_summary.json", {
        "output_dir": f"output\\Test SIT\\{old_version}"})

    results = check_version_paths(ctx, {"exhaustive_chunk_scan": True, "chunk_sample_size": 200})
    summary_results = [r for r in results if "output_dir" in r.title]
    assert summary_results
    assert summary_results[0].status == Status.FAIL
