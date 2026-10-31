"""Reset tile: clear saved runs, signed zones, captures and logs."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from live_lab import decode, driver
from live_lab import suite as suite_mod
from live_lab.ui.activity import _activity_expander
from live_lab.ui.common import (
    _PCAP_KEYS, _handle, _hint, _page_header, _sidebar_brand, activity, readiness,
)

_SESSION_KEYS = ("last_query", "query_log", "last_stats", "cache_result", "prepare_wall_s",
                 "prepare_summary", "last_log", "replay_fresh")


def sidebar_reset(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("reset")
    st.sidebar.markdown("### Reset")
    _hint("Pick what to clear in the main area. Nothing is removed until you press the button.")
    st.sidebar.markdown("**Always kept**")
    st.sidebar.markdown("- Docker images  \n- Configuration files  \n- Repository code")
    st.sidebar.markdown("---")
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_reset"):
        st.rerun()


# ─────────────────────────── RESET ───────────────────────────

def _fmt_size(n: int) -> str:
    return decode.format_bytes(n) if n else "0 B"


def _do_reset() -> None:
    """Callback for the Reset button: runs before the page redraws, so the preview is fresh."""
    ss = st.session_state
    h = _handle()
    scopes = {k: bool(ss.get(f"rs_{k}")) for k in
              ("session", "activity", "pcaps", "runs", "suites", "flush", "work")}
    if not any(scopes.values()):
        return
    removed = driver.clear_demo_data(
        h, session=scopes["session"], activity=scopes["activity"], pcaps=scopes["pcaps"],
        hide_prepare_metrics=True, work=scopes["work"], benchmarks=scopes["runs"], suites=scopes["suites"],
    )
    flushed = False
    flush_note = ""
    if scopes["flush"] and not scopes["work"]:
        try:
            if any((driver.status(h).get("containers") or {}).values()):
                driver.flush(h)
                flushed = True
            else:
                flush_note = "Resolver cache not flushed — the lab is not running."
        except Exception as e:  # noqa: BLE001
            flush_note = f"Resolver flush failed: {str(e)[:200]}"
    if scopes["session"] or scopes["work"]:
        for k in _SESSION_KEYS:
            ss.pop(k, None)
        ss.show_prepare_metrics = False
        ss.pop("selected_scenario", None)
    if scopes["activity"]:
        ss.activity = []
    if scopes["pcaps"]:
        for k in _PCAP_KEYS:
            ss.pop(k, None)
    if scopes["runs"] or scopes["work"] or scopes["suites"]:
        for k in ("last_run_dir", "bench_run_pick", "bench_flash", "bench_suite_pick"):
            ss.pop(k, None)
    names = {"session": "dashboard session", "activity": "activity log", "pcaps": "packet captures",
             "runs": "saved lab runs", "suites": "benchmarks of all dataset sizes", "flush": "resolver cache", "work": "signed zones and lab work"}
    ss.last_reset = {
        "when": datetime.now().strftime("%d %b %Y, %H:%M:%S"),
        "scopes": [names[k] for k, v in scopes.items() if v],
        "removed": removed,
        "flushed": flushed,
        "note": flush_note,
        "fresh": True,
    }
    activity("ok", "Reset", ", ".join(ss.last_reset["scopes"]), phase="ui", source="ui", h=h)
    # Back to the cautious defaults so a second click never repeats a destructive reset by accident.
    for k in ("pcaps", "runs", "flush", "work"):
        ss[f"rs_{k}"] = False
    ss.rs_confirm = False


def _scope_row(key: str, title: str, detail: str, *, default: bool, disabled: bool = False) -> bool:
    c1, c2 = st.columns([1, 3])
    v = c1.checkbox(title, value=default, key=f"rs_{key}", disabled=disabled)
    c2.caption(detail)
    return bool(v)


def reset_controls(h: driver.LabHandle, stt: dict) -> None:
    pv = driver.reset_preview(h)
    r = readiness(stt)
    last = st.session_state.get("last_reset")
    if last and last.pop("fresh", False):
        st.success(f"Reset complete · {last['when']} — cleared: {', '.join(last['scopes'])}.")
        if last.get("note"):
            st.warning(last["note"])

    with st.container(border=True):
        st.markdown("**Display and logs** · safe, nothing to rebuild")
        _scope_row("session", "Session view",
                   f"Recent queries ({pv['session_queries']}), cold/warm result, and the signing timings view.",
                   default=True)
        _scope_row("activity", "Activity log", f"Log file {_fmt_size(pv['activity_bytes'])} and the in-app feed.",
                   default=True)
    with st.container(border=True):
        st.markdown("**Evidence** · you can regenerate it, but not recover it")
        _scope_row("pcaps", "Live captures",
                   f"{pv['pcaps']} file(s) · {_fmt_size(pv['pcap_bytes'])} from Simulated lab → Query.", default=False)
        _scope_row("runs", "Saved lab runs (older)",
                   f"{pv['runs']} run(s) · {_fmt_size(pv['run_bytes'])} for lab {h.tag}.", default=False)
        _scope_row("suites", "Benchmarks (dataset sizes)",
                   f"{pv['suites']} benchmark(s) · {_fmt_size(pv['suite_bytes'])} with their signed zones and runs."
                   if not suite_mod.running_suite() else "A benchmark is running; wait or cancel it first.",
                   default=False, disabled=bool(suite_mod.running_suite()) or not pv["suite_bytes"])
    with st.container(border=True):
        st.markdown("**Lab environment**")
        _scope_row("flush", "Flush resolver cache",
                   "Empties Unbound's cache so the next lookup is cold." if r["running"]
                   else "Lab is not running, so there is nothing to flush.",
                   default=False, disabled=not r["running"])
        _scope_row("work", "Signed zones and lab work",
                   f"Stops the lab and deletes {_fmt_size(pv['work_bytes'])}. Sign the zones again afterwards."
                   if pv["work_exists"] else "Nothing signed yet.",
                   default=False, disabled=not pv["work_exists"])
    st.caption("Always kept: Docker images, configuration files and repository code.")

    ss = st.session_state
    chosen = [k for k in ("session", "activity", "pcaps", "runs", "suites", "flush", "work") if ss.get(f"rs_{k}")]
    destructive = [k for k in ("pcaps", "runs", "suites", "work") if ss.get(f"rs_{k}")]
    if destructive:
        wording = {"pcaps": f"{pv['pcaps']} capture file(s)", "runs": f"{pv['runs']} saved lab run(s)",
                   "suites": f"{pv['suites']} benchmark(s) of all dataset sizes",
                   "work": "the signed zones (the lab will stop)"}
        st.warning("This permanently deletes " + ", ".join(wording[k] for k in destructive) + ".")
        confirmed = st.checkbox("I understand this cannot be undone", key="rs_confirm")
    else:
        confirmed = True
    bench_blocks = bool(suite_mod.running_suite()) and any(k in chosen for k in ("runs", "suites", "work", "flush"))
    if bench_blocks:
        st.warning("A benchmark is running. Lab, run and benchmark resets are paused until it finishes or is cancelled.")
    st.button(
        f"Reset {len(chosen)} item(s)" if chosen else "Select something to reset",
        type="primary", disabled=not chosen or not confirmed or bench_blocks,
        on_click=_do_reset, key="rs_go",
    )


def reset_last() -> None:
    last = st.session_state.get("last_reset")
    if not last:
        st.info("Nothing has been reset in this session yet.")
        return
    st.markdown(f"**{last['when']}** — cleared: {', '.join(last['scopes'])}")
    rm = last["removed"]
    m = st.columns(5)
    m[0].metric("Session", "cleared" if rm.get("session") else "—")
    m[1].metric("Activity log", "cleared" if rm.get("activity") else "—")
    m[2].metric("Captures removed", rm.get("pcaps", 0))
    m[3].metric("Runs removed", rm.get("benchmarks", 0) + rm.get("suites", 0))
    m[4].metric("Zones removed", "yes" if rm.get("work") else "no")
    if last.get("flushed"):
        st.caption("Resolver cache flushed.")
    if last.get("note"):
        st.warning(last["note"])


def reset_page(h: driver.LabHandle, stt: dict) -> None:
    _page_header("reset", h, stt)
    controls, last = st.tabs(["Reset", "Results"])
    with controls:
        reset_controls(h, stt)
    with last:
        reset_last()
    _activity_expander(h)
