"""MCE detection coverage: what fraction of delivered documents actually
have a real MCE detection recorded (at least one parsed chunk snippet with
sit_found == True), exhaustive across every document in both Agreements and
Disagreements - never sampled, since this is specifically the number meant
to answer "did MCE actually run on everything or not" with no margin for
approximation. The raw numbers are computed once in qc.stats (shared with
the headline stat card qc.registry.run_all attaches to RunReport.stats);
this module turns that same computation into a PASS/FAIL checklist item so
it also appears in the "Checklist at a glance" table like every other check.
"""

from __future__ import annotations

from qc.context import VersionContext
from qc.models import CheckResult, Status
from qc.registry import register
from qc.stats import compute_mce_coverage

CATEGORY = "MCE Detection Coverage"
ITEM_REF = "addl"


@register(category=CATEGORY)
def check_mce_detection_coverage(ctx: VersionContext, options: dict) -> list[CheckResult]:
    coverage = compute_mce_coverage(ctx)
    results: list[CheckResult] = []
    for polarity_name in ("positive", "negative"):
        p = coverage[polarity_name]
        if p["checked"] == 0:
            continue
        detail = f"{p['detected']}/{p['checked']} doc(s) have an MCE detection ({p['pct']:.1f}%)"
        if p["detected"] == p["checked"]:
            results.append(CheckResult(Status.PASS, CATEGORY, ITEM_REF,
                f"every {polarity_name} document has an MCE detection", detail))
        else:
            missing = p["checked"] - p["detected"]
            results.append(CheckResult(Status.FAIL, CATEGORY, ITEM_REF,
                f"every {polarity_name} document has an MCE detection", detail, "",
                f"{missing} {polarity_name} document(s) were delivered with no chunk snippet "
                "carrying sit_found == true - MCE never detected the planted/expected value "
                "for them. Investigate the SITGrader/MCE step for these documents."))
    return results
