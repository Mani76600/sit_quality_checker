"""Five graded "quality metrics" (Strong/Acceptable/Weak/Critical against a
threshold ladder) - a different kind of result from the PASS/FAIL checklist
everywhere else in this package. Lives outside qc/checks/ (and does not
import qc.registry) for the same reason qc/stats.py does: every file under
qc/checks/ imports qc.registry to self-register, so registry.py (which
imports this module to populate RunReport.quality_metrics) can never import
from a checks/*.py module.

All five are computed EXHAUSTIVELY (every document, never sampled) in one
fused pass over every document's metadata.json + its plain_text file, since
every metric needs both and re-reading 15,000+ documents once per metric
would be wasteful. Only the final aggregation (entropy, cluster connected
components, ratios) differs per metric.

Known, deliberate approximations (documented here once rather than at every
call site):

- Template Cluster Rate (#1) uses MinHash/LSH + exact-estimate verification
  to find near-duplicate pairs, then takes connected components over the
  qualifying edges - this is single-link clustering, not true complete-link
  (which is impractical at this scale: true complete-link requires checking
  every pair within a candidate cluster, not just LSH-found edges). In
  practice this is the standard, tractable approach for near-duplicate
  detection at corpus scale (the same idea the sit_metrics.html reference's
  own "LSH candidate pairs, not an all-pairs distribution" caveat describes).
- Target-Language Purity (#2) runs ONE language-detection call per document
  on its whole assessable-token text, not per sentence - true per-sentence
  detection at 15,000+ documents would cost several minutes (benchmarked at
  ~1.2ms/call; even 3 spans/doc would be 45,000+ calls). A mixed-language
  document still correctly contributes its real non-matching token count to
  the unexpected-token tally; it just isn't localized to a specific sentence
  within that document.
- SIT Position Diversity (#3) bands instances[].value_start_offset against
  the document's real plain_text length. offset_accuracy_checks.py already
  found these offsets drift from the true position in the majority of real
  documents (computed against an earlier text representation) - but the
  drift is small (a handful to a few hundred characters) relative to the
  5 equal-length bands of a multi-KB document, so it practically never
  flips which band an occurrence falls into except right at a boundary.
- Negative Observed-Label Wording Uniqueness (#5) extracts the lead-in
  phrase via a simple heuristic (the last few words immediately before the
  value's real text position, cut at the nearest preceding sentence/line
  break) - not a semantic/NLP-derived label, matching the metric's own
  definition ("observed text, not inferred semantic roles").
"""

from __future__ import annotations

import re
from collections import Counter
from math import log2

from qc import file_cache
from qc.context import VersionContext
from qc.jsonio import read_json_cached
from qc.stats import sit_category

_SHINGLE_RE = re.compile(r"[a-z0-9]+")
_SHINGLE_K = 5  # 5-word shingles - a standard choice for near-duplicate text detection
_NUM_PERM = 128
_LSH_THRESHOLD = 0.75

_URL_OR_EMAIL_RE = re.compile(r"\S*(?:https?://|www\.|@)\S*")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
_ACRONYM_RE = re.compile(r"^[A-Z]{2,}$")
_MIN_WORDS_FOR_DETECTION = 6

_EASY_BAND_COUNT = 4
_EASY_MAX = 200.0  # instances[].keyword_distance range for difficulty="easy"
_HARD_BAND_COUNT = 5
_HARD_MIN, _HARD_MAX = 201.0, 300.0  # instances[].keyword_distance range for difficulty="hard"

_LEADIN_CUT_RE = re.compile(r"[\n]|(?<=[.!?])\s")
_LEADIN_WORDS = 4


def _evenly_sample(items: list, cap: int) -> list:
    """Deterministic, evenly-spaced subsample across the whole sequence
    (not just the first `cap` items) - keeps the sample representative of
    the entire document rather than biased toward its opening, while still
    bounding per-document CPU cost. Used for both shingles (MinHash) and
    assessable words (language detection) - measured on real sample data at
    ~3x the per-document cost without this cap, for a corpus-scale
    difference of several minutes vs well under one."""
    if cap <= 0 or len(items) <= cap:
        return items
    step = len(items) / cap
    return [items[int(i * step)] for i in range(cap)]


