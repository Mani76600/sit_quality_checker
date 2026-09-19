"""Small helpers shared by multiple check modules (metadata iteration + sampling)."""

from __future__ import annotations

import random
from pathlib import Path

from qc import file_cache
from qc.context import PolarityOutputs

RNG_SEED = 42


def metadata_files(polarity: PolarityOutputs) -> list[Path]:
    return file_cache.list_dir(polarity.metadata, "*.json")


def iter_instances(doc: dict):
    if not isinstance(doc, dict):
        return
    instances = doc.get("instances")
    if isinstance(instances, list):
        for inst in instances:
            if isinstance(inst, dict):
                yield inst


def sample(paths: list[Path], options: dict) -> list[Path]:
    exhaustive = bool(options.get("exhaustive_chunk_scan", False))
    sample_size = int(options.get("chunk_sample_size", 200))
    if exhaustive or len(paths) <= sample_size:
        return paths
    rng = random.Random(RNG_SEED)
    return rng.sample(paths, sample_size)
