from qc.checks.export_summary import _get_counts, _get_ground_truth, _label_value


def test_get_counts_lowercase():
    data = {"counts": {"positive": 10, "negative": 20, "total": 30}}
    assert _get_counts(data) == {"positive": 10, "negative": 20, "total": 30}


def test_get_counts_titlecase_fallback():
    data = {"counts": {"Positive": 10, "Negative": 20, "Total": 30}}
    assert _get_counts(data) == {"positive": 10, "negative": 20, "total": 30}


def test_get_ground_truth():
    data = {"normalized_context": {"records": 30, "ground_truth": {"true": 10, "false": 20}}}
    assert _get_ground_truth(data) == (10, 20, 30)


def test_label_value_variants():
    assert _label_value({"label": "Positive"}) == "positive"
    assert _label_value({"polarity": "Negative"}) == "negative"
    assert _label_value({"ground_truth": "true"}) == "positive"
    assert _label_value({"ground_truth": "false"}) == "negative"
    assert _label_value({"label": True}) == "positive"
    assert _label_value({"label": False}) == "negative"
    assert _label_value({}) is None
