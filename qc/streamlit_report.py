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

from qc.check_groups import ADDITIONAL_CHECK_CATEGORIES, ADDITIONAL_CHECKS_SECTION_TITLE
from qc.detail_format import split_detail
from qc.models import RunReport, Status
from qc.report_render import (
    _gated_critical_metrics, _quality_metrics_section_html, _truncate, render_html, render_pdf,
)

def _inject_metric_css() -> None:
    """Streamlit's default st.metric value font (~2.25rem, no wrapping) cuts
    off longer values like "4901 pos / 9746 neg" with an ellipsis - shrink
    it and allow wrapping so every metric stays fully readable. Also styles
    the raw-HTML result tables (see _result_table_html) - injected once per
    render rather than per category, since it's the same handful of rules
    every time."""
    st.markdown(
        "<style>"
        '[data-testid="stMetricValue"] { font-size: 1.3rem; white-space: normal; '
        "overflow-wrap: break-word; line-height: 1.3; }"
        '[data-testid="stMetricLabel"] { font-size: 0.85rem; }'
        ".qc-result-table { border-collapse: collapse; width: 100%; font-size: 0.85rem; "
        "table-layout: auto; line-height: 1.25; }"
        ".qc-result-table th, .qc-result-table td { border: 1px solid rgba(128,128,128,0.25); "
        "padding: 0.18rem 0.5rem; text-align: left; vertical-align: top; }"
        ".qc-result-table td:nth-child(1) { width: 60px; }"
        ".qc-result-table td:nth-child(2) { width: 24%; }"
        ".qc-row-badge { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 4px; "
        "color: white; font-size: 0.68rem; font-weight: 700; white-space: nowrap; }"
        ".qc-row-scope { opacity: 0.7; font-size: 0.72rem; }"
        "details.qc-row-expand summary { cursor: pointer; list-style: none; }"
        "details.qc-row-expand summary::-webkit-details-marker { display: none; }"
        'details.qc-row-expand summary::before { content: "\\25B8  "; opacity: 0.6; }'
        'details.qc-row-expand[open] summary::before { content: "\\25BE  "; }'
        ".qc-row-fix { color: #2d5a8b; font-size: 0.78rem; margin-top: 0.3rem; }"
        ".qc-row-examples { margin: 0.25rem 0 0.15rem 0; padding-left: 1.1rem; font-size: 0.78rem; }"
        # Quality Metrics section (see report_render.py's _quality_metrics_section_html,
        # reused as-is here) - same class names, defined once per render.
        ".qm-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); "
        "gap: 12px; margin: 0.6rem 0 1.3rem; }"
        ".qm-row { background: rgba(128,128,128,0.06); border: 1px solid rgba(128,128,128,0.25); "
        "border-radius: 8px; padding: 10px 14px; }"
        ".qm-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }"
        f".qm-label {{ font-weight: 700; color: {ACCENT}; font-size: 0.85rem; }}"
        ".qm-badge { color: white; font-size: 0.62rem; font-weight: 700; padding: 1px 7px; "
        "border-radius: 4px; letter-spacing: 0.03em; white-space: nowrap; }"
        f".qm-score {{ margin-left: auto; font-weight: 700; font-size: 0.9rem; color: {ACCENT}; }}"
        ".qm-detail { font-size: 0.76rem; opacity: 0.75; margin-top: 2px; }"
        ".qm-sample { opacity: 0.8; }"
        "</style>",
        unsafe_allow_html=True,
    )


