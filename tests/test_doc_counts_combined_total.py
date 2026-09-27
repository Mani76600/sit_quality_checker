import json

from qc.context import VersionContext
from qc.registry import _read_doc_counts


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_ctx(tmp_path):
    version_dir = tmp_path / "Version_20260101_0000"
    return VersionContext(version_dir=version_dir, sit_name="Test SIT", language=None, layout="english")


def test_combined_total_adds_agreements_and_disagreements(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write(ctx.version_dir / "export_summary.json",
           {"counts": {"positive": 675, "negative": 1405, "total": 2080}})
    _write(ctx.version_dir / "Disagreements" / "export_summary.json",
           {"counts": {"positive": 25, "negative": 95, "total": 120}})

    dc = _read_doc_counts(ctx)

    assert dc["total"] == 2080  # unchanged meaning: Agreements-only
    assert dc["combined_total"] == 2200  # 2080 + 120


def test_combined_total_none_when_disagreements_unreadable(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write(ctx.version_dir / "export_summary.json",
           {"counts": {"positive": 675, "negative": 1405, "total": 2080}})
    # no Disagreements/export_summary.json at all

    dc = _read_doc_counts(ctx)

    assert dc["total"] == 2080
    assert dc["combined_total"] is None


def test_combined_total_falls_back_to_positive_plus_negative_when_total_key_absent(tmp_path):
    ctx = _make_ctx(tmp_path)
    _write(ctx.version_dir / "export_summary.json",
           {"counts": {"positive": 10, "negative": 20}})
    _write(ctx.version_dir / "Disagreements" / "export_summary.json",
           {"counts": {"positive": 1, "negative": 2}})

    dc = _read_doc_counts(ctx)

    assert dc["total"] == 30
    assert dc["combined_total"] == 33
