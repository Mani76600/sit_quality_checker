"""Render a RunReport as a shareable, self-contained HTML page or a PDF -
alongside the existing raw JSON export, for reviewing/circulating results
outside the Streamlit app itself.

Visual design (HTML/PDF export only - see qc/streamlit_report.py for the
live in-app view): a dense, single-column-of-cards "dashboard" layout - a
navy verdict band up top, three glanceable gate cards (QC checks / Quality
Metrics / MCE Detection), one minimal "what/do" fix card per distinct
failing check, then compact data cards (quality metrics ladder table,
accepted-by-class table, document length histogram, file format bars,
business context spread) and a final compact checklist card. Only two
status colors anywhere - green/navy for pass, red for fail - everything
else is neutral chrome. The page is sized to print cleanly on A4 (see the
`@page`/`@media print` rules below) rather than an arbitrary web-page
width.
"""

from __future__ import annotations

import html
import re

from qc.check_groups import ADDITIONAL_CHECK_CATEGORIES, ADDITIONAL_CHECKS_SECTION_TITLE
from qc.detail_format import split_detail
from qc.models import RunReport, Status

# Literal hex (not CSS var() strings) on purpose: these constants are also
# inlined directly into HTML this module hands to qc/streamlit_report.py
# (_quality_metrics_section_html, _business_context_spread_html,
# _gates_summary_html), which is rendered inside Streamlit's own page -
# a page that never defines the `--strong`/`--crit`/... custom properties
# the new <style> block below sets up, so var() references would resolve
# to nothing there. Values match this file's new --strong/--crit/... tokens.
GREEN = "#1a7046"
RED = "#a82424"
ACCENT = "#1f3a68"  # structural chrome only (headings, big numbers) - never a status color
INK = "#14213d"
MUTED = "#56637d"
BORDER = "#d5dce8"
PANEL = "#ffffff"

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


def _truncate_short(text: str, limit: int) -> str:
    """Same idea as _truncate, but for compact card/chip contexts (fix
    cards, gate chips) where the "(truncated, N chars total)" meta-text
    reads as noise/indirect rather than information - just a clean
    ellipsis, no commentary about the truncation itself. Also trims a
    dangling trailing comma (many checks build detail text like "N file(s)
    failed to parse, e.g. [...]" where split_detail() strips the "e.g.
    [...]" part out into its own examples list, leaving an orphaned comma
    at the end of the lead sentence)."""
    text = (text or "").strip().rstrip(",")
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_space = cut.rfind(" ")
    if last_space > limit * 0.6:  # don't back up past a word boundary that isn't close to the limit
        cut = cut[:last_space]
    return cut.rstrip().rstrip(",") + "…"


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


def _verdict_text(n_fail: int, gated_critical: list[str]) -> tuple[str, str, bool]:
    """(headline, subline, is_failing) - a short bold headline plus a
    compact subline of only the counts that are actually non-zero (never
    "0 check(s) failed" when what actually failed was a quality metric, or
    vice versa), and no trailing "needs fixes before this output ships"
    sentence - the reference dashboards' own verdict is just as terse."""
    is_failing = bool(n_fail or gated_critical)
    if not is_failing:
        return "Ready to ship", "Every quality check passed for this run.", False
    bits = []
    if n_fail:
        bits.append(f"{n_fail} failed check{'s' if n_fail != 1 else ''}")
    if gated_critical:
        n = len(gated_critical)
        bits.append(f"{n} quality metric{'s' if n != 1 else ''} below threshold")
    return "Not ready to ship", " · ".join(bits), True


def _split_label(label: str) -> tuple[str, str]:
    """report.label is built as "<Title> / Version_YYYYMMDD_HHMM" (or
    "<Title> / <Language> / Version_...") - the band header shows the
    title as the page's <h1> and the trailing "Version_..." part as a
    small mono chip next to it, matching the reference dashboards' own
    title/version split."""
    if " / " in label:
        title, _, version = label.rpartition(" / ")
        return title, version
    return label, ""


# ------------------------------------------------------------- band/hero --

def _band_header_html(report: RunReport, headline: str, subline: str, is_failing: bool) -> str:
    title, version = _split_label(report.label)
    info = report.generation_info or {}
    meta_bits = []
    if version:
        meta_bits.append(f'<span class="mono">{html.escape(version)}</span>')
    if info.get("language_code"):
        meta_bits.append(
            f'<span>{html.escape(info["language_code"].upper())} &middot; '
            f'{html.escape(info.get("language_name", info["language_code"]))}</span>'
        )
    meta = f'<div class="meta">{"".join(meta_bits)}</div>' if meta_bits else ""
    verdict_bg = "var(--crit-bg)" if is_failing else "var(--strong-bg)"
    verdict_fg = "var(--crit)" if is_failing else "var(--strong)"
    return f"""
<header class="band">
  <div>
    <div class="label">SIT output quality report</div>
    <h1>{html.escape(title)}</h1>
    {meta}
  </div>
  <div class="verdict" style="background:{verdict_bg};color:{verdict_fg};">
    <b>{html.escape(headline)}</b><span>{html.escape(subline)}</span>
  </div>
</header>"""


def _hero_card_html(dc: dict) -> str:
    if not dc:
        return ""
    ag, da = dc.get("agreements", {}) or {}, dc.get("disagreements", {}) or {}
    combined_total = dc.get("combined_total") or 0
    accepted_total = dc.get("total") or 0
    ag_pos, ag_neg = ag.get("positive", 0) or 0, ag.get("negative", 0) or 0
    da_pos, da_neg = da.get("positive", 0) or 0, da.get("negative", 0) or 0
    gen_pos, gen_neg = ag_pos + da_pos, ag_neg + da_neg
    disagreed_total = da_pos + da_neg
    rate = (accepted_total / combined_total * 100.0) if combined_total else None
    rate_str = f"{rate:.1f}" if rate is not None else "n/a"
    bar_width = min(rate, 100.0) if rate is not None else 0.0
    return f"""
<div class="card hero">
  <div class="hero-main"><b>{accepted_total:,}</b><span>Accepted</span></div>
  <div class="hero-split">
    <div><b>{ag_pos:,}</b><span>Positive &middot; of {gen_pos:,}</span></div>
    <div><b>{ag_neg:,}</b><span>Negative &middot; of {gen_neg:,}</span></div>
  </div>
  <div class="hero-foot">
    <div class="bar"><i style="width:{bar_width:.2f}%"></i></div>
    <div class="t"><span><b>{rate_str}%</b> of {combined_total:,} generated</span>
    <span><b>{disagreed_total:,}</b> disagreed ({da_pos:,} pos &middot; {da_neg:,} neg)</span></div>
  </div>
</div>"""


