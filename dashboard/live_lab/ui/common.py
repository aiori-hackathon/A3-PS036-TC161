"""State and page helpers shared by several tiles. Tile modules import this; it never imports a tile."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import streamlit as st

from live_lab import driver, images, minimal

ROOT = Path(__file__).resolve().parents[3]
ECO_DIR = ROOT / "ecosystem"
SUITES_DIR = ECO_DIR / "results" / "suites"

LEVEL_ICON = {"info": "ℹ️", "ok": "✅", "warn": "⚠️", "err": "❌", "step": "▶️"}
ACTIVITY_CAP = 500

NAV_MODES = ("home", "offline", "simulated", "compare", "learn", "setup", "reset")

# What a person wants to do comes first: the two ways of benchmarking are tracks, each a short numbered path.
# The node's images are a prerequisite shown on top of each track, not a step; "setup" is the Node images page for both.
TRACKS = {
    "offline": {
        "steps": (("run", "Run"), ("results", "Results")),
        "default": "run",
    },
    "simulated": {
        "steps": (("prepare", "Prepare"), ("query", "Query"), ("benchmark", "Benchmark")),
        "default": "prepare",
    },
}
# Old tile names (bookmarks, links) land on the matching step of a track.
LEGACY_TILES = {"lab": ("simulated", "prepare"), "live": ("simulated", "query"),
                "benchmark": ("simulated", "benchmark")}

TILES = {
    "offline": {
        "icon": "⏱️",
        "title": "Offline benchmark",
        "pitch": "Signing, verification and message sizes, timed on one pinned core with no network.",
        "cta": "Open",
    },
    "simulated": {
        "icon": "🧪",
        "title": "Simulated lab",
        "pitch": "A containerised root → TLD → leaf hierarchy with a validating resolver: packets, truncation, cache.",
        "cta": "Open",
    },
    "compare": {
        "icon": "⚖️",
        "title": "Compare",
        "pitch": "Algorithms, runs and zone updates side by side.",
        "cta": "Open",
    },
    "learn": {
        "icon": "📖",
        "title": "Learn",
        "pitch": "Why DNSSEC needs MTL, what the benchmark measures and why in two modes, and what an operator does "
                 "differently.",
        "cta": "Open",
    },
    "setup": {
        "icon": "📦",
        "title": "Node images",
        "pitch": "The pinned images this node runs: build or pull, then verify.",
        "cta": "Manage",
    },
    "reset": {
        "icon": "🧹",
        "title": "Reset",
        "pitch": "Clear saved runs, zones and captures.",
        "cta": "Open",
    },
}


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
    """Sidebar explanation. Shown while Help text is on, which is the default."""
    if st.session_state.get("show_help"):
        st.sidebar.caption(text)


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


def images_ready(stt: dict, mode: str) -> bool:
    """True when every image ``mode`` needs is on this node and matches the stack profile (read by lab.status)."""
    rows = [row for row in stt.get("image_rows") or [] if row["role"] in images.MODES[mode]]
    return bool(rows) and all(row["state"] == "ok" for row in rows)


def readiness(stt: dict) -> dict:
    containers = stt.get("containers") or {}
    r = {
        "images": images_ready(stt, "simulated"),
        "offline_images": images_ready(stt, "offline"),
        "prepared": bool(stt.get("prepared")),
        "running": any(containers.values()),
        "active": stt.get("active"),
    }
    r["ready"] = r["images"] and r["prepared"] and r["running"]
    r["count"] = sum(1 for k in ("images", "prepared", "running") if r[k])
    return r


def _scenario_list(h: driver.LabHandle) -> list[str]:
    """Scenarios chosen in the Prepare step (and present on disk after signing), not only the active one."""
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


def track_steps(track: str) -> tuple:
    """The (key, label) steps of a track that may be shown (fewer in minimal mode)."""
    return minimal.steps(track, TRACKS[track]["steps"])


def track_default(track: str) -> str:
    keys = [key for key, _label in track_steps(track)]
    default = TRACKS[track]["default"]
    return default if default in keys else keys[0]


def resolve_tile(tile: str, step: str | None = None) -> tuple[str, str | None]:
    """Map a tile name (possibly an old one such as ``live``) and an optional step to ``(mode, step)``.

    In minimal mode a page or step a visitor may not open resolves to Home or to the track's first allowed step,
    whether it comes from a link, a bookmark or a button."""
    if tile in LEGACY_TILES:
        track, legacy_step = LEGACY_TILES[tile]
        tile, step = track, step if _is_step(track, step) else legacy_step
    if tile not in NAV_MODES or not minimal.mode_allowed(tile):
        return "home", None
    if tile in TRACKS:
        return tile, step if _is_step(tile, step) else track_default(tile)
    return tile, None


def _is_step(track: str, step: str | None) -> bool:
    return any(step == key for key, _label in track_steps(track))


def step_key(track: str) -> str:
    return f"step_{track}"


def current_step(track: str) -> str:
    """The step of ``track`` being shown. Sidebars read it before the page draws the step bar."""
    value = st.session_state.get(step_key(track))
    if not _is_step(track, value):
        value = track_default(track)
        st.session_state[step_key(track)] = value
    return value


def step_bar(track: str, done: dict[str, bool]) -> str:
    """Draw the numbered steps of a track (a tick marks a finished step) and return the chosen one."""
    labels = {key: (f"{label} ✓" if done.get(key) else label) for key, label in track_steps(track)}
    current_step(track)  # make sure the stored value is valid before the widget uses it
    return st.radio("Step", list(labels), key=step_key(track), horizontal=True, format_func=labels.get,
                    label_visibility="collapsed")


def _go(mode: str, step: str | None = None) -> None:
    mode, step = resolve_tile(mode, step)
    st.session_state.nav = mode
    if step:
        st.session_state[step_key(mode)] = step
    try:
        if mode == "home":
            for name in ("tile", "step"):
                if name in st.query_params:
                    del st.query_params[name]
        else:
            st.query_params["tile"] = mode
            if step:
                st.query_params["step"] = step
            elif "step" in st.query_params:
                del st.query_params["step"]
    except Exception:  # noqa: BLE001
        pass


def _init_nav() -> None:
    if "nav" in st.session_state:
        return
    tile, step = "home", None
    try:
        tile, step = st.query_params.get("tile", "home"), st.query_params.get("step")
    except Exception:  # noqa: BLE001
        pass
    requested = (tile, step)
    mode, step = resolve_tile(tile, step)
    st.session_state.nav = mode
    if step:
        st.session_state[step_key(mode)] = step
    if minimal.enabled() and requested != (mode, step):
        _go(mode, step)  # the address bar shows the page actually opened, not a refused one


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
    """Write configs/_dashboard.yaml from the stored settings and point the handle at it.

    In minimal mode nothing is written: the handle follows the lab the team last set up on this node."""
    if minimal.enabled():
        if not (driver.ECO / driver.DASHBOARD_CONFIG).is_file():
            return "No lab has been set up on this node yet."
        h.config = driver.DASHBOARD_CONFIG
        h.stack = driver.load_ui_prefs().get("stack") or cfg("stack", driver.DEFAULT_STACK)
        st.session_state.lab_handle = h
        return None
    sc = list(cfg("scenarios") or ["ecdsa", "slh-dsa", "mtl"])
    if not sc:
        return "Select at least one algorithm in the Prepare step."
    zone = cfg("zone_size", "small")
    shape, errors, _warnings = _shape()
    st.session_state.shape_errors = errors
    if errors or shape is None:
        return "Fix the custom zone numbers in the Prepare step: " + " ".join(errors)
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


def _embed_html(html: str, height: int | str = "content") -> None:
    """Show a self-contained HTML page in an iframe (``st.iframe`` on current Streamlit)."""
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:  # older Streamlit: needs a fixed height
        import streamlit.components.v1 as components
        components.html(html, height=int(height) if isinstance(height, int) else 780)


# ─────────────────────────── page chrome ───────────────────────────

def _lab_strip(h: driver.LabHandle, stt: dict, *, mode: str = "setup") -> None:
    r = readiness(stt)
    sc = _scenario_list(h)
    if mode == "simulated" and current_step("simulated") == "query" and r["active"]:
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
    if mode not in TRACKS:  # a track's card on Home already says what it is
        st.caption(tile["pitch"])
    if mode in ("simulated", "reset"):
        _lab_strip(h, stt, mode=mode)


def _gate(stt: dict, what: str) -> bool:
    """True when the lab is ready; otherwise explain what is missing and offer a way to fix it."""
    r = readiness(stt)
    if r["ready"]:
        return True
    missing = [name for name, ok in (("images", r["images"]), ("signed zones", r["prepared"]),
                                     ("running containers", r["running"])) if not ok]
    st.warning(f"{what} needs a ready lab. Missing: {', '.join(missing)}.")
    if minimal.enabled():
        st.caption("The team's lab is not running on this node right now. The saved results under Results, the "
                   "Benchmark step and Compare are still available.")
        return False
    if not r["images"]:
        st.caption("Get the lab images in the panel at the top of the page.")
    else:
        st.button("Go to Prepare", on_click=_go, args=("simulated", "prepare"), key=f"gate_{what}", type="primary")
    return False


# ─────────────────────────── sidebars ───────────────────────────

def _sidebar_brand(mode: str) -> None:
    st.sidebar.title("Cipherops")
    st.sidebar.caption("MTL DNSSEC Lab")
    if mode != "home":
        st.sidebar.button("← Home", on_click=_go, args=("home",), key="sb_home", width="stretch")
    st.sidebar.toggle("Help text", value=True, key="show_help",
                      help="Show the explanations next to each control.")
    st.sidebar.markdown("---")


def _step_label(n: int, title: str, state: str) -> str:
    mark = {"done": "🟢", "partial": "🟡"}.get(state, "⚪")
    return f"{mark}  **Step {n}** · {title}"


def _warn_unvalidated(stack: str, scenarios) -> None:
    bad = driver.unvalidated_scenarios(stack, scenarios)
    if bad:
        st.sidebar.warning(
            f"On **{stack}**, {', '.join(bad)} is not validated by the resolver (SERVFAIL), so it cannot be compared "
            "with MTL here. Use **draft06** for the MTL vs non-MTL comparison, or untick it.")