_SHINGLE_CAP = 150
_WORD_CAP = 150


def _shingles(text: str) -> set[str]:
    words = _SHINGLE_RE.findall(text.lower())
    if not words:
        return set()
    if len(words) < _SHINGLE_K:
        return {" ".join(words)}
    all_shingles = [" ".join(words[i:i + _SHINGLE_K]) for i in range(len(words) - _SHINGLE_K + 1)]
    return set(_evenly_sample(all_shingles, _SHINGLE_CAP))


def _assessable_words(text: str, planted_values: set[str]) -> list[str]:
    text = _URL_OR_EMAIL_RE.sub(" ", text)
    words = [w for w in _WORD_RE.findall(text)
              if len(w) > 1 and w not in planted_values and not _ACRONYM_RE.match(w)]
    return _evenly_sample(words, _WORD_CAP)


def _planted_values(doc: dict) -> set[str]:
    """sit_values/lookalike_values are dicts keyed by SIT key (e.g.
    {"south_africa_identification_number": ["5809235588083"]}), each mapping
    to a list of planted value strings - confirmed against real sample data
    (NOT a flat list, despite how similarly-named fields read elsewhere)."""
    values: set[str] = set()
    for field in ("sit_values", "lookalike_values"):
        bucket = doc.get(field)
        if isinstance(bucket, dict):
            for v in bucket.values():
                if isinstance(v, list):
                    values.update(str(x) for x in v)
        elif isinstance(bucket, list):
            values.update(str(x) for x in bucket)
    return values


def _find_value_span(text: str, value: str) -> tuple[int, int] | None:
    """Minimal, duplicated-on-purpose twin of content_verification.py's own
    _find_value_span (can't import it - see module docstring)."""
    if not value:
        return None
    idx = text.find(value)
    if idx != -1:
        return idx, idx + len(value)
    if len(value) > 40:
        return None
    pattern = r"[\s\-.]?".join(re.escape(c) for c in value)
    m = re.search(pattern, text)
    return (m.start(), m.end()) if m else None


def _observed_label(text: str, start: int) -> str | None:
    window = text[max(0, start - 80):start]
    cuts = [m.start() for m in _LEADIN_CUT_RE.finditer(window)]
    if cuts:
        window = window[cuts[-1] + 1:]
    window = window.strip(" \t:\u2013-")
    if not window:
        return None
    words = window.split()[-_LEADIN_WORDS:]
    label = " ".join(words).strip(" :,-")
    label = re.sub(r"\s+", " ", label).lower()
    return label or None