def _result_table_html(results) -> str:
    """One <table> per category, every result (PASS or FAIL alike) a single
    compact row by default with the full detail/examples/fix behind a
    native <details> in the same cell ONLY when there's genuinely something
    beyond the summary (truncated lead text, examples, or a fix) - the
    Streamlit-rendered twin of report_render.py's own _result_row_html
    (same no-dead-expand-arrow rule), kept as plain HTML (not one Streamlit
    widget per row) purely for render speed at real-report scale (see the
    call site's comment)."""
    import html as _html

    color = {Status.FAIL.value: RED, Status.WARN.value: "#9a6700", Status.PASS.value: GREEN,
             Status.INFO.value: GREEN}
    rows = []
    for r in results:
        lead, examples = split_detail(r.detail)
        short = _truncate(lead, 90)
        lead_truncated = short != lead
        short_html = _html.escape(short)
        body_parts = []
        if lead_truncated:
            body_parts.append(f"<div>{_html.escape(_truncate(lead))}</div>")
        if examples:
            body_parts.append('<ul class="qc-row-examples">' + "".join(
                f"<li>{_html.escape(_truncate(item))}</li>" for item in examples) + "</ul>")
        if r.fix:
            body_parts.append(f'<div class="qc-row-fix">Suggested fix: {_html.escape(r.fix)}</div>')
        scope_html = f' <span class="qc-row-scope">({_html.escape(r.scope)})</span>' if r.scope else ""
        if body_parts:
            result_html = f'<details class="qc-row-expand"><summary>{short_html}</summary>{"".join(body_parts)}</details>'
        else:
            result_html = short_html
        rows.append(
            f'<tr><td><span class="qc-row-badge" style="background:{color.get(r.status.value, GREEN)}">'
            f'{r.status.value}</span></td>'
            f"<td>{_html.escape(r.title)}{scope_html}</td>"
            f"<td>{result_html}</td></tr>"
        )
    return ('<table class="qc-result-table"><tr><th>Status</th><th>Check</th><th>Result</th></tr>'
            + "".join(rows) + "</table>")


def _corpus_breakdown_html(cb: dict) -> str:
    """Easy/Hard x Positive/Negative: Accepted vs Generated vs Disagreed vs
    Rate per bucket - the same data report_render.py's _corpus_breakdown_stat_html
    shows, built here as plain HTML for the Streamlit page."""
    if not cb or not cb.get("total_generated"):
        return ""
    label_names = {
        "easy positive": "Easy positive", "hard positive": "Hard positive",
        "easy negative": "Easy negative", "hard negative": "Hard negative",
    }
    rows = "".join(
        f'<tr><td style="padding:3px 6px">{label_names.get(b["label"], b["label"])}</td>'
        f'<td style="text-align:right;padding:3px 6px">{b["accepted"]:,}</td>'
        f'<td style="text-align:right;padding:3px 6px">{b["generated"]:,}</td>'
        f'<td style="text-align:right;padding:3px 6px">{b["disagreed"]:,}</td>'
        f'<td style="text-align:right;padding:3px 6px">{b["rate"]:.1f}%</td></tr>'
        for b in cb.get("buckets", [])
    )
    border = 'border-bottom:1px solid rgba(128,128,128,0.4)'
    return (
        '<table style="width:100%;border-collapse:collapse;font-size:0.82rem;">'
        f'<thead><tr><th style="text-align:left;padding:3px 6px;{border}"></th>'
        f'<th style="text-align:right;padding:3px 6px;{border}">Accepted</th>'
        f'<th style="text-align:right;padding:3px 6px;{border}">Generated</th>'
        f'<th style="text-align:right;padding:3px 6px;{border}">Disagreed</th>'
        f'<th style="text-align:right;padding:3px 6px;{border}">Rate</th></tr></thead>'
        f"<tbody>{rows}"
        '<tr style="font-weight:700;border-top:1px solid rgba(128,128,128,0.4)">'
        '<td style="padding:3px 6px">Total</td>'
        f'<td style="text-align:right;padding:3px 6px">{cb.get("total_accepted", 0):,}</td>'
        f'<td style="text-align:right;padding:3px 6px">{cb.get("total_generated", 0):,}</td>'
        f'<td style="text-align:right;padding:3px 6px">{cb.get("total_disagreed", 0):,}</td>'
        f'<td style="text-align:right;padding:3px 6px">{cb.get("total_rate", 0):.1f}%</td></tr>'
        "</tbody></table>"
    )


