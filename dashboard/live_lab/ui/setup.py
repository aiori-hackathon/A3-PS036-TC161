"""Setup tile: put the pinned images on this node, then prove they match the stack profile.

Two independent image sets, each with its own card, version, status and button:

    Offline benchmark   cipherops-bench:latest                      (always the `latest` stack)
    Simulated lab       cipherops-eco-{nsd,unbound,tools}:<stack>   (the stack chosen in the sidebar)

Either set is built here from docker/Dockerfile or pulled from a registry. Afterwards it is checked against the stack
profile (ecosystem/stacks/<stack>.env); Benchmark and Lab only use images that pass. Signing zones and starting a lab are
the Lab tile; measuring is the Benchmark tile."""

from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from live_lab import driver, image_job, images
from live_lab import suite as suite_mod
from live_lab.ui.activity import activity_tab
from live_lab.ui.common import (
    _chip, _go, _log_chunks, _page_header, _sidebar_brand, activity, cfg, sb,
)

SOURCES = ("Build locally", "Pull from registry")
STATE_ICON = {"ok": "🟢", "missing": "⚪", "mismatch": "🟡", "unverified": "🟡"}
STATE_TEXT = {"ok": "ready", "missing": "missing", "mismatch": "does not match the stack profile",
              "unverified": "cannot be verified"}


