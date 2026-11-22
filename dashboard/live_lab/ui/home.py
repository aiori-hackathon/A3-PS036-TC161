"""Home: this node's prerequisites, the two ways of benchmarking, then the supporting pages."""

from __future__ import annotations

from html import escape

import streamlit as st

from live_lab import compare_hub, driver, images, minimal, offline_job
from live_lab import suite as suite_mod
from live_lab.ui import dock, hero
from live_lab.ui.common import (
    ROOT, TILES, _brand, _chip, _go, _sidebar_brand, readiness, track_steps,
)

APP_NAME = "Cipherops · MTL DNSSEC Lab"
APP_TAGLINE = "DNSSEC with Verisign MTL: signing, verification, size and cache."

HERO_ANIMATION = True  # dots that form the lab's ideas beside the title; False shows the plain logo and title
CARD_DOCK = True  # tracks and pages as glass cards that react to the pointer; False shows the plain cards

TRACK_TEXT_HEIGHT = 170  # px; title, status chip, purpose and the steps line
TILE_TEXT_HEIGHT = 120


def sidebar_home(h: driver.LabHandle, stt: dict) -> None:
    """Testbed status lives only here on Home; the main page is hero + cards."""
    _sidebar_brand("home")
    r = readiness(stt)
    ok = r["offline_images"] and r["images"]
    rows = (
        (r["offline_images"], "Offline image", images.ref("bench", images.OFFLINE_STACK)),
        (r["images"], "Lab images", driver.stack_label(h.stack)),
        (r["prepared"], "Zones signed", None),
        (r["running"], "Lab running", None),
    )
    body = [
        f'<div class="co-sb-row"><span class="co-sb-dot{" on" if lit else ""}"></span>'
        f'<span class="co-sb-label">{escape(label)}</span>'
        + (f'<span class="co-sb-val">{escape(detail)}</span>' if detail else "")
        + "</div>"
        for lit, label, detail in rows
    ]
    st.sidebar.markdown(
        f'<div class="co-sb-panel"><div class="co-sb-head"><span class="co-sb-h">This node</span>'
        f'{_chip("ready" if ok else "needs images", "ok" if ok else "warn")}</div>'
        f'{"".join(body)}</div>',
        unsafe_allow_html=True,
    )
    if not minimal.enabled():
        st.sidebar.button("Manage images", on_click=_go, args=("setup",), key="sb_home_images",
                          width="stretch")
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_home"):
        st.rerun()


def _tile_chip(mode: str, stt: dict) -> tuple[str, str] | None:
    """(text, kind) for a card's status, or None when the card has no status."""
    r = readiness(stt)
    if mode == "offline":
        if offline_job.is_running():
            return "Running", "info"
        if not r["offline_images"]:
            return "Needs image", "warn"
        n = len(compare_hub.offline_catalogue(ROOT / "results"))
        return (f"{n} run(s)", "info") if n else ("Ready", "ok")
    if mode == "simulated":
        if suite_mod.running_suite():
            return "Running", "info"
        if not r["images"]:
            return "Needs images", "warn"
        return ("Lab up", "ok") if r["running"] else ("Ready", "ok")
    if mode == "compare":
        has = bool(compare_hub.offline_catalogue(ROOT / "results")) or bool(driver.list_labs())
        return ("Has data", "info") if has else ("No data", "idle")
    return None


def _chip_html(mode: str, stt: dict) -> str:
    chip = _tile_chip(mode, stt)
    return _chip(*chip) if chip else ""


def _steps_line(track: str) -> str:
    """Numbered path as small badges, so the step order matches the buttons instead of circled digits."""
    bits = []
    for i, (_key, label) in enumerate(track_steps(track), 1):
        bits.append(f'<span class="co-step"><span class="co-n">{i}</span>{label}</span>')
    joined = '<span class="co-arr">→</span>'.join(bits)
    return f'<div class="co-steps">{joined}</div>'


def _track_card(mode: str, stt: dict) -> None:
    tile = TILES[mode]
    with st.container(border=True):
        with st.container(height=TRACK_TEXT_HEIGHT, border=False):
            st.markdown(f"### {tile['icon']} {tile['title']}  {_chip_html(mode, stt)}", unsafe_allow_html=True)
            st.caption(tile["pitch"])
            st.markdown(_steps_line(mode), unsafe_allow_html=True)
        st.button(f"{tile['cta']} →", key=f"open_{mode}", on_click=_go, args=(mode,), type="primary",
                  width="stretch")


def _tile_card(mode: str, stt: dict) -> None:
    tile = TILES[mode]
    with st.container(border=True):
        with st.container(height=TILE_TEXT_HEIGHT, border=False):
            st.markdown(f"#### {tile['icon']} {tile['title']}  {_chip_html(mode, stt)}", unsafe_allow_html=True)
            st.caption(tile["pitch"])
        st.button(f"{tile['cta']} →", key=f"open_{mode}", on_click=_go, args=(mode,), type="primary",
                  width="stretch")


def _header() -> None:
    title = (f'<div class="co-hero"><h1 style="text-wrap:balance">{APP_NAME}</h1>'
             f'<p class="co-sub">{APP_TAGLINE}</p></div>')
    if HERO_ANIMATION and hero.available():
        title_col, art_col = st.columns([1.7, 1], vertical_alignment="center")
        title_col.markdown(title, unsafe_allow_html=True)
        with art_col:
            hero.render()
        return
    _symbol, full = _brand()
    if full is not None:
        logo_col, title_col = st.columns([1, 4], vertical_alignment="center")
        logo_col.image(full, width=160)
    else:
        title_col = st.container()
    title_col.markdown(title, unsafe_allow_html=True)


def _dock(stt: dict, tiles: list[str]) -> None:
    help_on = bool(st.session_state.get("show_help", True))
    dock.styles()
    for col, mode in zip(st.columns(2), ("offline", "simulated")):
        tile = TILES[mode]
        with col:
            dock.feature(mode, tile["icon"], tile["title"], _tile_chip(mode, stt),
                         tile["pitch"] if help_on else None, [label for _key, label in track_steps(mode)],
                         _go, (mode,))
    for col, mode in zip(st.columns(len(tiles)) if tiles else (), tiles):
        tile = TILES[mode]
        with col:
            dock.tile(mode, tile["icon"], tile["title"], _tile_chip(mode, stt), tile["pitch"] if help_on else None,
                      _go, (mode,), warn=mode == "reset")


def home_page(h: driver.LabHandle, stt: dict) -> None:
    _header()
    if stt.get("error"):
        st.error(f"Could not read lab status: {stt['error']}")

    tiles = [m for m in ("compare", "adopt", "learn", "reset") if minimal.mode_allowed(m)]
    if CARD_DOCK:
        _dock(stt, tiles)
        return
    left, right = st.columns(2)
    with left:
        _track_card("offline", stt)
    with right:
        _track_card("simulated", stt)
    for col, mode in zip(st.columns(len(tiles) or 1), tiles):
        with col:
            _tile_card(mode, stt)