def _to_number(value: object) -> float | None:
    """instances[].value_start_offset and keyword_distance are routinely
    string-typed in real data ("355", "58") rather than JSON numbers -
    confirmed on real South Africa sample metadata.json files, the same
    string/real-type mixing already documented elsewhere in this codebase
    (see document_composition_checks.py's Boolean-field type note)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _entropy01(counts: Counter) -> float | None:
    """Shannon entropy over however many of the bins actually have a count,
    normalized to 0-1 by the entropy of a uniform distribution over the same
    number of occupied bins' maximum possible band count (the ladder in the
    spec is itself 0-1, so this normalizes against log2(band_count))."""
    total = sum(counts.values())
    if total == 0:
        return None
    n_bands = len(counts)
    if n_bands <= 1:
        return 0.0
    h = -sum((c / total) * log2(c / total) for c in counts.values() if c)
    return h / log2(n_bands)


def _grade(score: float | None, strong_min: float, acceptable_min: float, weak_min: float) -> str:
    if score is None:
        return "n/a"
    if score >= strong_min:
        return "strong"
    if score >= acceptable_min:
        return "acceptable"
    if score >= weak_min:
        return "weak"
    return "critical"


def _process_one_document(fs_name: str, polarity_name: str, polarity, mpath,
                           expected_lang: str | None, want_minhash: bool, detect_fn) -> dict | None:
    """Pure, side-effect-free per-document worker - runs on a thread pool
    (see compute_quality_metrics). Reading 15,000+ small metadata.json/
    plain_text files one at a time was measured (on this real sample,
    Windows + real-time AV scanning in the mix) at ~35ms/file combined -
    over 900s wall-clock for a real 15k-document corpus. The actual CPU
    work (shingling, MinHash, langdetect) is comparatively cheap; threading
    gave a 40-50x speedup on the same file set in direct measurement
    (file reads release the GIL), which is the only reason this is
    tractable at all within this tool's runtime budget."""
    doc, err = read_json_cached(mpath)
    if err or not isinstance(doc, dict):
        return None
    filename = doc.get("filename")
    if not isinstance(filename, str) or not filename:
        return None
    text_path = polarity.plain_text / f"{filename}.txt"
    if not text_path.exists():
        return None
    try:
        text = text_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None

    total_len = len(text)
    category = sit_category(doc)  # "easy positive", "hard negative", ...
    polarity_key = "positive" if "positive" in category else (
        "negative" if "negative" in category else polarity_name)

    result: dict = {
        "minhash_id": None, "minhash": None,
        "purity_assessed": 0, "purity_unexpected": 0, "purity_checked": False,
        "position": None,  # (polarity_key, band)
        "keyword": [],  # [(polarity_key, difficulty, band), ...]
        "negative_labels": [],
    }

    if want_minhash:
        shingles = _shingles(text)
        if shingles:
            from datasketch import MinHash
            mh = MinHash(num_perm=_NUM_PERM)
            for s in shingles:
                mh.update(s.encode("utf8"))
            result["minhash_id"] = f"{fs_name}:{polarity_name}:{mpath.stem}"
            result["minhash"] = mh

    if expected_lang and detect_fn is not None:
        words = _assessable_words(text, _planted_values(doc))
        if len(words) >= _MIN_WORDS_FOR_DETECTION:
            try:
                lang = detect_fn(" ".join(words))
            except Exception:
                lang = None
            if lang:
                result["purity_checked"] = True
                result["purity_assessed"] = len(words)
                result["purity_unexpected"] = len(words) if lang != expected_lang else 0

    positions: list[tuple[str, int]] = []
    keyword_hits: list[tuple[str, str, int]] = []
    negative_labels: list[str] = []
    for inst in doc.get("instances") or []:
        if not isinstance(inst, dict):
            continue
        start = _to_number(inst.get("value_start_offset"))
        if start is not None and total_len > 0 and polarity_key in ("positive", "negative"):
            band = min(4, max(0, int(start / total_len * 5)))  # 5 equal-length bands, 0-4
            positions.append((polarity_key, band))

        difficulty = str(inst.get("difficulty") or "").strip().lower()
        distance = _to_number(inst.get("keyword_distance"))
        if inst.get("keyword_present") and distance is not None and difficulty in ("easy", "hard"):
            band = None
            if difficulty == "easy" and 0 <= distance < _EASY_MAX:
                band = min(_EASY_BAND_COUNT - 1, int(distance / (_EASY_MAX / _EASY_BAND_COUNT)))
            elif difficulty == "hard" and _HARD_MIN <= distance <= _HARD_MAX:
                band = min(_HARD_BAND_COUNT - 1,
                           int((distance - _HARD_MIN) / ((_HARD_MAX - _HARD_MIN) / _HARD_BAND_COUNT)))
            if band is not None:
                keyword_hits.append((polarity_key, difficulty, band))

        if polarity_key == "negative":
            value = str(inst.get("value") or "").strip()
            if value:
                span = _find_value_span(text, value)
                if span is not None:
                    label = _observed_label(text, span[0])
                    if label:
                        negative_labels.append(label)

    result["position"] = positions
    result["keyword"] = keyword_hits
    result["negative_labels"] = negative_labels
    return result


