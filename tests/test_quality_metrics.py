"""Tests for qc/quality_metrics.py - the five graded Quality Metrics (see
that module's own docstring for the full spec and documented
approximations). The end-to-end test below is deliberately built from real
on-disk files with the exact real-world quirks that caused two genuine bugs
during development: sit_values/lookalike_values are dicts-of-lists (not flat
lists), and value_start_offset/keyword_distance are string-typed in real
metadata.json - both are reproduced here so a regression would be caught
again.
"""

import json
from collections import Counter

import pytest

from qc import quality_metrics as qm
from qc.context import VersionContext


def _make_ctx(tmp_path, version_name="Version_20260101_0000"):
    return VersionContext(version_dir=tmp_path / version_name, sit_name="Test SIT",
                           language=None, layout="english")


def _write_doc(ctx, polarity, stem, *, sit_category, text, instances, sit_values=None):
    polarity_root = ctx.version_dir / polarity.capitalize()
    metadata_dir = polarity_root / "outputs" / "metadata"
    plain_text_dir = polarity_root / "outputs" / "parsed_raw_doc" / "plain_text"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    plain_text_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{stem}.txt"
    (plain_text_dir / f"{filename}.txt").write_text(text, encoding="utf-8")
    doc = {
        "filename": filename,
        "sit_category": sit_category,
        "sit_values": sit_values or {},
        "lookalike_values": {} if polarity == "positive" else (sit_values or {}),
        "instances": instances,
    }
    (metadata_dir / f"{stem}.json").write_text(json.dumps(doc), encoding="utf-8")


# ----------------------------------------------------------------- units ----

def test_evenly_sample_noop_under_cap():
    assert qm._evenly_sample([1, 2, 3], 10) == [1, 2, 3]


def test_evenly_sample_spans_the_whole_sequence_not_just_the_start():
    sampled = qm._evenly_sample(list(range(100)), 10)
    assert len(sampled) == 10
    assert sampled[0] == 0
    assert sampled[-1] >= 90


@pytest.mark.parametrize("value,expected", [
    ("355", 355.0), (355, 355.0), (355.5, 355.5),
    ("not a number", None), (True, None), (None, None),
])
def test_to_number_handles_string_typed_real_data(value, expected):
    """instances[].value_start_offset/keyword_distance are string-typed in
    real metadata.json (confirmed on a real South Africa sample) - the bug
    this guards against silently dropped every single instance from the
    position/keyword-proximity metrics."""
    assert qm._to_number(value) == expected


def test_entropy01_uniform_distribution_is_one():
    assert qm._entropy01(Counter({0: 10, 1: 10, 2: 10, 3: 10, 4: 10})) == pytest.approx(1.0)


def test_entropy01_single_occupied_bucket_is_zero():
    assert qm._entropy01(Counter({2: 50})) == 0.0


def test_entropy01_empty_counter_is_none():
    assert qm._entropy01(Counter()) is None


@pytest.mark.parametrize("score,expected", [
    (0.996, "strong"), (0.992, "acceptable"), (0.975, "weak"), (0.5, "critical"), (None, "n/a"),
])
def test_grade_thresholds(score, expected):
    assert qm._grade(score, 0.995, 0.990, 0.970) == expected


def test_planted_values_flattens_dict_of_lists():
    """sit_values/lookalike_values are dicts keyed by SIT key, not flat
    lists - confirmed on real sample data; the bug this guards against
    crashed compute_quality_metrics outright on every real corpus."""
    doc = {"sit_values": {"us_ssn": ["123-45-6789"]}, "lookalike_values": {"other": ["999"]}}
    assert qm._planted_values(doc) == {"123-45-6789", "999"}


def test_planted_values_tolerates_missing_or_empty_fields():
    assert qm._planted_values({}) == set()
    assert qm._planted_values({"sit_values": {}, "lookalike_values": {}}) == set()


def test_find_value_span_exact_and_reformatted_and_missing():
    assert qm._find_value_span("the code is 12345 here", "12345") == (12, 17)
    assert qm._find_value_span("the code is 1-2-3-4-5 here", "12345") is not None
    assert qm._find_value_span("nothing here", "12345") is None


def test_observed_label_extracts_the_lead_in_phrase():
    text = "Some preamble sentence. The Account Number: 9876543210 was verified."
    span = qm._find_value_span(text, "9876543210")
    label = qm._observed_label(text, span[0])
    assert label is not None
    assert "account number" in label


# ------------------------------------------------------------ end-to-end ----

