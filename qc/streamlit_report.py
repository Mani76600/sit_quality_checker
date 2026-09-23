"""Shared Streamlit rendering for a RunReport - used identically by both the
local-path app (app.py) and the upload-based web app, so the two entrypoints
only differ in how they get a filesystem path to point qc.discovery at, never
in how results are displayed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from qc.detail_format import split_detail
from qc.models import RunReport, Status
from qc.report_render import render_html, render_pdf

STATUS_ICON = {
    Status.PASS.value: "✅",
    Status.FAIL.value: "❌",
    Status.WARN.value: "⚠️",
    Status.INFO.value: "ℹ️",
}
STATUS_ORDER = [Status.FAIL.value, Status.WARN.value, Status.INFO.value, Status.PASS.value]


def category_sort_key(category: str):
    m = re.match(r"^(\d+)", category)
    return (int(m.group(1)) if m else 999, category)


def worst_status(results) -> tuple[str, dict]:
    cat_counts = {s: sum(1 for r in results if r.status.value == s) for s in STATUS_ORDER}
    return next((s for s in STATUS_ORDER if cat_counts[s]), Status.PASS.value), cat_counts


def _slugify(*parts: str) -> str:
    """Stable, HTML-id-safe anchor slug for a report/category so a link can
    jump straight to it - same rules on both ends (link + target), so
    a category with the same name always resolves to the same anchor."""
    s = "-".join(parts).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return f"qc-{s}"


def _anchor(html_id: str) -> None:
    """An invisible jump target - `scroll-margin-top` keeps Streamlit's
    sticky header from covering the section right after the jump."""
    st.markdown(f'<div id="{html_id}" style="scroll-margin-top: 4rem;"></div>',
                unsafe_allow_html=True)


def render_report(report: RunReport) -> None:
    counts = report.counts()
    report_anchor = _slugify("run", report.version_dir)
    _anchor(report_anchor)
    st.subheader(report.label)
    st.caption(str(report.version_dir))

    grouped = report.by_category()
    ordered_categories = sorted(grouped, key=category_sort_key)
    cat_anchor = {category: _slugify("run", report.version_dir, "cat", category)
                  for category in ordered_categories}

    n_fail, n_warn = counts.get("FAIL", 0), counts.get("WARN", 0)
    if n_fail:
        st.error(f"❌ {n_fail} check(s) failed — needs fixes before this output ships. "
                 f"See the checklist below for exactly which ones.")
        failed_cats = [c for c in ordered_categories if worst_status(grouped[c])[0] == Status.FAIL.value]
        links = " &nbsp;·&nbsp; ".join(
            f'<a href="#{cat_anchor[c]}">❌ {c}</a>' for c in failed_cats)
        st.markdown(f"**Jump to failed check(s):** {links}", unsafe_allow_html=True)
    elif n_warn:
        st.warning(f"⚠️ All mandatory checks passed, but {n_warn} item(s) need a human look "
                   f"(warnings) — see below.")
        warn_cats = [c for c in ordered_categories if worst_status(grouped[c])[0] == Status.WARN.value]
        links = " &nbsp;·&nbsp; ".join(
            f'<a href="#{cat_anchor[c]}">⚠️ {c}</a>' for c in warn_cats)
        st.markdown(f"**Jump to warning(s):** {links}", unsafe_allow_html=True)
    else:
        st.success("✅ Every quality check passed for this run.")

    cols = st.columns(4)
    for col, status in zip(cols, STATUS_ORDER):
        col.metric(f"{STATUS_ICON[status]} {status}", counts.get(status, 0))

    st.markdown("#### Checklist at a glance")
    st.caption("Click a checklist item to jump straight to it in the Details section below.")
    table_rows = []
    for category in ordered_categories:
        results = grouped[category]
        worst, cat_counts = worst_status(results)
        total = len(results)
        bits = [f"{cat_counts[Status.PASS.value]}/{total} passed"]
        if cat_counts[Status.FAIL.value]:
            bits.append(f"{cat_counts[Status.FAIL.value]} FAILED")
        if cat_counts[Status.WARN.value]:
            bits.append(f"{cat_counts[Status.WARN.value]} warning(s)")
        table_rows.append(
            f'<tr><td style="text-align:center">{STATUS_ICON[worst]}</td>'
            f'<td><a href="#{cat_anchor[category]}">{category}</a></td>'
            f'<td>{", ".join(bits)}</td></tr>')
    st.markdown(
        '<table style="width:100%; border-collapse: collapse;">'
        '<thead><tr>'
        '<th style="text-align:center; padding:4px 8px; border-bottom:1px solid rgba(128,128,128,0.4)"></th>'
        '<th style="text-align:left; padding:4px 8px; border-bottom:1px solid rgba(128,128,128,0.4)">Checklist item</th>'
        '<th style="text-align:left; padding:4px 8px; border-bottom:1px solid rgba(128,128,128,0.4)">Result</th>'
        '</tr></thead><tbody>'
        + "".join(table_rows)
        + "</tbody></table>",
        unsafe_allow_html=True,
    )

    base_name = f"qc_report_{Path(report.version_dir).name}"
    dl_cols = st.columns(3)
    dl_cols[0].download_button(
        "Download as JSON",
        data=json.dumps(report.to_dict(), indent=2),
        file_name=f"{base_name}.json",
        mime="application/json",
        key=f"dl_json_{report.version_dir}",
    )
    dl_cols[1].download_button(
        "Download as HTML (shareable)",
        data=render_html(report),
        file_name=f"{base_name}.html",
        mime="text/html",
        key=f"dl_html_{report.version_dir}",
    )
    dl_cols[2].download_button(
        "Download as PDF",
        data=render_pdf(report),
        file_name=f"{base_name}.pdf",
        mime="application/pdf",
        key=f"dl_pdf_{report.version_dir}",
    )

    st.markdown("#### Details (categories with a failure or warning are expanded automatically)")
    for category in ordered_categories:
        results = grouped[category]
        worst, cat_counts = worst_status(results)
        badge = " ".join(f"{STATUS_ICON[s]}{cat_counts[s]}" for s in STATUS_ORDER if cat_counts[s])
        needs_attention = worst in (Status.FAIL.value, Status.WARN.value)
        _anchor(cat_anchor[category])
        with st.expander(f"{STATUS_ICON[worst]} {category}  —  {badge}", expanded=needs_attention):
            actionable = [r for r in results if r.status != Status.PASS]
            passed = [r for r in results if r.status == Status.PASS]

            for r in actionable:
                st.markdown(f"**{STATUS_ICON[r.status.value]} [{r.item_ref}] {r.title}**"
                            + (f"  \n*scope: {r.scope}*" if r.scope else ""))
                lead, examples = split_detail(r.detail)
                st.write(lead)
                if examples:
                    st.markdown("\n".join(f"- {item}" for item in examples))
                if r.fix:
                    st.info(f"Suggested fix: {r.fix}")
                st.divider()

            if passed:
                st.caption(f"✅ {len(passed)} passed check(s) in this category:")
                st.dataframe(
                    pd.DataFrame([{
                        "Title": r.title,
                        "Scope": r.scope,
                        "Detail": r.detail,
                    } for r in passed]),
                    width='stretch', hide_index=True,
                )


def render_run_all_summary_table(reports) -> None:
    """The 'Summary across all runs' table shown when multiple runs are found."""
    st.subheader("Summary across all runs")
    st.caption("Click a run to jump straight to its full report below.")
    header_cells = "".join(
        f'<th style="text-align:left; padding:4px 8px; border-bottom:1px solid rgba(128,128,128,0.4)">{h}</th>'
        for h in ["Verdict", "Run", *STATUS_ORDER])
    body_rows = []
    for rep in reports:
        c = rep.counts()
        verdict = ("❌ Needs fixes" if c.get("FAIL")
                   else ("⚠️ Review warnings" if c.get("WARN") else "✅ Clean"))
        anchor = _slugify("run", rep.version_dir)
        count_cells = "".join(f"<td>{c.get(s, 0)}</td>" for s in STATUS_ORDER)
        body_rows.append(
            f"<tr><td>{verdict}</td>"
            f'<td><a href="#{anchor}">{rep.label}</a></td>'
            f"{count_cells}</tr>")
    st.markdown(
        f'<table style="width:100%; border-collapse: collapse;">'
        f"<thead><tr>{header_cells}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>",
        unsafe_allow_html=True,
    )