def _pipeline_card_html(info: dict) -> str:
    if not info:
        return ""
    comps = []
    if info.get("generator_model"):
        version_bit = (f' <small>({html.escape(str(info["generator_model_version"]))})</small>'
                       if info.get("generator_model_version") else "")
        comps.append((f'{html.escape(info["generator_model"])}{version_bit}', "Doc generation model"))
    if info.get("sit_grader_model"):
        comps.append((html.escape(info["sit_grader_model"]), "SIT grader model"))
    if info.get("mce_version"):
        comps.append((html.escape(str(info["mce_version"])), "MCE version"))
    if info.get("docparser_version"):
        comps.append((html.escape(str(info["docparser_version"])), "DocParser version"))
    if not comps:
        return ""
    cells = "".join(f"<div><b>{value}</b><span>{html.escape(label)}</span></div>" for value, label in comps)
    return f'<div class="card pad"><h2>Pipeline</h2><div class="comps">{cells}</div></div>'


# ------------------------------------------------------------------ gates --

_GRADE_CHIP_CLASS = {
    "strong": "c-s", "acceptable": "c-a", "weak": "c-w",
    "critical": "c-crit", "report_only": "c-n", "n/a": "c-n",
}


def _gates_summary_html(report: RunReport, core, additional) -> str:
    """Three glanceable gate cards - QC Checks, Quality Metrics, MCE
    Detection - each leading with one big bold number, shown right at the
    top so the overall health of a run is readable without scrolling.
    Adapted from (not copied from) the reference dashboards' "gates" row,
    built from our own category groups / quality metrics / MCE stats
    instead of a fixed external metric set."""
    groups = core + additional
    cards = []

    if groups:
        total_checks = sum(len(results) for _c, results in groups)
        failed_checks = sum(_fail_count(results) for _c, results in groups)
        n_fail_groups = sum(1 for _c, results in groups if _fail_count(results))
        dots = "".join(
            f'<span class="dot {"d-c" if _fail_count(results) else "d-s"}" '
            f'title="{html.escape(_display_category(category))}">'
            f'{"&#10005;" if _fail_count(results) else "&#10003;"}</span>'
            for category, results in groups
        )
        pill = (f"{n_fail_groups} group(s) failed", "p-crit") if n_fail_groups else ("All passing", "p-strong")
        cards.append(f"""
<div class="card gate{' bad' if n_fail_groups else ''}">
  <div class="head"><h2>QC checks</h2><span class="pill {pill[1]}">{pill[0]}</span></div>
  <div class="gate-fig"><b>{total_checks - failed_checks:,} / {total_checks:,}</b>
  <span>{len(groups) - n_fail_groups} of {len(groups)} groups</span></div>
  <div class="dots">{dots}</div>
</div>""")

    qm = report.quality_metrics or {}
    scored = [m for m in qm.values() if m.get("score") is not None]
    if scored:
        qm_bad = any(m.get("gate") and m.get("grade") == "critical" for m in scored)
        graded = [m for m in scored if m.get("grade") != "report_only"]
        report_only_n = len(scored) - len(graded)
        sub = f"{len(graded)} / {len(graded)} graded"
        if report_only_n:
            sub += f" &middot; {report_only_n} report-only"
        chips = "".join(
            f'<span class="chip {_GRADE_CHIP_CLASS.get(m.get("grade"), "c-n")}">'
            f'{html.escape(m["label"].split()[0])} {m["score"]:.3f}</span>'
            for m in scored
        )
        cards.append(f"""
<div class="card gate{' bad' if qm_bad else ''}">
  <div class="head"><h2>Quality metrics</h2>
  <span class="pill {'p-crit' if qm_bad else 'p-strong'}">{'Below threshold' if qm_bad else 'In range'}</span></div>
  <div class="gate-fig"><b>{len(scored)}</b><span>{sub}</span></div>
  <div class="chips">{chips}</div>
</div>""")

    mce = (report.stats or {}).get("mce_coverage") or {}
    if mce.get("checked"):
        pos, neg = mce.get("positive", {}), mce.get("negative", {})
        pct = mce.get("pct")
        mce_bad = pct is None or pct < 99.999
        pos_ok = (pos.get("pct") or 0) >= 99.999
        neg_ok = (neg.get("pct") or 0) >= 99.999
        cards.append(f"""
<div class="card gate{' bad' if mce_bad else ''}">
  <div class="head"><h2>MCE detection</h2>
  <span class="pill {'p-crit' if mce_bad else 'p-strong'}">{'Gap' if mce_bad else 'Full coverage'}</span></div>
  <div class="gate-fig"><b>{_pct_str(pct)}</b><span>{mce.get("detected", 0):,} / {mce.get("checked", 0):,}</span></div>
  <div class="chips chips-stack">
    <span class="chip {'c-s' if pos_ok else 'c-crit'}">Positive {pos.get("detected", 0):,} / {pos.get("checked", 0):,}</span>
    <span class="chip {'c-s' if neg_ok else 'c-crit'}">Negative {neg.get("detected", 0):,} / {neg.get("checked", 0):,}</span>
  </div>
</div>""")

    if not cards:
        return ""
    return f'<div class="gates">{"".join(cards)}</div>'


_METRIC_FIX_HINTS = {
    "target_language_purity": "Review and regenerate the non-target-language passages",
    "sit_position_diversity": "Spread the SIT value across more positions in the document",
    "keyword_proximity_diversity": "Vary how close the nearest keyword sits to the SIT value",
    "negative_label_uniqueness": "Vary the negative lead-in / label wording",
}


def _metric_fix_rows(quality_metrics: dict) -> list[tuple[str, str, str, str]]:
    """One (tag, tag_css_class, what, do) row per quality metric that's
    Weak or Critical - these are real "fix this" opportunities even though
    only a Critical-gated metric flips the overall verdict (see
    _gated_critical_metrics), so they belong in the same punch list as the
    failing checklist items, not off in the Quality Metrics table alone
    where an at-a-glance reader may never scroll to see them."""
    rows: list[tuple[str, str, str, str]] = []
    for key in _METRIC_ORDER:
        m = quality_metrics.get(key)
        if not m or key not in _METRIC_THRESHOLDS:
            continue
        grade = m.get("grade")
        score = m.get("score")
        if grade not in ("critical", "weak") or score is None:
            continue
        strong_min, acceptable_min, weak_min = _METRIC_THRESHOLDS[key]
        needs = weak_min if grade == "critical" else acceptable_min
        tag, cls = ("CRITICAL", "t-crit") if grade == "critical" else ("WEAK", "t-weak")
        what = f"{m['label']} at {score:.3f}, needs ≥ {_ladder_zone_label(needs)}"
        do = _METRIC_FIX_HINTS.get(key, "Review this metric's scoring detail.")
        rows.append((tag, cls, what, do))
    return rows


_CLAUSE_BREAK_RE = re.compile(r"\s+-\s+")

