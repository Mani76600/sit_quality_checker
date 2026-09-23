from qc.models import CheckResult, RunReport, Status, strip_root_prefix


def _report_with(version_dir: str, detail: str) -> RunReport:
    rep = RunReport(label="Australia Tax File Number / Version_20260813_1412",
                     version_dir=version_dir)
    rep.add(CheckResult(
        status=Status.FAIL, category="export_summary", item_ref="export_summary.json",
        title=f"Mismatch in {version_dir}/export_summary.json",
        detail=f"See {version_dir}/export_summary.json for details",
        scope="Agreements",
        fix=f"Fix {version_dir}/export_summary.json",
        evidence=[f"{version_dir}/export_summary.json"],
    ))
    return rep


def test_strip_root_prefix_posix():
    root = "/tmp/qcweb_0v4uxyfe/extracted"
    version_dir = f"{root}/Australia Tax File Number/Version_20260813_1412"
    rep = _report_with(version_dir, "irrelevant")
    strip_root_prefix(rep, root)
    expected = "Australia Tax File Number/Version_20260813_1412"
    assert rep.version_dir == expected
    r = rep.results[0]
    assert root not in r.title
    assert root not in r.detail
    assert root not in r.fix
    assert root not in r.evidence[0]
    assert f"{expected}/export_summary.json" in r.detail


def test_strip_root_prefix_windows_style():
    root = r"C:\Users\me\AppData\Local\Temp\qcweb_abc\extracted"
    version_dir = root + r"\Australia Tax File Number\Version_20260813_1412"
    rep = _report_with(version_dir, "irrelevant")
    strip_root_prefix(rep, root)
    assert root not in rep.version_dir
    assert "Australia Tax File Number" in rep.version_dir


def test_strip_root_prefix_noop_when_root_empty():
    rep = _report_with("/some/path/Version_x", "d")
    strip_root_prefix(rep, "")
    assert rep.version_dir == "/some/path/Version_x"


def test_strip_root_prefix_handles_missing_or_empty_fields():
    rep = RunReport(label="x", version_dir="/tmp/root/x")
    rep.add(CheckResult(status=Status.PASS, category="c", item_ref="", title="",
                         detail="", scope="", fix="", evidence=[]))
    strip_root_prefix(rep, "/tmp/root")
    assert rep.version_dir == "x"
