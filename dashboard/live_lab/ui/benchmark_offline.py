"""Benchmark tile, Offline mode: the harness run (sidebar, plan, progress, results and downloads)."""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import streamlit as st

from live_lab import compare_hub, driver, image_job, images, offline_job
from live_lab import glossary as gl
from live_lab import suite as suite_mod
from live_lab.ui.common import ROOT, _go, _hint, _store, activity, cfg, sb
from live_lab.ui.compare_page import _cmp_offline_run


def offline_image_problem() -> str:
    """Why the harness image cannot be used, or '' when it is on this node and matches the stack profile."""
    return images.explain(images.status(offline_job.OFFLINE_STACK, "offline"), "offline")


def _sidebar_bench_offline(h: driver.LabHandle, stt: dict) -> None:
    runs = compare_hub.offline_catalogue(ROOT / "results")
    if offline_job.is_running():
        st.sidebar.markdown("🔵  An offline benchmark is running")
    else:
        st.sidebar.markdown(f"🟢  {len(runs)} saved run(s)" if runs else "⚪  No offline run yet")
    preset = sb(st.sidebar.radio, "offline_preset", "quick", "Run size", options=list(offline_job.PRESETS),
                format_func=lambda k: f"{offline_job.PRESETS[k]['title']} · {offline_job.PRESETS[k]['note']}",
                help="Quick check: small zones and few runs, to see the pipeline work. Full run: the three zone "
                     "sizes with enough runs for tight confidence intervals; it takes much longer.")
    problem = offline_image_problem()
    if problem:
        st.sidebar.warning(problem)
        st.sidebar.button("Go to Setup", on_click=_go, args=("setup",), key="offline_to_setup", width="stretch")
    else:
        _hint(f"Runs inside the verified `cipherops-bench:{offline_job.OFFLINE_STACK}` image from Setup.")
    busy = bool(suite_mod.running_suite() or image_job.is_running() or driver.active_prepare_tags())
    if busy:
        st.sidebar.warning("A simulated benchmark, image build or pull, or signing job is running. Wait for it to finish.")
    if st.sidebar.button("Run offline benchmark", key="btn_offline", type="primary", width="stretch",
                         disabled=offline_job.is_running() or busy or bool(problem)):
        try:
            offline_job.start(preset)
            activity("step", f"Offline benchmark started · {offline_job.PRESETS[preset]['title']}", phase="run",
                     source="offline", h=h)
        except Exception as e:  # noqa: BLE001
            st.sidebar.error(str(e)[:600])
        st.rerun()
    st.sidebar.markdown("---")
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_offline"):
        st.rerun()


def _offline_plan(preset: str) -> None:
    import yaml
    path = ROOT / offline_job.PRESETS[preset]["config"]
    try:
        conf = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st.warning(f"Cannot read {path.name}.")
        return
    with st.container(border=True):
        st.markdown(f"**Planned run · {offline_job.PRESETS[preset]['title']}**")
        a = st.columns(4)
        a[0].metric(gl.label("zone_size") + "s", len(conf.get("datasets", [])), help=gl.tip("zone_size"))
        a[1].metric(gl.label("algorithm") + "s", len(conf.get("modes", [])), help=gl.tip("algorithm"))
        a[2].metric(gl.label("runs"), conf.get("trials", "—"), help=gl.tip("runs"))
        a[3].metric(gl.label("queries_per_run"), (conf.get("wire", {}).get("workload", {}) or {}).get("n_queries", "—"),
                    help=gl.tip("queries_per_run"))
        st.caption("Zone sizes (names): " + ", ".join(f"{d['n_names']:,}" for d in conf.get("datasets", [])) +
                   " · Algorithms: " + ", ".join(compare_hub.short(m) for m in conf.get("modes", [])) +
                   f" · Noise-check control: {compare_hub.short(conf.get('control', '—'))} · Config `{path.name}`")


@st.fragment(run_every=3)
def _offline_progress() -> None:
    """Bar, clock, and the log. The log body is built only while it is open, so a closed log is not redrawn."""
    s = offline_job.status()
    if s.get("state") not in offline_job.ACTIVE_STATES:
        st.rerun()  # whole page: the sidebar and Results pick up the finished run
        return
    elapsed = time.time() - float(s.get("started") or time.time())
    fraction, text = offline_job.display_progress(s)
    st.progress(fraction, text=text)
    st.caption(f"Elapsed {driver.format_duration(elapsed).lstrip('~')}. You can leave this page; the benchmark "
               "keeps running and Results fills in when it finishes.")
    log = st.expander("Progress log", expanded=False, key="offline_progress_log", on_change="rerun")
    if log.open:
        log.code(offline_job.log_text(4000) or "(starting…)", language="text")


def _offline_running(s: dict) -> None:
    with st.container(border=True):
        st.markdown(f"**Offline benchmark running** · {offline_job.PRESETS[s.get('preset', 'quick')]['title']}")
        _offline_progress()
        if st.button("Cancel benchmark", key="btn_cancel_offline"):
            offline_job.cancel()
            activity("warn", "Offline benchmark cancelled", phase="run", source="offline")
            st.rerun()


def _finish_offline_job() -> None:
    s = offline_job.status()
    if s.get("state") in offline_job.ACTIVE_STATES or s.get("handled"):
        return
    if s.get("state") == "done":
        activity("ok", "Offline benchmark finished", s.get("run_id") or "", phase="run", source="offline")
    elif s.get("state") in ("failed", "stopped"):
        activity("err", "Offline benchmark failed", (s.get("error") or "")[-2000:], phase="run", source="offline")
    if s:
        offline_job.mark_handled()


