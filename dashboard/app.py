"""Cipherops MTL DNSSEC Lab entrypoint.

    streamlit run dashboard/app.py
    streamlit run dashboard/live_lab/app.py
"""

from __future__ import annotations

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent / "live_lab" / "app.py"), run_name="__main__")
