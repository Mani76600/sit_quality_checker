"""Item 9 (content-level extension): open the actual document content and
independently verify the planted SIT value's real surrounding sentence,
instead of only trusting the pipeline's own self-reported metadata fields
(keyword_present, keyword_distance, proximity_bucket).

Uses parsed_raw_doc/plain_text/<raw-filename>.<ext>.txt - the pipeline
already extracts plain text for every raw_doc file regardless of source
format (.pdf, .docx, .xlsx, .txt, .eml, .log, .json, .xml, ...), so this
check works uniformly across every file type without needing its own
PDF/DOCX/XLSX parser.

For each sampled document:
  1. Read its metadata instances[] - each carries `value`, `keyword`,
     `keyword_distance`, `proximity_bucket`, `keyword_direction`, and
     sit_extensions.keyword_requirement (mandatory/optional).
  2. Read the real extracted plain_text.
  3. Locate the value in that text (exact match, falling back to a
     separator-tolerant match for formatted values like "580 923 5588 083").
  4. Pull the sentence containing the value plus the sentence before and
     after it (the actual before/after context, not an offset guess).
  5. Independently check whether the recorded keyword genuinely appears in
     that context - i.e. re-derive "does this document really carry
     rule-conforming evidence for this SIT" from the real text, rather than
     trusting metadata that could itself be wrong.

Sampling is stratified across raw_doc file extensions (grouped via the
raw_doc/ directory listing, which is free - no need to open every metadata
file first) so a check with the default sample size doesn't end up only
covering whichever file type happens to be most common.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

from qc import file_cache
from qc.context import FolderSet, PolarityOutputs, VersionContext
from qc.jsonio import read_json_cached, to_bool
from qc.models import CheckResult, Status
from qc.registry import register

CATEGORY = "9b. Content Verification (value-in-context)"

RNG_SEED = 42
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
MAX_EXAMPLES = 8


def _stratified_sample(metadata_files: list[Path], ext_by_stem: dict[str, str],
                        options: dict) -> list[Path]:
    exhaustive = bool(options.get("exhaustive_chunk_scan", False))
    sample_size = int(options.get("chunk_sample_size", 200))
    if exhaustive or len(metadata_files) <= sample_size:
        return metadata_files

    groups: dict[str, list[Path]] = {}
    for mp in metadata_files:
        ext = ext_by_stem.get(mp.stem, "")
        groups.setdefault(ext, []).append(mp)

    rng = random.Random(RNG_SEED)
    for g in groups.values():
        rng.shuffle(g)

    result: list[Path] = []
    group_lists = list(groups.values())
    i = 0
    while len(result) < sample_size and any(group_lists):
        g = group_lists[i % len(group_lists)]
        if g:
            result.append(g.pop())
        i += 1
    return result


def _find_value_span(text: str, value: str) -> tuple[int, int] | None:
    if not value:
        return None
    idx = text.find(value)
    if idx != -1:
        return idx, idx + len(value)
    # Tolerant fallback for reformatted values (e.g. "580 923 5588 083" for
    # "5809235588083") - allow an optional separator between each character.
    if len(value) > 40:  # too long to safely build a per-character pattern
        return None
    pattern = r"[\s\-.]?".join(re.escape(c) for c in value)
    m = re.search(pattern, text)
    return (m.start(), m.end()) if m else None


def _to_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _proximity_window(text: str, start: int, end: int, distance: int | None,
                       direction: str | None) -> str:
    """The actual search window for keyword confirmation, sized from the SIT's
    own recorded keyword_distance/keyword_direction (with a buffer) rather than
    a fixed number of sentences - a keyword in the "edge"/"far" proximity
    bucket can legitimately sit 200+ characters away, well beyond one sentence."""
    window = max(250, (distance or 0) + 60)
    direction = (direction or "").strip().lower()
    if direction == "before":
        return text[max(0, start - window):start]
    if direction == "after":
        return text[end:end + window]
    return text[max(0, start - window):end + window]


def _sentence_context(text: str, start: int, end: int) -> tuple[str, str, str]:
    sentences = SENTENCE_SPLIT_RE.split(text)
    pos = 0
    value_idx = None
    for i, s in enumerate(sentences):
        s_end = pos + len(s)
        if pos <= start < s_end + 1:
            value_idx = i
            break
        pos = s_end + 1
    if value_idx is None:
        return text[max(0, start - 150):start], text[start:end], text[end:end + 150]
    before = sentences[value_idx - 1].strip() if value_idx > 0 else ""
    current = sentences[value_idx].strip()
    after = sentences[value_idx + 1].strip() if value_idx + 1 < len(sentences) else ""
    return before, current, after


@register(category=CATEGORY)
def check_content_verification(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        for polarity_name, polarity in (("Positive", fs.positive), ("Negative", fs.negative)):
            results.extend(_check_polarity(fs, polarity_name, polarity, options))
    return results


def _check_polarity(fs: FolderSet, polarity_name: str, polarity: PolarityOutputs,
                     options: dict) -> list[CheckResult]:
    scope = f"{fs.name} / {polarity_name}"
    metadata_files = file_cache.list_dir(polarity.metadata, "*.json")
    if not metadata_files:
        return []

    raw_files = file_cache.list_dir_files(polarity.raw_doc)
    ext_by_stem = {p.stem: p.suffix.lower() for p in raw_files}
    sampled = _stratified_sample(metadata_files, ext_by_stem, options)
    exts_covered = sorted({ext_by_stem.get(mp.stem, "(unknown)") or "(none)" for mp in sampled})
    sample_note = (f" (sampled {len(sampled)}/{len(metadata_files)} docs across "
                   f"{len(exts_covered)} file type(s): {', '.join(exts_covered)})")

    value_not_found: list[str] = []
    mandatory_keyword_not_confirmed: list[str] = []
    keyword_metadata_mismatch: list[str] = []
    examples: list[str] = []
    checked_instances = 0

    for mp in sampled:
        doc, err = read_json_cached(mp)
        if err or not isinstance(doc, dict):
            continue
        instances = doc.get("instances") or []
        if not instances:
            continue

        ext = ext_by_stem.get(mp.stem)
        plain_text_name = f"{mp.stem}{ext}.txt" if ext else None
        text = None
        if plain_text_name:
            pt_path = polarity.plain_text / plain_text_name
            if pt_path.exists():
                try:
                    text = pt_path.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    text = None
        if text is None:
            continue

        for inst in instances:
            if not isinstance(inst, dict):
                continue
            value = str(inst.get("value") or inst.get("normalized_value") or "").strip()
            if not value:
                continue
            checked_instances += 1

            span = _find_value_span(text, value)
            if span is None:
                if len(value_not_found) < 15:
                    value_not_found.append(f"{mp.stem}{ext or ''} (value={value})")
                continue

            before, current, after = _sentence_context(text, *span)
            keyword = str(inst.get("keyword") or "").strip()
            requirement = str((inst.get("sit_extensions") or {}).get("keyword_requirement", "")).lower()
            keyword_present_claimed = to_bool(inst.get("keyword_present"))
            distance = _to_int(inst.get("keyword_distance"))
            direction = inst.get("keyword_direction")
            proximity_text = _proximity_window(text, *span, distance, direction)
            keyword_actually_present = bool(keyword) and keyword.lower() in proximity_text.lower()

            if keyword and keyword_present_claimed is not None and keyword_present_claimed != keyword_actually_present:
                if len(keyword_metadata_mismatch) < 15:
                    keyword_metadata_mismatch.append(
                        f"{mp.stem}{ext or ''}: metadata claims keyword_present={keyword_present_claimed}, "
                        f"but '{keyword}' {'is' if keyword_actually_present else 'is NOT'} in the real "
                        f"sentence context")

            if polarity_name == "Positive" and requirement == "mandatory" and not keyword_actually_present:
                if len(mandatory_keyword_not_confirmed) < 15:
                    mandatory_keyword_not_confirmed.append(f"{mp.stem}{ext or ''} (expected keyword '{keyword}')")

            if len(examples) < MAX_EXAMPLES:
                examples.append(
                    f"[{mp.stem}{ext or ''}] value='{value}' | ...{before[-80:]} >>>{current}<<< {after[:80]}...")

    results: list[CheckResult] = []
    if checked_instances == 0:
        return results

    if value_not_found:
        results.append(CheckResult(Status.FAIL, CATEGORY, "9b",
            f"Planted value is actually present in the extracted document text{sample_note}",
            f"{len(value_not_found)} instance(s) where the recorded value could not be found "
            f"(exact or reformatted) in parsed_raw_doc/plain_text, e.g. {value_not_found[:5]}", scope,
            "The value should be verbatim (or trivially reformatted) in the document's extracted "
            "text - investigate whether the document generation/placement step actually wrote the "
            "value, or whether text extraction is dropping/garbling it."))
    else:
        results.append(CheckResult(Status.PASS, CATEGORY, "9b",
            f"Planted value is actually present in the extracted document text{sample_note}",
            f"Checked {checked_instances} instance(s) across {len(sampled)} doc(s), all found.", scope))

    if polarity_name == "Positive":
        if mandatory_keyword_not_confirmed:
            results.append(CheckResult(Status.FAIL, CATEGORY, "9b",
                f"Mandatory keyword genuinely appears in the real sentence context{sample_note}",
                f"{len(mandatory_keyword_not_confirmed)} instance(s) with keyword_requirement=mandatory "
                f"whose keyword could not be independently confirmed in the actual text around the "
                f"value, e.g. {mandatory_keyword_not_confirmed[:5]}", scope,
                "A mandatory keyword must be verifiable in the real document text near the value, not "
                "just recorded in metadata - these documents may not genuinely satisfy this SIT's "
                "detection rule and are worth a manual read."))
        else:
            results.append(CheckResult(Status.PASS, CATEGORY, "9b",
                f"Mandatory keyword genuinely appears in the real sentence context{sample_note}",
                "No mandatory-keyword instance failed independent text confirmation.", scope))

    if keyword_metadata_mismatch:
        results.append(CheckResult(Status.WARN, CATEGORY, "9b",
            f"metadata keyword_present matches the real text{sample_note}",
            f"{len(keyword_metadata_mismatch)} instance(s) where metadata's keyword_present disagrees "
            f"with what's actually in the extracted sentence context, e.g. "
            f"{keyword_metadata_mismatch[:3]}", scope,
            "metadata.instances[].keyword_present may be stale/incorrect relative to the real "
            "document content - worth spot-checking the listed docs."))
    else:
        results.append(CheckResult(Status.PASS, CATEGORY, "9b",
            f"metadata keyword_present matches the real text{sample_note}",
            "Checked instances agree with the real extracted text.", scope))

    if examples:
        results.append(CheckResult(Status.INFO, CATEGORY, "9b",
            "Example value-in-context extractions (sentence before/current/after)",
            " || ".join(examples), scope))

    return results
