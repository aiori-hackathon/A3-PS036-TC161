"""Offline benchmark track: ① Run → ② Results, behind one prerequisite: the harness image is on this node."""

from __future__ import annotations

import streamlit as st

from live_lab import compare_hub, driver
from live_lab.ui import setup
from live_lab.ui.activity import _activity_expander
from live_lab.ui.benchmark_offline import (
    _finish_offline_job, _sidebar_bench_offline, benchmark_offline_results, benchmark_offline_run,
)
from live_lab.ui.common import ROOT, _page_header, _sidebar_brand, current_step, step_bar


def sidebar_offline(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("offline")
    setup._finish_image_job(h)  # report a build or pull that ended while another page was showing
    if current_step("offline") == "run":
        _sidebar_bench_offline(h, stt)
    elif st.sidebar.button("Refresh", width="stretch", key="sb_refresh_offline_results"):
        st.rerun()


def page_offline(h: driver.LabHandle, stt: dict) -> None:
    _page_header("offline", h, stt)
    setup.prerequisite(h, "offline")
    _finish_offline_job()  # hand over a run that ended while another step was showing
    step = step_bar("offline", {"results": bool(compare_hub.offline_catalogue(ROOT / "results"))})
    if step == "run":
        benchmark_offline_run()
    else:
        benchmark_offline_results()
    _activity_expander(h)