def _fix_list_html(grouped: dict, categories: list[str]) -> str:
    """A single prioritized punch-list of every FAIL across the given
    categories, meant to sit right under the FAIL/PASS metrics so what's
    broken is visible in one place instead of only discoverable by opening
    each category's own table - the Streamlit-rendered twin of
    report_render.py's own _fix_list_html."""
    import html as _html
    fails = [(category, r) for category in categories for r in grouped[category]
             if r.status == Status.FAIL]
    if not fails:
        return ""
    rows = []
    for category, r in fails:
        lead, _examples = split_detail(r.detail)
        scope_bit = f' <span style="opacity:0.7">({_html.escape(r.scope)})</span>' if r.scope else ""
        do_bit = _html.escape(_truncate(r.fix, 140)) if r.fix else ""
        rows.append(
            '<div style="display:grid;grid-template-columns:110px minmax(0,1.6fr) minmax(0,1fr);'
            'gap:10px;padding:4px 0;border-top:1px solid rgba(128,128,128,0.25);font-size:0.78rem;'
            'align-items:baseline;">'
            '<span style="opacity:0.65;font-size:0.7rem;text-transform:uppercase;letter-spacing:0.03em;">'
            f'{_html.escape(_display_category(category))}</span>'
            f'<span>{_html.escape(r.title)}{scope_bit} &mdash; {_html.escape(_truncate(lead, 140))}</span>'
            f'<span style="color:{ACCENT}">{do_bit}</span>'
            "</div>"
        )
    return (
        f'<div style="font-weight:700;color:{ACCENT};font-size:0.95rem;margin-bottom:4px;">'
        f"Fix these {len(fails)}</div>"
        '<div style="max-height:260px;overflow-y:auto;">' + "".join(rows) + "</div>"
    )


def _gates_dot_grid_html(grouped: dict, categories: list[str]) -> str:
    """Compact strip of colored dots, one per checklist category group
    (green = all passed, red = at least one FAIL) - a faster visual scan
    than reading every row of the glance table below it."""
    if not categories:
        return ""
    import html as _html

    def _cat_fail(category: str) -> int:
        return sum(1 for r in grouped[category] if r.status == Status.FAIL)

    n_fail_groups = sum(1 for c in categories if _cat_fail(c))
    dots = "".join(
        '<span style="width:11px;height:11px;border-radius:3px;display:inline-block;'
        f'background:{RED if _cat_fail(c) else GREEN}" '
        f'title="{_html.escape(_display_category(c))}: {"FAIL" if _cat_fail(c) else "PASS"}"></span>'
        for c in categories
    )
    return (
        '<div style="display:flex;align-items:center;gap:10px;margin:0 0 0.6rem;flex-wrap:wrap;">'
        f'<span style="font-size:0.78rem;opacity:0.7;white-space:nowrap;">'
        f"{len(categories) - n_fail_groups}/{len(categories)} check group(s) passing</span>"
        f'<div style="display:flex;flex-wrap:wrap;gap:4px;">{dots}</div></div>'
    )


STATUS_ICON = {
    Status.PASS.value: "✅",
    Status.FAIL.value: "❌",
    Status.WARN.value: "⚠️",
    Status.INFO.value: "ℹ️",
}
STATUS_ORDER = [Status.FAIL.value, Status.WARN.value, Status.INFO.value, Status.PASS.value]

GREEN = "#1a7f37"
RED = "#cf222e"
TRACK = "#e5e7eb"
ACCENT = "#1f3a5f"
CATEGORY_PALETTE = ["#1f3a5f", "#2d5a8b", "#3978b8", "#5b9bd5", "#7fb3e0",
                    "#21867a", "#4ca39a", "#7ec9bd"]
OTHER_COLOR = "#c7ced8"


def _pct_str(pct: float | None) -> str:
    return f"{pct:.1f}%" if pct is not None else "n/a"


def _split_bar_html(pct: float | None) -> str:
    """Same green/red split-bar visual as the HTML/PDF export - green fill
    for the detected share, red for whatever's missing, so a gap is
    immediately visible as a color, not just a number to read."""
    if pct is None:
        return (f'<div style="flex:1 1 auto;height:9px;border-radius:5px;background:{TRACK}">'
                 f"</div>")
    color = GREEN if pct >= 99.999 else RED
    return (f'<div style="flex:1 1 auto;height:9px;border-radius:5px;background:{TRACK};overflow:hidden">'
            f'<div style="height:100%;border-radius:5px;width:{pct:.1f}%;background:{color}"></div></div>')


