"""Check registry: modules self-register their check functions here.

To add a new check: write ``def check_xxx(ctx: VersionContext, options: dict) ->
list[CheckResult]`` anywhere under ``qc/checks/`` and decorate it with
``@register(category="...")``. Nothing else needs to change - ``run_all``
discovers and imports every module in ``qc/checks/`` automatically.
"""

from __future__ import annotations

import importlib
import pkgutil
import time
import traceback
from typing import Callable

from qc import file_cache
from qc import jsonio
from qc.context import VersionContext
from qc.models import CheckResult, RunReport, Status
from qc.progress import Reporter, null_reporter

_CHECKS: list[tuple[str, Callable]] = []
_LOADED = False


def register(category: str):
    def deco(fn: Callable):
        _CHECKS.append((category, fn))
        return fn
    return deco


def _load_check_modules() -> None:
    global _LOADED
    if _LOADED:
        return
    import qc.checks as checks_pkg

    for _, name, _ in pkgutil.iter_modules(checks_pkg.__path__, checks_pkg.__name__ + "."):
        importlib.import_module(name)
    _LOADED = True


def run_all(ctx: VersionContext, options: dict | None = None,
            report_progress: Reporter = null_reporter) -> RunReport:
    _load_check_modules()
    options = options or {}
    file_cache.clear()  # never reuse a listing across separate runs/reruns
    jsonio.clear_jsonl_cache()
    jsonio.clear_json_cache()
    report = RunReport(label=ctx.label, version_dir=str(ctx.version_dir))
    report_progress(f"Starting checks for: {ctx.label} ({len(_CHECKS)} check functions registered)")
    for i, (category, fn) in enumerate(_CHECKS, start=1):
        report_progress(f"[{i}/{len(_CHECKS)}] Running {fn.__module__}.{fn.__name__} ({category}) ...")
        start = time.monotonic()
        try:
            results = fn(ctx, options) or []
        except Exception as exc:  # a broken check must not kill the whole run
            results = [
                CheckResult(
                    status=Status.FAIL,
                    category=category,
                    item_ref="internal",
                    title=f"Check crashed: {fn.__name__}",
                    detail=f"{exc}\n{traceback.format_exc(limit=3)}",
                    scope="",
                    fix="This is a bug in the checker itself - please report it.",
                )
            ]
            report_progress(f"  -> CRASHED: {exc}")
        elapsed = time.monotonic() - start
        report_progress(f"  -> done in {elapsed:.2f}s, {len(results)} result(s)"
                         + (" [SLOW]" if elapsed > 5 else ""))
        report.add(results)
    report_progress(f"All checks finished for: {ctx.label}")
    return report
