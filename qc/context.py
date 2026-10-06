"""Resolved path context for one Version_* output directory.

A ``VersionContext`` never asserts that a path exists - resolution is pure
path arithmetic. Existence is something individual checks decide about and
report on, so a missing folder becomes a normal FAIL result instead of a
crash during discovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PolarityOutputs:
    """Positive/ or Negative/ subtree: outputs/{raw_doc,metadata,parsed_raw_doc/{chunks,plain_text}}."""

    root: Path  # .../Positive or .../Negative

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def raw_doc(self) -> Path:
        return self.outputs / "raw_doc"

    @property
    def metadata(self) -> Path:
        return self.outputs / "metadata"

    @property
    def metadata_index(self) -> Path:
        return self.metadata / "index.jsonl"

    @property
    def parsed_raw_doc(self) -> Path:
        return self.outputs / "parsed_raw_doc"

    @property
    def chunks(self) -> Path:
        return self.parsed_raw_doc / "chunks"

    @property
    def plain_text(self) -> Path:
        return self.parsed_raw_doc / "plain_text"


@dataclass
class FolderSet:
    """One 'level' of the output tree: either the Agreements root (the Version_*
    dir itself) or the Disagreements root (Version_*/Disagreements)."""

    name: str  # "Agreements" or "Disagreements"
    root: Path

    @property
    def combined_metadata(self) -> Path:
        return self.root / "combined_metadata.jsonl"

    @property
    def corpus(self) -> Path:
        return self.root / "corpus.jsonl"

    @property
    def export_summary(self) -> Path:
        return self.root / "export_summary.json"

    @property
    def sit_inverted_index(self) -> Path:
        return self.root / "sit_inverted_index.json"

    @property
    def sit_merged_index(self) -> Path:
        return self.root / "sit_merged_index.json"

    @property
    def context_output_normalized_dir(self) -> Path:
        """"context_output_normalized" is this pipeline's real, confirmed
        spelling - every sampled real export uses it. The British
        "context_output_normalised" alias has never been observed here, but
        is recognized as a fallback (only when the primary name is absent)
        since it costs nothing and some other tool/convention could plausibly
        use it."""
        primary = self.root / "context_output_normalized"
        if primary.exists():
            return primary
        alias = self.root / "context_output_normalised"
        return alias if alias.exists() else primary

    @property
    def positive(self) -> PolarityOutputs:
        return PolarityOutputs(self.root / "Positive")

    @property
    def negative(self) -> PolarityOutputs:
        return PolarityOutputs(self.root / "Negative")

    @property
    def sitgrader_dir(self) -> Path:
        return self.root / "SITGrader"

    @property
    def sitgrader_misc(self) -> Path:
        return self.sitgrader_dir / "misc"

    def chunk_run_dirs(self) -> list[Path]:
        if not self.sitgrader_dir.exists():
            return []
        return sorted(p for p in self.sitgrader_dir.glob("chunk_run_*") if p.is_dir())

    def legacy_benchmark_run_dirs(self) -> list[Path]:
        """"run_benchmark_*" - an older SITGrader naming convention, found in
        at least one real pre-rework backup tree alongside today's
        "chunk_run_*". This tool doesn't know that old format's internal
        schema (chunk_summary.json etc. is specific to the current
        convention), so these are only ever noted as present, never
        content-validated the way chunk_run_* is by sitgrader_checks.py."""
        if not self.sitgrader_dir.exists():
            return []
        return sorted(p for p in self.sitgrader_dir.glob("run_benchmark_*") if p.is_dir())

    def context_output_normalized_files(self) -> list[Path]:
        d = self.context_output_normalized_dir
        if not d.exists():
            return []
        return sorted(d.glob("*.json"))


@dataclass
class VersionContext:
    """Everything needed to run every check against one Version_* directory."""

    version_dir: Path
    sit_name: str
    language: str | None
    layout: str  # "english" | "multilingual" | "unknown"
    sit_name_source: str = "folder name"  # where sit_name came from, shown in the report
    discovery_note: str = ""  # human-readable caveat, e.g. "you gave the Version folder directly"

    @property
    def label(self) -> str:
        parts = [self.sit_name]
        if self.language:
            parts.append(self.language)
        parts.append(self.version_dir.name)
        return " / ".join(parts)

    @property
    def agreements(self) -> FolderSet:
        return FolderSet("Agreements", self.version_dir)

    @property
    def disagreements(self) -> FolderSet:
        return FolderSet("Disagreements", self.version_dir / "Disagreements")

    def folder_sets(self) -> list[FolderSet]:
        return [self.agreements, self.disagreements]

    def rel(self, path: Path) -> str:
        """Path relative to this version dir's grandparent, for readable messages."""
        base = self.version_dir.parent.parent if self.version_dir.parent.parent.exists() else self.version_dir.parent
        try:
            return str(path.relative_to(base))
        except ValueError:
            return str(path)
