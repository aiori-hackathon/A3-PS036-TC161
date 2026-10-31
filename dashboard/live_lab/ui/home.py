"""Home: this node's prerequisites, the two ways of benchmarking, then the supporting pages."""

from __future__ import annotations

import streamlit as st

from live_lab import compare_hub, driver, images, offline_job
from live_lab import suite as suite_mod
from live_lab.ui.common import (
    ROOT, TILES, TRACKS, _brand, _chip, _go, _sidebar_brand, readiness,
)

APP_NAME = "Cipherops · MTL DNSSEC Lab"
APP_TAGLINE = "DNSSEC with Verisign MTL: signing, verification, size and cache."

TRACK_TEXT_HEIGHT = 170  # px; title, status chip, purpose and the steps line
TILE_TEXT_HEIGHT = 120


def sidebar_home(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("home")
    r = readiness(stt)
    st.sidebar.markdown("### This node")
    for ok, text in ((r["offline_images"], "Offline image"), (r["images"], f"Lab images · {h.stack}"),
                     (r["prepared"], "Zones signed"), (r["running"], "Lab running")):
        st.sidebar.markdown(f"{'🟢' if ok else '⚪'}  {text}")
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_home"):
        st.rerun()


def _tile_chip(mode: str, stt: dict) -> str:
    r = readiness(stt)
    if mode == "offline":
        if offline_job.is_running():
            return _chip("Running", "info")
        if not r["offline_images"]:
            return _chip("Needs image", "warn")
        n = len(compare_hub.offline_catalogue(ROOT / "results"))
        return _chip(f"{n} run(s)" if n else "Ready", "ok")
    if mode == "simulated":
        if suite_mod.running_suite():
            return _chip("Running", "info")
        if not r["images"]:
            return _chip("Needs images", "warn")
        return _chip("Lab up", "ok") if r["running"] else _chip("Ready", "ok")
    if mode == "compare":
        has = bool(compare_hub.offline_catalogue(ROOT / "results")) or bool(driver.list_labs())
        return _chip("Has data", "ok") if has else _chip("No data", "idle")
    return ""


def _steps_line(track: str) -> str:
    """Numbered path as small badges, so the step order matches the buttons instead of circled digits."""
    bits = []
    for i, (_key, label) in enumerate(TRACKS[track]["steps"], 1):
        name = label.split(" ", 1)[-1]
        bits.append(f'<span class="co-step"><span class="co-n">{i}</span>{name}</span>')
    joined = '<span class="co-arr">→</span>'.join(bits)
    return f'<div class="co-steps">{joined}</div>'


def _node_item(label: str, value: str, ok: bool) -> str:
    return f'<span class="co-node-item">{label} {_chip(value, "ok" if ok else "warn")}</span>'


def _node_strip(h: driver.LabHandle, stt: dict) -> None:
    """The node's prerequisites in one line: which images are here, and a way to manage them."""
    r = readiness(stt)
    ok = r["offline_images"] and r["images"]
    with st.container(border=True):
        text, manage = st.columns([5.4, 1.15], vertical_alignment="center")
        text.markdown(
            '<div class="co-node">'
            f'<span class="co-node-group"><span class="co-node-title">This node</span>'
            f'{_chip("ready" if ok else "needs images", "ok" if ok else "warn")}</span>'
            f'{_node_item("Offline image", images.ref("bench", images.OFFLINE_STACK), r["offline_images"])}'
            f'{_node_item("Lab images", h.stack, r["images"])}'
            "</div>",
            unsafe_allow_html=True,
        )
        manage.button("Manage images", on_click=_go, args=("setup",), key="home_node_images", width="stretch")


def _track_card(mode: str, stt: dict) -> None:
    tile = TILES[mode]
    with st.container(border=True):
        with st.container(height=TRACK_TEXT_HEIGHT, border=False):
            st.markdown(f"### {tile['icon']} {tile['title']}  {_tile_chip(mode, stt)}", unsafe_allow_html=True)
            st.caption(tile["pitch"])
            st.markdown(_steps_line(mode), unsafe_allow_html=True)
        st.button(f"{tile['cta']} →", key=f"open_{mode}", on_click=_go, args=(mode,), type="primary",
                  width="stretch")


def _tile_card(mode: str, stt: dict) -> None:
    tile = TILES[mode]
    with st.container(border=True):
        with st.container(height=TILE_TEXT_HEIGHT, border=False):
            st.markdown(f"#### {tile['icon']} {tile['title']}  {_tile_chip(mode, stt)}", unsafe_allow_html=True)
            st.caption(tile["pitch"])
        st.button(f"{tile['cta']} →", key=f"open_{mode}", on_click=_go, args=(mode,), type="primary",
                  width="stretch")


def home_page(h: driver.LabHandle, stt: dict) -> None:
    _symbol, full = _brand()
    if full is not None:
        logo_col, title_col = st.columns([1, 4], vertical_alignment="center")
        logo_col.image(full, width=160)
    else:
        title_col = st.container()
    title_col.markdown(
        f'<div class="co-hero"><h1>{APP_NAME}</h1><p class="co-sub">{APP_TAGLINE}</p></div>',
        unsafe_allow_html=True,
    )
    if stt.get("error"):
        st.error(f"Could not read lab status: {stt['error']}")

    _node_strip(h, stt)
    left, right = st.columns(2)
    with left:
        _track_card("offline", stt)
    with right:
        _track_card("simulated", stt)
    for col, mode in zip(st.columns(3), ("compare", "learn", "reset")):
        with col:
            _tile_card(mode, stt)
