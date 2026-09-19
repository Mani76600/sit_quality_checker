"""Standalone reference-completeness report for sit_packs/ and SIT Specs/.

This is deliberately NOT part of the per-output-folder PASS/FAIL run: pack/
spec completeness is a property of the *reference material*, not of any one
pipeline run's output, so it lives in its own tab/report instead of being
blended into a Version_* directory's checklist.

Expected pack file set is derived from inspecting real sit_packs/ contents
(us_ssn has the full set; several packs only carry a subset) - flagged here
as missing rather than assumed mandatory, since the packs are not uniform
today.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from qc.jsonio import read_json

# Shown to the user when a SIT (or the specific language for it) can't be
# found anywhere in the provided reference material.
SIT_NOT_FOUND_LINK = (
    "https://microsoft.sharepoint.com/teams/DSCGPTeam/Shared%20Documents/Forms/AllItems.aspx?"
    "id=%2Fteams%2FDSCGPTeam%2FShared%20Documents%2FDS%20Product%20Specs%2FData%20Classification%2F"
    "Q1%20specs%2FClassifier%20improvements%2FBase%20SIT%2FFinal&p=true&"
    "share=cgqyBhrW2a%5FeSIpx5NIdbasGEgUCfnG636n6%2DjeGp2ioblCLPA&"
    "TeamsCID=aae691d0%2D5eed%2D4441%2Daa1a%2D342e175a11b7&OR=Teams%2DHL&"
    "CT=1787596696849&CID=dc7334a2%2D7057%2D1000%2Db2fd%2D325088e92427&cidOR=SPO&"
    "ovuser=72f988bf%2D86f1%2D41af%2D91ab%2D2d7cd011db47%2Cv%2Dmjeereddy%40microsoft%2Ecom&"
    "clickparams=eyJBcHBOYW1lIjoiVGVhbXMtRGVza3RvcCIsIkFwcFZlcnNpb24iOiI0OS8yNjA4MTMxOTMxOSIs"
    "Ikhhc0ZlZGVyYXRlZFVzZXIiOmZhbHNlfQ%3D%3D"
)

# Best-effort language-name -> ISO 639-1 code, for matching against
# sit_packs/*/keywords.csv's "locale" column (which uses codes like
# "en", "de", "zh-CN", not language names).
LANGUAGE_TO_CODE = {
    "english": "en", "german": "de", "spanish": "es", "french": "fr", "italian": "it",
    "japanese": "ja", "polish": "pl", "arabic": "ar", "chinese": "zh", "czech": "cs",
    "finnish": "fi", "swedish": "sv", "portuguese": "pt", "dutch": "nl", "russian": "ru",
    "korean": "ko", "hindi": "hi", "turkish": "tr", "norwegian": "no", "danish": "da",
    "romanian": "ro", "hungarian": "hu", "greek": "el", "hebrew": "he", "thai": "th",
    "vietnamese": "vi", "indonesian": "id", "ukrainian": "uk", "bulgarian": "bg",
    "slovak": "sk", "slovenian": "sl", "croatian": "hr", "serbian": "sr",
}

EXPECTED_PACK_FILES = [
    "contract.yaml",
    "keywords.csv",
    "positive_values.csv",
    "negative_values.csv",
    "golden_cases.jsonl",
    "scenarios.yaml",
    "README.md",
]


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


_STOPWORDS = {"sit", "definition", "and", "testdata", "test", "data", "combined", "v1", "final", "spec"}


def _tokens(name: str) -> set[str]:
    """Word tokens, plus an acronym token for the non-qualifier words (e.g.
    "US Social Security Number" -> {us, social, security, number, ssn}) so
    heavily-abbreviated pack names like "us_ssn" (tokens {us, ssn}) can still
    match a fully spelled-out name via the shared acronym, not just shared
    words - confirmed necessary against real sit_packs naming."""
    # "U.S." / "U.K." would otherwise split into single letters {u, s} on the
    # generic non-alnum split below and never be recognized as the "us"/"uk"
    # qualifier - strip periods first so they merge back into one token
    # (confirmed necessary: the 213 MCE keyword list spells it
    # "U.S. Social Security Number (SSN)").
    normalized = name.lower().replace(".", "")
    parts = re.split(r"[^a-z0-9]+", normalized)
    words = [p for p in parts if p and p not in _STOPWORDS]
    tokens = set(words)
    non_qualifier_words = [w for w in words if w not in COUNTRY_QUALIFIERS]
    if len(non_qualifier_words) >= 2:
        acronym = "".join(w[0] for w in non_qualifier_words)
        if 2 <= len(acronym) <= 6:
            tokens.add(acronym)
    return tokens


def _match_score(a_tokens: set[str], b_tokens: set[str]) -> float:
    """Containment coefficient (intersection / smaller set's size), not a
    fixed-direction fraction - so a short, heavily-abbreviated name (e.g.
    "us_ssn") can still score a full match against a long descriptive name
    if every one of its own tokens is found there, regardless of how much
    extra vocabulary the longer name has.

    For a very short name (<=2 tokens, e.g. a 2-word pack name), a fractional
    score is too easy to satisfy by sharing just one generic word (a made-up
    SIT name containing the word "passport" scored an exact 0.5 against a
    real 2-token "<country>_passport" pack in testing) - so short names
    require ALL of their tokens to be found, not just half."""
    if not a_tokens or not b_tokens:
        return 0.0
    inter = a_tokens & b_tokens
    # A match built ONLY out of a shared qualifier + a shared generic word
    # (e.g. "eu" + "number") matched two unrelated SIT categories - "EU
    # Passport Number" against "EU Social Security Number" - in testing.
    # Require at least one shared token that is neither a generic word nor
    # a country qualifier, so the match is grounded in something specific
    # to this SIT category.
    if not (inter - GENERIC_WORDS - COUNTRY_QUALIFIERS):
        return 0.0
    smaller = min(len(a_tokens), len(b_tokens))
    if smaller <= 2:
        return 1.0 if len(inter) == smaller else 0.0
    return len(inter) / smaller


# A country/region qualifier is the single most distinguishing word for SITs
# that otherwise share nearly all their vocabulary (e.g. "US Social Security
# Number" vs "EU Social Security Number"). Generic word-overlap scoring alone
# confidently matched the wrong one of these in testing (shared 3/4 tokens
# "social/security/number", differing only in us/eu) - so if BOTH names carry
# a recognized qualifier and they disagree, the match is vetoed outright,
# regardless of how high the overlap score is otherwise.
COUNTRY_QUALIFIERS = {
    "us", "usa", "uk", "eu", "canada", "china", "chinese", "japan", "japanese",
    "korea", "korean", "india", "indian", "mexico", "mexican", "brazil", "brazilian",
    "france", "french", "germany", "german", "sweden", "swedish", "norway", "norwegian",
    "denmark", "danish", "finland", "finnish", "poland", "polish", "russia", "russian",
    "australia", "australian", "taiwan", "taiwanese", "singapore", "singaporean",
    "ireland", "irish", "italy", "italian", "spain", "spanish", "netherlands", "dutch",
    "belgium", "belgian", "austria", "austrian", "switzerland", "swiss", "portugal",
    "portuguese", "greece", "greek", "turkey", "turkish", "israel", "israeli", "saudi",
    "uae", "qatar", "egypt", "egyptian", "nigeria", "nigerian", "kenya", "kenyan",
    "argentina", "argentine", "chile", "chilean", "colombia", "colombian", "peru",
    "peruvian", "vietnam", "vietnamese", "thailand", "thai", "malaysia", "malaysian",
    "indonesia", "indonesian", "philippines", "filipino", "ukraine", "ukrainian",
    "africa", "african", "america", "american", "zealand", "newzealand", "hongkong", "hong",
    "scotland", "scottish", "wales", "welsh", "hungary", "hungarian", "czech",
    "slovakia", "slovak", "slovenia", "slovenian", "croatia", "croatian", "serbia",
    "serbian", "romania", "romanian", "bulgaria", "bulgarian", "estonia", "estonian",
    "latvia", "latvian", "lithuania", "lithuanian",
}
GENERIC_WORDS = {
    "number", "card", "id", "license", "passport", "account", "registration",
    "identification", "national", "south", "north", "east", "west",
}


def _qualifier(tokens: set[str]) -> str | None:
    for t in tokens:
        if t in COUNTRY_QUALIFIERS:
            return t
    return None


def _qualifiers_conflict(a: set[str], b: set[str]) -> bool:
    qa, qb = _qualifier(a), _qualifier(b)
    return qa is not None and qb is not None and qa != qb


@dataclass
class PackReport:
    pack_name: str
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    has_matching_spec: bool | None = None


@dataclass
class SpecReport:
    filename: str
    size_bytes: int
    is_encrypted: bool
    matched_pack: str | None


@dataclass
class SitPacksReport:
    packs: list[PackReport] = field(default_factory=list)
    specs: list[SpecReport] = field(default_factory=list)
    packs_without_spec: list[str] = field(default_factory=list)
    specs_without_pack: list[str] = field(default_factory=list)


def _is_ole_encrypted(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
        return head[:4] == b"\xd0\xcf\x11\xe0"
    except OSError:
        return False


def build_report(sit_packs_dir: str | Path, sit_specs_dir: str | Path | None = None) -> SitPacksReport:
    sit_packs_dir = Path(sit_packs_dir)
    report = SitPacksReport()

    pack_dirs = sorted(p for p in sit_packs_dir.iterdir() if p.is_dir()) if sit_packs_dir.exists() else []
    for pack_dir in pack_dirs:
        actual_files = {p.name for p in pack_dir.iterdir() if p.is_file()}
        present = [f for f in EXPECTED_PACK_FILES if f in actual_files]
        missing = [f for f in EXPECTED_PACK_FILES if f not in actual_files]
        extra = sorted(actual_files - set(EXPECTED_PACK_FILES))
        report.packs.append(PackReport(pack_dir.name, present, missing, extra))

    spec_files = []
    if sit_specs_dir:
        sit_specs_dir = Path(sit_specs_dir)
        if sit_specs_dir.exists():
            spec_files = sorted(p for p in sit_specs_dir.iterdir()
                                 if p.is_file() and p.suffix.lower() == ".docx")

    # Best-effort matching only (token overlap on the pack/spec names) - some
    # SITs use an abbreviation in one source and the spelled-out name in the
    # other (e.g. pack "canada_social_insurance_number" vs spec file
    # "Canada_SIN_..."), which no name-only heuristic can reliably catch.
    # Treat the resulting lists as a starting point for manual review, not
    # an authoritative gap analysis.
    pack_token_map = {p.pack_name: _tokens(p.pack_name) for p in report.packs}
    matched_packs: set[str] = set()

    for spec_path in spec_files:
        spec_tokens = _tokens(spec_path.stem)
        matched_pack, _score = _best_match(spec_tokens, pack_token_map)
        if matched_pack:
            matched_packs.add(matched_pack)
        report.specs.append(SpecReport(
            filename=spec_path.name,
            size_bytes=spec_path.stat().st_size,
            is_encrypted=_is_ole_encrypted(spec_path),
            matched_pack=matched_pack,
        ))

    if spec_files:
        report.packs_without_spec = [name for name in pack_token_map if name not in matched_packs]
        report.specs_without_pack = [s.filename for s in report.specs if s.matched_pack is None]

    return report


MATCH_THRESHOLD = 0.5


@dataclass
class SitLanguageLookup:
    sit_name: str
    language: str
    matched_pack: str | None = None
    pack_language_covered: bool | None = None  # None = pack found but couldn't confirm language
    matched_spec: str | None = None
    matched_mce_entry: str | None = None
    mce_language_covered: bool | None = None
    status: str = "not_found"  # "confirmed" | "found_unconfirmed_language" | "not_found"
    message: str = ""
    link: str | None = None


def _best_match(target_tokens: set[str], candidates: dict[str, set[str]]) -> tuple[str | None, float]:
    best_name, best_score = None, 0.0
    for name, tokens in candidates.items():
        if _qualifiers_conflict(target_tokens, tokens):
            continue  # e.g. never match "US ..." against a "EU ..." candidate
        score = _match_score(target_tokens, tokens)
        if score > best_score:
            best_name, best_score = name, score
    return (best_name, best_score) if best_score >= MATCH_THRESHOLD else (None, best_score)


def check_sit_language(sit_packs_dir: str | Path | None, sit_specs_dir: str | Path | None,
                        mce_keywords_path: str | Path | None, sit_name: str,
                        language: str) -> SitLanguageLookup:
    """Best-effort: is this SIT (and specifically this language for it)
    present anywhere in the given reference material? Checks, in order:
    sit_packs/<pack>/keywords.csv locale column, SIT Specs/*.docx filenames
    (language coverage can't be verified - most are DRM-encrypted), and the
    213 MCE keyword list's per-entry language coverage (if its path is
    given) - the MCE list is the only one of the three that records
    language as a readable name rather than a locale code."""
    sit_tokens = _tokens(sit_name)
    lang_norm = (language or "").strip().lower()
    lang_code = LANGUAGE_TO_CODE.get(lang_norm)

    matched_pack = None
    pack_language_covered = None
    if sit_packs_dir:
        packs_dir = Path(sit_packs_dir)
        if packs_dir.exists():
            pack_tokens = {p.name: _tokens(p.name) for p in packs_dir.iterdir() if p.is_dir()}
            matched_pack, _score = _best_match(sit_tokens, pack_tokens)
            if matched_pack:
                kf = packs_dir / matched_pack / "keywords.csv"
                if kf.exists():
                    locales = set()
                    try:
                        with kf.open(encoding="utf-8-sig") as fh:
                            for row in csv.DictReader(fh):
                                loc = (row.get("locale") or "").strip().lower()
                                if loc:
                                    locales.add(loc.split("-")[0])
                    except OSError:
                        pass
                    if lang_code:
                        pack_language_covered = lang_code in locales

    matched_spec = None
    if sit_specs_dir:
        specs_dir = Path(sit_specs_dir)
        if specs_dir.exists():
            spec_tokens = {p.name: _tokens(p.stem) for p in specs_dir.glob("*.docx")}
            matched_spec, _score = _best_match(sit_tokens, spec_tokens)

    matched_mce_entry = None
    mce_language_covered = None
    if mce_keywords_path:
        mce_path = Path(mce_keywords_path)
        if mce_path.exists():
            data, err = read_json(mce_path)
            if not err and isinstance(data, list):
                entry_tokens = {}
                entries_by_key = {}
                for i, entry in enumerate(data):
                    if not isinstance(entry, dict):
                        continue
                    name = entry.get("canonical_sit_name") or entry.get("SIT") or ""
                    if not name:
                        continue
                    key = f"{name}#{i}"
                    entry_tokens[key] = _tokens(name)
                    entries_by_key[key] = entry
                best_key, _score = _best_match(sit_tokens, entry_tokens)
                if best_key:
                    entry = entries_by_key[best_key]
                    matched_mce_entry = entry.get("canonical_sit_name") or entry.get("SIT")
                    langs = set()
                    for kw in entry.get("keywords", []) or []:
                        if isinstance(kw, dict) and kw.get("language"):
                            langs.add(str(kw["language"]).strip().lower())
                    mce_language_covered = lang_norm in langs

    found = bool(matched_pack or matched_spec or matched_mce_entry)
    language_confirmed = bool(pack_language_covered or mce_language_covered)

    if not found:
        return SitLanguageLookup(
            sit_name, language, matched_pack, pack_language_covered, matched_spec,
            matched_mce_entry, mce_language_covered, "not_found",
            f"'{sit_name}' was not found in the provided sit_packs / SIT Specs"
            + (" / 213 MCE keywords" if mce_keywords_path else "") + " reference material.",
            SIT_NOT_FOUND_LINK)

    if not language_confirmed:
        return SitLanguageLookup(
            sit_name, language, matched_pack, pack_language_covered, matched_spec,
            matched_mce_entry, mce_language_covered, "found_unconfirmed_language",
            f"'{sit_name}' was found, but '{language}' coverage could not be confirmed for it "
            "in the available reference material.",
            SIT_NOT_FOUND_LINK)

    return SitLanguageLookup(
        sit_name, language, matched_pack, pack_language_covered, matched_spec,
        matched_mce_entry, mce_language_covered, "confirmed",
        f"'{sit_name}' ({language}) is present and its language coverage is confirmed in the "
        "reference material.", None)
