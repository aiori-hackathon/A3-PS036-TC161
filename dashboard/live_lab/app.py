"""Cipherops · MTL DNSSEC Lab — control room for the multi-zone stack.

    streamlit run dashboard/live_lab/app.py

Layout: a home hub with two tracks, Offline benchmark (Run → Results) and Simulated lab (Live lab =
Prepare+Query, plus an independent Benchmark), then Compare, Learn, Reset and the Node images page. Each
track checks its own images first and keeps its controls in the sidebar and its results in the main area.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from live_lab import compare, compare_hub, decode, driver  # noqa: E402
from live_lab import artifacts, lab, lanes, minimal, plugins, shapes  # noqa: E402
from live_lab import glossary as gl, image_job, images, jobs, offline_job, prepare_job, prepare_story  # noqa: E402
from live_lab import suite as suite_mod, suite_report  # noqa: E402

minimal = importlib.reload(minimal)
compare = importlib.reload(compare)
compare_hub = importlib.reload(compare_hub)
decode = importlib.reload(decode)
shapes = importlib.reload(shapes)
images = importlib.reload(images)
jobs = importlib.reload(jobs)
# driver re-exports lab and artifacts, which import from driver: two passes so each side sees the other's new code
for _ in range(2):
    lab = importlib.reload(lab)
    artifacts = importlib.reload(artifacts)
    driver = importlib.reload(driver)
image_job = importlib.reload(image_job)
offline_job = importlib.reload(offline_job)
gl = importlib.reload(gl)
prepare_story = importlib.reload(prepare_story)
suite_report = importlib.reload(suite_report)
suite_mod = importlib.reload(suite_mod)
prepare_job = importlib.reload(prepare_job)  # after suite_mod: it uses the suite's process helpers
plugins = importlib.reload(plugins)  # before adopt UI: Adoption reads stack helpers from here
lanes = importlib.reload(lanes)

st.set_page_config(page_title="Cipherops · MTL DNSSEC Lab", layout="wide", page_icon="🛰️")

from live_lab.ui import (  # noqa: E402
    activity, adopt, benchmark_offline, benchmark_simulated, common, compare_page, dock, hero, home,
    lab as lab_tile, learn, live, reset, setup, track_offline, track_simulated,
)

common = importlib.reload(common)
activity = importlib.reload(activity)
benchmark_simulated = importlib.reload(benchmark_simulated)
compare_page = importlib.reload(compare_page)
benchmark_offline = importlib.reload(benchmark_offline)
lab_tile = importlib.reload(lab_tile)
setup = importlib.reload(setup)
live = importlib.reload(live)
track_offline = importlib.reload(track_offline)
track_simulated = importlib.reload(track_simulated)
reset = importlib.reload(reset)
learn = importlib.reload(learn)
adopt = importlib.reload(adopt)
dock = importlib.reload(dock)
hero = importlib.reload(hero)
home = importlib.reload(home)

_CSS = """
<style>
/* Streamlit's wide layout leaves ~6rem above and ~10rem below the page; Home then needs a scrollbar. */
[data-testid="stMainBlockContainer"]{padding-top:2.75rem;padding-bottom:1.5rem}
.co-chip{display:inline-block;padding:2px 11px;border-radius:999px;font-size:.78rem;
  font-weight:600;letter-spacing:.01em;vertical-align:middle}
