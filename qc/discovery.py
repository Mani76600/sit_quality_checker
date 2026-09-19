"""Walk a user-supplied root path and build a VersionContext per Version_* dir found.

The root can point anywhere in the tree: at a single Version_* dir, at a
Language dir, at a SIT dir, or at a folder containing many SITs - every
``Version_YYYYMMDD_HHMM`` directory found anywhere underneath is discovered.

SIT-name/language resolution is content-first: the run's own
export_summary.json always carries the authoritative "sit_name" field, so
that is read and used to confirm (or correct) whatever the folder hierarchy
suggests, rather than trusting folder names alone. This matters because the
folder hierarchy is ambiguous on its own - e.g. a multilingual layout
("<SIT Name>/<Language>/Version_*/") looks identical, path-shape-wise, to an
English layout with an extra wrapper folder, and there is no way to tell
"Romanized Chinese" is a language (vs. a stray extra folder) from its name
alone. Folder-name matching against a small KNOWN_LANGUAGES list remains as
a fallback for when export_summary.json can't be read at all.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from qc.context import VersionContext
from qc.jsonio import read_json
from qc.progress import Reporter, null_reporter

VERSION_DIR_RE = re.compile(r"^Version_\d{8}_\d{4}$")

# Directory names never worth descending into when searching a broad root.
SKIP_DIR_NAMES = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".ruff_cache", "site-packages", ".idea", ".vscode", "dist-packages",
}

# Fallback only - used when export_summary.json/metadata can't be read at
# all. The pipeline's multilingual layout puts one of these under a SIT
# folder as SIT_Name/Language/Version_*/.
KNOWN_LANGUAGES = {
    "english", "spanish", "french", "german", "swedish", "japanese", "korean",
    "chinese", "portuguese", "italian", "dutch", "russian", "arabic", "hindi",
    "turkish", "polish", "norwegian", "danish", "finnish", "greek", "hebrew",
    "thai", "vietnamese", "indonesian", "czech", "hungarian", "romanian",
    "ukrainian", "bulgarian", "croatian", "slovak", "slovenian", "estonian",
    "latvian", "lithuanian", "serbian", "swahili", "afrikaans", "zulu",
}


def _normalize_name(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _read_content_sit_name(version_dir: Path) -> str | None:
    """Authoritative sit_name straight from the run's own output, not a
    folder-name guess. Tries export_summary.json first (always carries a
    top-level "sit_name" field per the pipeline's schema); falls back to the
    first readable per-doc metadata.json if the summary is missing/broken."""
    data, err = read_json(version_dir / "export_summary.json")
    if not err and isinstance(data, dict):
        sn = data.get("sit_name")
        if isinstance(sn, str) and sn.strip():
            return sn.strip()

    for polarity in ("Positive", "Negative"):
        meta_dir = version_dir / polarity / "outputs" / "metadata"
        if not meta_dir.exists():
            continue
        for f in meta_dir.glob("*.json"):
            data, err = read_json(f)
            if not err and isinstance(data, dict):
                sn = data.get("sit_name")
                if isinstance(sn, str) and sn.strip():
                    return sn.strip()
            break  # cheap fallback only - try exactly one file per polarity
    return None


@dataclass
class _Classification:
    sit_name: str
    language: str | None
    layout: str
    sit_name_source: str
    note: str = ""


def _classify(version_dir: Path, is_direct: bool) -> _Classification:
    parent = version_dir.parent
    grandparent = parent.parent
    content_sit_name = _read_content_sit_name(version_dir)

    if is_direct:
        # The user's given path IS the Version_* dir - there is no SIT-name
        # folder above it to read at all.
        if content_sit_name:
            return _Classification(
                content_sit_name, None, "unknown", "export_summary.json / metadata",
                "You gave the Version_* folder directly (no SIT-name folder above it). "
                f"SIT name '{content_sit_name}' was read from this run's own report, "
                "not guessed from a folder name.")
        return _Classification(
            parent.name, None, "unknown", "folder name (unconfirmed)",
            "You gave the Version_* folder directly, and export_summary.json / metadata "
            f"could not be read to confirm a SIT name - using the parent folder name "
            f"('{parent.name}') as a best-effort guess.")

    parent_norm = _normalize_name(parent.name)
    grandparent_norm = _normalize_name(grandparent.name) if grandparent.name else ""
    content_norm = _normalize_name(content_sit_name)

    if content_sit_name and content_norm == grandparent_norm and grandparent.name:
        # Multilingual: grandparent is the real SIT folder, parent is the language
        # (e.g. "Taiwan Passport Number/Romanized Chinese/Version_*/" - confirmed
        # by content, not by parent.name being in a hardcoded language list).
        return _Classification(grandparent.name, parent.name, "multilingual",
                                "export_summary.json (confirmed against SIT-name folder)")

    if content_sit_name and content_norm == parent_norm:
        return _Classification(parent.name, None, "english",
                                "export_summary.json (confirmed against folder name)")

    if parent.name.strip().lower() in KNOWN_LANGUAGES and grandparent.name:
        # Content didn't match either ancestor name, but the folder-name
        # heuristic still recognizes a multilingual layout - trust content
        # for the sit_name text if we have it, folder for the language.
        sit_name = content_sit_name or grandparent.name
        source = ("export_summary.json (folder language name recognized)" if content_sit_name
                  else "folder name (known-language heuristic)")
        return _Classification(sit_name, parent.name, "multilingual", source)

    if content_sit_name:
        return _Classification(
            content_sit_name, None, "english",
            "export_summary.json (folder name did not match)",
            f"Note: this run's own sit_name ('{content_sit_name}') does not match the "
            f"containing folder name ('{parent.name}') - double-check this output is in "
            "its expected location.")

    return _Classification(
        parent.name, None, "english", "folder name (unconfirmed)",
        "Could not read a sit_name from export_summary.json or metadata to confirm this "
        "folder name.")


def find_version_dirs(root: str | Path, report: Reporter = null_reporter) -> list[tuple[Path, bool]]:
    """Return (version_dir, is_direct) pairs. is_direct is True only when the
    given root itself IS the Version_* dir (no ancestor folders to read)."""
    root = Path(root)
    if not root.exists():
        report(f"Path does not exist: {root}")
        return []
    if VERSION_DIR_RE.match(root.name):
        report(f"Root itself is a Version_* directory: {root}")
        return [(root, True)]

    report(f"Scanning for Version_* directories under: {root}")
    found: list[Path] = []
    dirs_visited = 0
    for dirpath, dirnames, _filenames in os.walk(root, topdown=True):
        dirs_visited += 1
        if dirs_visited % 500 == 0:
            report(f"...still scanning ({dirs_visited} directories visited so far, "
                   f"{len(found)} run(s) found)")
        keep = []
        for name in dirnames:
            if name in SKIP_DIR_NAMES or name.startswith("."):
                continue
            if VERSION_DIR_RE.match(name):
                full = Path(dirpath) / name
                found.append(full)
                report(f"Found run: {full}")
                continue  # no nested Version_* dirs inside one - don't descend
            keep.append(name)
        dirnames[:] = keep

    report(f"Scan complete: visited {dirs_visited} directories, found {len(found)} run(s).")
    return [(p, False) for p in sorted(set(found))]


def discover(root: str | Path, report: Reporter = null_reporter) -> list[VersionContext]:
    contexts = []
    for version_dir, is_direct in find_version_dirs(root, report):
        c = _classify(version_dir, is_direct)
        contexts.append(
            VersionContext(
                version_dir=version_dir,
                sit_name=c.sit_name,
                language=c.language,
                layout=c.layout,
                sit_name_source=c.sit_name_source,
                discovery_note=c.note,
            )
        )
    return contexts
