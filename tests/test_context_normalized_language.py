from pathlib import Path

from qc.checks.context_normalized import _check_languages, _record_language
from qc.context import VersionContext
from qc.models import Status

_JF = Path("sit.json")


def _ctx(tmp_path) -> VersionContext:
    return VersionContext(version_dir=tmp_path, sit_name="Test SIT", language=None, layout="english")


def test_record_language_falls_back_to_nested_context_window():
    # A schema variant with no top-level "language" field at all - only
    # nested inside a context window (the check must not go silent here).
    rec = {"context_500": {"text": "...", "language": "sv"}}
    assert _record_language(rec) == "sv"


def test_record_language_prefers_top_level():
    rec = {"language": "en", "context_500": {"language": "sv"}}
    assert _record_language(rec) == "en"


def test_check_languages_never_silently_returns_nothing(tmp_path):
    # Real bug found in production: records with no language field anywhere
    # (top-level or nested) caused this check to return [] with zero
    # results - indistinguishable from the check never having run. It must
    # always report something.
    ctx = _ctx(tmp_path)
    data = [{"value": "123"}, {"value": "456"}]  # no language field at all
    results = _check_languages(ctx.agreements, _JF, data, {}, "Agreements")
    assert len(results) >= 1
    assert results[0].status == Status.INFO
    assert "no" in results[0].detail.lower() or "language" in results[0].title.lower()


def test_check_languages_detects_contamination_with_nested_only_field(tmp_path):
    ctx = _ctx(tmp_path)
    data = [
        {"value": "1", "document_name": "a.docx", "ground_truth": "true",
         "context_100": {"language": "sv"}},
        {"value": "2", "document_name": "b.docx", "ground_truth": "true",
         "context_100": {"language": "sv"}},
        {"value": "3", "document_name": "c.docx", "ground_truth": "true",
         "context_100": {"language": "no"}},  # contamination, nested-only
    ]
    results = _check_languages(ctx.agreements, _JF, data, {}, "Agreements")
    warn_results = [r for r in results if r.status == Status.WARN]
    assert warn_results, "expected a contamination WARN"
    assert "no" in warn_results[0].detail
    assert "c.docx" in warn_results[0].detail
