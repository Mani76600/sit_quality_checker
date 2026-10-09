"""Checks the compact, PowerBI-dashboard-style checklist rendering in
report_render.py:

1. Passed checks show NO per-check detail anywhere - a category where every
   check passed collapses to one line (name + its total check count). Detail
   boxes for passes were explicitly identified as "over-showing" real report
   output and removed; only FAILING checks keep their detail/examples/fix
   (that's the actionable information).
2. Category display strips the leading checklist number
   ("3. context_output_normalized" -> "context_output_normalized") in both
   the HTML and PDF export - there's no on-screen jump target in a static
   export, so the number is just noise.
"""

import re

from qc.models import CheckResult, RunReport, Status
from qc.report_render import _display_category, _verdict_text, render_html, render_pdf


def test_display_category_strips_various_leading_number_shapes():
    assert _display_category("3. context_output_normalized") == "context_output_normalized"
    assert _display_category("1-2. Folder Structure") == "Folder Structure"
    assert _display_category("9b. Content Verification (value-in-context)") == (
        "Content Verification (value-in-context)"
    )
    assert _display_category("6-7. corpus.jsonl / combined_metadata.jsonl counts") == (
        "corpus.jsonl / combined_metadata.jsonl counts"
    )
    # Unnumbered "(addition)" categories are untouched.
    assert _display_category("Business Context Balance (addition)") == (
        "Business Context Balance (addition)"
    )


def _sample_report() -> RunReport:
    report = RunReport(label="Test SIT / Version_20260101_0000", version_dir="C:\\out\\Version_20260101_0000")
    report.add(CheckResult(
        Status.PASS, "3. context_output_normalized", "3",
        "domain distribution", "Healthcare=5, Finance=3 (total=8, distinct=2)", "Agreements",
    ))
    report.add(CheckResult(
        Status.FAIL, "5. export_summary.json ground truth", "5",
        "counts.total == positive + negative", "5 != 4 (delta +1)", "Agreements",
        "Reconcile the counts.",
    ))
    return report


def test_render_html_does_not_show_detail_for_passed_checks():
    """The PASS category ("context_output_normalized") collapses to a
    single name+count line - its check's detail text ("Healthcare=5,
    Finance=3...") must not appear anywhere on the page."""
    html_out = render_html(_sample_report())
    assert "Healthcare=5, Finance=3" not in html_out
    assert "domain distribution" not in html_out
    assert "context_output_normalized" in html_out  # the category name itself still shows


def test_render_html_shows_detail_only_for_failed_checks():
    html_out = render_html(_sample_report())
    assert "counts.total == positive + negative" in html_out
    assert "5 != 4 (delta +1)" in html_out
    assert "Reconcile the counts." in html_out


def test_render_html_strips_leading_numbers_from_category_display():
    html_out = render_html(_sample_report())
    assert "3. context_output_normalized" not in html_out
    assert "5. export_summary.json ground truth" not in html_out
    assert "context_output_normalized" in html_out
    assert "export_summary.json ground truth" in html_out
    # No category header or glance-table cell should start with a digit.
    headers = re.findall(r"<h2>([^<]*)</h2>", html_out)
    assert not any(h[:1].isdigit() for h in headers)


def test_render_pdf_shows_fail_detail_but_not_pass_detail_and_strips_numbers():
    import io

    from pypdf import PdfReader

    pdf_bytes = render_pdf(_sample_report())
    assert len(pdf_bytes) > 100
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf_bytes)).pages)
    assert "Healthcare=5, Finance=3" not in text
    assert "5 != 4 (delta +1)" in text
    assert "Reconcile the counts." in text
    assert "3. context_output_normalized" not in text
    assert "5. export_summary.json ground truth" not in text


def _report_with_huge_detail() -> RunReport:
    """A real downloaded report had a "Content Verification" check whose
    detail joined several full document excerpts together (" || "-separated,
    several KB total) - unreadable dumped whole into an HTML/PDF page.
    FAIL (not PASS) here, since only FAIL details are shown at all now -
    truncation only matters for the detail that's actually rendered."""
    report = RunReport(label="Test SIT / Version_20260101_0000", version_dir="C:\\out\\Version_20260101_0000")
    huge = " || ".join(f"[doc_{i}.txt] some real document excerpt text here " * 20 for i in range(10))
    report.add(CheckResult(
        Status.FAIL, "9b. Content Verification (value-in-context)", "9b",
        "Example value-in-context extractions", huge, "Agreements",
        "Investigate the listed document(s).",
    ))
    return report


