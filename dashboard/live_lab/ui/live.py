"""Live tile: query, dig and cold/warm cache panels, and the packet views."""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import streamlit as st

from live_lab import decode, driver
from live_lab import glossary as gl
from live_lab import suite as suite_mod
from live_lab.ui.activity import _activity_expander
from live_lab.ui.common import (
    _PCAP_KEYS, _gate, _hint, _log_chunks, _page_header, _sidebar_brand, _step_label, _store, activity,
    cfg, readiness, sb,
)

SCENARIO_BLURB = {
    "rsa": "RSA-2048 with SHA-256",
    "ecdsa": "ECDSA P-256",
    "ed25519": "Ed25519",
    "slh-dsa": "Post-quantum SLH-DSA (large signatures)",
    "mtl": "SLH-DSA with MTL condensed signatures",
    "mixed-mtl": "ECDSA at root/TLD, MTL at the leaf zone",
}


def sidebar_live(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("live")
    r = readiness(stt)
    st.sidebar.markdown("### Live")

    st.sidebar.markdown(_step_label(1, "Algorithm", "done" if r["running"] and r["active"] else "none"))
    _hint("Which signed mode the live servers use right now.")
    scenarios: list[str] = []
    try:
        if h.prepared():
            scenarios = h.scenarios()
    except Exception:  # noqa: BLE001
        scenarios = []
    active = r["active"]
    if scenarios:
        default = active if active in scenarios else scenarios[0]
        scenario = sb(st.sidebar.selectbox, "live_scenario", default, "Active algorithm", options=scenarios,
                        help="Which signed zone set the live servers use right now. " + gl.tip("algorithm"))
        st.session_state.selected_scenario = scenario
        _hint(SCENARIO_BLURB.get(scenario, ""))
        if r["running"] and scenario and scenario != active and not suite_mod.running_suite():
            udp = int(cfg("udp", 1232))
            activity("step", f"Activate {scenario}", "", phase="activate", source="compose", h=h)
            st.toast(f"Switching the lab to {scenario}: loading its zones and restarting the root, TLD and leaf "
                     "servers and the resolver. This takes a few seconds.", icon="⏳")
            with st.spinner(f"Switching to {scenario}… (reloading root, TLD, leaf and resolver)"):
                try:
                    msg = driver.activate(h, scenario, udp=udp)
                    st.session_state.query_log = []
                    for k in ("last_query", "last_stats", "pcap_story_choice", "pcap_seq_choice", *_PCAP_KEYS):  # they describe the previous scenario
                        st.session_state.pop(k, None)
                    _log_chunks(msg, phase="activate", source="compose", title="activate")
                    activity("ok", f"{scenario} live", "", phase="activate", source="compose", h=h)
                    st.toast(f"{scenario} is live. Run a query to see its answer.", icon="✅")
                except Exception as e:  # noqa: BLE001
                    st.sidebar.error(str(e)[:600])
                    activity("err", "Activate failed", str(e)[:2000], phase="activate", source="compose", h=h)
            st.rerun()
        elif not r["running"] and scenario != active:
            _hint("Start the lab in step 2 to apply this choice.")
    else:
        _hint("Sign the zones in step 2 first.")

    done_q = bool(st.session_state.get("query_log") or st.session_state.get("last_query"))
    st.sidebar.markdown(_step_label(2, "Query", "done" if done_q else "none"))
    _hint("Use the Query tab to send a lookup; Results shows packets.")

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Resolver cache")
    if r["running"]:
        _hint("Empties the resolver cache. The next query is a cold-cache query. Takes a few seconds.")
    else:
        _hint("Start the lab in step 2 before flushing.")
    if st.sidebar.button("Flush cache", key="btn_flush_cache", width="stretch", disabled=not r["running"],
                         help=gl.tip("cold_cache")):
        activity("step", "Flush cache", "", phase="cache", source="unbound", h=h)
        with st.spinner("Flushing the resolver…"):
            try:
                driver.flush(h)
                activity("ok", "Resolver cache empty", "The next lookup is cold.", phase="cache", source="unbound", h=h)
                st.sidebar.success("Cache empty. The next lookup is cold.")
            except Exception as e:  # noqa: BLE001
                st.sidebar.error(str(e)[:600])
                activity("err", "Flush failed", str(e)[:2000], phase="cache", source="unbound", h=h)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Capture name")
    label = sb(
        st.sidebar.text_input, "capture_label", "", "Name (optional)",
        placeholder="e.g. mtl-cold-lookup", max_chars=40,
        help="Added to the capture file name, next to the date and time.",
    )
    tag = driver.sanitize_label(label)
    _hint(f"File: live-{tag + '-' if tag else ''}<date>-<time>.pcap")
    st.sidebar.markdown("---")
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_live"):
        st.rerun()


# ─────────────────────────── LIVE ───────────────────────────

def show_sequence_diagram(steps: list) -> None:
    """Interactive sequence (Plotly hover) plus optional PNG download."""
    if not steps:
        st.warning("Could not build a sequence from this capture’s DNS hops.")
        return

    summary = decode.sequence_summary(steps)
    m = st.columns(5)
    m[0].metric("Messages", summary["messages"])
    m[1].metric("Client B", f"{summary['client_bytes']:,}")
    m[2].metric("Max answer", decode.format_bytes(summary["max_bytes"]),
                help="Largest DNS message in this diagram (reassembled length, not one TCP segment).")
    m[3].metric("Span", f"{summary['span_ms']} ms",
                help="Last drawn hop time minus first — pcap timeline width, not Queries latency_ms.")
    m[4].metric("MTL-FULL", summary["mtl_full"])
    st.caption(
        "Solid = query · dashed = response · amber = MTL-FULL. "
        "Each label sits on the **sender**. Arrows are in capture time order: a large TCP "
        "answer can finish after the next query has already gone out, so a late Root arrow "
        "is still Root’s answer (often a negative proof), not the SLD’s. "
        "Hover an arrow for exact bytes, t, and packet number."
    )

    try:
        st.plotly_chart(
            decode.sequence_plotly_figure(steps),
            width="stretch",
            theme=None,  # keep dark ink; Streamlit theme was painting legend/hover white-on-white
            config={"displayModeBar": False},
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"Diagram failed: {e}")
        return

    table_rows = []
    for i, s in enumerate(steps, 1):
        delta = s.get("delta_ms")
        table_rows.append({
            "#": i,
            "Message": s.get("label") or "",
            "Hop": f"{s.get('src')} → {s.get('dst')}",
            "Bytes": int(s.get("bytes") or 0),
            "Size": decode.format_bytes(s.get("bytes")),
            "t (ms)": s.get("ms"),
            "Δ (ms)": "—" if delta is None else f"{float(delta):g}",
            "Phase": s.get("phase_label") or s.get("phase") or "",
        })
    st.dataframe(pd.DataFrame(table_rows), width="stretch", hide_index=True,
                 height=min(360, 80 + 28 * len(table_rows)))

    try:
        png = decode.sequence_png_bytes(steps)
        st.download_button("Download PNG", data=png, file_name="cipherops-sequence.png",
                           mime="image/png", key="seq_png_dl")
    except Exception:  # noqa: BLE001
        pass


def query_panel(h: driver.LabHandle, stt: dict) -> None:
    st.caption("Send one DNS lookup. Enable Capture to inspect packets afterward.")
    if not _gate(stt, "Querying"):
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
    path = st.radio("Path", ["Through Unbound", "Direct to root", "Direct to SLD"], horizontal=True)
    server = {
        "Through Unbound": "10.53.0.53",
        "Direct to root": "10.53.0.2",
        "Direct to SLD": driver.sld_server_ip(h),
    }[path]
    recurse = path.startswith("Through")
    if path.startswith("Direct to SLD") and (
        qname.strip() in (".", "") or (qtype.upper() == "DNSKEY" and qname.rstrip(".").count(".") == 0)
    ):
        st.warning("Direct-to-leaf works for host names, not for root DNSKEY queries.")

    c1, c2, c3 = st.columns(3)
    dnssec = c1.checkbox("DO (DNSSEC RRs)", value=True)
    mtl_full = c2.checkbox("MTL-FULL", value=False)
    capture = c3.checkbox("Capture", value=True)
    if capture:
        sniffer = "unbound (full chain)" if recurse else "client (direct to auth — no Unbound hops)"
        st.caption(f"Recording packets on **{sniffer}**")

    if st.button("Run query", type="primary"):
        pcap_path = None
        activity("step", f"Query {qname} {qtype}", path, phase="probe", source="probe", h=h)
        t_start = time.perf_counter()
        # Visible progress next to the button: each step is written as it starts, with how long the last one took.
        box = st.status(f"Running `{qname} {qtype}` · {path}", expanded=True)
        step_t: list = [None]

        def step(text: str | None) -> None:
            """Close the previous step with its duration, then announce the next one (None just closes)."""
            now = time.perf_counter()
            if step_t[0] is not None:
                box.write(f"   ↳ {now - step_t[0]:.1f} s")
            step_t[0] = now if text else None
            if text:
                box.write(text)
                box.update(label=text)

        try:
            if recurse:
                try:
                    step("1 · Reading the resolver's counters before the lookup")
                    driver.unbound_stats(h)
                except Exception:  # noqa: BLE001
                    pass
            if capture:
                step(f"2 · Starting packet capture on {'Unbound' if recurse else 'the client'}")
                pcap_path = driver.start_capture(h, server=server, label=cfg("capture_label", ""))
            step(f"3 · Sending the query to {server}" + (" (Unbound resolves it from the root down)" if recurse else ""))
            row = driver.probe(
                h, qname, qtype, server=server, mtl_full=mtl_full, dnssec=dnssec, recurse=recurse,
            )
            box.write(f"   answered in {row.get('latency_ms', 0):.1f} ms · {row.get('rcode')}"
                      f" · AD={'yes' if row.get('ad') else 'no'}")
            row.update(scenario=h.active_scenario(), path=path, at=time.strftime("%H:%M:%S"))
            if recurse:
                try:
                    step("4 · Reading the resolver's counters after the lookup")
                    row["unbound_stats"] = driver.unbound_stats_delta({}, driver.unbound_stats(h))
                except Exception:  # noqa: BLE001
                    row["unbound_stats"] = {}
            dropped = 0
            if capture:
                step("5 · Stopping the capture and decoding the packets")
                pcap_path, dropped = driver.stop_capture(h)
                if pcap_path and Path(pcap_path).exists():
                    try:
                        df = decode.decode_pcap(pcap_path)
                        st.session_state.last_story = decode.build_story(df)
                        st.session_state.last_wire = decode.wire_summary(df)
                        box.write(f"   {len(df)} packets captured")
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
            if pcap_path:
                st.session_state.pcap_focus = str(pcap_path)  # Results opens on this query's capture
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
            step(None)
            box.update(label=f"Done in {time.perf_counter() - t_start:.1f} s · {row.get('rcode')} · "
                             f"AD={'yes' if row.get('ad') else 'no'} · {size:,} B", state="complete", expanded=False)
        except Exception as e:  # noqa: BLE001
            box.update(label="Query failed", state="error", expanded=True)
            st.error(str(e)[:800])
            activity("err", "Query failed", str(e)[:2000], phase="probe", source="probe", h=h)
            if capture:
                try:
                    driver.stop_capture(h)
                except Exception:  # noqa: BLE001
                    pass

    row = st.session_state.get("last_query")
    if row:
        shown = row.get("scenario")
        st.markdown(f"**Last result** · `{row.get('qname')} {row.get('qtype')}` · {row.get('path', '')} · "
                    f"algorithm **{shown or '?'}** · {row.get('at', '')}")
        now = h.active_scenario()
        if shown and now and shown != now:
            st.warning(f"This result was taken on **{shown}**; the lab is now on **{now}**. Run the query again.")
        elif (row.get("qname"), row.get("qtype")) != (qname, qtype) or row.get("path") != path:
            st.caption("The query above has changed since this result. Press Run query to refresh it.")
        m = st.columns(5)
        m[0].metric("RCODE", row.get("rcode", "—"))
        m[1].metric("AD", "YES" if row.get("ad") else "no")
        m[2].metric("Transport", row.get("transport", "—"))
        m[3].metric("Bytes", f"{(row.get('tcp_response_bytes') or row.get('response_bytes') or 0):,}")
        m[4].metric("Latency", f"{row.get('latency_ms', 0):.1f} ms")
        st.caption("Full history, packet decode and sequence diagram are in the Results tab.")


def dig_panel(h: driver.LabHandle, stt: dict) -> None:
    st.caption("Run dig from the client container. Without an @server it asks Unbound (10.53.0.53).")
    if not _gate(stt, "dig"):
        return

    presets = driver.dig_presets(h)
    if "dig_cmd" not in st.session_state:
        st.session_state.dig_cmd = presets[0]
    st.selectbox(
        "Examples", presets, key="dig_preset",
        on_change=lambda: st.session_state.update(dig_cmd=st.session_state.dig_preset),
    )
    with st.form("dig_form", border=False):
        line = st.text_input("$", key="dig_cmd")
        c1, c2 = st.columns([1, 3])
        capture = c2.checkbox("Capture packets (shows up in Results)", value=True, key="dig_capture")
        run = c1.form_submit_button("Run dig", type="primary")

    if run:
        pcap_path = None
        try:
            argv = driver.dig_argv(line)
        except ValueError as e:
            st.error(str(e))
        else:
            activity("step", f"dig {line}", phase="probe", source="dig", h=h)
            try:
                if capture:
                    server = next(p[1:] for p in argv if p.startswith("@"))
                    pcap_path = driver.start_capture(h, server=server, label=cfg("capture_label", ""))
                res = driver.dig(h, line)
                if capture:
                    pcap_path, _ = driver.stop_capture(h)
                    st.session_state.last_pcap = str(pcap_path) if pcap_path else None
                    if pcap_path:
                        st.session_state.pcap_focus = str(pcap_path)
                st.session_state.setdefault("dig_log", []).insert(0, res)
                activity("ok" if res["exit"] == 0 else "err", res["command"],
                         res["output"][:2000], phase="probe", source="dig", h=h)
            except Exception as e:  # noqa: BLE001
                st.error(str(e)[:800])
                activity("err", "dig failed", str(e)[:2000], phase="probe", source="dig", h=h)
                if capture:
                    try:
                        driver.stop_capture(h)
                    except Exception:  # noqa: BLE001
                        pass

    log = st.session_state.get("dig_log", [])
    if log:
        st.code("\n\n".join(f"$ {r['command']}\n{r['output']}" for r in log[:10]), language="text")
        if st.button("Clear terminal"):
            st.session_state.dig_log = []
            st.rerun()
    else:
        st.caption("No output yet.")


def cache_panel(h: driver.LabHandle, stt: dict) -> None:
    st.caption("Compare a first query (cold cache) with repeats (warm cache). Flush cache in the sidebar empties the resolver on its own.")
    if not _gate(stt, "Cold vs warm cache"):
        return
    presets = driver.query_presets(h)
    qname = st.text_input("QNAME", presets[2][0] if len(presets) > 2 else "host000000.zone00.test.", key="cache_q")
    qtype = st.text_input("QTYPE", "A", key="cache_t")
    if st.button("Run cold then warm ×3"):
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
    _cache_result_view()


def _cache_result_view() -> None:
    res = st.session_state.get("cache_result")
    if not res:
        return
    c, w = res["cold"], res["warms"]
    wmed = float(pd.Series([x["latency_ms"] for x in w]).median())
    a, b, d = st.columns(3)
    a.metric("Cold-cache latency", f"{c['latency_ms']:.1f} ms", help=gl.tip("cold_cache"))
    b.metric("Warm-cache latency p50", f"{wmed:.1f} ms", help=gl.tip("warm_cache"))
    d.metric("Validated (AD)", "YES" if c.get("ad") and all(x.get("ad") for x in w) else "mixed/no",
             help="AD (Authenticated Data) is set when the resolver validated the DNSSEC signatures of the answer.")
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
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _pcap_options(h: driver.LabHandle) -> list[str]:
    captures = driver.list_pcaps(h)
    options = [str(p) for p in captures]
    last = st.session_state.get("last_pcap")
    if last and last not in options and Path(last).exists():
        options = [last] + options
    return options


def _pcap_folder_banner(h: driver.LabHandle, options: list[str]) -> None:
    """Make the active results folder obvious; offer a one-click jump if captures sit elsewhere."""
    st.caption(
        f"Captures for lab **`{h.tag}`** · folder "
        f"`results/{h.tag}/live-dashboard` · **{len(options)}** file(s). "
        f"Image version **{h.stack}** · zone **{cfg('zone_size', 'small')}**."
    )
    inv = driver.live_capture_inventory()
    others = [r for r in inv if r["tag"] != h.tag]
    if not others:
        return
    with st.expander(
        f"Other labs still have captures ({sum(r['count'] for r in others)} file(s)) — "
        "not deleted; this page only lists the active lab",
        expanded=not options,
    ):
        for r in others[:8]:
            label = f"**{r['tag']}** · {r['count']} pcap(s) · newest `{r['names'][0] if r['names'] else '?'}`"
            c1, c2 = st.columns([4, 1])
            c1.markdown(label)
            can_switch = bool(r.get("stack") and r.get("zone_size"))
            if can_switch and c2.button("Open", key=f"open_lab_{r['tag']}"):
                _store()["stack"] = r["stack"]
                _store()["zone_size"] = r["zone_size"]
                st.session_state.w_stack = r["stack"]
                st.session_state.w_zone_size = r["zone_size"]
                st.session_state.pop("lab_handle", None)
                for k in _PCAP_KEYS:
                    st.session_state.pop(k, None)
                st.rerun()


def _pcap_name(p: str) -> str:
    return Path(p).name


@st.cache_data(show_spinner=False, max_entries=16)
def _decoded(path: str, mtime_ns: int) -> pd.DataFrame:
    """Decode a capture once per file version; reruns (tab clicks, polling) reuse the result."""
    return decode.decode_pcap(path)


@st.cache_data(show_spinner=False, max_entries=16)
def _sequence(path: str, mtime_ns: int) -> list:
    df = _decoded(path, mtime_ns)
    return decode.build_sequence_steps(df) if not df.empty else []


def _mtime_ns(path: str) -> int:
    try:
        return Path(path).stat().st_mtime_ns
    except OSError:
        return 0


def _sync_pcap_choices(options: list[str]) -> None:
    """Point both capture pickers at the capture of the query just run; otherwise keep the user's pick;
    otherwise (nothing picked, or the picked file is gone) the newest capture."""
    focus = st.session_state.pop("pcap_focus", None)
    for key in ("pcap_story_choice", "pcap_seq_choice"):
        if focus in options:
            st.session_state[key] = focus
        elif st.session_state.get(key) not in options and options:
            st.session_state[key] = options[0]


def _query_for_pcap(pcap: str) -> dict | None:
    for r in reversed(st.session_state.get("query_log") or []):
        if r.get("pcap") == pcap:
            return r
    return None


def _capture_context(pcap: str) -> str:
    q = _query_for_pcap(pcap)
    if not q:
        return f"`{Path(pcap).name}` (a dig or earlier capture; no saved query is linked to it)"
    return (f"`{q.get('qname')} {q.get('qtype')}` · {q.get('path', '')} · scenario **{q.get('scenario') or '?'}** · "
            f"{q.get('at', '')} · {q.get('rcode', '')}")


def pcap_decode_view(h: driver.LabHandle, options: list[str]) -> None:
    if not options:
        st.caption("No captures yet — run a Live query with Capture on.")
        return
    choice = st.selectbox("Capture", options, key="pcap_story_choice", format_func=_pcap_name)
    st.caption(_capture_context(choice))
    hide_tcp = st.checkbox("Hide TCP handshake noise", value=True, key="pcap_hide_tcp")
    df = _decoded(choice, _mtime_ns(choice))
    if df.empty:
        st.warning("This capture has no DNS packets.")
        return
    wire = decode.wire_summary(df)
    story = decode.build_story(df)
    w = st.columns(4)
    w[0].metric("Packets", wire["packets"])
    w[1].metric("Client B", f"{wire['client_bytes']:,}")
    w[2].metric("Upstream B", f"{wire['upstream_bytes']:,}")
    w[3].metric("MTL-FULL", wire["mtl_full_packets"])
    st.markdown("**What happened**")
    for line in story:
        st.markdown(line)
    show = df[df["phase"] != "tcp_noise"] if hide_tcp and "phase" in df.columns else df
    cols = [c for c in ["#", "ms", "phase_label", "hop", "proto", "bytes", "dns_bytes",
                        "summary", "ad", "rrsigs"] if c in show.columns]
    st.caption(
        "**bytes** = this IP/UDP/TCP payload on the wire · "
        "**dns_bytes** = DNS message length (TCP length prefix / UDP DNS size). "
        "Large MTL answers often span several TCP segments — do not read Max answer from a single **bytes** cell."
    )
    # Force horizontal scroll: Streamlit squeezes wide text columns unless widths are set.
    cfg = {
        "summary": st.column_config.TextColumn("summary", width="large"),
        "rrsigs": st.column_config.TextColumn("rrsigs", width="medium"),
        "phase_label": st.column_config.TextColumn("phase_label", width="medium"),
        "hop": st.column_config.TextColumn("hop", width="medium"),
        "bytes": st.column_config.NumberColumn("bytes", help="Wire payload in this packet/segment"),
        "dns_bytes": st.column_config.NumberColumn(
            "dns_bytes", help="Reassembled DNS message length when known"),
    }
    st.dataframe(
        show[cols], width="stretch", hide_index=True, height=420,
        column_config={k: v for k, v in cfg.items() if k in cols},
    )


def pcap_sequence_view(h: driver.LabHandle, options: list[str]) -> None:
    if not options:
        st.caption("No captures yet — run a Live query with Capture on.")
        return
    seq_choice = st.selectbox("Capture", options, key="pcap_seq_choice", format_func=_pcap_name)
    st.caption(_capture_context(seq_choice))
    steps = _sequence(seq_choice, _mtime_ns(seq_choice))
    if not steps:
        if _decoded(seq_choice, _mtime_ns(seq_choice)).empty:
            st.warning("This capture has no DNS packets.")
        else:
            st.warning("Decoded the capture, but no Client/Unbound/Root/TLD/SLD hops "
                       "were recognized for a sequence diagram.")
        return
    st.session_state.last_seq_steps, st.session_state.last_seq_pcap = steps, seq_choice
    st.caption(f"{len(steps)} messages from this capture")
    show_sequence_diagram(steps)


def live_results(h: driver.LabHandle) -> None:
    queries, packets, sequence = st.tabs(["Queries", "Packet decode", "Sequence diagram"])
    options = _pcap_options(h)
    _sync_pcap_choices(options)
    _pcap_folder_banner(h, options)
    # Re-hydrate query log from disk after refresh (session_state is empty; session.json is not).
    if "query_log" not in st.session_state:
        st.session_state.query_log = list(driver.load_session(h).get("queries") or [])
    with queries:
        qlog = driver.session_queries(h, st.session_state.get("query_log"))
        if qlog and not st.session_state.get("query_log"):
            st.session_state.query_log = list(qlog)
        if qlog:
            cur = st.session_state.get("last_query") or qlog[-1]
            st.markdown(f"**Current query** · `{cur.get('qname')} {cur.get('qtype')}` · {cur.get('path', '')} · "
                        f"scenario **{cur.get('scenario') or '?'}** · {cur.get('rcode', '')} · "
                        f"AD={'yes' if cur.get('ad') else 'no'} · {cur.get('at', '')}")
            qdf = pd.DataFrame(qlog).iloc[::-1]  # newest first
            cols = [c for c in ["at", "scenario", "path", "qname", "qtype", "rcode", "ad", "latency_ms",
                                "response_bytes", "tcp_response_bytes", "transport"] if c in qdf.columns]
            st.dataframe(qdf[cols], width="stretch", hide_index=True)
        else:
            st.caption("No queries yet — send one from the Query tab.")
        row = st.session_state.get("last_query") or {}
        if row.get("rrsigs"):
            st.markdown("**Signatures in the last answer**")
            st.dataframe(pd.DataFrame(row["rrsigs"]), width="stretch", hide_index=True)
        stats = st.session_state.get("last_stats") or {}
        if stats:
            with st.expander("Resolver counters for the last query"):
                s = st.columns(4)
                s[0].metric("Cache hits", int(stats.get("total.num.cachehits") or 0))
                s[1].metric("Cache miss", int(stats.get("total.num.cachemiss") or 0))
                s[2].metric("Secure", int(stats.get("total.num.answer.secure") or 0))
                s[3].metric("Recursion s", f"{stats.get('total.recursion.time.avg') or 0:.4f}")
                st.json(stats)
        if st.session_state.get("cache_result"):
            st.markdown("**Cold vs warm**")
            _cache_result_view()
    with packets:
        pcap_decode_view(h, options)
    with sequence:
        pcap_sequence_view(h, options)


def live_body(h: driver.LabHandle, stt: dict) -> None:
    """Step 3 of the simulated lab: query the running lab and inspect the packets."""
    query, dig_tab, cache, results = st.tabs(["Query", "dig terminal", "Cold vs warm cache", "Results"])
    with query:
        query_panel(h, stt)
    with dig_tab:
        dig_panel(h, stt)
    with cache:
        cache_panel(h, stt)
    with results:
        live_results(h)
