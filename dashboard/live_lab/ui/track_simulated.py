"""Simulated lab track: Live lab (Prepare + Query) and an independent Benchmark.

Live lab is the hands-on product: sign zones, inspect files, start the hierarchy, then dig and
read packets. Benchmark does not use that lab session — it signs and measures each zone size on
its own and only needs the lab images.
"""

from __future__ import annotations

import streamlit as st

from live_lab import driver, minimal
from live_lab.ui import setup
from live_lab.ui.activity import _activity_expander
from live_lab.ui.benchmark_simulated import (
    _sidebar_bench_simulated, benchmark_results, benchmark_run,
)
from live_lab.ui.common import _hint, _page_header, _sidebar_brand, current_step, readiness, step_bar
from live_lab.ui.lab import lab_body, sidebar_lab
from live_lab.ui.live import live_body, sidebar_live

_LIVE_FOCUS_KEY = "live_lab_focus"
_LIVE_FOCUS_LABELS = {
    "prepare": "Prepare · sign & start",
    "query": "Query · dig & packets",
}


def live_lab_focus() -> str:
    """Which panel of Live lab is open: prepare or query."""
    if minimal.enabled() and _LIVE_FOCUS_KEY not in st.session_state:
        st.session_state[_LIVE_FOCUS_KEY] = "query"
    value = st.session_state.get(_LIVE_FOCUS_KEY, "prepare")
    return value if value in _LIVE_FOCUS_LABELS else "prepare"


def set_live_lab_focus(focus: str) -> None:
    if focus in _LIVE_FOCUS_LABELS:
        st.session_state[_LIVE_FOCUS_KEY] = focus


def sidebar_simulated(h: driver.LabHandle, stt: dict) -> None:
    setup._finish_image_job(h)  # report a build or pull that ended while another page was showing
    step = current_step("simulated")
    if step == "live":
        if live_lab_focus() == "query":
            sidebar_live(h, stt)
        else:
            sidebar_lab(h, stt)
    else:
        _sidebar_brand("simulated")
        if minimal.enabled():
            st.sidebar.markdown("### Benchmark")
            st.sidebar.caption(
                "Saved benchmarks of this node, read-only. Running a new one needs your own deployment."
            )
            if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_bench_minimal"):
                st.rerun()
        else:
            _sidebar_bench_simulated(h, stt)


def _live_lab_page(h: driver.LabHandle, stt: dict) -> None:
    st.markdown("### Live lab")
    st.caption(
        "The rich CipherOps experience: design and sign zones, start the root → TLD → leaf lab, "
        "then query it and inspect packets. Prepare and Query are two panels of the same live lab."
    )
    focus = st.radio(
        "Live lab panel",
        list(_LIVE_FOCUS_LABELS),
        key=_LIVE_FOCUS_KEY,
        horizontal=True,
        format_func=_LIVE_FOCUS_LABELS.get,
        label_visibility="collapsed",
    )
    if focus == "prepare":
        _hint("Sign the zones, browse unsigned/signed files, then Start the containers.")
        lab_body(h, stt)
    else:
        _hint("Send lookups through the validating resolver; capture and decode packets.")
        live_body(h, stt)


def page_simulated(h: driver.LabHandle, stt: dict) -> None:
    _page_header("simulated", h, stt)
    setup.prerequisite(h, "simulated")
    step = step_bar("simulated", {"live": readiness(stt)["ready"]})
    if step == "live":
        _live_lab_page(h, stt)
    elif minimal.enabled():
        st.caption(
            "This suite benchmark signs and measures each zone size on its own. "
            "It does not use the Live lab session above — only the lab images on this node."
        )
        benchmark_results(h)
    else:
        st.caption(
            "Independent of Live lab: for each chosen zone size the suite signs, measures, starts "
            "and stops its own lab. It only needs the images — not a prepared Live lab."
        )
        run_tab, results = st.tabs(["Run", "Results"])
        with run_tab:
            benchmark_run(h, stt)
        with results:
            benchmark_results(h)
    _activity_expander(h)
