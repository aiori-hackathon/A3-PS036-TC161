"""Playable lanes: stack + DNS backend + algorithm set (multiplayer catalogue).

YAML ``presentation: true`` only affects sort order and which lane expands by default;
user-facing copy uses Default / Verisign / PowerDNS badges and descriptive titles.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from live_lab import driver, images
from live_lab.driver import ECO, REPO

LANES_DIR = ECO / "lanes"


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not data.get("id"):
        raise ValueError(f"lane file {path.name} needs an id")
    return data


def list_lane_defs() -> list[dict]:
    """Shipped lane YAML files, presentation first."""
    if not LANES_DIR.is_dir():
        return []
    rows = []
    for path in sorted(LANES_DIR.glob("*.yaml")):
        try:
            rows.append(_load_yaml(path))
        except Exception:  # noqa: BLE001
            continue
    rows.sort(key=lambda r: (not r.get("presentation"), r.get("id") or ""))
    return rows


def auth_role_for_stack(stack: str) -> str:
    """Compose/image role for authoritative servers: ``nsd`` or ``pdns``."""
    pins = images.read_pins(stack)
    be = (pins.get("AUTH_BACKEND") or "nsd").strip().lower()
    return "pdns" if be == "powerdns" else "nsd"


def _images_for_lane(lane: dict) -> tuple[bool, list[str]]:
    status = (lane.get("status") or "ready").strip().lower()
    if status == "blocked":
        return False, ["lane blocked until algorithm/signer support exists"]
    stack = lane.get("stack") or ""
    if not stack or not (ECO / "stacks" / f"{stack}.env").is_file():
        return False, [f"missing stack profile ecosystem/stacks/{stack}.env"]
    try:
        rows = images.status(stack, "simulated")
        ok = bool(rows) and all(r.state == "ok" for r in rows)
        missing = [r.ref for r in rows if r.state != "ok"]
        return ok, missing
    except Exception as e:  # noqa: BLE001
        return False, [str(e)[:160]]


def list_lanes() -> list[dict]:
    """Lane defs plus live image readiness (for Adoption)."""
    out = []
    for lane in list_lane_defs():
        images_ok, missing = _images_for_lane(lane)
        out.append({
            **lane,
            "images_ok": images_ok,
            "missing": missing,
            "playable": (lane.get("status") or "").lower() == "ready" or (
                (lane.get("status") or "").lower() == "experimental" and (ECO / "stacks" / f"{lane.get('stack')}.env").is_file()
            ),
        })
    # Custom stacks not covered by a YAML lane still show as dynamic lanes.
    known_stacks = {l.get("stack") for l in out}
    for path in sorted((ECO / "stacks").glob("*.env")):
        if path.stem in known_stacks:
            continue
        pins = images.read_pins(path.stem)
        try:
            rows = images.status(path.stem, "simulated")
            images_ok = bool(rows) and all(r.state == "ok" for r in rows)
            missing = [r.ref for r in rows if r.state != "ok"]
        except Exception as e:  # noqa: BLE001
            images_ok, missing = False, [str(e)[:120]]
        out.append({
            "id": f"custom-{path.stem}",
            "title": f"Custom stack · {path.stem}",
            "status": "ready",
            "presentation": False,
            "stack": path.stem,
            "auth_backend": (pins.get("AUTH_BACKEND") or "nsd").lower(),
            "resolver_backend": "unbound",
            "signer_backend": "ldns",
            "algorithms": [a for a in (pins.get("ADOPT_ALGORITHMS") or "").split(",") if a.strip()],
            "draft": pins.get("ADOPT_DRAFT", ""),
            "blurb": pins.get("ADOPT_NOTES") or "Stack profile from Adoption GitHub pins.",
            "peer": "",
            "images_ok": images_ok,
            "missing": missing,
            "playable": True,
            "custom": True,
        })
    return out


def get_lane(lane_id: str) -> dict | None:
    for lane in list_lanes():
        if lane.get("id") == lane_id:
            return lane
    return None


def presentation_lane_id() -> str:
    for lane in list_lane_defs():
        if lane.get("presentation") and lane.get("stack") == driver.DEFAULT_STACK:
            return str(lane["id"])
    return "presentation-draft06"
