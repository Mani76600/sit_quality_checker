"""Render a RunReport as a shareable, self-contained HTML page or a PDF -
alongside the existing raw JSON export, for reviewing/circulating results
outside the Streamlit app itself. Both formats lead with the same compact
"checklist at a glance" summary as the on-screen report, then group details
by category with passed checks condensed and only FAIL/WARN/INFO spelled
out - the same "glanceable, not repetitive" design as the UI.
"""

from __future__ import annotations

import html
import re
from collections import Counter

from qc.detail_format import split_detail
from qc.models import RunReport, Status

STATUS_ORDER = [Status.FAIL.value, Status.WARN.value, Status.INFO.value, Status.PASS.value]
STATUS_COLOR = {
    Status.PASS.value: "#1a7f37",
    Status.FAIL.value: "#cf222e",
    Status.WARN.value: "#9a6700",
    Status.INFO.value: "#0969da",
}
STATUS_ICON = {
    Status.PASS.value: "PASS",
    Status.FAIL.value: "FAIL",
    Status.WARN.value: "WARN",
    Status.INFO.value: "INFO",
}


def _category_sort_key(category: str):
    m = re.match(r"^(\d+)", category)
    return (int(m.group(1)) if m else 999, category)


def _grouped(report: RunReport):
    grouped = report.by_category()
    return sorted(grouped.items(), key=lambda kv: _category_sort_key(kv[0]))


# ---------------------------------------------------------------- HTML ----

