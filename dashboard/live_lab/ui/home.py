"""Home hub: the two ways of benchmarking as tracks, then the supporting tiles."""

from __future__ import annotations

import streamlit as st

from live_lab import compare_hub, driver, offline_job
from live_lab import suite as suite_mod
from live_lab.ui.common import (
    ROOT, TILES, TRACKS, _brand, _chip, _go, _hint, _sidebar_brand, readiness,
)

APP_NAME = "Cipherops · MTL DNSSEC Lab"
APP_TAGLINE = "Root → TLD → SLD · validating Unbound · Verisign MTL"

TRACK_TEXT_HEIGHT = 150  # px; title, status chip, purpose and the steps line
TILE_TEXT_HEIGHT = 110


def sidebar_home(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("home")
    st.sidebar.markdown("### Two ways to benchmark")
    st.sidebar.markdown(
        "**Offline benchmark** — how fast is each algorithm? One pinned core, no network.  \n"
        "**Simulated lab** — how does a real resolver behave? Real servers, packets and cache.  \n\n"
        "Each is a short numbered path: get its images, then run or explore."
    )
    st.sidebar.markdown("---")
    r = readiness(stt)
    st.sidebar.markdown("### Readiness")
    for ok, text in ((r["offline_images"], "Offline image"), (r["images"], "Simulated lab images"),
                     (r["prepared"], "Zones signed"), (r["running"], "Containers running")):
        st.sidebar.markdown(f"{'🟢' if ok else '⚪'}  {text}")
    st.sidebar.markdown("---")
    _hint("Open a track to see its settings here. Compare, Learn, Images and Reset are below the tracks.")
    if st.sidebar.button("Refresh status", width="stretch", key="sb_refresh_home"):
        st.rerun()


# ─────────────────────────── HOME ───────────────────────────

def _tile_chip(mode: str, stt: dict, h: driver.LabHandle) -> str:
    r = readiness(stt)
    if mode == "offline":
        if offline_job.is_running():
            return _chip("Running", "info")
        if not r["offline_images"]:
            return _chip("Needs its image", "warn")
        n = len(compare_hub.offline_catalogue(ROOT / "results"))
        return _chip(f"Ready · {n} run(s)" if n else "Ready", "ok")
    if mode == "simulated":
        if suite_mod.running_suite():
            return _chip("Running", "info")
        if not r["images"]:
            return _chip("Needs images", "warn")
        return _chip("Lab running", "ok") if r["running"] else _chip("Ready", "ok")
    if mode == "setup":
        if r["offline_images"] and r["images"]:
            return _chip("Both ready", "ok")
        if r["offline_images"] or r["images"]:
            return _chip("One ready", "info")
        return _chip("None yet", "warn")
    if mode == "compare":
        has = bool(compare_hub.offline_catalogue(ROOT / "results")) or bool(driver.list_labs())
        return _chip("Has data", "ok") if has else _chip("No data yet", "idle")
    if mode == "learn":
        return _chip("No setup needed", "ok")
    return _chip("Maintenance", "idle")


def _steps_line(track: str) -> str:
    return " → ".join(label for _key, label in TRACKS[track]["steps"])


def _track_card(mode: str, stt: dict, h: driver.LabHandle) -> None:
    tile = TILES[mode]
    with st.container(border=True):
        with st.container(height=TRACK_TEXT_HEIGHT, border=False):
            st.markdown(f"### {tile['icon']} {tile['title']}  {_tile_chip(mode, stt, h)}", unsafe_allow_html=True)
            st.caption(tile["pitch"])
            st.markdown(f"**{_steps_line(mode)}**")
        st.button(f"{tile['cta']} →", key=f"open_{mode}", on_click=_go, args=(mode,), type="primary",
                  width="stretch")


def _tile_card(mode: str, stt: dict, h: driver.LabHandle) -> None:
    tile = TILES[mode]
    with st.container(border=True):
        with st.container(height=TILE_TEXT_HEIGHT, border=False):
            st.markdown(f"#### {tile['icon']} {tile['title']}  {_tile_chip(mode, stt, h)}", unsafe_allow_html=True)
            st.caption(tile["pitch"])
        st.button(f"{tile['cta']} →", key=f"open_{mode}", on_click=_go, args=(mode,), width="stretch")


def home_page(h: driver.LabHandle, stt: dict) -> None:
    _symbol, full = _brand()
    if full is not None:
        logo_col, title_col = st.columns([1, 4], vertical_alignment="center")
        logo_col.image(full, width=190)
    else:
        title_col = st.container()
    title_col.markdown(
        f'<div class="co-hero"><h1>{APP_NAME}</h1><p class="co-sub">{APP_TAGLINE}</p></div>',
        unsafe_allow_html=True,
    )
    if stt.get("error"):
        st.error(f"Could not read lab status: {stt['error']}")

    st.markdown("#### What do you want to do?")
    left, right = st.columns(2)
    with left:
        _track_card("offline", stt, h)
    with right:
        _track_card("simulated", stt, h)

    st.markdown("#### Also")
    for col, mode in zip(st.columns(4), ("compare", "learn", "setup", "reset")):
        with col:
            _tile_card(mode, stt, h)
