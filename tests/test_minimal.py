"""Minimal mode (CIPHEROPS_MINIMAL=1): visitors can look and send lookups, but nothing they do changes the shared lab.

Every refusal is checked to happen before any command reaches Docker, and the lookup inputs that reach a shell in the
client container are validated in every mode."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from live_lab import (  # noqa: E402
    artifacts, driver, image_job, jobs, lab, minimal, offline_job, prepare_job, suite,
)

APP = ROOT / "dashboard" / "live_lab" / "app.py"


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(minimal.ENV, "1")


@pytest.fixture
def no_docker(monkeypatch):
    """Fail the test if anything tries to run a command."""
    def boom(*a, **k):
        raise AssertionError(f"a command was run: {a[:1]}")
    monkeypatch.setattr(lab, "_run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)


def test_off_by_default(monkeypatch):
    monkeypatch.delenv(minimal.ENV, raising=False)
    assert not minimal.enabled()
    minimal.refuse("anything")  # no-op
    assert minimal.mode_allowed("reset")


H = driver.LabHandle()


@pytest.mark.parametrize("call", [
    lambda: driver.up(H), lambda: driver.down(H), lambda: driver.activate(H, "mtl"), lambda: driver.flush(H),
    lambda: driver.start_capture(H), lambda: driver.stop_capture(H), lambda: driver.prepare(H),
    lambda: driver.stop_other_labs(H), lambda: driver.write_ui_config(),
    lambda: driver.sign_trials(H, ["mtl"], 1, [20], "/x"),
    lambda: artifacts.clear_demo_data(H), lambda: artifacts.clear_suites(), lambda: artifacts.remove_tree(ROOT / "x"),
    lambda: suite.start({"sizes": [20]}), lambda: suite.cancel("x"),
    lambda: offline_job.start("quick"), lambda: offline_job.cancel(),
    lambda: prepare_job.start(H, jobs=1, scenarios=["mtl"]), lambda: prepare_job.cancel(H),
    lambda: image_job.start("pull", "draft06"), lambda: image_job.cancel(),
])
def test_every_change_to_the_lab_is_refused_before_any_command(on, no_docker, call):
    with pytest.raises(minimal.MinimalModeRefused):
        call()


def test_visitor_actions_are_not_written_to_shared_files(on, no_docker, tmp_path, monkeypatch):
    prefs = tmp_path / "prefs.json"
    monkeypatch.setattr(artifacts, "UI_PREFS_PATH", prefs)
    artifacts.save_ui_prefs({"stack": "latest"})
    assert not prefs.exists()
    h = driver.LabHandle()
    monkeypatch.setattr(driver.LabHandle, "capture_dir", property(lambda self: tmp_path / "cap"))
    artifacts.append_session_query(h, {"qname": "x"})
    artifacts.append_activity_file(h, "line")
    artifacts.save_session(h, {"queries": []})
    assert not (tmp_path / "cap").exists()
    status = tmp_path / "job"
    status.mkdir()
    (status / "status.json").write_text('{"state": "done"}')
    jobs.mark_handled(status)
    assert "handled" not in (status / "status.json").read_text()


@pytest.mark.parametrize("qname,qtype", [
    ("x.test; docker ps", "A"), ("$(id)", "A"), ("`id`", "A"), ("x.test", "A; id"), ("x.test", "A|id"),
    ("-x", "A"), ("x.test\nid", "A"), ("x.test", ""), ("", "A"), ("a b", "A"),
])
def test_probe_rejects_anything_that_is_not_a_dns_name_in_every_mode(no_docker, monkeypatch, qname, qtype):
    monkeypatch.delenv(minimal.ENV, raising=False)
    with pytest.raises(ValueError):
        driver.probe(H, qname, qtype)


@pytest.mark.parametrize("qname,qtype", [("x.test; id", "A"), ("x.test", "A; id"), ("", "A")])
def test_check_probe_args_rejects_bad_lookups_without_touching_docker(no_docker, qname, qtype):
    with pytest.raises(ValueError):
        driver.check_probe_args(qname, qtype, "10.53.0.53")
    assert driver.check_probe_args("*.Zone00.test", "aaaa", "10.53.0.2") == ("*.Zone00.test", "AAAA", "10.53.0.2")


def test_query_page_validates_before_it_starts_a_capture():
    src = (ROOT / "dashboard" / "live_lab" / "ui" / "live.py").read_text(encoding="utf-8")
    assert src.index("driver.check_probe_args(") < src.index("driver.start_capture(")


def test_probe_quotes_its_arguments(monkeypatch):
    monkeypatch.delenv(minimal.ENV, raising=False)
    seen = {}

    def fake_exec(h, inner, timeout=None):
        seen["inner"] = inner
        return '{"rcode": "NOERROR"}'
    monkeypatch.setattr(lab, "client_exec", fake_exec)
    driver.probe(H, "*.zone00.test", "aaaa", server="10.53.0.2")
    assert "'*.zone00.test' AAAA" in seen["inner"] and "--server 10.53.0.2" in seen["inner"]


def test_probe_and_dig_reach_only_the_lab_in_minimal_mode(on, no_docker):
    with pytest.raises(minimal.MinimalModeRefused):
        driver.probe(H, "example.com", "A", server="8.8.8.8")
    with pytest.raises(minimal.MinimalModeRefused):
        driver.dig(H, "dig @1.1.1.1 example.com A")
    with pytest.raises(minimal.MinimalModeRefused):
        driver.dig(H, "dig @lab.test x.test A")


def test_dig_to_a_lab_server_runs_with_a_short_timeout(on, monkeypatch):
    seen = {}

    def fake_run(cmd, timeout=None, check=True):
        seen["cmd"], seen["timeout"] = cmd, timeout
        return subprocess.CompletedProcess(cmd, 0, "ok", "")
    monkeypatch.setattr(lab, "_run", fake_run)
    monkeypatch.setattr(lab, "require_no_suite", lambda **k: None)
    monkeypatch.setattr(lab, "compose_cmd", lambda h, *args: ["docker", "compose", *args])
    res = driver.dig(H, "dig @10.53.0.2 . DNSKEY +dnssec")
    assert res["exit"] == 0 and seen["timeout"] == minimal.DIG_TIMEOUT_S and "@10.53.0.2" in seen["cmd"]


def test_cooldown_spaces_out_one_visitors_lookups(on):
    store = {}
    assert minimal.cooldown(store) == 0
    assert minimal.cooldown(store) > 0


def test_steps_and_pages(on):
    pytest.importorskip("streamlit")
    from live_lab.ui import common
    assert common.resolve_tile("setup") == ("home", None)
    assert common.resolve_tile("reset") == ("home", None)
    assert common.resolve_tile("offline", "run") == ("offline", "results")
    assert common.resolve_tile("simulated", "prepare") == ("simulated", "query")
    assert common.resolve_tile("lab") == ("simulated", "query")  # an old bookmark to Prepare
    assert common.resolve_tile("simulated", "benchmark") == ("simulated", "benchmark")
    assert common.resolve_tile("compare") == ("compare", None)


def _app(tile=None, step=None):
    testing = pytest.importorskip("streamlit.testing.v1")
    at = testing.AppTest.from_file(str(APP), default_timeout=180)
    if tile:
        at.query_params["tile"] = tile
    if step:
        at.query_params["step"] = step
    at.run()
    return at


@pytest.mark.parametrize("tile,step", [(None, None), ("offline", None), ("simulated", "query"),
                                       ("simulated", "benchmark"), ("compare", None), ("learn", None),
                                       ("setup", None), ("reset", None), ("simulated", "prepare")])
def test_every_page_renders_in_minimal_mode_with_the_notice(on, tile, step):
    at = _app(tile, step)
    assert not at.exception, [e.message for e in at.exception]
    assert any("Minimal mode" in i.value for i in at.info)
    labels = {b.label for b in at.button}
    assert not labels & {"Manage images", "Flush cache", "Sign zones", "Start", "Stop", "Run offline benchmark",
                         "Run benchmark", "Build images", "Pull images"}


def test_password_gate(on, monkeypatch):
    monkeypatch.setenv(minimal.PASSWORD_ENV, "s3cret")
    at = _app()
    assert any(t.label == "Access code" for t in at.text_input)
    assert not any("Offline benchmark" in (m.value or "") for m in at.markdown)
    at.text_input[0].input("s3cret")
    at.button[0].click().run()
    assert at.session_state["minimal_ok"]


def test_node_script_serves_only_the_minimal_dashboard_on_localhost():
    script = ROOT / "deploy" / "aws" / "minimal.sh"
    text = script.read_text()
    subprocess.run(["bash", "-n", str(script)], check=True)
    assert "Environment=CIPHEROPS_MINIMAL=1" in text
    assert "--server.address 127.0.0.1" in text and "--server.address 0.0.0.0" not in text
    assert "--url http://127.0.0.1:" in text  # the tunnel points at the minimal dashboard only
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert ":8501" not in code  # the team's dashboard is never touched


MINIMAL_SH = ROOT / "deploy" / "aws" / "minimal.sh"


def _minimal_sh(tmp_path, *args, env=None):
    """Run `minimal.sh start ...` as if root. Every case below must stop at input validation, before any change."""
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    (fake / "id").write_text("#!/bin/sh\necho 0\n")
    (fake / "id").chmod(0o755)
    run_env = {"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(tmp_path), **(env or {})}
    return subprocess.run(["bash", str(MINIMAL_SH), "start", *args], capture_output=True, text=True, env=run_env,
                          stdin=subprocess.DEVNULL, timeout=20)


@pytest.mark.parametrize("args,env,message", [
    (["--domain", "not a host"], {"TUNNEL_TOKEN": "x" * 60}, "not a host name"),
    (["--domain", "-x.example.com"], {"TUNNEL_TOKEN": "x" * 60}, "not a host name"),
    (["--domain", "eval.example.com"], {}, "tunnel token"),
    (["--domain", "eval.example.com"], {"TUNNEL_TOKEN": "short"}, "tunnel token"),
    (["--domain", "eval.example.com"], {"TUNNEL_TOKEN": "x" * 60 + "\nEVIL=1"}, "tunnel token"),
    (["--nope"], {}, "unknown option"),
])
def test_named_tunnel_inputs_are_checked_before_anything_changes(tmp_path, args, env, message):
    done = _minimal_sh(tmp_path, *args, env=env)
    assert done.returncode == 2 and message in done.stderr


def test_minimal_sh_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(MINIMAL_SH)]).returncode == 0


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("1 ", True), (' "1" ', True), ("'1'", True), ("true", True), ("ON", True), ("yes", True),
    ("0", False), ("", False), ("no", False), ("false", False), ("11", False),
])
def test_minimal_mode_flag_survives_windows_quoting(monkeypatch, value, expected):
    monkeypatch.setenv(minimal.ENV, value)
    assert minimal.enabled() is expected


def test_minimal_mode_is_off_when_the_variable_is_unset(monkeypatch):
    monkeypatch.delenv(minimal.ENV, raising=False)
    assert minimal.enabled() is False
