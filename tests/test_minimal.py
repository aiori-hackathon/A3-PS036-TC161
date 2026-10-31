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
    lambda: driver.up(H), lambda: driver.down(H), lambda: driver.flush(H),
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


def test_tunnel_connected_finds_the_line_in_a_long_log_under_pipefail(tmp_path):
    """Regression: `journalctl | grep -q` under pipefail failed on a long log, so a connected tunnel was reported as down."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "journalctl").write_text(
        "#!/bin/sh\necho 'INF Registered tunnel connection connIndex=0'\nseq 1 300000\n")
    (fake / "journalctl").chmod(0o755)
    (fake / "empty").mkdir()
    env = {"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(tmp_path)}
    run = lambda: subprocess.run(  # noqa: E731
        ["bash", "-c", f"set -euo pipefail; source {MINIMAL_SH}; tunnel_connected && echo yes || echo no"],
        capture_output=True, text=True, env=env, timeout=20)
    assert run().stdout.strip() == "yes"
    (fake / "journalctl").write_text("#!/bin/sh\necho 'INF Starting tunnel'\nseq 1 1000\n")
    assert run().stdout.strip() == "no"


@pytest.fixture
def signed_lab(tmp_path, monkeypatch):
    """A lab with two signed algorithms (ecdsa active) and a recorder for the commands sent to the client."""
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "x.yaml").write_text("name: eco-test\nscenarios:\n  ecdsa: {}\n  mtl: {}\n  slh-dsa: {}\n")
    for mod in (driver, lab, artifacts):
        monkeypatch.setattr(mod, "ECO", tmp_path)
    h = driver.LabHandle(config="configs/x.yaml", stack="draft06")
    for sc in ("ecdsa", "mtl"):
        (h.work / "zones" / sc).mkdir(parents=True)
        (h.work / "zones" / sc / "manifest.json").write_text("{}")
    (h.work / "active").mkdir()
    (h.work / "active" / "scenario.txt").write_text("ecdsa 1232\n")
    sent = []

    def fake_exec(hh, inner, timeout=None):
        sent.append(inner)
        scenario = inner.split("--scenario ")[1].split()[0]
        (hh.work / "active" / "scenario.txt").write_text(f"{scenario} 1232\n")  # what ecolab activate leaves behind
        return "active: ok\n"

    monkeypatch.setattr(lab, "client_exec", fake_exec)
    monkeypatch.setattr(lab, "require_not_signing", lambda hh: None)
    monkeypatch.setattr(minimal, "_last_switch", [float("-inf")])
    return h, sent


def test_a_visitor_can_switch_to_a_signed_algorithm(on, signed_lab):
    h, sent = signed_lab
    assert driver.activate(h, "mtl") == "active: ok\n"
    assert sent == ["python3 -m ecolab activate configs/x.yaml --scenario mtl --udp 1232"]


def test_a_switch_keeps_the_labs_udp_size_whatever_the_visitor_asks(on, signed_lab):
    h, sent = signed_lab
    (h.work / "active" / "scenario.txt").write_text("ecdsa 4096\n")
    driver.activate(h, "mtl", udp=1)
    assert sent[0].endswith("--udp 4096")


@pytest.mark.parametrize("scenario", ["slh-dsa", "nope", "ed25519"])
def test_a_visitor_cannot_switch_to_an_algorithm_the_lab_has_not_signed(on, signed_lab, scenario):
    h, sent = signed_lab
    with pytest.raises(minimal.MinimalModeRefused):
        driver.activate(h, scenario)  # slh-dsa is configured but has no signed zones
    assert sent == []


@pytest.mark.parametrize("scenario", ["mtl; id", "$(id)", "mtl --udp 1", "../x", "", "a b", "mtl\nid"])
def test_activate_rejects_names_that_are_not_algorithm_names_in_every_mode(monkeypatch, signed_lab, scenario):
    h, sent = signed_lab
    monkeypatch.delenv(minimal.ENV, raising=False)
    with pytest.raises(ValueError):
        driver.activate(h, scenario)
    assert sent == []


def test_switching_to_the_active_algorithm_restarts_nothing(on, signed_lab):
    h, sent = signed_lab
    assert "already" in driver.activate(h, "ecdsa")
    assert sent == []


def test_switches_are_spaced_out_for_the_whole_node(on, signed_lab):
    h, sent = signed_lab
    driver.activate(h, "mtl")
    with pytest.raises(minimal.MinimalModeRefused, match="moments ago"):
        driver.activate(h, "ecdsa")
    assert len(sent) == 1
    minimal._last_switch[0] -= minimal.SWITCH_COOLDOWN_S  # the pause is over
    driver.activate(h, "ecdsa")
    assert len(sent) == 2


def test_a_failed_switch_still_starts_the_pause_and_frees_the_lookup_slots(on, signed_lab, monkeypatch):
    h, _ = signed_lab
    monkeypatch.setattr(lab, "client_exec", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("docker down")))
    with pytest.raises(RuntimeError):
        driver.activate(h, "mtl")
    with pytest.raises(minimal.MinimalModeRefused, match="moments ago"):
        driver.activate(h, "mtl")
    for _ in range(minimal.MAX_CONCURRENT):  # every slot is free again
        assert minimal._slots.acquire(blocking=False)
    for _ in range(minimal.MAX_CONCURRENT):
        minimal._slots.release()


def test_only_one_visitor_switches_at_a_time(on, signed_lab, monkeypatch):
    h, _ = signed_lab
    with minimal.switch_algorithm():
        with pytest.raises(minimal.MinimalModeRefused, match="Another visitor"):
            driver.activate(h, "mtl")


def test_a_switch_waits_for_lookups_in_flight(on, monkeypatch):
    monkeypatch.setattr(minimal, "SWITCH_WAIT_S", 0.2)
    monkeypatch.setattr(minimal, "_last_switch", [float("-inf")])
    with minimal.lookup_slot():  # a visitor's lookup is running
        with pytest.raises(minimal.MinimalModeRefused, match="still running"):
            with minimal.switch_algorithm():
                pass
    with minimal.switch_algorithm():  # nothing was left held, and the failed attempt started no pause
        pass


def test_lookups_are_told_the_lab_is_busy_while_it_switches(on, monkeypatch):
    monkeypatch.setattr(minimal, "LOOKUP_WAIT_S", 0.1)
    monkeypatch.setattr(minimal, "_last_switch", [float("-inf")])
    with minimal.switch_algorithm():
        with pytest.raises(minimal.MinimalModeRefused, match="busy"):
            with minimal.lookup_slot():
                pass
    with minimal.lookup_slot():  # and they go through again afterwards
        pass


def test_outside_minimal_mode_switching_has_no_guard(monkeypatch, signed_lab):
    h, sent = signed_lab
    monkeypatch.delenv(minimal.ENV, raising=False)
    driver.activate(h, "mtl")
    driver.activate(h, "ecdsa")
    assert len(sent) == 2


def test_example_names_are_real_names_with_the_one_type_each_has(signed_lab):
    import json
    h, _ = signed_lab
    zdir = h.work / "zones" / "ecdsa"
    (zdir / "manifest.json").write_text(json.dumps({"zones": [
        {"origin": "test.", "tier": "tld", "signed_file": "test.zone.signed"},
        {"origin": "zone00.test.", "tier": "sld", "signed_file": "zone00.zone.signed"},
        {"origin": "zone01.test.", "tier": "sld", "signed_file": "zone01.zone.signed"}]}))
    (zdir / "zone00.zone.signed").write_text(
        "zone00.test.\t3600\tIN\tSOA\tns1 h 1 2 3 4 5\n"
        "host000000.zone00.test.\t3600\tIN\tA\t192.0.0.0\n"
        "host000000.zone00.test.\t3600\tIN\tRRSIG\tA 13 3 3600 x y 1 zone00.test. sig\n"
        "host000001.zone00.test.\t3600\tIN\tMX\t10 mail.zone00.test.\n"
        "abcdefg.zone00.test.\t3600\tIN\tNSEC3\t1 0 0 - x\n"
        "host000002.zone00.test.\t3600\tIN\tTXT\t\"hi\"\n")
    (zdir / "zone01.zone.signed").write_text("host000000.zone01.test.\t3600\tIN\tAAAA\t::1\n")
    assert driver.example_names(h, per_zone=2) == [
        ("host000000.zone00.test.", "A"), ("host000001.zone00.test.", "MX"), ("host000000.zone01.test.", "AAAA")]
    assert driver.example_names(h, zones=1, per_zone=9) == [
        ("host000000.zone00.test.", "A"), ("host000001.zone00.test.", "MX"), ("host000002.zone00.test.", "TXT")]


def test_example_names_are_empty_when_the_lab_is_not_signed(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, "ECO", tmp_path)
    assert driver.example_names(driver.LabHandle(config="configs/x.yaml", stack="draft06")) == []


@pytest.mark.parametrize("path,stats,expected", [
    ("Through Unbound", {"total.num.cachehits": 1.0, "total.num.cachemiss": 0.0}, "hit"),
    ("Through Unbound", {"total.num.cachehits": 0.0, "total.num.cachemiss": 1.0}, "miss"),
    ("Through Unbound", {"total.num.cachehits": 1.0, "total.num.cachemiss": 1.0}, ""),   # someone else's lookup too
    ("Through Unbound", {"total.num.cachehits": 0.0, "total.num.cachemiss": 0.0}, ""),
    ("Through Unbound", {}, ""),
    ("Direct to root", {"total.num.cachehits": 1.0, "total.num.cachemiss": 0.0}, ""),    # bypasses the resolver
])
def test_cache_state_comes_from_the_resolver_counters_and_says_nothing_when_unsure(path, stats, expected):
    from live_lab.ui import live
    assert live._cache_state({"path": path, "unbound_stats": stats}) == expected
