"""Addition: scenario_id <-> filename correspondence, and scenario_id
agreement across corpus.jsonl / combined_metadata.jsonl / per-doc metadata.

Every document carries a `scenario_id` field (in corpus.jsonl,
combined_metadata.jsonl, and each Positive|Negative/outputs/metadata/<stem>.json)
that is the FULL, un-truncated content slug for that document; `stem` (the
delivered filename, minus extension) is a TRUNCATED/re-hashed derivative of
it. The exact truncation/hashing scheme varies by pipeline run - verified
against three real corpora with three different conventions:

  - South Africa: stem = <full slug> + "-" + <1-3 char discriminator> +
    "-" + <fresh random hash>
  - Sweden:       stem = <slug truncated at a word boundary> + "-" +
    <zero-padded sequential index> (no hash at all)
  - South Korea:  stem = <slug truncated mid-word to a fixed length> + "-"
    + <fresh random hash>

So stem is never a reliable prefix of scenario_id, but the reverse always
holds: stem, once its own trailing hash/sequential-number token(s) are
stripped, is always a prefix of scenario_id. Also, scenario_id slugifies
non-ASCII characters to "-" while stem/filename preserves the real Unicode
character (e.g. Swedish "aktivering-av-tjänst" in stem vs
"aktivering-av-tj-nst" in scenario_id) - both sides are normalized through
the same ASCII-only slugifier before comparing so that difference doesn't
produce false failures. Verified against 3,200+ real documents across all
three sample corpora (English, Swedish, and a third independent SIT) with
zero exceptions after both fixes.

A document whose normalized scenario_id does NOT start with its own stem's
normalized slug indicates the wrong scenario_id got attached to it (or a
files/metadata mixup) - the same kind of cross-contamination the rest of
this tool's checks (see raw_doc_checks.py's SIT-identity check) also guard
against, at a finer grain.

This also cross-checks that scenario_id is recorded identically in
corpus.jsonl, combined_metadata.jsonl, and the per-doc metadata.json for the
same doc_id - these three files describe the same document and should never
disagree about its scenario_id.
"""

from __future__ import annotations

import re
from pathlib import Path

from qc import file_cache
from qc.context import FolderSet, VersionContext
from qc.jsonio import read_json_cached, read_jsonl_cached
from qc.models import CheckResult, Status
from qc.registry import register
from qc.checks._common import sample as _sample

CATEGORY = "Scenario ID Consistency (addition)"

TRAILING_TOKEN_RE = re.compile(r"-([0-9a-f]{1,16})$", re.IGNORECASE)
NON_ASCII_ALNUM_RE = re.compile(r"[^a-z0-9]+")
MAX_EXAMPLES = 15


def _strip_trailing_hash_tokens(stem: str) -> str:
    """Repeatedly strip trailing hyphen-segments that are pure hex/digits
    (a discriminator, a hash, a sequential index, or a combination of
    those) until a segment containing a real word is reached."""
    s = stem
    while True:
        m = TRAILING_TOKEN_RE.search(s)
        if not m:
            return s
        s = s[:m.start()]


def _normalize(s: str) -> str:
    """ASCII-only slugify so a stem's preserved Unicode characters compare
    equal to scenario_id's own slugified (non-ASCII -> '-') version."""
    return NON_ASCII_ALNUM_RE.sub("-", (s or "").lower()).strip("-")


def _stem_slug(stem: str) -> str:
    return _normalize(_strip_trailing_hash_tokens(stem or ""))


