"""Benchmark tile, Simulated mode: the zone-size suite on the Verisign stack (plan, progress, settings, results)."""

from __future__ import annotations

import json
import time
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from live_lab import compare, driver, image_job, offline_job
from live_lab import glossary as gl
from live_lab import suite as suite_mod
from live_lab.ui.common import (
    EDITABLE_PROFILES, PROFILE_FIELDS, SUITES_DIR, _go, _hint, _profiles, _restore_workload, _store,
    _warn_unvalidated, _workload, _workload_is_default, activity, cfg, readiness, sb,
)

SUITE_SIZE_CHOICES = [20, 50, 500, 5000]
SUITE_DEFAULT_SIZES = list(suite_mod.BENCH_SIZES)


def _size_label(n: int) -> str:
    return f"{n:,} names" + (" (quick check)" if n == suite_mod.SMOKE_SIZE else "")


def _sidebar_bench_simulated(h: driver.LabHandle, stt: dict) -> None:
    r = readiness(stt)
    suites = suite_mod.list_suites()
    done = [s for s in suites if s["complete"]]
    live = suite_mod.running_suite()
    if live:
        st.sidebar.markdown("🔵  A benchmark is running")
    else:
        st.sidebar.markdown(f"🟢  {len(done)} saved benchmark(s)" if done else "⚪  No benchmark yet")
    _hint(
        "Runs on its own: for each zone size it signs the zones, repeats signing and verification, "
        "starts the lab, measures, and stops it. It only needs the lab images."
    )
    bench_stack = cfg("stack", driver.DEFAULT_STACK)
    _hint(f"Uses the lab version `{bench_stack}`.")

    st.sidebar.markdown("**Run name**")
    label = sb(
        st.sidebar.text_input, "run_label", "", "Name this benchmark",
        placeholder="e.g. panel-final", max_chars=30,
        help="Stored with the date and time, so you can tell benchmarks apart later.",
    )
    clean = driver.sanitize_label(label)[:30]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    _hint(f"Saved as {stamp}{'_' + clean if clean else ''} (time = when you press Run)")

    st.sidebar.markdown("**What to measure**")
    sizes = sb(st.sidebar.multiselect, "bench_sizes", SUITE_DEFAULT_SIZES, gl.label("zone_size"),
               options=SUITE_SIZE_CHOICES, format_func=_size_label,
               help=gl.tip("zone_size") + " Recommended: 50, 500 and 5,000.")
    sc = sb(st.sidebar.multiselect, "bench_scenarios", ["ecdsa", "slh-dsa", "mtl"], gl.label("algorithm") + "s",
            options=list(driver.SCENARIO_ALGS), help=gl.tip("algorithm"))
    sb(st.sidebar.number_input, "bench_trials", 5, gl.label("runs"), min_value=1, max_value=10,
       help=gl.tip("runs") + " The interval of the median needs at least 6 runs to reach 95%; with 5 it covers 94%.")
    sb(st.sidebar.number_input, "bench_sign_trials", suite_mod.DEFAULT_SIGN_TRIALS, gl.label("signing_repeats"),
       min_value=1, max_value=10, help=gl.tip("signing_repeats"))
    sb(st.sidebar.number_input, "bench_sample", suite_mod.DEFAULT_SAMPLE_NAMES, gl.label("signing_sample"),
       min_value=10, max_value=500, step=10, help=gl.tip("signing_sample"))
    sb(st.sidebar.number_input, "bench_jobs", 1, gl.label("parallel_jobs"), min_value=1, max_value=4,
       help=gl.tip("parallel_jobs"))
    sb(st.sidebar.number_input, "bench_queries", 500, gl.label("queries_per_run"), min_value=10, max_value=5000,
       help=gl.tip("queries_per_run"))
    nets = sb(st.sidebar.multiselect, "bench_networks", ["lan"], gl.label("network_profile") + "s",
              options=["lan", "wan", "lossy"], help=gl.tip("network_profile"))
    sb(st.sidebar.selectbox, "bench_udp", 1232, gl.label("edns_buffer"), options=[1232, 4096],
       help=gl.tip("edns_buffer"))

    wl_default = driver.WORKLOAD_DEFAULTS
    with st.sidebar.expander("Workload mix", expanded=False):
        sb(st.number_input, "nx_pct", int(round(wl_default["nx_fraction"] * 100)), gl.label("nx_share"),
           min_value=0, max_value=50, step=1, help=gl.tip("nx_share"))
        sb(st.number_input, "zone_zipf", float(wl_default["zone_zipf_s"]), gl.label("zone_skew"),
           min_value=0.0, max_value=2.0, step=0.1, format="%.1f", help=gl.tip("zone_skew"))
        sb(st.number_input, "name_zipf", float(wl_default["name_zipf_s"]), gl.label("name_skew"),
           min_value=0.0, max_value=2.0, step=0.1, format="%.1f", help=gl.tip("name_skew"))
        sb(st.number_input, "timeout_s", float(wl_default["timeout_s"]), gl.label("query_timeout"),
           min_value=1.0, max_value=30.0, step=1.0, format="%.0f", help=gl.tip("query_timeout"))
    with st.sidebar.expander(gl.label("network_profile") + "s", expanded=False):
        st.caption("lan is the zero-delay baseline. Delay is added towards each tier on the resolver; "
                   "loss applies to the resolver's queries. Only the profiles ticked above are run.")
        for prof in EDITABLE_PROFILES:
            base = driver.PROFILE_DEFAULTS[prof]
            st.markdown(f"**{prof}**")
            cols = st.columns(2)
            for i, (field, flabel) in enumerate(PROFILE_FIELDS):
                with cols[i % 2]:
                    if field == "loss_pct":
                        sb(st.number_input, f"{prof}_{field}", float(base[field]), flabel,
                           min_value=0.0, max_value=20.0, step=0.5, format="%.1f")
                    else:
                        sb(st.number_input, f"{prof}_{field}", int(base[field]), flabel,
                           min_value=0, max_value=500, step=1)
    if not _workload_is_default():
        st.sidebar.caption("Custom workload or network numbers are in use.")
        st.sidebar.button("Restore defaults", on_click=_restore_workload, key="btn_restore_wl",
                          width="stretch")

    _warn_unvalidated(cfg("stack", "draft06"), sc)
    ready = (bool(sizes and sc and nets) and r["images"] and not live and not image_job.is_running()
             and not offline_job.is_running())
    if r["running"] and not live:
        st.sidebar.caption("A lab started in Prepare is stopped while the benchmark runs.")
    if st.sidebar.button("Run benchmark", key="btn_bench", type="primary", width="stretch", disabled=not ready):
        params = {
            "label": clean, "stack": cfg("stack", "draft06"), "sizes": sorted(int(n) for n in sizes),
            "scenarios": list(sc), "trials": int(cfg("bench_trials", 5)),
            "sign_trials": int(cfg("bench_sign_trials", suite_mod.DEFAULT_SIGN_TRIALS)),
            "sample_names": int(cfg("bench_sample", suite_mod.DEFAULT_SAMPLE_NAMES)),
            "n_queries": int(cfg("bench_queries", 500)), "networks": list(nets),
            "udp": int(cfg("bench_udp", 1232)), "jobs": int(cfg("bench_jobs", 1)),
            "workload": _workload(), "profiles": _profiles(),
        }
        try:
            sid = suite_mod.start(params)
            st.session_state.bench_suite_pick = sid
            activity("step", f"Benchmark started · {sid}", json.dumps(params)[:1500], phase="run", source="suite",
                     h=h)
        except Exception as e:  # noqa: BLE001
            st.sidebar.error(str(e)[:600])
        st.rerun()
    st.sidebar.markdown("---")
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_bench"):
        st.rerun()