def _clock(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds < 3600 else f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def _finish_image_job(h: driver.LabHandle) -> None:
    """Take over a build or pull that has just ended: log the result once (``handled`` is stored)."""
    stt = image_job.status()
    state = stt.get("state")
    if state not in ("done", "failed", "stopped") or stt.get("handled"):
        return
    image_job.mark_handled()
    verb = "Pull" if stt.get("action") == "pull" else "Build"
    log = image_job.log_text(8000)
    st.session_state.last_log = log
    _log_chunks(log, phase="build", source="compose", title=verb.lower())
    secs = float(stt.get("finished") or time.time()) - float(stt.get("started") or time.time())
    if state == "done":
        activity("ok", f"{verb} done ({secs:.0f}s)", f"Images for {stt.get('stack')}", phase="build",
                 source="compose", h=h)
        st.sidebar.success(f"Images ready · {secs:.0f}s")
    else:
        msg = str(stt.get("error") or f"The {verb.lower()} did not finish.")[-2000:]
        st.sidebar.error(msg[:600])
        activity("err", f"{verb} failed", msg, phase="build", source="compose", h=h)


def _start(h: driver.LabHandle, action: str, stack: str, mode: str, registry: str | None) -> None:
    what = "Pull images" if action == "pull" else "Build images"
    activity("step", what, f"{stack} · {images.MODE_TITLE[mode]}" + (f" · {registry}" if registry else ""),
             phase="build", source="compose", h=h)
    try:
        image_job.start(action, stack, mode=mode, registry=registry)  # returns at once; the page shows the log
    except Exception as e:  # noqa: BLE001
        st.error(str(e)[:600])
        activity("err", f"{what} failed to start", str(e)[:2000], phase="build", source="compose", h=h)
        return
    st.rerun()


def sidebar_setup(h: driver.LabHandle, stt: dict) -> None:
    """Sidebar of the Node images page."""
    _sidebar_brand("setup")
    st.sidebar.markdown("### Node images")
    _finish_image_job(h)
    if suite_mod.running_suite():
        st.sidebar.warning("A benchmark is running: build and pull are paused.")
    sb(st.sidebar.radio, "image_mode", SOURCES[0], "Get images by", options=list(SOURCES), help=_SOURCE_HELP)
    if cfg("image_mode", SOURCES[0]) == SOURCES[1]:
        sb(st.sidebar.text_input, "registry", images.DEFAULT_REGISTRY, "Registry", placeholder="ghcr.io/org/benchmark",
           help=_REGISTRY_HELP)
    sb(st.sidebar.selectbox, "stack", driver.DEFAULT_STACK, "Lab version", options=driver.list_stacks(),
       help=_VERSION_HELP)


_SOURCE_HELP = ("Build compiles everything on this node (tens of minutes the first time). Pull downloads images "
                "someone already built from the same stack profile; they are verified after the pull.")
_REGISTRY_HELP = ("Namespace holding cipherops-bench, cipherops-eco-nsd, -unbound and -tools, tagged with the stack "
                  "name. Published with scripts/publish_images.sh.")
_VERSION_HELP = ("Which pinned stack the simulated lab runs. draft06: condensed MTL by default. latest: always-full "
                 "MTL. The offline benchmark always uses latest.")


def _get_controls(h: driver.LabHandle, *, mode: str, stack: str, ok: bool, key: str) -> None:
    """Choose build or pull and start it. The choice is remembered across pages."""
    source = sb(st.radio, "image_mode", SOURCES[0], "Get by", options=list(SOURCES), horizontal=True,
                help=_SOURCE_HELP)
    pull = source == SOURCES[1]
    registry = sb(st.text_input, "registry", images.DEFAULT_REGISTRY, "Registry", help=_REGISTRY_HELP) if pull else None
    verb = "Pull" if pull else "Build"
    label = f"{verb} again" if ok else f"{verb} {'image' if mode == 'offline' else 'images'}"
    if st.button(label, key=f"get_{key}", type="secondary" if ok else "primary",
                 disabled=bool(suite_mod.running_suite() or (pull and not (registry or "").strip()))):
        _start(h, "pull" if pull else "build", stack, mode, registry)


def prerequisite(h: driver.LabHandle, track: str) -> bool:
    """The node prerequisite shown above a track: its images are on this node and match the stack profile.

    Ready: one line and a Manage popover. Missing: what is missing and the build or pull action, inline.
    While a build or pull runs, its progress replaces the panel. Returns whether the track can run.
    """
    mode = "offline" if track == "offline" else "simulated"
    if image_job.is_running():
        with st.container(border=True):
            _job_body()
        return False
    with st.container(border=True):
        if track == "simulated":
            version_col, state_col, manage_col = st.columns([3, 7, 2], vertical_alignment="center")
            with version_col:
                version = sb(st.selectbox, "stack", driver.DEFAULT_STACK, "Lab version", options=driver.list_stacks(),
                             label_visibility="collapsed", help=_VERSION_HELP)
            if version != h.stack:
                st.rerun()  # the lab handle follows the chosen version
        else:
            state_col, manage_col = st.columns([9, 2], vertical_alignment="center")
        stack = images.OFFLINE_STACK if track == "offline" else h.stack
        rows = images.status(stack, mode)
        ok = images.ready(rows)
        with state_col:
            if ok:
                st.markdown("✅ **Node ready** · " + " · ".join(f"`{s.ref}`" for s in rows))
            else:
                st.markdown("⛔ **Needed on this node** · " +
                            " · ".join(f"{STATE_ICON[s.state]} `{s.ref}`" for s in rows))
        if ok:
            with manage_col.popover("Manage", width="stretch"):
                _get_controls(h, mode=mode, stack=stack, ok=True, key=track)
        else:
            problems = sorted({p for s in rows if s.state != "ok" for p in s.problems if p != "not on this host"})
            if problems:
                st.caption("; ".join(problems))
            _get_controls(h, mode=mode, stack=stack, ok=False, key=track)
    return ok


def _chip_for(rows: list[images.ImageStatus]) -> str:
    if images.ready(rows):
        return _chip("ready", "ok")
    if any(s.state in ("mismatch", "unverified") for s in rows):
        return _chip("needs attention", "warn")
    return _chip("not on this node", "idle")


def _card(h: driver.LabHandle, *, title: str, purpose: str, stack: str, stack_note: str, mode: str,
          rows: list[images.ImageStatus], tile: str, step: str, cta: str, busy: bool) -> None:
    source = cfg("image_mode", SOURCES[0])
    registry = cfg("registry", images.DEFAULT_REGISTRY)
    pull = source == SOURCES[1]
    ok = images.ready(rows)
    with st.container(border=True):
        st.markdown(f"#### {title}  {_chip_for(rows)}", unsafe_allow_html=True)
        st.caption(purpose)
        st.markdown(f"**Version:** `{stack}` · {stack_note}")
        for s in rows:
            note = "" if s.state == "ok" else f" · {STATE_TEXT[s.state]}"
            st.markdown(f"{STATE_ICON[s.state]} {s.title} `{s.ref}`{note}")
        problems = sorted({p for s in rows if s.state != "ok" for p in s.problems if p != "not on this host"})
        if problems:
            st.caption("; ".join(problems))
        verb = "Pull" if pull else "Build"
        label = f"{verb} again" if ok else f"{verb} {'image' if len(rows) == 1 else 'images'}"
        if st.button(label, key=f"setup_get_{mode}", type="secondary" if ok else "primary", width="stretch",
                     disabled=bool(busy or (pull and not (registry or "").strip()))):
            _start(h, "pull" if pull else "build", stack, mode, registry if pull else None)
        if ok:
            st.button(cta, on_click=_go, args=(tile, step), key=f"setup_next_{mode}", type="primary",
                      width="stretch")


@st.fragment(run_every=2.0)
def _job_body() -> None:
    """Polls the build or pull every two seconds and shows its log. Hands over when it ends."""
    job = image_job.status()
    if job.get("state") not in image_job.ACTIVE_STATES:
        st.rerun()  # whole page: the sidebar logs the result
        return
    elapsed = time.time() - float(job.get("started") or time.time())
    pull = job.get("action") == "pull"
    total = int(job.get("total") or 1)
    done = int(job.get("done") or 0)
    frac = min(0.99, done / total) if pull else 0.0
    verb = "Pulling" if pull else "Building"
    st.progress(frac, text=f"{verb} {images.MODE_TITLE[job.get('mode', 'all')].lower()} images for "
                           f"{job.get('stack')} · {job.get('step') or ''} · {_clock(elapsed)} elapsed")
    if not pull:
        st.caption("A first build compiles from source (tens of minutes); later builds use the cache. "
                   "You can leave this page.")
    st.code(image_job.log_text(5000) or "(starting…)", language="text")
    if st.button("Cancel", key="btn_images_cancel"):
        image_job.cancel()
        activity("info", f"{verb} cancelled", "", phase="build", source="compose")
        st.rerun()


def _details(offline_rows: list[images.ImageStatus], lab_rows: list[images.ImageStatus]) -> None:
    table = pd.DataFrame([{
        "Used by": "Offline benchmark" if s.role == "bench" else "Simulated lab",
        "Image": s.ref, "State": STATE_TEXT[s.state],
        "ID": s.image_id or "-", "Size (MB)": s.size_mb or None,
        "Built": s.created[:19].replace("T", " ") if s.created else "-",
        "Digest": (s.digest[:19] + "…") if s.digest else "-",
        "Notes": "; ".join(s.problems),
    } for s in offline_rows + lab_rows])
    with st.expander("Image details"):
        st.dataframe(table, width="stretch", hide_index=True)
        st.caption("Ready = the pins in the image's `io.cipherops.*` labels equal its stack profile.")
    with st.expander("Stack profiles"):
        stacks = driver.list_stacks()
        pins = {s: images.read_pins(s) for s in stacks}
        keys = sorted({k for p in pins.values() for k in p})
        st.dataframe(pd.DataFrame({s: [pins[s].get(k, "") for k in keys] for s in stacks}, index=keys),
                     width="stretch")
    with st.expander("Terminal"):
        st.code(
            f"python scripts/images.py status --stack {images.OFFLINE_STACK} --mode offline\n"
            f"python scripts/images.py build  --stack {images.OFFLINE_STACK} --mode offline\n"
            f"python scripts/images.py pull   --stack {images.OFFLINE_STACK} --mode offline "
            f"--registry {images.DEFAULT_REGISTRY}\n\n"
            "python scripts/images.py status --stack draft06 --mode simulated\n"
            "python scripts/images.py build  --stack draft06 --mode simulated\n"
            f"python scripts/images.py pull   --stack draft06 --mode simulated --registry {images.DEFAULT_REGISTRY}\n\n"
            "scripts/publish_images.sh <stack>   # push to the registry (docker login first)",
            language="bash")


def image_step(h: driver.LabHandle) -> None:
    """The Node images page: both image sets, each with its status and button, then the details.

    While a build or pull runs, its progress and log replace the cards.
    """
    if image_job.is_running():
        _job_body()
        return
    offline_rows = images.status(images.OFFLINE_STACK, "offline")
    lab_rows = images.status(h.stack, "simulated")
    busy = bool(suite_mod.running_suite())
    offline = dict(title="Offline benchmark", mode="offline", stack=images.OFFLINE_STACK, rows=offline_rows,
                   stack_note="fixed: needs liboqs's FIPS 205 names",
                   purpose="One pinned core, no network.",
                   tile="offline", step="run", cta="Run →", busy=busy)
    lab = dict(title="Simulated lab", mode="simulated", stack=h.stack, rows=lab_rows,
               stack_note="change it in the sidebar",
               purpose="Containerised root, TLD and leaf servers with a validating resolver.",
               tile="simulated", step="prepare", cta="Prepare →", busy=busy)
    left, right = st.columns(2)
    with left:
        _card(h, **offline)
    with right:
        _card(h, **lab)
    _details(offline_rows, lab_rows)


def setup_page(h: driver.LabHandle, stt: dict) -> None:
    _page_header("setup", h, stt)
    if image_job.is_running():
        _job_body()
        return
    images_tab, log_tab = st.tabs(["Images", "Activity log"])
    with images_tab:
        image_step(h)
    with log_tab:
        activity_tab(h)
