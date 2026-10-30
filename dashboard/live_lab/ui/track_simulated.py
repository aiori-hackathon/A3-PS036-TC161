"""Simulated lab track: ① Images → ② Prepare lab → ③ Query lab → ④ Benchmark.

The lab is a root, TLD and leaf hierarchy of real servers behind a validating resolver. Steps 2 and 3 are the hands-on
path (sign and start the lab, then send queries and read packets); step 4 benchmarks it automatically and needs only the
images from step 1. One page, numbered steps; the sidebar shows the settings of the step being viewed."""

from __future__ import annotations

import streamlit as st

from live_lab import driver
from live_lab.ui import setup
from live_lab.ui.activity import _activity_expander
from live_lab.ui.benchmark_simulated import (
    _sidebar_bench_simulated, benchmark_results, benchmark_run,
)
from live_lab.ui.common import (
    _page_header, _sidebar_brand, current_step, readiness, step_bar,
)
from live_lab.ui.lab import lab_body, sidebar_lab
from live_lab.ui.live import live_body, sidebar_live


def _done(stt: dict) -> dict[str, bool]:
    r = readiness(stt)
    return {"images": r["images"], "prepare": r["ready"]}


def sidebar_simulated(h: driver.LabHandle, stt: dict) -> None:
    step = current_step("simulated")
    if step == "images":
        setup.sidebar_setup(h, stt, "simulated")
    elif step == "prepare":
        sidebar_lab(h, stt)
    elif step == "query":
        sidebar_live(h, stt)
    else:
        _sidebar_brand("simulated")
        st.sidebar.markdown("### Benchmark")
        _sidebar_bench_simulated(h, stt)


def page_simulated(h: driver.LabHandle, stt: dict) -> None:
    _page_header("simulated", h, stt)
    step = step_bar("simulated", _done(stt))
    if step == "images":
        setup.image_step(h, "simulated")
    elif step == "prepare":
        lab_body(h, stt)
    elif step == "query":
        live_body(h, stt)
    else:
        st.caption("Benchmarks the lab automatically: for each zone size it signs the zones, starts the lab, measures "
                   "and stops it. Steps 2 and 3 are not needed first; only the images from step 1.")
        run_tab, results = st.tabs(["Run", "Results"])
        with run_tab:
            benchmark_run(h, stt)
        with results:
            benchmark_results(h)
    _activity_expander(h)