def _category_donut_html(top: list, other_count: int, total: int, size: int = 130) -> str:
    """A donut for a small, bounded category set (file format) - distinct on
    purpose from the long-tail bar lists used for business-context
    dimensions elsewhere in this same section: one answers "what share of
    the whole", the other "what are the most common values among many"."""
    stops, cursor = [], 0.0
    for i, (_value, count, _pct) in enumerate(top):
        color = CATEGORY_PALETTE[i % len(CATEGORY_PALETTE)]
        share = count / total * 100.0
        stops.append(f"{color} {cursor:.2f}% {cursor + share:.2f}%")
        cursor += share
    if other_count:
        share = other_count / total * 100.0
        stops.append(f"{OTHER_COLOR} {cursor:.2f}% {cursor + share:.2f}%")
    gradient = ", ".join(stops) if stops else f"{TRACK} 0% 100%"
    return (f'<div style="width:{size}px;height:{size}px;border-radius:50%;'
            f'background:conic-gradient({gradient});display:flex;align-items:center;'
            f'justify-content:center;flex:0 0 auto;"><div style="width:68%;height:68%;'
            f'border-radius:50%;background:#ffffff;display:flex;flex-direction:column;'
            f'align-items:center;justify-content:center;text-align:center;">'
            f'<div style="font-size:1.05rem;font-weight:700;color:{ACCENT};">{total}</div>'
            f'<div style="font-size:0.7rem;color:#6b7280;">documents</div></div></div>')


def _category_legend_html(top: list, other_count: int, total: int) -> str:
    items = []
    for i, (value, count, pct) in enumerate(top):
        color = CATEGORY_PALETTE[i % len(CATEGORY_PALETTE)]
        items.append(
            f'<li style="margin:3px 0;"><span style="display:inline-block;width:10px;height:10px;'
            f'margin-right:6px;border-radius:2px;vertical-align:middle;background:{color};"></span>'
            f"{value}: {count} ({pct:.1f}%)</li>")
    if other_count:
        other_pct = other_count / total * 100.0
        items.append(
            f'<li style="margin:3px 0;"><span style="display:inline-block;width:10px;height:10px;'
            f'margin-right:6px;border-radius:2px;vertical-align:middle;background:{OTHER_COLOR};"></span>'
            f"Other: {other_count} ({other_pct:.1f}%)</li>")
    return f'<ul style="list-style:none;margin:0;padding:0;font-size:0.8rem;">{"".join(items)}</ul>'


def _donut_html(pct: float | None, size: int = 84) -> str:
    if pct is None:
        return (f'<div style="width:{size}px;height:{size}px;border-radius:50%;'
                 f'background:conic-gradient({TRACK} 0% 100%);display:flex;align-items:center;'
                 f'justify-content:center;flex:0 0 auto;"><div style="width:68%;height:68%;'
                 f'border-radius:50%;background:#ffffff;'
                 f'display:flex;align-items:center;justify-content:center;font-size:0.8rem;'
                 f'font-weight:700;color:#1e2530;">n/a</div></div>')
    color = GREEN if pct >= 99.999 else RED
    return (f'<div style="width:{size}px;height:{size}px;border-radius:50%;'
            f'background:conic-gradient({GREEN} 0% {pct:.1f}%, {RED} {pct:.1f}% 100%);'
            f'display:flex;align-items:center;justify-content:center;flex:0 0 auto;">'
            f'<div style="width:68%;height:68%;border-radius:50%;background:#ffffff;'
            f'display:flex;align-items:center;justify-content:center;font-size:0.8rem;font-weight:700;'
            f'color:{color};">{pct:.1f}%</div></div>')


def category_sort_key(category: str):
    m = re.match(r"^(\d+)", category)
    return (int(m.group(1)) if m else 999, category)


def worst_status(results) -> tuple[str, dict]:
    cat_counts = {s: sum(1 for r in results if r.status.value == s) for s in STATUS_ORDER}
    return next((s for s in STATUS_ORDER if cat_counts[s]), Status.PASS.value), cat_counts


_LEADING_NUMBER_RE = re.compile(r"^\d+[a-z]?(?:-\d+)?\.\s*")


def _display_category(category: str) -> str:
    """User-facing category name with any leading checklist-item number
    stripped ("3. context_output_normalized" -> "context_output_normalized")
    - now that every category is a clickable jump target, the number no
    longer carries any navigational meaning and just reads as noise.
    Sorting/anchors/grouping still use the raw category string."""
    return _LEADING_NUMBER_RE.sub("", category)


