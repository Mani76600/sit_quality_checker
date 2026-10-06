"""Which checklist categories were added in this round of work, kept in
their own visually separate section ("Default Checklist") in both the
Streamlit app and the HTML/PDF export, distinct from this tool's
longer-standing original checklist - with no explanation of where they came
from in the UI itself (no "external"/"file"/"review" wording anywhere;
that context lives in conversation history and code comments, not the
user-facing report). Dependency-free (no qc.registry import) so both
qc/report_render.py and qc/streamlit_report.py can import it without
re-triggering the checks/*.py <-> qc.registry circular-import rule.

Only whole NEW categories are listed here - categories that already existed
and were merely extended with additional checks this same round (e.g.
"5. export_summary.json ground truth" gained a counts-vs-real-files
reconciliation check) are intentionally NOT listed, since the category
itself isn't new, just improved.
"""

from __future__ import annotations

ADDITIONAL_CHECK_CATEGORIES = {
    "Folder/File Hygiene",
    "Metadata Schema Self-Consistency",
    "combined_metadata.jsonl vs Individual metadata.json Join",
    "Instance Value Offset Accuracy",
    "Context Detection Row Join",
}

ADDITIONAL_CHECKS_SECTION_TITLE = "Default Checklist"
