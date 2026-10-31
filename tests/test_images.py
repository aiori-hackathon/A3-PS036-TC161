"""Image catalogue: what each mode needs, and verification of an image against the stack profile. No Docker."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from live_lab import images  # noqa: E402

PINS = {"STACK": "latest", "OPENSSL_TAG": "openssl-3.6.0", "LIBOQS_TAG": "0.16.0", "MTL_COMMIT": "m" * 40,
        "LDNS_COMMIT": "l" * 40, "NSD_COMMIT": "n" * 40, "UNBOUND_COMMIT": "u" * 40}


def labelled(role, **over):
    labels = {"io.cipherops.stack": "latest"}
    for key in images.ROLE_PINS[role]:
        labels[images._label_name(key)] = PINS[key]
    labels.update(over)
    return {"Id": "sha256:" + "a" * 64, "Created": "2026-10-30T00:00:00Z", "Size": 1_300_000_000,
            "Config": {"Labels": labels, "Env": []}, "RepoDigests": []}


def test_modes_name_the_images_each_benchmark_needs():
    assert images.roles_for("offline") == ("bench",)
    assert images.roles_for("simulated") == ("nsd", "unbound", "tools")
    assert set(images.roles_for("all")) == set(images.REPOS)
    with pytest.raises(ValueError):
        images.roles_for("nope")
    assert images.ref("tools", "draft06") == "cipherops-eco-tools:draft06"


def test_an_image_with_matching_labels_is_ok():
    st = images.check("bench", "latest", labelled("bench"), PINS)
    assert st.state == "ok" and st.present and st.problems == []
    assert st.image_id == "a" * 12 and st.size_mb == 1300.0


def test_a_pinned_value_that_differs_is_a_mismatch():
    info = labelled("nsd", **{"io.cipherops.nsd-commit": "x" * 40})
    st = images.check("nsd", "latest", info, PINS)
    assert st.state == "mismatch" and "NSD_COMMIT" in st.problems[0]


def test_an_image_built_for_another_stack_is_a_mismatch():
    st = images.check("bench", "latest", labelled("bench", **{"io.cipherops.stack": "draft06"}), PINS)
    assert st.state == "mismatch" and "draft06" in st.problems[0]


def test_images_built_before_the_labels_are_verified_from_their_env():
    info = labelled("bench")
    info["Config"] = {"Labels": None, "Env": ["CIPHEROPS_STACK=latest", "CIPHEROPS_OPENSSL_TAG=openssl-3.6.0",
                                              "CIPHEROPS_LIBOQS_TAG=0.16.0", "CIPHEROPS_MTL_COMMIT=" + "m" * 40]}
    assert images.check("bench", "latest", info, PINS).state == "ok"
    info["Config"]["Env"][2] = "CIPHEROPS_LIBOQS_TAG=0.14.0"
    assert images.check("bench", "latest", info, PINS).state == "mismatch"


def test_an_image_that_records_no_pins_cannot_be_verified():
    info = labelled("bench")
    info["Config"] = {"Labels": {}, "Env": []}
    st = images.check("bench", "latest", info, PINS)
    assert st.state == "unverified" and "does not record" in st.problems[0]


def test_a_missing_image_is_reported_and_blocks_readiness():
    st = images.check("tools", "latest", None, PINS)
    assert st.state == "missing" and not st.present
    assert not images.ready([st]) and not images.ready([])


def test_a_pulled_image_shows_its_registry_digest():
    info = labelled("bench")
    info["RepoDigests"] = ["ghcr.io/org/b/cipherops-bench@sha256:" + "d" * 64]
    assert images.check("bench", "latest", info, PINS).digest == "sha256:" + "d" * 64


def test_status_checks_every_image_of_the_mode(tmp_path, monkeypatch):
    (tmp_path / "stacks").mkdir()
    (tmp_path / "stacks" / "latest.env").write_text("\n".join(f"{k}={v}" for k, v in PINS.items()), encoding="utf-8")
    monkeypatch.setattr(images, "ECO", tmp_path)
    present = {"cipherops-eco-nsd:latest": labelled("nsd"), "cipherops-eco-tools:latest": labelled("tools")}
    rows = images.status("latest", "simulated", inspect=present.get)
    assert [r.role for r in rows] == ["nsd", "unbound", "tools"]
    assert [r.state for r in rows] == ["ok", "missing", "ok"]
    assert not images.ready(rows)
    assert "cipherops-eco-unbound:latest" in images.explain(rows, "simulated")
    assert "Build or pull them" in images.explain(rows, "simulated")
    assert images.explain([r for r in rows if r.state == "ok"], "simulated") == ""


def test_build_command_builds_only_the_services_of_the_mode(tmp_path, monkeypatch):
    (tmp_path / "stacks").mkdir()
    (tmp_path / "stacks" / "latest.env").write_text("STACK=latest\n", encoding="utf-8")
    monkeypatch.setattr(images, "ECO", tmp_path)
    cmd = images.build_command("latest", "offline")
    assert cmd[:2] == ["docker", "compose"] and cmd[-2:] == ["build", "bench"]
    assert cmd[cmd.index("-f") + 1].endswith("docker/build.yml")
    assert images.build_command("latest", "simulated")[-4:] == ["build", "nsd", "unbound", "tools"]
    with pytest.raises(RuntimeError, match="missing stack env"):
        images.build_command("nope")


def test_pull_plan_retags_remote_images_to_the_local_names():
    plan = images.pull_plan("https://ghcr.io/org/benchmark/", "latest", "offline")
    assert plan == [("ghcr.io/org/benchmark/cipherops-bench:latest", "cipherops-bench:latest")]
    cmds = images.pull_commands("ghcr.io/org/benchmark", "draft06", "simulated")
    assert cmds[0] == ["docker", "pull", "ghcr.io/org/benchmark/cipherops-eco-nsd:draft06"]
    assert cmds[1] == ["docker", "tag", "ghcr.io/org/benchmark/cipherops-eco-nsd:draft06", "cipherops-eco-nsd:draft06"]
    assert len(cmds) == 6


def test_the_offline_image_exists_for_the_offline_stack_only():
    assert images.roles_for("all", "latest") == ("bench", "nsd", "unbound", "tools")
    assert images.roles_for("all", "draft06") == ("nsd", "unbound", "tools")
    assert images.roles_for("offline", "draft06") == ()
    assert images.roles_for("offline") == ("bench",)  # no stack: the mode as defined


def test_nothing_is_built_or_pulled_for_an_offline_image_on_another_stack(tmp_path, monkeypatch):
    (tmp_path / "stacks").mkdir()
    (tmp_path / "stacks" / "draft06.env").write_text("STACK=draft06\n", encoding="utf-8")
    monkeypatch.setattr(images, "ECO", tmp_path)
    # an empty service list would make `docker compose build` build everything
    with pytest.raises(RuntimeError, match="runs on latest only|latest only"):
        images.build_command("draft06", "offline")
    with pytest.raises(RuntimeError, match="latest only"):
        images.pull_plan("ghcr.io/o/b", "draft06", "offline")
    cmds = images.pull_commands("ghcr.io/o/b", "draft06", "all")
    assert not any("cipherops-bench" in " ".join(c) for c in cmds) and len(cmds) == 6
    assert images.build_command("draft06", "all")[-3:] == ["nsd", "unbound", "tools"]


def test_a_stack_without_the_offline_image_explains_it():
    assert images.status("draft06", "offline", inspect=lambda r: None) == []
    assert "latest stack only" in images.explain([], "offline")


def test_an_empty_registry_is_refused():
    with pytest.raises(RuntimeError, match="registry is empty"):
        images.normalize_registry("  ")
