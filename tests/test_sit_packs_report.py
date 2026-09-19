from reference.sit_packs_report import _match_score, _qualifiers_conflict, _tokens


def test_acronym_token_lets_abbreviated_pack_match_full_name():
    # "us_ssn" has almost no word-overlap with the fully spelled-out name -
    # only the acronym token bridges them.
    pack_tokens = _tokens("us_ssn")
    full_tokens = _tokens("US Social Security Number")
    assert _match_score(pack_tokens, full_tokens) == 1.0


def test_country_qualifier_veto_rejects_cross_country_match():
    us_tokens = _tokens("US Social Security Number")
    eu_tokens = _tokens("EU Social Security Number")
    assert _qualifiers_conflict(us_tokens, eu_tokens)
    assert _match_score(us_tokens, eu_tokens) > 0  # score alone would match...
    # ...which is exactly why callers must check the qualifier veto too.


def test_dotted_abbreviation_us_is_recognized_as_qualifier():
    # "U.S." must not tokenize into separate single letters {u, s} - that
    # silently disabled the qualifier veto against real MCE keyword-list
    # entries spelled "U.S. Social Security Number (SSN)".
    tokens = _tokens("U.S. Social Security Number (SSN)")
    assert "us" in tokens
    assert "u" not in tokens
    assert "s" not in tokens


def test_generic_word_only_overlap_is_rejected():
    # A made-up SIT name sharing only the generic word "passport" with a
    # real 2-token "<country>_passport" pack must not match.
    fake_tokens = _tokens("Atlantis Secret Passport")
    real_pack_tokens = _tokens("canada_passport")
    assert _match_score(fake_tokens, real_pack_tokens) == 0.0


def test_shared_qualifier_and_number_alone_is_rejected():
    # "eu" + "number" alone matched two unrelated SIT categories in testing
    # (EU Passport Number vs EU Social Security Number).
    a = _tokens("EU Passport Number")
    b = _tokens("EU Social Security Number")
    assert _match_score(a, b) == 0.0


def test_substantive_word_overlap_still_matches():
    a = _tokens("Canada Social Insurance Number")
    b = _tokens("canada_social_insurance_number")
    assert _match_score(a, b) == 1.0