def render_html(report: RunReport) -> str:
    counts = report.counts()
    n_fail, n_warn = counts.get("FAIL", 0), counts.get("WARN", 0)
    if n_fail:
        verdict = ("FAILING", f"{n_fail} check(s) failed - needs fixes before this output ships.")
    elif n_warn:
        verdict = ("REVIEW", f"All mandatory checks passed, but {n_warn} item(s) need a human look.")
    else:
        verdict = ("CLEAN", "Every quality check passed for this run.")

    parts = [f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>QC Report - {html.escape(report.label)}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 2rem;
          color: #1f2328; background: #ffffff; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 0.2rem; }}
  .path {{ color: #57606a; font-size: 0.85rem; margin-bottom: 1rem; }}
  .verdict {{ display: inline-block; padding: 0.5rem 1rem; border-radius: 6px; font-weight: 600;
              margin-bottom: 1rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 1.5rem; }}
  th, td {{ border: 1px solid #d0d7de; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.9rem;
            vertical-align: top; }}
  th {{ background: #f6f8fa; }}
  .badge {{ display: inline-block; padding: 0.1rem 0.5rem; border-radius: 4px; color: white;
            font-size: 0.75rem; font-weight: 600; }}
  .cat {{ margin-top: 1.5rem; }}
  .cat h2 {{ font-size: 1.05rem; border-bottom: 2px solid #d0d7de; padding-bottom: 0.3rem; }}
  .result {{ margin: 0.6rem 0; padding: 0.5rem 0.7rem; border-left: 4px solid #d0d7de;
             background: #f6f8fa; }}
  .result.FAIL {{ border-left-color: {STATUS_COLOR["FAIL"]}; }}
  .result.WARN {{ border-left-color: {STATUS_COLOR["WARN"]}; }}
  .result.INFO {{ border-left-color: {STATUS_COLOR["INFO"]}; }}
  .fix {{ color: #0969da; font-size: 0.85rem; margin-top: 0.3rem; }}
  .examples {{ margin: 0.3rem 0 0.2rem 0; padding-left: 1.3rem; font-size: 0.88rem; }}
  .examples li {{ margin: 0.15rem 0; }}
  .passed-summary {{ font-size: 0.85rem; color: #57606a; margin-bottom: 0.3rem; }}
  .passed-list {{ font-size: 0.85rem; color: #57606a; columns: 2; column-gap: 1.5rem;
                  padding-left: 1.2rem; }}
  .passed-list li {{ margin: 0.15rem 0; break-inside: avoid; }}
</style></head><body>
<h1>SIT Output Quality Report</h1>
<div class="path"><strong>{html.escape(report.label)}</strong><br>{html.escape(report.version_dir)}</div>
<div class="verdict" style="background:{STATUS_COLOR['FAIL'] if n_fail else (STATUS_COLOR['WARN'] if n_warn else STATUS_COLOR['PASS'])};color:white;">
  {verdict[0]}: {html.escape(verdict[1])}
</div>
<table><tr><th>PASS</th><th>FAIL</th><th>WARN</th><th>INFO</th></tr>
<tr><td>{counts.get("PASS", 0)}</td><td>{counts.get("FAIL", 0)}</td>
<td>{counts.get("WARN", 0)}</td><td>{counts.get("INFO", 0)}</td></tr></table>

<h2>Checklist at a glance</h2>
<table><tr><th>Status</th><th>Checklist item</th><th>Result</th></tr>
"""]

    for category, results in _grouped(report):
        cat_counts = Counter(r.status.value for r in results)
        worst = next((s for s in STATUS_ORDER if cat_counts[s]), Status.PASS.value)
        total = len(results)
        bits = [f"{cat_counts['PASS']}/{total} passed"]
        if cat_counts["FAIL"]:
            bits.append(f"{cat_counts['FAIL']} FAILED")
        if cat_counts["WARN"]:
            bits.append(f"{cat_counts['WARN']} warning(s)")
        parts.append(
            f'<tr><td><span class="badge" style="background:{STATUS_COLOR[worst]}">'
            f'{STATUS_ICON[worst]}</span></td><td>{html.escape(category)}</td>'
            f"<td>{html.escape(', '.join(bits))}</td></tr>\n")
    parts.append("</table>\n")

    for category, results in _grouped(report):
        parts.append(f'<div class="cat"><h2>{html.escape(category)}</h2>\n')
        actionable = [r for r in results if r.status != Status.PASS]
        passed = [r for r in results if r.status == Status.PASS]
        for r in actionable:
            scope_html = f" &mdash; <em>{html.escape(r.scope)}</em>" if r.scope else ""
            lead, examples = split_detail(r.detail)
            detail_html = f"<div>{html.escape(lead)}</div>"
            if examples:
                detail_html += '<ul class="examples">' + "".join(
                    f"<li>{html.escape(item)}</li>" for item in examples) + "</ul>"
            parts.append(
                f'<div class="result {r.status.value}">'
                f'<span class="badge" style="background:{STATUS_COLOR[r.status.value]}">'
                f'{STATUS_ICON[r.status.value]}</span> '
                f"<strong>{html.escape(r.title)}</strong>{scope_html}"
                f"{detail_html}"
                + (f'<div class="fix">Suggested fix: {html.escape(r.fix)}</div>' if r.fix else "")
                + "</div>\n")
        if passed:
            parts.append(f'<div class="passed-summary">{len(passed)} passed check(s):</div>\n')
            parts.append('<ul class="passed-list">')
            for p in passed:
                title_html = html.escape(p.title)
                if p.scope:
                    title_html += f' <span style="color:#8b949e">({html.escape(p.scope)})</span>'
                parts.append(f"<li>{title_html}</li>")
            parts.append("</ul>\n")
        parts.append("</div>\n")

    parts.append("</body></html>")
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

    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 8, "SIT Output Quality Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(87, 96, 106)
    pdf.multi_cell(0, 5, _clean(report.label), new_x="LMARGIN", new_y="NEXT")
    pdf.multi_cell(0, 5, _clean(report.version_dir), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    counts = report.counts()
    n_fail, n_warn = counts.get("FAIL", 0), counts.get("WARN", 0)
    if n_fail:
        verdict = f"FAILING - {n_fail} check(s) failed, needs fixes before this output ships."
        color = (207, 34, 46)
    elif n_warn:
        verdict = f"REVIEW - all mandatory checks passed, but {n_warn} item(s) need a human look."
        color = (154, 103, 0)
    else:
        verdict = "CLEAN - every quality check passed for this run."
        color = (26, 127, 55)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*color)
    pdf.multi_cell(0, 7, _clean(verdict), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, f"PASS: {counts.get('PASS', 0)}   FAIL: {counts.get('FAIL', 0)}   "
                         f"WARN: {counts.get('WARN', 0)}   INFO: {counts.get('INFO', 0)}",
                   new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 13)
    pdf.multi_cell(0, 7, "Checklist at a glance", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for category, results in _grouped(report):
        cat_counts = Counter(r.status.value for r in results)
        total = len(results)
        bits = [f"{cat_counts['PASS']}/{total} passed"]
        if cat_counts["FAIL"]:
            bits.append(f"{cat_counts['FAIL']} FAILED")
        if cat_counts["WARN"]:
            bits.append(f"{cat_counts['WARN']} warning(s)")
        worst = next((s for s in STATUS_ORDER if cat_counts[s]), Status.PASS.value)
        pdf.multi_cell(0, 5, _clean(f"[{worst}] {category}: {', '.join(bits)}"),
                        new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    for category, results in _grouped(report):
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(0, 7, _clean(category), new_x="LMARGIN", new_y="NEXT")
        actionable = [r for r in results if r.status != Status.PASS]
        passed = [r for r in results if r.status == Status.PASS]
        pdf.set_font("Helvetica", "", 9)
        for r in actionable:
            pdf.set_font("Helvetica", "B", 9)
            scope_note = f" ({r.scope})" if r.scope else ""
            pdf.multi_cell(0, 5, _clean(f"[{r.status.value}] {r.title}{scope_note}"),
                            new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9)
            lead, examples = split_detail(r.detail)
            pdf.multi_cell(0, 5, _clean(lead), new_x="LMARGIN", new_y="NEXT")
            for item in examples:
                pdf.multi_cell(0, 5, _clean(f"    - {item}"), new_x="LMARGIN", new_y="NEXT")
            if r.fix:
                pdf.set_font("Helvetica", "I", 9)
                pdf.multi_cell(0, 5, _clean(f"Suggested fix: {r.fix}"), new_x="LMARGIN", new_y="NEXT")
                pdf.set_font("Helvetica", "", 9)
            pdf.ln(1)
        if passed:
            pdf.set_font("Helvetica", "I", 8)
            pdf.multi_cell(0, 4, _clean(f"{len(passed)} passed check(s):"),
                            new_x="LMARGIN", new_y="NEXT")
            for p in passed:
                label = f"    - {p.title} ({p.scope})" if p.scope else f"    - {p.title}"
                pdf.multi_cell(0, 4, _clean(label), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    return bytes(pdf.output())
