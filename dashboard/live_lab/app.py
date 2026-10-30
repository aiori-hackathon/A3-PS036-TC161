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
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
ECO_DIR = ROOT / "ecosystem"
SUITES_DIR = ECO_DIR / "results" / "suites"
sys.path.insert(0, str(ROOT / "dashboard"))

from live_lab import compare, compare_hub, decode, driver  # noqa: E402
from live_lab import build_job, glossary as gl, offline_job, prepare_job, prepare_story  # noqa: E402
from live_lab import suite as suite_mod  # noqa: E402
from cipherops import evolution  # noqa: E402

compare = importlib.reload(compare)
compare_hub = importlib.reload(compare_hub)
decode = importlib.reload(decode)
driver = importlib.reload(driver)
build_job = importlib.reload(build_job)
offline_job = importlib.reload(offline_job)
gl = importlib.reload(gl)
prepare_story = importlib.reload(prepare_story)
suite_mod = importlib.reload(suite_mod)
prepare_job = importlib.reload(prepare_job)  # after suite_mod: it uses the suite's process helpers

st.set_page_config(page_title="Cipherops · MTL DNSSEC Lab", layout="wide", page_icon="🛰️")

APP_NAME = "Cipherops · MTL DNSSEC Lab"
APP_TAGLINE = "Root → TLD → SLD · validating Unbound · Verisign MTL"

IMAGE_LABELS = {
    "cipherops-eco-nsd": "NSD authoritative servers",
    "cipherops-eco-unbound": "Unbound validating resolver",
    "cipherops-eco-tools": "Signing & probe tools",
}

SCENARIO_BLURB = {
    "rsa": "RSA-2048 with SHA-256",
    "ecdsa": "ECDSA P-256",
    "ed25519": "Ed25519",
    "slh-dsa": "Post-quantum SLH-DSA (large signatures)",
    "mtl": "SLH-DSA with MTL condensed signatures",
    "mixed-mtl": "ECDSA at root/TLD, MTL at the leaf zone",
}

LEVEL_ICON = {"info": "ℹ️", "ok": "✅", "warn": "⚠️", "err": "❌", "step": "▶️"}
ACTIVITY_CAP = 500

NAV_MODES = ("home", "setup", "live", "benchmark", "compare", "learn", "reset")
TILE_TEXT_HEIGHT = 120  # px; title, status chip and a two-line purpose

TILES = {
    "setup": {
        "icon": "🛠️",
        "title": "Setup",
        "pitch": "Build the images, sign the zones and start the test lab.",
        "cta": "Open Setup",
    },
    "live": {
        "icon": "📡",
        "title": "Live",
        "pitch": "Send queries to the running lab and inspect the packets.",
        "cta": "Open Live",
    },
    "benchmark": {
        "icon": "📊",
        "title": "Benchmark",
        "pitch": "Measure signing, verification, response size and cache, offline or simulated.",
        "cta": "Open Benchmark",
    },
    "compare": {
        "icon": "⚖️",
        "title": "Compare",
        "pitch": "Algorithms side by side, run against run, and the cost of zone updates.",
        "cta": "Open Compare",
    },
    "learn": {
        "icon": "📖",
        "title": "Learn",
        "pitch": "Why DNSSEC needs MTL, and what an operator does differently: two short animations.",
        "cta": "Open Learn",
    },
    "reset": {
        "icon": "🧹",
        "title": "Reset",
        "pitch": "Clear saved runs, signed zones, captures and logs. Nothing else.",
        "cta": "Open Reset",
    },
}

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


@st.cache_resource(show_spinner=False)
def _brand():
    """(symbol, full logo) from the team logo, with its black backdrop made transparent.

    Returns (None, None) if the logo file is missing so the app still runs without it.
    """
    try:
        import numpy as np
        from PIL import Image

        src = ROOT / "docs" / "media" / "cipherops-logo-dark.png"
        rgb = np.asarray(Image.open(src).convert("RGB")).astype(float)
        alpha = np.clip((rgb.max(axis=2) - 12) * (255 / 70), 0, 255)
        full = Image.fromarray(np.dstack([rgb, alpha]).astype("uint8"), "RGBA")

        def _crop(img, rows=None, pad=6):
            a = np.asarray(img)[..., 3]
            if rows is not None:
                a = a[:rows]
            ys, xs = np.where(a > 60)
            return img.crop((max(xs.min() - pad, 0), max(ys.min() - pad, 0),
                             xs.max() + pad, ys.max() + pad))

        return _crop(full, rows=605), _crop(full)
    except Exception:  # noqa: BLE001
        return None, None


def _chip(text: str, kind: str = "idle") -> str:
    return f'<span class="co-chip co-{kind}">{text}</span>'


def _hint(text: str) -> None:
    """One-line sidebar guidance (public-demo friendly, not a wall of text)."""
    st.sidebar.caption(text)


_SESSION_KEYS = ("last_query", "query_log", "last_stats", "cache_result", "prepare_wall_s",
                 "prepare_summary", "last_log", "replay_fresh")
_PCAP_KEYS = ("last_pcap", "last_dropped", "last_story", "last_wire", "last_seq_steps",
              "last_seq_pcap")


# ─────────────────────────── settings that survive tile switches ───────────────────────────
# Streamlit drops a widget's state as soon as the widget stops being drawn. Each tile draws a
# different sidebar, so values live in st.session_state["cfg"] and widgets are re-seeded from it.

def _store() -> dict:
    return st.session_state.setdefault("cfg", {})


def _bootstrap_cfg_from_disk() -> None:
    """Browser refresh wipes session_state; restore stack/zone from disk so pcaps do not 'vanish'."""
    if st.session_state.get("_cfg_bootstrapped"):
        return
    st.session_state._cfg_bootstrapped = True
    inferred = driver.infer_ui_from_disk()
    if not inferred:
        return
    store = _store()
    for key in ("stack", "zone_size", "scenarios"):
        if key in inferred and key not in store:
            store[key] = inferred[key]
    # Seed widget keys before first sb() draw so selectboxes do not snap to DEFAULT_STACK.
    if "stack" in inferred and "w_stack" not in st.session_state:
        st.session_state.w_stack = inferred["stack"]
    if "zone_size" in inferred and "w_zone_size" not in st.session_state:
        st.session_state.w_zone_size = inferred["zone_size"]
    if "scenarios" in inferred and "w_scenarios" not in st.session_state:
        st.session_state.w_scenarios = list(inferred["scenarios"])


def cfg(name: str, default=None):
    return _store().get(name, default)


def sb(fn, name: str, default, *args, options=None, **kw):
    """Draw a sidebar widget whose value persists in cfg[name] across tiles."""
    ss = st.session_state
    wk = f"w_{name}"
    if wk not in ss:
        ss[wk] = cfg(name, default)
    if options is not None:
        opts = list(options)
        if isinstance(ss[wk], list):
            ss[wk] = [v for v in ss[wk] if v in opts]
        elif ss[wk] not in opts:
            ss[wk] = default if default in opts else (opts[0] if opts else None)
        val = fn(*args, opts, key=wk, **kw) if args else fn(opts, key=wk, **kw)
    else:
        val = fn(*args, key=wk, **kw)
    _store()[name] = val
    return val


# ─────────────────────────── shared state helpers ───────────────────────────

def _handle() -> driver.LabHandle:
    if "lab_handle" not in st.session_state:
        st.session_state.lab_handle = driver.LabHandle()
    return st.session_state.lab_handle


def activity(
    level: str, title: str, detail: str = "", *,
    phase: str = "ui", source: str = "ui", h: driver.LabHandle | None = None,
    tech: bool = False,
) -> None:
    entry = {
        "t": datetime.now().strftime("%H:%M:%S"),
        "level": level, "title": title, "detail": (detail or "").strip(),
        "phase": phase, "source": source, "tech": bool(tech),
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
    """Keep raw Docker/tool output for debugging; hidden from the simple Activity view."""
    chunks = driver.chunk_log(text)
    for i, ch in enumerate(chunks, 1):
        suffix = f" ({i}/{len(chunks)})" if len(chunks) > 1 else ""
        activity("info", f"{title}{suffix}", f"```\n{ch}\n```", phase=phase, source=source, tech=True)


def prepare_metrics_visible(h: driver.LabHandle) -> bool:
    if "show_prepare_metrics" in st.session_state:
        return bool(st.session_state.show_prepare_metrics)
    return bool(driver.load_session(h).get("show_prepare_metrics", False))


def _status(h: driver.LabHandle) -> dict:
    try:
        return driver.status(h)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:300], "images": {}, "containers": {}, "prepared": False, "active": None}


def readiness(stt: dict) -> dict:
    imgs = stt.get("images") or {}
    containers = stt.get("containers") or {}
    r = {
        "images": bool(imgs) and all(imgs.values()),
        "prepared": bool(stt.get("prepared")),
        "running": any(containers.values()),
        "active": stt.get("active"),
    }
    r["ready"] = r["images"] and r["prepared"] and r["running"]
    r["count"] = sum(1 for k in ("images", "prepared", "running") if r[k])
    return r


def _scenario_list(h: driver.LabHandle) -> list[str]:
    """Scenarios chosen in Setup (and present on disk after Prepare), not only the active one."""
    chosen = [str(s) for s in (cfg("scenarios") or [])]
    on_disk: list[str] = []
    zones = h.work / "zones"
    if zones.is_dir():
        on_disk = sorted(
            p.name for p in zones.iterdir()
            if p.is_dir() and (p / "manifest.json").is_file()
        )
    if on_disk:
        ordered = [s for s in chosen if s in on_disk]
        return ordered + [s for s in on_disk if s not in ordered]
    return chosen


def _go(mode: str) -> None:
    st.session_state.nav = mode if mode in NAV_MODES else "home"
    try:
        if mode == "home":
            if "tile" in st.query_params:
                del st.query_params["tile"]
        else:
            st.query_params["tile"] = mode
    except Exception:  # noqa: BLE001
        pass


def _init_nav() -> None:
    if "nav" in st.session_state:
        return
    tile = "home"
    try:
        tile = st.query_params.get("tile", "home")
    except Exception:  # noqa: BLE001
        pass
    st.session_state.nav = tile if tile in NAV_MODES else "home"


PROFILE_FIELDS = (
    ("client_ms", "Client link (ms)"), ("root_ms", "To root (ms)"), ("tld_ms", "To TLD (ms)"),
    ("sld_ms", "To leaf (ms)"), ("loss_pct", "Loss (%)"),
)
EDITABLE_PROFILES = ("wan", "lossy")  # lan stays the zero-delay baseline


def _custom_inputs() -> dict:
    return {
        "tlds": cfg("custom_tlds", "test"),
        "sld_count": int(cfg("custom_sld_count", 4)),
        "sizes": cfg("custom_sizes", "25, 50"),
        "servers": int(cfg("custom_servers", 1)),
        "seed": int(cfg("custom_seed", driver.DEFAULT_SEED)),
    }


def _shape() -> tuple[dict | None, list[str], list[str]]:
    """Zone shape for the current Setup choice, plus validation errors and warnings."""
    return driver.shape_for(cfg("zone_size", "small"), _custom_inputs())


def _workload() -> dict:
    d = driver.WORKLOAD_DEFAULTS
    return {
        "nx_fraction": int(cfg("nx_pct", round(d["nx_fraction"] * 100))) / 100,
        "zone_zipf_s": float(cfg("zone_zipf", d["zone_zipf_s"])),
        "name_zipf_s": float(cfg("name_zipf", d["name_zipf_s"])),
        "timeout_s": float(cfg("timeout_s", d["timeout_s"])),
    }


def _profiles() -> dict:
    out = {}
    for p in EDITABLE_PROFILES:
        base = driver.PROFILE_DEFAULTS[p]
        out[p] = {f: cfg(f"{p}_{f}", base[f]) for f, _ in PROFILE_FIELDS}
    return out


def _workload_is_default() -> bool:
    d = driver.WORKLOAD_DEFAULTS
    w = _workload()
    if (w["nx_fraction"], w["zone_zipf_s"], w["name_zipf_s"], w["timeout_s"]) != (
            d["nx_fraction"], d["zone_zipf_s"], d["name_zipf_s"], d["timeout_s"]):
        return False
    return all(float(v) == float(driver.PROFILE_DEFAULTS[p][f])
               for p, vals in _profiles().items() for f, v in vals.items())


def _restore_workload() -> None:
    keys = ["nx_pct", "zone_zipf", "name_zipf", "timeout_s"] + [
        f"{p}_{f}" for p in EDITABLE_PROFILES for f, _ in PROFILE_FIELDS]
    for k in keys:
        _store().pop(k, None)
        st.session_state.pop(f"w_{k}", None)


def _apply_ui_config(h: driver.LabHandle) -> str | None:
    """Write configs/_dashboard.yaml from the stored settings and point the handle at it."""
    sc = list(cfg("scenarios") or ["ecdsa", "slh-dsa", "mtl"])
    if not sc:
        return "Select at least one algorithm in Setup."
    zone = cfg("zone_size", "small")
    shape, errors, _warnings = _shape()
    st.session_state.shape_errors = errors
    if errors or shape is None:
        return "Fix the custom zone numbers in Setup: " + " ".join(errors)
    if cfg("_zone_prev") != zone:
        # New zone size → queries-per-trial follows that size's default until the user edits it.
        _store()["n_queries"] = int(shape["n_queries_default"])
        _store()["_zone_prev"] = zone
        st.session_state.pop("w_n_queries", None)
    nq = int(cfg("n_queries", shape["n_queries_default"]))
    try:
        path = driver.write_ui_config(
            zone_size=zone,
            scenarios=sc,
            n_queries=nq,
            trials=int(cfg("trials", 1)),
            network_run=list(cfg("networks") or ["lan"]),
            udp_limits=[int(cfg("udp", 1232))],
            custom=_custom_inputs(),
            workload=_workload(),
            profiles=_profiles(),
        )
    except Exception as e:  # noqa: BLE001
        return str(e)[:400]
    h.config = path
    h.stack = cfg("stack", "draft06")
    st.session_state.lab_handle = h
    # Survive browser refresh: session_state alone is not enough for stack/zone.
    driver.save_ui_prefs({
        "stack": h.stack,
        "zone_size": zone,
        "scenarios": sc,
        "lab": h.lab,
        "tag": h.tag,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    })
    return None


# ─────────────────────────── page chrome ───────────────────────────

def _lab_strip(h: driver.LabHandle, stt: dict, *, mode: str = "setup") -> None:
    r = readiness(stt)
    sc = _scenario_list(h)
    if mode == "live" and r["active"]:
        scen_chip = _chip(f"Active · {r['active']}", "info")
    elif sc:
        label = "Algorithm" if len(sc) == 1 else "Algorithms"
        scen_chip = _chip(f"{label} · {', '.join(sc)}", "info")
    elif r["active"]:
        scen_chip = _chip(f"Algorithm · {r['active']}", "info")
    else:
        scen_chip = ""
    chips = [
        _chip("Lab running", "ok") if r["running"] else _chip("Lab stopped", "idle"),
        _chip("Zones signed", "ok") if r["prepared"] else _chip("Zones not signed", "warn"),
        scen_chip,
        _chip(f"Zone · {cfg('zone_size', 'small')}", "idle"),
    ]
    st.markdown(f'<div class="co-strip">{" ".join(c for c in chips if c)}</div>', unsafe_allow_html=True)


def _page_header(mode: str, h: driver.LabHandle, stt: dict) -> None:
    tile = TILES[mode]
    st.markdown(f"## {tile['icon']} {tile['title']}")
    st.caption(tile["pitch"])
    if mode in ("setup", "live", "reset"):
        _lab_strip(h, stt, mode=mode)


def _gate(stt: dict, what: str) -> bool:
    """True when the lab is ready; otherwise explain what is missing and offer a way to Setup."""
    r = readiness(stt)
    if r["ready"]:
        return True
    missing = [name for name, ok in (("images", r["images"]), ("signed zones", r["prepared"]),
                                     ("running containers", r["running"])) if not ok]
    st.warning(f"{what} needs a ready lab. Still missing: {', '.join(missing)}.")
    st.button("Go to Setup", on_click=_go, args=("setup",), key=f"gate_{what}", type="primary")
    return False


def _activity_expander(h: driver.LabHandle) -> None:
    with st.expander("Activity log", expanded=False):
        activity_tab(h)


# ─────────────────────────── sidebars ───────────────────────────

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
    _embed_html(_chrome_html(_lab_revision()), height=1)

