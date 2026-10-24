"""Cipherops · MTL DNSSEC Lab — control room for the multi-zone stack.

    streamlit run dashboard/live_lab/app.py
"""

from __future__ import annotations

import importlib
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from live_lab import decode, driver  # noqa: E402

decode = importlib.reload(decode)
driver = importlib.reload(driver)

st.set_page_config(page_title="Cipherops · MTL DNSSEC Lab", layout="wide", page_icon="🛰️")

APP_NAME = "Cipherops · MTL DNSSEC Lab"
APP_TAGLINE = "Root → TLD → SLD · validating Unbound · Verisign MTL"

IMAGE_LABELS = {
    "cipherops-eco-nsd": "NSD authoritative servers",
    "cipherops-eco-unbound": "Unbound validating resolver",
    "cipherops-eco-tools": "Signing & probe tools",
}

SCENARIO_BLURB = {
    "ecdsa": "ECDSA-P256 baseline",
    "slh-dsa": "SLH-DSA (large sigs, often TCP)",
    "mtl": "SLH-DSA + MTL (condensed answers)",
    "ed25519": "Ed25519 baseline",
    "mixed-mtl": "ECDSA root/TLD, MTL at SLD",
}

LEVEL_ICON = {"info": "ℹ️", "ok": "✅", "warn": "⚠️", "err": "❌", "step": "▶️"}
ACTIVITY_CAP = 500

_DEMO_STATE_KEYS = (
    "activity", "query_log", "last_query", "last_pcap", "last_dropped",
    "last_stats", "last_story", "last_wire", "last_log", "cache_result",
    "prepare_wall_s", "prepare_summary", "last_seq_steps", "last_seq_pcap", "auto_decode",
)


def _handle() -> driver.LabHandle:
    if "lab_handle" not in st.session_state:
        st.session_state.lab_handle = driver.LabHandle()
    return st.session_state.lab_handle


def activity(
    level: str, title: str, detail: str = "", *,
    phase: str = "ui", source: str = "ui", h: driver.LabHandle | None = None,
) -> None:
    entry = {
        "t": datetime.now().strftime("%H:%M:%S"),
        "level": level, "title": title, "detail": (detail or "").strip(),
        "phase": phase, "source": source,
    }
    log = st.session_state.setdefault("activity", [])
    log.insert(0, entry)
    st.session_state.activity = log[:ACTIVITY_CAP]
    handle = h or st.session_state.get("lab_handle")
    if handle is not None:
        try:
            line = f"{entry['t']} [{source}/{phase}] {level.upper()} {title}"
            if entry["detail"]:
                line += f" | {entry['detail'][:2000]}"
            driver.append_activity_file(handle, line)
        except Exception:  # noqa: BLE001
            pass


def _log_chunks(text: str, *, phase: str, source: str, title: str) -> None:
    chunks = driver.chunk_log(text)
    for i, ch in enumerate(chunks, 1):
        suffix = f" ({i}/{len(chunks)})" if len(chunks) > 1 else ""
        activity("info", f"{title}{suffix}", f"```\n{ch}\n```", phase=phase, source=source)


def clear_ui_demo_state() -> None:
    for k in _DEMO_STATE_KEYS:
        st.session_state.pop(k, None)
    st.session_state.activity = []
    st.session_state.query_log = []
    st.session_state.show_prepare_metrics = False


def prepare_metrics_visible(h: driver.LabHandle) -> bool:
    if "show_prepare_metrics" in st.session_state:
        return bool(st.session_state.show_prepare_metrics)
    return bool(driver.load_session(h).get("show_prepare_metrics", False))


def progress(h: driver.LabHandle, stt: dict | None = None) -> dict[str, bool]:
    """Sidebar greens = this demo's readiness, not leftover files on disk alone.

    Step 2 stays white until Prepare has been run for this session (metrics visible).
    Step 4 stays white unless the lab is actually running with an active scenario.
    """
    stt = stt or {}
    try:
        if not stt:
            stt = driver.status(h)
    except Exception:  # noqa: BLE001
        stt = {}
    imgs = stt.get("images") or {}
    containers = stt.get("containers") or {}
    running = any(containers.values())
    prepared_disk = bool(stt.get("prepared"))
    # Old work/zones can exist without clicking Prepare this time — don't mark green.
    prepared_session = prepared_disk and prepare_metrics_visible(h)
    return {
        "images": bool(imgs) and all(imgs.values()),
        "prepared": prepared_session,
        "running": running,
        "activated": running and bool(stt.get("active")),
        "queried": bool(st.session_state.get("query_log") or st.session_state.get("last_query")),
    }


def _step_label(n: int, title: str, done: bool) -> str:
    mark = "🟢" if done else "⚪"
    return f"{mark}  **Step {n}** · {title}"


