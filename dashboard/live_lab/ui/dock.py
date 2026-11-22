"""Home dock: the two benchmark tracks and the supporting pages as cards in the same style as the pages they open.

Each card is drawn as HTML and an invisible Streamlit button is laid over it, so a click navigates exactly
like the plain buttons did. Chips and step numbers reuse the page-wide ``co-`` classes from ``app.py``.
"""

from __future__ import annotations

from html import escape
from typing import Callable
from urllib.parse import quote

import streamlit as st

_ON = ':is([class*="st-key-co_card_"]:hover,[class*="st-key-co_card_"]:has(button:focus-visible))'

_CSS = """
<style>
[data-testid="stHorizontalBlock"]:has([class*="st-key-co_card_"]){align-items:stretch}
[data-testid="stColumn"]:has([class*="st-key-co_card_"]) > [data-testid="stVerticalBlock"],
[data-testid="stLayoutWrapper"]:has(> [class*="st-key-co_card_"]){height:100%}
[class*="st-key-co_card_"]{position:relative;height:100%}
[class*="st-key-co_card_"] > [data-testid="stElementContainer"]:first-child{flex:1}
[class*="st-key-co_card_"] .stHtml,[class*="st-key-co_card_"] .stHtml > div{height:100%}
[class*="st-key-co_card_"] > [data-testid="stElementContainer"]:has(.stButton){position:absolute;inset:0;z-index:3;margin:0}
[class*="st-key-co_card_"] .stButton,[class*="st-key-co_card_"] .stButton button{width:100%;height:100%;opacity:0;cursor:pointer}

.seg{height:100%;box-sizing:border-box;border:1px solid rgba(250,250,250,.2);border-radius:.5rem;
  transition:border-color .2s,background .2s,transform .2s}
.seg.feat{padding:1rem 1.15rem;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;column-gap:1rem}
.seg.tile{padding:.85rem .95rem;display:grid;grid-template-columns:auto minmax(0,1fr);align-items:start;column-gap:.75rem}
%ON% .seg{border-color:rgba(255,75,75,.75);background:rgba(250,250,250,.025);transform:translateY(-2px)}
%ON% .seg.warn{border-color:rgba(255,209,102,.7)}
[class*="st-key-co_card_"]:has(button:focus-visible) .seg{outline:2px solid #ff4b4b;outline-offset:2px}

.seg .body{display:flex;flex-direction:column;gap:.4rem;min-width:0}
.seg.tile .body{gap:.25rem}
.seg .head{display:flex;flex-wrap:wrap;align-items:center;gap:.25rem .55rem}
.seg .title{font-weight:600;line-height:1.3}
.seg.feat .title{font-size:1.3rem}
.seg.tile .title{font-size:1.02rem}
.seg .desc{opacity:.7;font-size:.88rem;line-height:1.4}
.seg.tile .desc{font-size:.8rem;line-height:1.35;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;
  overflow:hidden}
.seg .co-steps{margin:0}
.seg .co-n{background:#2a2f3a;color:#d5dbe6;transition:background .2s,color .2s}
%ON% .seg .co-n{background:#ff4b4b;color:#fff}

.badge{width:2.4rem;height:2.4rem;border-radius:.5rem;display:flex;align-items:center;justify-content:center;
  font-size:1.2rem;background:rgba(250,250,250,.06);margin-top:.05rem}
.ico{display:inline-block;line-height:1;transform-origin:50% 85%;will-change:transform}
.seg.feat .ico{margin-right:.2rem}

/* Each card's emoji wakes up on hover: ring, bob, tip, plug, flip, sweep. */
%ON% .ico-offline{animation:co-ring .45s ease-in-out infinite}
%ON% .ico-simulated{animation:co-bob .65s ease-in-out infinite}
%ON% .ico-compare{animation:co-tip .7s ease-in-out infinite}
%ON% .ico-adopt{animation:co-plug .55s ease-in-out infinite}
%ON% .ico-learn{animation:co-flip .75s ease-in-out infinite}
%ON% .ico-reset{animation:co-sweep .5s ease-in-out infinite}

.go{width:2.1rem;height:2.1rem;border-radius:50%;display:flex;align-items:center;justify-content:center;
  color:rgba(250,250,250,.6);box-shadow:inset 0 0 0 1px rgba(250,250,250,.2);transition:color .2s,background .2s,box-shadow .2s}
.go i{width:14px;height:14px;background:currentColor;transition:transform .25s;
  -webkit-mask:%ARROW% center/contain no-repeat;mask:%ARROW% center/contain no-repeat}
%ON% .go{color:#fff;background:#ff4b4b;box-shadow:none}
%ON% .go i{transform:translateX(2px)}

@keyframes co-ring{0%,100%{transform:rotate(0)}20%{transform:rotate(18deg)}40%{transform:rotate(-16deg)}
  60%{transform:rotate(12deg)}80%{transform:rotate(-8deg)}}
@keyframes co-bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px) scale(1.06)}}
@keyframes co-tip{0%,100%{transform:rotate(0)}30%{transform:rotate(-18deg)}70%{transform:rotate(16deg)}}
@keyframes co-plug{0%,100%{transform:translateY(0)}35%{transform:translateY(3px) scale(.94)}70%{transform:translateY(-2px)}}
@keyframes co-flip{0%,100%{transform:rotateY(0)}50%{transform:rotateY(55deg)}}
@keyframes co-sweep{0%,100%{transform:rotate(-8deg)}50%{transform:rotate(28deg)}}
@media (prefers-reduced-motion:reduce){.seg,.seg *{animation:none !important;transition:none !important}%ON% .seg{transform:none}}
</style>
"""


