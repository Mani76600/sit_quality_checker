"""Regression tests for the clickable-navigation feature in
qc/streamlit_report.py: the "Checklist at a glance" table and the "Jump to
failed/warning check(s)" links must point at anchor ids that actually
exist elsewhere on the same rendered page, so clicking them really jumps
to the right section instead of a dead link."""

import re
from pathlib import Path

import pytest

from streamlit.testing.v1 import AppTest

from qc.models import Status
from qc.streamlit_report import _display_category, _effective_worst_and_passed

FIXTURE = Path(__file__).parent / "fixture_apps" / "nav_report_app.py"
REAL_FIXTURE = Path(__file__).parent / "fixture_apps" / "nav_real_report_app.py"
REAL_SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "South Africa Identification Number"


def _all_markdown_html(at) -> str:
    return "\n".join(m.value for m in at.markdown)


def _anchor_ids(html: str) -> set[str]:
    return set(re.findall(r'<div id="([^"]+)"', html))


def _hrefs(html: str) -> set[str]:
    return {h.lstrip("#") for h in re.findall(r'href="#([^"]+)"', html)}


def test_single_report_renders_without_error():
    at = AppTest.from_file(str(FIXTURE), default_timeout=30)
    at.run()
    assert not at.exception


def test_every_link_target_exists_on_the_page():
    at = AppTest.from_file(str(FIXTURE), default_timeout=30)
    at.run()
    html = _all_markdown_html(at)
    anchors = _anchor_ids(html)
    hrefs = _hrefs(html)
    assert hrefs, "expected at least one clickable link in the rendered report"
    missing = hrefs - anchors
    assert not missing, f"link(s) point at anchors that don't exist on the page: {missing}"


def test_glance_table_links_every_category():
    # Category names are shown with their leading checklist number stripped
    # (e.g. "3. context_output_normalized" -> "context_output_normalized") -
    # now that every row is a clickable jump target, the number is just
    # noise; the raw numbered string is still used internally for sorting
    # and anchors, just never shown to the user.
    at = AppTest.from_file(str(FIXTURE), default_timeout=30)
    at.run()
    html = _all_markdown_html(at)
    for category in ["3. context_output_normalized", "8. Metadata", "1-2. Folder Structure"]:
        assert f">{category}</a>" not in html, f"number prefix should be stripped for: {category}"
    for display_name in ["context_output_normalized", "Metadata", "Folder Structure"]:
        assert f">{display_name}</a>" in html, f"expected a clickable link for: {display_name}"


def test_jump_to_failed_link_present_when_fail_and_warn_both_exist():
    # FAIL takes priority over WARN for the top banner (pre-existing
    # behavior, unchanged) - the glance table below still links every
    # category regardless, covered by test_glance_table_links_every_category.
    at = AppTest.from_file(str(FIXTURE), default_timeout=30)
    at.run()
    html = _all_markdown_html(at)
    assert "Jump to failed check(s)" in html


def test_jump_to_warning_link_present_when_only_warnings(monkeypatch):
    monkeypatch.setenv("FIXTURE_WARN_ONLY", "1")
    at = AppTest.from_file(str(FIXTURE), default_timeout=30)
    at.run()
    assert not at.exception
    html = _all_markdown_html(at)
    assert "Jump to warning(s)" in html
    anchors = _anchor_ids(html)
    hrefs = _hrefs(html)
    assert not (hrefs - anchors)


def test_multi_run_summary_table_links_to_each_run(monkeypatch):
    monkeypatch.setenv("FIXTURE_MULTI_RUN", "1")
    at = AppTest.from_file(str(FIXTURE), default_timeout=30)
    at.run()
    assert not at.exception
    html = _all_markdown_html(at)
    anchors = _anchor_ids(html)
    hrefs = _hrefs(html)
    assert not (hrefs - anchors)
    assert ">SIT A / Version_20260101_1200</a>" in html
    assert ">SIT B / Version_20260102_1300</a>" in html


@pytest.mark.skipif(not REAL_SAMPLE_ROOT.exists(), reason="real sample output not present on this machine")
def test_real_report_with_messy_category_names_links_resolve():
    # Real category names contain dots/slashes/parentheses (e.g. "4. Index
    # Files (sit_inverted_index.json / sit_merged_index.json)") - confirm
    # the slugifier still produces matching, unique anchors for these, not
    # just for the clean synthetic names in nav_report_app.py.
    at = AppTest.from_file(str(REAL_FIXTURE), default_timeout=120)
    at.run()
    assert not at.exception
    html = _all_markdown_html(at)
    anchors = _anchor_ids(html)
    hrefs = _hrefs(html)
    assert hrefs, "expected clickable links in a real report"
    missing = hrefs - anchors
    assert not missing, f"link(s) point at anchors that don't exist on the page: {missing}"