def _apply_ui_config(h: driver.LabHandle) -> None:
    """Write configs/_dashboard.yaml from sidebar widgets and point the handle at it."""
    zone = st.session_state.get("opt_zone_size", "small")
    sc = st.session_state.get("opt_scenarios") or ["ecdsa", "slh-dsa", "mtl"]
    nq = int(st.session_state.get("opt_n_queries", 40))
    trials = int(st.session_state.get("opt_trials", 1))
    nets = st.session_state.get("opt_networks") or ["lan"]
    udp = int(st.session_state.get("opt_udp", 1232))
    path = driver.write_ui_config(
        zone_size=zone,
        scenarios=list(sc),
        n_queries=nq,
        trials=trials,
        network_run=list(nets),
        udp_limits=[udp],
    )
    h.config = path
    h.stack = st.session_state.get("opt_stack", "draft06")
    st.session_state.lab_handle = h
    st.session_state.ui_udp = udp
    st.session_state.ui_scenarios = list(sc)
    st.session_state.ui_networks = list(nets)
    st.session_state.ui_trials = trials


def sidebar(h: driver.LabHandle) -> dict:
    st.sidebar.title("Cipherops")
    st.sidebar.caption("MTL DNSSEC Lab")

    st.sidebar.markdown("### Lab options")
    size_keys = list(driver.ZONE_SIZE_PRESETS.keys())
    st.sidebar.selectbox(
        "Zone size",
        size_keys,
        format_func=lambda k: driver.ZONE_SIZE_PRESETS[k]["label"],
        key="opt_zone_size",
    )
    size = st.session_state.get("opt_zone_size", "small")
    preset = driver.ZONE_SIZE_PRESETS[size]
    st.sidebar.caption(
        f"{preset['sld_count']} SLDs · sizes {preset['sld_sizes']} · "
        f"TLDs {', '.join(preset['tlds'])}"
    )

    all_sc = list(driver.SCENARIO_ALGS.keys())
    default_sc = ["ecdsa", "slh-dsa", "mtl"]
    st.sidebar.multiselect(
        "Scenarios to sign / run",
        all_sc,
        default=[s for s in default_sc if s in all_sc],
        key="opt_scenarios",
    )
    st.sidebar.number_input("Sign jobs", min_value=1, max_value=8, value=2, key="opt_jobs")
    st.sidebar.selectbox("Stack", ["draft06"], key="opt_stack")

    st.sidebar.markdown("### Benchmark options")
    if st.session_state.get("_zone_prev") != size:
        st.session_state.opt_n_queries = int(preset["n_queries_default"])
        st.session_state._zone_prev = size
    if "opt_n_queries" not in st.session_state:
        st.session_state.opt_n_queries = int(preset["n_queries_default"])
    if "opt_trials" not in st.session_state:
        st.session_state.opt_trials = 1
    st.sidebar.number_input("Queries / trial", min_value=10, max_value=5000, key="opt_n_queries")
    st.sidebar.number_input("Trials", min_value=1, max_value=10, key="opt_trials")
    st.sidebar.multiselect(
        "Network profiles",
        ["lan", "wan", "lossy"],
        default=["lan"],
        key="opt_networks",
    )
    st.sidebar.selectbox("UDP buffer", [1232, 4096], key="opt_udp")

    try:
        if st.session_state.get("opt_scenarios"):
            _apply_ui_config(h)
        else:
            st.sidebar.warning("Select at least one scenario")
    except Exception as e:  # noqa: BLE001
        st.sidebar.error(str(e)[:400])

    try:
        stt = driver.status(h)
    except Exception as e:  # noqa: BLE001
        st.sidebar.error(str(e)[:300])
        stt = {}
    done = progress(h, stt)
    runs = driver.list_run_dirs(h)
    done_bench = bool(runs)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Steps")

    # —— Step 1 Build / Pull ——
    st.sidebar.markdown(_step_label(1, "Images", done["images"]))
    img_mode = st.sidebar.radio(
        "Image source",
        ["Build locally", "Pull from registry"],
        horizontal=False,
        key="opt_image_mode",
    )
    if img_mode.startswith("Pull"):
        st.session_state.setdefault("opt_registry", "")
        st.sidebar.text_input(
            "Registry",
            placeholder="ghcr.io/your-org/cipherops",
            key="opt_registry",
            help="Pulls {registry}/cipherops-eco-{nsd,unbound,tools}:{stack} and retags locally.",
        )
        if st.sidebar.button("Pull images", key="btn_pull", use_container_width=True):
            reg = st.session_state.get("opt_registry", "")
            activity("step", "Pull images", reg, phase="build", source="compose", h=h)
            with st.spinner("Pulling…"):
                try:
                    t0 = time.perf_counter()
                    log = driver.pull_images(h, reg)
                    secs = time.perf_counter() - t0
                    st.session_state.last_log = log[-8000:]
                    _log_chunks(log, phase="build", source="compose", title="pull")
                    activity("ok", f"Pull done ({secs:.0f}s)", "", phase="build", source="compose", h=h)
                    st.sidebar.success(f"Done · {secs:.0f}s")
                except Exception as e:  # noqa: BLE001
                    st.sidebar.error(str(e)[:800])
                    activity("err", "Pull failed", str(e)[:2000], phase="build", source="compose", h=h)
            st.rerun()
    else:
        if st.sidebar.button("Build images", key="btn_build", use_container_width=True):
            activity("step", "Build images", "", phase="build", source="compose", h=h)
            with st.spinner("Building…"):
                try:
                    t0 = time.perf_counter()
                    log = driver.build_images(h)
                    secs = time.perf_counter() - t0
                    st.session_state.last_log = log[-8000:]
                    _log_chunks(log, phase="build", source="compose", title="build")
                    activity("ok", f"Build done ({secs:.0f}s)", "", phase="build", source="compose", h=h)
                    st.sidebar.success(f"Done · {secs:.0f}s")
                except Exception as e:  # noqa: BLE001
                    st.sidebar.error(str(e)[:600])
                    activity("err", "Build failed", str(e)[:2000], phase="build", source="compose", h=h)
            st.rerun()

    # —— Step 2 Prepare ——
    st.sidebar.markdown(_step_label(2, "Prepare (sign)", done["prepared"]))
    st.sidebar.caption(f"Lab `{h.lab}` · {h.config}")
    if st.sidebar.button("Prepare", key="btn_prep", use_container_width=True):
        _apply_ui_config(h)
        sc = list(st.session_state.get("opt_scenarios") or [])
        jobs = int(st.session_state.get("opt_jobs", 2))
        activity("step", "Prepare", f"size={size} scenarios={sc}", phase="prepare", source="prepare", h=h)
        with st.spinner("Signing…"):
            try:
                t0 = time.perf_counter()
                log = driver.prepare(h, jobs=jobs, scenarios=sc)
                secs = time.perf_counter() - t0
                st.session_state.last_log = log[-8000:]
                st.session_state.prepare_wall_s = secs
                st.session_state.show_prepare_metrics = True
                summary = driver.prepare_summary(h, wall_s=secs, scenarios=sc)
                st.session_state.prepare_summary = summary
                sess = driver.load_session(h)
                sess["prepare_wall_s"] = secs
                sess["show_prepare_metrics"] = True
                sess["prepare_summary"] = summary
                driver.save_session(h, sess)
                _log_chunks(log, phase="prepare", source="prepare", title="prepare")
                paths = (
                    f"work: `{summary['work_dir']}`\n"
                    f"zones: `{summary['zones_dir']}`\n"
                    f"active: `{summary['active_dir']}`\n"
                    f"compose: `{summary['compose']}`"
                )
                activity("ok", f"Prepare done ({secs:.0f}s)", paths, phase="prepare", source="prepare", h=h)
                st.sidebar.success(f"Done · {secs:.0f}s")
            except Exception as e:  # noqa: BLE001
                st.sidebar.error(str(e)[:600])
                activity("err", "Prepare failed", str(e)[:2000], phase="prepare", source="prepare", h=h)
        st.rerun()

    # —— Step 3 Start ——
    st.sidebar.markdown(_step_label(3, "Start lab", done["running"]))
    b3a, b3b = st.sidebar.columns(2)
    if b3a.button("Start", key="btn_up", use_container_width=True):
        activity("step", "Start lab", "", phase="compose", source="compose", h=h)
        with st.spinner("Starting…"):
            try:
                log = driver.up(h)
                st.session_state.last_log = log[-4000:]
                _log_chunks(log, phase="compose", source="compose", title="up")
                activity("ok", "Lab started", "", phase="compose", source="compose", h=h)
            except Exception as e:  # noqa: BLE001
                st.sidebar.error(str(e)[:600])
                activity("err", "Start failed", str(e)[:2000], phase="compose", source="compose", h=h)
        st.rerun()
    if b3b.button("Stop", key="btn_down", use_container_width=True):
        with st.spinner("Stopping…"):
            try:
                driver.down(h)
                activity("ok", "Lab stopped", "", phase="compose", source="compose", h=h)
            except Exception as e:  # noqa: BLE001
                st.sidebar.error(str(e)[:600])
        st.rerun()

    # —— Step 4 Activate ——
    st.sidebar.markdown(_step_label(4, "Activate scenario", done["activated"]))
    scenarios = []
    try:
        if h.prepared():
            scenarios = h.scenarios()
    except Exception:  # noqa: BLE001
        scenarios = []
    active = stt.get("active")
    udp = int(st.session_state.get("ui_udp", 1232))
    if scenarios:
        if "side_scenario" not in st.session_state:
            st.session_state.side_scenario = active if active in scenarios else scenarios[0]
        elif st.session_state.side_scenario not in scenarios:
            st.session_state.side_scenario = scenarios[0]

        scenario = st.sidebar.selectbox("Scenario", scenarios, key="side_scenario")
        st.session_state.selected_scenario = scenario
        st.sidebar.caption(SCENARIO_BLURB.get(scenario, ""))

        if done["running"] and scenario and scenario != active:
            activity("step", f"Activate {scenario}", "", phase="activate", source="compose", h=h)
            with st.spinner(f"Switching to {scenario}…"):
                try:
                    msg = driver.activate(h, scenario, udp=udp)
                    st.session_state.query_log = []
                    _log_chunks(msg, phase="activate", source="compose", title="activate")
                    activity("ok", f"{scenario} live", "", phase="activate", source="compose", h=h)
                    stt = driver.status(h)
                except Exception as e:  # noqa: BLE001
                    st.sidebar.error(str(e)[:600])
                    activity("err", "Activate failed", str(e)[:2000],
                             phase="activate", source="compose", h=h)
            st.rerun()
        elif not done["running"] and scenario != active:
            st.sidebar.caption("Start lab to apply this scenario")
    else:
        st.sidebar.caption("Prepare first")

    # —— Step 5 Query ——
    st.sidebar.markdown(_step_label(5, "Query", done["queried"]))
    st.sidebar.caption("Lab tab → Run query")

    # —— Step 6 Benchmark ——
    st.sidebar.markdown(_step_label(6, "Run benchmark", done_bench))
    if st.sidebar.button("Run benchmark", key="btn_bench", use_container_width=True):
        if not done["running"]:
            st.sidebar.error("Start lab first")
        else:
            _apply_ui_config(h)
            sc = list(st.session_state.get("opt_scenarios") or [])
            nets = list(st.session_state.get("opt_networks") or ["lan"])
            trials = int(st.session_state.get("opt_trials", 1))
            activity(
                "step", "Benchmark",
                f"scenarios={sc} profiles={nets} trials={trials}",
                phase="run", source="compose", h=h,
            )
            with st.spinner("Running trials (cold/warm)…"):
                try:
                    t0 = time.perf_counter()
                    log = driver.run_benchmark(
                        h, scenarios=sc, profiles=nets, trials=trials, udp=udp,
                    )
                    secs = time.perf_counter() - t0
                    st.session_state.last_log = log[-8000:]
                    st.session_state.last_run_dir = (
                        str(driver.list_run_dirs(h)[0]) if driver.list_run_dirs(h) else ""
                    )
                    _log_chunks(log, phase="run", source="compose", title="ecolab run")
                    activity("ok", f"Benchmark done ({secs:.0f}s)",
                             st.session_state.last_run_dir, phase="run", source="compose", h=h)
                    st.sidebar.success(f"Done · {secs:.0f}s")
                except Exception as e:  # noqa: BLE001
                    st.sidebar.error(str(e)[:800])
                    activity("err", "Benchmark failed", str(e)[:2000],
                             phase="run", source="compose", h=h)
            st.rerun()

    st.sidebar.markdown("---")
    if st.sidebar.button("Refresh", use_container_width=True):
        st.rerun()

    st.sidebar.markdown("### Reset")
    wipe_pcaps = st.sidebar.checkbox("Delete pcaps", value=True, key="reset_pcaps")
    wipe_work = st.sidebar.checkbox("Delete work (signed zones)", value=False, key="reset_work")
    hide_sign = st.sidebar.checkbox("Hide sign metrics", value=True, key="reset_hide_sign")
    flush_ub = st.sidebar.checkbox("Flush Unbound", value=False, key="reset_flush_ub")
    if st.sidebar.button("Clear results", use_container_width=True, key="reset_demo_btn"):
        driver.clear_demo_data(
            h, session=True, activity=True, pcaps=wipe_pcaps,
            hide_prepare_metrics=hide_sign, work=wipe_work,
        )
        clear_ui_demo_state()
        st.session_state.show_prepare_metrics = not hide_sign
        if flush_ub:
            try:
                if any((stt.get("containers") or {}).values()):
                    driver.flush(h)
            except Exception:  # noqa: BLE001
                pass
        activity("ok", "Cleared", "work wiped" if wipe_work else "", phase="ui", source="ui", h=h)
        st.rerun()

    return stt