def compute_quality_metrics(ctx: VersionContext, expected_lang: str | None) -> dict:
    """One fused, exhaustive pass over every document's metadata.json + its
    plain_text, feeding all five metrics' accumulators at once - parallelized
    across a thread pool since this is I/O-dominated (see
    _process_one_document's docstring for the measured why)."""
    import os
    from concurrent.futures import ThreadPoolExecutor

    try:
        from datasketch import MinHash  # noqa: F401 - availability probe only
        want_minhash = True
    except ImportError:
        want_minhash = False
    try:
        from langdetect import detect as _detect_lang
    except ImportError:
        _detect_lang = None

    tasks = [
        (fs.name, polarity_name, polarity, mpath)
        for fs in ctx.folder_sets()
        for polarity_name, polarity in (("positive", fs.positive), ("negative", fs.negative))
        for mpath in file_cache.list_dir(polarity.metadata, "*.json")
    ]
    if not tasks:
        return {}

    minhashes: dict[str, object] = {}
    purity_assessed = 0
    purity_unexpected = 0
    purity_checked_docs = 0
    position_bands: dict[str, Counter] = {"positive": Counter(), "negative": Counter()}
    keyword_bands: dict[tuple[str, str], Counter] = {}
    negative_label_total = 0
    negative_labels: set[str] = set()
    total_docs = 0

    max_workers = min(32, max(4, (os.cpu_count() or 4) * 4))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(_process_one_document, fs_name, polarity_name, polarity, mpath,
                        expected_lang, want_minhash, _detect_lang)
            for fs_name, polarity_name, polarity, mpath in tasks
        ]
        for future in futures:
            result = future.result()
            if result is None:
                continue
            total_docs += 1
            if result["minhash_id"]:
                minhashes[result["minhash_id"]] = result["minhash"]
            if result["purity_checked"]:
                purity_checked_docs += 1
                purity_assessed += result["purity_assessed"]
                purity_unexpected += result["purity_unexpected"]
            for polarity_key, band in result["position"]:
                position_bands[polarity_key][band] += 1
            for polarity_key, difficulty, band in result["keyword"]:
                keyword_bands.setdefault((polarity_key, difficulty), Counter())[band] += 1
            for label in result["negative_labels"]:
                negative_label_total += 1
                negative_labels.add(label)

    return {
        "template_cluster_rate": _finalize_cluster_rate(minhashes, total_docs),
        "target_language_purity": _finalize_purity(
            purity_assessed, purity_unexpected, purity_checked_docs, expected_lang),
        "sit_position_diversity": _finalize_position_diversity(position_bands),
        "keyword_proximity_diversity": _finalize_keyword_diversity(keyword_bands),
        "negative_label_uniqueness": _finalize_label_uniqueness(negative_label_total, negative_labels),
    }


