from qc.checks.content_verification import _find_value_span, _proximity_window, _sentence_context


def test_find_value_span_exact():
    text = "The record shows ID 5809235588083 was verified."
    span = _find_value_span(text, "5809235588083")
    assert span == (20, 33)
    assert text[20:33] == "5809235588083"


def test_find_value_span_reformatted():
    text = "Account number 580-923-5588-083 was flagged."
    span = _find_value_span(text, "5809235588083")
    assert span is not None
    start, end = span
    assert text[start:end] == "580-923-5588-083"


def test_find_value_span_not_present():
    assert _find_value_span("nothing relevant here", "5809235588083") is None


def test_proximity_window_respects_recorded_distance():
    # Keyword sits ~214 chars after the value - a fixed one-sentence window
    # would miss it, which is exactly the false-positive this function fixes.
    value = "9404129255183"
    filler = "x" * 190
    text = f"The signed package includes {value} for the applicant. {filler} Identification verification was completed."
    start = text.find(value)
    end = start + len(value)
    window = _proximity_window(text, start, end, distance=214, direction="after")
    assert "identification" in window.lower()


def test_proximity_window_direction_before_does_not_leak_after_text():
    text = "Identification card noted earlier. VALUE123 appears here with no nearby keyword after it."
    start = text.find("VALUE123")
    end = start + len("VALUE123")
    window = _proximity_window(text, start, end, distance=50, direction="after")
    assert "identification" not in window.lower()


def test_sentence_context_splits_before_current_after():
    text = "First sentence here. The value 42 appears in this sentence. Third sentence follows."
    idx = text.find("42")
    before, current, after = _sentence_context(text, idx, idx + 2)
    assert before == "First sentence here."
    assert "42" in current
    assert after == "Third sentence follows."
