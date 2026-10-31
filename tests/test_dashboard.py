"""Headless smoke test of the Streamlit lab dashboard: every page and every step of both tracks renders without an
exception, the node prerequisite is shown, and the navigation helpers map old tile names to the right step."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dashboard" / "live_lab" / "app.py"
sys.path.insert(0, str(ROOT / "dashboard"))


def _run(tile=None, step=None, cfg=None):
    testing = pytest.importorskip("streamlit.testing.v1")
    at = testing.AppTest.from_file(str(APP), default_timeout=180)
    if tile:
        at.query_params["tile"] = tile
    if step:
        at.query_params["step"] = step
    if cfg:
        at.session_state["cfg"] = cfg
    at.run()
    return at


@pytest.mark.parametrize("tile", [None, "offline", "simulated", "compare", "learn", "setup", "reset"])
def test_page_renders_without_exceptions(tile):
    at = _run(tile)
    assert not at.exception, [e.message for e in at.exception]


@pytest.mark.parametrize("tile,step", [
    ("offline", "run"), ("offline", "results"),
    ("simulated", "prepare"), ("simulated", "query"), ("simulated", "benchmark"),
])
def test_every_step_of_a_track_renders_without_exceptions(tile, step):
    at = _run(tile, step)
    assert not at.exception, [e.message for e in at.exception]
    assert at.session_state[f"step_{tile}"] == step


@pytest.mark.parametrize("old,expected", [("lab", "prepare"), ("live", "query"), ("benchmark", "benchmark")])
def test_old_tile_names_open_the_matching_step(old, expected):
    at = _run(old)
    assert not at.exception, [e.message for e in at.exception]
    assert at.session_state["nav"] == "simulated" and at.session_state["step_simulated"] == expected


@pytest.mark.parametrize("tile,cfg", [
    ("compare", {"cmp_view": "evolution"}),
    ("compare", {"cmp_view": "runs"}),
])
def test_compare_views_render_without_exceptions(tile, cfg):
    at = _run(tile, cfg=cfg)
    assert not at.exception, [e.message for e in at.exception]


def test_image_readiness_is_a_prerequisite_not_a_step():
    from live_lab.ui import common
    assert [k for k, _ in common.TRACKS["offline"]["steps"]] == ["run", "results"]
    assert [k for k, _ in common.TRACKS["simulated"]["steps"]] == ["prepare", "query", "benchmark"]
    for track in ("offline", "simulated"):
        at = _run(track)
        labels = at.radio(key=f"step_{track}").options
        assert not any("mage" in label for label in labels)
        text = " ".join(m.value for m in at.markdown)
        assert "Node ready" in text or "Needed on this node" in text  # the prerequisite panel is on the page
    assert at.radio(key="step_simulated").options[0].startswith("Prepare")
    assert "①" not in " ".join(at.radio(key="step_simulated").options)


def test_help_text_is_on_by_default_and_toggles():
    at = _run("simulated")
    assert at.session_state["show_help"]
    shown = len(at.sidebar.caption)
    at.sidebar.toggle(key="show_help").set_value(False).run()
    assert not at.exception
    assert len(at.sidebar.caption) < shown


def test_resolve_tile_maps_old_names_and_ignores_unknown_steps():
    from live_lab.ui import common
    assert common.resolve_tile("lab") == ("simulated", "prepare")
    assert common.resolve_tile("live") == ("simulated", "query")
    assert common.resolve_tile("benchmark") == ("simulated", "benchmark")
    assert common.resolve_tile("offline") == ("offline", "run")
    assert common.resolve_tile("offline", "results") == ("offline", "results")
    assert common.resolve_tile("offline", "image") == ("offline", "run")      # the old image step is gone
    assert common.resolve_tile("simulated", "images") == ("simulated", "prepare")
    assert common.resolve_tile("setup") == ("setup", None)
    assert common.resolve_tile("does-not-exist") == ("home", None)
