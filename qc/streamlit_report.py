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


def render_report(report: RunReport) -> None:
    counts = report.counts()
    st.subheader(report.label)
    st.caption(str(report.version_dir))

    n_fail, n_warn = counts.get("FAIL", 0), counts.get("WARN", 0)
    if n_fail:
        st.error(f"❌ {n_fail} check(s) failed — needs fixes before this output ships. "
                 f"See the checklist below for exactly which ones.")
    elif n_warn:
        st.warning(f"⚠️ All mandatory checks passed, but {n_warn} item(s) need a human look "
                   f"(warnings) — see below.")
    else:
        st.success("✅ Every quality check passed for this run.")

    cols = st.columns(4)
    for col, status in zip(cols, STATUS_ORDER):
        col.metric(f"{STATUS_ICON[status]} {status}", counts.get(status, 0))

    grouped = report.by_category()
    ordered_categories = sorted(grouped, key=category_sort_key)

    st.markdown("#### Checklist at a glance")
    summary_rows = []
    for category in ordered_categories:
        results = grouped[category]
        worst, cat_counts = worst_status(results)
        total = len(results)
        bits = [f"{cat_counts[Status.PASS.value]}/{total} passed"]
        if cat_counts[Status.FAIL.value]:
            bits.append(f"{cat_counts[Status.FAIL.value]} FAILED")
        if cat_counts[Status.WARN.value]:
            bits.append(f"{cat_counts[Status.WARN.value]} warning(s)")
        summary_rows.append({
            "": STATUS_ICON[worst],
            "Checklist item": category,
            "Result": ", ".join(bits),
        })
    st.dataframe(pd.DataFrame(summary_rows), width='stretch', hide_index=True)

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
    summary_rows = []
    for rep in reports:
        c = rep.counts()
        verdict = ("❌ Needs fixes" if c.get("FAIL")
                   else ("⚠️ Review warnings" if c.get("WARN") else "✅ Clean"))
        summary_rows.append({"Verdict": verdict, "Run": rep.label, **c})
    st.subheader("Summary across all runs")
    st.dataframe(pd.DataFrame(summary_rows), width='stretch', hide_index=True)
