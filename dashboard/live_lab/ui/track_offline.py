"""Offline benchmark track: ① Image → ② Run → ③ Results.

One page, three numbered steps. The sidebar shows the settings of the step being viewed; the existing run and results
views are reused unchanged."""

from __future__ import annotations

import streamlit as st

from live_lab import compare_hub, driver
from live_lab.ui import setup
from live_lab.ui.activity import _activity_expander
from live_lab.ui.benchmark_offline import (
    _finish_offline_job, _sidebar_bench_offline, benchmark_offline_results, benchmark_offline_run,
    offline_image_problem,
)
from live_lab.ui.common import ROOT, _hint, _page_header, _sidebar_brand, current_step, step_bar


def _done() -> dict[str, bool]:
    return {"image": not offline_image_problem(),
            "results": bool(compare_hub.offline_catalogue(ROOT / "results"))}


def sidebar_offline(h: driver.LabHandle, stt: dict) -> None:
    step = current_step("offline")
    if step == "image":
        setup.sidebar_setup(h, stt, "offline")
        return
    _sidebar_brand("offline")
    if step == "run":
        st.sidebar.markdown("### Run")
        _sidebar_bench_offline(h, stt)
        return
    st.sidebar.markdown("### Results")
    _hint("Pick a run on the page to see its tables, charts and downloads. To put two runs side by side, use Compare.")
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_offline_results"):
        st.rerun()


def page_offline(h: driver.LabHandle, stt: dict) -> None:
    _page_header("offline", h, stt)
    _finish_offline_job()  # hand over a run that ended while another step was showing
    step = step_bar("offline", _done())
    if step == "image":
        setup.image_step(h, "offline")
    elif step == "run":
        benchmark_offline_run()
    else:
        benchmark_offline_results()
    _activity_expander(h)
