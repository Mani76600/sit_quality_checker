"""Render a RunReport as a shareable, self-contained HTML page or a PDF -
alongside the existing raw JSON export, for reviewing/circulating results
outside the Streamlit app itself.

Design: only two status colors anywhere in the page - green for PASS,
red for FAIL (WARN/INFO are folded into PASS, so there's nothing else to
color). Neutral navy/gray is used only for structural chrome (headings,
borders), never to mean "status". The checklist table drops raw pass
counts ("34/34 passed" reads as noise once color already answers "did this
pass") in favor of a single plain-language line. Headline numbers lead with
real visualizations instead of plain text: a donut for overall MCE
detection coverage, split green/red bars per polarity, and a simple bar
chart for the easy/hard x positive/negative distribution - the same
reasoning behind the stat cards (doc counts / MCE coverage / label
distribution) shown at the top of the Streamlit report, now also in the
static HTML/PDF export, which previously didn't carry them at all.
"""

from __future__ import annotations

import html
import re

from qc.check_groups import ADDITIONAL_CHECK_CATEGORIES, ADDITIONAL_CHECKS_SECTION_TITLE
from qc.detail_format import split_detail
from qc.models import RunReport, Status

GREEN = "#1a7f37"
RED = "#cf222e"
ACCENT = "#1f3a5f"  # structural chrome only (headings) - never a status color
INK = "#1e2530"
MUTED = "#6b7280"
BORDER = "#e5e7eb"
PANEL = "#f6f7fb"

# A handful of checks (e.g. content_verification.py's value-in-context
# examples) build a detail string by joining several real document excerpts
# together, which can run to several KB - fine for the raw JSON export
# (nothing is thrown away there), but unreadable dumped whole into an HTML
# table cell or a PDF paragraph. Truncate only in these two human-facing
# renderers; the number is generous enough to keep real diagnostic context
# (a full example or two) while capping the pathological cases.
MAX_DETAIL_CHARS = 320


