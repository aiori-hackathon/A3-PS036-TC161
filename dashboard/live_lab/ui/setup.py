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
    _chip, _go, _hint, _log_chunks, _page_header, _sidebar_brand, activity, cfg, sb,
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


def sidebar_setup(h: driver.LabHandle, stt: dict, track: str | None = None) -> None:
    """Sidebar of the Images page (``track`` None) or of a track's image step (``offline`` / ``simulated``)."""
    _sidebar_brand("setup")
    st.sidebar.markdown("### Images")
    _hint({"offline": "Get the one image the offline benchmark runs in.",
           "simulated": "Get the three images the simulated lab runs in.",
           None: "Two image sets, each with its own button on the page: one for the offline benchmark, one for "
                 "the simulated lab."}[track])
    _finish_image_job(h)
    if suite_mod.running_suite():
        st.sidebar.warning("A benchmark is running. Building or pulling would disturb its timings, so they are "
                           "paused until it finishes or is cancelled.")

    source = sb(st.sidebar.radio, "image_mode", SOURCES[0], "Get images by", options=list(SOURCES),
                help="Build compiles everything on this node (tens of minutes the first time). Pull downloads "
                     "images someone already built from the same stack profile; they are verified after the pull.")
    if source == SOURCES[1]:
        sb(st.sidebar.text_input, "registry", images.DEFAULT_REGISTRY, "Registry", placeholder="ghcr.io/org/benchmark",
           help="Namespace holding cipherops-bench, cipherops-eco-nsd, -unbound and -tools, tagged with the stack "
                "name. Published with scripts/publish_images.sh.")

    if track == "offline":
        return  # the offline image is always the `latest` stack: nothing more to choose
    st.sidebar.markdown("---")
    stack = sb(st.sidebar.selectbox, "stack", driver.DEFAULT_STACK, "Simulated lab version",
               options=driver.list_stacks(), format_func=driver.stack_label,
               help="Which pinned Verisign DNS stack the lab runs. It only affects the simulated lab; the offline "
                    "benchmark always uses `latest`. Images are named per "
                    "stack, so both versions can be on the node at once.")
    pins = driver.stack_pins(stack)
    if pins:
        _hint(f"Pins for **{stack}**: MTL `{(pins.get('MTL_COMMIT') or '')[:12]}…` · "
              f"liboqs {pins.get('LIBOQS_TAG') or '?'} · {pins.get('OPENSSL_TAG') or '?'}.")


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
        st.caption("A first build compiles OpenSSL, liboqs, MTL, NSD and Unbound from source and can take tens of "
                   "minutes. Later builds reuse Docker's cache and take seconds. You can leave this page.")
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
        st.caption("An image is *ready* only when the pins it was built from (its `io.cipherops.*` labels) equal "
                   "its stack profile `ecosystem/stacks/<stack>.env`. A pulled image that differs is reported "
                   "here, never used.")
    with st.expander("Stack profiles (the pins every image must match)"):
        stacks = driver.list_stacks()
        pins = {s: images.read_pins(s) for s in stacks}
        keys = sorted({k for p in pins.values() for k in p})
        st.dataframe(pd.DataFrame({s: [pins[s].get(k, "") for k in keys] for s in stacks}, index=keys),
                     width="stretch")
    with st.expander("The same from a terminal"):
        st.code(
            f"python scripts/images.py status --stack {images.OFFLINE_STACK} --mode offline\n"
            f"python scripts/images.py build  --stack {images.OFFLINE_STACK} --mode offline\n"
            f"python scripts/images.py pull   --stack {images.OFFLINE_STACK} --mode offline "
            f"--registry {images.DEFAULT_REGISTRY}\n\n"
            "python scripts/images.py status --stack draft06 --mode simulated\n"
            "python scripts/images.py build  --stack draft06 --mode simulated\n"
            f"python scripts/images.py pull   --stack draft06 --mode simulated --registry {images.DEFAULT_REGISTRY}",
            language="bash")
        st.caption("Publish with `scripts/publish_images.sh <stack>` from a machine that is logged in to the registry; "
                   "the registry's owner makes each package public once.")


def image_step(h: driver.LabHandle, track: str | None = None) -> None:
    """The image cards: both on the Images page (``track`` None), or just the track's own card.

    While a build or pull runs, its progress and log replace the cards.
    """
    if image_job.is_running():
        _job_body()
        return
    offline_rows = images.status(images.OFFLINE_STACK, "offline")
    lab_rows = images.status(h.stack, "simulated")
    busy = bool(suite_mod.running_suite())
    offline = dict(title="Offline benchmark", mode="offline", stack=images.OFFLINE_STACK, rows=offline_rows,
                   stack_note="fixed: the harness needs liboqs's FIPS 205 names",
                   purpose="Times each signing and verification call on one pinned core with no network.",
                   tile="offline", step="run", cta="Continue: run it →", busy=busy)
    lab = dict(title="Simulated lab", mode="simulated", stack=h.stack, rows=lab_rows,
               stack_note="change it in the sidebar",
               purpose="Root, TLD and leaf servers behind a validating resolver, for querying by hand and for the "
                       "simulated benchmark.",
               tile="simulated", step="prepare", cta="Continue: prepare the lab →", busy=busy)
    if track == "offline":
        _card(h, **offline)
    elif track == "simulated":
        _card(h, **lab)
    else:
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
