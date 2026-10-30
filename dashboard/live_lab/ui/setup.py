"""Setup tile: put the pinned images on this node, then prove they match the stack profile.

Two ways to get them: build them here from docker/Dockerfile, or pull them from a registry. Either way the images are
then checked against the stack profile (ecosystem/stacks/<stack>.env), and the Benchmark and Lab tiles only use images
that pass. Signing zones and starting a lab are the Lab tile; measuring is the Benchmark tile."""

from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from live_lab import driver, image_job, images
from live_lab import suite as suite_mod
from live_lab.ui.activity import activity_tab
from live_lab.ui.common import (
    _chip, _go, _hint, _log_chunks, _page_header, _sidebar_brand, activity, sb,
)

SOURCES = ("Build locally", "Pull from registry")
STATE_CHIP = {"ok": ("ready", "ok"), "mismatch": ("does not match", "warn"),
              "unverified": ("unverified", "warn"), "missing": ("missing", "idle")}


def _clock(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds < 3600 else f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def image_rows(stt: dict) -> list[images.ImageStatus]:
    """Every image of the selected stack, as ``lab.status`` read it from Docker on this run."""
    return [images.ImageStatus(**row) for row in stt.get("image_rows") or []]


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


def _start(h: driver.LabHandle, action: str, mode: str, registry: str | None) -> None:
    what = "Pull images" if action == "pull" else "Build images"
    activity("step", what, f"{h.stack} · {images.MODE_TITLE[mode]}" + (f" · {registry}" if registry else ""),
             phase="build", source="compose", h=h)
    try:
        image_job.start(action, h.stack, mode=mode, registry=registry)  # returns at once; the page shows the log
    except Exception as e:  # noqa: BLE001
        st.sidebar.error(str(e)[:600])
        activity("err", f"{what} failed to start", str(e)[:2000], phase="build", source="compose", h=h)
    st.rerun()


def sidebar_setup(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("setup")
    st.sidebar.markdown("### Setup")
    bench_busy = suite_mod.running_suite()
    if bench_busy:
        st.sidebar.warning(f"Benchmark {bench_busy['id']} is running. Building or pulling would disturb its timings, "
                           "so they are paused until it finishes or is cancelled.")
    _finish_image_job(h)
    running = image_job.is_running()

    _hint("Put the pinned images on this node. Everything else (Lab, Benchmark) runs inside them.")
    stack = sb(
        st.sidebar.selectbox, "stack", driver.DEFAULT_STACK, "Stack version",
        options=driver.list_stacks(), format_func=driver.stack_label,
        help="Which pinned Verisign DNS stack to use. "
             "draft06 = condensed-by-default (EDNS 65050). "
             "latest = always-full MTL, same MTL commit as the offline harness. "
             "Images are named per stack, so both can be on the node at once.",
    )
    pins = driver.stack_pins(stack)
    if pins:
        _hint(f"Pins for **{stack}**: MTL `{(pins.get('MTL_COMMIT') or '')[:12]}…` · "
              f"liboqs {pins.get('LIBOQS_TAG') or '?'} · {pins.get('OPENSSL_TAG') or '?'}.")

    mode = sb(st.sidebar.radio, "image_set", "all", "Images for", options=list(images.MODES),
              format_func=images.MODE_TITLE.get,
              help="Offline benchmark: the harness image. Simulated lab: NSD, Unbound and the signing tools. "
                   "Both are built from one Dockerfile and share the compiled OpenSSL/liboqs/MTL layers.")
    if stack != images.OFFLINE_STACK and mode in ("offline", "all"):
        st.sidebar.info(f"The offline harness image exists for the **{images.OFFLINE_STACK}** stack only (its liboqs "
                        "has the FIPS 205 names). "
                        + ("Pick that stack for it." if mode == "offline" else "Here, Both covers the lab images only."))
    source = sb(st.sidebar.radio, "image_mode", SOURCES[0], "Image source", options=list(SOURCES),
                help="Build compiles everything on this node (tens of minutes the first time). Pull downloads "
                     "images someone already built from the same stack profile; they are verified after the pull.")
    if source == SOURCES[1]:
        registry = sb(st.sidebar.text_input, "registry", images.DEFAULT_REGISTRY, "Registry",
                      placeholder="ghcr.io/org/benchmark",
                      help="Namespace holding cipherops-bench, cipherops-eco-nsd, -unbound and -tools, tagged "
                           "with the stack name. Published with scripts/publish_images.sh.")
        label, action, reg = ("Pulling…" if running else "Pull images"), "pull", registry
    else:
        label, action, reg = ("Building…" if running else "Build images"), "build", None
    if running:
        _hint("Running in the background. You can leave this page; the log is on the Setup page.")
    if st.sidebar.button(label, key="btn_images", width="stretch", type="primary",
                         disabled=bool(bench_busy or running or not images.roles_for(mode, stack)
                                       or (action == "pull" and not (reg or "").strip()))):
        _start(h, action, mode, reg)


def _table(rows: list[images.ImageStatus]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Image": s.title, "Reference": s.ref, "State": STATE_CHIP[s.state][0],
        "ID": s.image_id or "-", "Size (MB)": s.size_mb or None,
        "Built": s.created[:19].replace("T", " ") if s.created else "-",
        "Digest": (s.digest[:19] + "…") if s.digest else "-",
        "Notes": "; ".join(s.problems),
    } for s in rows])


