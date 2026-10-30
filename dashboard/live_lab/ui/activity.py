"""Activity log: the feed under every tile and the log tab."""

from __future__ import annotations

import streamlit as st

from live_lab import driver
from live_lab.ui.common import LEVEL_ICON


def _activity_expander(h: driver.LabHandle) -> None:
    with st.expander("Activity log", expanded=False):
        activity_tab(h)


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
