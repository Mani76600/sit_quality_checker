"""AppTest fixture: renders a REAL RunReport (from an actual sample output
folder) through render_report(), so the navigation-anchor tests also cover
real category names (which contain dots/slashes/parentheses, e.g. '4.
Index Files (sit_inverted_index.json / sit_merged_index.json)') rather
than only the clean synthetic ones in nav_report_app.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qc.discovery import discover
from qc.registry import run_all
from qc.streamlit_report import render_report

ROOT = Path(__file__).resolve().parents[3] / "South Africa Identification Number"
contexts = discover(str(ROOT))
rep = run_all(contexts[0], {"context_ratio_threshold": 1.0, "exhaustive_chunk_scan": False,
                            "chunk_sample_size": 50})
render_report(rep)