# Generic wording cleanups applied to a check's concrete fact text (the
# "lead" - e.g. "export_summary.json claims counts.positive=5625 but 5900
# file(s) are actually present in raw_doc/ (delta +275)") to get it down
# to the shortest line that still states the actual numbers/fields - never
# a hardcoded per-check rewrite (there are dozens of distinct checks), just
# narration words that add no information once the tag already says "QC".
# The source file name is kept (as a short "file: fact" prefix, not dropped
# outright) - which file disagrees is part of "what's actually wrong", not
# narration; only the verb "claims" is noise.
_FACT_CLEANUP = [
    (re.compile(r"^(\S+\.(?:json|jsonl))\s+claims\s+", re.IGNORECASE), r"\1: "),
    # "counts.positive=5625 but 5900 files..." -> "...=5625 ≠ 5900 files..." -
    # a mismatch between two numbers reads as a mismatch at a glance with
    # "!=" instead of the word "but", which doesn't say which way it's wrong.
    (re.compile(r"(=[\d,]+)\s+but\s+(?=[\d,]+\b)"), "\\1 ≠ "),
    (re.compile(r"\bfile\(s\)", re.IGNORECASE), "files"),
    (re.compile(r"\brow\(s\)", re.IGNORECASE), "rows"),
    (re.compile(r"\bvalue\(s\)", re.IGNORECASE), "values"),
    (re.compile(r"\bdocument\(s\)", re.IGNORECASE), "documents"),
    (re.compile(r"\bare actually present in\b", re.IGNORECASE), "present in"),
    (re.compile(r"\bis actually present in\b", re.IGNORECASE), "present in"),
    (re.compile(r"\bactually\s+"), ""),
    (re.compile(r"/\s*\("), " ("),
    (re.compile(r"\s{2,}"), " "),
]


def _simplify_fact(text: str, limit: int = 150) -> str:
    """The shortest line that still states the actual numbers/fields -
    strip narration, not information. Only hard-truncates (via ellipsis)
    when the fact itself genuinely needs more than one line's worth of
    distinct numbers/names to stay meaningful (e.g. comparing counts
    across two named files) - length varies by how much the fact actually
    requires, never an arbitrary word-count cap."""
    text = (text or "").strip()
    for pattern, repl in _FACT_CLEANUP:
        text = pattern.sub(repl, text)
    return _truncate_short(text.strip(), limit)


def _short_clause(text: str, limit: int = 180) -> str:
    """A suggested-fix sentence, cut at its first " - " (every check's fix
    text puts the justification clause there, e.g. "Reconcile X with Y -
    they must describe the same document set" -> "Reconcile X with Y") so
    the checklist's fix line states the action without restating why -
    the fact line right above it already shows the actual numbers."""
    text = (text or "").strip()
    m = _CLAUSE_BREAK_RE.search(text)
    if m:
        text = text[:m.start()].strip()
    return _truncate_short(text, limit)


def _fix_list_html(core, additional, quality_metrics: dict | None = None) -> str:
    """One compact row per issue - a severity tag plus a single short line
    stating the concrete fact (the actual numbers/fields that are wrong),
    hard-truncated to one line via CSS ellipsis only when the fact itself
    needs more room, with the full problem + fix text available on hover.
    The fact alone is the point: a reader scanning "counts.positive=5625
    but 5900 files present (delta +275)" already knows exactly what's
    wrong without a separate "regenerate X" sentence restating it. Quality
    metric rows (tagged WEAK/CRITICAL) show their curated short fix hint
    instead, since their own fact (score vs. threshold) is already visible
    in the Quality Metrics table above."""
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

    # (tag, tag_css_class, display_text, full_text_for_hover)
    rows: list[tuple[str, str, str, str]] = []
    for category, title in order:
        items = groups[(category, title)]
        first = items[0]
        lead, _examples = split_detail(first.detail)
        lead = lead.rstrip().rstrip(",")
        fix = first.fix or "See the checklist for detail."
        # Which scope (Agreements/Disagreements, Positive/Negative, ...)
        # this fact is from - collapsing every scope into one line loses
        # that otherwise, and a bare count/delta with no scope reads as
        # "where is this even coming from?".
        scopes = sorted({r.scope for r in items if r.scope})
        if len(scopes) > 1:
            scope_bit = f"{len(items)} scopes"
        elif scopes:
            scope_bit = scopes[0]
        else:
            scope_bit = ""
        fact = _simplify_fact(lead or title)
        display = f"{scope_bit}: {fact}" if scope_bit else fact
        rows.append(("QC", "t-crit", display, f"{lead or title} — {fix}"))
    for tag, cls, what, do in _metric_fix_rows(quality_metrics or {}):
        rows.append((tag, cls, do, f"{what} — {do}"))
    if not rows:
        return ""

    row_html = "".join(
        f'<div class="fixrow"><span class="tag {cls}">{tag}</span>'
        f'<span class="fr-action" title="{html.escape(_truncate(full_text))}">{html.escape(display)}</span></div>'
        for tag, cls, display, full_text in rows
    )
    return f'<div class="card"><div class="cardhead head"><h2>Fix these {len(rows)}</h2></div>{row_html}</div>'


# -------------------------------------------------------- quality metrics --

_GRADE_COLORS = {
    "strong": GREEN, "acceptable": "#22609c", "weak": "#8f5a00",
    "critical": RED, "report_only": MUTED, "n/a": MUTED,
}
_GRADE_PILL_CLASS = {
    "strong": "p-strong", "acceptable": "p-ok", "weak": "p-weak",
    "critical": "p-crit", "report_only": "p-info", "n/a": "p-info",
}
_LADDER_ON_CLASS = {"strong": "s", "acceptable": "a", "weak": "w", "critical": "c"}
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
    with a marker at the metric's actual score - reused as-is by
    _quality_metrics_section_html (shared with the Streamlit app)."""
    zones = [(0.0, weak_min, RED), (weak_min, acceptable_min, "#8f5a00"),
             (acceptable_min, strong_min, "#22609c"), (strong_min, 1.0, GREEN)]
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
    """Full quality-metrics section (label/badge/score/bar-ladder/detail per
    metric) - used as-is by the Streamlit app. The HTML/PDF export instead
    uses the more compact _quality_metrics_table_card_html below, matching
    the reference dashboards' table-plus-discrete-ladder layout."""
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


def _ladder_zone_label(x: float) -> str:
    s = f"{x:.3f}".rstrip("0").rstrip(".")
    if s.startswith("0."):
        s = s[1:]
    return s


def _metric_ladder_boxes_html(grade: str, strong_min: float, acceptable_min: float, weak_min: float) -> str:
    """A discrete, four-box labeled ladder (critical/weak/acceptable/
    strong threshold ranges, one box highlighted for the metric's actual
    grade) - the HTML/PDF export's own visual for "where does this score
    fall", matching the reference dashboards' ladder table cell."""
    zones = [
        (f"&lt; {_ladder_zone_label(weak_min)}", "critical"),
        (f"{_ladder_zone_label(weak_min)}&ndash;{_ladder_zone_label(acceptable_min)}", "weak"),
        (f"{_ladder_zone_label(acceptable_min)}&ndash;{_ladder_zone_label(strong_min)}", "acceptable"),
        (f"&ge; {_ladder_zone_label(strong_min)}", "strong"),
    ]
    cells = "".join(
        f'<span class="{"on " + _LADDER_ON_CLASS[zone_grade] if zone_grade == grade else ""}">{label}</span>'
        for label, zone_grade in zones
    )
    return f'<div class="ladder">{cells}</div>'