def test_render_html_truncates_pathologically_long_detail():
    html_out = render_html(_report_with_huge_detail())
    assert "truncated" in html_out
    # The full several-KB blob must not appear verbatim.
    assert " || ".join(["x"] * 10) not in html_out  # sanity: separator alone isn't the whole thing
    huge_detail = _report_with_huge_detail().results[0].detail
    assert huge_detail not in html_out


def test_render_pdf_truncates_pathologically_long_detail():
    import io

    from pypdf import PdfReader

    pdf_bytes = render_pdf(_report_with_huge_detail())
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf_bytes)).pages)
    assert "truncated" in text
    huge_detail = _report_with_huge_detail().results[0].detail
    assert huge_detail not in text


def test_passed_category_collapses_to_a_single_name_and_count_line():
    """A category where every check passed never gets a per-check table -
    just one compact row with its total check count, since the color
    already says every one of them passed."""
    report = RunReport(label="Test SIT / Version_20260101_0000", version_dir="C:\\out\\Version_20260101_0000")
    for i in range(3):
        report.add(CheckResult(Status.PASS, "Folder Structure", "1", f"check {i}", f"detail {i}", "Agreements"))
    html_out = render_html(report)
    assert "Folder Structure" in html_out
    assert ">3<" in html_out  # the total check count for this category
    for i in range(3):
        assert f"detail {i}" not in html_out


def test_failing_category_lists_only_its_failing_checks():
    """A category with a mix of PASS and FAIL shows the FAIL(s) in full,
    but never enumerates the passing checks alongside them."""
    report = RunReport(label="Test SIT / Version_20260101_0000", version_dir="C:\\out\\Version_20260101_0000")
    report.add(CheckResult(Status.PASS, "Integrity", "1", "ok check", "nothing wrong here", "Agreements"))
    report.add(CheckResult(Status.FAIL, "Integrity", "1", "broken check", "it broke", "Agreements", "fix it"))
    html_out = render_html(report)
    assert "broken check" in html_out
    assert "it broke" in html_out
    assert "ok check" not in html_out
    assert "nothing wrong here" not in html_out
    assert ">1 / 2<" in html_out  # 1 passed out of 2 total in this category


def test_verdict_text_never_shows_zero_failed_checks_when_a_quality_metric_gated_it():
    """Real bug: with 0 FAIL checks but a quality metric gated-critical, the
    old verdict read "FAILING: 0 check(s) failed + 1 quality metric(s)...
    needs fixes before this output ships." - confusing (0 failed, yet
    FAILING) and verbose. The subline must only mention the counts that are
    actually non-zero."""
    headline, subline, is_failing = _verdict_text(0, ["Negative Observed-Label Wording Uniqueness"])
    assert is_failing is True
    assert "0" not in subline
    assert "failed check" not in subline
    assert "1 quality metric" in subline
    assert "needs fixes before this output ships" not in subline


def test_verdict_text_shows_failed_checks_only_when_no_gated_metric():
    headline, subline, is_failing = _verdict_text(3, [])
    assert is_failing is True
    assert subline == "3 failed checks"
    assert "needs fixes before this output ships" not in subline


def test_verdict_text_clean_when_nothing_failed():
    headline, subline, is_failing = _verdict_text(0, [])
    assert is_failing is False
    assert headline == "Ready to ship"


def test_render_html_verdict_omits_zero_count_when_quality_metric_gated_it():
    report = RunReport(label="Test SIT / Version_20260101_0000", version_dir="C:\\out\\Version_20260101_0000")
    report.quality_metrics = {
        "negative_label_uniqueness": {
            "label": "Negative Observed-Label Wording Uniqueness",
            "gate": True, "grade": "critical", "score": 0.1,
            "detail": "100 distinct label(s) / 1000 located negative SIT occurrence(s) = 0.100.",
            "sample_note": "Every located negative SIT occurrence",
        },
    }
    html_out = render_html(report)
    assert "0 check(s) failed" not in html_out
    assert "0 failed check" not in html_out
    assert "needs fixes before this output ships" not in html_out
    assert "Not ready to ship" in html_out
