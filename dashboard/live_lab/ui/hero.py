"""Home hero: dots that gather into the lab's ideas, one shape after another (``assets/hero_morph.html``).

Optional. To remove it, delete this file and the asset, and drop the ``hero`` lines in ``home.py``; Home then
shows its plain logo and title again.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import streamlit as st

from live_lab.ui.common import _brand

ASSET = Path(__file__).resolve().parents[1] / "assets" / "hero_morph.html"
HEIGHT = 196  # px; keep Home on one screen without a scrollbar
_MARK_PX = 220  # the mark is read back as dots, so it does not need to be large


def _mark_data_uri() -> str:
    """The team mark as a small PNG data URI, or '' when it is not available."""
    symbol, _full = _brand()
    if symbol is None:
        return ""
    try:
        img = symbol.copy()
        img.thumbnail((_MARK_PX, _MARK_PX))
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        return ""


@st.cache_data(show_spinner=False)
def _page(stamp: float) -> str:
    """The page with the mark inlined; ``stamp`` is the asset's mtime, so an edited asset is picked up."""
    return ASSET.read_text(encoding="utf-8").replace("__LOGO__", _mark_data_uri())


def available() -> bool:
    return ASSET.is_file()


def render(height: int = HEIGHT) -> None:
    html = _page(ASSET.stat().st_mtime)
    if hasattr(st, "iframe"):
        st.iframe(html, height=height, alt="Dots that gather into the lab's ideas, one after another")
    else:  # older Streamlit
        import streamlit.components.v1 as components
        components.html(html, height=height)
