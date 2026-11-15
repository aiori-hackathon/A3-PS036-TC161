"""Adoption: multiplayer lanes (stack + backend + algos) → build images → play end to end."""

from __future__ import annotations

import streamlit as st

from live_lab import driver, image_job, images, lanes, minimal, plugins
from live_lab.ui.common import _hint, _page_header, _sidebar_brand, _store, cfg


def sidebar_adopt(h, stt: dict) -> None:  # noqa: ARG001
    _sidebar_brand("adopt")
    st.sidebar.markdown("### Adoption")
    _hint(
        "Each lane is a ready-made path: Verisign MTL mode, PowerDNS auth, or custom pins. "
        f"Build its images, then open Live lab or Benchmark. "
        f"Default lab version: **{driver.stack_label(driver.DEFAULT_STACK)}**."
    )
    st.sidebar.markdown("---")
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_adopt"):
        st.rerun()


def _select_stack(name: str) -> None:
    """Make this stack the lab version (same as Setup / Query picker)."""
    _store()["stack"] = name
    st.session_state.w_stack = name
    prefs = dict(driver.infer_ui_from_disk() or {})
    prefs["stack"] = name
    prefs["saved_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    driver.save_ui_prefs(prefs)
    if "lab_handle" in st.session_state:
        st.session_state.lab_handle.stack = name


def _finish_build_banner(h) -> None:  # noqa: ARG001
    stt = image_job.status()
    state = stt.get("state")
    if state not in ("done", "failed", "stopped") or stt.get("handled"):
        return
    image_job.mark_handled()
    verb = "Pull" if stt.get("action") == "pull" else "Build"
    stack = stt.get("stack") or "?"
    if state == "done":
        st.success(
            f"{verb} finished for **{driver.stack_label(stack)}**. "
            "Activate that lane and open Query or Benchmark.")
    else:
        msg = str(stt.get("error") or f"{verb} did not finish.")[:800]
        st.error(f"{verb} failed for **{driver.stack_label(stack)}**: {msg}")


def _status_badge(lane: dict) -> str:
    stt = (lane.get("status") or "").lower()
    # "presentation" in YAML is internal (sort / default expand) — show Default only for the shipped default stack.
    if lane.get("presentation") and lane.get("stack") == driver.DEFAULT_STACK:
        return ":blue-badge[Default]"
    if lane.get("presentation"):
        return ":blue-badge[Verisign]"
    if stt == "blocked":
        return ":red-badge[Blocked]"
    if stt == "experimental":
        return ":orange-badge[Experimental]"
    if lane.get("custom"):
        return ":green-badge[Custom]"
    if (lane.get("auth_backend") or "").lower() == "powerdns":
        return ":violet-badge[PowerDNS]"
    return ":green-badge[Ready]"


def _lanes_tab(h) -> None:  # noqa: ARG001
    st.markdown(
        "Each **lane** is a full path: lab version (MTL mode) + auth/resolver + algorithms. "
        "**Default** is Condensed MTL on Verisign NSD + Unbound. "
        "Other lanes switch MTL mode (always-full), algorithms (ML-DSA), or auth (PowerDNS)."
    )
    current = cfg("stack", driver.DEFAULT_STACK)
    rows = lanes.list_lanes()
    if not rows:
        st.warning("No lanes under `ecosystem/lanes/`.")
        return

    for lane in rows:
        lid = lane["id"]
        stack = lane.get("stack") or ""
        img = ":green-badge[Images ready]" if lane.get("images_ok") else ":orange-badge[Build needed]"
        active = ":violet-badge[Active]" if stack == current else ""
        with st.expander(
            f"{_status_badge(lane)} {img} {active}  **{lane.get('title', lid)}**",
            expanded=bool(lane.get("presentation") and stack == driver.DEFAULT_STACK),
        ):
            st.markdown(
                f"- **Lab version:** **{driver.stack_label(stack)}**\n"
                f"- **Auth:** `{lane.get('auth_backend', 'nsd')}` · "
                f"**Resolver:** `{lane.get('resolver_backend', 'unbound')}` · "
                f"**Signer:** `{lane.get('signer_backend', 'ldns')}`\n"
                f"- **Algorithms:** {', '.join(lane.get('algorithms') or []) or '—'}"
            )
            if lane.get("draft"):
                st.caption(f"Draft: {lane['draft']}")
            if lane.get("peer"):
                st.caption(lane["peer"])
            if lane.get("blurb"):
                st.write(lane["blurb"])
            if lane.get("missing") and not lane.get("images_ok"):
                st.warning("Missing or mismatched: " + ", ".join(lane["missing"][:8]))

            blocked = (lane.get("status") or "").lower() == "blocked"
            if minimal.enabled():
                st.caption("Minimal mode: lanes are read-only here. Deploy a full copy to switch stacks or build.")
            else:
                c1, c2, c3 = st.columns(3)
                with c1:
                    if st.button(
                        "Use this lane",
                        key=f"lane_use_{lid}",
                        type="primary",
                        disabled=blocked or not stack,
                    ):
                        _select_stack(stack)
                        st.success(
                            f"Active: **{driver.stack_label(stack)}**. "
                            "Open Simulated lab → Live lab.")
                        st.rerun()
                with c2:
                    can_build = (not blocked) and bool(stack)
                    if st.button(
                        "Build images" if not lane.get("images_ok") else "Rebuild images",
                        key=f"lane_build_{lid}",
                        disabled=not can_build,
                    ):
                        try:
                            image_job.start("build", stack, mode="simulated")
                            st.rerun()
                        except Exception as e:  # noqa: BLE001
                            st.error(str(e)[:600])
                with c3:
                    if lane.get("custom"):
                        if st.button("Delete stack profile", key=f"lane_del_{lid}"):
                            try:
                                plugins.delete_custom_stack(stack)
                                if cfg("stack") == stack:
                                    _select_stack(driver.DEFAULT_STACK)
                                st.rerun()
                            except Exception as e:  # noqa: BLE001
                                st.error(str(e)[:400])

    st.dataframe(
        [{
            "Lane": r.get("title"),
            "Lab version": driver.stack_label(r.get("stack") or ""),
            "Auth": r.get("auth_backend"),
            "Status": r.get("status"),
            "Images": "ok" if r.get("images_ok") else "need build",
            "Active": "yes" if r.get("stack") == current else "",
        } for r in rows],
        width="stretch",
        hide_index=True,
    )


def _pins_tab() -> None:
    if minimal.enabled():
        st.warning("Minimal mode is read-only. Deploy a full copy to create custom pin stacks.")
        return
    st.markdown(
        "Advanced: Verisign-compatible **GitHub pins** (MTL + mtl-mode ldns/nsd/unbound). "
        "Creates a custom stack that shows up as its own lane. "
        "For PowerDNS auth use the **powerdns-slh-mtl** lane (AUTH_BACKEND), not these NSD URL fields."
    )
    d = plugins.DEFAULTS
    with st.form("adopt_create"):
        name = st.text_input("Stack name", placeholder="my-mtl-fork")
        c_os, c_oqs = st.columns(2)
        with c_os:
            openssl = st.text_input("OPENSSL_TAG", value=d["OPENSSL_TAG"])
        with c_oqs:
            liboqs = st.text_input("LIBOQS_TAG", value=d["LIBOQS_TAG"])
        st.markdown("##### Repositories and commits")
        mtl_repo = st.text_input("MTL_REPO", value=d["MTL_REPO"])
        mtl_commit = st.text_input("MTL_COMMIT", value=d["MTL_COMMIT"])
        ldns_repo = st.text_input("LDNS_REPO", value=d["LDNS_REPO"])
        ldns_commit = st.text_input("LDNS_COMMIT", value=d["LDNS_COMMIT"])
        nsd_repo = st.text_input("NSD_REPO", value=d["NSD_REPO"])
        nsd_commit = st.text_input("NSD_COMMIT", value=d["NSD_COMMIT"])
        unbound_repo = st.text_input("UNBOUND_REPO", value=d["UNBOUND_REPO"])
        unbound_commit = st.text_input("UNBOUND_COMMIT", value=d["UNBOUND_COMMIT"])
        draft = st.text_input("IETF draft (optional)", placeholder="draft-…")
        algos = st.text_input("Algorithms hint (optional)", placeholder="mtl,slh-dsa")
        notes = st.text_area("Notes (optional)")
        overwrite = st.checkbox("Overwrite if this stack name already exists")
        build_now = st.checkbox("Start simulated-lab image build after save", value=True)
        submitted = st.form_submit_button("Save stack profile", type="primary")
    if submitted:
        try:
            path = plugins.write_stack_env(
                name,
                pins={
                    "OPENSSL_TAG": openssl, "LIBOQS_TAG": liboqs,
                    "MTL_REPO": mtl_repo, "MTL_COMMIT": mtl_commit,
                    "LDNS_REPO": ldns_repo, "LDNS_COMMIT": ldns_commit,
                    "NSD_REPO": nsd_repo, "NSD_COMMIT": nsd_commit,
                    "UNBOUND_REPO": unbound_repo, "UNBOUND_COMMIT": unbound_commit,
                },
                algorithms=[a.strip() for a in (algos or "").split(",") if a.strip()],
                draft=draft or "", notes=notes or "", overwrite=overwrite,
            )
            _select_stack(path.stem)
            st.success(f"Wrote stack `{path.stem}` — it appears under Playable lanes.")
            if build_now:
                image_job.start("build", path.stem, mode="simulated")
            st.rerun()
        except (ValueError, minimal.MinimalModeRefused) as e:
            st.error(str(e))
        except Exception as e:  # noqa: BLE001
            st.error(str(e)[:800])


def adopt_page(h, stt: dict) -> None:  # noqa: ARG001
    _page_header("adopt", h, stt)
    st.markdown(
        "Choose a **lane**, build its images, then open Simulated → Live lab "
        "(or Benchmark). "
        f"Default: **{driver.stack_label(driver.DEFAULT_STACK)}** on Verisign NSD + Unbound."
    )
    st.caption(plugins.AIORI_NOTE)

    _finish_build_banner(h)

    if image_job.is_running():
        job = image_job.status()
        with st.container(border=True):
            st.markdown(
                f"**Building** **{driver.stack_label(job.get('stack') or '')}** · "
                f"{images.MODE_TITLE.get(job.get('mode') or 'all', 'images')}…"
            )
            st.code(image_job.log_text(5000) or "(starting…)", language="text")
            if st.button("Cancel build", key="adopt_cancel_build"):
                try:
                    image_job.cancel()
                except Exception as e:  # noqa: BLE001
                    st.error(str(e)[:400])
                st.rerun()
        return

    lanes_tab, pins_tab = st.tabs(["Playable lanes", "Custom Verisign pins"])
    with lanes_tab:
        _lanes_tab(h)
    with pins_tab:
        _pins_tab()
