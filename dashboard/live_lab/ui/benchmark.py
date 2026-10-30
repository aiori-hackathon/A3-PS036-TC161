"""Benchmark tile: mode switch and page; Offline and Simulated each live in their own module."""

from __future__ import annotations

import streamlit as st

from live_lab import driver
from live_lab.ui.activity import _activity_expander
from live_lab.ui.benchmark_offline import (
    _sidebar_bench_offline, benchmark_offline_results, benchmark_offline_run,
)
from live_lab.ui.benchmark_simulated import _sidebar_bench_simulated, benchmark_results, benchmark_run
from live_lab.ui.common import _hint, _page_header, _sidebar_brand, cfg, sb

BENCH_MODES = {"offline": "Offline (harness)", "simulated": "Simulated (Verisign stack)"}
BENCH_MODE_NOTE = {
    "offline": "Times each signing and verification call on one dedicated core with no network, and counts the DNS "
               "response bytes. Repeatable; runs inside the verified image from Setup (no lab needed).",
    "simulated": "A root, TLD and leaf hierarchy of NSD servers behind a validating Unbound, on the Verisign MTL "
                 "stack in Docker. Real packets and cache behaviour; timings are indicative.",
}


def bench_mode() -> str:
    mode = cfg("bench_mode", "offline")
    return mode if mode in BENCH_MODES else "offline"


def sidebar_benchmark(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("benchmark")
    st.sidebar.markdown("### Benchmark")
    mode = sb(st.sidebar.radio, "bench_mode", "offline", "Mode", options=list(BENCH_MODES),
              format_func=BENCH_MODES.get,
              help="Offline (harness): crypto timings and DNS message sizes on one pinned core, no network. "
                   "Simulated (Verisign stack): a real root, TLD and leaf hierarchy with a validating resolver, "
                   "so you see packets, truncation and cache behaviour.")
    _hint(BENCH_MODE_NOTE[mode])
    st.sidebar.markdown("---")
    if mode == "offline":
        _sidebar_bench_offline(h, stt)
    else:
        _sidebar_bench_simulated(h, stt)


def benchmark_page(h: driver.LabHandle, stt: dict) -> None:
    _page_header("benchmark", h, stt)
    mode = bench_mode()
    st.caption(f"**{BENCH_MODES[mode]}** · {BENCH_MODE_NOTE[mode]}")
    run_tab, results = st.tabs(["Run", "Results"])
    with run_tab:
        benchmark_offline_run() if mode == "offline" else benchmark_run(h, stt)
    with results:
        benchmark_offline_results() if mode == "offline" else benchmark_results(h)
    _activity_expander(h)