.co-ok{background:#0f3d2b;color:#6df0b0}
.co-warn{background:#47360a;color:#ffd166}
.co-idle{background:#2a2f3a;color:#b6bdcc}
.co-info{background:#0e2f52;color:#7cc4ff}
.co-hero h1{margin-bottom:.1rem}
.co-sub{opacity:.75;margin-top:0}
.co-strip{margin:.2rem 0 .8rem 0;opacity:.95}
.co-node{display:flex;align-items:center;flex-wrap:wrap;gap:.45rem 1.15rem}
[data-testid="stMarkdownContainer"]:has(.co-node){margin-bottom:0}
.co-node-group,.co-node-item{display:inline-flex;align-items:center;gap:.45rem;white-space:nowrap}
.co-node-title{font-weight:650;font-size:1.05rem}
.co-node-item{opacity:.92}
.co-steps{display:flex;align-items:center;flex-wrap:wrap;gap:.35rem .55rem;margin:.2rem 0 0}
.co-step{display:inline-flex;align-items:center;gap:.4rem;font-weight:600}
.co-n{display:inline-flex;align-items:center;justify-content:center;width:1.35rem;height:1.35rem;
  border-radius:999px;background:#ff4b4b;color:#fff;font-size:.75rem;font-weight:700;line-height:1;flex:none}
.co-arr{opacity:.4;font-weight:500}
/* Sidebar: brand, About, and a compact testbed status list. */
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p{margin-bottom:.2rem}
.co-sb-brand{margin:.15rem 0 .55rem}
.co-sb-name{font-size:1.35rem;font-weight:700;letter-spacing:-.01em;line-height:1.2}
.co-sb-tag{opacity:.65;font-size:.88rem;margin-top:.1rem}
.co-sb-panel{margin:.15rem 0 .7rem}
.co-sb-head{display:flex;align-items:center;flex-wrap:wrap;gap:.35rem .55rem;margin-bottom:.55rem}
.co-sb-head .co-sb-h{font-size:1.02rem;font-weight:650}
.co-sb-row{display:grid;grid-template-columns:10px minmax(0,1fr);column-gap:.55rem;row-gap:.1rem;
  align-items:start;margin:.42rem 0}
.co-sb-dot{width:8px;height:8px;border-radius:50%;margin-top:.42rem;background:rgba(250,250,250,.28);flex:none}
.co-sb-dot.on{background:#5BE7A9;box-shadow:0 0 0 3px rgba(91,231,169,.12)}
.co-sb-label{font-size:.92rem;font-weight:600;line-height:1.35}
.co-sb-val{grid-column:2;font:400 .78rem ui-monospace,"Cascadia Code",Consolas,monospace;
  color:rgba(233,241,255,.62);overflow-wrap:anywhere}
/* Track steps: one coral number, not a radio dot plus a circled digit. */
.st-key-step_offline [data-testid="stRadioGroup"],
.st-key-step_simulated [data-testid="stRadioGroup"]{counter-reset:cotrack;gap:1.15rem}
.st-key-step_offline [data-testid="stRadioOption"] > div > div:first-child,
.st-key-step_simulated [data-testid="stRadioOption"] > div > div:first-child{display:none !important}
.st-key-step_offline [data-testid="stRadioGroup"] [data-testid="stMarkdownContainer"] p,
.st-key-step_simulated [data-testid="stRadioGroup"] [data-testid="stMarkdownContainer"] p{
  display:flex;align-items:center;gap:.45rem;margin:0}
.st-key-step_offline [data-testid="stRadioGroup"] [data-testid="stMarkdownContainer"] p::before,
.st-key-step_simulated [data-testid="stRadioGroup"] [data-testid="stMarkdownContainer"] p::before{
  counter-increment:cotrack;content:counter(cotrack);display:inline-flex;align-items:center;
  justify-content:center;width:1.35rem;height:1.35rem;border-radius:999px;background:#2a2f3a;
  color:#d5dbe6;font-size:.75rem;font-weight:700;line-height:1;flex:none}
.st-key-step_offline [data-selected="true"] [data-testid="stMarkdownContainer"] p::before,
.st-key-step_simulated [data-selected="true"] [data-testid="stMarkdownContainer"] p::before{
  background:#ff4b4b;color:#fff}
</style>
"""


def _lab_revision() -> str:
    """Short git commit for the About panel; empty if git is unavailable."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT), text=True, timeout=2,
        ).strip()
    except Exception:  # noqa: BLE001
        return ""


_ABOUT_ASSET = Path(__file__).parent / "assets" / "about.html"

# About + maximize sit together in the app header band (left of Deploy), not in the sidebar.
_CHROME_TEMPLATE = r"""
<style>
  html, body { margin: 0; background: transparent; overflow: hidden; }
  .bar { display: flex; align-items: center; gap: 6px; justify-content: flex-end; height: 34px;
         font: 13px "Source Sans Pro", "Source Sans 3", system-ui, sans-serif; }
  .bar button { height: 32px; border-radius: 8px; cursor: pointer; color: #fafafa;
                background: rgba(38,39,48,.94); border: 1px solid rgba(250,250,250,.2); }
  .bar button:hover { border-color: #ff4b4b; color: #ff4b4b; }
  .bar #about { padding: 0 12px; font: inherit; font-weight: 600; letter-spacing: .01em; }
  .bar #fs { width: 32px; padding: 0; font-size: 15px; line-height: 1; }
</style>
<div class="bar">
  <button type="button" id="about" title="About this lab">About</button>
  <button type="button" id="fs" title="Full screen" aria-label="Full screen">&#x26F6;</button>
</div>
<script>
(function () {
  const PANEL = __PANEL__;
  const frame = window.frameElement;
  if (frame) {
    // Sit just under Streamlit's Deploy/menu row so both buttons stay visible and off the hero.
    Object.assign(frame.style, { position: "fixed", top: "3.75rem", right: "1.5rem",
      width: "124px", height: "36px", zIndex: "1000", border: "none", background: "transparent" });
    const slot = frame.closest("[data-testid='stElementContainer'], .stElementContainer, .element-container");
    if (slot) Object.assign(slot.style, { height: "0", margin: "0", padding: "0", overflow: "visible" });
  }
  const win = window.parent, doc = win.document;

  const fsBtn = document.getElementById("fs");
  try {
    const sync = () => {
      const on = !!doc.fullscreenElement;
      fsBtn.textContent = on ? "\u2715" : "\u26F6";
      fsBtn.title = on ? "Exit full screen" : "Full screen";
      fsBtn.setAttribute("aria-label", fsBtn.title);
    };
    fsBtn.onclick = () => (doc.fullscreenElement ? doc.exitFullscreen() : doc.documentElement.requestFullscreen());
    if (win.__coFs) doc.removeEventListener("fullscreenchange", win.__coFs);
    win.__coFs = sync;
    doc.addEventListener("fullscreenchange", sync);
    sync();
  } catch (e) { fsBtn.title = "Press F11 for full screen"; fsBtn.disabled = true; }

  function ensurePanel() {
    let root = doc.getElementById("co-about-root");
    const version = String(PANEL.length);
    if (root && root.dataset.v === version) return root;
    if (root) root.remove();
    root = doc.createElement("div");
    root.id = "co-about-root";
    root.dataset.v = version;
    root.innerHTML = PANEL;
    doc.body.appendChild(root);
    const close = () => root.classList.remove("open");
    root.querySelector(".ab-close").onclick = close;
    root.querySelector(".ab-backdrop").onclick = close;
    if (win.__coAboutKey) doc.removeEventListener("keydown", win.__coAboutKey);
    win.__coAboutKey = (ev) => { if (ev.key === "Escape") close(); };
    doc.addEventListener("keydown", win.__coAboutKey);
    return root;
  }
  document.getElementById("about").onclick = () => {
    const root = ensurePanel();
    root.classList.add("open");
    root.querySelector(".ab-scroll").scrollTop = 0;
  };
})();
</script>
"""


def _chrome_html(revision: str) -> str:
    panel = _ABOUT_ASSET.read_text(encoding="utf-8").replace("__REV__", revision or "local")
    return _CHROME_TEMPLATE.replace("__PANEL__", json.dumps(panel).replace("</", "<\\/"))


def _chrome_bar() -> None:
    """About + maximize in the header band, left of Streamlit's Deploy control."""
    common._embed_html(_chrome_html(_lab_revision()), height=1)


# ─────────────────────────── ENTRY POINT ───────────────────────────

SIDEBARS = {
    "home": home.sidebar_home, "offline": track_offline.sidebar_offline,
    "simulated": track_simulated.sidebar_simulated, "setup": setup.sidebar_setup,
    "compare": compare_page.sidebar_compare, "learn": learn.sidebar_learn, "adopt": adopt.sidebar_adopt,
    "reset": reset.sidebar_reset,
}
PAGES = {
    "offline": track_offline.page_offline, "simulated": track_simulated.page_simulated,
    "setup": setup.setup_page, "compare": compare_page.compare_page, "learn": learn.learn_page,
    "adopt": adopt.adopt_page, "reset": reset.reset_page,
}


def _minimal_gate() -> bool:
    """In minimal mode with CIPHEROPS_MINIMAL_PASSWORD set, ask for it once per browser session."""
    secret = minimal.password()
    if not minimal.enabled() or not secret or st.session_state.get("minimal_ok"):
        return True
    import hmac
    st.markdown("## 🛰️ Cipherops · MTL DNSSEC Lab")
    st.info(minimal.NOTICE)
    with st.form("minimal_login"):
        typed = st.text_input("Access code", type="password", help="Shared with the evaluators by the team.")
        if st.form_submit_button("Enter", type="primary"):
            if hmac.compare_digest(typed.encode(), secret.encode()):
                st.session_state.minimal_ok = True
                st.rerun()
            st.error("That code is not right.")
    st.caption(minimal.FULL_EXPERIENCE)
    return False


def main() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    if not _minimal_gate():
        return
    common.chrome_bar = None  # About + maximize are in the header band, not the sidebar
    _chrome_bar()
    symbol, _full = common._brand()
    if symbol is not None:
        st.logo(symbol, icon_image=symbol, size="large")  # top-left on Home and on every tile
    common._init_nav()
    common._bootstrap_cfg_from_disk()
    h = common._handle()
    mode = st.session_state.nav
    if not minimal.mode_allowed(mode):  # checked on every run, not only when the page is opened
        common._go("home")
        mode = st.session_state.nav
    if minimal.enabled():
        st.info(minimal.NOTICE + "  \n" + minimal.FULL_EXPERIENCE)

    err = common._apply_ui_config(h)
    tag_before = h.tag
    stt = common._status(h)

    SIDEBARS[mode](h, stt)
    # Sidebar widgets may have changed settings this run; write them out before the page reads them.
    err = common._apply_ui_config(h) or err
    if h.tag != tag_before:
        st.rerun()  # zone size changed → a different lab; re-read its status
    if (err and mode == "simulated" and common.current_step("simulated") == "live"
            and not st.session_state.get("shape_errors")):
        st.sidebar.error(err)

    if mode == "home":
        home.home_page(h, stt)
    else:
        PAGES[mode](h, stt)


if __name__ == "__main__":
    main()
