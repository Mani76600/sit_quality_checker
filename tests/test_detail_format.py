from qc.detail_format import MAX_EXAMPLES_SHOWN, split_detail


def test_splits_lead_and_examples():
    detail = "3 issue(s), e.g. ['a.json', 'b.json', 'c.json']"
    lead, items = split_detail(detail)
    assert lead == "3 issue(s),"
    assert items == ["a.json"]


def test_examples_are_capped_regardless_of_how_many_the_check_collected():
    # The lead sentence keeps the real total count - only the redundant
    # example bullets are capped, since one representative example is
    # enough once the count is already stated.
    detail = "2080 record(s) have a non-numeric confidence value, e.g. " + repr(
        [f"index {i}: 'imported_historical_detection'" for i in range(10)])
    lead, items = split_detail(detail)
    assert lead == "2080 record(s) have a non-numeric confidence value,"
    assert len(items) == MAX_EXAMPLES_SHOWN == 1
    assert items == ["index 0: 'imported_historical_detection'"]


def test_handles_strings_with_embedded_quotes():
    detail = "1 issue(s), e.g. [\"doc.json: value='abc' vs stem='xyz'\"]"
    lead, items = split_detail(detail)
    assert items == ["doc.json: value='abc' vs stem='xyz'"]


def test_no_examples_pattern_returns_original_text_unchanged():
    detail = "All 15000 records are language 'en'."
    lead, items = split_detail(detail)
    assert lead == detail
    assert items == []


def test_non_list_bracket_content_is_left_alone():
    # e.g. a dict repr - not a list, must not be misparsed as one
    detail = "mismatch found, e.g. {'a': 1, 'b': 2}"
    lead, items = split_detail(detail)
    assert lead == detail
    assert items == []


def test_empty_detail():
    assert split_detail("") == ("", [])
