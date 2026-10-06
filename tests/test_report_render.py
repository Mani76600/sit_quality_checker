"""Two real bugs found by inspecting a real downloadable report:

1. Passed checks only showed "title (scope)" in the HTML/PDF export,
   dropping the detail text entirely - invisible for checks whose whole
   point is showing counts (Business Context Balance, Document Format
   Distribution, ...), even though the live Streamlit app already showed
   detail for every passed check via a dataframe.
2. The downloadable HTML/PDF still showed raw category strings like
   "5. export_summary.json ground truth", even though the live Streamlit
   app already strips this leading number via its own _display_category
   (there's no on-screen jump target in a static export either, so the
   number is just noise) - report_render.py never got the same treatment.
"""

import re

from qc.models import CheckResult, RunReport, Status
from qc.report_render import _display_category, render_html, render_pdf


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


def test_render_html_shows_detail_for_passed_checks():
    html_out = render_html(_sample_report())
    idx = html_out.find("domain distribution")
    assert idx != -1
    # The detail text must appear somewhere near the title, not be dropped.
    assert "Healthcare=5, Finance=3" in html_out


def test_render_html_strips_leading_numbers_from_category_display():
    html_out = render_html(_sample_report())
    assert "3. context_output_normalized" not in html_out
    assert "5. export_summary.json ground truth" not in html_out
    assert "context_output_normalized" in html_out
    assert "export_summary.json ground truth" in html_out
    # No category header or glance-table cell should start with a digit.
    headers = re.findall(r"<h2>([^<]*)</h2>", html_out)
    assert not any(h[:1].isdigit() for h in headers)


def test_render_pdf_shows_detail_for_passed_checks_and_strips_numbers():
    import io

    from pypdf import PdfReader

    pdf_bytes = render_pdf(_sample_report())
    assert len(pdf_bytes) > 100
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf_bytes)).pages)
    assert "Healthcare=5, Finance=3" in text
    assert "3. context_output_normalized" not in text
    assert "5. export_summary.json ground truth" not in text


def _report_with_huge_detail() -> RunReport:
    """A real downloaded report had a "Content Verification" check whose
    detail joined several full document excerpts together (" || "-separated,
    several KB total) - unreadable dumped whole into an HTML table cell or a
    PDF paragraph."""
    report = RunReport(label="Test SIT / Version_20260101_0000", version_dir="C:\\out\\Version_20260101_0000")
    huge = " || ".join(f"[doc_{i}.txt] some real document excerpt text here " * 20 for i in range(10))
    report.add(CheckResult(
        Status.PASS, "9b. Content Verification (value-in-context)", "9b",
        "Example value-in-context extractions", huge, "Agreements",
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


def test_render_html_wraps_every_result_in_a_collapsed_details_row():
    """Every single result - PASS or FAIL alike - is a compact table row by
    default (a short one-line summary), with the full detail/examples/fix
    tucked behind a native <details> inside that same row's cell. A large
    corpus can produce hundreds of results, so the page must open on short,
    scannable rows, not a wall of always-expanded text, while still making
    the full detail reachable with one click - for a FAIL exactly as much
    as for a PASS, since both need to stay readable at scale."""
    html_out = render_html(_sample_report())
    assert "result-table" in html_out
    assert "row-expand" in html_out
    # One result each (PASS + FAIL) -> one details-wrapped row each.
    assert html_out.count('<details class="row-expand">') == 2
