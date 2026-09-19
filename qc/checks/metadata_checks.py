"""Item 8: Positive/Negative metadata + chunk-level checks.

- instances[].expected_engine_match should be True ("True" as a string in the
  real pipeline output - see qc.jsonio.to_bool) for Positive docs
- actual_engine_match mismatch rate reported as a summary WARN (not a hard
  fail per-doc - hard positives/negatives legitimately disagree sometimes)
- duplicate SITGrader/chunk_run_* directories: identify the latest by
  timestamp and explicitly recommend deleting the older one(s)
- chunk snippet label/sit_found: True/True expected for Positive, label
  False expected for Negative (sit_found may legitimately be True for a
  hard negative/lookalike, so that half is not flagged)
"""

from __future__ import annotations

from pathlib import Path

from qc import file_cache
from qc.context import FolderSet, PolarityOutputs, VersionContext
from qc.jsonio import read_json_cached, to_bool
from qc.models import CheckResult, Status
from qc.registry import register
from qc.checks._common import metadata_files as _metadata_files
from qc.checks._common import iter_instances as _iter_instances
from qc.checks._common import sample as _sample

CATEGORY = "8. Metadata / Engine Match / Chunk Labels"


@register(category=CATEGORY)
def check_metadata_engine_match(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        results.extend(_check_chunk_run_duplicates(fs))
        results.extend(_check_engine_match(fs, "Positive", fs.positive, options))
        results.extend(_check_engine_match(fs, "Negative", fs.negative, options))
        results.extend(_check_chunk_labels(fs, "Positive", fs.positive, options))
        results.extend(_check_chunk_labels(fs, "Negative", fs.negative, options))
        results.extend(_check_chunk_label_consistency(fs, "Positive", fs.positive, options))
        results.extend(_check_chunk_label_consistency(fs, "Negative", fs.negative, options))
    return results


def _check_chunk_run_duplicates(fs: FolderSet) -> list[CheckResult]:
    if fs.name != "Disagreements":
        return []
    runs = fs.chunk_run_dirs()
    if len(runs) <= 1:
        return []
    latest = runs[-1]  # chunk_run_<timestamp> sorts lexicographically == chronologically
    older = runs[:-1]
    return [CheckResult(
        Status.WARN, CATEGORY, "8", "Duplicate SITGrader/chunk_run_* directories found",
        f"{len(runs)} chunk_run_* directories present: {[p.name for p in runs]}. "
        f"Latest: {latest.name}.", fs.name,
        f"Keep only the latest run ({latest.name}) and delete the older run(s): "
        f"{', '.join(p.name for p in older)}.")]


def _check_engine_match(fs: FolderSet, polarity_name: str, polarity: PolarityOutputs,
                         options: dict) -> list[CheckResult]:
    scope = f"{fs.name} / {polarity_name}"
    files = _metadata_files(polarity)
    if not files:
        return []

    sampled = _sample(files, options)
    expected_target = "true" if polarity_name == "Positive" else "false"

    expected_mismatches: list[str] = []
    actual_mismatches: list[str] = []
    total_instances = 0

    for path in sampled:
        doc, err = read_json_cached(path)
        if err:
            continue
        for inst in _iter_instances(doc):
            total_instances += 1
            expected = to_bool(inst.get("expected_engine_match"))
            actual = to_bool(inst.get("actual_engine_match"))
            if polarity_name == "Positive" and expected is False:
                if len(expected_mismatches) < 15:
                    expected_mismatches.append(path.name)
            if expected is not None and actual is not None and expected != actual:
                if len(actual_mismatches) < 15:
                    actual_mismatches.append(path.name)

    results: list[CheckResult] = []
    sample_note = f" (sampled {len(sampled)}/{len(files)} docs)" if len(sampled) < len(files) else ""

    if polarity_name == "Positive":
        if expected_mismatches:
            results.append(CheckResult(Status.FAIL, CATEGORY, "8",
                f"expected_engine_match == True for all Positive instances{sample_note}",
                f"{len(expected_mismatches)} doc(s) with expected_engine_match=False, e.g. "
                f"{expected_mismatches[:5]}", scope,
                "expected_engine_match must be True for every Positive-labeled instance; "
                "investigate generation/labeling for the listed docs."))
        else:
            results.append(CheckResult(Status.PASS, CATEGORY, "8",
                f"expected_engine_match == True for all Positive instances{sample_note}",
                f"Checked {total_instances} instance(s) across {len(sampled)} doc(s).", scope))

    if actual_mismatches:
        results.append(CheckResult(Status.WARN, CATEGORY, "8",
            f"actual_engine_match vs expected_engine_match agreement{sample_note}",
            f"{len(actual_mismatches)}/{len(sampled)} doc(s) sampled have at least one "
            f"instance where actual != expected, e.g. {actual_mismatches[:5]}", scope,
            "Some disagreement is expected for hard positives/negatives, but review the "
            "listed docs to confirm the mismatch rate is within tolerance for this SIT."))
    elif total_instances:
        results.append(CheckResult(Status.PASS, CATEGORY, "8",
            f"actual_engine_match vs expected_engine_match agreement{sample_note}",
            f"All {total_instances} sampled instance(s) agree.", scope))

    return results


def _extract_snippets(obj, found: list | None = None) -> list[dict]:
    """Recursively collect snippet-like dicts (must carry a 'label' key)."""
    if found is None:
        found = []
    if isinstance(obj, dict):
        if "label" in obj and ("sit_found" in obj or "snippet" in obj):
            found.append(obj)
        else:
            for v in obj.values():
                _extract_snippets(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _extract_snippets(item, found)
    return found


def _check_chunk_labels(fs: FolderSet, polarity_name: str, polarity: PolarityOutputs,
                         options: dict) -> list[CheckResult]:
    """A document is chunked into multiple snippets (e.g. one per sheet/section);
    only the chunk(s) that actually contain the planted value carry
    label=true/sit_found=true - the rest of the same document's snippets
    legitimately carry label=false. So the check is per-DOCUMENT ("does at
    least one snippet confirm the value was found"), not per-snippet."""
    scope = f"{fs.name} / {polarity_name}"
    chunk_files = file_cache.list_dir(polarity.chunks, "*.json")
    if not chunk_files:
        return []
    sampled = _sample(chunk_files, options)
    sample_note = f" (sampled {len(sampled)}/{len(chunk_files)} files)" if len(sampled) < len(chunk_files) else ""

    label_mismatches: list[str] = []
    sit_found_mismatches: list[str] = []
    docs_checked = 0
    total_snippets = 0

    for path in sampled:
        data, err = read_json_cached(path)
        if err:
            continue
        snippets = _extract_snippets(data)
        if not snippets:
            continue
        docs_checked += 1
        total_snippets += len(snippets)
        any_label_true = any(s.get("label") is True for s in snippets)
        any_sit_found_true = any(s.get("sit_found") is True for s in snippets)

        if polarity_name == "Positive":
            if not any_label_true and len(label_mismatches) < 15:
                label_mismatches.append(path.name)
            if not any_sit_found_true and len(sit_found_mismatches) < 15:
                sit_found_mismatches.append(path.name)
        else:  # Negative: no snippet in the document should have label=true
            if any_label_true and len(label_mismatches) < 15:
                label_mismatches.append(path.name)

    results: list[CheckResult] = []
    if docs_checked == 0:
        return results

    if polarity_name == "Positive":
        if label_mismatches:
            results.append(CheckResult(Status.FAIL, CATEGORY, "8",
                f"at least one chunk snippet has label == true per Positive doc{sample_note}",
                f"{len(label_mismatches)}/{docs_checked} doc(s) have NO snippet with label=true, "
                f"e.g. {label_mismatches[:5]}", scope,
                "At least one chunk of every Positive doc must have label == true - the "
                "planted value should be found in some chunk of the document."))
        else:
            results.append(CheckResult(Status.PASS, CATEGORY, "8",
                f"at least one chunk snippet has label == true per Positive doc{sample_note}",
                f"Checked {docs_checked} doc(s) / {total_snippets} snippet(s), all have >=1 "
                "matching chunk.", scope))

        if sit_found_mismatches:
            results.append(CheckResult(Status.FAIL, CATEGORY, "8",
                f"at least one chunk snippet has sit_found == true per Positive doc{sample_note}",
                f"{len(sit_found_mismatches)}/{docs_checked} doc(s) have NO snippet with "
                f"sit_found=true, e.g. {sit_found_mismatches[:5]}", scope,
                "At least one chunk of every Positive doc must have sit_found == true."))
        else:
            results.append(CheckResult(Status.PASS, CATEGORY, "8",
                f"at least one chunk snippet has sit_found == true per Positive doc{sample_note}",
                f"Checked {docs_checked} doc(s) / {total_snippets} snippet(s), all have >=1 "
                "matching chunk.", scope))
    else:
        if label_mismatches:
            results.append(CheckResult(Status.FAIL, CATEGORY, "8",
                f"no chunk snippet has label == true for Negative docs{sample_note}",
                f"{len(label_mismatches)}/{docs_checked} doc(s) have a snippet with label=true, "
                f"e.g. {label_mismatches[:5]}", scope,
                "A Negative (true-negative) doc should have no chunk where label == true."))
        else:
            results.append(CheckResult(Status.PASS, CATEGORY, "8",
                f"no chunk snippet has label == true for Negative docs{sample_note}",
                f"Checked {docs_checked} doc(s) / {total_snippets} snippet(s), none matched.", scope))

    return results


def _extract_snippet_candidates(obj, found: list | None = None) -> list[dict]:
    """Like _extract_snippets(), but does NOT require a 'label' key to be
    present - used specifically to detect a snippet that is genuinely
    MISSING its label field, which _extract_snippets() would otherwise
    silently skip rather than flag."""
    if found is None:
        found = []
    if isinstance(obj, dict):
        if "sit_found" in obj or "snippet" in obj:
            found.append(obj)
        else:
            for v in obj.values():
                _extract_snippet_candidates(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _extract_snippet_candidates(item, found)
    return found


def _check_chunk_label_consistency(fs: FolderSet, polarity_name: str, polarity: PolarityOutputs,
                                    options: dict) -> list[CheckResult]:
    """Per-snippet consistency: wherever sit_found == true, label must equal
    the polarity's expected value (True for Positive, False for Negative) -
    confirmed as an exact, zero-exception invariant across 10,000+ real
    chunk files spanning three independent SIT corpora (South Africa,
    Sweden, Taiwan). Also flags any snippet genuinely missing a 'label'
    field, and reports (as a count + examples, not one alert per file -
    multi-snippet documents are common, 20-50% of files in real samples)
    how many files carry more than one sit_found==true snippet."""
    scope = f"{fs.name} / {polarity_name}"
    chunk_files = file_cache.list_dir(polarity.chunks, "*.json")
    if not chunk_files:
        return []
    sampled = _sample(chunk_files, options)
    sample_note = f" (sampled {len(sampled)}/{len(chunk_files)} files)" if len(sampled) < len(chunk_files) else ""

    expected_label = polarity_name == "Positive"
    missing_label_examples: list[str] = []
    violation_examples: list[str] = []
    multi_sit_found_examples: list[str] = []
    total_snippets = 0
    files_checked = 0

    for path in sampled:
        data, err = read_json_cached(path)
        if err:
            continue
        candidates = _extract_snippet_candidates(data)
        if not candidates:
            continue
        files_checked += 1
        sit_found_true_count = 0

        for snippet in candidates:
            total_snippets += 1
            if "label" not in snippet:
                if len(missing_label_examples) < 15:
                    missing_label_examples.append(path.name)
                continue
            if snippet.get("sit_found") is True:
                sit_found_true_count += 1
                if snippet.get("label") is not expected_label:
                    if len(violation_examples) < 15:
                        violation_examples.append(
                            f"{path.name} (label={snippet.get('label')}, sit_found=True)")

        if sit_found_true_count > 1 and len(multi_sit_found_examples) < 15:
            multi_sit_found_examples.append(f"{path.name} ({sit_found_true_count} snippets)")

    results: list[CheckResult] = []
    if files_checked == 0:
        return results

    if missing_label_examples:
        results.append(CheckResult(Status.FAIL, CATEGORY, "8",
            f"every chunk snippet has a 'label' field{sample_note}",
            f"{len(missing_label_examples)} file(s) have a snippet with no 'label' key at all, "
            f"e.g. {missing_label_examples[:5]}", scope,
            "Every chunk snippet must carry a 'label' field - regenerate the listed chunk "
            "files."))
    else:
        results.append(CheckResult(Status.PASS, CATEGORY, "8",
            f"every chunk snippet has a 'label' field{sample_note}",
            f"Checked {total_snippets} snippet(s) across {files_checked} file(s), all have a "
            "label field.", scope))

    expected_str = "true" if expected_label else "false"
    if violation_examples:
        results.append(CheckResult(Status.FAIL, CATEGORY, "8",
            f"sit_found==true implies label=={expected_str} for {polarity_name} docs{sample_note}",
            f"{len(violation_examples)} snippet(s) violate this, e.g. {violation_examples[:5]}",
            scope,
            f"Wherever sit_found is true in a {polarity_name} document's chunk, label must be "
            f"{expected_str} - investigate the listed files."))
    else:
        results.append(CheckResult(Status.PASS, CATEGORY, "8",
            f"sit_found==true implies label=={expected_str} for {polarity_name} docs{sample_note}",
            f"Checked {total_snippets} snippet(s) across {files_checked} file(s), all consistent.",
            scope))

    if multi_sit_found_examples:
        results.append(CheckResult(Status.INFO, CATEGORY, "8",
            f"files with more than one sit_found==true snippet{sample_note}",
            f"{len(multi_sit_found_examples)}/{files_checked} file(s) have multiple "
            f"sit_found==true snippets (typically multi-page/sheet documents), e.g. "
            f"{multi_sit_found_examples[:5]}", scope))

    return results