@pytest.mark.parametrize("raw,expected", [
    ("3. context_output_normalized", "context_output_normalized"),
    ("1-2. Folder Structure", "Folder Structure"),
    ("4. Index Files (sit_inverted_index.json / sit_merged_index.json)",
     "Index Files (sit_inverted_index.json / sit_merged_index.json)"),
    ("9b. Content Verification (value-in-context)", "Content Verification (value-in-context)"),
    ("10. Keyword Requirement / semantic_ground_truth", "Keyword Requirement / semantic_ground_truth"),
    ("Integrity (addition)", "Integrity (addition)"),  # no leading number - unchanged
    ("SITGrader Consistency (addition)", "SITGrader Consistency (addition)"),
])
def test_display_category_strips_leading_number(raw, expected):
    assert _display_category(raw) == expected


def test_effective_worst_and_passed_folds_info_into_pass():
    cat_counts = {Status.FAIL.value: 0, Status.WARN.value: 0, Status.INFO.value: 3, Status.PASS.value: 5}
    worst, passed = _effective_worst_and_passed(cat_counts)
    assert worst == Status.PASS.value
    assert passed == 8


def test_effective_worst_and_passed_fail_takes_priority():
    cat_counts = {Status.FAIL.value: 1, Status.WARN.value: 2, Status.INFO.value: 3, Status.PASS.value: 5}
    worst, passed = _effective_worst_and_passed(cat_counts)
    assert worst == Status.FAIL.value
    assert passed == 8  # info still folds into passed even when FAIL is present elsewhere


def test_effective_worst_and_passed_warn_without_fail():
    cat_counts = {Status.FAIL.value: 0, Status.WARN.value: 1, Status.INFO.value: 0, Status.PASS.value: 2}
    worst, passed = _effective_worst_and_passed(cat_counts)
    assert worst == Status.WARN.value
    assert passed == 2


def test_top_metrics_row_has_only_fail_and_pass_with_info_folded_in():
    at = AppTest.from_file(str(FIXTURE), default_timeout=30)
    at.run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert set(metrics.keys()) == {"❌ FAIL", "✅ PASS"}
    assert metrics["❌ FAIL"] == "1"
    assert metrics["✅ PASS"] == "1"  # 1 literal PASS + 0 INFO in this fixture


def test_info_results_land_in_passed_table_not_actionable_callouts(monkeypatch):
    monkeypatch.setenv("FIXTURE_DOC_COUNTS", "1")
    at = AppTest.from_file(str(FIXTURE), default_timeout=30)
    at.run()
    assert not at.exception
    html = _all_markdown_html(at)
    # An INFO result's title must not appear as a flagged callout (which
    # would bold it with its own status icon + a "Suggested fix" box) -
    # it should only show up inside the condensed passed-checks dataframe.
    assert "**ℹ️" not in html
    assert "Just a note" not in html  # only in the dataframe, not markdown text
    dataframes = [d.value for d in at.dataframe]
    assert any("Just a note" in df.to_string() for df in dataframes)


def test_doc_counts_highlight_renders_when_present(monkeypatch):
    monkeypatch.setenv("FIXTURE_DOC_COUNTS", "1")
    at = AppTest.from_file(str(FIXTURE), default_timeout=30)
    at.run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics.get("Total documents") == "150"
    assert metrics.get("Agreements") == "100 pos / 50 neg"
    assert metrics.get("Disagreements") == "3 pos / 2 neg"


def test_doc_counts_highlight_absent_when_not_populated():
    at = AppTest.from_file(str(FIXTURE), default_timeout=30)
    at.run()
    assert not at.exception
    metrics = {m.label for m in at.metric}
    assert "Total documents" not in metrics


def test_clean_run_has_no_jump_links(monkeypatch):
    # A run with zero FAIL/WARN shouldn't render a "Jump to failed/warning"
    # line pointing at nothing.
    monkeypatch.setenv("FIXTURE_MULTI_RUN", "1")
    at = AppTest.from_file(str(FIXTURE), default_timeout=30)
    at.run()
    html = _all_markdown_html(at)
    # SIT B (clean) still has its own report section; just make sure the
    # overall page doesn't claim more jump-links than there are FAIL/WARN
    # categories (SIT A contributes exactly one "Jump to failed").
    assert html.count("Jump to failed check(s)") == 1
    assert html.count("Jump to warning(s)") == 0