# ─────────────────────────── BENCHMARK ───────────────────────────

def _suite_params() -> dict:
    return {
        "sizes": sorted(int(n) for n in (cfg("bench_sizes") or SUITE_DEFAULT_SIZES)),
        "scenarios": list(cfg("bench_scenarios") or ["ecdsa", "slh-dsa", "mtl"]),
        "trials": int(cfg("bench_trials", 5)),
        "sign_trials": int(cfg("bench_sign_trials", suite_mod.DEFAULT_SIGN_TRIALS)),
        "sample_names": int(cfg("bench_sample", suite_mod.DEFAULT_SAMPLE_NAMES)),
        "n_queries": int(cfg("bench_queries", 500)),
        "jobs": int(cfg("bench_jobs", 1)),
        "networks": list(cfg("bench_networks") or ["lan"]),
        "udp": int(cfg("bench_udp", 1232)),
    }


def _bench_plan() -> None:
    p = _suite_params()
    label = driver.sanitize_label(cfg("run_label", ""))[:30]
    with st.container(border=True):
        st.markdown("**Planned benchmark**")
        a = st.columns(5)
        a[0].metric(gl.label("zone_size") + "s", len(p["sizes"]), help=gl.tip("zone_size"))
        a[1].metric(gl.label("algorithm") + "s", len(p["scenarios"]), help=gl.tip("algorithm"))
        a[2].metric(gl.label("runs"), p["trials"], help=gl.tip("runs"))
        a[3].metric(gl.label("signing_repeats"), p["sign_trials"], help=gl.tip("signing_repeats"))
        a[4].metric(gl.label("queries_per_run"), p["n_queries"], help=gl.tip("queries_per_run"))
        rows = []
        for n in p["sizes"]:
            s = driver.shape_stats(suite_mod.bench_shape(n), p["scenarios"], p["jobs"])
            rows.append({gl.label("zone_size"): _size_label(n), gl.label("zones"): s["zones"],
                         gl.label("leaf_zones"): suite_mod.bench_shape(n)["sld_count"],
                         gl.label("est_rrsigs"): s["est_rrsigs"],
                         gl.label("est_signing"): driver.format_duration(suite_mod.estimate_seconds(
                             [n], p["scenarios"], p["jobs"], p["sign_trials"], p["sample_names"]))})
        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, width="stretch", hide_index=True, column_config=gl.column_config(df.columns))
        total = suite_mod.estimate_seconds(p["sizes"], p["scenarios"], p["jobs"], p["sign_trials"], p["sample_names"])
        passes = len(p["scenarios"]) * len(p["networks"]) * p["trials"] * 2 * max(1, len(p["sizes"]))
        st.caption(
            f"Signing takes about **{driver.format_duration(total)}** in total, plus measuring "
            f"({passes} cold/warm passes, about {passes * p['n_queries']:,} queries). "
            f"Name **{label or '(none: date and time only)'}** · network profiles {', '.join(p['networks'])} · "
            f"EDNS buffer {p['udp']} B"
        )
        w = _workload()
        st.caption(
            f"Workload: NXDOMAIN **{round(w['nx_fraction'] * 100)}%** · zone skew **{w['zone_zipf_s']:g}** · "
            f"name skew **{w['name_zipf_s']:g}** · query timeout **{w['timeout_s']:g} s**"
            + ("" if _workload_is_default() else " · _custom values_")
        )
        shown = {n: {**driver.PROFILE_DEFAULTS[n], **_profiles().get(n, {})} for n in p["networks"]}
        if shown:
            st.dataframe(
                pd.DataFrame(shown).T.rename(columns=dict(PROFILE_FIELDS)).rename_axis(gl.label("network_profile")),
                width="stretch",
            )


