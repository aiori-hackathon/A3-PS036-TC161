"""One source of truth for pinned versions: the stack profiles feed both the offline image and the lab images."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STACKS = sorted((ROOT / "ecosystem" / "stacks").glob("*.env"))
REQUIRED = ("STACK", "OPENSSL_TAG", "LIBOQS_TAG", "MTL_COMMIT", "LDNS_COMMIT", "NSD_COMMIT", "UNBOUND_COMMIT")


def _pins(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def test_there_are_profiles():
    assert {p.stem for p in STACKS} >= {"draft06", "latest"}


@pytest.mark.parametrize("path", STACKS, ids=lambda p: p.stem)
def test_profile_is_complete_and_named_after_its_file(path):
    pins = _pins(path)
    assert [k for k in REQUIRED if not pins.get(k)] == []
    assert pins["STACK"] == path.stem
    assert re.fullmatch(r"[0-9a-f]{40}", pins["MTL_COMMIT"])


def test_dockerfile_has_no_pins_of_its_own():
    """A default here would let an image drift from the profile every image is built from."""
    text = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    for name in ("OPENSSL_TAG", "LIBOQS_TAG", "MTL_COMMIT", "LDNS_COMMIT", "NSD_COMMIT", "UNBOUND_COMMIT"):
        assert re.search(rf"^ARG {name}\s*$", text, re.M), f"{name} must be declared without a default"


def test_build_file_takes_every_pin_from_the_profile():
    text = (ROOT / "docker" / "build.yml").read_text(encoding="utf-8")
    for name in REQUIRED:
        assert "${" + name in text
    for role in ("bench", "nsd", "unbound", "tools"):
        assert f"target: {role}" in text


def test_compose_runs_the_prebuilt_bench_image_of_the_profile():
    text = (ROOT / "docker" / "compose.yml").read_text(encoding="utf-8")
    assert "${STACK" in text and "build:" not in text
    assert "bench:pinned" not in text


def test_every_image_records_the_pins_it_was_built_from():
    """Setup verifies images against the profile through these labels."""
    text = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    for key in ("openssl-tag", "liboqs-tag", "mtl-commit", "ldns-commit", "nsd-commit", "unbound-commit", "stack"):
        assert f"io.cipherops.{key}=" in text
