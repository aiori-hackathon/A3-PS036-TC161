"""Home hub: the tile cards and the lab readiness sidebar."""

from __future__ import annotations

import streamlit as st

from live_lab import compare_hub, driver, offline_job
from live_lab import suite as suite_mod
from live_lab.ui.common import (
    ROOT, TILES, _brand, _chip, _go, _hint, _lab_strip, _scenario_list, _sidebar_brand, readiness,
)

APP_NAME = "Cipherops · MTL DNSSEC Lab"
APP_TAGLINE = "Root → TLD → SLD · validating Unbound · Verisign MTL"

TILE_TEXT_HEIGHT = 120  # px; title, status chip and a two-line purpose


def sidebar_home(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("home")
    st.sidebar.markdown("### How to use this lab")
    st.sidebar.markdown(
        "1. **Setup** (first, once) — put the pinned images on this node: build them or pull them  \n"
        "2. **Benchmark** — *Offline* needs the harness image; *Simulated* needs the lab images  \n"
        "3. **Lab** — sign the zones and start the simulated lab (for hands-on queries)  \n"
        "4. **Live** — send a query, compare cold and warm cache, read the packets  \n"
        "5. **Compare** — algorithms side by side, run against run, zone updates  \n"
        "6. **Learn** — why DNSSEC needs MTL  \n"
        "7. **Reset** — clear only what you choose"
    )
    st.sidebar.markdown("---")
    r = readiness(stt)
    st.sidebar.markdown("### Readiness")
    for ok, text in ((r["offline_images"], "Offline harness image"), (r["images"], "Simulated lab images"),
                     (r["prepared"], "Zones signed"), (r["running"], "Containers running")):
        st.sidebar.markdown(f"{'🟢' if ok else '⚪'}  {text}")
    sc = _scenario_list(h)
    if sc:
        st.sidebar.markdown(f"🔵  Algorithms **{', '.join(sc)}**")
    if r["active"] and r["running"]:
        st.sidebar.markdown(f"▶️  Active **{r['active']}**")
    st.sidebar.markdown("---")
    _hint("Open a tile to see its settings here. Each tile has its own Results view.")
    if st.sidebar.button("Refresh status", width="stretch", key="sb_refresh_home"):
        st.rerun()


# ─────────────────────────── HOME ───────────────────────────

def _tile_chip(mode: str, stt: dict, h: driver.LabHandle) -> str:
    r = readiness(stt)
    if mode == "setup":
        if r["offline_images"] and r["images"]:
            return _chip("Images ready", "ok")
        if r["offline_images"] or r["images"]:
            return _chip("Partly ready", "info")
        return _chip("Start here", "warn")
    if mode == "lab":
        if not r["images"]:
            return _chip("Needs images", "warn")
        if r["ready"]:
            return _chip("Ready", "ok")
        return _chip("Signed" if r["prepared"] else "Not signed", "info")
    if mode == "benchmark":
        if suite_mod.running_suite() or offline_job.is_running():
            return _chip("Running", "info")
        if not (r["offline_images"] or r["images"]):
            return _chip("Needs images", "warn")
        n = len([s for s in suite_mod.list_suites() if s["complete"]]) + len(compare_hub.offline_catalogue(ROOT / "results"))
        return _chip(f"Ready · {n} saved" if n else "Ready", "ok")
    if mode == "live":
        if not r["ready"]:
            return _chip("Needs a running lab", "warn")
        return _chip(f"Live · {r['active']}", "ok") if r["active"] else _chip("Ready", "ok")
    if mode == "compare":
        has = bool(compare_hub.offline_catalogue(ROOT / "results")) or bool(driver.list_labs())
        return _chip("Has data", "ok") if has else _chip("No data yet", "idle")
    if mode == "learn":
        return _chip("No setup needed", "ok")
    return _chip("Maintenance", "idle")


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
    _lab_strip(h, stt)
    if stt.get("error"):
        st.error(f"Could not read lab status: {stt['error']}")

    r = readiness(stt)
    if not (r["offline_images"] or r["images"]):
        st.info("**New here? Start with Setup.** It puts the pinned images on this node (build them here, or pull "
                "them from a registry) and verifies them. Benchmark runs inside those images.")
    elif not r["ready"]:
        st.info("Benchmark is ready. **Lab** signs the zones and starts the simulated lab, which Live needs.")

    order = [("setup", "lab", "benchmark"), ("live", "compare", "learn"), ("reset",)]
    for row in order:
        for col, mode in zip(st.columns(3), row):
            tile = TILES[mode]
            with col, st.container(border=True):
                # Fixed-height text block so the cards (and their buttons) line up however the copy wraps.
                with st.container(height=TILE_TEXT_HEIGHT, border=False):
                    st.markdown(f"#### {tile['icon']} {tile['title']}  {_tile_chip(mode, stt, h)}",
                                unsafe_allow_html=True)
                    st.caption(tile["pitch"])
                # Readiness is shown by the chip and the banner above, not by dimming some buttons.
                st.button(
                    f"{tile['cta']} →", key=f"open_{mode}", on_click=_go, args=(mode,),
                    type="primary", width="stretch",
                )