def _cancel_suite(suite_id: str) -> None:
    suite_mod.cancel(suite_id)
    activity("warn", f"Benchmark cancelled · {suite_id}", phase="run", source="suite")


@st.fragment(run_every=3)
def _suite_progress(suite_id: str) -> None:
    s = suite_mod.status(suite_id)
    state = s.get("state", "unknown")
    if state not in ("running", "starting"):
        st.rerun()  # whole page: the sidebar and Results pick up the finished suite
    total = max(1, int(s.get("total", 1)))
    done = int(s.get("done", 0))
    elapsed = time.time() - float(s.get("started") or time.time())
    with st.container(border=True):
        st.markdown(f"**Benchmark running** · {suite_id}")
        st.progress(min(done / total, 1.0), text=f"{done} of {total} steps · {s.get('step', '')}")
        st.caption(f"Elapsed {driver.format_duration(elapsed).lstrip('~')}. You can leave this page; the benchmark "
                   "keeps running and the Results tab fills in when it finishes.")
        st.button("Cancel benchmark", on_click=_cancel_suite, args=(suite_id,), key="btn_cancel_suite")
        log = SUITES_DIR / suite_id / "progress.log"
        if log.is_file():
            with st.expander("Progress log"):
                st.code(log.read_text(encoding="utf-8", errors="replace")[-3000:], language="text")


