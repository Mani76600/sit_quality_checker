from qc.checks.scenario_id_checks import _normalize, _stem_slug


def test_south_africa_convention_slug_and_discriminator_hash():
    stem = "academic-record-correction-amendment-and-reappro-0-65fd3ec6cf55"
    scenario_id = "academic-record-correction-amendment-and-reappro-092b30ea17ce"
    assert _normalize(scenario_id).startswith(_stem_slug(stem))


def test_sweden_convention_word_truncation_and_sequential_index():
    stem = "beredning-av-beslut-yttrande-00000059"
    scenario_id = "beredning-av-beslut-yttrande-kontroll-av-betalni-b9a770e7d408"
    assert _normalize(scenario_id).startswith(_stem_slug(stem))


def test_south_korea_convention_midword_truncation_and_hash():
    stem = "consent-and-disclo-0c73786f50c9"
    scenario_id = "consent_and_disclosure_validation_corrective_action_verification"
    assert _normalize(scenario_id).startswith(_stem_slug(stem))


def test_non_ascii_stem_vs_slugified_scenario_id():
    # scenario_id slugifies non-ASCII to '-'; stem keeps the real character.
    stem = "aktivering-av-tjänst-00000473"
    scenario_id = "aktivering-av-tj-nst-samordningsmeddelande-rappo-d57a22ec0632"
    assert _normalize(scenario_id).startswith(_stem_slug(stem))


def test_genuine_mismatch_is_still_caught():
    stem = "invoice-reconciliation-case-9f1a2b3c4d5e"
    scenario_id = "totally-unrelated-scenario-name-092b30ea17ce"
    assert not _normalize(scenario_id).startswith(_stem_slug(stem))
