"""Split a CheckResult's free-text detail into a lead sentence plus a clean
list of example items, when it ends in the "...e.g. ['a', 'b']" pattern
every check module uses. Every renderer (Streamlit UI, HTML export, PDF
export) shares this so a bracketed Python list-repr never gets shown
verbatim to the user - it becomes a real bullet list instead.
"""

from __future__ import annotations

import ast
import re

_EXAMPLES_RE = re.compile(r"^(?P<prefix>.*?e\.g\.\s*)(?P<list>\[.*\])\s*$", re.DOTALL)


def split_detail(detail: str) -> tuple[str, list[str]]:
    """Return (lead_text, example_items). example_items is [] when the
    detail doesn't end in a recognizable "e.g. [...]" list."""
    if not detail:
        return detail, []
    m = _EXAMPLES_RE.match(detail)
    if not m:
        return detail, []
    try:
        items = ast.literal_eval(m.group("list"))
    except (ValueError, SyntaxError):
        return detail, []
    if not isinstance(items, list):
        return detail, []
    lead = m.group("prefix").rstrip()
    if lead.endswith("e.g."):
        lead = lead[: -len("e.g.")].rstrip()
    return lead, [str(item) for item in items]
