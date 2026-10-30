"""Cipherops · MTL DNSSEC Lab — control room for the multi-zone stack.

    streamlit run dashboard/live_lab/app.py

Layout: a home hub of independent tiles (Setup, Live, Benchmark, Reset). Setup is the one
mandatory first step; every other tile works on its own once the lab is up. Each tile keeps
its controls in the sidebar and its own Results view in the main area.
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
from live_lab import artifacts, lab, shapes  # noqa: E402
from live_lab import glossary as gl, image_job, images, jobs, offline_job, prepare_job, prepare_story  # noqa: E402
from live_lab import suite as suite_mod  # noqa: E402

compare = importlib.reload(compare)
compare_hub = importlib.reload(compare_hub)
decode = importlib.reload(decode)
shapes = importlib.reload(shapes)
images = importlib.reload(images)
jobs = importlib.reload(jobs)
driver = importlib.reload(driver)
lab = importlib.reload(lab)
artifacts = importlib.reload(artifacts)
driver = importlib.reload(driver)  # again, so its re-exports are the reloaded lab and artifacts
image_job = importlib.reload(image_job)
offline_job = importlib.reload(offline_job)
gl = importlib.reload(gl)
prepare_story = importlib.reload(prepare_story)
suite_mod = importlib.reload(suite_mod)
prepare_job = importlib.reload(prepare_job)  # after suite_mod: it uses the suite's process helpers

st.set_page_config(page_title="Cipherops · MTL DNSSEC Lab", layout="wide", page_icon="🛰️")

from live_lab.ui import (  # noqa: E402
    activity, benchmark, benchmark_offline, benchmark_simulated, common, compare_page, home, lab as lab_tile,
    learn, live, reset, setup,
)

common = importlib.reload(common)
activity = importlib.reload(activity)
benchmark_simulated = importlib.reload(benchmark_simulated)
compare_page = importlib.reload(compare_page)
benchmark_offline = importlib.reload(benchmark_offline)
benchmark = importlib.reload(benchmark)
lab_tile = importlib.reload(lab_tile)
setup = importlib.reload(setup)
live = importlib.reload(live)
reset = importlib.reload(reset)
learn = importlib.reload(learn)
home = importlib.reload(home)

_CSS = """
<style>
.co-chip{display:inline-block;padding:2px 11px;border-radius:999px;font-size:.78rem;
  font-weight:600;letter-spacing:.01em;vertical-align:middle}
.co-ok{background:#0f3d2b;color:#6df0b0}
.co-warn{background:#47360a;color:#ffd166}
.co-idle{background:#2a2f3a;color:#b6bdcc}
.co-info{background:#0e2f52;color:#7cc4ff}
.co-hero h1{margin-bottom:.1rem}
.co-sub{opacity:.75;margin-top:0}
.co-strip{margin:.2rem 0 .8rem 0;opacity:.95}
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


# Top-right chrome: "About" (opens a full-page panel) + full-screen symbol. The About content is
# assets/about.html; this small iframe only draws the two buttons and injects that panel into the page.
_ABOUT_ASSET = Path(__file__).parent / "assets" / "about.html"

_CHROME_TEMPLATE = r"""
<style>
  html, body { margin: 0; background: transparent; overflow: hidden; }
  .bar { display: flex; align-items: center; gap: 6px; justify-content: flex-end; height: 34px;
         font: 13px "Source Sans Pro", "Source Sans 3", system-ui, sans-serif; }
  .bar button { height: 32px; border-radius: 8px; cursor: pointer; color: #31333f;
                background: rgba(255,255,255,.94); border: 1px solid rgba(49,51,63,.18); }
  .bar button:hover { border-color: #ff4b4b; color: #ff4b4b; }
  .bar #about { padding: 0 12px; font: inherit; font-weight: 600; letter-spacing: .01em; }
  .bar #fs { width: 32px; padding: 0; font-size: 15px; line-height: 1; }
  @media (prefers-color-scheme: dark) {
    .bar button { color: #fafafa; background: rgba(38,39,48,.94); border-color: rgba(250,250,250,.2); }
    .bar button:hover { border-color: #ff4b4b; color: #ff4b4b; }
  }
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
    Object.assign(frame.style, { position: "fixed", top: "4.1rem", right: "1.25rem",
      width: "118px", height: "36px", zIndex: "998", border: "none", background: "transparent" });
    const slot = frame.closest("[data-testid='stElementContainer'], .stElementContainer, .element-container");
    if (slot) Object.assign(slot.style, { height: "0", margin: "0", padding: "0", overflow: "visible" });
  }
  const win = window.parent, doc = win.document;

  // Full screen: needs a click, so it is a button. Replace any listener left by an earlier run of this script.
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

  // About panel: built once per page load (rebuilt if its content changed), closed by x, backdrop or Esc.
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
    common._embed_html(_chrome_html(_lab_revision()), height=1)


# ─────────────────────────── ENTRY POINT ───────────────────────────

SIDEBARS = {
    "home": home.sidebar_home, "setup": setup.sidebar_setup, "lab": lab_tile.sidebar_lab,
    "live": live.sidebar_live,
    "benchmark": benchmark.sidebar_benchmark, "compare": compare_page.sidebar_compare,
    "learn": learn.sidebar_learn, "reset": reset.sidebar_reset,
}
PAGES = {
    "setup": setup.setup_page, "lab": lab_tile.lab_page, "live": live.live_page,
    "benchmark": benchmark.benchmark_page,
    "compare": compare_page.compare_page, "learn": learn.learn_page, "reset": reset.reset_page,
}


def main() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    _chrome_bar()  # About + full screen, top-right; no layout cost on the sidebar or page body
    symbol, _full = common._brand()
    if symbol is not None:
        st.logo(symbol, icon_image=symbol, size="large")  # top-left on Home and on every tile
    common._init_nav()
    common._bootstrap_cfg_from_disk()
    h = common._handle()
    mode = st.session_state.nav

    err = common._apply_ui_config(h)
    tag_before = h.tag
    stt = common._status(h)

    SIDEBARS[mode](h, stt)
    # Sidebar widgets may have changed settings this run; write them out before the page reads them.
    err = common._apply_ui_config(h) or err
    if h.tag != tag_before:
        st.rerun()  # zone size changed → a different lab; re-read its status
    if err and mode == "setup" and not st.session_state.get("shape_errors"):
        st.sidebar.error(err)

    if mode == "home":
        home.home_page(h, stt)
    else:
        PAGES[mode](h, stt)


if __name__ == "__main__":
    main()