def benchmark_run(h: driver.LabHandle, stt: dict) -> None:
    live = suite_mod.running_suite()
    if live:
        _suite_progress(live["id"])
        return
    last = next(iter(suite_mod.list_suites()), None)
    if last and last["state"] in ("failed", "stopped", "cancelled"):
        s = suite_mod.status(last["id"])
        st.warning(f"The last benchmark ({last['title']}) {s.get('state')}: {s.get('error') or s.get('step', '')}")
    st.caption("Set the options in the sidebar, then **Run benchmark**: it signs, starts, measures and stops the lab "
               "for each zone size.")
    _bench_plan()


def _fmt_ci(med: float | None, lo: float | None, hi: float | None) -> str:
    if med is None or pd.isna(med):
        return "—"
    base = compare.fmt_seconds(med)
    if lo is None or hi is None or pd.isna(lo) or pd.isna(hi) or lo == hi:
        return base
    return f"{base} ({compare.fmt_seconds(lo)}–{compare.fmt_seconds(hi)})"


def _signing_stats_table(stats: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        gl.label("algorithm"): stats["scenario"],
        gl.label("signing_repeats"): stats["trials"],
        "Sample (names)": stats["sample_names"],
        "Sign per RRSIG (ms)": stats["sign_ms_per_rrsig"].round(3),
        "Fixed per zone (s)": stats["sign_fixed_s"].round(3),
        "Zone signing time, estimated (CI of the median)": [_fmt_ci(m, a, b) for m, a, b in
                                             zip(stats["est_sign_s"], stats["est_sign_lo"], stats["est_sign_hi"])],
        "Zone verification time, estimated (CI of the median)": [_fmt_ci(m, a, b) for m, a, b in
                                               zip(stats["est_verify_s"], stats["est_verify_lo"], stats["est_verify_hi"])],
        "Full zones sign, measured once": stats["full_sign_s"].map(compare.fmt_seconds),
        "Full zones verify, measured once": stats["full_verify_s"].map(compare.fmt_seconds),
        gl.label("signed_zone_size"): stats["signed_bytes"].map(compare.fmt_bytes),
    })


