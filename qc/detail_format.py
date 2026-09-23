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

# Every check module accumulates several example items (5-20, depending on
# the check) for its own diagnostic purposes, but showing them all as
# bullets is mostly repetition once the lead sentence already states the
# real total count (e.g. "2080 record(s) have a non-numeric confidence
# value") - one representative example is enough for a user to understand
# *what* the problem looks like. Capped once, here, since every renderer
# (Streamlit UI, HTML export, PDF export) shares this single function.
MAX_EXAMPLES_SHOWN = 1


def split_detail(detail: str) -> tuple[str, list[str]]:
    """Return (lead_text, example_items). example_items is [] when the
    detail doesn't end in a recognizable "e.g. [...]" list; otherwise it's
    capped to MAX_EXAMPLES_SHOWN items regardless of how many the check
    itself collected."""
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
    return lead, [str(item) for item in items[:MAX_EXAMPLES_SHOWN]]