def _effective_worst_and_passed(cat_counts: dict) -> tuple[str, int]:
    """INFO-only results read as noise next to real problems, so they're
    folded into "passed" everywhere they're summarized: a category (or a
    whole run) only ever shows as FAIL/WARN/PASS, never as its own
    separate INFO state - real FAIL/WARN keep their own distinct counts."""
    if cat_counts[Status.FAIL.value]:
        worst = Status.FAIL.value
    elif cat_counts[Status.WARN.value]:
        worst = Status.WARN.value
    else:
        worst = Status.PASS.value
    passed = cat_counts[Status.PASS.value] + cat_counts[Status.INFO.value]
    return worst, passed


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
    _inject_metric_css()
    counts = report.counts()
    report_anchor = _slugify("run", report.version_dir)
    _anchor(report_anchor)
    st.subheader(report.label)
    st.caption(str(report.version_dir))

    if report.discovery_note:
        st.caption(f"📁 {report.discovery_note}")

    info = report.generation_info or {}
    info_bits = []
    if info.get("generator_model"):
        version_bit = f" ({info['generator_model_version']})" if info.get("generator_model_version") else ""
        info_bits.append(f"**Model:** {info['generator_model']}{version_bit}")
    if info.get("language_code"):
        info_bits.append(f"**Language:** {info['language_code'].upper()} - "
                          f"{info.get('language_name', info['language_code'])}")
    if info.get("sit_grader_model"):
        info_bits.append(f"**SIT Grader:** {info['sit_grader_model']}")
    if info.get("docparser_version"):
        info_bits.append(f"**DocParser:** {info['docparser_version']}")
    if info.get("mce_version"):
        info_bits.append(f"**MCE:** {info['mce_version']}")
    if info_bits:
        st.caption(" &nbsp;·&nbsp; ".join(info_bits), unsafe_allow_html=True)

    dc = report.doc_counts or {}
    if dc:
        ag, da = dc.get("agreements", {}), dc.get("disagreements", {})
        hi_cols = st.columns(4)
        hi_cols[0].metric(
            "Total documents (all)",
            dc.get("combined_total") if dc.get("combined_total") is not None else "—",
        )
        hi_cols[1].metric(
            "Total documents (Agreements)",
            dc.get("total") if dc.get("total") is not None else "—",
        )
        hi_cols[2].metric("Agreements", f"{ag.get('positive', '—')} pos / {ag.get('negative', '—')} neg")
        hi_cols[3].metric("Disagreements", f"{da.get('positive', '—')} pos / {da.get('negative', '—')} neg")

    stats = report.stats or {}
    mce = stats.get("mce_coverage") or {}
    dist = stats.get("label_distribution") or {}
    doc_length = stats.get("document_length") or {}
    if mce.get("checked") or dist.get("total") or doc_length.get("total"):
        stat_cols = st.columns(3)
        with stat_cols[0]:
            if mce.get("checked"):
                st.markdown("**MCE detection coverage**")
                pos, neg = mce.get("positive", {}), mce.get("negative", {})
                pos_pct, neg_pct = pos.get("pct"), neg.get("pct")
                st.markdown(
                    '<div style="display:flex;align-items:center;gap:16px;">'
                    + _donut_html(mce.get("pct"))
                    + '<div style="flex:1 1 auto;min-width:0;">'
                    + '<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">'
                    + '<span style="width:56px;flex:0 0 auto;font-size:0.8rem;color:#6b7280;">Positive</span>'
                    + _split_bar_html(pos_pct)
                    + f'<span style="width:140px;flex:0 0 auto;font-size:0.8rem;text-align:right;">'
                      f'{_pct_str(pos_pct)} ({pos.get("detected", 0)}/{pos.get("checked", 0)})</span></div>'
                    + '<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">'
                    + '<span style="width:56px;flex:0 0 auto;font-size:0.8rem;color:#6b7280;">Negative</span>'
                    + _split_bar_html(neg_pct)
                    + f'<span style="width:140px;flex:0 0 auto;font-size:0.8rem;text-align:right;">'
                      f'{_pct_str(neg_pct)} ({neg.get("detected", 0)}/{neg.get("checked", 0)})</span></div>'
                    + f'<div style="font-size:0.78rem;color:#6b7280;margin-top:2px;">'
                      f'{mce.get("detected", 0)}/{mce.get("checked", 0)} total documents detected</div>'
                      "</div></div>",
                    unsafe_allow_html=True,
                )
        with stat_cols[1]:
            if doc_length.get("total"):
                st.markdown("**Document Length**")
                st.caption("Character count distribution across the corpus")
                histogram = doc_length.get("histogram") or []
                labels = [f"{s:,}-{e:,}" for s, e, _c in histogram]
                counts_list = [c for _s, _e, c in histogram]
                overflow = doc_length.get("overflow", 0)
                if overflow:
                    labels.append(f"{doc_length.get('overflow_from', '?'):,}+")
                    counts_list.append(overflow)
                length_df = pd.DataFrame({"count": counts_list}, index=labels)
                st.bar_chart(length_df, height=160)
                median = doc_length.get("median", 0)
                median_str = f"{median:,.0f}" if isinstance(median, float) else f"{median:,}"
                mini_cols = st.columns(3)
                mini_cols[0].metric("Min", f"{doc_length.get('min', 0):,}")
                mini_cols[1].metric("Median", median_str)
                mini_cols[2].metric("Max", f"{doc_length.get('max', 0):,}")
        with stat_cols[2]:
            if dist.get("total"):
                st.markdown("**Easy / Hard &times; Positive / Negative**")
                dist_df = pd.DataFrame(
                    {"count": [dist.get("easy positive", 0), dist.get("hard positive", 0),
                               dist.get("easy negative", 0), dist.get("hard negative", 0)]},
                    index=["Easy positive", "Hard positive", "Easy negative", "Hard negative"],
                )
                st.bar_chart(dist_df, height=180)
                dist_total = dist.get("total", 0) or 1
                pct_bits = " &nbsp;·&nbsp; ".join(
                    f"{name}: {count} ({count / dist_total * 100:.1f}%)"
                    for name, count in zip(dist_df.index, dist_df["count"])
                )
                st.caption(pct_bits, unsafe_allow_html=True)
                unknown = dist.get("unknown", 0)
                st.caption(f"total: {dist.get('total', 0)}" + (f"  ·  unknown: {unknown}" if unknown else ""))

    cb = stats.get("corpus_breakdown") or {}
    if cb.get("total_generated"):
        st.markdown("**Corpus Breakdown**")
        st.caption("Accepted vs Generated vs Disagreed, per difficulty/polarity bucket")
        st.markdown(_corpus_breakdown_html(cb), unsafe_allow_html=True)

    quality_metrics_html = _quality_metrics_section_html(report.quality_metrics)
    if quality_metrics_html:
        st.markdown(quality_metrics_html, unsafe_allow_html=True)

    composition = stats.get("composition") or {}
    if composition:
        with st.expander("📊 Document composition - most common counts", expanded=False):
            comp_titles = {
                "format": "File format", "domain": "Domain", "department": "Department",
                "function": "Function", "workflow": "Workflow", "process": "Process",
                "persona": "Persona", "role": "Role", "document_type": "Document type",
            }
            comp_cols = st.columns(2)
            i = 0
            for key, title in comp_titles.items():
                data = composition.get(key)
                if not data or not data.get("top"):
                    continue
                all_values = data.get("all", data["top"])
                with comp_cols[i % 2]:
                    st.markdown(f"**{title}**")
                    if key == "format":
                        top = data["top"]
                        other_count = data["total"] - sum(c for _v, c, _p in top)
                        st.markdown(
                            '<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;">'
                            + _category_donut_html(top, other_count, data["total"])
                            + _category_legend_html(top, other_count, data["total"])
                            + "</div>",
                            unsafe_allow_html=True,
                        )
                    else:
                        bar_rows = "".join(
                            f'<div style="display:flex;align-items:center;gap:7px;margin:3px 0;">'
                            f'<span style="width:42%;flex:0 0 auto;font-size:0.78rem;color:#6b7280;'
                            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" '
                            f'title="{v}">{v}</span>'
                            f'<div style="flex:1 1 auto;height:9px;border-radius:5px;background:{TRACK};'
                            f'overflow:hidden;"><div style="height:100%;border-radius:5px;width:{p:.1f}%;'
                            f'background:{ACCENT};"></div></div>'
                            f'<span style="width:90px;flex:0 0 auto;font-size:0.78rem;text-align:right;">'
                            f"{c} ({p:.1f}%)</span></div>"
                            for v, c, p in data["top"]
                        )
                        st.markdown(bar_rows, unsafe_allow_html=True)
                    # Full scrollable list (every distinct value, not just the
                    # top ones shown above) instead of a dead-end "+N more".
                    remaining = data["distinct"] - len(data["top"])
                    if remaining > 0:
                        with st.expander(f"Show all {data['distinct']} value(s)"):
                            all_df = pd.DataFrame(
                                [{"value": v, "count": c, "pct": f"{p:.1f}%"} for v, c, p in all_values]
                            ).set_index("value")
                            st.dataframe(all_df, width='stretch', hide_index=False, height=250)
                i += 1

    grouped = report.by_category()
    ordered_categories = sorted(grouped, key=category_sort_key)
    core_categories = [c for c in ordered_categories if c not in ADDITIONAL_CHECK_CATEGORIES]
    additional_categories = [c for c in ordered_categories if c in ADDITIONAL_CHECK_CATEGORIES]
    cat_anchor = {category: _slugify("run", report.version_dir, "cat", category)
                  for category in ordered_categories}
    cat_effective = {category: _effective_worst_and_passed(worst_status(grouped[category])[1])
                      for category in ordered_categories}

    n_fail, n_warn = counts.get("FAIL", 0), counts.get("WARN", 0)
    gated_critical = _gated_critical_metrics(report.quality_metrics)
    if n_fail or gated_critical:
        extra = (f" plus {len(gated_critical)} quality metric(s) below gate threshold "
                 f"({', '.join(gated_critical)})") if gated_critical else ""
        st.error(f"❌ {n_fail} check(s) failed{extra} — needs fixes before this output ships. "
                 f"See the checklist below for exactly which ones.")
        failed_cats = [c for c in ordered_categories if cat_effective[c][0] == Status.FAIL.value]
        links = " &nbsp;·&nbsp; ".join(
            f'<a href="#{cat_anchor[c]}">❌ {_display_category(c)}</a>' for c in failed_cats)
        if links:
            st.markdown(f"**Jump to failed check(s):** {links}", unsafe_allow_html=True)
    elif n_warn:
        st.warning(f"⚠️ All mandatory checks passed, but {n_warn} item(s) need a human look "
                   f"(warnings) — see below.")
        warn_cats = [c for c in ordered_categories if cat_effective[c][0] == Status.WARN.value]
        links = " &nbsp;·&nbsp; ".join(
            f'<a href="#{cat_anchor[c]}">⚠️ {_display_category(c)}</a>' for c in warn_cats)
        st.markdown(f"**Jump to warning(s):** {links}", unsafe_allow_html=True)
    else:
        st.success("✅ Every quality check passed for this run.")

    cols = st.columns(2)
    cols[0].metric(f"{STATUS_ICON[Status.FAIL.value]} FAIL", n_fail)
    cols[1].metric(f"{STATUS_ICON[Status.PASS.value]} PASS",
                    counts.get("PASS", 0) + counts.get("INFO", 0))

    all_categories = core_categories + additional_categories
    fix_list_html = _fix_list_html(grouped, all_categories)
    if fix_list_html:
        st.markdown(fix_list_html, unsafe_allow_html=True)
    gates_html = _gates_dot_grid_html(grouped, all_categories)
    if gates_html:
        st.markdown(gates_html, unsafe_allow_html=True)

    def _glance_table_html(categories: list[str]) -> str:
        table_rows = []
        for category in categories:
            results = grouped[category]
            _, cat_counts = worst_status(results)
            worst, eff_passed = cat_effective[category]
            bits = []
            if cat_counts[Status.FAIL.value]:
                bits.append(f"{cat_counts[Status.FAIL.value]} FAILED")
            if cat_counts[Status.WARN.value]:
                bits.append(f"{cat_counts[Status.WARN.value]} warning(s)")
            if not bits:
                bits = ["All checks passed"]
            table_rows.append(
                f'<tr><td style="text-align:center">{STATUS_ICON[worst]}</td>'
                f'<td><a href="#{cat_anchor[category]}">{_display_category(category)}</a></td>'
                f'<td>{", ".join(bits)}</td></tr>')
        return (
            '<table style="width:100%; border-collapse: collapse;">'
            '<thead><tr>'
            '<th style="text-align:center; padding:4px 8px; border-bottom:1px solid rgba(128,128,128,0.4)"></th>'
            '<th style="text-align:left; padding:4px 8px; border-bottom:1px solid rgba(128,128,128,0.4)">Checklist item</th>'
            '<th style="text-align:left; padding:4px 8px; border-bottom:1px solid rgba(128,128,128,0.4)">Result</th>'
            '</tr></thead><tbody>'
            + "".join(table_rows)
            + "</tbody></table>"
        )

    st.markdown("#### Checklist at a glance")
    st.caption("Click a checklist item to jump straight to it in the Details section below.")
    st.markdown(_glance_table_html(core_categories), unsafe_allow_html=True)
    if additional_categories:
        st.markdown(f"##### {ADDITIONAL_CHECKS_SECTION_TITLE}")
        st.markdown(_glance_table_html(additional_categories), unsafe_allow_html=True)

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

    def _render_category_details(category: str) -> None:
        results = grouped[category]
        _, cat_counts = worst_status(results)
        worst, eff_passed = cat_effective[category]
        badge_bits = []
        if cat_counts[Status.FAIL.value]:
            badge_bits.append(f"{STATUS_ICON[Status.FAIL.value]}{cat_counts[Status.FAIL.value]}")
        if cat_counts[Status.WARN.value]:
            badge_bits.append(f"{STATUS_ICON[Status.WARN.value]}{cat_counts[Status.WARN.value]}")
        if eff_passed:
            badge_bits.append(f"{STATUS_ICON[Status.PASS.value]}{eff_passed}")
        badge = " ".join(badge_bits)
        needs_attention = worst in (Status.FAIL.value, Status.WARN.value)
        _anchor(cat_anchor[category])
        with st.expander(f"{STATUS_ICON[worst]} {_display_category(category)}  —  {badge}",
                          expanded=needs_attention):
            # Every result is one compact table row by default - a short
            # one-line summary, status-colored - with the full detail/
            # examples/fix behind a native <details> inside that same cell.
            # Rendered as ONE raw-HTML block (like the HTML/PDF export's own
            # result-table) rather than one Streamlit widget (columns +
            # popover) per result: a real report can carry several hundred
            # results, and a widget-per-row version measured 2-3x slower to
            # render than this - <details> costs nothing until a viewer
            # actually opens it, since it's just inert HTML to the browser.
            st.markdown(_result_table_html(results), unsafe_allow_html=True)

    st.markdown("#### Details (categories with a failure or warning are expanded automatically)")
    for category in core_categories:
        _render_category_details(category)
    if additional_categories:
        st.markdown(f"#### {ADDITIONAL_CHECKS_SECTION_TITLE}")
        for category in additional_categories:
            _render_category_details(category)