def _truncate(text: str, limit: int = MAX_DETAIL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"... (truncated, {len(text):,} chars total)"


def _category_sort_key(category: str):
    m = re.match(r"^(\d+)", category)
    return (int(m.group(1)) if m else 999, category)


_LEADING_NUMBER_RE = re.compile(r"^\d+[a-z]?(?:-\d+)?\.\s*")


def _display_category(category: str) -> str:
    """User-facing category name with any leading checklist-item number
    stripped ("3. context_output_normalized" -> "context_output_normalized") -
    same rationale and same pattern as streamlit_report.py's own
    _display_category: the number no longer carries navigational meaning
    (there's no on-screen jump target in a static HTML/PDF export either)
    and just reads as noise, especially next to the newer unnumbered
    "... (addition)" categories. Sorting/grouping still use the raw
    category string via _category_sort_key above."""
    return _LEADING_NUMBER_RE.sub("", category)


def _grouped(report: RunReport):
    grouped = report.by_category()
    return sorted(grouped.items(), key=lambda kv: _category_sort_key(kv[0]))


def _core_and_additional(report: RunReport):
    """Split the grouped categories into (core, additional) - "additional"
    being whole new categories added in this round of work (see
    qc.check_groups), kept in their own visually separate section with no
    explanation of where they came from in the UI text itself."""
    core, additional = [], []
    for category, results in _grouped(report):
        (additional if category in ADDITIONAL_CHECK_CATEGORIES else core).append((category, results))
    return core, additional


def _fail_count(results) -> int:
    return sum(1 for r in results if r.status == Status.FAIL)


def _pct_str(pct: float | None) -> str:
    return f"{pct:.1f}%" if pct is not None else "n/a"


def _gated_critical_metrics(quality_metrics: dict) -> list[str]:
    """Quality metrics (see qc.quality_metrics) are a different kind of
    result from the PASS/FAIL checklist - graded Strong/Acceptable/Weak/
    Critical against a threshold ladder, not binary. Only the ones marked
    "gate" in their own definition (Position/Keyword Proximity/Negative
    Label diversity - Template Cluster Rate is report-only and Language
    Purity is advisory-only) flip the overall verdict to FAILING when they
    land in Critical."""
    return [m["label"] for m in (quality_metrics or {}).values()
            if m.get("gate") and m.get("grade") == "critical"]


# ------------------------------------------------------------ HTML charts --

def _donut_svg(pct: float | None, size: int = 92, stroke: int = 12) -> str:
    """A ring split green (detected share) / red (missing share) via
    conic-gradient - CSS-only, no JS/images, so it survives as a plain
    double-clickable HTML file. pct is None (nothing checked) -> a flat
    gray ring with no label."""
    if pct is None:
        return (f'<div class="donut" style="background: conic-gradient({BORDER} 0% 100%); '
                 f'width:{size}px;height:{size}px;">'
                 f'<div class="donut-hole">n/a</div></div>')
    color = GREEN if pct >= 99.999 else RED
    return (f'<div class="donut" style="background: conic-gradient('
            f'{GREEN} 0% {pct:.1f}%, {RED} {pct:.1f}% 100%); '
            f'width:{size}px;height:{size}px;">'
            f'<div class="donut-hole" style="color:{color}">{pct:.1f}%</div></div>')


def _split_bar_html(pct: float | None) -> str:
    """A thin horizontal track, green fill for the detected share, the
    remainder left as the plain track background (no red fill at 100%,
    since there's nothing to call out)."""
    if pct is None:
        return f'<div class="bar-track"><div class="bar-fill" style="width:0%;background:{BORDER}"></div></div>'
    color = GREEN if pct >= 99.999 else RED
    return f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:{color}"></div></div>'


def _mce_stat_html(mce: dict) -> str:
    if not mce or not mce.get("checked"):
        return ""
    pos, neg = mce.get("positive", {}), mce.get("negative", {})
    return f"""
<div class="stat-card stat-card-wide">
  <div class="stat-label">MCE detection coverage</div>
  <div class="mce-layout">
    {_donut_svg(mce.get("pct"))}
    <div class="mce-bars">
      <div class="mce-bar-row">
        <span class="mce-bar-label">Positive</span>
        {_split_bar_html(pos.get("pct"))}
        <span class="mce-bar-value">{_pct_str(pos.get("pct"))} ({pos.get("detected", 0)}/{pos.get("checked", 0)})</span>
      </div>
      <div class="mce-bar-row">
        <span class="mce-bar-label">Negative</span>
        {_split_bar_html(neg.get("pct"))}
        <span class="mce-bar-value">{_pct_str(neg.get("pct"))} ({neg.get("detected", 0)}/{neg.get("checked", 0)})</span>
      </div>
      <div class="stat-secondary">{mce.get("detected", 0)} / {mce.get("checked", 0)} total documents detected</div>
    </div>
  </div>
</div>"""


def _label_distribution_stat_html(dist: dict) -> str:
    if not dist or not dist.get("total"):
        return ""
    labels = [
        ("Easy positive", dist.get("easy positive", 0)),
        ("Hard positive", dist.get("hard positive", 0)),
        ("Easy negative", dist.get("easy negative", 0)),
        ("Hard negative", dist.get("hard negative", 0)),
    ]
    dist_total = dist.get("total", 0) or 1
    max_count = max((c for _, c in labels), default=0) or 1
    rows = "".join(
        f'<div class="dist-row"><span class="dist-name">{name}</span>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{c / max_count * 100:.1f}%;'
        f'background:{ACCENT}"></div></div>'
        f'<span class="dist-value">{c} ({c / dist_total * 100:.1f}%)</span></div>'
        for name, c in labels
    )
    unknown = dist.get("unknown", 0)
    footer = f'<div class="stat-secondary">unknown: {unknown}</div>' if unknown else ""
    return f"""
<div class="stat-card stat-card-wide">
  <div class="stat-label">Easy / Hard &times; Positive / Negative</div>
  <div class="dist-grid">{rows}</div>
  <div class="stat-secondary">total: {dist.get("total", 0)}</div>{footer}
</div>"""


def _document_length_stat_html(dl: dict) -> str:
    if not dl or not dl.get("total"):
        return ""
    histogram = dl.get("histogram") or []
    max_count = max((c for _s, _e, c in histogram), default=0) or 1
    bars = "".join(
        f'<div class="doclen-bar" style="height:{c / max_count * 100:.1f}%" '
        f'title="{s}-{e}: {c} document(s)"></div>'
        for s, e, c in histogram
    )
    overflow = dl.get("overflow", 0)
    if overflow:
        bars += (f'<div class="doclen-bar doclen-bar-overflow" style="height:100%" '
                  f'title="{dl.get("overflow_from", "?")}+: {overflow} document(s)"></div>')
    axis_min = histogram[0][0] if histogram else dl.get("min", 0)
    axis_max = histogram[-1][1] if histogram else dl.get("max", 0)
    median = dl.get("median", 0)
    median_str = f"{median:,.0f}" if isinstance(median, float) else f"{median:,}"
    return f"""
<div class="stat-card stat-card-wide">
  <div class="stat-label">Document Length</div>
  <div class="stat-secondary">Character count distribution across the corpus</div>
  <div class="doclen-layout">
    <div class="doclen-chart">
      <div class="doclen-bars">{bars}</div>
      <div class="doclen-axis"><span>{axis_min:,}</span><span>{axis_max:,}{"+" if overflow else ""}</span></div>
    </div>
    <div class="doclen-stats">
      <div class="mini-stat"><div class="stat-secondary">Min</div><div class="stat-primary">{dl.get("min", 0):,}</div></div>
      <div class="mini-stat"><div class="stat-secondary">Median</div><div class="stat-primary">{median_str}</div></div>
      <div class="mini-stat"><div class="stat-secondary">Max</div><div class="stat-primary">{dl.get("max", 0):,}</div></div>
    </div>
  </div>
</div>"""


def _generation_info_html(info: dict) -> str:
    if not info:
        return ""
    chips = []
    generator = info.get("generator_model")
    if generator:
        version_bit = f" ({info['generator_model_version']})" if info.get("generator_model_version") else ""
        chips.append(("Model", f"{generator}{version_bit}"))
    if info.get("language_code"):
        chips.append(("Language", f"{info['language_code'].upper()} - {info.get('language_name', info['language_code'])}"))
    if info.get("sit_grader_model"):
        chips.append(("SIT Grader", info["sit_grader_model"]))
    if info.get("docparser_version"):
        chips.append(("DocParser", info["docparser_version"]))
    if info.get("mce_version"):
        chips.append(("MCE", info["mce_version"]))
    if not chips:
        return ""
    cells = "".join(
        f'<div class="info-chip"><span class="info-chip-label">{html.escape(label)}</span>'
        f'<span class="info-chip-value">{html.escape(str(value))}</span></div>'
        for label, value in chips
    )
    return f'<div class="info-strip">{cells}</div>'


_COMPOSITION_TITLES = {
    "format": "File format", "domain": "Domain", "department": "Department",
    "function": "Function", "workflow": "Workflow", "process": "Process",
    "persona": "Persona", "role": "Role", "document_type": "Document type",
}
# File format is a structural/technical attribute with a small, bounded set
# of categories - a donut (share-of-whole) fits it better than the long-tail
# bar list used for the business-context dimensions below, which routinely
# run into dozens/hundreds of distinct values. Kept visually distinct on
# purpose, not just for variety: these are genuinely different kinds of
# data (one is "what proportion", the other is "what are the most common
# values among many").
_DONUT_CATEGORY_KEY = "format"
_CATEGORY_PALETTE = ["#1f3a5f", "#2d5a8b", "#3978b8", "#5b9bd5", "#7fb3e0",
                     "#21867a", "#4ca39a", "#7ec9bd"]
_OTHER_COLOR = "#c7ced8"


def _show_all_table_html(all_values: list, visible_count: int) -> str:
    """A real expandable list of every distinct value (not just the top N
    shown inline) - replaces a dead-end "+N more distinct value(s)" label
    with something the user can actually open and read."""
    remaining = all_values[visible_count:]
    if not remaining:
        return ""
    rows = "".join(
        f"<tr><td>{html.escape(str(value))}</td><td>{count}</td><td>{pct:.1f}%</td></tr>"
        for value, count, pct in remaining
    )
    return (
        f'<details class="show-all"><summary>Show all {len(all_values)} value(s) '
        f'(+{len(remaining)} more)</summary>'
        '<table class="show-all-table"><tr><th>Value</th><th>Count</th><th>%</th></tr>'
        f"{rows}</table></details>"
    )


def _category_donut_svg(top: list, other_count: int, total: int, size: int = 150) -> str:
    stops = []
    cursor = 0.0
    for i, (_value, count, _pct) in enumerate(top):
        color = _CATEGORY_PALETTE[i % len(_CATEGORY_PALETTE)]
        share = count / total * 100.0
        stops.append(f"{color} {cursor:.2f}% {cursor + share:.2f}%")
        cursor += share
    if other_count:
        share = other_count / total * 100.0
        stops.append(f"{_OTHER_COLOR} {cursor:.2f}% {cursor + share:.2f}%")
        cursor += share
    gradient = ", ".join(stops) if stops else f"{BORDER} 0% 100%"
    return (f'<div class="donut" style="background: conic-gradient({gradient}); '
            f'width:{size}px;height:{size}px;">'
            f'<div class="donut-hole"><div class="stat-primary" style="font-size:1.1rem">'
            f"{total}</div><div class=\"stat-secondary\">documents</div></div></div>")


def _format_legend_html(top: list, other_count: int, total: int) -> str:
    items = []
    for i, (value, count, pct) in enumerate(top):
        color = _CATEGORY_PALETTE[i % len(_CATEGORY_PALETTE)]
        items.append(
            f'<li><span class="legend-swatch" style="background:{color}"></span>'
            f"{html.escape(str(value))}: {count} ({pct:.1f}%)</li>")
    if other_count:
        other_pct = other_count / total * 100.0
        items.append(
            f'<li><span class="legend-swatch" style="background:{_OTHER_COLOR}"></span>'
            f"Other: {other_count} ({other_pct:.1f}%)</li>")
    return f'<ul class="comp-legend">{"".join(items)}</ul>'


def _composition_section_html(composition: dict) -> str:
    if not composition:
        return ""
    cards = []
    for key, title in _COMPOSITION_TITLES.items():
        data = composition.get(key)
        if not data or not data.get("top"):
            continue
        all_values = data.get("all", data["top"])
        if key == _DONUT_CATEGORY_KEY:
            top = data["top"]
            other_count = data["total"] - sum(c for _v, c, _p in top)
            body = (
                '<div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;">'
                + _category_donut_svg(top, other_count, data["total"])
                + _format_legend_html(top, other_count, data["total"])
                + "</div>"
                + _show_all_table_html(all_values, len(top))
            )
        else:
            rows = "".join(
                f'<div class="comp-row"><span class="comp-name" title="{html.escape(str(value))}">'
                f'{html.escape(str(value))}</span>'
                f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;'
                f'background:{ACCENT}"></div></div>'
                f'<span class="comp-value">{count} ({pct:.1f}%)</span></div>'
                for value, count, pct in data["top"]
            )
            body = rows + _show_all_table_html(all_values, len(data["top"]))
        cards.append(
            f'<div class="comp-card"><div class="stat-label">{html.escape(title)}</div>'
            f'{body}</div>')
    if not cards:
        return ""
    return (
        '<details class="passed-block" open><summary>Document composition - most common counts'
        '</summary><div class="comp-grid">' + "".join(cards) + "</div></details>"
    )


def _corpus_breakdown_stat_html(cb: dict) -> str:
    if not cb or not cb.get("total_generated"):
        return ""
    label_names = {
        "easy positive": "Easy positive", "hard positive": "Hard positive",
        "easy negative": "Easy negative", "hard negative": "Hard negative",
    }
    rows = "".join(
        f'<tr><td>{label_names.get(b["label"], b["label"])}</td>'
        f'<td>{b["accepted"]:,}</td><td>{b["generated"]:,}</td>'
        f'<td>{b["disagreed"]:,}</td><td>{b["rate"]:.1f}%</td></tr>'
        for b in cb.get("buckets", [])
    )
    return f"""
<div class="stat-card stat-card-wide">
  <div class="stat-label">Corpus Breakdown</div>
  <table class="corpus-table"><tr><th></th><th>Accepted</th><th>Generated</th><th>Disagreed</th><th>Rate</th></tr>
  {rows}
  <tr class="corpus-total"><td>Total</td><td>{cb.get("total_accepted", 0):,}</td>
  <td>{cb.get("total_generated", 0):,}</td><td>{cb.get("total_disagreed", 0):,}</td>
  <td>{cb.get("total_rate", 0):.1f}%</td></tr></table>
</div>"""


def _fix_list_html(core, additional) -> str:
    """One minimal card per distinct (category, check) that's failing - a
    bold headline ("what") with a small muted subline of concrete numbers,
    and a bold suggested action ("do") with a small muted subline naming
    the category and its pass rate. Every FAIL CheckResult for the same
    check is grouped into one card (scopes like Agreements/Positive,
    Agreements/Negative, ... collapse into one line) rather than one row
    per scope, matching the "very minimal, to the point" fix-card style of
    the reference dashboards - adapted to our own data (we group
    structurally; we don't attempt to semantically re-merge each check's
    free-text detail the way a hand-written dashboard would)."""
    cat_results = {category: results for category, results in core + additional}
    groups: dict[tuple[str, str], list] = {}
    order: list[tuple[str, str]] = []
    for category, results in core + additional:
        for r in results:
            if r.status != Status.FAIL:
                continue
            key = (category, r.title)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(r)
    if not groups:
        return ""

    rows = []
    for category, title in order:
        items = groups[(category, title)]
        first = items[0]
        lead, _examples = split_detail(first.detail)
        scopes = sorted({r.scope for r in items if r.scope})
        if len(scopes) > 1:
            scope_bit = f"{len(items)} issue(s) across {len(scopes)} scope(s)"
        elif scopes:
            scope_bit = f"{html.escape(scopes[0])}"
        else:
            scope_bit = f"{len(items)} issue(s)"
        cat_total = len(cat_results.get(category, []))
        cat_passed = cat_total - _fail_count(cat_results.get(category, []))
        do_text = html.escape(_truncate(first.fix, 110)) if first.fix else "&mdash;"
        rows.append(f"""
<div class="fixcard">
  <span class="fixcard-k">Fix this</span>
  <span class="fixcard-what">{html.escape(_truncate(title, 90))}<small>{html.escape(_truncate(lead, 110))} &middot; {scope_bit}</small></span>
  <span class="fixcard-do">{do_text}<small>{html.escape(_display_category(category))} &middot; {cat_passed} / {cat_total}</small></span>
</div>""")
    return f'<div class="fix-section">{"".join(rows)}</div>'


def _gates_dot_grid_html(core, additional) -> str:
    """Compact strip of colored dots, one per checklist category group
    (green = all passed, red = at least one FAIL) - a faster visual scan
    than reading every row of the glance table below it. Adapted from (not
    copied from) the reference dashboards' "gates" panel, using our own
    category groups instead of a fixed metric set."""
    groups = core + additional
    if not groups:
        return ""
    n_fail_groups = sum(1 for _c, results in groups if _fail_count(results))
    dots = "".join(
        f'<span class="gate-dot" style="background:{RED if _fail_count(results) else GREEN}" '
        f'title="{html.escape(_display_category(category))}: '
        f'{"FAIL" if _fail_count(results) else "PASS"}"></span>'
        for category, results in groups
    )
    return (
        '<div class="gates-strip">'
        f'<span class="gates-label">{len(groups) - n_fail_groups}/{len(groups)} check group(s) passing</span>'
        f'<div class="gates-dots">{dots}</div></div>'
    )


def _fail_detail_html(r) -> str:
    """One failing check's detail - title, scope, lead text, examples, and
    suggested fix. Only ever called for FAIL results; passed checks never
    get this treatment (see _checklist_grid_html)."""
    lead, examples = split_detail(r.detail)
    scope_html = f' <span class="row-scope">({html.escape(r.scope)})</span>' if r.scope else ""
    body = [f"<div>{html.escape(_truncate(lead))}</div>"]
    if examples:
        body.append('<ul class="examples">' + "".join(
            f"<li>{html.escape(_truncate(item))}</li>" for item in examples) + "</ul>")
    if r.fix:
        body.append(f'<div class="fix">Suggested fix: {html.escape(r.fix)}</div>')
    return f'<div class="ckfail-item"><b>{html.escape(r.title)}</b>{scope_html}{"".join(body)}</div>'


def _checklist_grid_html(core, additional) -> str:
    """Compact, PowerBI-dashboard-style checklist, replacing both the old
    "Checklist at a glance" table and the full per-check results table that
    used to follow it. A passing category collapses to a single line (name
    + its total check count - no per-check detail, since the color already
    says every one of them passed). A failing category gets a highlighted
    block showing only its FAILING checks, never the passing ones in that
    same category - at real-report scale (hundreds of checks across dozens
    of categories), showing full detail for every PASS was "over-showing"
    far more than it helped."""
    def _section(label: str, groups) -> str:
        if not groups:
            return ""
        passed = sum(len(results) - _fail_count(results) for _c, results in groups)
        total = sum(len(results) for _c, results in groups)
        parts = [f'<div class="checklist-sublabel">{html.escape(label)} &middot; {len(groups)} '
                 f'group(s) &middot; {passed} / {total}</div>']
        pass_rows = []
        for category, results in groups:
            n_fail_cat = _fail_count(results)
            n_total = len(results)
            name = html.escape(_display_category(category))
            if n_fail_cat:
                fails = [r for r in results if r.status == Status.FAIL]
                parts.append(
                    '<div class="ckfail">'
                    '<div class="ckfail-head"><span class="mk no">&#10005;</span>'
                    f'<span class="cn"><b>{name}</b></span>'
                    f'<span class="cc">{n_total - n_fail_cat} / {n_total}</span></div>'
                    + "".join(_fail_detail_html(r) for r in fails)
                    + "</div>"
                )
            else:
                pass_rows.append(
                    f'<div class="ck"><span class="mk ok">&#10003;</span>'
                    f'<span class="cn">{name}</span><span class="cc">{n_total}</span></div>'
                )
        if pass_rows:
            parts.append(f'<div class="cks">{"".join(pass_rows)}</div>')
        return "".join(parts)

    return ('<h2>Checklist</h2>'
            + _section("Checklist", core)
            + _section(ADDITIONAL_CHECKS_SECTION_TITLE, additional))


_GRADE_COLORS = {
    "strong": GREEN, "acceptable": "#2d5a8b", "weak": "#9a6700",
    "critical": RED, "report_only": MUTED, "n/a": MUTED,
}
# (strong_min, acceptable_min, weak_min) per gated/graded metric - everything
# below weak_min is "critical". Template Cluster Rate has no ladder (report
# only, lower-is-better, no pass/fail grade per its own spec).
_METRIC_THRESHOLDS = {
    "target_language_purity": (0.995, 0.990, 0.970),
    "sit_position_diversity": (0.85, 0.75, 0.60),
    "keyword_proximity_diversity": (0.70, 0.55, 0.30),
    "negative_label_uniqueness": (0.70, 0.55, 0.35),
}
_METRIC_ORDER = [
    "template_cluster_rate", "target_language_purity", "sit_position_diversity",
    "keyword_proximity_diversity", "negative_label_uniqueness",
]


def _metric_ladder_html(score: float, strong_min: float, acceptable_min: float, weak_min: float) -> str:
    """A thin, four-zone (critical/weak/acceptable/strong) horizontal track
    with a marker at the metric's actual score - our own visual for "where
    does this score fall against its own thresholds", reusing the same
    bar-track idiom already used for MCE coverage elsewhere in this report,
    not the reference dashboards' own box-ladder."""
    zones = [(0.0, weak_min, RED), (weak_min, acceptable_min, "#9a6700"),
             (acceptable_min, strong_min, "#2d5a8b"), (strong_min, 1.0, GREEN)]
    segs = "".join(
        f'<div style="position:absolute;left:{a * 100:.1f}%;width:{(b - a) * 100:.1f}%;'
        f'height:100%;background:{color}"></div>'
        for a, b, color in zones if b > a
    )
    marker = max(0.0, min(1.0, score)) * 100
    return (
        '<div style="position:relative;height:7px;border-radius:4px;overflow:hidden;'
        f'background:{BORDER};margin:5px 0 2px;">{segs}'
        f'<div style="position:absolute;left:{marker:.1f}%;top:-3px;width:2px;height:13px;'
        'background:#14181f;"></div></div>'
    )


def _quality_metrics_section_html(quality_metrics: dict) -> str:
    if not quality_metrics:
        return ""
    rows = []
    for key in _METRIC_ORDER:
        m = quality_metrics.get(key)
        if not m:
            continue
        grade = m.get("grade", "n/a")
        color = _GRADE_COLORS.get(grade, MUTED)
        score = m.get("score")
        score_str = f"{score:.3f}" if score is not None else "n/a"
        ladder = (_metric_ladder_html(score, *_METRIC_THRESHOLDS[key])
                  if key in _METRIC_THRESHOLDS and score is not None else "")
        gate_note = " &middot; gates the verdict" if m.get("gate") else ""
        rows.append(f"""
<div class="qm-row">
  <div class="qm-head"><span class="qm-label">{html.escape(m["label"])}</span>
  <span class="qm-badge" style="background:{color}">{grade.replace('_', ' ').upper()}</span>
  <span class="qm-score">{score_str}</span></div>
  {ladder}
  <div class="qm-detail">{html.escape(m["detail"])}<span class="qm-sample"> ({html.escape(m.get("sample_note", ""))}{gate_note})</span></div>
</div>""")
    if not rows:
        return ""
    return f'<h2>Quality Metrics</h2><div class="qm-grid">{"".join(rows)}</div>'


def _doc_counts_stat_html(dc: dict) -> str:
    if not dc:
        return ""
    ag, da = dc.get("agreements", {}) or {}, dc.get("disagreements", {}) or {}
    combined = dc.get("combined_total")
    total = dc.get("total")
    return f"""
<div class="stat-card">
  <div class="stat-label">Documents</div>
  <div class="stat-primary">{combined if combined is not None else "&mdash;"}</div>
  <div class="stat-secondary">total (Agreements + Disagreements)</div>
  <div class="stat-secondary">Agreements: {total if total is not None else "&mdash;"}
    ({ag.get("positive", "&mdash;")} pos / {ag.get("negative", "&mdash;")} neg)</div>
  <div class="stat-secondary">Disagreements: {da.get("positive", "&mdash;")} pos / {da.get("negative", "&mdash;")} neg</div>
</div>"""


# ---------------------------------------------------------------- HTML ----

def render_html(report: RunReport) -> str:
    counts = report.counts()
    n_fail = counts.get("FAIL", 0)
    gated_critical = _gated_critical_metrics(report.quality_metrics)
    if n_fail or gated_critical:
        extra = (f" + {len(gated_critical)} quality metric(s) below gate threshold "
                 f"({', '.join(gated_critical)})") if gated_critical else ""
        verdict = ("FAILING", f"{n_fail} check(s) failed{extra} - needs fixes before this output ships.")
    else:
        verdict = ("CLEAN", "Every quality check passed for this run.")

    core, additional = _core_and_additional(report)

    stat_cards = "".join([
        _doc_counts_stat_html(report.doc_counts),
        _mce_stat_html((report.stats or {}).get("mce_coverage", {})),
        _document_length_stat_html((report.stats or {}).get("document_length", {})),
        _label_distribution_stat_html((report.stats or {}).get("label_distribution", {})),
        _corpus_breakdown_stat_html((report.stats or {}).get("corpus_breakdown", {})),
    ])

    parts = [f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>QC Report - {html.escape(report.label)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif; margin: 0;
          padding: 1.75rem 2rem; color: {INK}; background: #ffffff; line-height: 1.45;
          font-size: 13px; }}
  h1 {{ font-size: 1.3rem; margin: 0 0 0.15rem; color: {ACCENT}; }}
  h2 {{ margin: 0; font-size: 1rem; }}
  .path {{ color: {MUTED}; font-size: 0.8rem; margin-bottom: 1rem; }}
  .verdict {{ display: inline-block; padding: 0.45rem 0.9rem; border-radius: 6px; font-weight: 600;
              margin-bottom: 1rem; font-size: 0.85rem; }}
  .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                gap: 12px; margin-bottom: 1.3rem; align-items: stretch; }}
  .stat-card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; padding: 12px 14px; }}
  .stat-card-wide {{ grid-column: span 2; min-width: 320px; }}
  .stat-label {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em;
                 color: {MUTED}; margin-bottom: 6px; }}
  .stat-primary {{ font-size: 1.35rem; font-weight: 700; color: {ACCENT}; }}
  .stat-secondary {{ font-size: 0.78rem; color: {MUTED}; margin-top: 2px; }}
  .donut {{ border-radius: 50%; flex: 0 0 auto; display: flex; align-items: center;
            justify-content: center; }}
  .donut-hole {{ width: 68%; height: 68%; border-radius: 50%; background: #ffffff;
                 display: flex; flex-direction: column; align-items: center; justify-content: center;
                 font-size: 0.82rem; font-weight: 700; text-align: center; }}
  .mce-layout {{ display: flex; align-items: center; gap: 16px; }}
  .mce-bars {{ flex: 1 1 auto; min-width: 0; }}
  .mce-bar-row {{ display: flex; align-items: center; gap: 8px; margin: 3px 0; }}
  .mce-bar-label {{ width: 56px; flex: 0 0 auto; font-size: 0.78rem; color: {MUTED}; }}
  .mce-bar-value {{ width: 150px; flex: 0 0 auto; font-size: 0.78rem; color: {INK}; text-align: right; }}
  .bar-track {{ flex: 1 1 auto; height: 9px; border-radius: 5px; background: {BORDER};
                overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 5px; }}
  .dist-grid {{ margin-top: 2px; }}
  .dist-row {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; }}
  .dist-name {{ width: 96px; flex: 0 0 auto; font-size: 0.78rem; color: {MUTED}; }}
  .dist-value {{ width: 86px; flex: 0 0 auto; font-size: 0.78rem; font-weight: 700;
                 color: {ACCENT}; text-align: right; }}
  .doclen-layout {{ display: grid; grid-template-columns: 1fr 90px; gap: 14px; align-items: center;
                     margin-top: 6px; }}
  .doclen-chart {{ min-width: 0; }}
  .doclen-bars {{ display: flex; align-items: flex-end; gap: 2px; height: 70px; }}
  .doclen-bar {{ flex: 1 1 0; background: {ACCENT}; border-radius: 2px 2px 0 0; min-height: 2px; }}
  .doclen-bar-overflow {{ background: {MUTED}; }}
  .doclen-axis {{ display: flex; justify-content: space-between; font-size: 0.68rem;
                  color: {MUTED}; margin-top: 3px; }}
  .doclen-stats {{ display: flex; flex-direction: column; gap: 6px; }}
  .mini-stat {{ background: #ffffff; border: 1px solid {BORDER}; border-radius: 6px;
                padding: 5px 8px; text-align: center; }}
  .info-strip {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 0.6rem 0 1.1rem; }}
  .info-chip {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 999px;
                padding: 4px 12px; font-size: 0.76rem; display: flex; gap: 6px; align-items: baseline; }}
  .info-chip-label {{ color: {MUTED}; text-transform: uppercase; letter-spacing: 0.04em; font-size: 0.65rem; }}
  .info-chip-value {{ color: {ACCENT}; font-weight: 700; }}
  .comp-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
                gap: 12px; margin-top: 0.6rem; }}
  .comp-card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; padding: 10px 12px; }}
  .comp-row {{ display: flex; align-items: center; gap: 7px; margin: 3px 0; }}
  .comp-name {{ width: 42%; flex: 0 0 auto; font-size: 0.74rem; color: {MUTED};
                overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .comp-value {{ width: 82px; flex: 0 0 auto; font-size: 0.74rem; color: {INK}; text-align: right; }}
  .comp-legend {{ list-style: none; margin: 0; padding: 0; font-size: 0.74rem; flex: 1 1 auto; min-width: 140px; }}
  .comp-legend li {{ margin: 3px 0; }}
  .legend-swatch {{ display: inline-block; width: 10px; height: 10px; margin-right: 6px;
                     border-radius: 2px; vertical-align: middle; }}
  details.show-all {{ margin-top: 6px; }}
  details.show-all summary {{ cursor: pointer; font-size: 0.72rem; color: {ACCENT};
    padding: 2px 0; user-select: none; }}
  .show-all-table {{ font-size: 0.72rem; margin: 4px 0 0; max-height: 220px;
                      display: block; overflow-y: auto; }}
  .show-all-table th, .show-all-table td {{ padding: 0.2rem 0.4rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 1.3rem; table-layout: fixed; }}
  th, td {{ border: 1px solid {BORDER}; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.82rem;
            vertical-align: top; word-wrap: break-word; overflow-wrap: break-word; }}
  th {{ background: {PANEL}; color: {ACCENT}; }}
  tbody tr:nth-child(even) {{ background: #fafbfc; }}
  .fix {{ color: {ACCENT}; font-size: 0.78rem; margin-top: 0.3rem; }}
  .examples {{ margin: 0.25rem 0 0.15rem 0; padding-left: 1.1rem; font-size: 0.8rem; }}
  .examples li {{ margin: 0.1rem 0; }}
  details.passed-block {{ margin-top: 0.5rem; }}
  details.passed-block summary {{ cursor: pointer; font-size: 0.82rem; color: {MUTED};
    padding: 0.3rem 0; user-select: none; }}
  details.passed-block summary:hover {{ color: {INK}; }}
  .row-scope {{ color: {MUTED}; font-size: 0.72rem; font-weight: 400; }}
  /* Fix cards - one per distinct failing check, minimal "what/do" layout */
  .fix-section {{ display: flex; flex-direction: column; gap: 8px; margin-bottom: 1.1rem; }}
  .fixcard {{ background: {PANEL}; border-left: 4px solid {RED}; border-radius: 6px;
              padding: 9px 14px; display: grid; grid-template-columns: 70px minmax(0, 1fr) minmax(0, 1fr);
              gap: 4px 18px; align-items: center; }}
  .fixcard-k {{ font-size: 0.68rem; letter-spacing: 0.05em; text-transform: uppercase;
                font-weight: 700; color: {RED}; }}
  .fixcard-what {{ font-weight: 700; font-size: 0.85rem; }}
  .fixcard-do {{ font-weight: 700; font-size: 0.82rem; color: {ACCENT}; }}
  .fixcard-what small, .fixcard-do small {{ display: block; font-weight: 400; font-size: 0.74rem;
    color: {MUTED}; margin-top: 1px; }}
  /* Checklist - compact pass rows (name + count only) vs highlighted fail blocks */
  .checklist-sublabel {{ font-size: 0.72rem; letter-spacing: 0.05em; text-transform: uppercase;
    color: {MUTED}; font-weight: 700; margin: 1.1rem 0 0.4rem; }}
  .cks {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 0 18px; }}
  .ck {{ display: grid; grid-template-columns: 18px minmax(0, 1fr) auto; gap: 8px; align-items: baseline;
         padding: 4px 0; border-top: 1px solid {BORDER}; font-size: 0.82rem; }}
  .mk {{ width: 16px; height: 16px; border-radius: 4px; display: flex; align-items: center;
         justify-content: center; font-size: 0.65rem; font-weight: 700; }}
  .mk.ok {{ background: #dff1e7; color: {GREEN}; }}
  .mk.no {{ background: #f8dddd; color: {RED}; }}
  .cn {{ overflow-wrap: anywhere; }}
  .cc {{ color: {MUTED}; font-variant-numeric: tabular-nums; }}
  .ckfail {{ background: #fdf1f1; border-radius: 6px; padding: 8px 12px; margin: 6px 0; }}
  .ckfail-head {{ display: grid; grid-template-columns: 18px minmax(0, 1fr) auto; gap: 8px;
                  align-items: baseline; font-size: 0.85rem; }}
  .ckfail-head .cc {{ color: {RED}; font-weight: 700; }}
  .ckfail-item {{ margin: 6px 0 0 26px; font-size: 0.8rem; }}
  .ckfail-item b {{ font-weight: 700; }}
  .gates-strip {{ display: flex; align-items: center; gap: 10px; margin: 0 0 0.8rem; flex-wrap: wrap; }}
  .gates-label {{ font-size: 0.78rem; color: {MUTED}; white-space: nowrap; }}
  .gates-dots {{ display: flex; flex-wrap: wrap; gap: 4px; }}
  .gate-dot {{ width: 11px; height: 11px; border-radius: 3px; display: inline-block; }}
  .corpus-table {{ margin: 4px 0 0; font-size: 0.78rem; table-layout: auto; }}
  .corpus-table th, .corpus-table td {{ padding: 0.25rem 0.5rem; }}
  .corpus-table tr.corpus-total td {{ font-weight: 700; background: #ffffff; }}
  .qm-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
              gap: 12px; margin: 0.6rem 0 1.3rem; }}
  .qm-row {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; padding: 10px 14px; }}
  .qm-head {{ display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }}
  .qm-label {{ font-weight: 700; color: {ACCENT}; font-size: 0.85rem; }}
  .qm-badge {{ color: white; font-size: 0.62rem; font-weight: 700; padding: 1px 7px;
               border-radius: 4px; letter-spacing: 0.03em; white-space: nowrap; }}
  .qm-score {{ margin-left: auto; font-weight: 700; font-size: 0.9rem; color: {ACCENT}; }}
  .qm-detail {{ font-size: 0.76rem; color: {MUTED}; margin-top: 2px; }}
  .qm-sample {{ opacity: 0.8; }}

</style></head><body>
<h1>SIT Output Quality Report</h1>
<div class="path"><strong>{html.escape(report.label)}</strong><br>{html.escape(report.version_dir)}</div>
{_generation_info_html(report.generation_info)}
<div class="verdict" style="background:{RED if (n_fail or gated_critical) else GREEN};color:white;">
  {verdict[0]}: {html.escape(verdict[1])}
</div>

{_fix_list_html(core, additional)}

<div class="stat-grid">{stat_cards}</div>

{_composition_section_html((report.stats or {}).get("composition", {}))}

{_quality_metrics_section_html(report.quality_metrics)}

{_gates_dot_grid_html(core, additional)}

{_checklist_grid_html(core, additional)}
</body></html>"""]
    return "".join(parts)


# ----------------------------------------------------------------- PDF ----

def render_pdf(report: RunReport) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def _clean(s: str) -> str:
        # FPDF's built-in fonts are Latin-1 only; replace anything outside
        # that range instead of raising, so oddities in real file paths
        # (e.g. non-ASCII filenames) never crash the export.
        return (s or "").encode("latin-1", "replace").decode("latin-1")

    def _bar(pct: float | None, width: float = 90, height: float = 4.2) -> None:
        """A small filled rectangle "progress bar" - green fill for the
        detected share, light-gray track for the remainder, red fill
        instead of green whenever coverage isn't 100% (the one thing this
        number must never hide)."""
        x, y = pdf.get_x(), pdf.get_y()
        pdf.set_fill_color(229, 231, 235)
        pdf.rect(x, y, width, height, style="F")
        if pct:
            fill_w = width * min(pct, 100.0) / 100.0
            if pct >= 99.999:
                pdf.set_fill_color(26, 127, 55)
            else:
                pdf.set_fill_color(207, 34, 46)
            pdf.rect(x, y, fill_w, height, style="F")
        pdf.set_xy(x, y + height + 1.5)

    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 8, "SIT Output Quality Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(107, 114, 128)
    pdf.multi_cell(0, 5, _clean(report.label), new_x="LMARGIN", new_y="NEXT")
    pdf.multi_cell(0, 5, _clean(report.version_dir), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    info = report.generation_info or {}
    info_bits = []
    if info.get("generator_model"):
        version_bit = f" ({info['generator_model_version']})" if info.get("generator_model_version") else ""
        info_bits.append(f"Model: {info['generator_model']}{version_bit}")
    if info.get("sit_grader_model"):
        info_bits.append(f"SIT Grader: {info['sit_grader_model']}")
    if info.get("docparser_version"):
        info_bits.append(f"DocParser: {info['docparser_version']}")
    if info.get("mce_version"):
        info_bits.append(f"MCE: {info['mce_version']}")
    if info_bits:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(31, 58, 95)
        pdf.multi_cell(0, 5, _clean("  |  ".join(info_bits)), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    counts = report.counts()
    n_fail = counts.get("FAIL", 0)
    gated_critical = _gated_critical_metrics(report.quality_metrics)
    if n_fail or gated_critical:
        extra = (f" + {len(gated_critical)} quality metric(s) below gate threshold "
                 f"({', '.join(gated_critical)})") if gated_critical else ""
        verdict = f"FAILING - {n_fail} check(s) failed{extra}, needs fixes before this output ships."
        color = (207, 34, 46)
    else:
        verdict = "CLEAN - every quality check passed for this run."
        color = (26, 127, 55)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*color)
    pdf.multi_cell(0, 7, _clean(verdict), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(1)

    core, additional = _core_and_additional(report)
    fails = [(category, r) for category, results in core + additional
             for r in results if r.status == Status.FAIL]
    if fails:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(31, 58, 95)
        pdf.multi_cell(0, 6, _clean(f"Fix these {len(fails)}"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 9)
        for category, r in fails:
            lead, _examples = split_detail(r.detail)
            scope_bit = f" ({r.scope})" if r.scope else ""
            line = f"[{_display_category(category)}] {r.title}{scope_bit} - {_truncate(lead, 140)}"
            pdf.multi_cell(0, 4.5, _clean(line), new_x="LMARGIN", new_y="NEXT")
            if r.fix:
                pdf.set_font("Helvetica", "I", 8.5)
                pdf.multi_cell(0, 4, _clean(f"    Fix: {_truncate(r.fix, 140)}"),
                                new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", "", 9)
        pdf.ln(2)

    dc = report.doc_counts or {}
    if dc:
        ag, da = dc.get("agreements", {}) or {}, dc.get("disagreements", {}) or {}
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, _clean(
            f"Documents: {dc.get('combined_total', 'n/a')} total  |  "
            f"Agreements: {dc.get('total', 'n/a')} "
            f"({ag.get('positive', 'n/a')} pos / {ag.get('negative', 'n/a')} neg)  |  "
            f"Disagreements: {da.get('positive', 'n/a')} pos / {da.get('negative', 'n/a')} neg"
        ), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    mce = (report.stats or {}).get("mce_coverage") or {}
    if mce.get("checked"):
        pos, neg = mce.get("positive", {}), mce.get("negative", {})
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(0, 5, _clean(f"MCE detection coverage: {_pct_str(mce.get('pct'))} "
                                     f"({mce.get('detected', 0)}/{mce.get('checked', 0)})"),
                        new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 4, _clean(f"positive: {_pct_str(pos.get('pct'))} "
                                     f"({pos.get('detected', 0)}/{pos.get('checked', 0)})"),
                        new_x="LMARGIN", new_y="NEXT")
        _bar(pos.get("pct"))
        pdf.multi_cell(0, 4, _clean(f"negative: {_pct_str(neg.get('pct'))} "
                                     f"({neg.get('detected', 0)}/{neg.get('checked', 0)})"),
                        new_x="LMARGIN", new_y="NEXT")
        _bar(neg.get("pct"))
        pdf.ln(1)

    dist = (report.stats or {}).get("label_distribution") or {}
    if dist.get("total"):
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(0, 5, "Easy / Hard x Positive / Negative", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        buckets = [("Easy positive", dist.get("easy positive", 0)),
                   ("Hard positive", dist.get("hard positive", 0)),
                   ("Easy negative", dist.get("easy negative", 0)),
                   ("Hard negative", dist.get("hard negative", 0))]
        dist_total = dist.get("total", 0) or 1
        max_count = max((c for _, c in buckets), default=0) or 1
        pdf.set_fill_color(31, 58, 95)
        for name, count in buckets:
            x, y = pdf.get_x(), pdf.get_y()
            pdf.cell(32, 4.2, _clean(name))
            pdf.rect(x + 32, y, 90 * count / max_count, 4.2, style="F")
            pdf.set_xy(x + 32 + 92, y)
            pdf.cell(0, 4.2, f"{count} ({count / dist_total * 100:.1f}%)",
                     new_x="LMARGIN", new_y="NEXT")
        pdf.multi_cell(0, 4, f"total: {dist.get('total', 0)}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    cb = (report.stats or {}).get("corpus_breakdown") or {}
    if cb.get("total_generated"):
        label_names = {
            "easy positive": "Easy positive", "hard positive": "Hard positive",
            "easy negative": "Easy negative", "hard negative": "Hard negative",
        }
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(0, 5, "Corpus Breakdown (Accepted / Generated / Disagreed / Rate)",
                        new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        for b in cb.get("buckets", []):
            pdf.multi_cell(0, 4.2, _clean(
                f"{label_names.get(b['label'], b['label'])}: {b['accepted']} / "
                f"{b['generated']} / {b['disagreed']} / {b['rate']:.1f}%"
            ), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 9)
        pdf.multi_cell(0, 4.2, _clean(
            f"Total: {cb.get('total_accepted', 0)} / {cb.get('total_generated', 0)} / "
            f"{cb.get('total_disagreed', 0)} / {cb.get('total_rate', 0):.1f}%"
        ), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.ln(2)

    quality_metrics = report.quality_metrics or {}
    if quality_metrics:
        pdf.set_font("Helvetica", "B", 13)
        pdf.multi_cell(0, 7, "Quality Metrics", new_x="LMARGIN", new_y="NEXT")
        for key in _METRIC_ORDER:
            m = quality_metrics.get(key)
            if not m:
                continue
            grade = m.get("grade", "n/a")
            score = m.get("score")
            score_str = f"{score:.3f}" if score is not None else "n/a"
            pdf.set_font("Helvetica", "B", 10)
            pdf.multi_cell(0, 5, _clean(f"{m['label']}: {score_str} [{grade.replace('_', ' ').upper()}]"),
                            new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9)
            pdf.multi_cell(0, 4.5, _clean(m["detail"]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        pdf.ln(1)

    composition = (report.stats or {}).get("composition") or {}
    if composition:
        pdf.set_font("Helvetica", "B", 13)
        pdf.multi_cell(0, 7, "Document composition - most common counts",
                        new_x="LMARGIN", new_y="NEXT")
        for key, title in _COMPOSITION_TITLES.items():
            data = composition.get(key)
            if not data or not data.get("top"):
                continue
            pdf.set_font("Helvetica", "B", 10)
            pdf.multi_cell(0, 5, _clean(title), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9)
            max_count = max((c for _v, c, _p in data["top"]), default=0) or 1
            pdf.set_fill_color(31, 58, 95)
            for value, count, pct in data["top"]:
                x, y = pdf.get_x(), pdf.get_y()
                pdf.cell(45, 4.2, _clean(_truncate(str(value), 40)))
                pdf.rect(x + 45, y, 70 * count / max_count, 4.2, style="F")
                pdf.set_xy(x + 45 + 72, y)
                pdf.cell(0, 4.2, f"{count} ({pct:.1f}%)", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        pdf.ln(2)

    def _checklist_block(label: str, groups) -> None:
        """One line per category: a pass is just "[OK] Name  N" (no
        per-check detail - the color already says every one of them
        passed); a fail shows its count plus the failing checks only,
        never the passing ones in that same category. Mirrors
        _checklist_grid_html's HTML/Streamlit behavior."""
        if not groups:
            return
        passed = sum(len(results) - _fail_count(results) for _c, results in groups)
        total = sum(len(results) for _c, results in groups)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(31, 58, 95)
        pdf.multi_cell(0, 6, _clean(f"{label} - {len(groups)} group(s) - {passed} / {total}"),
                        new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 9)
        for category, results in groups:
            n_fail_cat = _fail_count(results)
            n_total = len(results)
            name = _display_category(category)
            if not n_fail_cat:
                pdf.multi_cell(0, 5, _clean(f"[OK]  {name}  ({n_total})"), new_x="LMARGIN", new_y="NEXT")
                continue
            pdf.set_text_color(207, 34, 46)
            pdf.set_font("Helvetica", "B", 9)
            pdf.multi_cell(0, 5, _clean(f"[FAIL]  {name}  ({n_total - n_fail_cat} / {n_total})"),
                            new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Helvetica", "", 9)
            for r in results:
                if r.status != Status.FAIL:
                    continue
                scope_note = f" ({r.scope})" if r.scope else ""
                lead, examples = split_detail(r.detail)
                pdf.multi_cell(0, 5, _clean(f"    - {r.title}{scope_note}: {_truncate(lead, 200)}"),
                                new_x="LMARGIN", new_y="NEXT")
                for item in examples:
                    pdf.multi_cell(0, 4.5, _clean(f"        - {_truncate(item, 160)}"),
                                    new_x="LMARGIN", new_y="NEXT")
                if r.fix:
                    pdf.set_font("Helvetica", "I", 8.5)
                    pdf.multi_cell(0, 4.5, _clean(f"        Fix: {_truncate(r.fix, 160)}"),
                                    new_x="LMARGIN", new_y="NEXT")
                    pdf.set_font("Helvetica", "", 9)
            pdf.ln(1)
        pdf.ln(2)

    pdf.set_font("Helvetica", "B", 13)
    pdf.multi_cell(0, 7, "Checklist", new_x="LMARGIN", new_y="NEXT")
    _checklist_block("Checklist", core)
    _checklist_block(ADDITIONAL_CHECKS_SECTION_TITLE, additional)

    return bytes(pdf.output())