def _mask(paths: str, stroke: float) -> str:
    """An outline icon as a CSS mask: st.html drops inline <svg>, but a data URI in a style survives."""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#000" '
           f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{paths}</svg>')
    return f'url("data:image/svg+xml,{quote(svg)}")'


_CSS = (_CSS.replace("%ON%", _ON)
        .replace("%ARROW%", _mask('<path d="M3.75 12h16.5M14.25 6l6 6-6 6"/>', 2.7)))


def styles() -> None:
    """Card styles; call once on the page before the cards."""
    st.html(_CSS)


def _chip(chip: tuple[str, str] | None) -> str:
    return f'<span class="co-chip co-{chip[1]}">{escape(chip[0])}</span>' if chip else ""


def _steps(names: list[str]) -> str:
    bits = [f'<span class="co-step"><span class="co-n">{i}</span>{escape(name)}</span>'
            for i, name in enumerate(names, 1)]
    return f'<div class="co-steps">{"<span class=co-arr>→</span>".join(bits)}</div>'


def _clickable(mode: str, html: str, label: str, on_click: Callable, args: tuple) -> None:
    with st.container(key=f"co_card_{mode}"):
        st.html(html)
        st.button(label, key=f"open_{mode}", on_click=on_click, args=args)


def _ico(mode: str, icon: str) -> str:
    return f'<span class="ico ico-{escape(mode)}" aria-hidden="true">{icon}</span>'


def feature(mode: str, icon: str, title: str, chip: tuple[str, str] | None, desc: str | None, steps: list[str],
            on_click: Callable, args: tuple) -> None:
    """A wide card for a benchmark track: name and status, purpose, numbered steps."""
    body = (f'<span class="body"><span class="head"><span class="title">{_ico(mode, icon)} {escape(title)}</span>'
            f'{_chip(chip)}</span>'
            + (f'<span class="desc">{escape(desc)}</span>' if desc else "")
            + f"{_steps(steps)}</span>")
    html = f'<div class="seg feat">{body}<span class="go"><i aria-hidden="true"></i></span></div>'
    _clickable(mode, html, f"Open {title}", on_click, args)


def tile(mode: str, icon: str, title: str, chip: tuple[str, str] | None, desc: str | None, on_click: Callable,
         args: tuple, warn: bool = False) -> None:
    """A small card for a supporting page: its icon, name and status, and a one-line purpose."""
    html = (f'<div class="seg tile{" warn" if warn else ""}"><span class="badge">{_ico(mode, icon)}</span>'
            f'<span class="body"><span class="head"><span class="title">{escape(title)}</span>{_chip(chip)}</span>'
            + (f'<span class="desc">{escape(desc)}</span>' if desc else "")
            + "</span></div>")
    _clickable(mode, html, f"Open {title}", on_click, args)