def _render_prepare_summary(h: driver.LabHandle) -> None:
    summary = st.session_state.get("prepare_summary")
    if not summary:
        sess = driver.load_session(h)
        summary = sess.get("prepare_summary")
        if summary:
            st.session_state.prepare_summary = summary
    if not summary and h.prepared():
        wall = driver.load_session(h).get("prepare_wall_s")
        summary = driver.prepare_summary(h, wall_s=wall)
        st.session_state.prepare_summary = summary
    if not summary:
        return
    with st.expander("Prepare output (paths & what happened)", expanded=True):
        wall = summary.get("wall_s")
        wall_txt = f"{wall:.1f}" if isinstance(wall, (int, float)) else (wall or "—")
        st.markdown(
            f"**Lab** `{summary.get('lab')}` · wall **{wall_txt}** s  \n"
            f"Signed each selected scenario bottom-up (SLD → TLD → root), "
            f"wrote manifests, compose file, and initial `active/` configs."
        )
        st.code(
            "\n".join([
                f"work     {summary.get('work_dir')}",
                f"zones    {summary.get('zones_dir')}",
                f"active   {summary.get('active_dir')}",
                f"compose  {summary.get('compose')}",
            ]),
            language="text",
        )
        rows = summary.get("scenarios") or []
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def status_panel(h: driver.LabHandle, stt: dict) -> None:
    st.subheader("Status")
    imgs = stt.get("images") or {}
    containers = stt.get("containers") or {}
    active = stt.get("active") or "—"
    selected = st.session_state.get("selected_scenario") or st.session_state.get("side_scenario") or active
    zone = st.session_state.get("opt_zone_size", "small")
    st.caption(
        f"Lab **{h.lab}** · zone size **{zone}** · "
        f"queries/trial **{st.session_state.get('opt_n_queries', '—')}** · "
        f"trials **{st.session_state.get('opt_trials', '—')}**"
    )
    c = st.columns(4)
    c[0].metric("Docker images", f"{sum(imgs.values())}/{len(imgs) or 3}")
    c[1].metric("Prepared", "yes" if stt.get("prepared") else "no")
    c[2].metric("Containers", str(sum(1 for v in containers.values() if v)))
    c[3].metric("Scenario", active)
    if selected and active and selected != active and selected != "—":
        st.caption(f"Sidebar selection **{selected}** — start lab to apply.")

    left, right = st.columns(2)
    with left:
        st.markdown("**Docker images**")
        for name, ok in imgs.items():
            repo = name.split(":")[0]
            tag = name.split(":")[1] if ":" in name else ""
            label = IMAGE_LABELS.get(repo, repo)
            st.write(("🟢" if ok else "🔴"), f"{label} (`{repo}:{tag}`)" if tag else f"{label} (`{repo}`)")
    with right:
        st.markdown("**Containers**")
        for name, ok in sorted(containers.items()):
            st.write(("🟢" if ok else "🔴"), name)

    _render_prepare_summary(h)