@register(category=CATEGORY)
def check_scenario_id(ctx: VersionContext, options: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fs in ctx.folder_sets():
        results.extend(_check_one(fs, options))
    return results


def _collect_sampled_doc_metadata(fs: FolderSet, options: dict) -> dict[str, dict]:
    """doc_id -> {scenario_id, stem, source} for a bounded sample of docs
    across Positive and Negative metadata."""
    info: dict[str, dict] = {}
    for polarity_name, polarity in (("Positive", fs.positive), ("Negative", fs.negative)):
        files = file_cache.list_dir(polarity.metadata, "*.json")
        for path in _sample(files, options):
            doc, err = read_json_cached(path)
            if err or not isinstance(doc, dict):
                continue
            doc_id = doc.get("doc_id")
            if not doc_id:
                continue
            info[doc_id] = {
                "scenario_id": doc.get("scenario_id"),
                "stem": doc.get("stem") or "",
                "source": f"{polarity_name}/{path.name}",
            }
    return info


def _scan_jsonl_scenario_ids(path: Path, wanted_doc_ids: set[str]) -> dict[str, str]:
    """{doc_id: scenario_id} for the doc_ids we're checking, from the shared
    cached parse - corpus.jsonl/combined_metadata.jsonl are also read by
    export_summary.py's label-distribution check, so read_jsonl_cached()
    ensures each file is only parsed once per run rather than once per
    check module."""
    if not wanted_doc_ids:
        return {}
    records, _errors = read_jsonl_cached(path)
    found: dict[str, str] = {}
    for rec in records:
        did = rec.get("doc_id")
        if did in wanted_doc_ids:
            found[did] = rec.get("scenario_id")
    return found


def _check_one(fs: FolderSet, options: dict) -> list[CheckResult]:
    scope = fs.name
    results: list[CheckResult] = []

    doc_info = _collect_sampled_doc_metadata(fs, options)
    if not doc_info:
        return results
    doc_ids = set(doc_info)
    total = len(doc_info)
    sample_note = f" (sampled {total} doc(s))"

    corpus_scenario_ids = _scan_jsonl_scenario_ids(fs.corpus, doc_ids)
    combined_scenario_ids = _scan_jsonl_scenario_ids(fs.combined_metadata, doc_ids)

    missing_scenario_id: list[str] = []
    prefix_mismatch: list[str] = []
    corpus_mismatch: list[str] = []
    combined_mismatch: list[str] = []
    missing_in_corpus: list[str] = []
    missing_in_combined: list[str] = []

    for doc_id, info in doc_info.items():
        sid = info.get("scenario_id")
        stem = info.get("stem")
        label = f"{info['source']} (doc_id={doc_id})"

        if not sid:
            if len(missing_scenario_id) < MAX_EXAMPLES:
                missing_scenario_id.append(label)
            continue

        slug = _stem_slug(stem)
        if not slug or not _normalize(sid).startswith(slug):
            if len(prefix_mismatch) < MAX_EXAMPLES:
                prefix_mismatch.append(f"{label}: scenario_id='{sid}' vs stem='{stem}' (stem slug='{slug}')")

        corpus_sid = corpus_scenario_ids.get(doc_id)
        if corpus_sid is None:
            if len(missing_in_corpus) < MAX_EXAMPLES:
                missing_in_corpus.append(label)
        elif corpus_sid != sid:
            if len(corpus_mismatch) < MAX_EXAMPLES:
                corpus_mismatch.append(
                    f"{label}: metadata scenario_id='{sid}' vs corpus.jsonl scenario_id='{corpus_sid}'")

        combined_sid = combined_scenario_ids.get(doc_id)
        if combined_sid is None:
            if len(missing_in_combined) < MAX_EXAMPLES:
                missing_in_combined.append(label)
        elif combined_sid != sid:
            if len(combined_mismatch) < MAX_EXAMPLES:
                combined_mismatch.append(
                    f"{label}: metadata scenario_id='{sid}' vs combined_metadata.jsonl "
                    f"scenario_id='{combined_sid}'")

    def add(bad: list[str], title: str, fix: str, status: Status = Status.FAIL) -> None:
        if bad:
            results.append(CheckResult(status, CATEGORY, "addl", f"{title}{sample_note}",
                f"{len(bad)} issue(s), e.g. {bad[:5]}", scope, fix))
        else:
            results.append(CheckResult(Status.PASS, CATEGORY, "addl", f"{title}{sample_note}",
                f"Checked {total} doc(s), no issues.", scope))

    add(missing_scenario_id, "scenario_id is present in metadata",
        "Every document's metadata.json must carry a non-empty scenario_id.")
    # INFO, not FAIL: confirmed against a real Taiwan (Romanized Chinese) sample
    # that this genuinely diverges ~40% of the time for fully-localized SITs -
    # scenario_id can stay in English (the internal scenario template name)
    # while the delivered filename is localized to the target language, with
    # zero shared vocabulary by design. Still worth surfacing as a heads-up
    # (a real mixup would also look like this), just not something to fail on.
    add(prefix_mismatch, "scenario_id's content slug matches the delivered filename",
        "scenario_id (normalized) usually starts with the delivered filename's own slug - "
        "a mismatch can mean a file/metadata mixup, but is also expected for fully-localized "
        "SITs where scenario_id stays in English while the filename is translated (confirmed "
        "on real Taiwan/Romanized Chinese output). Spot-check the listed docs rather than "
        "treating this as a definite defect.",
        status=Status.INFO)
    add(corpus_mismatch, "scenario_id matches between metadata.json and corpus.jsonl",
        "corpus.jsonl's scenario_id for this doc_id should be identical to the per-doc "
        "metadata.json's scenario_id - regenerate corpus.jsonl from the metadata.")
    add(combined_mismatch, "scenario_id matches between metadata.json and combined_metadata.jsonl",
        "combined_metadata.jsonl's scenario_id for this doc_id should be identical to the "
        "per-doc metadata.json's scenario_id - regenerate combined_metadata.jsonl.")
    add(missing_in_corpus, "every sampled doc_id has a corpus.jsonl row",
        "Every document should have a corresponding corpus.jsonl row for the same doc_id.",
        status=Status.WARN)
    add(missing_in_combined, "every sampled doc_id has a combined_metadata.jsonl row",
        "Every document should have a corresponding combined_metadata.jsonl row for the "
        "same doc_id.", status=Status.WARN)

    return results
