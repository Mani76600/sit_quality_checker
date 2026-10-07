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
from qc import lang_names
from qc import quality_metrics as qc_quality
from qc import stats as qc_stats
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


def _folder_set_counts(fs) -> dict:
    data, err = jsonio.read_json_cached(fs.export_summary)
    counts = data.get("counts", {}) if not err and isinstance(data, dict) else {}
    counts = counts if isinstance(counts, dict) else {}
    return {
        "positive": counts.get("positive", counts.get("Positive")),
        "negative": counts.get("negative", counts.get("Negative")),
        "total": counts.get("total", counts.get("Total")),
    }


def _resolved_total(counts: dict) -> int | None:
    total = counts["total"]
    if total is None and counts["positive"] is not None and counts["negative"] is not None:
        total = counts["positive"] + counts["negative"]
    return total


def _read_generation_info(ctx: VersionContext) -> dict:
    """Model/tool versions this run was generated with, read once from
    generation_log.json (schema confirmed in generation_log_checks.py) for
    display at the top of the report - purely informational, independent of
    that module's own PASS/FAIL schema validation of the same file.

    generation_log.json is sometimes missing/misnamed/empty in real output
    (that's already its own FAIL check elsewhere) - when that happens, fall
    back to the weaker but still real version info combined_metadata.jsonl
    carries per document (generation_model, docparser tool name, MCE/detector
    engine version), so the version chips don't go fully empty just because
    the dedicated log file wasn't written."""
    data, err = jsonio.read_json_cached(ctx.version_dir / "generation_log.json")
    info = {}
    if not err and isinstance(data, dict):
        generator = data.get("generator") if isinstance(data.get("generator"), dict) else {}
        sit_grader = data.get("sit_grader") if isinstance(data.get("sit_grader"), dict) else {}
        mce = data.get("mce") if isinstance(data.get("mce"), dict) else {}
        docparser = data.get("docparser") if isinstance(data.get("docparser"), dict) else {}
        info = {
            "generator_model": generator.get("model"),
            "generator_model_version": generator.get("model_version"),
            "sit_grader_model": sit_grader.get("model"),
            "mce_version": mce.get("version"),
            "docparser_version": docparser.get("version"),
            "generated_at": data.get("generated_at"),
        }
    if not info.get("generator_model") or not info.get("mce_version") or not info.get("docparser_version"):
        for key, value in _read_generation_info_fallback(ctx).items():
            if not info.get(key):
                info[key] = value
    return info


def _read_generation_info_fallback(ctx: VersionContext) -> dict:
    """Best-effort version info salvaged from combined_metadata.jsonl when
    generation_log.json is absent/invalid - read from the first record that
    has it, since every record in a run shares the same tool versions.
    There is no sit_grader-model equivalent anywhere in combined_metadata.jsonl,
    so that chip stays empty unless generation_log.json provides it."""
    for fs in ctx.folder_sets():
        records, _errors = jsonio.read_jsonl_cached(fs.combined_metadata)
        for rec in records:
            if not isinstance(rec, dict):
                continue
            result = {}
            if rec.get("generation_model"):
                result["generator_model"] = rec["generation_model"]
            docparser = rec.get("docparser") if isinstance(rec.get("docparser"), dict) else {}
            if docparser.get("parser"):
                result["docparser_version"] = docparser["parser"]
            for inst in rec.get("instances") or []:
                if not isinstance(inst, dict):
                    continue
                ext = inst.get("sit_extensions") if isinstance(inst.get("sit_extensions"), dict) else {}
                if ext.get("detector_engine_version"):
                    result["mce_version"] = ext["detector_engine_version"]
                    break
            if result:
                return result
    return {}


def _read_doc_counts(ctx: VersionContext) -> dict:
    """Small summary (total / Agreements pos-neg / Disagreements pos-neg)
    read once per run from each folder set's export_summary.json, shown as
    a quick-glance highlight right under the report header - independent
    of, and much cheaper than, the full reconciliation checks that also
    read this file."""
    agreements = _folder_set_counts(ctx.agreements)
    disagreements = _folder_set_counts(ctx.disagreements)
    agreements_total = _resolved_total(agreements)
    disagreements_total = _resolved_total(disagreements)
    # "total" stays Agreements-only (unchanged - existing dashboards/consumers
    # rely on this meaning "the reconciled corpus size"). "combined_total" is
    # additive: Agreements + Disagreements, or None if either side is unknown.
    combined_total = (
        agreements_total + disagreements_total
        if agreements_total is not None and disagreements_total is not None
        else None
    )
    return {
        "total": agreements_total,
        "combined_total": combined_total,
        "agreements": agreements,
        "disagreements": disagreements,
    }


def run_all(ctx: VersionContext, options: dict | None = None,
            report_progress: Reporter = null_reporter) -> RunReport:
    _load_check_modules()
    options = options or {}
    file_cache.clear()  # never reuse a listing across separate runs/reruns
    jsonio.clear_jsonl_cache()
    jsonio.clear_json_cache()
    # Deferred imports: these check modules import qc.registry, so they can
    # only be imported after _load_check_modules() has already loaded them
    # (avoids a circular import at module load time).
    from qc.checks.context_normalized import clear_scan_cache
    clear_scan_cache()
    from qc.checks.inverted_index_scan import clear_scan_cache as clear_inverted_index_cache
    clear_inverted_index_cache()
    report_progress(f"Computing MCE detection coverage + label distribution for: {ctx.label} ...")
    run_stats = {
        "mce_coverage": qc_stats.compute_mce_coverage(ctx),
        "label_distribution": qc_stats.compute_label_distribution(ctx),
        "composition": qc_stats.compute_composition_distributions(ctx),
        "document_length": qc_stats.compute_document_length_distribution(ctx),
        "corpus_breakdown": qc_stats.compute_corpus_breakdown(ctx),
    }
    generation_info = _read_generation_info(ctx)
    language = qc_stats.compute_dominant_language(ctx)
    if language.get("code"):
        generation_info["language_code"] = language["code"]
        generation_info["language_name"] = lang_names.full_name(language["code"])
    report_progress(f"Computing quality metrics (template clustering, language purity, "
                     f"position/keyword diversity) for: {ctx.label} ...")
    quality_metrics = qc_quality.compute_quality_metrics(ctx, language.get("code"))
    report = RunReport(label=ctx.label, version_dir=str(ctx.version_dir),
                        doc_counts=_read_doc_counts(ctx), stats=run_stats,
                        discovery_note=ctx.discovery_note,
                        generation_info=generation_info,
                        quality_metrics=quality_metrics)
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
