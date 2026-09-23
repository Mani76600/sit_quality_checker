"""AppTest fixture script: renders a small synthetic RunReport (and, if
FIXTURE_MULTI_RUN is set, a run-all summary table too) so
test_streamlit_report_navigation.py can execute the real Streamlit render
path and inspect the emitted markdown/HTML."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qc.models import CheckResult, RunReport, Status
from qc.streamlit_report import render_report, render_run_all_summary_table


def _make_report(label: str, version_dir: str, with_fail: bool, with_warn: bool,
                  with_info: bool = False, doc_counts: dict | None = None) -> RunReport:
    rep = RunReport(label=label, version_dir=version_dir, doc_counts=doc_counts or {})
    if with_fail:
        rep.add(CheckResult(Status.FAIL, "3. context_output_normalized", "3",
                             "Bad thing", "detail here", "Agreements", "fix it"))
    if with_warn:
        rep.add(CheckResult(Status.WARN, "8. Metadata", "8",
                             "Iffy thing", "detail warn", "Agreements"))
    if with_info:
        rep.add(CheckResult(Status.INFO, "9. raw_doc Parity & SIT Identity", "9",
                             "Just a note", "detail info", "Agreements"))
    rep.add(CheckResult(Status.PASS, "1-2. Folder Structure", "1",
                         "Good thing", "detail2", "Agreements"))
    return rep


if os.environ.get("FIXTURE_DOC_COUNTS"):
    rep = _make_report("Test SIT / Version_20260101_1200",
                        "/tmp/x/Test SIT/Version_20260101_1200",
                        with_fail=False, with_warn=False, with_info=True,
                        doc_counts={"total": 150, "agreements": {"positive": 100, "negative": 50},
                                    "disagreements": {"positive": 3, "negative": 2}})
    render_report(rep)
elif os.environ.get("FIXTURE_MULTI_RUN"):
    reports = [
        _make_report("SIT A / Version_20260101_1200", "/tmp/x/SIT A/Version_20260101_1200",
                     with_fail=True, with_warn=False),
        _make_report("SIT B / Version_20260102_1300", "/tmp/x/SIT B/Version_20260102_1300",
                     with_fail=False, with_warn=False),
    ]
    render_run_all_summary_table(reports)
    for rep in reports:
        render_report(rep)
elif os.environ.get("FIXTURE_WARN_ONLY"):
    rep = _make_report("Test SIT / Version_20260101_1200",
                        "/tmp/x/Test SIT/Version_20260101_1200",
                        with_fail=False, with_warn=True)
    render_report(rep)
else:
    rep = _make_report("Test SIT / Version_20260101_1200",
                        "/tmp/x/Test SIT/Version_20260101_1200",
                        with_fail=True, with_warn=True)
    render_report(rep)