def _finalize_cluster_rate(minhashes: dict, total_docs: int) -> dict:
    if not minhashes or total_docs == 0:
        return {}
    from datasketch import MinHashLSH

    doc_ids = list(minhashes)
    lsh = MinHashLSH(threshold=_LSH_THRESHOLD, num_perm=_NUM_PERM)
    for doc_id in doc_ids:
        lsh.insert(doc_id, minhashes[doc_id])

    edges: list[tuple[str, str, float]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for doc_id in doc_ids:
        for other_id in lsh.query(minhashes[doc_id]):
            if other_id == doc_id:
                continue
            pair = tuple(sorted((doc_id, other_id)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            edges.append((pair[0], pair[1], minhashes[doc_id].jaccard(minhashes[other_id])))

    def _cluster_rate(min_jaccard: float) -> tuple[float, int]:
        parent = {doc_id: doc_id for doc_id in doc_ids}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b, j in edges:
            if j >= min_jaccard:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb

        sizes = Counter(find(doc_id) for doc_id in doc_ids)
        clustered = sum(size for size in sizes.values() if size >= 2)
        return clustered / total_docs, clustered

    rate_75, clustered_75 = _cluster_rate(0.75)
    rate_85, _ = _cluster_rate(0.85)
    rate_90, _ = _cluster_rate(0.90)
    return {
        "key": "template_cluster_rate",
        "label": "Normalized-Template Cluster Rate @ .75",
        "score": rate_75,
        "grade": "report_only",
        "gate": False,
        "higher_is_better": False,
        "sample_note": "All documents",
        "total": total_docs,
        "clustered_docs": clustered_75,
        "rate_85": rate_85,
        "rate_90": rate_90,
        "detail": (
            f"{clustered_75}/{total_docs} document(s) ({rate_75 * 100:.1f}%) belong to a "
            f"copy-like template cluster at Jaccard >=.75 (diagnostics: {rate_85 * 100:.1f}% "
            f"@ .85, {rate_90 * 100:.1f}% @ .90)."
        ),
    }


def _finalize_purity(assessed: int, unexpected: int, checked_docs: int, expected_lang: str | None) -> dict:
    if not expected_lang or assessed == 0:
        return {}
    score = 1 - (unexpected / assessed)
    return {
        "key": "target_language_purity",
        "label": "Target-Language Purity",
        "score": score,
        "grade": _grade(score, 0.995, 0.990, 0.970),
        "gate": False,  # advisory
        "higher_is_better": True,
        "sample_note": "All documents",
        "expected_language": expected_lang,
        "checked_docs": checked_docs,
        "assessed_tokens": assessed,
        "unexpected_tokens": unexpected,
        "detail": (
            f"{score * 100:.1f}% purity ({unexpected:,}/{assessed:,} assessed token(s) not "
            f"detected as '{expected_lang}') across {checked_docs:,} document(s)."
        ),
    }


def _finalize_position_diversity(position_bands: dict[str, Counter]) -> dict:
    entropies = {pol: _entropy01(counts) for pol, counts in position_bands.items() if counts}
    if not entropies:
        return {}
    score = min(entropies.values())
    bits = "; ".join(f"{pol}={h:.3f}" for pol, h in sorted(entropies.items()))
    return {
        "key": "sit_position_diversity",
        "label": "SIT Position Diversity",
        "score": score,
        "grade": _grade(score, 0.85, 0.75, 0.60),
        "gate": True,
        "higher_is_better": True,
        "sample_note": "Every located SIT occurrence",
        "by_polarity": entropies,
        "detail": f"Headline (min across polarities) = {score:.3f}. Per-polarity entropy: {bits}.",
    }


def _finalize_keyword_diversity(keyword_bands: dict[tuple[str, str], Counter]) -> dict:
    entropies = {slice_key: _entropy01(counts) for slice_key, counts in keyword_bands.items() if counts}
    entropies = {k: v for k, v in entropies.items() if v is not None}
    if not entropies:
        return {}
    score = sum(entropies.values()) / len(entropies)
    bits = "; ".join(f"{pol} {diff}={h:.3f}" for (pol, diff), h in sorted(entropies.items()))
    return {
        "key": "keyword_proximity_diversity",
        "label": "Keyword Proximity Diversity",
        "score": score,
        "grade": _grade(score, 0.70, 0.55, 0.30),
        "gate": True,
        "higher_is_better": True,
        "sample_note": "Every SIT occurrence with an allowed, in-range nearest keyword",
        "by_slice": {f"{pol} {diff}": h for (pol, diff), h in entropies.items()},
        "detail": f"Mean entropy across {len(entropies)} applicable polarity x difficulty slice(s) "
                  f"= {score:.3f}. {bits}.",
    }


def _finalize_label_uniqueness(total: int, distinct_labels: set[str]) -> dict:
    if total == 0:
        return {}
    score = len(distinct_labels) / total
    return {
        "key": "negative_label_uniqueness",
        "label": "Negative Observed-Label Wording Uniqueness",
        "score": score,
        "grade": _grade(score, 0.70, 0.55, 0.35),
        "gate": True,
        "higher_is_better": True,
        "sample_note": "Every located negative SIT occurrence",
        "distinct": len(distinct_labels),
        "total": total,
        "detail": f"{len(distinct_labels):,} distinct observed label(s) / {total:,} located "
                  f"negative SIT occurrence(s) = {score:.3f}.",
    }