def show_sequence_diagram(steps: list) -> None:
    """Display one capture sequence (local matplotlib PNG — no Mermaid/Kroki)."""
    if not steps:
        st.caption("No diagram.")
        return
    try:
        st.image(decode.sequence_png_bytes(steps), use_container_width=True)
    except Exception as e:  # noqa: BLE001
        st.error(f"Diagram failed: {e}")


def query_panel(h: driver.LabHandle, stt: dict) -> None:
    st.subheader("Query")
    if not any((stt.get("containers") or {}).values()):
        st.warning("Complete Step 3 — Start lab.")
        return

    presets = driver.query_presets(h)
    st.session_state._presets_list = presets
    if "qname_in" not in st.session_state:
        st.session_state.qname_in = presets[2][0]
        st.session_state.qtype_in = presets[2][1]
        st.session_state.preset_idx = 2

    def _on_preset() -> None:
        i = min(int(st.session_state.get("preset_idx", 0)), len(presets) - 1)
        st.session_state.qname_in = presets[i][0]
        st.session_state.qtype_in = presets[i][1]

    st.selectbox(
        "Preset", list(range(len(presets))),
        format_func=lambda i: f"{presets[i][0]} {presets[i][1]}",
        key="preset_idx", on_change=_on_preset,
    )
    qname = st.text_input("QNAME", key="qname_in")
    qtype = st.text_input("QTYPE", key="qtype_in")
    path = st.radio(
        "Path",
        ["Through Unbound", "Direct to root", "Direct to SLD"],
        horizontal=True,
    )
    server = {
        "Through Unbound": "10.53.0.53",
        "Direct to root": "10.53.0.2",
        "Direct to SLD": driver.sld_server_ip(h),
    }[path]
    recurse = path.startswith("Through")
    if path.startswith("Direct to SLD") and (
        qname.strip() in (".", "") or (qtype.upper() == "DNSKEY" and qname.rstrip(".").count(".") == 0)
    ):
        st.warning("SLD path is for leaf names — not root `. DNSKEY`.")

    c1, c2, c3 = st.columns(3)
    dnssec = c1.checkbox("DO (DNSSEC RRs)", value=True)
    mtl_full = c2.checkbox("MTL-FULL", value=False)
    capture = c3.checkbox("Capture", value=True)
    if capture:
        sniffer = "unbound (full chain)" if recurse else "client (direct to auth — no Unbound hops)"
        st.caption(f"Capture sniffer: **{sniffer}**")

    if st.button("Run query", type="primary"):
        pcap_path = None
        activity("step", f"Query {qname} {qtype}", path, phase="probe", source="probe", h=h)
        try:
            if recurse:
                try:
                    driver.unbound_stats(h)
                except Exception:  # noqa: BLE001
                    pass
            if capture:
                pcap_path = driver.start_capture(h, server=server)
            row = driver.probe(
                h, qname, qtype, server=server, mtl_full=mtl_full,
                dnssec=dnssec, recurse=recurse,
            )
            if recurse:
                try:
                    row["unbound_stats"] = driver.unbound_stats_delta({}, driver.unbound_stats(h))
                except Exception:  # noqa: BLE001
                    row["unbound_stats"] = {}
            dropped = 0
            if capture:
                pcap_path, dropped = driver.stop_capture(h)
                if pcap_path and Path(pcap_path).exists():
                    try:
                        df = decode.decode_pcap(pcap_path)
                        st.session_state.last_story = decode.build_story(df)
                        st.session_state.last_wire = decode.wire_summary(df)
                        st.session_state.auto_decode = True
                        activity("info", "Packet story", "\n".join((st.session_state.last_story or [])[:10]),
                                 phase="pcap", source="pcap", h=h)
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    _log_chunks(driver.container_logs(h, "unbound", 40),
                                phase="probe", source="unbound", title="unbound")
                except Exception:  # noqa: BLE001
                    pass

            st.session_state.last_query = row
            st.session_state.last_pcap = str(pcap_path) if pcap_path else None
            st.session_state.last_dropped = dropped
            st.session_state.last_stats = row.get("unbound_stats") or {}
            st.session_state.setdefault("query_log", []).append({**row, "pcap": st.session_state.last_pcap})
            driver.append_session_query(h, {**row, "pcap": st.session_state.last_pcap})
            size = row.get("tcp_response_bytes") or row.get("response_bytes") or 0
            activity(
                "ok",
                f"{row.get('rcode')} · AD={'yes' if row.get('ad') else 'no'} · {size:,} B",
                f"{row.get('latency_ms', 0):.1f} ms",
                phase="probe", source="probe", h=h,
            )
        except Exception as e:  # noqa: BLE001
            st.error(str(e)[:800])
            activity("err", "Query failed", str(e)[:2000], phase="probe", source="probe", h=h)
            if capture:
                try:
                    driver.stop_capture(h)
                except Exception:  # noqa: BLE001
                    pass

    row = st.session_state.get("last_query")
    if row:
        m = st.columns(5)
        m[0].metric("RCODE", row.get("rcode", "—"))
        m[1].metric("AD", "YES" if row.get("ad") else "no")
        m[2].metric("Transport", row.get("transport", "—"))
        m[3].metric("Bytes", f"{(row.get('tcp_response_bytes') or row.get('response_bytes') or 0):,}")
        m[4].metric("Latency", f"{row.get('latency_ms', 0):.1f} ms")
        stats = row.get("unbound_stats") or st.session_state.get("last_stats") or {}
        if stats:
            s = st.columns(4)
            s[0].metric("Cache hits", int(stats.get("total.num.cachehits") or 0))
            s[1].metric("Cache miss", int(stats.get("total.num.cachemiss") or 0))
            s[2].metric("Secure", int(stats.get("total.num.answer.secure") or 0))
            s[3].metric("Recursion s", f"{stats.get('total.recursion.time.avg') or 0:.4f}")
        if row.get("rrsigs"):
            st.dataframe(pd.DataFrame(row["rrsigs"]), use_container_width=True, hide_index=True)

    log = st.session_state.get("query_log") or []
    if len(log) > 1:
        df = pd.DataFrame(log)
        cols = [c for c in ["qname", "qtype", "rcode", "ad", "transport", "response_bytes",
                            "tcp_response_bytes", "latency_ms"] if c in df.columns]
        st.dataframe(df[cols], use_container_width=True, hide_index=True)