def render_run_all_summary_table(reports) -> None:
    """The 'Summary across all runs' table shown when multiple runs are found."""
    st.subheader("Summary across all runs")
    st.caption("Click a run to jump straight to its full report below.")
    header_cells = "".join(
        f'<th style="text-align:left; padding:4px 8px; border-bottom:1px solid rgba(128,128,128,0.4)">{h}</th>'
        for h in ["Verdict", "Run", f"{STATUS_ICON[Status.FAIL.value]} FAIL", f"{STATUS_ICON[Status.PASS.value]} PASS"])
    body_rows = []
    for rep in reports:
        c = rep.counts()
        verdict = ("❌ Needs fixes" if c.get("FAIL")
                   else ("⚠️ Review warnings" if c.get("WARN") else "✅ Clean"))
        anchor = _slugify("run", rep.version_dir)
        count_cells = f"<td>{c.get('FAIL', 0)}</td><td>{c.get('PASS', 0) + c.get('INFO', 0)}</td>"
        body_rows.append(
            f"<tr><td>{verdict}</td>"
            f'<td><a href="#{anchor}">{rep.label}</a></td>'
            f"{count_cells}</tr>")
    st.markdown(
        f'<table style="width:100%; border-collapse: collapse;">'
        f"<thead><tr>{header_cells}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>",
        unsafe_allow_html=True,
    )
