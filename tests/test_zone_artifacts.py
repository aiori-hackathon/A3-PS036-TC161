"""Prepare zone artifact browser helpers (no Docker)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from live_lab import artifacts, driver, lab  # noqa: E402


@pytest.fixture
def eco(tmp_path, monkeypatch):
    for mod in (driver, lab, artifacts):
        monkeypatch.setattr(mod, "ECO", tmp_path)
    return tmp_path


def _handle(root: Path) -> driver.LabHandle:
    (root / "configs").mkdir(exist_ok=True)
    (root / "configs" / "x.yaml").write_text("name: eco-test-lab\nscenarios: {}\n", encoding="utf-8")
    h = driver.LabHandle(config="configs/x.yaml", stack="draft06")
    (h.work / "zones").mkdir(parents=True, exist_ok=True)
    (h.work / "compose.yml").write_text("services: {}", encoding="utf-8")
    return h


def _write_scenario(h: driver.LabHandle, scenario: str = "mtl") -> Path:
    root = h.work / "zones" / scenario
    keys = root / "keys" / "example.test"
    keys.mkdir(parents=True)
    unsigned = keys / "example.test.zone"
    unsigned.write_text(
        "example.test. 3600 IN SOA ns1.example.test. hostmaster.example.test. 1 7200 3600 1209600 3600\n"
        "example.test. 3600 IN NS ns1.example.test.\n",
        encoding="utf-8",
    )
    (keys / "Kexample.test.+017+12345.private").write_text("Private-key-format: v1.3\n", encoding="utf-8")
    signed = root / "example.test.zone.signed"
    signed.write_text(
        "example.test. 3600 IN SOA ns1.example.test. hostmaster.example.test. 1 7200 3600 1209600 3600\n"
        "example.test. 3600 IN RRSIG SOA 17 2 3600 20300101000000 20200101000000 12345 example.test. aaaa\n"
        "example.test. 3600 IN NS ns1.example.test.\n"
        "example.test. 3600 IN RRSIG NS 17 2 3600 20300101000000 20200101000000 12345 example.test. bbbb\n",
        encoding="utf-8",
    )
    manifest = {
        "scenario": scenario,
        "algorithms": {"sld": "MTL-HMAC-SHA2-256"},
        "trust_anchor": ["example DS"],
        "zones": [{
            "origin": "example.test.",
            "tier": "sld",
            "algorithm": "MTL-HMAC-SHA2-256",
            "signed_file": "example.test.zone.signed",
            "signed_bytes": signed.stat().st_size,
            "rrsig_count": 2,
            "n_names": 1,
            "sign_seconds": 0.1,
            "verify_seconds": 0.01,
        }],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_list_zone_artifacts_resolves_signed_and_unsigned(eco):
    h = _handle(eco)
    _write_scenario(h)
    rows = artifacts.list_zone_artifacts(h, "mtl")
    assert len(rows) == 1
    row = rows[0]
    assert row["origin"] == "example.test."
    assert row["tier"] == "sld"
    assert row["stem"] == "example.test"
    assert row["signed_exists"] and row["unsigned_exists"] and row["keys_present"]
    assert row["signed_path"].endswith("example.test.zone.signed")
    assert row["unsigned_path"].endswith("example.test.zone")


def test_list_zone_artifacts_empty_without_manifest(eco):
    h = _handle(eco)
    assert artifacts.list_zone_artifacts(h, "missing") == []


def test_read_zone_preview_truncates_by_lines(eco):
    path = eco / "big.zone"
    path.write_text("\n".join(f"line{i}" for i in range(500)) + "\n", encoding="utf-8")
    preview = artifacts.read_zone_preview(path, max_bytes=10_000_000, max_lines=10)
    assert preview["error"] is None
    assert preview["truncated"] is True
    assert preview["text"].count("\n") == 10
    assert "line9" in preview["text"]
    assert "line10" not in preview["text"]


def test_read_zone_preview_truncates_by_bytes(eco):
    path = eco / "wide.zone"
    path.write_text("x" * 1000, encoding="utf-8")
    preview = artifacts.read_zone_preview(path, max_bytes=50, max_lines=200)
    assert preview["error"] is None
    assert preview["truncated"] is True
    assert len(preview["text"].encode("utf-8")) <= 50


def test_read_zone_preview_refuses_private_keys(eco):
    path = eco / "Kexample.+017+1.private"
    path.write_text("secret", encoding="utf-8")
    preview = artifacts.read_zone_preview(path)
    assert preview["text"] == ""
    assert "Private key" in (preview["error"] or "")


def test_read_zone_preview_missing_file(eco):
    preview = artifacts.read_zone_preview(eco / "nope.zone")
    assert preview["error"]
    assert preview["text"] == ""


def test_driver_reexports_zone_helpers(eco):
    h = _handle(eco)
    _write_scenario(h)
    assert len(driver.list_zone_artifacts(h, "mtl")) == 1
    signed = h.work / "zones" / "mtl" / "example.test.zone.signed"
    assert driver.read_zone_preview(signed)["error"] is None
