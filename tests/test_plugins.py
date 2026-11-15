"""Adoption: custom stack profiles from GitHub pins (no Docker)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from live_lab import plugins  # noqa: E402


@pytest.fixture
def stacks_dir(tmp_path, monkeypatch):
    d = tmp_path / "stacks"
    d.mkdir()
    monkeypatch.setattr(plugins, "STACKS_DIR", d)
    return d


def test_defaults_are_verisign_https_git():
    for key in ("MTL_REPO", "LDNS_REPO", "NSD_REPO", "UNBOUND_REPO"):
        assert plugins.DEFAULTS[key].startswith("https://")
        assert plugins.DEFAULTS[key].endswith(".git")


def test_write_and_read_custom_stack(stacks_dir):
    path = plugins.write_stack_env(
        "my-fork",
        pins={
            "MTL_REPO": "https://github.com/example/MTL.git",
            "MTL_COMMIT": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "LDNS_COMMIT": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "NSD_COMMIT": "cccccccccccccccccccccccccccccccccccccccc",
            "UNBOUND_COMMIT": "dddddddddddddddddddddddddddddddddddddddd",
        },
        algorithms=["mtl", "slh-dsa"],
        draft="draft-example",
        notes="demo",
    )
    assert path.name == "my-fork.env"
    pins = plugins.read_pins("my-fork")
    assert pins["STACK"] == "my-fork"
    assert pins["MTL_REPO"] == "https://github.com/example/MTL.git"
    assert pins["MTL_COMMIT"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert pins["ADOPT_ALGORITHMS"] == "mtl,slh-dsa"
    assert pins["ADOPT_DRAFT"] == "draft-example"


def test_cannot_overwrite_shipped_name(stacks_dir):
    with pytest.raises(ValueError, match="shipped"):
        plugins.write_stack_env("draft06", pins={})


def test_bad_repo_rejected(stacks_dir):
    with pytest.raises(ValueError, match="MTL_REPO"):
        plugins.write_stack_env(
            "bad-repo",
            pins={"MTL_REPO": "git@github.com:x/y.git", "MTL_COMMIT": "a" * 40,
                  "LDNS_COMMIT": "b" * 40, "NSD_COMMIT": "c" * 40, "UNBOUND_COMMIT": "d" * 40},
        )


def test_overwrite_and_delete(stacks_dir):
    plugins.write_stack_env(
        "tmp-stack",
        pins={
            "MTL_COMMIT": "a" * 40, "LDNS_COMMIT": "b" * 40,
            "NSD_COMMIT": "c" * 40, "UNBOUND_COMMIT": "d" * 40,
        },
    )
    with pytest.raises(ValueError, match="already exists"):
        plugins.write_stack_env(
            "tmp-stack",
            pins={
                "MTL_COMMIT": "a" * 40, "LDNS_COMMIT": "b" * 40,
                "NSD_COMMIT": "c" * 40, "UNBOUND_COMMIT": "d" * 40,
            },
        )
    plugins.write_stack_env(
        "tmp-stack",
        pins={
            "MTL_COMMIT": "e" * 40, "LDNS_COMMIT": "b" * 40,
            "NSD_COMMIT": "c" * 40, "UNBOUND_COMMIT": "d" * 40,
        },
        overwrite=True,
    )
    assert plugins.read_pins("tmp-stack")["MTL_COMMIT"] == "e" * 40
    plugins.delete_custom_stack("tmp-stack")
    assert not (stacks_dir / "tmp-stack.env").is_file()


def test_aiori_note_mentions_suite():
    assert "suite.json" in plugins.AIORI_NOTE
    assert "AIORI" in plugins.AIORI_NOTE or "IMN" in plugins.AIORI_NOTE
