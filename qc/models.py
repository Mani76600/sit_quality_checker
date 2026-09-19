"""Result/report data model shared by every check module."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    INFO = "INFO"


# Order used for sorting / severity ranking (lower = more severe).
_SEVERITY_ORDER = {Status.FAIL: 0, Status.WARN: 1, Status.INFO: 2, Status.PASS: 3}


@dataclass
class CheckResult:
    """One evaluated check against one scope (e.g. Agreements / Disagreements)."""

    status: Status
    category: str
    item_ref: str
    title: str
    detail: str
    scope: str = ""
    fix: str = ""
    evidence: list[str] = field(default_factory=list)

    def severity(self) -> int:
        return _SEVERITY_ORDER.get(self.status, 9)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "category": self.category,
            "item_ref": self.item_ref,
            "title": self.title,
            "detail": self.detail,
            "scope": self.scope,
            "fix": self.fix,
            "evidence": self.evidence,
        }


@dataclass
class RunReport:
    """All results for one Version_* directory."""

    label: str  # human readable identifier, e.g. "Sweden National ID / Swedish / Version_20260915_2135"
    version_dir: str
    results: list[CheckResult] = field(default_factory=list)

    def add(self, results: list[CheckResult] | CheckResult) -> None:
        if isinstance(results, CheckResult):
            results = [results]
        self.results.extend(results)

    def counts(self) -> dict:
        out = {s.value: 0 for s in Status}
        for r in self.results:
            out[r.status.value] += 1
        return out

    def by_category(self) -> dict[str, list[CheckResult]]:
        grouped: dict[str, list[CheckResult]] = {}
        for r in self.results:
            grouped.setdefault(r.category, []).append(r)
        for cat in grouped:
            grouped[cat].sort(key=lambda r: r.severity())
        return grouped

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "version_dir": self.version_dir,
            "counts": self.counts(),
            "results": [r.to_dict() for r in self.results],
        }