def _suite_settings(sd: dict) -> None:
    s = sd["suite"]
    with st.expander("Settings and environment recorded for this benchmark"):
        st.markdown(
            f"**Zone sizes** {', '.join(f'{n:,}' for n in s['sizes'])} names · **Algorithms** "
            f"{', '.join(s['scenarios'])} · **Stack** {s['stack']}  \n"
            f"**Runs** {s['trials']} (cold then warm cache) · **Queries per run** {s['n_queries']} · "
            f"**Network profiles** {', '.join(s['networks'])} · **EDNS buffer** {s['udp']} B  \n"
            f"**Signing repeats** {s['sign_trials']} on samples of {s['sample_names']} names (zone time is an "
            f"extrapolation, the full zones are also signed once)"
        )
        wl = s.get("workload") or driver.WORKLOAD_DEFAULTS
        st.markdown(
            f"**Workload** NXDOMAIN {round(float(wl['nx_fraction']) * 100)}% · zone skew {wl['zone_zipf_s']} · "
            f"name skew {wl['name_zipf_s']} · query timeout {wl['timeout_s']} s · seeded (same seed for every algorithm)"
        )
        hier = [{gl.label("zone_size"): f"{n:,}", gl.label("leaf_zones"): suite_mod.bench_shape(n)["sld_count"],
                 "Names per leaf zone": ", ".join(str(x) for x in suite_mod.bench_shape(n)["sld_sizes"])}
                for n in s["sizes"]]
        hier_df = pd.DataFrame(hier)
        st.dataframe(hier_df, width="stretch", hide_index=True, column_config=gl.column_config(hier_df.columns))
        first = next(iter(sd["runs"].values()), {})
        env = first.get("environment") or {}
        if env:
            dock = env.get("docker") or {}
            st.markdown(
                f"**Environment** Docker {dock.get('ServerVersion')} · {dock.get('NCPU')} CPUs · "
                f"{round((dock.get('MemTotal') or 0) / 1024 ** 3, 1)} GiB · host {s.get('host', {}).get('platform', '')}  \n"
                f"**Pinned images** " + ", ".join(sorted({(v.get('image') or '') for v in (env.get('images') or {}).values()}))
            )
        cpus = ((first.get("lab") or {}).get("cpusets")) or {}
        if cpus:
            st.caption(f"CPU pinning: {cpus}")
        profs = {n: v for n, v in (s.get("profiles") or {}).items() if n in s["networks"]}
        if profs:
            st.dataframe(pd.DataFrame(profs).T.rename_axis(gl.label("network_profile")), width="stretch")


RESULT_COLUMNS = {
    "scenario": gl.label("algorithm"), "profile": gl.label("network_profile"), "pass": gl.label("udp_cache"),
    "udp_limit": gl.label("edns_buffer") + " (B)", "trials": gl.label("runs"), "valid_trials": gl.label("valid_runs"),
    "median_ms": gl.label("latency_p50"), "p95_ms": gl.label("latency_p95"),
    "upstream_bytes": gl.label("resolver_auth_bytes"), "upstream_queries": gl.label("resolver_auth_queries"),
    "upstream_truncated": gl.label("tc_responses"), "validated_pct": gl.label("validated_pct"),
    "failed": gl.label("failed"), "servfail": gl.label("servfail"), "timeouts": gl.label("timeouts"),
}