def _quality_metrics_table_card_html(quality_metrics: dict) -> str:
    if not quality_metrics:
        return ""
    rows = []
    for key in _METRIC_ORDER:
        m = quality_metrics.get(key)
        if not m:
            continue
        grade = m.get("grade", "n/a")
        score = m.get("score")
        score_str = f"{score:.3f}" if score is not None else "n/a"
        if key in _METRIC_THRESHOLDS and score is not None:
            ladder_cell = _metric_ladder_boxes_html(grade, *_METRIC_THRESHOLDS[key])
        else:
            ladder_cell = '<span class="muted">Lower is better</span>'
        sub = html.escape(_truncate_short(m.get("detail", ""), 44))
        rows.append(
            f'<tr><td class="m"><b>{html.escape(m["label"])}</b><span class="sub">{sub}</span></td>'
            f'<td class="r score">{score_str}</td>'
            f'<td><span class="pill {_GRADE_PILL_CLASS.get(grade, "p-info")}">'
            f'{html.escape(grade.replace("_", " ").title())}</span></td>'
            f'<td>{ladder_cell}</td></tr>'
        )
    if not rows:
        return ""
    return (
        '<div class="card"><div class="cardhead head"><h2>Quality metrics</h2></div>'
        '<div class="scroll"><table><thead><tr><th>Metric</th><th class="r">Score</th><th>Grade</th>'
        '<th><div class="ladder-key"><span>Crit</span><span>Weak</span><span>Accept</span>'
        '<span>Strong</span></div></th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div></div>"
    )


# ------------------------------------------------------ data/stat cards ----

_SIT_REUSE_RE = re.compile(r"(\d+) value\(s\) reused")


def _sit_value_reuse_note(core, additional, total_documents: int) -> str:
    """"N SIT value(s) reused across M documents" - the same note the
    reference dashboards show under their class-breakdown table. Our own
    "SIT Value Reuse" check (qc/checks/document_composition_checks.py)
    never fails (it's informational), so its CheckResults never show up in
    the fix cards or the failing-checklist blocks - this is the only place
    that figure is otherwise visible. Summed across every polarity/scope
    result in that category rather than read from a single field, since
    the check records one result per polarity per folder-set scope."""
    if not total_documents:
        return ""
    reused = 0
    found = False
    for category, results in core + additional:
        if "sit value reuse" not in category.lower():
            continue
        for r in results:
            m = _SIT_REUSE_RE.search(r.detail)
            if m:
                found = True
                reused += int(m.group(1))
    if not found:
        return ""
    return f"{reused:,} SIT value(s) reused across {total_documents:,} documents."


def _accepted_by_class_card_html(cb: dict, dist: dict, note: str = "") -> str:
    """Accepted/Generated/Disagreed/Rate per Easy/Hard x Positive/Negative
    bucket when corpus_breakdown stats are available (matches the
    reference dashboards' "Accepted by class" table exactly); falls back
    to a plain count/share table from label_distribution stats when a SIT
    doesn't have corpus_breakdown computed, rather than showing nothing."""
    label_names = {
        "easy positive": "Easy positive", "hard positive": "Hard positive",
        "easy negative": "Easy negative", "hard negative": "Hard negative",
    }
    if cb and cb.get("total_generated"):
        rows = "".join(
            f'<tr><td>{label_names.get(b["label"], b["label"])}</td>'
            f'<td class="r acc">{b["accepted"]:,}</td><td class="r">{b["generated"]:,}</td>'
            f'<td class="r">{b["disagreed"]:,}</td><td class="r">{b["rate"]:.1f}%</td></tr>'
            for b in cb.get("buckets", [])
        )
        title = "Accepted by class"
        body = (
            '<table><thead><tr><th>Class</th><th class="r">Accepted</th><th class="r">Generated</th>'
            '<th class="r">Disagreed</th><th class="r">Rate</th></tr></thead><tbody>' + rows
            + f'</tbody><tfoot><tr><td>Total</td><td class="r">{cb.get("total_accepted", 0):,}</td>'
            f'<td class="r">{cb.get("total_generated", 0):,}</td>'
            f'<td class="r">{cb.get("total_disagreed", 0):,}</td>'
            f'<td class="r">{cb.get("total_rate", 0):.1f}%</td></tr></tfoot></table>'
        )
    elif dist and dist.get("total"):
        dist_total = dist.get("total", 0) or 1
        buckets = [("Easy positive", dist.get("easy positive", 0)), ("Hard positive", dist.get("hard positive", 0)),
                   ("Easy negative", dist.get("easy negative", 0)), ("Hard negative", dist.get("hard negative", 0))]
        rows = "".join(
            f'<tr><td>{name}</td><td class="r acc">{c:,}</td>'
            f'<td class="r">{c / dist_total * 100:.1f}%</td></tr>'
            for name, c in buckets
        )
        title = "Label distribution"
        body = (
            '<table><thead><tr><th>Class</th><th class="r">Count</th><th class="r">Share</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
        )
    else:
        return ""
    note_html = f'<div class="stat-note">{html.escape(note)}</div>' if note else ""
    return (f'<div class="card"><div class="cardhead head"><h2>{title}</h2></div>'
            f'<div class="pad" style="padding-top:0"><div class="scroll">{body}</div>{note_html}</div></div>')