def cache_panel(h: driver.LabHandle, stt: dict) -> None:
    st.subheader("Cold vs warm")
    if not any((stt.get("containers") or {}).values()):
        return
    presets = driver.query_presets(h)
    qname = st.text_input("QNAME", presets[2][0] if len(presets) > 2 else "host000000.zone00.test.", key="cache_q")
    qtype = st.text_input("QTYPE", "A", key="cache_t")
    if st.button("Flush + cold + warm ×3"):
        activity("step", "Cold/warm", qname, phase="cache", source="unbound", h=h)
        try:
            with st.spinner("Flushing…"):
                driver.flush(h)
            try:
                driver.unbound_stats(h)
            except Exception:  # noqa: BLE001
                pass
            cold = driver.probe(h, qname, qtype)
            try:
                cold_stats = driver.unbound_stats_delta({}, driver.unbound_stats(h))
            except Exception:  # noqa: BLE001
                cold_stats = {}
            warms, warm_stats = [], []
            for _ in range(3):
                try:
                    driver.unbound_stats(h)
                except Exception:  # noqa: BLE001
                    pass
                warms.append(driver.probe(h, qname, qtype))
                try:
                    warm_stats.append(driver.unbound_stats_delta({}, driver.unbound_stats(h)))
                except Exception:  # noqa: BLE001
                    warm_stats.append({})
            st.session_state.cache_result = {
                "cold": cold, "warms": warms, "cold_stats": cold_stats, "warm_stats": warm_stats,
            }
            sess = driver.load_session(h)
            sess["cold_warm"] = st.session_state.cache_result
            driver.save_session(h, sess)
            activity("ok", "Cold/warm done", "", phase="cache", source="unbound", h=h)
        except Exception as e:  # noqa: BLE001
            st.error(str(e)[:600])

    res = st.session_state.get("cache_result")
    if not res:
        return
    c, w = res["cold"], res["warms"]
    wmed = float(pd.Series([x["latency_ms"] for x in w]).median())
    a, b, d = st.columns(3)
    a.metric("Cold", f"{c['latency_ms']:.1f} ms")
    b.metric("Warm median", f"{wmed:.1f} ms")
    d.metric("AD", "YES" if c.get("ad") and all(x.get("ad") for x in w) else "mixed/no")
    rows = [{
        "pass": "cold", "ms": c["latency_ms"], "ad": c.get("ad"),
        "hits": (res.get("cold_stats") or {}).get("total.num.cachehits"),
        "miss": (res.get("cold_stats") or {}).get("total.num.cachemiss"),
    }]
    for i, x in enumerate(w, 1):
        ws = (res.get("warm_stats") or [{}])[i - 1] if i - 1 < len(res.get("warm_stats") or []) else {}
        rows.append({
            "pass": f"warm{i}", "ms": x["latency_ms"], "ad": x.get("ad"),
            "hits": ws.get("total.num.cachehits"), "miss": ws.get("total.num.cachemiss"),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def metrics_panel(h: driver.LabHandle) -> None:
    st.subheader("Metrics")
    if st.button("Clear session metrics"):
        driver.clear_demo_data(h, session=True, activity=False, pcaps=False, hide_prepare_metrics=True)
        clear_ui_demo_state()
        st.rerun()

    if prepare_metrics_visible(h) and h.prepared():
        rows = driver.manifest_rows(h)
        if rows:
            df = pd.DataFrame(rows)
            scenarios = sorted(df["scenario"].unique())
            sel = st.session_state.get("selected_scenario") or st.session_state.get("side_scenario")
            default = [sel] if sel in scenarios else scenarios
            pick = st.multiselect("Scenarios", scenarios, default=default)
            view = df[df["scenario"].isin(pick)] if pick else df
            wall = st.session_state.get("prepare_wall_s")
            if wall is None:
                wall = driver.load_session(h).get("prepare_wall_s")
            if wall:
                st.metric("Prepare wall", f"{wall:.1f}s")
            st.dataframe(
                view[["scenario", "tier", "origin", "sign_seconds", "verify_seconds",
                      "signed_bytes", "rrsig_count"]],
                use_container_width=True, hide_index=True,
            )
            agg = view.groupby("scenario", as_index=False).agg(
                sign_seconds=("sign_seconds", "sum"),
                verify_seconds=("verify_seconds", "sum"),
            )
            c1, c2 = st.columns(2)
            c1.plotly_chart(px.bar(agg, x="scenario", y="sign_seconds", title="Sign (s)"),
                            use_container_width=True)
            c2.plotly_chart(px.bar(agg, x="scenario", y="verify_seconds", title="Zone verify (s)"),
                            use_container_width=True)
    else:
        st.info("Sign/verify appear after **Prepare**.")

    st.markdown("**Session queries**")
    qlog = driver.session_queries(
        h, st.session_state.query_log if "query_log" in st.session_state else None,
    )
    if qlog:
        qdf = pd.DataFrame(qlog)
        cols = [c for c in ["qname", "qtype", "rcode", "ad", "latency_ms", "response_bytes",
                            "tcp_response_bytes", "transport"] if c in qdf.columns]
        st.dataframe(qdf[cols], use_container_width=True, hide_index=True)
    else:
        st.caption("No queries yet.")

    stats = st.session_state.get("last_stats") or {}
    if stats:
        st.json(stats)

    runs = driver.list_run_dirs(h)
    if runs:
        st.markdown("**Benchmark runs**")
        labels = [p.name for p in runs[:10]]
        pick = st.selectbox("Run folder", labels, key="metrics_run_pick")
        run_dir = next(p for p in runs if p.name == pick)
        st.caption(str(run_dir))
        for name in ("passes.csv", "summary.csv", "client.csv"):
            f = run_dir / name
            if f.is_file():
                st.markdown(f"`{name}`")
                try:
                    st.dataframe(pd.read_csv(f).head(50), use_container_width=True, hide_index=True)
                except Exception:  # noqa: BLE001
                    st.caption("Could not read CSV")
                break
        else:
            files = sorted(p.name for p in run_dir.iterdir() if p.is_file())[:20]
            st.caption("Files: " + ", ".join(files) if files else "Empty run dir")


def _pcap_options(h: driver.LabHandle) -> list[str]:
    captures = sorted(h.capture_dir.glob("*.pcap"), key=lambda p: p.stat().st_mtime, reverse=True)
    options = [str(p) for p in captures]
    last = st.session_state.get("last_pcap")
    if last and last not in options:
        options = [last] + options
    return options


def pcap_panel(h: driver.LabHandle) -> None:
    st.subheader("Packet story")
    st.caption(
        "Through Unbound → sniffer on Unbound. "
        "Direct to root/SLD → sniffer on client (referral / AA only)."
    )

    story_tab, diagram_tab = st.tabs(["Capture decode", "Sequence diagram"])
    options = _pcap_options(h)

    with story_tab:
        if not options:
            st.caption("Run a query with Capture enabled.")
        else:
            choice = st.selectbox("Capture", options, index=0, key="pcap_story_choice")
            hide_tcp = st.checkbox("Hide TCP noise", value=True, key="pcap_hide_tcp")
            if st.button("Decode", key="pcap_story_decode") or st.session_state.get("auto_decode"):
                st.session_state.auto_decode = False
                df = decode.decode_pcap(choice)
                if df.empty:
                    st.warning("Empty capture.")
                else:
                    wire = decode.wire_summary(df)
                    story = decode.build_story(df)
                    w = st.columns(4)
                    w[0].metric("Packets", wire["packets"])
                    w[1].metric("Client B", f"{wire['client_bytes']:,}")
                    w[2].metric("Upstream B", f"{wire['upstream_bytes']:,}")
                    w[3].metric("MTL-FULL", wire["mtl_full_packets"])
                    st.markdown("**Story**")
                    for line in story:
                        st.markdown(line)
                    show = df[df["phase"] != "tcp_noise"] if hide_tcp and "phase" in df.columns else df
                    cols = [c for c in ["#", "ms", "phase_label", "hop", "proto", "bytes", "summary", "ad", "rrsigs"]
                            if c in show.columns]
                    st.dataframe(show[cols], use_container_width=True, hide_index=True, height=360)

    with diagram_tab:
        if not options:
            st.caption("Run a query with Capture enabled, then decode here.")
        else:
            idx = 0
            pref = st.session_state.get("last_seq_pcap") or st.session_state.get("last_pcap")
            if pref in options:
                idx = options.index(pref)
            seq_choice = st.selectbox("Capture", options, index=idx, key="pcap_seq_choice")
            if st.button("Decode → sequence", key="pcap_seq_decode", type="primary"):
                df = decode.decode_pcap(seq_choice)
                if df.empty:
                    st.warning("Empty capture.")
                    st.session_state.pop("last_seq_steps", None)
                    st.session_state.pop("last_seq_pcap", None)
                else:
                    st.session_state.last_seq_steps = decode.build_sequence_steps(df)
                    st.session_state.last_seq_pcap = seq_choice
            steps = st.session_state.get("last_seq_steps")
            if steps:
                st.caption(f"Sequence for `{Path(st.session_state.get('last_seq_pcap') or seq_choice).name}`")
                show_sequence_diagram(steps)
            else:
                st.info("Pick a pcap and click **Decode → sequence**.")


def activity_tab(h: driver.LabHandle) -> None:
    st.subheader("Activity")
    top = st.columns([1, 2, 2])
    if top[0].button("Clear"):
        st.session_state.activity = []
        st.rerun()
    entries = st.session_state.get("activity") or []
    if not entries:
        st.caption("Empty.")
        return
    sources = sorted({e.get("source", "ui") for e in entries})
    phases = sorted({e.get("phase", "ui") for e in entries})
    filt_src = top[1].multiselect("Source", sources, default=sources)
    filt_phase = top[2].multiselect("Phase", phases, default=phases)
    filtered = [e for e in entries if e.get("source") in filt_src and e.get("phase") in filt_phase]
    blob = "\n\n".join(
        f"[{e['t']}] {e['level']} {e['title']}\n{e.get('detail', '')}" for e in filtered
    )
    st.download_button("Download", data=blob, file_name=f"activity-{h.tag}.txt", mime="text/plain")
    for e in filtered[:80]:
        icon = LEVEL_ICON.get(e["level"], "•")
        with st.container(border=True):
            st.markdown(f"{icon} **{e['t']}** · {e['title']}")
            if e.get("detail") and not e["detail"].startswith("```"):
                st.caption(e["detail"][:500])
            elif e.get("detail"):
                with st.expander("output"):
                    st.code(e["detail"].strip("`").replace("```", ""))


def main() -> None:
    st.title(APP_NAME)
    st.caption(APP_TAGLINE)
    h = _handle()
    stt = sidebar(h)

    lab, metrics, packets, logs = st.tabs(["Lab", "Metrics", "Packets", "Activity"])
    with lab:
        status_panel(h, stt)
        st.markdown("---")
        query_panel(h, stt)
        st.markdown("---")
        cache_panel(h, stt)
    with metrics:
        metrics_panel(h)
    with packets:
        pcap_panel(h)
    with logs:
        activity_tab(h)


if __name__ == "__main__":
    main()
