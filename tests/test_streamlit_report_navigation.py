"""Regression tests for the clickable-navigation feature in
qc/streamlit_report.py: the "Checklist at a glance" table and the "Jump to
failed/warning check(s)" links must point at anchor ids that actually
exist elsewhere on the same rendered page, so clicking them really jumps
to the right section instead of a dead link."""

import re
from pathlib import Path

import pytest

from streamlit.testing.v1 import AppTest

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
    at = AppTest.from_file(str(FIXTURE))
    at.run()
    assert not at.exception


def test_every_link_target_exists_on_the_page():
    at = AppTest.from_file(str(FIXTURE))
    at.run()
    html = _all_markdown_html(at)
    anchors = _anchor_ids(html)
    hrefs = _hrefs(html)
    assert hrefs, "expected at least one clickable link in the rendered report"
    missing = hrefs - anchors
    assert not missing, f"link(s) point at anchors that don't exist on the page: {missing}"


def test_glance_table_links_every_category():
    at = AppTest.from_file(str(FIXTURE))
    at.run()
    html = _all_markdown_html(at)
    for category in ["3. context_output_normalized", "8. Metadata", "1-2 Folder Structure"]:
        assert f">{category}</a>" in html, f"expected a clickable link for category: {category}"


def test_jump_to_failed_link_present_when_fail_and_warn_both_exist():
    # FAIL takes priority over WARN for the top banner (pre-existing
    # behavior, unchanged) - the glance table below still links every
    # category regardless, covered by test_glance_table_links_every_category.
    at = AppTest.from_file(str(FIXTURE))
    at.run()
    html = _all_markdown_html(at)
    assert "Jump to failed check(s)" in html


def test_jump_to_warning_link_present_when_only_warnings(monkeypatch):
    monkeypatch.setenv("FIXTURE_WARN_ONLY", "1")
    at = AppTest.from_file(str(FIXTURE))
    at.run()
    assert not at.exception
    html = _all_markdown_html(at)
    assert "Jump to warning(s)" in html
    anchors = _anchor_ids(html)
    hrefs = _hrefs(html)
    assert not (hrefs - anchors)


def test_multi_run_summary_table_links_to_each_run(monkeypatch):
    monkeypatch.setenv("FIXTURE_MULTI_RUN", "1")
    at = AppTest.from_file(str(FIXTURE))
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


def test_clean_run_has_no_jump_links(monkeypatch):
    # A run with zero FAIL/WARN shouldn't render a "Jump to failed/warning"
    # line pointing at nothing.
    monkeypatch.setenv("FIXTURE_MULTI_RUN", "1")
    at = AppTest.from_file(str(FIXTURE))
    at.run()
    html = _all_markdown_html(at)
    # SIT B (clean) still has its own report section; just make sure the
    # overall page doesn't claim more jump-links than there are FAIL/WARN
    # categories (SIT A contributes exactly one "Jump to failed").
    assert html.count("Jump to failed check(s)") == 1
    assert html.count("Jump to warning(s)") == 0