def _hist_card_html(dl: dict) -> str:
    if not dl or not dl.get("total"):
        return ""
    histogram = dl.get("histogram") or []
    if not histogram:
        return ""
    max_count = max((c for _s, _e, c in histogram), default=0) or 1
    overflow = dl.get("overflow", 0)
    total_cols = len(histogram) + (1 if overflow else 0)
    bars = "".join(
        f'<i style="height:{max(c / max_count * 100, 1.5):.1f}%" '
        f'title="{s:,}-{e:,} chars &middot; {c:,} doc(s)"></i>'
        for s, e, c in histogram
    )
    if overflow:
        bars += (f'<i class="over" style="height:{max(overflow / max_count * 100, 1.5):.1f}%" '
                  f'title="{dl.get("overflow_from", "?"):,}+ chars &middot; {overflow:,} doc(s)"></i>')
    axis_min = histogram[0][0]
    axis_max = histogram[-1][1]
    median = dl.get("median", 0)
    median_str = f"{median:,.0f}" if isinstance(median, float) else f"{median:,}"
    bin_width = (histogram[0][1] - histogram[0][0]) or 1
    if median < axis_max:
        idx = max(0, min(len(histogram) - 1, int((median - axis_min) // bin_width)))
        frac = ((median - axis_min) - idx * bin_width) / bin_width
        median_pos = (idx + frac) / total_cols * 100.0
    else:
        median_pos = (len(histogram) + 0.5) / total_cols * 100.0
    median_pos = max(2.0, min(98.0, median_pos))
    return f"""
<div class="card pad">
  <div class="head"><h2>Document length &middot; characters</h2></div>
  <div>
    <div class="hist" style="grid-template-columns:repeat({total_cols},minmax(0,1fr))">{bars}
      <div class="median" style="left:{median_pos:.2f}%"><span>Median {median_str}</span></div>
    </div>
    <div class="axis"><span style="left:0">{axis_min:,}</span>
    <span style="left:100%">{axis_max:,}{"+" if overflow else ""}</span></div>
  </div>
  <div class="stats">
    <div><b>{dl.get("min", 0):,}</b><span>Min</span></div>
    <div><b>{median_str}</b><span>Median</span></div>
    <div><b>{dl.get("max", 0):,}</b><span>Max</span></div>
  </div>
</div>"""


def _flagged_format_values(core, additional, format_values: list[str]) -> set[str]:
    """Which file-format values (if any) a real FAIL result is calling out
    by name - e.g. a "file_format != file_ext" mismatch - so the file
    format card can flag that one value with a small red marker, matching
    the reference dashboards' own flagged-row treatment. Generic text
    search rather than a hardcoded check name, so it keeps working if the
    check that owns this gets renamed."""
    blobs = [r.detail for _category, results in core + additional
             for r in results if r.status == Status.FAIL
             and ("file_format" in r.detail or "file_ext" in r.detail)]
    if not blobs:
        return set()
    blob = " ".join(blobs)
    return {v for v in format_values if v and v in blob}


def _format_frows_html(data: dict, flagged: set[str]) -> str:
    if not data or not data.get("all"):
        return ""
    all_values = data["all"]
    max_count = max((c for _v, c, _p in all_values), default=0) or 1
    rows = []
    for value, count, pct in all_values:
        is_flagged = str(value) in flagged
        cls = "frow flag" if is_flagged else "frow"
        mark = " <b>&#10005;</b>" if is_flagged else ""
        rows.append(
            f'<div class="{cls}" title="{html.escape(str(value))} &middot; {count:,} doc(s) &middot; {pct:.1f}%">'
            f'<span class="fn">{html.escape(str(value))}{mark}</span>'
            f'<span class="ft"><i style="width:{max(count / max_count * 100, 1.5):.1f}%"></i></span>'
            f'<span class="fc">{count:,}</span><span class="fp">{pct:.1f}%</span></div>'
        )
    return (
        f'<div class="card pad"><div class="head"><h2>File format</h2><b>{len(all_values)}</b></div>'
        f'<div class="frows">{"".join(rows)}</div></div>'
    )


_BUSINESS_CONTEXT_LABELS = {
    "domain": "Domain", "department": "Department", "function": "Function",
    "workflow": "Workflow", "process": "Process", "persona": "Persona",
    "role": "Role", "document_type": "Document type",
}


def _business_context_spread_html(composition: dict) -> str:
    """One compact row per business-context dimension - distinct value
    count, the share range across every value (min-max %, from the full
    "all" distribution, not just the top N), and what an evenly-split
    distribution would look like (100/distinct). At real-report scale some
    of these dimensions have 30-180+ distinct values, and a reader checking
    for balance only ever needs "how many values, how skewed is it", not
    every individual value's bar - reused as-is by the Streamlit app."""
    rows = []
    for key, label in _BUSINESS_CONTEXT_LABELS.items():
        data = composition.get(key)
        all_values = data.get("all") if data else None
        if not all_values:
            continue
        distinct = data["distinct"]
        min_pct = all_values[-1][2]
        max_pct = all_values[0][2]
        even_pct = 100.0 / distinct if distinct else 0.0
        rows.append(
            f"<tr><td>{html.escape(label)}</td><td><b>{distinct}</b></td>"
            f"<td>{min_pct:.1f}&ndash;{max_pct:.1f}%</td><td>{even_pct:.1f}%</td></tr>"
        )
    if not rows:
        return ""
    return (
        '<div class="stat-card"><div class="stat-label">Business Context Spread</div>'
        '<div class="scroll"><table class="bc-table"><tr><th>Dimension</th><th>Values</th>'
        "<th>Share range</th><th>If even</th></tr>" + "".join(rows) + "</table></div></div>"
    )


# -------------------------------------------------------------- checklist --

def _fail_group_html(title: str, items: list) -> str:
    """Every failing CheckResult sharing one title - i.e. the exact same
    check, just run against several scopes (Agreements/Positive,
    Agreements/Negative, ...) - collapsed into ONE block: one short
    simplified-fact line per scope, and the fix once (not repeated per
    scope), its own justification clause dropped too. No separate bold
    check-title line any more - check titles are phrased as the PASSING
    condition (e.g. "counts.positive matches actual delivered raw_doc file
    count"), which reads backwards/confusing printed right above a block
    that's actively showing it does NOT match; the fact line already says
    what's actually wrong, in plain numbers, without that framing. At
    real-report scale a check failing across 2-4 scopes used to print the
    same title and the same full "Suggested fix: ..." sentence that many
    times - real repeated information with nothing new beyond the one
    differing number per scope."""
    lines = []
    for r in items:
        lead, _examples = split_detail(r.detail)
        lead = lead.rstrip().rstrip(",")
        fact_full = lead or title
        fact = html.escape(_simplify_fact(fact_full, limit=150))
        scope_bit = f'<span class="row-scope">{html.escape(r.scope)}:</span> ' if r.scope and len(items) > 1 else ""
        # title= carries the untruncated fact, so a line that does get
        # shortened (a rare, genuinely long fact - several distinct numbers/
        # file names at once) is still fully readable on hover, not cut off
        # for good.
        lines.append(f'<div title="{html.escape(_truncate(fact_full))}">{scope_bit}{fact}</div>')
    fix = next((r.fix for r in items if r.fix), "")
    fix_html = (f'<div class="fix" title="{html.escape(_truncate(fix))}">Fix: {html.escape(_short_clause(fix))}</div>'
                if fix else "")
    return f'<div class="ckfail-item">{"".join(lines)}{fix_html}</div>'


def _checklist_card_html(core, additional) -> str:
    """Compact, PowerBI-dashboard-style checklist, wrapped in its own card.
    A passing category collapses to a single line (name + its total check
    count - no per-check detail, since the color already says every one of
    them passed). A failing category gets a highlighted block showing only
    its FAILING checks, never the passing ones in that same category - at
    real-report scale (hundreds of checks across dozens of categories),
    showing full detail for every PASS was "over-showing" far more than it
    helped."""
    groups = core + additional
    if not groups:
        return ""
    total_checks = sum(len(results) for _c, results in groups)
    failed_checks = sum(_fail_count(results) for _c, results in groups)

    def _section(label: str, section_groups) -> str:
        if not section_groups:
            return ""
        passed = sum(len(results) - _fail_count(results) for _c, results in section_groups)
        total = sum(len(results) for _c, results in section_groups)
        parts = [f'<div class="sublabel">{html.escape(label)} &middot; {len(section_groups)} '
                 f'group(s) &middot; {passed} / {total}</div>']
        pass_rows = []
        for category, results in section_groups:
            n_fail_cat = _fail_count(results)
            n_total = len(results)
            name = html.escape(_display_category(category))
            if n_fail_cat:
                fails = [r for r in results if r.status == Status.FAIL]
                fail_groups: dict[str, list] = {}
                fail_order: list[str] = []
                for r in fails:
                    if r.title not in fail_groups:
                        fail_groups[r.title] = []
                        fail_order.append(r.title)
                    fail_groups[r.title].append(r)
                parts.append(
                    '<div class="ckfail">'
                    '<div class="ckfail-head"><span class="mk no">&#10005;</span>'
                    f'<span class="cn"><b>{name}</b></span>'
                    f'<span class="cc">{n_total - n_fail_cat} / {n_total}</span></div>'
                    + "".join(_fail_group_html(t, fail_groups[t]) for t in fail_order)
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

    body = _section("Checklist", core) + _section(ADDITIONAL_CHECKS_SECTION_TITLE, additional)
    return (f'<div class="card"><div class="cardhead head"><h2>QC checks</h2>'
            f'<b>{total_checks - failed_checks:,} / {total_checks:,}</b></div>{body}</div>')


# ---------------------------------------------------------------- HTML ----

_PAGE_CSS = """
:root{
  --bg:#f6f3ec; --surface:#ffffff; --sunk:#efe9dd;
  --ink:#242019; --muted:#8a7f6e; --line:#e4dccb;
  --brand:#0e4c47; --on-brand:#ffffff; --on-brand-muted:#b9d9d2; --hero:#0e4c47;
  --mark:#c56a2e; --mark-soft:#ecc9a4;
  --strong:#1f7a5c; --strong-bg:#e1f1e9;
  --ok:#2b6ca3; --ok-bg:#e2edf7;
  --weak:#b5790a; --weak-bg:#faecd2;
  --crit:#b23a2e; --crit-bg:#f8e1dd;
  --neutral-bg:#ece5d5;
}
*{box-sizing:border-box}
@page{size:A4;margin:11mm 13mm}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,"Segoe UI",Roboto,Arial,sans-serif;font-size:12px;line-height:1.3}
.page{max-width:800px;margin:0 auto;padding:10px 10px 18px;display:flex;flex-direction:column;gap:6px}
h1,h2,p{margin:0}
h1{font-size:17px;line-height:1.1;letter-spacing:-.01em}
h2{font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:700}
.mono{font-family:Consolas,"Cascadia Mono",ui-monospace,Menlo,monospace;font-size:.88em}
.muted{color:var(--muted)}
.label{font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:700}
.card{background:var(--surface);border:1px solid var(--line);border-radius:11px;min-width:0;
  box-shadow:0 1px 2px rgba(36,32,25,0.05)}
.pad{padding:8px 11px;display:flex;flex-direction:column;gap:6px}
.head{display:flex;justify-content:space-between;align-items:baseline;gap:6px 10px;flex-wrap:wrap}
.head b{font-variant-numeric:tabular-nums}
.pill{display:inline-block;font-size:9px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;
  padding:2px 7px;border-radius:99px;white-space:nowrap}
.p-strong{background:var(--strong-bg);color:var(--strong)}
.p-ok{background:var(--ok-bg);color:var(--ok)}
.p-weak{background:var(--weak-bg);color:var(--weak)}
.p-crit{background:var(--crit-bg);color:var(--crit)}
.p-info{background:var(--neutral-bg);color:var(--muted)}

.band{background:linear-gradient(135deg,var(--brand),#163f3c);color:var(--on-brand);
  border-radius:11px;padding:9px 14px;
  display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 16px;align-items:center}
.band .label{color:var(--on-brand-muted)}
.band h1{color:var(--on-brand)}
.band .meta{color:var(--on-brand-muted);font-size:10.5px;margin-top:1px;display:flex;flex-wrap:wrap;gap:1px 10px}
.verdict{border-radius:7px;padding:5px 12px;text-align:center}
.verdict b{display:block;font-size:13px;line-height:1.1}
.verdict span{font-size:9.5px;font-variant-numeric:tabular-nums}

.rowA{display:grid;grid-template-columns:minmax(0,6fr) minmax(0,5fr);gap:7px}
.rowA>*,.gates>*,.two>*,.three>*{min-width:0}
.hero{padding:9px 12px;display:grid;grid-template-columns:auto minmax(0,1fr);gap:4px 16px;align-items:end}
.hero-main b{display:block;font-size:28px;line-height:.95;color:var(--hero);font-variant-numeric:tabular-nums}
.hero-main span{font-size:9.5px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--hero)}
.hero-split{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 12px}
.hero-split b{display:block;font-size:16px;line-height:1;font-variant-numeric:tabular-nums}
.hero-split span{font-size:9.5px;color:var(--muted);font-variant-numeric:tabular-nums}
.hero-foot{grid-column:1 / -1;display:flex;flex-direction:column;gap:3px}
.bar{height:5px;border-radius:99px;background:var(--mark-soft);overflow:hidden;display:flex}
.bar i{display:block;background:var(--strong)}
.hero-foot .t{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;font-size:9.5px;
  color:var(--muted);font-variant-numeric:tabular-nums}
.hero-foot b{color:var(--ink)}
.comps{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 12px}
.comps b{display:block;font-family:Consolas,"Cascadia Mono",ui-monospace,Menlo,monospace;font-size:11px;
  overflow-wrap:anywhere;line-height:1.2}
.comps b small{font-family:-apple-system,"Segoe UI",Roboto,Arial,sans-serif;font-weight:400;
  color:var(--muted);font-size:9.5px}
.comps span{color:var(--muted);font-size:9.5px}

.gates{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px}
.gate{padding:6px 10px 7px;display:flex;flex-direction:column;gap:3px;border-left:4px solid var(--strong);
  border-radius:7px 11px 11px 7px}
.gate.bad{border-left-color:var(--crit)}
.gate-fig{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap}
.gate-fig b{font-size:18px;line-height:1;font-variant-numeric:tabular-nums}
.gate-fig span{color:var(--muted);font-size:9.5px;font-variant-numeric:tabular-nums}
.dots{display:flex;flex-wrap:wrap;gap:2px}
.dot{width:11px;height:11px;border-radius:50%;display:grid;place-items:center;font-size:7px;font-weight:700}
.d-s{background:var(--strong-bg);color:var(--strong)}
.d-c{background:var(--crit-bg);color:var(--crit);outline:1.5px solid var(--crit);outline-offset:-1.5px}
.chips{display:flex;flex-wrap:wrap;gap:3px}
.chips-stack{flex-direction:column;align-items:stretch;gap:5px}
.chips-stack .chip{display:block;width:100%;text-align:left;box-sizing:border-box;padding:4px 9px}
.chip{font-size:9px;font-weight:700;padding:2px 6px;border-radius:99px;font-variant-numeric:tabular-nums;
  white-space:nowrap}
.c-s{background:var(--strong-bg);color:var(--strong)}
.c-a{background:var(--ok-bg);color:var(--ok)}
.c-w{background:var(--weak-bg);color:var(--weak)}
.c-crit{background:var(--crit-bg);color:var(--crit)}
.c-n{background:var(--neutral-bg);color:var(--muted)}

.tag{display:inline-block;font-size:8px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;
  padding:3px 6px;border-radius:5px;white-space:nowrap;text-align:center}
.t-crit{background:var(--crit-bg);color:var(--crit)}
.t-weak{background:var(--weak-bg);color:var(--weak)}
.fixrow{display:grid;grid-template-columns:58px minmax(0,1fr);gap:10px;
  align-items:center;padding:5px 11px;border-top:1px solid var(--line)}
.fr-action{font-weight:600;font-size:10.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

.two{display:flex;flex-direction:column;gap:7px}
.three{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,4fr) minmax(0,4fr);gap:7px;align-items:stretch}
.scroll{min-width:0}
table{border-collapse:collapse;width:100%;font-size:10.5px;font-variant-numeric:tabular-nums;table-layout:auto}
th,td{padding:2px 7px;text-align:left;border-top:1px solid var(--line);vertical-align:middle}
thead th{font-size:8.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
td.r,th.r{text-align:right}
td .sub{display:block;color:var(--muted);font-size:9.5px;white-space:normal;margin-top:1px}
tfoot td{font-weight:700;background:var(--sunk)}
td.acc{font-weight:700;color:var(--hero)}
.cardhead{padding:7px 11px 5px}
.ladder,.ladder-key{display:grid;grid-template-columns:repeat(4,40px);gap:2px}
.ladder span{background:var(--sunk);color:var(--muted);font-size:8px;text-align:center;padding:2px 0;
  border-radius:4px}
.ladder .on.s{background:var(--strong-bg);color:var(--strong);font-weight:700;outline:1.5px solid var(--strong);outline-offset:-1.5px}
.ladder .on.a{background:var(--ok-bg);color:var(--ok);font-weight:700;outline:1.5px solid var(--ok);outline-offset:-1.5px}
.ladder .on.w{background:var(--weak-bg);color:var(--weak);font-weight:700;outline:1.5px solid var(--weak);outline-offset:-1.5px}
.ladder .on.c{background:var(--crit-bg);color:var(--crit);font-weight:700;outline:1.5px solid var(--crit);outline-offset:-1.5px}
.ladder-key{text-align:center}

.hist{position:relative;height:62px;display:grid;gap:2px;align-items:end;border-bottom:1px solid var(--line);margin-top:9px}
.hist i{display:block;background:var(--brand);border-radius:2px 2px 0 0;min-height:2px}
.hist i.over{background:var(--mark-soft)}
.median{position:absolute;top:-9px;bottom:0;border-left:1.5px dashed var(--ink);pointer-events:none}
.median span{position:absolute;left:4px;top:-1px;font-size:8.5px;white-space:nowrap;color:var(--ink);
  font-variant-numeric:tabular-nums}
.axis{position:relative;height:12px;font-size:8.5px;color:var(--muted);font-variant-numeric:tabular-nums}
.axis span{position:absolute;top:2px;transform:translateX(-50%)}
.axis span:first-child{transform:none}
.axis span:last-child{transform:translateX(-100%)}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:5px}
.stats b{display:block;font-size:13px;line-height:1.1;font-variant-numeric:tabular-nums}
.stats span{font-size:9px;color:var(--muted)}

.frows{display:flex;flex-direction:column;gap:1px}
.frow{display:grid;grid-template-columns:74px minmax(0,1fr) 38px 34px;gap:5px;align-items:center;
  font-size:9.5px;font-variant-numeric:tabular-nums}
.fn{font-family:Consolas,"Cascadia Mono",ui-monospace,Menlo,monospace;font-size:9px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.fn b{color:var(--crit)}
.ft{height:7px;display:block;background:var(--neutral-bg);border-radius:0 3px 3px 0}
.ft i{display:block;height:100%;background:var(--brand);border-radius:0 3px 3px 0}
.fc{text-align:right}
.fp{text-align:right;color:var(--muted)}
.frow.flag .fn{color:var(--crit);font-weight:700}

.stat-card{background:var(--surface);border:1px solid var(--line);border-radius:11px;padding:7px 11px;
  box-shadow:0 1px 2px rgba(36,32,25,0.05)}
.stat-label{font-size:9.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
  font-weight:700;margin-bottom:5px}
.stat-note{font-size:9px;color:var(--muted);margin-top:5px;padding-top:5px;border-top:1px solid var(--line)}
.bc-table{margin:0}
.bc-table th,.bc-table td{padding:2px 6px;text-align:right}
.bc-table th:first-child,.bc-table td:first-child{text-align:left}

.cks{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:0 14px;padding:2px 11px 7px}
.ck{display:grid;grid-template-columns:14px minmax(0,1fr) auto;gap:5px;align-items:baseline;padding:2px 0;
  border-top:1px solid var(--line);font-size:10.5px}
.mk{width:13px;height:13px;border-radius:50%;display:grid;place-items:center;font-size:8px;font-weight:700;
  align-self:center}
.mk.ok{background:var(--strong-bg);color:var(--strong)}
.mk.no{background:var(--crit-bg);color:var(--crit)}
.cn{overflow-wrap:anywhere}
.cc{color:var(--muted);font-variant-numeric:tabular-nums}
.ckfail{background:var(--crit-bg);border-radius:7px;padding:5px 11px;margin:0 11px 5px}
.ckfail-head{display:grid;grid-template-columns:14px minmax(0,1fr) auto;gap:5px;align-items:baseline;
  font-size:10.5px}
.ckfail-head .cc{color:var(--crit);font-weight:700}
.ckfail-item{margin:4px 0 0 19px;font-size:10px}
.ckfail-item b{font-weight:700}
.row-scope{color:var(--muted);font-size:9.5px;font-weight:400}
.examples{margin:0.2rem 0 0.1rem 0;padding-left:1rem;font-size:9.5px}
.examples li{margin:0.1rem 0}
.fix{color:var(--hero);font-size:9.5px;margin-top:0.2rem}
.sublabel{padding:7px 11px 2px;font-size:8.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);
  font-weight:700;border-top:1px solid var(--line)}

.qm-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px;margin:0.5rem 0 1rem}
.qm-row{background:var(--sunk);border:1px solid var(--line);border-radius:8px;padding:9px 13px}
.qm-head{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.qm-label{font-weight:700;color:var(--hero);font-size:12px}
.qm-badge{color:white;font-size:9.5px;font-weight:700;padding:1px 7px;border-radius:4px;letter-spacing:.03em;
  white-space:nowrap}
.qm-score{margin-left:auto;font-weight:700;font-size:13px;color:var(--hero)}
.qm-detail{font-size:10.5px;color:var(--muted);margin-top:2px}
.qm-sample{opacity:0.8}

@media print{
  body{background:#fff}
  .page{max-width:none;padding:0}
  /* Avoid splitting one card/gate/row mid-content across a page break, but
     deliberately NOT on the .gates/.rowA/.two/.three row containers
     themselves - avoiding a split on the whole row (rather than each card
     in it) forced the entire row to the next page even when only one card
     in it didn't fit, leaving large blank gaps at the bottom of the
     previous page. */
  .card,.gate,.fixrow,.ckfail,.ck,.qm-row{break-inside:avoid;page-break-inside:avoid}
}
"""


def render_html(report: RunReport) -> str:
    counts = report.counts()
    n_fail = counts.get("FAIL", 0)
    gated_critical = _gated_critical_metrics(report.quality_metrics)
    headline, subline, is_failing = _verdict_text(n_fail, gated_critical)

    core, additional = _core_and_additional(report)
    stats = report.stats or {}
    composition = stats.get("composition") or {}
    format_data = composition.get("format")
    format_values = [str(v) for v, _c, _p in (format_data.get("all", []) if format_data else [])]
    flagged = _flagged_format_values(core, additional, format_values)

    cb = stats.get("corpus_breakdown") or {}
    total_documents = cb.get("total_generated") or (report.doc_counts or {}).get("combined_total") or 0
    reuse_note = _sit_value_reuse_note(core, additional, total_documents)
    two_row = "".join([
        _quality_metrics_table_card_html(report.quality_metrics),
        _accepted_by_class_card_html(cb, stats.get("label_distribution") or {}, reuse_note),
    ])
    three_row = "".join([
        _hist_card_html(stats.get("document_length") or {}),
        _format_frows_html(format_data, flagged),
        _business_context_spread_html(composition),
    ])

    body = [
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<title>QC Report - {html.escape(report.label)}</title>'
        f'<style>{_PAGE_CSS}</style></head><body><div class="page">',
        _band_header_html(report, headline, subline, is_failing),
        f'<div class="rowA">{_hero_card_html(report.doc_counts)}{_pipeline_card_html(report.generation_info)}</div>',
        _gates_summary_html(report, core, additional),
        _fix_list_html(core, additional, report.quality_metrics),
        f'<div class="two">{two_row}</div>' if two_row else "",
        f'<div class="three">{three_row}</div>' if three_row else "",
        _checklist_card_html(core, additional),
        "</div></body></html>",
    ]
    return "".join(part for part in body if part)


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
                pdf.set_fill_color(26, 112, 70)
            else:
                pdf.set_fill_color(168, 36, 36)
            pdf.rect(x, y, fill_w, height, style="F")
        pdf.set_xy(x, y + height + 1.5)

    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 8, "SIT Output Quality Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(86, 99, 125)
    pdf.multi_cell(0, 5, _clean(report.label), new_x="LMARGIN", new_y="NEXT")
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
        pdf.set_text_color(31, 58, 104)
        pdf.multi_cell(0, 5, _clean("  |  ".join(info_bits)), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    counts = report.counts()
    n_fail = counts.get("FAIL", 0)
    gated_critical = _gated_critical_metrics(report.quality_metrics)
    headline, subline, is_failing = _verdict_text(n_fail, gated_critical)
    color = (168, 36, 36) if is_failing else (26, 112, 70)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*color)
    pdf.multi_cell(0, 7, _clean(headline), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, _clean(subline), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(1)

    core, additional = _core_and_additional(report)

    groups = core + additional
    if groups:
        total_checks = sum(len(results) for _c, results in groups)
        failed_checks = sum(_fail_count(results) for _c, results in groups)
        n_fail_groups = sum(1 for _c, results in groups if _fail_count(results))
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(0, 5, _clean(
            f"QC Checks: {total_checks - failed_checks:,} / {total_checks:,} "
            f"({len(groups) - n_fail_groups} of {len(groups)} groups passing)"
        ), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
    qm_scored = [m for m in (report.quality_metrics or {}).values() if m.get("score") is not None]
    if qm_scored:
        pdf.multi_cell(0, 5, _clean(
            "Quality Metrics: " + "; ".join(f"{m['label']} {m['score']:.3f}" for m in qm_scored)
        ), new_x="LMARGIN", new_y="NEXT")
    mce = (report.stats or {}).get("mce_coverage") or {}
    if mce.get("checked"):
        pdf.multi_cell(0, 5, _clean(
            f"MCE Detection: {_pct_str(mce.get('pct'))} ({mce.get('detected', 0):,} / {mce.get('checked', 0):,})"
        ), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    fails = [(category, r) for category, results in core + additional
             for r in results if r.status == Status.FAIL]
    if fails:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(31, 58, 104)
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
        pdf.set_fill_color(31, 58, 104)
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
    format_data = composition.get("format")
    if format_data and format_data.get("top"):
        pdf.set_font("Helvetica", "B", 13)
        pdf.multi_cell(0, 7, "File format", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        all_values = format_data.get("all", format_data["top"])
        max_count = max((c for _v, c, _p in all_values), default=0) or 1
        pdf.set_fill_color(31, 58, 104)
        for value, count, pct in all_values:
            x, y = pdf.get_x(), pdf.get_y()
            pdf.cell(45, 4.2, _clean(_truncate(str(value), 40)))
            pdf.rect(x + 45, y, 70 * count / max_count, 4.2, style="F")
            pdf.set_xy(x + 45 + 72, y)
            pdf.cell(0, 4.2, f"{count} ({pct:.1f}%)", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    bc_rows = []
    for key, label in _BUSINESS_CONTEXT_LABELS.items():
        data = composition.get(key)
        all_values = data.get("all") if data else None
        if not all_values:
            continue
        distinct = data["distinct"]
        bc_rows.append((label, distinct, all_values[-1][2], all_values[0][2], 100.0 / distinct))
    if bc_rows:
        pdf.set_font("Helvetica", "B", 13)
        pdf.multi_cell(0, 7, "Business Context Spread", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        for label, distinct, min_pct, max_pct, even_pct in bc_rows:
            pdf.multi_cell(0, 4.5, _clean(
                f"{label}: {distinct} value(s), share {min_pct:.1f}-{max_pct:.1f}% (even = {even_pct:.1f}%)"
            ), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    def _checklist_block(label: str, groups) -> None:
        """One line per category: a pass is just "[OK] Name  N" (no
        per-check detail - the color already says every one of them
        passed); a fail shows its count plus the failing checks only,
        never the passing ones in that same category. Mirrors
        _checklist_card_html's HTML/Streamlit behavior."""
        if not groups:
            return
        passed = sum(len(results) - _fail_count(results) for _c, results in groups)
        total = sum(len(results) for _c, results in groups)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(31, 58, 104)
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
            pdf.set_text_color(168, 36, 36)
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