def _sidebar_brand(mode: str) -> None:
    st.sidebar.title("Cipherops")
    st.sidebar.caption("MTL DNSSEC Lab")
    if mode != "home":
        st.sidebar.button("← Home", on_click=_go, args=("home",), key="sb_home", width="stretch")
        st.sidebar.markdown("---")


def sidebar_home(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("home")
    st.sidebar.markdown("### How to use this lab")
    st.sidebar.markdown(
        "1. **Setup** (first, once) — build the images, sign the zones, start the lab  \n"
        "2. **Live** — send a query, compare cold and warm cache, read the packets  \n"
        "3. **Benchmark** — *Offline* needs no setup; *Simulated* needs only the images  \n"
        "4. **Compare** — algorithms side by side, run against run, zone updates  \n"
        "5. **Learn** — why DNSSEC needs MTL  \n"
        "6. **Reset** — clear only what you choose"
    )
    st.sidebar.markdown("---")
    r = readiness(stt)
    st.sidebar.markdown("### Lab readiness")
    for ok, text in ((r["images"], "Images built"), (r["prepared"], "Zones signed"),
                     (r["running"], "Containers running")):
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


def _step_label(n: int, title: str, state: str) -> str:
    mark = {"done": "🟢", "partial": "🟡"}.get(state, "⚪")
    return f"{mark}  **Step {n}** · {title}"


def _finish_prepare_job(h: driver.LabHandle) -> None:
    """Take over a Prepare job that has just ended: update this session and the Activity log, once.

    The worker has already saved the session summary to disk; this adds what only the page can do
    (open the signing replay, log the result). ``handled`` is stored with the job, so a second browser
    tab, or a later session, does not repeat it.
    """
    stt = prepare_job.status(h)
    state = stt.get("state")
    if state not in ("done", "failed", "stopped") or stt.get("handled"):
        return
    prepare_job.mark_handled(h)
    scen_txt = ", ".join(stt.get("scenarios") or [])
    if state != "done":
        msg = str(stt.get("error") or "Signing did not finish.")[-2000:]
        st.sidebar.error(msg[:600])
        activity("err", "Signing failed", msg, phase="prepare", source="prepare", h=h)
        return
    secs = float(stt.get("wall_s") or 0)
    summary = stt.get("summary") or driver.prepare_summary(h, wall_s=secs)
    ss = st.session_state
    ss.prepare_wall_s = secs
    ss.show_prepare_metrics = True
    ss.replay_fresh = True  # Setup → Overview opens the signing replay
    ss.prepare_summary = summary
    log = prepare_job.log_text(h)
    ss.last_log = log[-8000:]
    _log_chunks(log, phase="prepare", source="prepare", title="prepare")
    n_scen = len(summary.get("scenarios") or stt.get("scenarios") or [])
    activity("ok", f"Zones signed ({secs:.0f}s)",
             f"Signed zones for {n_scen} algorithm(s): {scen_txt}. Watch the signing replay in Setup, "
             "or open Results for sign times.", phase="prepare", source="prepare", h=h)
    activity("info", "Signing paths",
             f"work: `{summary['work_dir']}`\nzones: `{summary['zones_dir']}`\n"
             f"active: `{summary['active_dir']}`\ncompose: `{summary['compose']}`",
             phase="prepare", source="prepare", h=h, tech=True)
    st.sidebar.success(f"Done · {secs:.0f}s")


def _warn_unvalidated(stack: str, scenarios) -> None:
    bad = driver.unvalidated_scenarios(stack, scenarios)
    if bad:
        st.sidebar.warning(
            f"On **{stack}**, {', '.join(bad)} is not validated by the resolver (SERVFAIL), so it cannot be compared "
            "with MTL here. Use **draft06** for the MTL vs non-MTL comparison, or untick it.")


def _finish_build_job(h: driver.LabHandle) -> None:
    """Take over a build that has just ended: log the result in the Activity log, once (``handled`` is stored)."""
    stt = build_job.status()
    state = stt.get("state")
    if state not in ("done", "failed", "stopped") or stt.get("handled"):
        return
    build_job.mark_handled()
    log = build_job.log_text(8000)
    st.session_state.last_log = log
    _log_chunks(log, phase="build", source="compose", title="build")
    secs = float(stt.get("finished") or time.time()) - float(stt.get("started") or time.time())
    if state == "done":
        activity("ok", f"Build done ({secs:.0f}s)", f"Image set {stt.get('stack')}", phase="build", source="compose", h=h)
        st.sidebar.success(f"Images built · {secs:.0f}s")
    else:
        msg = str(stt.get("error") or "The build did not finish.")[-2000:]
        st.sidebar.error(msg[:600])
        activity("err", "Build failed", msg, phase="build", source="compose", h=h)


def sidebar_setup(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("setup")
    st.sidebar.markdown("### Setup")
    bench_busy = suite_mod.running_suite()
    if bench_busy:
        st.sidebar.warning(f"Benchmark {bench_busy['id']} is using the lab. Build, Sign zones, Start and Stop are "
                           "paused until it finishes or is cancelled.")

    r = readiness(stt)
    prepared_session = r["prepared"] and prepare_metrics_visible(h)
    _hint("Green means ready. Yellow means found on disk from an earlier session.")

    # —— Step 1: images (everything else runs inside them, so this comes first) ——
    _finish_build_job(h)
    building = build_job.is_running()
    st.sidebar.markdown(_step_label(1, "Images", "done" if r["images"] else "none"))
    _hint("Choose the DNS stack version, then build it. Sign zones, Start and the benchmark all run inside these images.")
    stacks = driver.list_stacks()
    stack = sb(
        st.sidebar.selectbox, "stack", driver.DEFAULT_STACK, "Image version",
        options=stacks, format_func=driver.stack_label,
        help="Which pinned Verisign DNS stack to build and run. "
             "draft06 = condensed-by-default (EDNS 65050). "
             "latest = always-full MTL, same MTL commit as the main Cipherops library report. "
             "Change this, then Build images (and Sign zones again for Live).",
    )
    pins = driver.stack_pins(stack)
    if pins:
        mtl = (pins.get("MTL_COMMIT") or "")[:12]
        oqs = pins.get("LIBOQS_TAG") or "?"
        _hint(f"Pins for **{stack}**: MTL `{mtl}…` · liboqs {oqs}. Build this version before Sign zones / Benchmark.")
    img_mode = sb(st.sidebar.radio, "image_mode", "Build locally", "Image source",
                  options=["Build locally", "Pull from registry"])
    if img_mode.startswith("Pull"):
        reg = sb(
            st.sidebar.text_input, "registry", "", "Registry", placeholder="ghcr.io/your-org/cipherops",
            help="Pre-built images named cipherops-eco-nsd / unbound / tools.",
        )
        if st.sidebar.button("Pull images", key="btn_pull", width="stretch", disabled=bool(bench_busy or building)):
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
        if building:
            _hint("Building in the background. The log is on the Setup page; you can keep using the dashboard.")
        if st.sidebar.button("Build images" if not building else "Building…", key="btn_build", width="stretch",
                             disabled=bool(bench_busy or building)):
            activity("step", "Build images", f"Image set {h.stack}", phase="build", source="compose", h=h)
            try:
                build_job.start(h.stack)  # returns at once; Setup shows the log live
            except Exception as e:  # noqa: BLE001
                st.sidebar.error(str(e)[:600])
                activity("err", "Build failed to start", str(e)[:2000], phase="build", source="compose", h=h)
            st.rerun()

    # —— Step 2: prepare (zone design, then sign) ——
    prep_state = "done" if prepared_session else ("partial" if r["prepared"] else "none")
    st.sidebar.markdown(_step_label(2, gl.label("sign_zones"), prep_state))
    _hint("Design the zones, then sign them for each selected algorithm.")
    size_keys = list(driver.ZONE_SIZE_PRESETS.keys())
    size = sb(
        st.sidebar.selectbox, "zone_size", "small", "Zone size", options=size_keys,
        format_func=lambda k: driver.ZONE_SIZE_PRESETS[k]["label"],
        help="Controls how many zones and names are generated. Use Small for a first visit, "
             "or Custom to set the numbers yourself.",
    )
    if size == driver.CUSTOM_SIZE:
        st.sidebar.markdown("**Custom zones**")
        sb(st.sidebar.text_input, "custom_tlds", "test", "TLDs",
           help="Comma-separated, e.g. test, lab. One server per TLD (up to 9).")
        sb(st.sidebar.number_input, "custom_sld_count", 4, "Leaf zones", min_value=1, max_value=200, step=1,
           help="Second-level zones, spread round-robin over the TLDs.")
        sb(st.sidebar.text_input, "custom_sizes", "25, 50", "Names per leaf zone",
           help="Comma-separated; the list repeats across the leaf zones, e.g. 25, 100, 250.")
        sb(st.sidebar.number_input, "custom_servers", 1, "Leaf servers", min_value=1, max_value=9, step=1,
           help="Servers that host the leaf zones (up to 9, and no more than leaf zones).")
        sb(st.sidebar.number_input, "custom_seed", driver.DEFAULT_SEED, "Seed", min_value=0,
           max_value=2**31 - 1, step=1, help="Same numbers and seed give the same zones every time.")
    shape, shape_errors, shape_warnings = _shape()
    if shape_errors:
        for msg in shape_errors:
            st.sidebar.error(msg)
    else:
        _hint(f"{shape['sld_count']} leaf zones · TLDs: {', '.join(shape['tlds'])}")
        for msg in shape_warnings:
            st.sidebar.warning(msg)

    all_sc = list(driver.SCENARIO_ALGS.keys())
    default_sc = [s for s in ("ecdsa", "slh-dsa", "mtl") if s in all_sc]
    sc = sb(
        st.sidebar.multiselect, "scenarios", default_sc, "Algorithms", options=all_sc,
        help=gl.tip("algorithm") + " Change this, then sign the zones again.",
    )
    sb(st.sidebar.number_input, "jobs", 2, "Sign jobs", min_value=1, max_value=8,
       help=gl.tip("parallel_jobs"))
    if not sc:
        st.sidebar.warning("Select at least one algorithm")
    _warn_unvalidated(stack, sc)

    _finish_prepare_job(h)
    signing = prepare_job.is_running(h)
    no_images = not r["images"]
    if no_images:
        _hint("Build or pull the images (step 1) first.")
    if signing:
        _hint("Signing is running. Watch it live in Setup.")
    if st.sidebar.button(gl.label("sign_zones"), key="btn_prep", help=gl.tip("sign_zones"), width="stretch",
                         disabled=not sc or bool(shape_errors) or signing or bool(bench_busy) or building or no_images):
        _apply_ui_config(h)
        jobs = int(cfg("jobs", 2))
        scen_txt = ", ".join(sc)
        activity("step", "Signing zones", f"Size {size} · {scen_txt}", phase="prepare", source="prepare", h=h)
        try:
            prepare_job.start(h, jobs=jobs, scenarios=list(sc))  # returns at once; Setup shows it live
            st.session_state.pop("replay_fresh", None)
        except Exception as e:  # noqa: BLE001
            st.sidebar.error(str(e)[:600])
            activity("err", "Signing failed to start", str(e)[:2000], phase="prepare", source="prepare", h=h)
        st.rerun()

    # —— Step 3: start / stop ——
    st.sidebar.markdown(_step_label(3, "Start", "done" if r["running"] else "none"))
    _hint("Start root, TLD, leaf, and resolver containers. Only one lab runs at a time, "
          "so starting stops any other zone shape.")
    can_start = bool(r["prepared"])
    if not can_start:
        _hint("Start stays off until the zones of this size are signed (compose + manifests on disk).")
    b3a, b3b = st.sidebar.columns(2)
    if b3a.button("Start", key="btn_up", width="stretch", disabled=not can_start or bool(bench_busy or signing or building or no_images)):
        if not h.prepared():
            st.sidebar.error("Sign the zones first — there is nothing signed to start for this lab.")
            activity("err", "Start blocked", "Lab is not prepared", phase="compose", source="compose", h=h)
        else:
            activity("step", "Starting the lab", f"Image set {h.stack}", phase="compose", source="compose", h=h)
            with st.spinner("Starting…"):
                try:
                    log = driver.up(h)
                    st.session_state.last_log = log[-4000:]
                    st.session_state.pop("last_start_error", None)
                    _log_chunks(log, phase="compose", source="compose", title="up")
                    activity("ok", "Lab is running", "DNS servers and resolver are up.",
                             phase="compose", source="compose", h=h)
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    st.session_state.last_start_error = msg[-4000:]
                    st.sidebar.error(msg[:800])
                    activity("err", "Start failed", msg[-3000:], phase="compose", source="compose", h=h)
            st.rerun()
    if b3b.button("Stop", key="btn_down", width="stretch", disabled=bool(bench_busy)):
        with st.spinner("Stopping…"):
            try:
                driver.down(h)
                st.session_state.pop("last_start_error", None)
                activity("ok", "Lab stopped", "", phase="compose", source="compose", h=h)
            except Exception as e:  # noqa: BLE001
                st.sidebar.error(str(e)[:600])
                activity("err", "Stop failed", str(e)[:2000], phase="compose", source="compose", h=h)
        st.rerun()

    st.sidebar.markdown("---")
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_setup"):
        st.rerun()


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
            _hint("Start the lab in Setup to apply this choice.")
    else:
        _hint("Sign the zones in Setup first.")

    done_q = bool(st.session_state.get("query_log") or st.session_state.get("last_query"))
    st.sidebar.markdown(_step_label(2, "Query", "done" if done_q else "none"))
    _hint("Use the Query tab to send a lookup; Results shows packets.")

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Resolver cache")
    if r["running"]:
        _hint("Empties the resolver cache. The next query is a cold-cache query. Takes a few seconds.")
    else:
        _hint("Start the lab in Setup before flushing.")
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


SUITE_SIZE_CHOICES = [20, 50, 500, 5000]
SUITE_DEFAULT_SIZES = list(suite_mod.BENCH_SIZES)


def _size_label(n: int) -> str:
    return f"{n:,} names" + (" (quick check)" if n == suite_mod.SMOKE_SIZE else "")


BENCH_MODES = {"offline": "Offline (harness)", "simulated": "Simulated (Verisign stack)"}
BENCH_MODE_NOTE = {
    "offline": "Times each signing and verification call on one dedicated core with no network, and counts the DNS "
               "response bytes. Repeatable; no lab or Setup needed.",
    "simulated": "A root, TLD and leaf hierarchy of NSD servers behind a validating Unbound, on the Verisign MTL "
                 "stack in Docker. Real packets and cache behaviour; timings are indicative.",
}


def bench_mode() -> str:
    mode = cfg("bench_mode", "offline")
    return mode if mode in BENCH_MODES else "offline"


def sidebar_benchmark(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("benchmark")
    st.sidebar.markdown("### Benchmark")
    mode = sb(st.sidebar.radio, "bench_mode", "offline", "Mode", options=list(BENCH_MODES),
              format_func=BENCH_MODES.get,
              help="Offline (harness): crypto timings and DNS message sizes on one pinned core, no network. "
                   "Simulated (Verisign stack): a real root, TLD and leaf hierarchy with a validating resolver, "
                   "so you see packets, truncation and cache behaviour.")
    _hint(BENCH_MODE_NOTE[mode])
    st.sidebar.markdown("---")
    if mode == "offline":
        _sidebar_bench_offline(h, stt)
    else:
        _sidebar_bench_simulated(h, stt)


def _sidebar_bench_offline(h: driver.LabHandle, stt: dict) -> None:
    runs = compare_hub.offline_catalogue(ROOT / "results")
    if offline_job.is_running():
        st.sidebar.markdown("🔵  An offline benchmark is running")
    else:
        st.sidebar.markdown(f"🟢  {len(runs)} saved run(s)" if runs else "⚪  No offline run yet")
    preset = sb(st.sidebar.radio, "offline_preset", "quick", "Run size", options=list(offline_job.PRESETS),
                format_func=lambda k: f"{offline_job.PRESETS[k]['title']} · {offline_job.PRESETS[k]['note']}",
                help="Quick check: small zones and few runs, to see the pipeline work. Full run: the three zone "
                     "sizes with enough runs for tight confidence intervals; it takes much longer.")
    _hint("The first run builds the pinned image, which can take a long time. Later runs reuse it.")
    busy = bool(suite_mod.running_suite() or build_job.is_running() or driver.active_prepare_tags())
    if busy:
        st.sidebar.warning("A simulated benchmark, image build or signing job is running. Wait for it to finish.")
    if st.sidebar.button("Run offline benchmark", key="btn_offline", type="primary", width="stretch",
                         disabled=offline_job.is_running() or busy):
        try:
            offline_job.start(preset)
            activity("step", f"Offline benchmark started · {offline_job.PRESETS[preset]['title']}", phase="run",
                     source="offline", h=h)
        except Exception as e:  # noqa: BLE001
            st.sidebar.error(str(e)[:600])
        st.rerun()
    st.sidebar.markdown("---")
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_offline"):
        st.rerun()


def _sidebar_bench_simulated(h: driver.LabHandle, stt: dict) -> None:
    r = readiness(stt)
    suites = suite_mod.list_suites()
    done = [s for s in suites if s["complete"]]
    live = suite_mod.running_suite()
    if live:
        st.sidebar.markdown("🔵  A benchmark is running")
    else:
        st.sidebar.markdown(f"🟢  {len(done)} saved benchmark(s)" if done else "⚪  No benchmark yet")
    _hint(
        "Runs on its own: for each zone size it signs the zones, repeats signing and verification, "
        "starts the lab, measures, and stops it. It only needs the built images (Setup, step 1)."
    )
    bench_stack = cfg("stack", driver.DEFAULT_STACK)
    _hint(f"Uses **Image version `{bench_stack}`** from Setup — build that stack before you run.")

    st.sidebar.markdown("**Run name**")
    label = sb(
        st.sidebar.text_input, "run_label", "", "Name this benchmark",
        placeholder="e.g. panel-final", max_chars=30,
        help="Stored with the date and time, so you can tell benchmarks apart later.",
    )
    clean = driver.sanitize_label(label)[:30]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    _hint(f"Saved as {stamp}{'_' + clean if clean else ''} (time = when you press Run)")

    st.sidebar.markdown("**What to measure**")
    sizes = sb(st.sidebar.multiselect, "bench_sizes", SUITE_DEFAULT_SIZES, gl.label("zone_size"),
               options=SUITE_SIZE_CHOICES, format_func=_size_label,
               help=gl.tip("zone_size") + " Recommended: 50, 500 and 5,000.")
    sc = sb(st.sidebar.multiselect, "bench_scenarios", ["ecdsa", "slh-dsa", "mtl"], gl.label("algorithm") + "s",
            options=list(driver.SCENARIO_ALGS), help=gl.tip("algorithm"))
    sb(st.sidebar.number_input, "bench_trials", 5, gl.label("runs"), min_value=1, max_value=10,
       help=gl.tip("runs") + " The interval of the median needs at least 6 runs to reach 95%; with 5 it covers 94%.")
    sb(st.sidebar.number_input, "bench_sign_trials", suite_mod.DEFAULT_SIGN_TRIALS, gl.label("signing_repeats"),
       min_value=1, max_value=10, help=gl.tip("signing_repeats"))
    sb(st.sidebar.number_input, "bench_sample", suite_mod.DEFAULT_SAMPLE_NAMES, gl.label("signing_sample"),
       min_value=10, max_value=500, step=10, help=gl.tip("signing_sample"))
    sb(st.sidebar.number_input, "bench_jobs", 1, gl.label("parallel_jobs"), min_value=1, max_value=4,
       help=gl.tip("parallel_jobs"))
    sb(st.sidebar.number_input, "bench_queries", 500, gl.label("queries_per_run"), min_value=10, max_value=5000,
       help=gl.tip("queries_per_run"))
    nets = sb(st.sidebar.multiselect, "bench_networks", ["lan"], gl.label("network_profile") + "s",
              options=["lan", "wan", "lossy"], help=gl.tip("network_profile"))
    sb(st.sidebar.selectbox, "bench_udp", 1232, gl.label("edns_buffer"), options=[1232, 4096],
       help=gl.tip("edns_buffer"))

    wl_default = driver.WORKLOAD_DEFAULTS
    with st.sidebar.expander("Workload mix", expanded=False):
        sb(st.number_input, "nx_pct", int(round(wl_default["nx_fraction"] * 100)), gl.label("nx_share"),
           min_value=0, max_value=50, step=1, help=gl.tip("nx_share"))
        sb(st.number_input, "zone_zipf", float(wl_default["zone_zipf_s"]), gl.label("zone_skew"),
           min_value=0.0, max_value=2.0, step=0.1, format="%.1f", help=gl.tip("zone_skew"))
        sb(st.number_input, "name_zipf", float(wl_default["name_zipf_s"]), gl.label("name_skew"),
           min_value=0.0, max_value=2.0, step=0.1, format="%.1f", help=gl.tip("name_skew"))
        sb(st.number_input, "timeout_s", float(wl_default["timeout_s"]), gl.label("query_timeout"),
           min_value=1.0, max_value=30.0, step=1.0, format="%.0f", help=gl.tip("query_timeout"))
    with st.sidebar.expander(gl.label("network_profile") + "s", expanded=False):
        st.caption("lan is the zero-delay baseline. Delay is added towards each tier on the resolver; "
                   "loss applies to the resolver's queries. Only the profiles ticked above are run.")
        for prof in EDITABLE_PROFILES:
            base = driver.PROFILE_DEFAULTS[prof]
            st.markdown(f"**{prof}**")
            cols = st.columns(2)
            for i, (field, flabel) in enumerate(PROFILE_FIELDS):
                with cols[i % 2]:
                    if field == "loss_pct":
                        sb(st.number_input, f"{prof}_{field}", float(base[field]), flabel,
                           min_value=0.0, max_value=20.0, step=0.5, format="%.1f")
                    else:
                        sb(st.number_input, f"{prof}_{field}", int(base[field]), flabel,
                           min_value=0, max_value=500, step=1)
    if not _workload_is_default():
        st.sidebar.caption("Custom workload or network numbers are in use.")
        st.sidebar.button("Restore defaults", on_click=_restore_workload, key="btn_restore_wl",
                          width="stretch")

    _warn_unvalidated(cfg("stack", "draft06"), sc)
    ready = (bool(sizes and sc and nets) and r["images"] and not live and not build_job.is_running()
             and not offline_job.is_running())
    if not r["images"]:
        st.sidebar.warning("Build the images first (Setup, step 1).")
    if r["running"] and not live:
        st.sidebar.caption("The lab running in Setup will be stopped while the benchmark uses the containers.")
    if st.sidebar.button("Run benchmark", key="btn_bench", type="primary", width="stretch", disabled=not ready):
        params = {
            "label": clean, "stack": cfg("stack", "draft06"), "sizes": sorted(int(n) for n in sizes),
            "scenarios": list(sc), "trials": int(cfg("bench_trials", 5)),
            "sign_trials": int(cfg("bench_sign_trials", suite_mod.DEFAULT_SIGN_TRIALS)),
            "sample_names": int(cfg("bench_sample", suite_mod.DEFAULT_SAMPLE_NAMES)),
            "n_queries": int(cfg("bench_queries", 500)), "networks": list(nets),
            "udp": int(cfg("bench_udp", 1232)), "jobs": int(cfg("bench_jobs", 1)),
            "workload": _workload(), "profiles": _profiles(),
        }
        try:
            sid = suite_mod.start(params)
            st.session_state.bench_suite_pick = sid
            activity("step", f"Benchmark started · {sid}", json.dumps(params)[:1500], phase="run", source="suite",
                     h=h)
        except Exception as e:  # noqa: BLE001
            st.sidebar.error(str(e)[:600])
        st.rerun()
    st.sidebar.markdown("---")
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_bench"):
        st.rerun()


def sidebar_reset(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("reset")
    st.sidebar.markdown("### Reset")
    _hint("Pick what to clear in the main area. Nothing is removed until you press the button.")
    st.sidebar.markdown("**Always kept**")
    st.sidebar.markdown("- Docker images  \n- Configuration files  \n- Repository code")
    st.sidebar.markdown("---")
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_reset"):
        st.rerun()


# ─────────────────────────── HOME ───────────────────────────

def _tile_chip(mode: str, stt: dict, h: driver.LabHandle) -> str:
    r = readiness(stt)
    if mode == "setup":
        if r["ready"]:
            return _chip("Ready", "ok")
        if r["count"] == 0:
            return _chip("Start here", "warn")
        return _chip(f"In progress · {r['count']}/3", "info")
    if mode == "benchmark":
        if suite_mod.running_suite() or offline_job.is_running():
            return _chip("Running", "info")
        n = len([s for s in suite_mod.list_suites() if s["complete"]]) + len(compare_hub.offline_catalogue(ROOT / "results"))
        return _chip(f"Ready · {n} saved" if n else "Ready", "ok")
    if mode == "live":
        if not r["ready"]:
            return _chip("Needs Setup", "warn")
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
    if not r["ready"]:
        st.info("**New here? Start with Setup.** Live needs the lab signed and running. Benchmark needs no "
                "Setup in Offline mode, and only the built images in Simulated mode.")

    order = [("setup", "live", "benchmark"), ("compare", "learn", "reset")]
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


# ─────────────────────────── SETUP ───────────────────────────

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
    with st.expander("What signing created", expanded=True):
        wall = summary.get("wall_s")
        wall_txt = f"{wall:.1f}" if isinstance(wall, (int, float)) else (wall or "—")
        st.markdown(
            f"Signed **{summary.get('lab')}** in **{wall_txt}** s — "
            f"leaf zones first, then TLD, then root. "
            f"Files below are what the running lab serves."
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
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def planned_lab(h: driver.LabHandle) -> None:
    """The numbers behind the current Setup choice, and the exact config the lab will use."""
    shape, errors, warnings = _shape()
    with st.container(border=True):
        st.markdown("**Planned lab**")
        if errors or shape is None:
            for e in errors:
                st.error(e)
            return
        scenarios = list(cfg("scenarios") or [])
        stats = driver.shape_stats(shape, scenarios, int(cfg("jobs", 2)))
        m = st.columns(5)
        m[0].metric("Zones", stats["zones"], help="Root + TLDs + leaf zones")
        m[1].metric("Leaf names", f"{stats['names']:,}")
        m[2].metric("Containers", stats["containers"], help="Root, TLD and leaf servers, resolver and client")
        m[3].metric("Signatures", f"~{stats['est_rrsigs']:,}", help="Signatures (RRSIG records) per algorithm, estimated")
        m[4].metric("Signing time", driver.format_duration(stats["est_prepare_s"]),
                    help="Rough estimate from the Medium lab; SLH-DSA dominates.")
        sizes = ", ".join(str(s) for s in shape["sld_sizes"])
        st.caption(
            f"TLDs **{', '.join(shape['tlds'])}** · {shape['sld_count']} leaf zones on "
            f"{shape['sld_servers']} server(s) · names per zone cycle **{sizes}** · "
            f"lab folder **{h.tag}**"
        )
        for w in warnings:
            st.warning(w)
        cfg_path = ECO_DIR / h.config
        if cfg_path.is_file():
            with st.expander("Effective configuration (read-only)"):
                text = cfg_path.read_text(encoding="utf-8")
                st.code(text, language="yaml")
                st.download_button("Download YAML", data=text, file_name=f"{h.lab}.yaml",
                                   mime="text/yaml", key="cfg_dl")


def _embed_html(html: str, height: int | str = "content") -> None:
    """Show a self-contained HTML page in an iframe (``st.iframe`` on current Streamlit)."""
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:  # older Streamlit: needs a fixed height
        import streamlit.components.v1 as components
        components.html(html, height=int(height) if isinstance(height, int) else 780)


def _clock(seconds: float) -> str:
    m, s = divmod(int(max(0, seconds)), 60)
    return f"{m}:{s:02d}"


_STATE_TEXT = {"waiting": "waiting", "signing": "signing…", "done": "signed"}


@st.fragment(run_every=1.0)
def _prepare_live_body(h: driver.LabHandle) -> None:
    """Polls the Prepare job once a second and redraws the monitor. Hands over when the job ends."""
    job = prepare_job.status(h)
    if job.get("state") not in prepare_job.ACTIVE_STATES:
        st.rerun()  # whole page: the sidebar takes over the result and the replay opens
        return
    started = float(job.get("started") or time.time())
    live = prepare_job.live_state(prepare_job.events(h), expected=job.get("scenarios"))
    elapsed = time.time() - started

    if not live["scenarios"]:
        st.progress(0.0, text=f"Starting the signing container… {_clock(elapsed)}")
    else:
        done, total = live["zones_done"], live["zones_total"]
        st.progress(min(1.0, live["fraction"]), text=f"{done} of {total} zones signed · {_clock(elapsed)} elapsed")

        pills = []
        for x in live["scenarios"]:
            pills.append(f"**{x['scenario']}** {_STATE_TEXT[x['state']]} ({x['done']}/{x['total']})")
        pills += [f"**{s}** waiting" for s in live["pending"]]
        st.markdown("  ·  ".join(pills))

        cur = next(x for x in live["scenarios"] if x["scenario"] == live["current"])
        algs = cur["algorithms"]
        if algs:
            st.caption("Signing **{}** with: ".format(cur["scenario"]) + " · ".join(
                f"{prepare_job.TIER_LABEL[t].lower()} {algs[t]}" for t in prepare_job.TIER_ORDER if t in algs))

        cols = st.columns(len(prepare_job.TIER_ORDER))
        for col, tier in zip(cols, prepare_job.TIER_ORDER):
            t = cur["tiers"].get(tier) or {"total": 0, "done": 0, "signing": 0}
            col.metric(prepare_job.TIER_LABEL[tier], f"{t['done']} / {t['total']}",
                       f"{t['signing']} signing now" if t["signing"] else None, delta_color="off")

        left, right = st.columns([3, 2])
        with left:
            # natural size: stretching a small graph to the column makes the boxes huge and the text tiny
            st.graphviz_chart(prepare_job.live_dot(cur))
        with right:
            tier_rank = {t: i for i, t in enumerate(prepare_job.TIER_ORDER)}
            rows = []
            for z in sorted(cur["zones"], key=lambda z: (tier_rank.get(z["tier"], 9), z["origin"])):
                if z["state"] == "done":
                    took = f"{float(z.get('sign_s') or 0):.1f} s"
                elif z["state"] == "signing" and z.get("elapsed") is not None:
                    took = f"{z['elapsed']:.0f} s…"
                else:
                    took = ""
                rows.append({"Zone": z["origin"], "Tier": prepare_job.TIER_LABEL.get(z["tier"], z["tier"]),
                             "State": _STATE_TEXT[z["state"]],
                             "RRSIGs": int(z["rrsigs"]) if z.get("rrsigs") is not None else None,
                             "Sign time": took})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(420, 38 + 35 * len(rows)))

    st.caption("Zones sign in parallel (Sign jobs), tier by tier: leaf zones first, then each TLD with its "
               "children's DS records, then the root. RRSIG counts and times are measured as each zone finishes.")
    with st.expander("Signer output"):
        st.code(prepare_job.log_text(h, 4000) or "(nothing printed yet)", language="text")
    if st.button("Cancel signing", key="btn_prep_cancel"):
        prepare_job.cancel(h)
        activity("info", "Signing cancelled", "", phase="prepare", source="prepare", h=h)
        st.rerun()


@st.fragment(run_every=2.0)
def _build_live_body() -> None:
    """Polls the image build every two seconds and shows its log. Hands over when it ends."""
    job = build_job.status()
    if job.get("state") not in build_job.ACTIVE_STATES:
        st.rerun()  # whole page: the sidebar logs the result
        return
    elapsed = time.time() - float(job.get("started") or time.time())
    st.progress(0.0, text=f"Building images for {job.get('stack')} · {_clock(elapsed)} elapsed")
    st.caption("A first build compiles OpenSSL, liboqs, MTL, NSD and Unbound from source and can take tens of "
               "minutes. Later builds reuse Docker's cache and take seconds. You can leave this page.")
    st.code(build_job.log_text(5000) or "(starting…)", language="text")
    if st.button("Cancel build", key="btn_build_cancel"):
        build_job.cancel()
        activity("info", "Build cancelled", "", phase="build", source="compose")
        st.rerun()


def build_live_panel() -> None:
    st.markdown("### Building images")
    _build_live_body()


def prepare_live_panel(h: driver.LabHandle) -> None:
    st.markdown("### Signing live")
    _prepare_live_body(h)


def setup_replay(h: driver.LabHandle, *, key: str) -> None:
    """Animated replay of how the hierarchy was signed (built from the signing manifests on disk)."""
    manifests = driver.list_manifests(h) if (prepare_metrics_visible(h) and h.prepared()) else {}
    if not manifests:
        st.info("The signing replay appears here after you sign the zones (Step 2 in the sidebar).")
        return
    names = compare.order_scenarios(manifests)
    pick = names[0]
    if len(names) > 1:
        pick = st.radio(
            "Algorithm", names, horizontal=True, key=f"replay_pick_{key}",
            index=names.index("mtl") if "mtl" in names else 0,
            help="Each algorithm signs the same zones. Compare how many signatures each one needs.",
        )
    size_key = cfg("zone_size", "small")
    size_label = driver.ZONE_SIZE_PRESETS.get(size_key, {}).get("label", str(size_key))
    scene = prepare_story.build_scene(
        manifests[pick], lab=h.lab, scenario=pick,
        zones_dir=h.work / "zones" / pick, size_label=size_label,
    )
    _embed_html(prepare_story.render_html(scene), height="content")
    st.caption(
        "**Want more records?** Change **Zone size** in the sidebar (Small, Medium, Large, or **Custom…** for "
        "TLDs, leaf zones and names per leaf zone), then press **Sign zones**. The replay always shows the last signing run."
    )
    st.caption(
        "Time is compressed so every tier takes the same few seconds; the real signing times are on the labels. "
        "Record counts are the RRSIGs found in the signed zone files; the tree is drawn from that count "
        "(one leaf per signed RRset, one perfect tree per 1-bit of the count)."
    )


def setup_overview(h: driver.LabHandle, stt: dict) -> None:
    imgs = stt.get("images") or {}
    containers = stt.get("containers") or {}
    r = readiness(stt)

    if st.session_state.get("replay_fresh") and h.prepared():
        with st.container(border=True):
            top, hide = st.columns([6, 1])
            top.markdown("**Signing replay** · how the hierarchy was just signed")
            hide.button("Hide", key="hide_replay", on_click=lambda: st.session_state.pop("replay_fresh", None))
            setup_replay(h, key="fresh")

    err = st.session_state.get("last_start_error")
    if err:
        with st.container(border=True):
            st.error("**Start lab failed** (full Docker message below). Fix the conflict, then Start again.")
            st.code(err[-2500:], language="text")
            if st.button("Dismiss start error", key="dismiss_start_err"):
                st.session_state.pop("last_start_error", None)
                st.rerun()

    sc = _scenario_list(h)
    c = st.columns(4)
    c[0].metric("Docker images", f"{sum(imgs.values())}/{len(imgs) or 3}")
    c[1].metric("Zones signed", "yes" if r["prepared"] else "no")
    c[2].metric("Containers up", str(sum(1 for v in containers.values() if v)))
    scen_label = "Algorithm" if len(sc) <= 1 else "Algorithms"
    c[3].metric(scen_label, ", ".join(sc) if sc else (r["active"] or "—"))
    active_note = f" · active **{r['active']}**" if r["active"] and r["running"] else ""
    st.caption(
        f"Lab **{h.lab}** · zone size **{cfg('zone_size', 'small')}** · "
        f"scenarios **{', '.join(sc) or '—'}**{active_note}"
    )

    if r["ready"]:
        with st.container(border=True):
            st.success("Setup complete. The lab is signed, running and ready.")
            a, b, _ = st.columns([1, 1, 2])
            a.button("Continue to Live →", on_click=_go, args=("live",), key="setup_to_live",
                     type="primary", width="stretch")
            b.button("Continue to Benchmark →", on_click=_go, args=("benchmark",),
                     key="setup_to_bench", width="stretch")
    else:
        nxt = ("Build or pull the images (Step 1)" if not r["images"]
               else "Sign the zones (Step 2)" if not r["prepared"]
               else "Press Start to launch the containers (Step 3)")
        st.info(f"**Next:** {nxt} — use the sidebar.")

    planned_lab(h)

    left, right = st.columns(2)
    with left:
        st.markdown("**Images**")
        for name, ok in imgs.items():
            repo = name.split(":")[0]
            tag = name.split(":")[1] if ":" in name else ""
            st.write(("🟢" if ok else "🔴"), IMAGE_LABELS.get(repo, repo) + (f" · {tag}" if tag else ""))
    with right:
        st.markdown("**Running services**")
        if containers:
            for name, ok in sorted(containers.items()):
                st.write(("🟢" if ok else "🔴"), name)
        else:
            st.caption("No services yet.")


def setup_results(h: driver.LabHandle) -> None:
    if not (prepare_metrics_visible(h) and h.prepared()):
        st.info("Signing results appear here after you sign the zones.")
        return
    _render_prepare_summary(h)
    rows = driver.manifest_rows(h)
    if not rows:
        return
    df = pd.DataFrame(rows)
    scenarios = sorted(df["scenario"].unique())
    pick = st.multiselect("Algorithms", scenarios, default=scenarios, key="setup_res_pick")
    view = df[df["scenario"].isin(pick)] if pick else df
    wall = st.session_state.get("prepare_wall_s") or driver.load_session(h).get("prepare_wall_s")
    if wall:
        st.metric("Signing wall time", f"{wall:.1f}s")
    st.dataframe(
        view[["scenario", "tier", "origin", "sign_seconds", "verify_seconds", "signed_bytes", "rrsig_count"]],
        width="stretch", hide_index=True,
    )
    agg = view.groupby("scenario", as_index=False).agg(
        sign_seconds=("sign_seconds", "sum"), verify_seconds=("verify_seconds", "sum"),
    )
    c1, c2 = st.columns(2)
    c1.plotly_chart(px.bar(agg, x="scenario", y="sign_seconds", title="Sign (s)"), width="stretch")
    c2.plotly_chart(px.bar(agg, x="scenario", y="verify_seconds", title="Zone verify (s)"),
                    width="stretch")


def setup_page(h: driver.LabHandle, stt: dict) -> None:
    _page_header("setup", h, stt)
    if build_job.is_running():
        build_live_panel()
        return
    if prepare_job.is_running(h):
        # Signing is in progress: show it live. The other tabs describe the previous Prepare, so they
        # wait until it ends (the page reruns by itself and opens the replay).
        prepare_live_panel(h)
        return
    overview, replay, results, activity = st.tabs(["Overview", "Signing replay", "Results", "Activity log"])
    with overview:
        setup_overview(h, stt)
    with replay:
        setup_replay(h, key="tab")
    with results:
        setup_results(h)
    with activity:
        activity_tab(h)


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


def live_page(h: driver.LabHandle, stt: dict) -> None:
    _page_header("live", h, stt)
    query, dig_tab, cache, results = st.tabs(["Query", "dig terminal", "Cold vs warm cache", "Results"])
    with query:
        query_panel(h, stt)
    with dig_tab:
        dig_panel(h, stt)
    with cache:
        cache_panel(h, stt)
    with results:
        live_results(h)
    _activity_expander(h)


# ─────────────────────────── BENCHMARK ───────────────────────────

def _suite_params() -> dict:
    return {
        "sizes": sorted(int(n) for n in (cfg("bench_sizes") or SUITE_DEFAULT_SIZES)),
        "scenarios": list(cfg("bench_scenarios") or ["ecdsa", "slh-dsa", "mtl"]),
        "trials": int(cfg("bench_trials", 5)),
        "sign_trials": int(cfg("bench_sign_trials", suite_mod.DEFAULT_SIGN_TRIALS)),
        "sample_names": int(cfg("bench_sample", suite_mod.DEFAULT_SAMPLE_NAMES)),
        "n_queries": int(cfg("bench_queries", 500)),
        "jobs": int(cfg("bench_jobs", 1)),
        "networks": list(cfg("bench_networks") or ["lan"]),
        "udp": int(cfg("bench_udp", 1232)),
    }


def _bench_plan() -> None:
    p = _suite_params()
    label = driver.sanitize_label(cfg("run_label", ""))[:30]
    with st.container(border=True):
        st.markdown("**Planned benchmark**")
        a = st.columns(5)
        a[0].metric(gl.label("zone_size") + "s", len(p["sizes"]), help=gl.tip("zone_size"))
        a[1].metric(gl.label("algorithm") + "s", len(p["scenarios"]), help=gl.tip("algorithm"))
        a[2].metric(gl.label("runs"), p["trials"], help=gl.tip("runs"))
        a[3].metric(gl.label("signing_repeats"), p["sign_trials"], help=gl.tip("signing_repeats"))
        a[4].metric(gl.label("queries_per_run"), p["n_queries"], help=gl.tip("queries_per_run"))
        rows = []
        for n in p["sizes"]:
            s = driver.shape_stats(suite_mod.bench_shape(n), p["scenarios"], p["jobs"])
            rows.append({gl.label("zone_size"): _size_label(n), gl.label("zones"): s["zones"],
                         gl.label("leaf_zones"): suite_mod.bench_shape(n)["sld_count"],
                         gl.label("est_rrsigs"): s["est_rrsigs"],
                         gl.label("est_signing"): driver.format_duration(suite_mod.estimate_seconds(
                             [n], p["scenarios"], p["jobs"], p["sign_trials"], p["sample_names"]))})
        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, width="stretch", hide_index=True, column_config=gl.column_config(df.columns))
        total = suite_mod.estimate_seconds(p["sizes"], p["scenarios"], p["jobs"], p["sign_trials"], p["sample_names"])
        passes = len(p["scenarios"]) * len(p["networks"]) * p["trials"] * 2 * max(1, len(p["sizes"]))
        st.caption(
            f"Signing takes about **{driver.format_duration(total)}** in total, plus measuring "
            f"({passes} cold/warm passes, about {passes * p['n_queries']:,} queries). "
            f"Name **{label or '(none: date and time only)'}** · network profiles {', '.join(p['networks'])} · "
            f"EDNS buffer {p['udp']} B"
        )
        w = _workload()
        st.caption(
            f"Workload: NXDOMAIN **{round(w['nx_fraction'] * 100)}%** · zone skew **{w['zone_zipf_s']:g}** · "
            f"name skew **{w['name_zipf_s']:g}** · query timeout **{w['timeout_s']:g} s**"
            + ("" if _workload_is_default() else " · _custom values_")
        )
        shown = {n: {**driver.PROFILE_DEFAULTS[n], **_profiles().get(n, {})} for n in p["networks"]}
        if shown:
            st.dataframe(
                pd.DataFrame(shown).T.rename(columns=dict(PROFILE_FIELDS)).rename_axis(gl.label("network_profile")),
                width="stretch",
            )


def _cancel_suite(suite_id: str) -> None:
    suite_mod.cancel(suite_id)
    activity("warn", f"Benchmark cancelled · {suite_id}", phase="run", source="suite")


@st.fragment(run_every=3)
def _suite_progress(suite_id: str) -> None:
    s = suite_mod.status(suite_id)
    state = s.get("state", "unknown")
    if state not in ("running", "starting"):
        st.rerun()  # whole page: the sidebar and Results pick up the finished suite
    total = max(1, int(s.get("total", 1)))
    done = int(s.get("done", 0))
    elapsed = time.time() - float(s.get("started") or time.time())
    with st.container(border=True):
        st.markdown(f"**Benchmark running** · {suite_id}")
        st.progress(min(done / total, 1.0), text=f"{done} of {total} steps · {s.get('step', '')}")
        st.caption(f"Elapsed {driver.format_duration(elapsed).lstrip('~')}. You can leave this page; the benchmark "
                   "keeps running and the Results tab fills in when it finishes.")
        st.button("Cancel benchmark", on_click=_cancel_suite, args=(suite_id,), key="btn_cancel_suite")
        log = SUITES_DIR / suite_id / "progress.log"
        if log.is_file():
            with st.expander("Progress log"):
                st.code(log.read_text(encoding="utf-8", errors="replace")[-3000:], language="text")


def benchmark_run(h: driver.LabHandle, stt: dict) -> None:
    r = readiness(stt)
    live = suite_mod.running_suite()
    if live:
        _suite_progress(live["id"])
        return
    last = next(iter(suite_mod.list_suites()), None)
    if last and last["state"] in ("failed", "stopped", "cancelled"):
        s = suite_mod.status(last["id"])
        st.warning(f"The last benchmark ({last['title']}) {s.get('state')}: {s.get('error') or s.get('step', '')}")
    if not r["images"]:
        st.warning("The lab images are not built yet. Build them once in **Setup** (step 1); the benchmark "
                   "does the rest itself.")
        st.button("Go to Setup", on_click=_go, args=("setup",), key="bench_to_setup")
    st.caption("Set the options in the sidebar, then press **Run benchmark**. It signs, starts and stops the "
               "lab on its own, one zone size after another.")
    _bench_plan()


def _fmt_ci(med: float | None, lo: float | None, hi: float | None) -> str:
    if med is None or pd.isna(med):
        return "—"
    base = compare.fmt_seconds(med)
    if lo is None or hi is None or pd.isna(lo) or pd.isna(hi) or lo == hi:
        return base
    return f"{base} ({compare.fmt_seconds(lo)}–{compare.fmt_seconds(hi)})"


def _signing_stats_table(stats: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        gl.label("algorithm"): stats["scenario"],
        gl.label("signing_repeats"): stats["trials"],
        "Sample (names)": stats["sample_names"],
        "Sign per RRSIG (ms)": stats["sign_ms_per_rrsig"].round(3),
        "Fixed per zone (s)": stats["sign_fixed_s"].round(3),
        "Zone signing time, estimated (CI of the median)": [_fmt_ci(m, a, b) for m, a, b in
                                             zip(stats["est_sign_s"], stats["est_sign_lo"], stats["est_sign_hi"])],
        "Zone verification time, estimated (CI of the median)": [_fmt_ci(m, a, b) for m, a, b in
                                               zip(stats["est_verify_s"], stats["est_verify_lo"], stats["est_verify_hi"])],
        "Full zones sign, measured once": stats["full_sign_s"].map(compare.fmt_seconds),
        "Full zones verify, measured once": stats["full_verify_s"].map(compare.fmt_seconds),
        gl.label("signed_zone_size"): stats["signed_bytes"].map(compare.fmt_bytes),
    })


def _suite_settings(sd: dict) -> None:
    s = sd["suite"]
    with st.expander("Settings and environment recorded for this benchmark"):
        st.markdown(
            f"**Zone sizes** {', '.join(f'{n:,}' for n in s['sizes'])} names · **Algorithms** "
            f"{', '.join(s['scenarios'])} · **Stack** {s['stack']}  \n"
            f"**Runs** {s['trials']} (cold then warm cache) · **Queries per run** {s['n_queries']} · "
            f"**Network profiles** {', '.join(s['networks'])} · **EDNS buffer** {s['udp']} B  \n"
            f"**Signing repeats** {s['sign_trials']} on samples of {s['sample_names']} names (zone time is an "
            f"extrapolation, the full zones are also signed once)"
        )
        wl = s.get("workload") or driver.WORKLOAD_DEFAULTS
        st.markdown(
            f"**Workload** NXDOMAIN {round(float(wl['nx_fraction']) * 100)}% · zone skew {wl['zone_zipf_s']} · "
            f"name skew {wl['name_zipf_s']} · query timeout {wl['timeout_s']} s · seeded (same seed for every algorithm)"
        )
        hier = [{gl.label("zone_size"): f"{n:,}", gl.label("leaf_zones"): suite_mod.bench_shape(n)["sld_count"],
                 "Names per leaf zone": ", ".join(str(x) for x in suite_mod.bench_shape(n)["sld_sizes"])}
                for n in s["sizes"]]
        hier_df = pd.DataFrame(hier)
        st.dataframe(hier_df, width="stretch", hide_index=True, column_config=gl.column_config(hier_df.columns))
        first = next(iter(sd["runs"].values()), {})
        env = first.get("environment") or {}
        if env:
            dock = env.get("docker") or {}
            st.markdown(
                f"**Environment** Docker {dock.get('ServerVersion')} · {dock.get('NCPU')} CPUs · "
                f"{round((dock.get('MemTotal') or 0) / 1024 ** 3, 1)} GiB · host {s.get('host', {}).get('platform', '')}  \n"
                f"**Pinned images** " + ", ".join(sorted({(v.get('image') or '') for v in (env.get('images') or {}).values()}))
            )
        cpus = ((first.get("lab") or {}).get("cpusets")) or {}
        if cpus:
            st.caption(f"CPU pinning: {cpus}")
        profs = {n: v for n, v in (s.get("profiles") or {}).items() if n in s["networks"]}
        if profs:
            st.dataframe(pd.DataFrame(profs).T.rename_axis(gl.label("network_profile")), width="stretch")


RESULT_COLUMNS = {
    "scenario": gl.label("algorithm"), "profile": gl.label("network_profile"), "pass": gl.label("udp_cache"),
    "udp_limit": gl.label("edns_buffer") + " (B)", "trials": gl.label("runs"), "valid_trials": gl.label("valid_runs"),
    "median_ms": gl.label("latency_p50"), "p95_ms": gl.label("latency_p95"),
    "upstream_bytes": gl.label("resolver_auth_bytes"), "upstream_queries": gl.label("resolver_auth_queries"),
    "upstream_truncated": gl.label("tc_responses"), "validated_pct": gl.label("validated_pct"),
    "failed": gl.label("failed"), "servfail": gl.label("servfail"), "timeouts": gl.label("timeouts"),
}


def benchmark_results(h: driver.LabHandle) -> None:
    suites = [s for s in suite_mod.list_suites() if s["complete"]]
    if not suites:
        st.info("No finished benchmark yet. Choose the options in the sidebar and press **Run benchmark**.")
        return
    by = {s["id"]: s for s in suites}
    if st.session_state.get("bench_suite_pick") not in by:
        st.session_state.pop("bench_suite_pick", None)
    pick = st.selectbox("Benchmark", list(by), key="bench_suite_pick", format_func=lambda i: by[i]["title"])
    sd = compare.load_suite(by[pick]["path"])
    if sd is None:
        st.warning("This benchmark has no stored results.")
        return
    s = sd["suite"]
    m = st.columns(4)
    m[0].metric("Name", s.get("label") or "—")
    m[1].metric("Started", by[pick]["when"])
    m[2].metric("Duration", driver.format_duration(s.get("seconds", 0)).lstrip("~"))
    m[3].metric(gl.label("zone_size") + "s", ", ".join(f"{n:,}" for n in sd["datasets"]), help=gl.tip("zone_size"))
    _suite_settings(sd)
    st.button("Open in Compare →", on_click=_open_in_compare, args=(pick,), key="bench_to_compare")

    stats = compare.signing_trial_stats(sd["trials"], sd["full"])
    tabs = st.tabs([f"{n:,} names" for n in sd["datasets"]])
    for tab, n in zip(tabs, sd["datasets"]):
        with tab:
            st.markdown("**Signing and verification**")
            ds = stats[stats["dataset"] == n]
            if ds.empty:
                st.caption("No signing data.")
            else:
                tbl = _signing_stats_table(ds)
                st.dataframe(tbl, width="stretch", hide_index=True, column_config=gl.column_config(tbl.columns, {
                    "Zone signing time, estimated (CI of the median)": gl.tip("signing_time"),
                    "Zone verification time, estimated (CI of the median)": gl.tip("verification_time"),
                    "Full zones sign, measured once": "The complete zones the lab serves, signed one time.",
                    "Full zones verify, measured once": "The complete zones, validated one time.",
                    "Sign per RRSIG (ms)": "Time to produce one signature (RRSIG), from the signing repeats.",
                    "Fixed per zone (s)": "Time per zone that does not depend on the number of signatures.",
                    "Sample (names)": gl.tip("signing_sample")}))
                jobs = int(s.get("jobs", 1))
                st.caption("Estimated = from the repeated sample trials (run one at a time), extrapolated to every "
                           "zone of the dataset. Measured once = the full zones that the lab served, signed one time "
                           f"with {jobs} parallel job(s)"
                           + ("" if jobs == 1 else "; with more than one job these timings are inflated by contention")
                           + ".")
            summary = sd["bench"].get(n)
            if summary is not None and not summary.empty:
                st.markdown("**Resolver measurements**", help="What a validating Unbound did with the replayed queries: "
                            "latency, bytes to the authoritative servers, truncation and failures.")
                cols = [c for c in ["scenario", "profile", "pass", "udp_limit", "trials", "valid_trials", "median_ms",
                                    "p95_ms", "upstream_bytes", "upstream_queries", "upstream_truncated",
                                    "validated_pct", "failed", "servfail", "timeouts"] if c in summary.columns]
                shown = summary[cols].rename(columns=RESULT_COLUMNS)
                st.dataframe(shown, width="stretch", hide_index=True,
                             column_config=gl.column_config(shown.columns, {"Runs": gl.tip("runs")}))
                for note in compare.lookup_problems(summary):
                    st.warning(note)
                _bench_charts(summary)
    report_html = by[pick]["path"] / "report.html"
    report_md = by[pick]["path"] / "report.md"
    if not report_html.is_file() or not report_md.is_file():
        try:
            from live_lab import suite_report
            suite_report.write(by[pick]["path"])
            report_html = by[pick]["path"] / "report.html"
            report_md = by[pick]["path"] / "report.md"
        except Exception as e:  # noqa: BLE001
            st.caption(f"Report not generated yet: {e}")
    d0, d1, d2 = st.columns(3)
    if report_html.is_file():
        d0.download_button("Download report (HTML)", data=report_html.read_bytes(),
                           file_name=f"{pick}-report.html", mime="text/html", key="bench_dl_report_html")
    elif report_md.is_file():
        d0.download_button("Download report (Markdown)", data=report_md.read_text(encoding="utf-8"),
                           file_name=f"{pick}-report.md", mime="text/markdown", key="bench_dl_report_md")
    d1.download_button("Download signing trials (CSV)", data=sd["trials"].to_csv(index=False),
                       file_name=f"{pick}-signing-trials.csv", mime="text/csv", key="bench_dl_trials")
    d2.download_button("Download full-zone signing (CSV)", data=sd["full"].to_csv(index=False),
                       file_name=f"{pick}-signing-full.csv", mime="text/csv", key="bench_dl_full")
    if report_html.is_file():
        st.caption(f"Reproducible report on disk: `{report_html}` (also `report.md` and `figures/`).")


def _bench_charts(summary: pd.DataFrame) -> None:
    if summary.empty:
        return
    base = {"x": "scenario", "color": "pass", "barmode": "group"}
    facet = {"facet_col": "profile"} if "profile" in summary and summary["profile"].nunique() > 1 else {}
    if "median_ms" in summary:
        st.plotly_chart(
            px.bar(summary, y="median_ms", title=gl.label("latency_p50"), **base, **facet),
            width="stretch",
        )
    if "upstream_bytes" in summary:
        st.plotly_chart(
            px.bar(summary, y="upstream_bytes", title=gl.label("resolver_auth_bytes") + " per pass", **base, **facet),
            width="stretch",
        )


def _open_in_compare(suite_id: str) -> None:
    _store().update(cmp_src="live", cmp_view="algorithms", cmp_a_live=suite_id)
    for k in ("w_cmp_src", "w_cmp_view", "w_cmp_a_live"):
        st.session_state.pop(k, None)
    _go("compare")


def _offline_plan(preset: str) -> None:
    import yaml
    path = ROOT / offline_job.PRESETS[preset]["config"]
    try:
        conf = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st.warning(f"Cannot read {path.name}.")
        return
    with st.container(border=True):
        st.markdown(f"**Planned run · {offline_job.PRESETS[preset]['title']}**")
        a = st.columns(4)
        a[0].metric(gl.label("zone_size") + "s", len(conf.get("datasets", [])), help=gl.tip("zone_size"))
        a[1].metric(gl.label("algorithm") + "s", len(conf.get("modes", [])), help=gl.tip("algorithm"))
        a[2].metric(gl.label("runs"), conf.get("trials", "—"), help=gl.tip("runs"))
        a[3].metric(gl.label("queries_per_run"), (conf.get("wire", {}).get("workload", {}) or {}).get("n_queries", "—"),
                    help=gl.tip("queries_per_run"))
        st.caption("Zone sizes (names): " + ", ".join(f"{d['n_names']:,}" for d in conf.get("datasets", [])) +
                   " · Algorithms: " + ", ".join(compare_hub.short(m) for m in conf.get("modes", [])) +
                   f" · Noise-check control: {compare_hub.short(conf.get('control', '—'))} · Config `{path.name}`")


@st.fragment(run_every=3)
def _offline_progress() -> None:
    """Bar, clock, and the log. The log body is built only while it is open, so a closed log is not redrawn."""
    s = offline_job.status()
    if s.get("state") not in offline_job.ACTIVE_STATES:
        st.rerun()  # whole page: the sidebar and Results pick up the finished run
        return
    elapsed = time.time() - float(s.get("started") or time.time())
    fraction, text = offline_job.display_progress(s)
    st.progress(fraction, text=text)
    st.caption(f"Elapsed {driver.format_duration(elapsed).lstrip('~')}. You can leave this page; the benchmark "
               "keeps running and Results fills in when it finishes.")
    log = st.expander("Progress log", expanded=False, key="offline_progress_log", on_change="rerun")
    if log.open:
        log.code(offline_job.log_text(4000) or "(starting…)", language="text")


def _offline_running(s: dict) -> None:
    with st.container(border=True):
        st.markdown(f"**Offline benchmark running** · {offline_job.PRESETS[s.get('preset', 'quick')]['title']}")
        _offline_progress()
        if st.button("Cancel benchmark", key="btn_cancel_offline"):
            offline_job.cancel()
            activity("warn", "Offline benchmark cancelled", phase="run", source="offline")
            st.rerun()


def _finish_offline_job() -> None:
    s = offline_job.status()
    if s.get("state") in offline_job.ACTIVE_STATES or s.get("handled"):
        return
    if s.get("state") == "done":
        activity("ok", "Offline benchmark finished", s.get("run_id") or "", phase="run", source="offline")
    elif s.get("state") in ("failed", "stopped"):
        activity("err", "Offline benchmark failed", (s.get("error") or "")[-2000:], phase="run", source="offline")
    if s:
        offline_job.mark_handled()


def benchmark_offline_run() -> None:
    _finish_offline_job()
    if offline_job.is_running():
        _offline_running(offline_job.status())
        return
    s = offline_job.status()
    if s.get("state") in ("failed", "stopped", "cancelled"):
        st.warning(f"The last offline benchmark {s['state']}: {s.get('error') or s.get('step', '')}")
        if offline_job.log_text():
            with st.expander("Progress log"):
                st.code(offline_job.log_text(4000), language="text")
    st.caption("Choose the run size in the sidebar, then press **Run offline benchmark**. It builds the pinned image "
               "if needed and runs on a dedicated core with no network.")
    _offline_plan(cfg("offline_preset", "quick"))


def _open_offline_in_compare(run_id: str) -> None:
    _store().update(cmp_src="offline", cmp_view="algorithms", cmp_a_offline=run_id)
    for k in ("w_cmp_src", "w_cmp_view", "w_cmp_a_offline"):
        st.session_state.pop(k, None)
    _go("compare")


@st.cache_data(show_spinner=False)
def _offline_file_bytes(path: str, mtime: float) -> bytes:
    return Path(path).read_bytes()


@st.cache_data(show_spinner=False)
def _offline_bundle_bytes(path: str, mtime: float) -> bytes:
    return compare_hub.offline_bundle(Path(path))


def _offline_downloads(path: Path, rid: str, ds: str, table: pd.DataFrame | None) -> None:
    """The same kind of downloads as a simulated run: the report, the table on screen, and the trial file."""
    report_html = path / "report.html"
    report_md = path / "report.md"
    trials = path / "trials.csv"
    stamp = (path / "summary.json").stat().st_mtime if (path / "summary.json").is_file() else 0
    d0, d1, d2, d3 = st.columns(4)
    if report_html.is_file():
        d0.download_button("Download report (HTML)", data=_offline_file_bytes(str(report_html), report_html.stat().st_mtime),
                           file_name=f"{rid}-report.html", mime="text/html", key=f"bench_off_html_{rid}")
    elif report_md.is_file():
        d0.download_button("Download report (Markdown)", data=_offline_file_bytes(str(report_md), report_md.stat().st_mtime),
                           file_name=f"{rid}-report.md", mime="text/markdown", key=f"bench_off_md_{rid}")
    if table is not None and not table.empty:
        d1.download_button("Download this comparison (CSV)", data=table.to_csv(index=False),
                           file_name=f"{rid}-{ds}-comparison.csv", mime="text/csv", key=f"bench_off_cmp_{rid}_{ds}")
    if trials.is_file():
        d2.download_button("Download signing trials (CSV)", data=_offline_file_bytes(str(trials), trials.stat().st_mtime),
                           file_name=f"{rid}-trials.csv", mime="text/csv", key=f"bench_off_trials_{rid}")
    bundle = _offline_bundle_bytes(str(path), stamp)
    if bundle:
        d3.download_button("Download all results (ZIP)", data=bundle, file_name=f"{rid}-results.zip",
                           mime="application/zip", key=f"bench_off_zip_{rid}")
    if report_html.is_file():
        st.caption(f"Reproducible report on disk: `{report_html}` (also `report.md`, `figures/`, and the CSV tables). "
                   "The zip is that report plus the result tables. The per-operation log stays in the run folder.")


def benchmark_offline_results() -> None:
    cat = compare_hub.offline_catalogue(ROOT / "results")
    if not cat:
        st.info("No offline run yet. Press **Run offline benchmark** in the sidebar.")
        return
    by = {e["id"]: e for e in cat}
    rid = st.selectbox("Run", list(by), key="bench_offline_pick", format_func=lambda i: by[i]["title"])
    entry = by[rid]
    path = entry["path"]
    run = _cmp_offline_run(str(path), (path / "summary.json").stat().st_mtime)
    facts = entry["facts"]
    m = st.columns(4)
    m[0].metric(gl.label("runs"), facts["Runs"], help=gl.tip("runs"))
    m[1].metric("Stack", facts["Stack profile"], help="The pinned software versions (OpenSSL, liboqs, MTL) the run used.")
    m[2].metric("CPU core(s) used", facts["Cores used"].strip("[]"),
                help="Every timing ran on this dedicated core, so results are repeatable.")
    m[3].metric(gl.label("zone_size") + "s", len(entry["datasets"]), help=gl.tip("zone_size"))
    datasets = [d for d in run.dataset_order if d in set(entry["datasets"])] or entry["datasets"]
    ds = st.segmented_control(gl.label("zone_size"), datasets, default=datasets[-1], key="bench_offline_ds",
                              help=gl.tip("zone_size")) or datasets[-1]
    frame = compare_hub.offline_frame(run, ds)
    table = None
    if frame.empty:
        st.info("This run has no data for that zone size.")
    else:
        keys = list(frame.key)
        base = "slh-dsa" if "slh-dsa" in keys else keys[0]
        paired = compare_hub.offline_paired(run, ds, base) if len(keys) > 1 else None
        table = compare_hub.algorithm_table(frame, base, paired)
        st.dataframe(table, width="stretch", hide_index=True, column_config=gl.column_config(table.columns))
        st.caption(f"Each ratio is against {compare_hub.short(base)}; lower is better. "
                   f"{compare_hub.stats_label(run)} Details: {facts['Zone sizes']}.")
        st.button("Open in Compare →", on_click=_open_offline_in_compare, args=(rid,), key="bench_offline_to_cmp")
    _offline_downloads(path, rid, ds, table)


def benchmark_page(h: driver.LabHandle, stt: dict) -> None:
    _page_header("benchmark", h, stt)
    mode = bench_mode()
    st.caption(f"**{BENCH_MODES[mode]}** · {BENCH_MODE_NOTE[mode]}")
    run_tab, results = st.tabs(["Run", "Results"])
    with run_tab:
        benchmark_offline_run() if mode == "offline" else benchmark_run(h, stt)
    with results:
        benchmark_offline_results() if mode == "offline" else benchmark_results(h)
    _activity_expander(h)


# ─────────────────────────── COMPARE ───────────────────────────

CMP_VIEWS = {"algorithms": "Algorithms side by side", "runs": "Run against run", "evolution": "Zone updates"}
CMP_SOURCES = {"offline": "Offline (harness)", "live": "Simulated (Verisign stack)", "lab": "Saved lab run"}
CMP_SOURCE_NOTE = {
    "offline": "Pinned image, one dedicated core, repeated trials: reference timings (FIPS 205 SLH-DSA on the `latest` stack).",
    "live": "Real Unbound and NSD in containers (a finished simulated benchmark): real packets and cache behaviour, indicative timings (not core-pinned).",
    "lab": "One lab's last signing run plus one saved run, for older data. A benchmark is easier to compare.",
}
CMP_BADGE = {"measured": ":green-badge[Measured]", "estimated": ":blue-badge[Estimated]",
             "extrapolated": ":orange-badge[Extrapolated]"}


def _cmp_summary(tag: str | None, folder: str | None) -> pd.DataFrame | None:
    if not tag or not folder:
        return None
    f = ECO_DIR / "results" / tag / folder / "summary.csv"
    if not f.is_file():
        return None
    try:
        return pd.read_csv(f)
    except Exception:  # noqa: BLE001
        return None


def _cmp_catalogue(source: str, h: driver.LabHandle) -> list[dict]:
    """The runs one source can offer, newest first, each with the facts used to judge comparability."""
    if source == "offline":
        return compare_hub.offline_catalogue(ROOT / "results")
    if source == "live":
        return [compare_hub.live_suite_entry(s) for s in suite_mod.list_suites() if s["complete"]]
    out = []
    for lab in driver.list_labs():
        for r in driver.list_runs(h, lab["tag"]):
            if (r["path"] / "summary.csv").is_file():
                out.append({"id": f"{lab['tag']}|{r['folder']}", "title": f"{lab['tag']} · {r['title']}",
                            "source": "lab", "tag": lab["tag"], "folder": r["folder"], "stack": "—", "trials": None,
                            "facts": {"Lab": lab["tag"]}, "datasets": [lab["tag"]]})
    return out


@st.cache_resource(show_spinner=False)
def _cmp_offline_run(path: str, mtime: float):
    return compare_hub.load_offline(Path(path))


@st.cache_resource(show_spinner=False)
def _cmp_suite(path: str, mtime: float):
    return compare.load_suite(Path(path))


def _cmp_ds_label(source: str):
    return (lambda d: f"{d:,} names") if source == "live" else (lambda d: str(d))


def _cmp_data(entry: dict, ds, prof, udp, h: driver.LabHandle) -> dict:
    """One run reduced to the shared frame, plus whatever the detailed views need."""
    src = entry["source"]
    if src == "offline":
        run = _cmp_offline_run(str(entry["path"]), (entry["path"] / "summary.json").stat().st_mtime)
        return {"frame": compare_hub.offline_frame(run, ds), "run": run, "summary": None, "raw": None,
                "man_rows": [], "sd": None}
    if src == "live":
        sd = _cmp_suite(str(entry["path"]), (entry["path"] / "suite.json").stat().st_mtime)
        raw = compare.suite_frame(sd, int(ds), prof, udp)
        full = sd["full"][sd["full"]["dataset"] == ds] if not sd["full"].empty else sd["full"]
        return {"frame": compare_hub.live_frame(raw), "run": None, "summary": sd["bench"].get(int(ds)), "raw": raw,
                "man_rows": full.to_dict("records"), "sd": sd}
    man_rows = driver.manifest_rows(h, entry["tag"])
    summary = _cmp_summary(entry["tag"], entry["folder"])
    bench = compare.bench_by_scenario(summary, prof, udp) if summary is not None else compare.bench_by_scenario(pd.DataFrame())
    raw = compare.merge(compare.signing_by_scenario(man_rows), bench, compare.analytical_by_scenario(man_rows))
    return {"frame": compare_hub.live_frame(raw), "run": None, "summary": summary, "raw": raw, "man_rows": man_rows,
            "sd": None}


def _cmp_net_choices(entry: dict, ds) -> tuple[list, list]:
    """Network profiles and UDP limits a live run has numbers for."""
    summary = None
    if entry["source"] == "live":
        sd = _cmp_suite(str(entry["path"]), (entry["path"] / "suite.json").stat().st_mtime)
        summary = sd["bench"].get(int(ds)) if sd else None
    elif entry["source"] == "lab":
        summary = _cmp_summary(entry["tag"], entry["folder"])
    if summary is None or summary.empty:
        return [], []
    profiles = [str(p) for p in summary["profile"].dropna().unique()] if "profile" in summary else []
    udps = [int(u) for u in summary["udp_limit"].dropna().unique()] if "udp_limit" in summary else []
    return profiles, udps


def sidebar_compare(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("compare")  # the pickers are drawn by compare_page once it knows the view and the source


def _cmp_pickers(view: str, source: str, cat: list[dict], h: driver.LabHandle) -> dict | None:
    """Sidebar: which run(s), which dataset, which pair. Returns the choices with the data loaded."""
    by = {e["id"]: e for e in cat}
    ids = list(by)
    st.sidebar.markdown("### Compare")
    a_id = sb(st.sidebar.selectbox, f"cmp_a_{source}", ids[0], "Run A" if view == "runs" else "Run",
              options=ids, format_func=lambda i: by[i]["title"])
    ea, eb = by[a_id], None
    if view == "runs":
        b_id = sb(st.sidebar.selectbox, f"cmp_b_{source}", ids[1] if len(ids) > 1 else ids[0], "Run B",
                  options=ids, format_func=lambda i: by[i]["title"])
        eb = by[b_id]
    datasets = [d for d in ea["datasets"] if eb is None or d in eb["datasets"]]
    if not datasets:
        st.sidebar.warning("These two runs have no dataset size in common.")
        return None
    ds = sb(st.sidebar.selectbox, f"cmp_ds_{source}", datasets[len(datasets) // 2], gl.label("zone_size"),
            options=datasets, format_func=_cmp_ds_label(source), help=gl.tip("zone_size"))
    prof = udp = None
    if source != "offline":
        profiles, udps = _cmp_net_choices(ea, ds)
        if profiles:
            prof = sb(st.sidebar.selectbox, f"cmp_profile_{source}", profiles[0], gl.label("network_profile"), options=profiles,
                      help=gl.tip("network_profile"))
        if udps:
            udp = sb(st.sidebar.selectbox, f"cmp_udp_{source}", udps[0], gl.label("edns_buffer"), options=udps,
                      help=gl.tip("edns_buffer"))
    a = _cmp_data(ea, ds, prof, udp, h)
    b = _cmp_data(eb, ds, prof, udp, h) if eb else None
    out = {"ea": ea, "eb": eb, "ds": ds, "prof": prof, "udp": udp, "a": a, "b": b}
    if view == "algorithms":
        keys = list(a["frame"].key)
        if len(keys) >= 2:
            st.sidebar.markdown("---")
            st.sidebar.markdown("**Focus pair**", help="Pick the algorithm to evaluate and what to compare it with.")
            cand = sb(st.sidebar.selectbox, f"cmp_cand_{source}", next((k for k in ("slh-dsa-mtl", "mtl") if k in keys), keys[-1]),
                      "Candidate", options=keys, format_func=compare_hub.short,
                      help="The algorithm being evaluated.")
            others = [k for k in keys if k != cand]
            base = sb(st.sidebar.selectbox, f"cmp_base_{source}", "slh-dsa" if "slh-dsa" in others else others[0],
                      "against baseline", options=others, format_func=compare_hub.short,
                      help="The algorithm the candidate is compared with. Every ratio is relative to it; lower is better.")
            out.update(cand=cand, base=base)
    st.sidebar.markdown("---")
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_cmp"):
        _cmp_offline_run.clear()
        _cmp_suite.clear()
        st.rerun()
    return out


def _plot_cfg(fig, height: int = 360):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=50, b=10), legend_title_text="")
    fig.for_each_xaxis(lambda a: a.update(title_text=gl.label("algorithm")) if a.title.text == "scenario" else None)
    return fig


def _cmp_reference_line(fig, categories: list[str], y: float, name: str) -> None:
    """A dotted reference line as a trace: a shape on a log axis is placed inconsistently between plotly versions."""
    fig.add_scatter(x=categories, y=[y] * len(categories), mode="lines", name=name, showlegend=False,
                    line=dict(dash="dot", color="#555", width=1), hoverinfo="skip")


def _cmp_bar(frame: pd.DataFrame, col: str, *, height: int = 330, err: bool = False):
    d = frame.dropna(subset=[col]).copy()
    if d.empty:
        return None
    title, unit = compare_hub.METRICS[col]
    d["text"] = [compare_hub.fmt_value(v, unit) + (" (extrapolated)" if col == "sign_s" and b == "extrapolated" else "")
                 for v, b in zip(d[col], d["sign_basis"])]
    kw = {}
    lo, hi = {"sign_s": ("sign_lo", "sign_hi"), "verify_s": ("verify_lo", "verify_hi")}.get(col, (None, None))
    if err and lo and d[lo].notna().all() and d[hi].notna().all():
        d["err_hi"], d["err_lo"] = (d[hi] - d[col]).clip(lower=0), (d[col] - d[lo]).clip(lower=0)
        kw = {"error_y": "err_hi", "error_y_minus": "err_lo"}
    log = unit != "n" and (d[col] > 0).all()
    fig = px.bar(d, x="label", y=col, color="key", color_discrete_map=compare_hub.COLORS, text="text", log_y=log,
                 category_orders={"label": list(frame.label)}, title=f"{title}" + (" (log scale)" if log else ""), **kw)
    fig.update_layout(showlegend=False, xaxis_title=None, yaxis_title={"s": "seconds", "B": "bytes", "n": "count"}[unit])
    fig.update_traces(textposition="outside", cliponaxis=False)
    return _plot_cfg(fig, height)


METRIC_TERM = {"sign_s": "signing_time", "verify_s": "verification_time", "size_bytes": "signature_size",
               "wire_bytes": "response_size", "truncations": "tc_responses", "cache_bytes": "cache_size"}


def _cmp_cards(frame: pd.DataFrame, base: str, cand: str, paired: pd.DataFrame | None) -> None:
    x = compare_hub.vs_baseline(frame, base).set_index("key")
    fr = frame.set_index("key")
    cols = st.columns(len(compare_hub.METRICS))
    for c, (col, (title, unit)) in zip(cols, compare_hub.METRICS.items()):
        v, b = fr.loc[cand, col], fr.loc[base, col]
        what = gl.tip(METRIC_TERM[col])
        if pd.isna(v) or pd.isna(b):
            c.metric(title, "—", help=f"{what}\n\nNo data for this metric.")
            continue
        text, help_ = compare_hub.fmt_x(x.loc[cand, col]), what
        if paired is not None and not paired.empty:
            p = paired[(paired.key == cand) & (paired.col == col)]
            if len(p):
                text = compare_hub.fmt_x(p.iloc[0].ratio)
                help_ = (f"{what}\n\nMedian of the per-trial ratios over {int(p.iloc[0].trials)} runs; interval "
                         f"{p.iloc[0].lo:.3g} to {p.iloc[0].hi:.3g} (baseline ÷ candidate).")
        c.metric(title, text, f"{compare_hub.fmt_value(v, unit)} vs {compare_hub.fmt_value(b, unit)}",
                 delta_color="off", delta_arrow="off", help=help_)


def _cmp_scaling(source: str, a: dict, prof, udp) -> pd.DataFrame:
    if source == "offline":
        return compare_hub.offline_scaling(a["run"])
    if source == "live":
        parts = [compare_hub.live_frame(compare.suite_frame(a["sd"], d, prof, udp)).assign(dataset=d, rrsets=d)
                 for d in a["sd"]["datasets"]]
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return pd.DataFrame()


def _cmp_live_detail(df: pd.DataFrame, sd, ds, use_suite: bool, man_rows: list, name: str) -> None:
    scen_order, colors = list(df.scenario), compare.COLORS
    signing, wire, cache, allnums = st.tabs(["Signing & size", "Response size by tier", "Cache & latency", "All numbers"])
    with signing:
        if use_suite:
            st.markdown(":blue-badge[Estimated] sign and verify time from the repeated sample trials · "
                        ":green-badge[Measured] signed size and the single full signing run")
        else:
            st.markdown(":green-badge[Measured] from the last signing run")
        sg = df.dropna(subset=["sign_s"]) if "sign_s" in df else df
        if sg.empty:
            st.info("No signing data: sign the zones in Setup.")
        else:
            c1, c2 = st.columns(2)
            long = sg.melt(id_vars="scenario", value_vars=["sign_s", "verify_s"], var_name="what", value_name="seconds")
            long["what"] = long["what"].map({"sign_s": "Signing time", "verify_s": "Verification time"})
            if use_suite:
                long = long.merge(sg[["scenario", "sign_lo_s", "sign_hi_s"]], on="scenario", how="left")
                is_sign = long["what"] == "Signing time"
                long["err_hi"] = (long["sign_hi_s"] - long["seconds"]).where(is_sign)
                long["err_lo"] = (long["seconds"] - long["sign_lo_s"]).where(is_sign)
                f1 = px.bar(long, x="scenario", y="seconds", color="what", barmode="group", log_y=True,
                            error_y="err_hi", error_y_minus="err_lo", category_orders={"scenario": scen_order},
                            title="Signing and verification time, estimated (s, log; bars = confidence interval of the median)")
            else:
                f1 = px.bar(long, x="scenario", y="seconds", color="what", barmode="group", log_y=True,
                            category_orders={"scenario": scen_order}, title="Signing and verification time (s, log)")
            c1.plotly_chart(_plot_cfg(f1), width="stretch")
            sz = sg.assign(MB=sg.signed_bytes / 1024 ** 2)
            f2 = px.bar(sz, x="scenario", y="MB", color="scenario", color_discrete_map=colors,
                        category_orders={"scenario": scen_order}, title="Signed zone size (MB)", text_auto=".2f")
            f2.update_layout(showlegend=False)
            c2.plotly_chart(_plot_cfg(f2), width="stretch")
            f3 = px.bar(sg, x="scenario", y="sign_ms_per_rrsig", color="scenario", color_discrete_map=colors,
                        category_orders={"scenario": scen_order}, log_y=True,
                        title="Signing time per RRSIG (ms, log)", text_auto=".3g")
            f3.update_layout(showlegend=False)
            st.plotly_chart(_plot_cfg(f3, 320), width="stretch")
            if use_suite:
                stt_ = compare.signing_trial_stats(sd["trials"], sd["full"])
                stt_ = stt_[stt_["dataset"] == int(ds)]
                st.markdown("**Signing repeats in detail**")
                st.dataframe(_signing_stats_table(stt_), width="stretch", hide_index=True)
                st.caption("Estimated: each trial signs two sample zones; a line through them gives a fixed cost per "
                           "zone and a cost per RRSIG, applied to every zone of the dataset. Measured once: the full "
                           "zones the lab served, signed one time. Compare the two to judge the extrapolation.")
            st.caption("Verification here is `ldns-verify-zone` over every signed zone (an offline check). "
                       "How long a resolver takes to validate is part of the cold-pass latency under Cache & latency.")

    with wire:
        st.markdown(":green-badge[Measured] from packet counts in the saved run")
        bw = df.dropna(subset=["upstream_bytes"]) if "upstream_bytes" in df else df
        if bw.empty:
            st.info("No benchmark data: run a benchmark in the Benchmark tile.")
        else:
            c1, c2 = st.columns(2)
            hops = bw.melt(id_vars="scenario", value_vars=["root_bytes", "tld_bytes", "sld_bytes"],
                           var_name="hop", value_name="bytes")
            hops["hop"] = hops["hop"].map({"root_bytes": "Root", "tld_bytes": "TLD", "sld_bytes": "Leaf"})
            hops["KB"] = hops["bytes"] / 1024
            f1 = px.bar(hops, x="scenario", y="KB", color="hop", barmode="stack",
                        category_orders={"scenario": scen_order},
                        title="Resolver→authoritative bytes by tier, cold cache (KB)")
            c1.plotly_chart(_plot_cfg(f1), width="stretch")
            tr = bw.melt(id_vars="scenario", value_vars=["truncated", "tcp_conns", "upstream_queries"],
                         var_name="what", value_name="count")
            tr["what"] = tr["what"].map({"truncated": "Truncated (TC) responses", "tcp_conns": "TCP connections",
                                         "upstream_queries": "Resolver→authoritative queries"})
            f2 = px.bar(tr, x="scenario", y="count", color="what", barmode="group",
                        category_orders={"scenario": scen_order}, title="Truncation, TCP fallback and upstream queries")
            c2.plotly_chart(_plot_cfg(f2), width="stretch")
            cb = bw["client_bytes"].dropna()
            if not cb.empty:
                same = "the same for every algorithm" if cb.nunique() == 1 else "shown in the table"
                st.caption(f"Client ↔ resolver traffic is {same} (about {compare.fmt_bytes(cb.median())}). "
                           "Warm-cache passes are answered from the cache and send no upstream traffic.")

    with cache:
        cc = df.dropna(subset=["cache_rrset"]) if "cache_rrset" in df else df
        st.markdown("##### Measured :green-badge[Measured]")
        if cc.empty:
            st.info("No benchmark data: run a benchmark in the Benchmark tile.")
        else:
            c1, c2 = st.columns(2)
            mem = cc.melt(id_vars="scenario", value_vars=["cache_rrset", "cache_message"],
                          var_name="cache", value_name="bytes")
            mem["cache"] = mem["cache"].map({"cache_rrset": "RRset cache", "cache_message": "Message cache"})
            mem["MB"] = mem["bytes"] / 1024 ** 2
            f1 = px.bar(mem, x="scenario", y="MB", color="cache", barmode="group",
                        category_orders={"scenario": scen_order}, title="Cache size after the cold-cache pass (MB)")
            c1.plotly_chart(_plot_cfg(f1), width="stretch")
            lat = cc.melt(id_vars="scenario", value_vars=["cold_ms", "warm_ms"], var_name="pass", value_name="ms")
            lat["pass"] = lat["pass"].map({"cold_ms": "Cold cache", "warm_ms": "Warm cache"})
            f2 = px.bar(lat, x="scenario", y="ms", color="pass", barmode="group",
                        category_orders={"scenario": scen_order}, title=gl.label("latency_p50"))
            c2.plotly_chart(_plot_cfg(f2), width="stretch")
            st.caption("Cold is the first pass after flushing the resolver; warm repeats the same lookups from cache. "
                       "Cache memory is Unbound's RRset and message cache after the cold pass.")

        st.markdown("---")
        st.markdown("##### Analytical :orange-badge[Analytical]")
        an = df.dropna(subset=["analytic_sig_bytes"])
        per = compare.analytical_per_signature(man_rows)
        if an.empty:
            st.info("No analytical estimate for these algorithms (needs signed zones for SLH-DSA or MTL).")
        else:
            c1, c2 = st.columns([3, 2])
            ax = an.assign(MB=an.analytic_sig_bytes / 1024 ** 2)
            f = px.bar(ax, x="scenario", y="MB", color="scenario", color_discrete_map=colors,
                       category_orders={"scenario": scen_order}, text_auto=".2f",
                       title="Signature storage for the whole zone set, draft §12 formulas (MB)")
            f.update_layout(showlegend=False)
            c1.plotly_chart(_plot_cfg(f, 320), width="stretch")
            if per:
                c2.markdown(f"**One signature, maximum size** (largest zone: {per['n_messages']} RRsets)")
                c2.markdown(
                    f"- Direct SLH-DSA: **{per['direct']:,} B**  \n"
                    f"- MTL condensed: **{per['condensed']:,} B**  \n"
                    f"- MTL signed ladder (once per zone): **{per['ladder']:,} B**  \n"
                    f"- MTL full (condensed + ladder): **{per['full']:,} B**"
                )
        st.caption("Analytical figures are computed from the draft's size formulas and the zone's RRSIG counts. "
                   "They are never read from the lab and are kept apart from the measured cache above. "
                   "They describe signature material for the whole zone set, not what the resolver actually cached.")


    with allnums:
        t = compare.table(df)
        st.dataframe(t, width="stretch", hide_index=True)
        st.download_button("Download comparison (CSV)", data=df.to_csv(index=False), key="cmp_live_dl",
                           file_name=f"comparison-{name}.csv", mime="text/csv")



def _cmp_algorithms(source: str, pick: dict, h: driver.LabHandle) -> None:
    a, ea, ds = pick["a"], pick["ea"], pick["ds"]
    frame = a["frame"]
    if frame.empty or len(frame) < 2:
        st.info("This run has fewer than two algorithms with data for the chosen dataset.")
        return
    base, cand = pick.get("base"), pick.get("cand")
    paired = compare_hub.offline_paired(a["run"], ds, base) if a["run"] is not None and base else None
    facts = ea["facts"]
    bits = [f"**{ea['title']}**", f"dataset **{_cmp_ds_label(source)(ds)}**"]
    if source == "offline":
        bits.append(f"stack {facts.get('Stack profile')} · {facts.get('Runs')} runs · one pinned core")
    elif pick["prof"]:
        bits.append(f"network {pick['prof']}, UDP {pick['udp']}")
    st.caption(" · ".join(bits))
    for note in compare.lookup_problems(a["summary"]):
        st.warning(note + ". Do not compare this algorithm until the failures are explained.")
    bands = [r["band"] for r in (a["run"].summary.get("noise_floor", []) if a["run"] is not None else [])
             if r["dataset"] == ds]
    if bands:
        band = max(bands)
        st.caption(f"Measurement noise floor on this run (A/A control): ±{band * 100:.1f}%. A timing difference "
                   "smaller than that is not a difference.")

    st.markdown(f"#### {compare_hub.short(cand)} vs {compare_hub.short(base)}")
    _cmp_cards(frame, base, cand, paired)

    tabs = st.tabs(["At a glance", "Signing & verification", "Response size & truncation", "Cache & latency",
                    "Across zone sizes", "All numbers"])
    glance, signing, wire, cache, across, allnums = tabs
    notes = compare_hub.METRIC_NOTES["offline" if source == "offline" else "live"]
    colors = compare_hub.COLORS

    with glance:
        rel = []
        fr = frame.set_index("key")
        for k in frame.key:
            for col, (title, _u) in compare_hub.METRICS.items():
                b, v = fr.loc[base, col], fr.loc[k, col]
                if pd.notna(b) and pd.notna(v) and b > 0 and v > 0:
                    rel.append({"Metric": title, "Algorithm": compare_hub.short(k), "% of baseline": 100 * v / b})
        if rel:
            fig = px.bar(pd.DataFrame(rel), x="Metric", y="% of baseline", color="Algorithm", barmode="group", log_y=True,
                         color_discrete_map={compare_hub.short(k): c for k, c in colors.items()},
                         category_orders={"Algorithm": [compare_hub.short(k) for k in frame.key]},
                         title=f"Everything relative to {compare_hub.short(base)} (= 100%, log scale; lower is better)")
            _cmp_reference_line(fig, [m[0] for m in compare_hub.METRICS.values()], 100, compare_hub.short(base))
            st.plotly_chart(_plot_cfg(fig, 420), width="stretch")
        st.markdown("##### All algorithms")
        alg = compare_hub.algorithm_table(frame, base, paired)
        st.dataframe(alg, width="stretch", hide_index=True, column_config=gl.column_config(alg.columns))
        st.caption("'× lower' means the value is that many times below the baseline (every metric here: lower is "
                   "better). Brackets show the interval of the per-trial ratio where trials are paired.")

    with signing:
        if source == "offline":
            what = {"measured": "timed library calls", "extrapolated": "direct SLH-DSA total, from a sample of the zone"}
            st.markdown(" · ".join(f"{CMP_BADGE[b]} {what[b]}" for b in what if b in set(frame.sign_basis)))
        else:
            st.markdown(f"{CMP_BADGE['estimated']} {notes['sign_s']}")
        c1, c2 = st.columns(2)
        for col_, w in (("sign_s", c1), ("verify_s", c2)):
            fig = _cmp_bar(frame, col_, err=True)
            if fig is not None:
                w.plotly_chart(fig, width="stretch")
        fig = _cmp_bar(frame, "size_bytes")
        if fig is not None:
            st.plotly_chart(fig, width="stretch")
        st.caption(f"Signing: {notes['sign_s']}  \nVerification: {notes['verify_s']}  \nSize: {notes['size_bytes']}  \n"
                   "Bars show the interval of the median where one exists.")
        if source == "offline":
            st.caption(compare_hub.stats_label(a["run"]))

    with wire:
        st.markdown(":green-badge[Measured] " + ("DNS messages of the replayed workload" if source == "offline"
                                                 else "packet counts of the saved run"))
        c1, c2 = st.columns(2)
        for col_, w in (("wire_bytes", c1), ("truncations", c2)):
            fig = _cmp_bar(frame, col_)
            if fig is not None:
                w.plotly_chart(fig, width="stretch")
        if frame[["wire_bytes", "truncations"]].isna().all().all():
            st.info("No bytes-on-the-wire data in this run. Run a benchmark (live) or the full harness (offline).")
        st.caption(f"Bytes: {notes['wire_bytes']}  \nTruncations: {notes['truncations']}")

    with cache:
        st.markdown(":green-badge[Measured] " + notes["cache_bytes"])
        c1, c2 = st.columns(2)
        fig = _cmp_bar(frame, "cache_bytes")
        if fig is not None:
            c1.plotly_chart(fig, width="stretch")
        lat = frame.dropna(subset=["cold_ms"])
        if not lat.empty:
            m = lat.melt(id_vars=["key", "label"], value_vars=["cold_ms", "warm_ms"], var_name="pass", value_name="ms")
            m["pass"] = m["pass"].map({"cold_ms": "Cold cache", "warm_ms": "Warm cache"})
            f2 = px.bar(m, x="label", y="ms", color="pass", barmode="group", category_orders={"label": list(lat.label)},
                        title=gl.label("latency_p50"))
            f2.update_layout(xaxis_title=None)
            c2.plotly_chart(_plot_cfg(f2), width="stretch")
        elif source == "offline":
            c2.info("Lookup latency is measured only in Simulated mode, where a real resolver answers.")
        if fig is None and lat.empty:
            st.info("No cache or latency data in this run.")

    with across:
        sc = _cmp_scaling(source, a, pick["prof"], pick["udp"])
        if sc.empty or sc.dataset.nunique() < 2:
            st.info("Only one dataset size is available for this run.")
        else:
            x_title = "RRsets in the zone" if source == "offline" else "Data names"
            left, right = st.columns(2)
            for i, (col_, (title, unit)) in enumerate(compare_hub.METRICS.items()):
                data = sc.dropna(subset=[col_])
                data = data[data[col_] > 0]
                if data.empty:
                    continue
                f = px.line(data, x="rrsets", y=col_, color="key", markers=True, log_x=True, log_y=True,
                            color_discrete_map=colors, category_orders={"key": list(frame.key)}, title=title)
                f.update_xaxes(title=x_title)
                f.update_layout(legend_title_text="")
                (left if i % 2 == 0 else right).plotly_chart(_plot_cfg(f, 320), width="stretch")
            st.caption("Both axes are logarithmic: a straight line is power-law growth and a constant gap between two "
                       "lines is a constant ratio.")

    with allnums:
        st.dataframe(frame.drop(columns=["label"]), width="stretch", hide_index=True)
        st.download_button("Download this comparison (CSV)", data=frame.to_csv(index=False), key="cmp_alg_dl",
                           file_name=f"algorithms-{ea['id']}-{ds}.csv", mime="text/csv")

    if source != "offline" and a["raw"] is not None and not a["raw"].empty:
        with st.expander("Live-lab detail: bytes by tier, latency, analytical sizes"):
            _cmp_live_detail(a["raw"], a["sd"], ds, source == "live", a["man_rows"], ea["id"])


def _cmp_runs(source: str, pick: dict) -> None:
    ea, eb, ds, a, b = pick["ea"], pick["eb"], pick["ds"], pick["a"], pick["b"]
    if ea["id"] == eb["id"]:
        st.info("Run A and Run B are the same run. Pick two different runs in the sidebar.")
        return
    st.markdown("#### What differs between the two runs")
    for note in compare_hub.comparability_notes(ea["facts"], eb["facts"]):
        (st.info if note.startswith("Same") else st.warning)(note)
    diff = compare_hub.facts_diff(ea["facts"], eb["facts"])
    diff["Same"] = diff["Same"].map({True: "", False: "differs"})
    st.dataframe(diff, width="stretch", hide_index=True)

    bands = {}
    if source == "offline":
        for col_, name in (("sign_s", "signing"), ("verify_s", "verification (warm)")):
            vals = [x for x in (compare_hub.noise_band(a["run"], ds, name), compare_hub.noise_band(b["run"], ds, name))
                    if x is not None]
            if vals:
                bands[col_] = max(vals)
    ratios = compare_hub.run_ratios(a["frame"], b["frame"], bands)
    if ratios.empty:
        st.info("The two runs have no algorithm and metric in common for this dataset.")
        return
    st.markdown(f"#### Run B relative to Run A · dataset {_cmp_ds_label(source)(ds)}")
    near = int((abs(ratios.ratio - 1) <= 0.05).sum())
    st.caption(f"{near} of {len(ratios)} values are within 5% of each other. A ratio of 1 means identical; "
               "above 1 means Run B is higher (all metrics here: lower is better).")
    ratios = ratios.assign(**{"Algorithm": ratios["label"]})
    fig = px.bar(ratios, x="metric", y="ratio", color="Algorithm", barmode="group", log_y=True,
                 color_discrete_map={compare_hub.short(k): c for k, c in compare_hub.COLORS.items()},
                 category_orders={"Algorithm": [compare_hub.short(k) for k in compare_hub.order_keys(ratios.key)]},
                 title="Run B ÷ Run A (log scale)")
    _cmp_reference_line(fig, list(dict.fromkeys(ratios["metric"])), 1, "identical")
    st.plotly_chart(_plot_cfg(fig, 400), width="stretch")
    show = pd.DataFrame({
        "Algorithm": ratios["label"], "Metric": ratios["metric"],
        "Run A": [compare_hub.fmt_value(v, u) for v, u in zip(ratios.a, ratios.unit)],
        "Run B": [compare_hub.fmt_value(v, u) for v, u in zip(ratios.b, ratios.unit)],
        "B ÷ A": [f"{r:.3g}" for r in ratios.ratio],
        "Against the noise floor": ["" if w is None else ("within noise" if w else "outside noise")
                                    for w in ratios.within_noise],
    })
    st.dataframe(show, width="stretch", hide_index=True)
    st.download_button("Download this comparison (CSV)", data=ratios.drop(columns=["Algorithm"]).to_csv(index=False),
                       key="cmp_runs_dl", file_name=f"runs-{ea['id']}-vs-{eb['id']}-{ds}.csv", mime="text/csv")
    if source == "offline" and bands:
        st.caption("Noise floor: the larger A/A band of the two runs, for signing and verification only.")


def _ladder_figure(leaves: int, cached_leaves: int, age: int):
    """The signer's ladder and a resolver's older one on the same leaf axis: a rung is a complete binary subtree, and
    the leaves appended since the cached ladder have no rung in it."""
    fig = go.Figure()
    palette = ["#2e9e6f", "#4c78a8"]
    rows = [("Signer's ladder now", leaves), (f"Resolver's cached ladder ({age} round(s) old)", cached_leaves)]
    for i, (name, count) in enumerate(rows):
        for size, a, b in evolution.rungs_of(count):
            fig.add_trace(go.Bar(y=[name], x=[size], base=[a], orientation="h", marker_color=palette[i],
                                 marker_line=dict(color="white", width=1.5), text=[f"{size:,}"],
                                 textposition="inside", insidetextanchor="middle", showlegend=False,
                                 hovertemplate=f"rung of {size:,} leaves ({a:,} to {b:,})<extra></extra>"))
    if leaves > cached_leaves:
        fig.add_trace(go.Bar(y=[rows[1][0]], x=[leaves - cached_leaves], base=[cached_leaves], orientation="h",
                             marker_color="#e4572e", marker_line=dict(color="white", width=1.5),
                             text=[f"{leaves - cached_leaves:,} new"], textposition="inside", showlegend=False,
                             hovertemplate="appended since: no rung in the cached ladder<extra></extra>"))
    fig.update_layout(barmode="overlay", height=230, margin=dict(l=10, r=10, t=30, b=10), showlegend=False,
                      xaxis_title="leaves of the series (rungs are complete binary subtrees)",
                      yaxis=dict(autorange="reversed"))
    return fig


def _cmp_evolution(h: driver.LabHandle) -> None:
    cat = compare_hub.evolution_catalogue(ROOT / "results")
    if not cat:
        st.info("No offline run includes the zone-updates experiment yet. Every offline run whose config has an "
                "`evolution:` block includes it (the default configs do). Start one from the Benchmark tile.")
        st.button("Go to Benchmark", on_click=_go, args=("benchmark",), key="cmp_ev_to_bench")
        return
    by = {e["id"]: e for e in cat}
    st.sidebar.markdown("### Zone updates")
    rid = sb(st.sidebar.selectbox, "cmp_ev_run", next(iter(by)), "Run", options=list(by),
             format_func=lambda i: by[i]["title"])
    path = by[rid]["path"]
    run = _cmp_offline_run(str(path), (path / "summary.json").stat().st_mtime)
    mtl, direct = run.frames["evolution"], run.frames["evolution_direct"]
    datasets = [d for d in run.dataset_order if d in set(mtl.dataset)]
    ds = sb(st.sidebar.selectbox, "cmp_ev_ds", datasets[-1], "Zone size", options=datasets)
    ks = sorted(int(k) for k in mtl.k.unique())
    k = sb(st.sidebar.selectbox, "cmp_ev_k", ks[len(ks) // 2], "Records changed per update", options=ks,
                help=gl.tip("records_changed"))
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_ev"):
        _cmp_offline_run.clear()
        st.rerun()

    n_trials = int(run.config.get("trials", 0))
    st.caption(f"**{by[rid]['title']}** · zone **{ds}** · {n_trials} trials · measured with the reference library on "
               f"one pinned core. An update changes records chosen at random, keeping their size; "
               f"{stats_note(n_trials)}")
    st.markdown(":green-badge[Measured] re-sign cost, growth and checks · :violet-badge[Derived] response size")

    cost = evolution.update_cost(mtl, direct)
    cost = cost[cost.dataset == ds].set_index("k")
    c = cost.loc[k]
    m = st.columns(4)
    m[0].metric("Re-sign after an update · MTL", compare.fmt_seconds(c.mtl_round_ns / 1e9),
                f"1 signature, {k} record(s) changed", delta_color="off", delta_arrow="off",
                help=gl.tip("resign_time"))
    m[1].metric("Re-sign after an update · SLH-DSA", compare.fmt_seconds(c.direct_round_ns / 1e9),
                f"{k} signature(s), one per changed record", delta_color="off", delta_arrow="off",
                help=gl.tip("resign_time"))
    ratio = compare_hub.fmt_x(c.ratio)  # direct ÷ MTL: above 1 means MTL needs less time
    m[2].metric("MTL re-sign time vs SLH-DSA", ratio.replace("lower", "less time").replace("higher", "more time"),
                f"per-run ratio {c.ratio:.3g} [{c.ratio_lo:.3g}, {c.ratio_hi:.3g}]", delta_color="off",
                delta_arrow="off", help="How many times longer SLH-DSA takes than MTL to re-sign after one update, "
                "with the interval over the runs. Above 1 means MTL needs less time.")
    m[3].metric("Resolver validation after an update", compare.fmt_seconds(c.mtl_resolver_ns / 1e9),
                f"vs {compare.fmt_seconds(c.direct_resolver_ns / 1e9)} for {k} signature(s)", delta_color="off",
                delta_arrow="off", help="Time the resolver needs to validate the updated data (one signed ladder for "
                "MTL, the changed signatures for SLH-DSA).")
    if c.ratio_hi < 1.5 and c.ratio_lo > 0.67:
        st.warning(f"With {k} changed record(s) per update, MTL is **not faster to re-sign**: it still makes one "
                   "SLH-DSA signature per update, the same as SLH-DSA needs for a single changed record. Its gain "
                   "here is in response size and verification.")

    t_cost, t_bytes, t_cov, t_stale, t_growth, t_ladder = st.tabs(
        [gl.label("resign_time").replace("time", "cost"), "Response size", gl.label("stale_cache"), "Earlier signatures",
         "Zone growth", gl.label("mtl_internals")])
    g = evolution.growth(mtl)
    g = g[(g.dataset == ds) & (g.k == k)].sort_values("round")
    rounds = [int(r) for r in g["round"]]
    ages = [a for a in evolution.ages_in(mtl) if a > 0]

    with t_ladder:
        st.caption(gl.tip("mtl_internals"), help="Skip this tab unless you are reading the MTL draft.")
        c1, c2 = st.columns(2)
        r = c1.select_slider("Update", options=rounds, value=rounds[-1], key="cmp_ev_round")
        avail = [a for a in ages if a <= r] or [0]
        age = c2.select_slider("Updates since the resolver's cached ladder was signed", options=avail, value=avail[0],
                               key="cmp_ev_age")
        row = g[g["round"] == r].iloc[0]
        leaves = int(row.leaf_count)
        cached = int(g[g["round"] == r - age].iloc[0].leaf_count) if age else leaves
        parts = " + ".join(f"{s:,}" for s, _a, _b in evolution.rungs_of(leaves))
        st.markdown(f"**Update {r}:** {leaves:,} leaves = {parts} · {int(row.rung_count)} rung(s) · signed ladder "
                    f"**{compare_hub.fmt_value(row.ladder_bytes, 'B')}** · largest condensed signature "
                    f"**{int(row.condensed_bytes_max)} B**")
        st.plotly_chart(_ladder_figure(leaves, cached, age), width="stretch")
        st.caption("A ladder lists one hash per rung, and each rung is a complete binary subtree, so the rungs are the "
                   "binary breakdown of the leaf count. An RRset whose leaf lies inside a rung of the resolver's "
                   "cached ladder verifies with a condensed signature. A leaf appended after that ladder was signed "
                   "(orange) has no rung in it: that answer needs a fresh ladder, that is, a full signature.")

    with t_cov:
        st.caption(gl.tip("stale_cache"))
        cov = evolution.coverage_table(mtl)
        cov = cov[(cov.dataset == ds) & (cov.age > 0)]
        if cov.empty:
            st.info("This run has no stale-cache data.")
        else:
            piv = cov.pivot_table(index="k", columns="age", values="share") * 100
            fig = px.imshow(piv, text_auto=".1f", aspect="auto", color_continuous_scale="RdYlGn", zmin=0, zmax=100,
                            labels=dict(x="updates since the resolver last refreshed", y="records changed per update",
                                        color="% still short"),
                            title=f"Share of answers a stale resolver can still validate with a short signature, "
                                  f"{ds} zone")
            fig.update_yaxes(type="category")
            fig.update_xaxes(type="category")
            st.plotly_chart(_plot_cfg(fig, 340), width="stretch")
            st.caption("Measured for every record of the zone after each update. The missing share is the records changed "
                       "since the resolver's last refresh: those answers carry a full signature (about 8 KB, usually "
                       "over TCP). If changes are independent of popularity, the share of queries affected is the "
                       "same in expectation.")

    with t_cost:
        d = mtl[(mtl.dataset == ds) & (mtl["round"] >= 1)].assign(
            seconds=lambda x: (x.append_ns + x.ladder_sign_ns) / 1e9)
        a = d.groupby(["k", "round"]).seconds.median().reset_index().assign(method="MTL: 1 signature per update")
        dd = direct[direct["round"] >= 1].groupby(["k", "round"]).sign_ns.median().reset_index()
        b = dd.assign(seconds=dd.sign_ns / 1e9, method="SLH-DSA: 1 signature per changed record")
        both = pd.concat([a, b[["k", "round", "seconds", "method"]]], ignore_index=True)
        both["k"] = both["k"].map(lambda v: f"{int(v)} changed")
        fig = px.line(both, x="round", y="seconds", color="k", line_dash="method", markers=True, log_y=True,
                      title=f"Re-sign time per update, {ds} zone (log scale)")
        fig.update_layout(legend_title_text="", xaxis_title="update")
        st.plotly_chart(_plot_cfg(fig, 400), width="stretch")
        tab = cost.reset_index()
        st.dataframe(pd.DataFrame({
            "Records changed": tab.k, "MTL re-sign": [compare.fmt_seconds(v / 1e9) for v in tab.mtl_round_ns],
            "SLH-DSA re-sign": [compare.fmt_seconds(v / 1e9) for v in tab.direct_round_ns],
            "SLH-DSA ÷ MTL": [f"{r:.3g} [{lo:.3g}, {hi:.3g}]" for r, lo, hi in zip(tab.ratio, tab.ratio_lo, tab.ratio_hi)],
            "Interval coverage": [f"{x * 100:.0f}%" for x in tab.coverage]}), width="stretch", hide_index=True)
        st.caption("MTL makes one SLH-DSA signature per update however many records changed; SLH-DSA makes one per "
                   "changed record. The ratio is the median of the per-trial ratios with its interval. Producing the "
                   "short per-answer signatures is not part of the re-sign time.")

    with t_bytes:
        eb = evolution.effective_bytes(mtl)
        eb = eb[(eb.dataset == ds) & (eb.age > 0)]
        if eb.empty:
            st.info("This run has no stale-cache data.")
        else:
            eb = eb.assign(k=eb.k.map(lambda v: f"{int(v)} changed"), age=eb.age.map(lambda v: f"{int(v)} update(s) old"))
            fig = px.bar(eb, x="age", y="effective_bytes", color="k", barmode="group", text_auto=",.0f",
                         title=f"Signature bytes per answer, {ds} zone (derived)")
            fig.add_hline(y=7856, line_dash="dot", annotation_text="plain SLH-DSA: 7,856 B")
            fig.update_layout(xaxis_title="age of the resolver's cached data", yaxis_title="bytes per answer")
            st.plotly_chart(_plot_cfg(fig, 400), width="stretch")
            st.caption(":violet-badge[Derived] Measured short-signature size plus the stale share times a full signature. "
                       "An upper bound: a real resolver refreshes once and then covers the rest.")

    with t_stale:
        s = evolution.stale_by_round(mtl)
        s = s[(s.dataset == ds) & (s.k == k)]
        if s.empty or s.stale_tested.sum() == 0:
            st.info("No data.")
        else:
            fig = px.line(s, x="round", y=s.share * 100, markers=True, title=f"Earlier signatures that still validate "
                          f"against the newest data, {ds} zone, {k} changed per update")
            fig.update_layout(yaxis_title="% still valid", xaxis_title="update", yaxis_range=[0, 102])
            st.plotly_chart(_plot_cfg(fig, 360), width="stretch")
            st.caption("As the zone grows, signatures issued earlier no longer match the newest MTL ladder and must be "
                       "reissued for resolvers that fetch it. When the record count reaches a power of two, every "
                       "earlier signature is affected.")

    with t_growth:
        gg = evolution.growth(mtl)
        gg = gg[(gg.dataset == ds)].assign(k=lambda x: x.k.map(lambda v: f"{int(v)} changed"))
        c1, c2 = st.columns(2)
        c1.plotly_chart(_plot_cfg(px.line(gg, x="round", y="leaf_count", color="k", markers=True,
                                          title="Records in the series"), 320), width="stretch")
        c2.plotly_chart(_plot_cfg(px.line(gg, x="round", y="ladder_bytes", color="k", markers=True,
                                          title="Signed ladder (bytes)"), 320), width="stretch")
        c1.plotly_chart(_plot_cfg(px.line(gg, x="round", y="condensed_bytes_max", color="k", markers=True,
                                          title="Largest short signature (bytes)"), 320), width="stretch")
        c2.plotly_chart(_plot_cfg(px.line(gg, x="round", y="rung_count", color="k", markers=True,
                                          title="Rungs in the ladder"), 320), width="stretch")
        st.caption("Every change adds an entry and none leave, so the series only grows. The signed ladder follows the "
                   "number of rungs (the 1 bits in the entry count); the short signature grows by 16 bytes each "
                   "time the count reaches a power of two.")


def stats_note(n_trials: int) -> str:
    return evolution.stats.ci_label(n_trials) + "."


def compare_page(h: driver.LabHandle, stt: dict) -> None:
    _page_header("compare", h, stt)
    sources = {s: len(_cmp_catalogue(s, h)) for s in CMP_SOURCES}
    available = [s for s, n in sources.items() if n]
    view = sb(st.segmented_control, "cmp_view", "algorithms", "What to compare", options=list(CMP_VIEWS),
              format_func=CMP_VIEWS.get) or "algorithms"
    if view == "evolution":
        st.caption("What a zone change costs: records are added or modified, the zone is re-signed, and resolvers may "
                   "still hold earlier signatures. MTL against signing every changed record. Offline data only.")
        _cmp_evolution(h)
        return
    default_src = "offline" if sources["offline"] else ("live" if sources["live"] else "offline")
    source = sb(st.segmented_control, "cmp_src", default_src, "Data from", options=list(CMP_SOURCES),
                format_func=lambda s: f"{CMP_SOURCES[s]} ({sources[s]})") or default_src
    st.caption(f"{CMP_SOURCE_NOTE[source]} Offline and simulated results use different libraries and versions, "
               "so they are never drawn together.")
    cat = _cmp_catalogue(source, h)
    if not cat:
        others = [CMP_SOURCES[s] for s in available if s != source]
        if source == "offline":
            st.info("No offline run yet. Start one from the Benchmark tile (Offline mode).")
            st.button("Go to Benchmark", on_click=_go, args=("benchmark",), key="cmp_to_bench_off")
        elif source == "live":
            st.info("No finished simulated benchmark yet. Run one in the Benchmark tile (Simulated mode).")
            st.button("Go to Benchmark", on_click=_go, args=("benchmark",), key="cmp_to_bench")
        else:
            st.info("No saved lab run with results yet. The Live tile and the Benchmark tile create them.")
        if others:
            st.caption("Data is available from: " + ", ".join(others) + ".")
        return
    if view == "runs" and len(cat) < 2:
        st.info("Comparing runs needs at least two runs from this source. Repeat the benchmark (a reproducibility "
                "check), or run it on another stack profile or machine.")
        view = "algorithms"
    pick = _cmp_pickers(view, source, cat, h)
    if pick is None:
        return
    if view == "runs":
        _cmp_runs(source, pick)
    else:
        _cmp_algorithms(source, pick, h)


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
                   f"{pv['pcaps']} file(s) · {_fmt_size(pv['pcap_bytes'])} from the Live tile.", default=False)
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


# ─────────────────────────── ACTIVITY ───────────────────────────

def _is_tech_entry(e: dict) -> bool:
    if e.get("tech"):
        return True
    detail = e.get("detail") or ""
    title = (e.get("title") or "").lower()
    # Older sessions: raw Docker dumps were logged as fenced code blocks.
    if detail.lstrip().startswith("```"):
        return True
    if title.startswith(("build", "pull", "up", "prepare", "activate")) and "(" in title:
        return True
    return False


def activity_tab(h: driver.LabHandle) -> None:
    entries = st.session_state.get("activity") or []
    top = st.columns([1, 1, 2])
    if top[0].button("Clear", key="act_clear"):
        st.session_state.activity = []
        st.rerun()
    show_tech = top[1].checkbox("Technical details", value=False, key="act_tech",
                                help="Docker and tool output for debugging. Leave off for a normal demo.")
    if not entries:
        st.caption("Nothing yet. Use the sidebar steps — this list fills in as you go.")
        return

    visible = entries if show_tech else [e for e in entries if not _is_tech_entry(e)]
    if not visible:
        st.caption("Only technical lines so far. Turn on **Technical details** to see them.")
        return

    blob = "\n\n".join(
        f"[{e['t']}] {e['level']} {e['title']}\n{e.get('detail', '')}" for e in visible
    )
    top[2].download_button(
        "Download", data=blob, file_name=f"activity-{h.tag}.txt", mime="text/plain", key="act_dl",
    )

    for e in visible[:60]:
        icon = LEVEL_ICON.get(e["level"], "•")
        with st.container(border=True):
            st.markdown(f"{icon} **{e['t']}** · {e['title']}")
            detail = e.get("detail") or ""
            if not detail:
                continue
            if detail.lstrip().startswith("```"):
                with st.expander("Tool output"):
                    st.code(detail.strip().strip("`").replace("```", "").lstrip("\n"))
            else:
                st.caption(detail[:500])


# ─────────────────────────── LEARN ───────────────────────────

LEARN_DIR = ROOT / "docs" / "learn"
LEARN_PAGES = {
    "why": ("Why DNSSEC needs MTL", "why-dnssec-needs-mtl.html", 1180,
            "Six short animated scenes, from a poisoned cache to the packet-size problem. Click inside the animation "
            "first, then use → and ← to step and space to play."),
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
               "The measured results are in the Benchmark and Compare tiles and in each run's report.")
    st.button("Back to Home", on_click=_go, args=("home",), key="learn_home")


# ─────────────────────────── ENTRY POINT ───────────────────────────

SIDEBARS = {
    "home": sidebar_home, "setup": sidebar_setup, "live": sidebar_live,
    "benchmark": sidebar_benchmark, "compare": sidebar_compare, "learn": sidebar_learn, "reset": sidebar_reset,
}
PAGES = {
    "setup": setup_page, "live": live_page, "benchmark": benchmark_page, "compare": compare_page,
    "learn": learn_page, "reset": reset_page,
}


def main() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    _chrome_bar()  # About + full screen, top-right; no layout cost on the sidebar or page body
    symbol, _full = _brand()
    if symbol is not None:
        st.logo(symbol, icon_image=symbol, size="large")  # top-left on Home and on every tile
    _init_nav()
    _bootstrap_cfg_from_disk()
    h = _handle()
    mode = st.session_state.nav

    err = _apply_ui_config(h)
    tag_before = h.tag
    stt = _status(h)

    SIDEBARS[mode](h, stt)
    # Sidebar widgets may have changed settings this run; write them out before the page reads them.
    err = _apply_ui_config(h) or err
    if h.tag != tag_before:
        st.rerun()  # zone size changed → a different lab; re-read its status
    if err and mode == "setup" and not st.session_state.get("shape_errors"):
        st.sidebar.error(err)

    if mode == "home":
        home_page(h, stt)
    else:
        PAGES[mode](h, stt)


if __name__ == "__main__":
    main()