def _mode_card(mode: str, rows: list[images.ImageStatus], tile: str, cta: str) -> None:
    mine = [s for s in rows if s.role in images.MODES[mode]]
    ok = bool(mine) and images.ready(mine)
    with st.container(border=True):
        if not mine:
            st.markdown(f"**{images.MODE_TITLE[mode]}** {_chip('not on this stack', 'idle')}", unsafe_allow_html=True)
            st.caption(f"Runs on the **{images.OFFLINE_STACK}** stack only. Pick it above to set this up.")
            return
        st.markdown(f"**{images.MODE_TITLE[mode]}** {_chip('ready' if ok else 'not ready', 'ok' if ok else 'idle')}",
                    unsafe_allow_html=True)
        if ok:
            st.caption(f"{len(mine)} image(s) verified against the stack profile.")
            st.button(cta, on_click=_go, args=(tile,), key=f"setup_next_{mode}", width="stretch")
        else:
            bad = [s for s in mine if s.state != "ok"]
            st.caption("Needs: " + (", ".join(s.ref for s in bad) or "images"))


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
    st.progress(frac, text=f"{verb} images for {job.get('stack')} · {job.get('step') or ''} · {_clock(elapsed)} elapsed")
    if not pull:
        st.caption("A first build compiles OpenSSL, liboqs, MTL, NSD and Unbound from source and can take tens of "
                   "minutes. Later builds reuse Docker's cache and take seconds. You can leave this page.")
    st.code(image_job.log_text(5000) or "(starting…)", language="text")
    if st.button("Cancel", key="btn_images_cancel"):
        image_job.cancel()
        activity("info", f"{verb} cancelled", "", phase="build", source="compose")
        st.rerun()


def _overview(h: driver.LabHandle, stt: dict) -> None:
    rows = image_rows(stt)
    st.markdown(f"#### Images on this node · stack **{h.stack}**")
    if not rows:
        st.info("Docker did not answer. Is it running, and may this user use it?")
        return
    a, b = st.columns(2)
    with a:
        _mode_card("offline", rows, "benchmark", "Continue to Benchmark →")
    with b:
        _mode_card("simulated", rows, "lab", "Continue to Lab →")
    st.dataframe(_table(rows), width="stretch", hide_index=True)
    st.caption("An image is *ready* only when the pins it was built from (its `io.cipherops.*` labels) equal the "
               f"stack profile `ecosystem/stacks/{h.stack}.env`. A pulled image that differs is reported here, never used.")

    with st.expander("Stack profile (the pins every image must match)"):
        st.dataframe(pd.DataFrame(sorted(images.read_pins(h.stack).items()), columns=["Pin", "Value"]),
                     width="stretch", hide_index=True)
    with st.expander("The same from a terminal"):
        st.code(
            f"python scripts/images.py status --stack {h.stack}\n"
            f"python scripts/images.py build  --stack {h.stack} --mode all\n"
            f"python scripts/images.py pull   --stack {h.stack} --mode all --registry {images.DEFAULT_REGISTRY}",
            language="bash")
        st.caption("Publish with `scripts/publish_images.sh <stack>` from a machine that is logged in to the registry; "
                   "the registry's owner makes each package public once.")


def setup_page(h: driver.LabHandle, stt: dict) -> None:
    _page_header("setup", h, stt)
    if image_job.is_running():
        _job_body()
        return
    images_tab, log_tab = st.tabs(["Images", "Activity log"])
    with images_tab:
        _overview(h, stt)
    with log_tab:
        activity_tab(h)
