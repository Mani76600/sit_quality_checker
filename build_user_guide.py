"""One-time authoring script that generates User_Guide.docx - a plain-
language, step-by-step guide for end users of the web app. Not a runtime
dependency of app.py; re-run this manually whenever the guide's content
needs updating, then it's served as a static file via a download button.

Usage:
    pip install python-docx
    python build_user_guide.py
"""

from __future__ import annotations

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

BRAND_DARK = RGBColor(0x1F, 0x23, 0x28)
BRAND_BLUE = RGBColor(0x09, 0x69, 0xDA)
BRAND_GREEN = RGBColor(0x1A, 0x7F, 0x37)
BRAND_RED = RGBColor(0xCF, 0x22, 0x2E)
BRAND_AMBER = RGBColor(0x9A, 0x67, 0x00)
BRAND_GREY = RGBColor(0x57, 0x60, 0x6A)


def set_cell_shading(cell, hex_color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.makeelement(qn("w:shd"), {
        qn("w:val"): "clear", qn("w:color"): "auto", qn("w:fill"): hex_color,
    })
    tc_pr.append(shd)


def add_title_page(doc: Document) -> None:
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("SIT Output Quality Checker")
    run.font.size = Pt(30)
    run.font.bold = True
    run.font.color.rgb = BRAND_DARK

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run("User Guide - Web Version")
    run.font.size = Pt(16)
    run.font.color.rgb = BRAND_GREY

    doc.add_paragraph()
    intro = doc.add_paragraph()
    intro.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = intro.add_run(
        "A step-by-step guide to checking your SIT pipeline output for quality "
        "issues, using nothing but a web browser."
    )
    run.font.size = Pt(12)
    run.font.italic = True
    run.font.color.rgb = BRAND_GREY

    doc.add_page_break()


def add_heading(doc: Document, text: str, level: int = 1):
    h = doc.add_heading(level=level)
    run = h.add_run(text)
    run.font.color.rgb = BRAND_DARK
    return h


def add_body(doc: Document, text: str, bold: bool = False) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.size = Pt(11)
    run.font.bold = bold
    p.paragraph_format.space_after = Pt(8)


def add_bullet(doc: Document, text: str) -> None:
    p = doc.add_paragraph(style="List Bullet")
    run = p.add_run(text)
    run.font.size = Pt(11)


def add_numbered_step(doc: Document, title: str, detail: str) -> None:
    p = doc.add_paragraph(style="List Number")
    run = p.add_run(title)
    run.font.bold = True
    run.font.size = Pt(11.5)
    if detail:
        p2 = doc.add_paragraph()
        p2.paragraph_format.left_indent = Inches(0.35)
        run2 = p2.add_run(detail)
        run2.font.size = Pt(10.5)
        run2.font.color.rgb = BRAND_GREY


def add_note(doc: Document, label: str, text: str, color: RGBColor) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(10)
    run = p.add_run(f"{label}  ")
    run.font.bold = True
    run.font.color.rgb = color
    run2 = p.add_run(text)
    run2.font.size = Pt(10.5)
    run2.font.color.rgb = BRAND_DARK


def build() -> Document:
    doc = Document()

    # Base font
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    add_title_page(doc)

    # ---------------------------------------------------------------- TOC
    add_heading(doc, "Contents", level=1)
    for section in [
        "1. What is this tool?",
        "2. Before you start",
        "3. Step-by-step: Running a quality check",
        "4. Understanding your results",
        "5. Downloading your report",
        "6. Reference Completeness (optional tool)",
        "7. Frequently asked questions",
        "8. Getting help",
    ]:
        add_bullet(doc, section)
    doc.add_page_break()

    # ---------------------------------------------------------- 1. WHAT IS
    add_heading(doc, "1. What is this tool?", level=1)
    add_body(doc,
        "The SIT Output Quality Checker automatically reviews the output of the SIT "
        "(Sensitive Information Type) test-data pipeline and tells you exactly what "
        "passed, what failed, and what needs a second look - checking file structure, "
        "document counts, labels, and dozens of other details that would otherwise "
        "have to be checked by hand.")
    add_body(doc,
        "This is the web version: instead of installing anything, you simply upload "
        "your output folder (compressed into a single archive file) through your "
        "browser, and the tool checks it for you.")

    # ---------------------------------------------------------- 2. BEFORE
    add_heading(doc, "2. Before you start", level=1)
    add_body(doc, "You will need:", bold=True)
    add_bullet(doc, "A web browser (Chrome, Edge, or Firefox all work fine).")
    add_bullet(doc, "The link to the tool, provided by your team.")
    add_bullet(doc, "Your SIT output folder, compressed into a single archive file.")

    add_body(doc, "")
    add_body(doc, "Supported archive formats:", bold=True)
    add_bullet(doc, ".zip")
    add_bullet(doc, ".7z")
    add_bullet(doc, ".tar, .tar.gz, .tgz, .tar.bz2")
    add_note(doc, "Not supported:",
             ".rar files cannot be used - please re-compress your folder as .zip or "
             ".7z instead.", BRAND_AMBER)

    add_body(doc, "")
    add_body(doc, "How to compress your output folder:", bold=True)
    add_numbered_step(doc, "Find your output folder",
        "This is the folder named after your SIT (e.g. \"Taiwan Passport Number\") "
        "that contains the Version_YYYYMMDD_HHMM folder(s).")
    add_numbered_step(doc, "Right-click the folder",
        "On Windows: choose \"Compress to ZIP file\" (or use 7-Zip / WinRAR if you have "
        "them installed and prefer .7z).")
    add_numbered_step(doc, "Wait for it to finish",
        "Large folders with many documents can take several minutes to compress - "
        "this is normal.")

    # ------------------------------------------------------- 3. STEP-BY-STEP
    add_heading(doc, "3. Step-by-step: Running a quality check", level=1)

    add_numbered_step(doc, "Open the tool",
        "Go to the web address provided by your team. You'll see two tabs: "
        "\"Run Checks\" and \"Reference Completeness\". Stay on \"Run Checks\".")
    add_numbered_step(doc, "Upload your archive",
        "In the left-hand sidebar, click \"Browse files\" under \"Output folder "
        "archive\" and select the .zip/.7z file you prepared in Step 2 above.")
    add_numbered_step(doc, "(Optional) Adjust settings",
        "Most users can leave these as-is:\n"
        "  - Ratio threshold: how many context records are expected per document "
        "(default is fine for most SITs).\n"
        "  - Exhaustive scan: checks every single file instead of a representative "
        "sample. Turn this on only if you need a fully thorough check and don't "
        "mind waiting longer.\n"
        "  - Sample size: how many documents to check per test when not running "
        "exhaustively (200 is a good default).")
    add_numbered_step(doc, "Click \"Run Checks\"",
        "The tool will first extract your archive (this can take a few minutes for "
        "large folders), then discover your output run(s), then run every check. "
        "You'll see live progress messages the whole time - it hasn't frozen, it's "
        "working through your files.")
    add_numbered_step(doc, "If more than one run is found",
        "A dropdown will let you either view all of them together or pick one "
        "specific run to inspect.")
    add_numbered_step(doc, "Review your results",
        "See Section 4 below for how to read what you get back.")

    add_note(doc, "How long does this take?",
             "Mostly driven by how many individual files are inside your archive, "
             "not its total size. A folder with about 75,000 files took roughly "
             "5 minutes end-to-end in our own testing. Larger folders will take "
             "longer - this is expected, not a malfunction.", BRAND_BLUE)

    # ------------------------------------------------------ 4. UNDERSTANDING
    add_heading(doc, "4. Understanding your results", level=1)
    add_body(doc,
        "At the top of your report you'll see one of three verdicts, followed by a "
        "\"Checklist at a glance\" table summarizing every category, then full "
        "details further down.")

    table = doc.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text = "Status"
    hdr[1].text = "What it means"
    rows_data = [
        ("PASS", "This check succeeded - nothing to do.", BRAND_GREEN),
        ("FAIL", "A real problem was found. Each FAIL includes a \"Suggested fix\" "
                 "explaining what to do about it.", BRAND_RED),
        ("WARN", "Not necessarily wrong, but worth a look before you rely on "
                 "this output.", BRAND_AMBER),
        ("INFO", "Background information - not a problem, just useful context "
                 "(for example, which SIT name was detected).", BRAND_BLUE),
    ]
    for label, meaning, color in rows_data:
        row = table.add_row().cells
        run = row[0].paragraphs[0].add_run(label)
        run.font.bold = True
        run.font.color.rgb = color
        row[1].text = meaning

    doc.add_paragraph()
    add_body(doc,
        "Click on any category (like \"3. context_output_normalized\" or \"8. "
        "Metadata / Engine Match / Chunk Labels\") to expand it and see every "
        "individual result. Categories with a FAIL or WARN open automatically so "
        "you don't have to hunt for them.")
    add_body(doc,
        "For any FAIL or WARN, read the detail text and the \"Suggested fix\" line - "
        "together they tell you exactly what's wrong and what to do about it.")

    # ------------------------------------------------------- 5. DOWNLOADING
    add_heading(doc, "5. Downloading your report", level=1)
    add_body(doc, "Three download buttons appear above the detailed results:")
    add_bullet(doc, "JSON - for feeding into another tool or script.")
    add_bullet(doc, "HTML - a shareable web page you can send to a colleague or open "
                    "in any browser, with the same clean formatting you see on screen.")
    add_bullet(doc, "PDF - for printing or attaching to an email/ticket.")
    add_body(doc,
        "When you're finished, click \"Clear uploaded data\" in the sidebar. This "
        "removes your uploaded files from the server - good practice since SIT test "
        "data is designed to look like real sensitive information.")

    # -------------------------------------------------- 6. REFERENCE TAB
    add_heading(doc, "6. Reference Completeness (optional tool)", level=1)
    add_body(doc,
        "This second tab is a separate, optional feature - it checks your reference "
        "material (not any specific output run) and looks up whether a particular "
        "SIT and language are covered.")
    add_numbered_step(doc, "Upload your sit_packs archive",
        "Required for this tab. Compress your sit_packs folder the same way as "
        "Section 2 above.")
    add_numbered_step(doc, "(Optional) Upload SIT Specs and/or the 213 MCE keywords file",
        "These improve the accuracy of the SIT + language lookup below, but aren't "
        "required.")
    add_numbered_step(doc, "Look up a specific SIT + language",
        "Type a SIT name and a language, then click \"Check this SIT + language\". "
        "If it can't be confirmed, you'll be given a link to the master specs "
        "reference to check manually.")
    add_note(doc, "Note:",
             "This matching is best-effort, not authoritative - treat a \"not found\" "
             "result as a prompt to double-check by hand, not a final answer.",
             BRAND_GREY)

    # -------------------------------------------------------------- 7. FAQ
    add_heading(doc, "7. Frequently asked questions", level=1)

    faqs = [
        ("The tool says \"No Version_YYYYMMDD_HHMM directories found\" - what does "
         "that mean?",
         "The tool couldn't find a recognizable output run inside your archive. "
         "Double check you compressed the correct folder (the one containing your "
         "Version_... folder, at any level inside the archive)."),
        ("I got an error about an \"unsafe path\" in my archive.",
         "This is a safety check that rejects archives containing files that try to "
         "write outside the expected folder. This should never happen with a "
         "normal, honestly-created archive - if you see it, try re-compressing the "
         "folder from scratch."),
        ("Is my uploaded data safe?",
         "Your file is extracted to a private, temporary area on the server only "
         "for your session, and isn't shared with other users. Use \"Clear uploaded "
         "data\" when you're done. If your organization has policies against "
         "uploading this kind of data anywhere, ask your team about the desktop "
         "version instead, which never sends your data anywhere."),
        ("Why is it taking so long for my large folder?",
         "Processing time depends mainly on how many individual files are inside "
         "your archive, not its total size in gigabytes. This is expected - the "
         "tool always runs the complete check against your real data rather than a "
         "shortened version."),
        ("Can I upload a plain folder instead of a zip file?",
         "Not currently - browsers don't support uploading a whole folder structure "
         "reliably, and for folders with tens of thousands of files, one compressed "
         "archive is actually faster and more reliable than uploading many "
         "individual files."),
    ]
    for q, a in faqs:
        p = doc.add_paragraph()
        run = p.add_run("Q: " + q)
        run.font.bold = True
        run.font.size = Pt(11)
        p2 = doc.add_paragraph()
        p2.paragraph_format.left_indent = Inches(0.25)
        p2.paragraph_format.space_after = Pt(10)
        run2 = p2.add_run("A: " + a)
        run2.font.size = Pt(10.5)

    # ---------------------------------------------------------- 8. HELP
    add_heading(doc, "8. Getting help", level=1)
    add_body(doc,
        "If something doesn't behave as described in this guide, note down what "
        "you uploaded, what you clicked, and what you saw on screen, then reach out "
        "to your team for support.")

    return doc


if __name__ == "__main__":
    document = build()
    document.save("User_Guide.docx")
    print("Saved User_Guide.docx")