def _padded_doc_with_value_at(value: str, offset: int, total_len: int) -> str:
    """A document of exactly `total_len` characters with `value` placed so
    it starts at `offset` - lets position-band tests target a specific one
    of the 5 bands deterministically."""
    before = ("filler word " * total_len)[:offset]
    after_len = total_len - offset - len(value)
    after = ("more filler text " * total_len)[:max(0, after_len)]
    return before + value + after


def _padded_doc_with_labeled_value_at(lead_in: str, value: str, offset: int, total_len: int) -> str:
    """Same idea as _padded_doc_with_value_at, but with a controlled
    lead-in phrase immediately before the value, cut off from the filler
    padding by a real sentence boundary (". ") - so _observed_label's
    nearest-preceding-sentence-break cut applies exactly like it would on
    a real document, and the extracted label doesn't vary by offset
    (how much filler precedes the sentence break is irrelevant once cut)."""
    filler = ("filler word " * total_len)[:max(0, offset - len(lead_in) - 3)]
    before = filler + ". " + lead_in + " "
    after_len = total_len - len(before) - len(value)
    after = ("more filler text " * total_len)[:max(0, after_len)]
    return before + value + after


def test_compute_quality_metrics_end_to_end(tmp_path):
    ctx = _make_ctx(tmp_path)

    # Four positive docs with their value planted at four different position
    # bands (0, 1, 3, 4 of 5) and four different easy-band keyword distances,
    # so position/keyword entropy is non-degenerate (not a single bucket).
    for i, (offset, keyword_distance) in enumerate([(50, "10"), (150, "60"), (300, "110"), (450, "160")]):
        value = f"900000000{i}"
        text = _padded_doc_with_value_at(value, offset, 500)
        _write_doc(ctx, "positive", f"pos{i}", sit_category="easy positive", text=text,
                   sit_values={"test_sit": [value]},
                   instances=[{
                       "value": value, "value_start_offset": str(text.index(value)),
                       "difficulty": "easy", "keyword_present": True,
                       "keyword_distance": keyword_distance,
                   }])

    # Four negative docs: two share the same observed lead-in phrase, two
    # have distinct ones -> expect 3 distinct / 4 total = 0.75 uniqueness.
    # Also spread across four different position bands (like the positive
    # docs above) so position-diversity entropy isn't degenerately 0 for
    # this polarity either.
    lead_ins = ["Account Number:", "Account Number:", "Reference ID:", "Ticket Code:"]
    offsets = [50, 150, 300, 450]
    for i, (lead_in, offset) in enumerate(zip(lead_ins, offsets)):
        value = f"800000000{i}"
        text = _padded_doc_with_labeled_value_at(lead_in, value, offset, 500)
        _write_doc(ctx, "negative", f"neg{i}", sit_category="easy negative", text=text,
                   sit_values={"test_sit": [value]},
                   instances=[{
                       "value": value, "value_start_offset": str(text.index(value)),
                       "difficulty": "easy", "keyword_present": True, "keyword_distance": "5",
                   }])

    result = qm.compute_quality_metrics(ctx, "en")

    assert set(result) == {
        "template_cluster_rate", "target_language_purity", "sit_position_diversity",
        "keyword_proximity_diversity", "negative_label_uniqueness",
    }

    cluster = result["template_cluster_rate"]
    assert cluster["total"] == 8
    assert cluster["grade"] == "report_only"
    assert cluster["gate"] is False

    position = result["sit_position_diversity"]
    assert position["gate"] is True
    assert 0.0 < position["score"] <= 1.0  # non-degenerate: not all in one band

    keyword = result["keyword_proximity_diversity"]
    assert keyword["gate"] is True
    assert 0.0 < keyword["score"] <= 1.0

    uniqueness = result["negative_label_uniqueness"]
    assert uniqueness["gate"] is True
    assert uniqueness["total"] == 4
    assert uniqueness["distinct"] == 3
    assert uniqueness["score"] == pytest.approx(0.75)


def test_compute_quality_metrics_empty_corpus_returns_empty_dict(tmp_path):
    ctx = _make_ctx(tmp_path)
    assert qm.compute_quality_metrics(ctx, "en") == {}


def test_compute_quality_metrics_without_expected_language_skips_purity(tmp_path):
    ctx = _make_ctx(tmp_path)
    value = "9000000000"
    text = _padded_doc_with_value_at(value, 50, 500)
    _write_doc(ctx, "positive", "pos0", sit_category="easy positive", text=text,
               sit_values={"test_sit": [value]},
               instances=[{
                   "value": value, "value_start_offset": str(text.index(value)),
                   "difficulty": "easy", "keyword_present": True, "keyword_distance": "10",
               }])

    result = qm.compute_quality_metrics(ctx, None)
    assert result["target_language_purity"] == {}
