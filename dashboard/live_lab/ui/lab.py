"""Lab tile: sign the zones and start or stop the simulated lab, to query it by hand in Live.

The images come from Setup (built here or pulled); this tile only uses them. The simulated *benchmark* needs none of
this: it signs, starts and stops its own lab for every dataset size."""

from __future__ import annotations

import time

import pandas as pd
import plotly.express as px
import streamlit as st

from live_lab import compare, driver, image_job, prepare_job, prepare_story
from live_lab import glossary as gl
from live_lab import suite as suite_mod
from live_lab.ui.activity import activity_tab
from live_lab.ui.common import (
    ECO_DIR, _apply_ui_config, _embed_html, _go, _hint, _log_chunks, _page_header, _scenario_list,
    _shape, _sidebar_brand, _step_label, _warn_unvalidated, activity, cfg, prepare_metrics_visible,
    readiness, sb,
)

IMAGE_LABELS = {
    "cipherops-eco-nsd": "NSD authoritative servers",
    "cipherops-eco-unbound": "Unbound validating resolver",
    "cipherops-eco-tools": "Signing & probe tools",
}


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


def sidebar_lab(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("lab")
    st.sidebar.markdown("### Lab")
    bench_busy = suite_mod.running_suite()
    if bench_busy:
        st.sidebar.warning(f"Benchmark {bench_busy['id']} is using the lab. Sign zones, Start and Stop are "
                           "paused until it finishes or is cancelled.")

    r = readiness(stt)
    prepared_session = r["prepared"] and prepare_metrics_visible(h)
    _hint("Green means ready. Yellow means found on disk from an earlier session.")

    stack = h.stack
    building = image_job.is_running()
    if r["images"]:
        _hint(f"Images for **{stack}** are on this node and match the stack profile. Change the version in Setup.")
    else:
        st.sidebar.warning("The lab images are not ready. Build or pull them in Setup first.")
        st.sidebar.button("Go to Setup", on_click=_go, args=("setup",), key="lab_to_setup", width="stretch")

    # —— Step 1: prepare (zone design, then sign) ——
    prep_state = "done" if prepared_session else ("partial" if r["prepared"] else "none")
    st.sidebar.markdown(_step_label(1, gl.label("sign_zones"), prep_state))
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
        _hint("Build or pull the images in Setup first.")
    if signing:
        _hint("Signing is running. Watch it live on this page.")
    if st.sidebar.button(gl.label("sign_zones"), key="btn_prep", help=gl.tip("sign_zones"), width="stretch",
                         disabled=not sc or bool(shape_errors) or signing or bool(bench_busy) or building or no_images):
        _apply_ui_config(h)
        jobs = int(cfg("jobs", 2))
        scen_txt = ", ".join(sc)
        activity("step", "Signing zones", f"Size {size} · {scen_txt}", phase="prepare", source="prepare", h=h)
        try:
            prepare_job.start(h, jobs=jobs, scenarios=list(sc))  # returns at once; Lab shows it live
            st.session_state.pop("replay_fresh", None)
        except Exception as e:  # noqa: BLE001
            st.sidebar.error(str(e)[:600])
            activity("err", "Signing failed to start", str(e)[:2000], phase="prepare", source="prepare", h=h)
        st.rerun()

    # —— Step 2: start / stop ——
    st.sidebar.markdown(_step_label(2, "Start", "done" if r["running"] else "none"))
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


def prepare_live_panel(h: driver.LabHandle) -> None:
    st.markdown("### Signing live")
    _prepare_live_body(h)


def setup_replay(h: driver.LabHandle, *, key: str) -> None:
    """Animated replay of how the hierarchy was signed (built from the signing manifests on disk)."""
    manifests = driver.list_manifests(h) if (prepare_metrics_visible(h) and h.prepared()) else {}
    if not manifests:
        st.info("The signing replay appears here after you sign the zones (Step 1 in the sidebar).")
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
            st.success("The lab is signed, running and ready.")
            a, b, _ = st.columns([1, 1, 2])
            a.button("Continue to Live →", on_click=_go, args=("live",), key="lab_to_live",
                     type="primary", width="stretch")
            b.button("Continue to Benchmark →", on_click=_go, args=("benchmark",),
                     key="lab_to_bench", width="stretch")
    else:
        nxt = ("Build or pull the images in Setup" if not r["images"]
               else "Sign the zones (Step 1)" if not r["prepared"]
               else "Press Start to launch the containers (Step 2)")
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


def lab_page(h: driver.LabHandle, stt: dict) -> None:
    _page_header("lab", h, stt)
    if image_job.is_running():
        st.info("Images are being built or pulled in Setup. The lab buttons stay off until they are ready.")
        st.button("Open Setup", on_click=_go, args=("setup",), key="lab_open_setup")
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
