"""Headless smoke test of the Streamlit lab dashboard: every tile renders without an exception."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dashboard" / "live_lab" / "app.py"


@pytest.mark.parametrize("tile", [None, "setup", "lab", "live", "benchmark", "compare", "learn", "reset"])
def test_tile_renders_without_exceptions(tile):
    testing = pytest.importorskip("streamlit.testing.v1")
    at = testing.AppTest.from_file(str(APP), default_timeout=180)
    if tile:
        at.query_params["tile"] = tile
    at.run()
    assert not at.exception, [e.message for e in at.exception]


@pytest.mark.parametrize("tile,cfg", [
    ("benchmark", {"bench_mode": "simulated"}),
    ("benchmark", {"bench_mode": "offline"}),
    ("compare", {"cmp_view": "evolution"}),
    ("compare", {"cmp_view": "runs"}),
])
def test_modes_and_views_render_without_exceptions(tile, cfg):
    testing = pytest.importorskip("streamlit.testing.v1")
    at = testing.AppTest.from_file(str(APP), default_timeout=180)
    at.query_params["tile"] = tile
    at.session_state["cfg"] = cfg
    at.run()
    assert not at.exception, [e.message for e in at.exception]
