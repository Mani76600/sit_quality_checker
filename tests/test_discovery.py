import json
from pathlib import Path

from qc.discovery import _classify, find_version_dirs


def _write_export_summary(version_dir: Path, sit_name: str) -> None:
    (version_dir / "export_summary.json").write_text(
        json.dumps({"sit_name": sit_name, "counts": {"Positive": 1, "Negative": 1, "total": 2}}),
        encoding="utf-8",
    )


def test_classify_english_layout_confirmed_by_content(tmp_path):
    version_dir = tmp_path / "South Africa Identification Number" / "Version_20260911_1945"
    version_dir.mkdir(parents=True)
    _write_export_summary(version_dir, "South Africa Identification Number")

    c = _classify(version_dir, is_direct=False)
    assert c.sit_name == "South Africa Identification Number"
    assert c.language is None
    assert c.layout == "english"
    assert "export_summary.json" in c.sit_name_source


def test_classify_multilingual_layout_confirmed_by_content(tmp_path):
    # "Romanized Chinese" is deliberately NOT in the hardcoded KNOWN_LANGUAGES
    # list - this is exactly the case the user reported: a language folder
    # whose name a static list can't recognize, so it must be resolved from
    # the run's own export_summary.json instead.
    version_dir = tmp_path / "Taiwan Passport Number" / "Romanized Chinese" / "Version_20260911_1945"
    version_dir.mkdir(parents=True)
    _write_export_summary(version_dir, "Taiwan Passport Number")

    c = _classify(version_dir, is_direct=False)
    assert c.sit_name == "Taiwan Passport Number"
    assert c.language == "Romanized Chinese"
    assert c.layout == "multilingual"
    assert "export_summary.json" in c.sit_name_source


def test_classify_multilingual_known_language_fallback_without_content(tmp_path):
    version_dir = tmp_path / "Sweden National ID" / "Swedish" / "Version_20260915_2135"
    version_dir.mkdir(parents=True)
    # No export_summary.json at all - must fall back to the known-language list.
    c = _classify(version_dir, is_direct=False)
    assert c.sit_name == "Sweden National ID"
    assert c.language == "Swedish"
    assert c.layout == "multilingual"


def test_classify_direct_version_dir_with_content(tmp_path):
    version_dir = tmp_path / "some_random_export_folder" / "Version_20260101_0000"
    version_dir.mkdir(parents=True)
    _write_export_summary(version_dir, "Japan Passport Number")

    c = _classify(version_dir, is_direct=True)
    assert c.sit_name == "Japan Passport Number"
    assert c.layout == "unknown"
    assert "directly" in c.note.lower()


def test_classify_direct_version_dir_without_content(tmp_path):
    version_dir = tmp_path / "some_folder" / "Version_20260101_0000"
    version_dir.mkdir(parents=True)
    c = _classify(version_dir, is_direct=True)
    assert c.sit_name == "some_folder"
    assert "unconfirmed" in c.sit_name_source
    assert "directly" in c.note.lower()


def test_find_version_dirs_nested(tmp_path):
    v1 = tmp_path / "SIT A" / "Version_20260101_0000"
    v2 = tmp_path / "SIT B" / "French" / "Version_20260101_0000"
    v1.mkdir(parents=True)
    v2.mkdir(parents=True)
    (tmp_path / "SIT A" / "not_a_version_dir").mkdir()

    found = find_version_dirs(tmp_path)
    assert {p for p, _ in found} == {v1, v2}
    assert all(is_direct is False for _, is_direct in found)


def test_find_version_dirs_root_is_version_dir(tmp_path):
    version_dir = tmp_path / "Version_20260101_0000"
    version_dir.mkdir()
    found = find_version_dirs(version_dir)
    assert found == [(version_dir, True)]


def test_find_version_dirs_missing_root(tmp_path):
    assert find_version_dirs(tmp_path / "does_not_exist") == []