def benchmark_results(h: driver.LabHandle) -> None:
    suites = [s for s in suite_mod.list_suites() if s["complete"]]
    if not suites:
        st.info("No finished benchmark yet. Choose the options in the sidebar and press **Run benchmark**.")
        return
    by = {s["id"]: s for s in suites}
    if st.session_state.get("bench_suite_pick") not in by:
        st.session_state.pop("bench_suite_pick", None)
    pick = st.selectbox("Benchmark", list(by), key="bench_suite_pick", format_func=lambda i: by[i]["title"])
    sd = compare.load_suite(by[pick]["path"])
    if sd is None:
        st.warning("This benchmark has no stored results.")
        return
    s = sd["suite"]
    m = st.columns(4)
    m[0].metric("Name", s.get("label") or "—")
    m[1].metric("Started", by[pick]["when"])
    m[2].metric("Duration", driver.format_duration(s.get("seconds", 0)).lstrip("~"))
    m[3].metric(gl.label("zone_size") + "s", ", ".join(f"{n:,}" for n in sd["datasets"]), help=gl.tip("zone_size"))
    _suite_settings(sd)
    st.button("Open in Compare →", on_click=_open_in_compare, args=(pick,), key="bench_to_compare")

    stats = compare.signing_trial_stats(sd["trials"], sd["full"])
    tabs = st.tabs([f"{n:,} names" for n in sd["datasets"]])
    for tab, n in zip(tabs, sd["datasets"]):
        with tab:
            st.markdown("**Signing and verification**")
            ds = stats[stats["dataset"] == n]
            if ds.empty:
                st.caption("No signing data.")
            else:
                tbl = _signing_stats_table(ds)
                st.dataframe(tbl, width="stretch", hide_index=True, column_config=gl.column_config(tbl.columns, {
                    "Zone signing time, estimated (CI of the median)": gl.tip("signing_time"),
                    "Zone verification time, estimated (CI of the median)": gl.tip("verification_time"),
                    "Full zones sign, measured once": "The complete zones the lab serves, signed one time.",
                    "Full zones verify, measured once": "The complete zones, validated one time.",
                    "Sign per RRSIG (ms)": "Time to produce one signature (RRSIG), from the signing repeats.",
                    "Fixed per zone (s)": "Time per zone that does not depend on the number of signatures.",
                    "Sample (names)": gl.tip("signing_sample")}))
                jobs = int(s.get("jobs", 1))
                st.caption("Estimated = from the repeated sample trials (run one at a time), extrapolated to every "
                           "zone of the dataset. Measured once = the full zones that the lab served, signed one time "
                           f"with {jobs} parallel job(s)"
                           + ("" if jobs == 1 else "; with more than one job these timings are inflated by contention")
                           + ".")
            summary = sd["bench"].get(n)
            if summary is not None and not summary.empty:
                st.markdown("**Resolver measurements**", help="What a validating Unbound did with the replayed queries: "
                            "latency, bytes to the authoritative servers, truncation and failures.")
                cols = [c for c in ["scenario", "profile", "pass", "udp_limit", "trials", "valid_trials", "median_ms",
                                    "p95_ms", "upstream_bytes", "upstream_queries", "upstream_truncated",
                                    "validated_pct", "failed", "servfail", "timeouts"] if c in summary.columns]
                shown = summary[cols].rename(columns=RESULT_COLUMNS)
                st.dataframe(shown, width="stretch", hide_index=True,
                             column_config=gl.column_config(shown.columns, {"Runs": gl.tip("runs")}))
                for note in compare.lookup_problems(summary):
                    st.warning(note)
                _bench_charts(summary)
    report_html = by[pick]["path"] / "report.html"
    report_md = by[pick]["path"] / "report.md"
    if not report_html.is_file() or not report_md.is_file():
        try:
            from live_lab import suite_report
            suite_report.write(by[pick]["path"])
            report_html = by[pick]["path"] / "report.html"
            report_md = by[pick]["path"] / "report.md"
        except Exception as e:  # noqa: BLE001
            st.caption(f"Report not generated yet: {e}")
    d0, d1, d2 = st.columns(3)
    if report_html.is_file():
        d0.download_button("Download report (HTML)", data=report_html.read_bytes(),
                           file_name=f"{pick}-report.html", mime="text/html", key="bench_dl_report_html")
    elif report_md.is_file():
        d0.download_button("Download report (Markdown)", data=report_md.read_text(encoding="utf-8"),
                           file_name=f"{pick}-report.md", mime="text/markdown", key="bench_dl_report_md")
    d1.download_button("Download signing trials (CSV)", data=sd["trials"].to_csv(index=False),
                       file_name=f"{pick}-signing-trials.csv", mime="text/csv", key="bench_dl_trials")
    d2.download_button("Download full-zone signing (CSV)", data=sd["full"].to_csv(index=False),
                       file_name=f"{pick}-signing-full.csv", mime="text/csv", key="bench_dl_full")
    if report_html.is_file():
        st.caption(f"Reproducible report on disk: `{report_html}` (also `report.md` and `figures/`).")


def _bench_charts(summary: pd.DataFrame) -> None:
    if summary.empty:
        return
    base = {"x": "scenario", "color": "pass", "barmode": "group"}
    facet = {"facet_col": "profile"} if "profile" in summary and summary["profile"].nunique() > 1 else {}
    if "median_ms" in summary:
        st.plotly_chart(
            px.bar(summary, y="median_ms", title=gl.label("latency_p50"), **base, **facet),
            width="stretch",
        )
    if "upstream_bytes" in summary:
        st.plotly_chart(
            px.bar(summary, y="upstream_bytes", title=gl.label("resolver_auth_bytes") + " per pass", **base, **facet),
            width="stretch",
        )


def _open_in_compare(suite_id: str) -> None:
    _store().update(cmp_src="live", cmp_view="algorithms", cmp_a_live=suite_id)
    for k in ("w_cmp_src", "w_cmp_view", "w_cmp_a_live"):
        st.session_state.pop(k, None)
    _go("compare")

