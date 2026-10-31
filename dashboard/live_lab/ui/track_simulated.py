"""Simulated lab track: ① Prepare → ② Query → ③ Benchmark, behind one prerequisite: the lab images are on this node.

Prepare and Query are the hands-on path (sign and start the lab, then send queries and read packets); Benchmark runs on its
own and needs only the images."""

from __future__ import annotations

import streamlit as st

from live_lab import driver, minimal
from live_lab.ui import setup
from live_lab.ui.activity import _activity_expander
from live_lab.ui.benchmark_simulated import (
    _sidebar_bench_simulated, benchmark_results, benchmark_run,
)
from live_lab.ui.common import _page_header, _sidebar_brand, current_step, readiness, step_bar
from live_lab.ui.lab import lab_body, sidebar_lab
from live_lab.ui.live import live_body, sidebar_live


def sidebar_simulated(h: driver.LabHandle, stt: dict) -> None:
    setup._finish_image_job(h)  # report a build or pull that ended while another page was showing
    step = current_step("simulated")
    if step == "prepare":
        sidebar_lab(h, stt)
    elif step == "query":
        sidebar_live(h, stt)
    else:
        _sidebar_brand("simulated")
        if minimal.enabled():
            st.sidebar.markdown("### Benchmark")
            st.sidebar.caption("Saved benchmarks of this node, read-only. Running a new one needs your own deployment.")
            if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_bench_minimal"):
                st.rerun()
        else:
            _sidebar_bench_simulated(h, stt)


def page_simulated(h: driver.LabHandle, stt: dict) -> None:
    _page_header("simulated", h, stt)
    setup.prerequisite(h, "simulated")
    step = step_bar("simulated", {"prepare": readiness(stt)["ready"]})
    if step == "prepare":
        lab_body(h, stt)
    elif step == "query":
        live_body(h, stt)
    elif minimal.enabled():
        benchmark_results(h)
    else:
        run_tab, results = st.tabs(["Run", "Results"])
        with run_tab:
            benchmark_run(h, stt)
        with results:
            benchmark_results(h)
    _activity_expander(h)
