"""Learn tile: the three animated walkthroughs."""

from __future__ import annotations

import streamlit as st

from live_lab import driver
from live_lab.ui.common import ROOT, _go, _hint, _page_header, _sidebar_brand, cfg, sb

LEARN_DIR = ROOT / "docs" / "learn"
LEARN_PAGES = {
    "why": ("Why DNSSEC needs MTL", "why-dnssec-needs-mtl.html", 1180,
            "Six short animated scenes, from a poisoned cache to the packet-size problem. Click inside the animation "
            "first, then use → and ← to step and space to play."),
    "measure": ("What we measure, and why two modes", "what-we-measure.html", 1300,
                "The four PS-036 measurements and who pays for each, how the offline harness and the simulated lab "
                "measure them, what each mode cannot tell you, and which claims the results support."),
    "ops": ("Operator walkthrough", "mtl-dnssec-ops-walkthrough.html", 1500,
            "What a DNS operator does, step by step, for SLH-DSA and for SLH-DSA with MTL: bring up a signed zone, then "
            "add a record. Switch the mode at any step to compare the work, the bytes and the resolver's job."),
}


@st.cache_data(show_spinner=False)
def _learn_html(name: str, mtime: float) -> str:
    return (LEARN_DIR / name).read_text(encoding="utf-8")


def sidebar_learn(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("learn")
    st.sidebar.markdown("### Learn")
    sb(st.sidebar.radio, "learn_page", "why", "Walkthrough", options=list(LEARN_PAGES),
       format_func=lambda k: LEARN_PAGES[k][0], help="Pick the animation to show.")
    _hint(LEARN_PAGES[cfg("learn_page", "why")][3])
    st.sidebar.markdown("---")
    st.sidebar.markdown("**Go further**")
    st.sidebar.markdown("- `docs/learn/README.md`: the same story in text, with a reading path  \n"
                        "- `docs/methodology.md`: how every number is measured")


def learn_page(h: driver.LabHandle, stt: dict) -> None:
    _page_header("learn", h, stt)
    key = cfg("learn_page", "why")
    title, name, height, _note = LEARN_PAGES.get(key, LEARN_PAGES["why"])
    page = LEARN_DIR / name
    if not page.is_file():
        st.info(f"The walkthrough is missing from this checkout (`docs/learn/{name}`).")
        return
    import streamlit.components.v1 as components
    st.markdown(f"**{title}**")
    components.html(_learn_html(name, page.stat().st_mtime), height=height, scrolling=True)
    st.caption("A teaching aid, not a measurement: its sizes are illustrative or the draft's maximum-size formulas. "
               "The measured results are in the Offline benchmark and Simulated lab tracks, in Compare and in each run's report.")
    st.button("Back to Home", on_click=_go, args=("home",), key="learn_home")
