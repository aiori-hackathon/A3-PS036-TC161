"""Multiplayer lanes catalogue and PowerDNS auth role (no Docker)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "ecosystem" / "src"))

from live_lab import images, lanes  # noqa: E402
from ecolab import topology  # noqa: E402


def test_presentation_lanes_shipped():
    ids = {l["id"] for l in lanes.list_lane_defs()}
    assert "presentation-draft06" in ids
    assert "presentation-latest" in ids
    assert "powerdns-slh-mtl" in ids
    assert "experimental-mldsa" in ids
    assert "experimental-mldsa-latest" in ids
    assert "powerdns-mldsa" in ids


def test_mldsa_lanes_use_active_draft_algos():
    ver = next(l for l in lanes.list_lane_defs() if l["id"] == "experimental-mldsa")
    assert ver["stack"] == "draft06"
    assert ver.get("auth_backend") == "nsd"
    assert "ml-dsa-mtl" in ver["algorithms"] and "ml-dsa" in ver["algorithms"]
    assert ver.get("status") == "ready"
    lat = next(l for l in lanes.list_lane_defs() if l["id"] == "experimental-mldsa-latest")
    assert lat["stack"] == "latest"
    assert "ml-dsa-mtl" in lat["algorithms"] and "ml-dsa" in lat["algorithms"]
    assert lat.get("status") == "ready"
    pl = next(l for l in lanes.list_lane_defs() if l["id"] == "presentation-latest")
    assert "ml-dsa-mtl" in pl["algorithms"]
    pdns = next(l for l in lanes.list_lane_defs() if l["id"] == "powerdns-mldsa")
    assert pdns["stack"] == "powerdns-draft06"
    assert pdns.get("auth_backend") == "powerdns"
    assert "ml-dsa-mtl" in pdns["algorithms"]
    assert pdns.get("status") == "ready"


def test_scenario_algs_include_mldsa():
    from live_lab import driver

    assert driver.SCENARIO_ALGS["ml-dsa"]["sld"] == "ML_DSA_44"
    assert driver.SCENARIO_ALGS["ml-dsa-mtl"]["sld"] == "ML-DSA-44-MTL-SHAKE-128"


def test_stack_labels_are_panel_friendly():
    from live_lab import driver

    assert driver.stack_label("draft06") == "Condensed MTL"
    assert driver.stack_label("latest") == "Always-full MTL"
    assert driver.stack_label("powerdns-draft06") == "PowerDNS · Condensed MTL"
    assert driver.DEFAULT_STACK == "draft06"  # backend id unchanged
    assert driver.stack_label("unknown-custom") == "unknown-custom"


def test_draft06_lane_points_at_default_stack():
    lane = next(l for l in lanes.list_lane_defs() if l["id"] == "presentation-draft06")
    assert lane["stack"] == "draft06"
    assert lane.get("presentation") is True
    assert lane.get("auth_backend") == "nsd"


def test_powerdns_stack_selects_pdns_auth_role():
    assert images.auth_role("draft06") == "nsd"
    assert images.auth_role("powerdns-draft06") == "pdns"
    roles = images.roles_for("simulated", "powerdns-draft06")
    assert "pdns" in roles and "nsd" not in roles
    assert "unbound" in roles and "tools" in roles
    assert images.roles_for("simulated", "draft06")[:1] == ("nsd",)


def test_topology_auth_backend_reads_stack_env():
    assert topology.auth_backend("draft06") == "nsd"
    assert topology.auth_backend("powerdns-draft06") == "powerdns"


def test_list_lanes_includes_image_fields():
    rows = lanes.list_lanes()
    assert rows
    assert "images_ok" in rows[0] and "missing" in rows[0]
