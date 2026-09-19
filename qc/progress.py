"""Tiny progress-reporting helper.

Every long-running step in this tool (discovery, each check) reports through
one of these callables. It always prints to stdout (so running
``python -m streamlit run app.py`` from a terminal shows live progress there
regardless of the UI), and optionally forwards the same message to a UI
sink (e.g. a ``st.status()`` box) so the Streamlit page itself updates live
instead of sitting on a bare spinner with no detail.
"""

from __future__ import annotations

import time
from typing import Callable

Reporter = Callable[[str], None]


def make_reporter(ui_sink: Reporter | None = None) -> Reporter:
    def report(message: str) -> None:
        line = f"[QC {time.strftime('%H:%M:%S')}] {message}"
        print(line, flush=True)
        if ui_sink is not None:
            try:
                ui_sink(message)
            except Exception:
                pass  # never let a UI hiccup break the actual check run
    return report


def null_reporter(_message: str) -> None:
    return None