def benchmark_offline_run() -> None:
    _finish_offline_job()
    if offline_job.is_running():
        _offline_running(offline_job.status())
        return
    s = offline_job.status()
    if s.get("state") in ("failed", "stopped", "cancelled"):
        st.warning(f"The last offline benchmark {s['state']}: {s.get('error') or s.get('step', '')}")
        if offline_job.log_text():
            with st.expander("Progress log"):
                st.code(offline_job.log_text(4000), language="text")
    problem = offline_image_problem()
    if problem:
        st.warning(problem)
        st.button("Go to Setup", on_click=_go, args=("setup",), key="offline_page_to_setup")
    st.caption("Choose the run size in the sidebar, then press **Run offline benchmark**. It runs on a dedicated "
               "core with no network, inside the image Setup put on this node.")
    _offline_plan(cfg("offline_preset", "quick"))


def _open_offline_in_compare(run_id: str) -> None:
    _store().update(cmp_src="offline", cmp_view="algorithms", cmp_a_offline=run_id)
    for k in ("w_cmp_src", "w_cmp_view", "w_cmp_a_offline"):
        st.session_state.pop(k, None)
    _go("compare")


@st.cache_data(show_spinner=False)
def _offline_file_bytes(path: str, mtime: float) -> bytes:
    return Path(path).read_bytes()


@st.cache_data(show_spinner=False)
def _offline_bundle_bytes(path: str, mtime: float) -> bytes:
    return compare_hub.offline_bundle(Path(path))


def _offline_downloads(path: Path, rid: str, ds: str, table: pd.DataFrame | None) -> None:
    """The same kind of downloads as a simulated run: the report, the table on screen, and the trial file."""
    report_html = path / "report.html"
    report_md = path / "report.md"
    trials = path / "trials.csv"
    stamp = (path / "summary.json").stat().st_mtime if (path / "summary.json").is_file() else 0
    d0, d1, d2, d3 = st.columns(4)
    if report_html.is_file():
        d0.download_button("Download report (HTML)", data=_offline_file_bytes(str(report_html), report_html.stat().st_mtime),
                           file_name=f"{rid}-report.html", mime="text/html", key=f"bench_off_html_{rid}")
    elif report_md.is_file():
        d0.download_button("Download report (Markdown)", data=_offline_file_bytes(str(report_md), report_md.stat().st_mtime),
                           file_name=f"{rid}-report.md", mime="text/markdown", key=f"bench_off_md_{rid}")
    if table is not None and not table.empty:
        d1.download_button("Download this comparison (CSV)", data=table.to_csv(index=False),
                           file_name=f"{rid}-{ds}-comparison.csv", mime="text/csv", key=f"bench_off_cmp_{rid}_{ds}")
    if trials.is_file():
        d2.download_button("Download signing trials (CSV)", data=_offline_file_bytes(str(trials), trials.stat().st_mtime),
                           file_name=f"{rid}-trials.csv", mime="text/csv", key=f"bench_off_trials_{rid}")
    bundle = _offline_bundle_bytes(str(path), stamp)
    if bundle:
        d3.download_button("Download all results (ZIP)", data=bundle, file_name=f"{rid}-results.zip",
                           mime="application/zip", key=f"bench_off_zip_{rid}")
    if report_html.is_file():
        st.caption(f"Reproducible report on disk: `{report_html}` (also `report.md`, `figures/`, and the CSV tables). "
                   "The zip is that report plus the result tables. The per-operation log stays in the run folder.")


def benchmark_offline_results() -> None:
    cat = compare_hub.offline_catalogue(ROOT / "results")
    if not cat:
        st.info("No offline run yet. Press **Run offline benchmark** in the sidebar.")
        return
    by = {e["id"]: e for e in cat}
    rid = st.selectbox("Run", list(by), key="bench_offline_pick", format_func=lambda i: by[i]["title"])
    entry = by[rid]
    path = entry["path"]
    run = _cmp_offline_run(str(path), (path / "summary.json").stat().st_mtime)
    facts = entry["facts"]
    m = st.columns(4)
    m[0].metric(gl.label("runs"), facts["Runs"], help=gl.tip("runs"))
    m[1].metric("Stack", facts["Stack profile"], help="The pinned software versions (OpenSSL, liboqs, MTL) the run used.")
    m[2].metric("CPU core(s) used", facts["Cores used"].strip("[]"),
                help="Every timing ran on this dedicated core, so results are repeatable.")
    m[3].metric(gl.label("zone_size") + "s", len(entry["datasets"]), help=gl.tip("zone_size"))
    datasets = [d for d in run.dataset_order if d in set(entry["datasets"])] or entry["datasets"]
    ds = st.segmented_control(gl.label("zone_size"), datasets, default=datasets[-1], key="bench_offline_ds",
                              help=gl.tip("zone_size")) or datasets[-1]
    frame = compare_hub.offline_frame(run, ds)
    table = None
    if frame.empty:
        st.info("This run has no data for that zone size.")
    else:
        keys = list(frame.key)
        base = "slh-dsa" if "slh-dsa" in keys else keys[0]
        paired = compare_hub.offline_paired(run, ds, base) if len(keys) > 1 else None
        table = compare_hub.algorithm_table(frame, base, paired)
        st.dataframe(table, width="stretch", hide_index=True, column_config=gl.column_config(table.columns))
        st.caption(f"Each ratio is against {compare_hub.short(base)}; lower is better. "
                   f"{compare_hub.stats_label(run)} Details: {facts['Zone sizes']}.")
        st.button("Open in Compare →", on_click=_open_offline_in_compare, args=(rid,), key="bench_offline_to_cmp")
    _offline_downloads(path, rid, ds, table)
