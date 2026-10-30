"""Demo-safety tests: nothing may disturb a running benchmark suite, the 'starting' window counts as
active, and dig's argument restrictions cannot be bypassed. No Docker and no Streamlit."""

import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "ecosystem" / "src"))

from live_lab import build_job, driver, prepare_job as pj, suite  # noqa: E402


@pytest.fixture
def eco(tmp_path, monkeypatch):
    """Point the driver and suite at an empty ecosystem folder and record docker calls."""
    monkeypatch.setattr(driver, "ECO", tmp_path)
    monkeypatch.setattr(driver, "SUITES_DIR", tmp_path / "results" / "suites")
    monkeypatch.setattr(suite, "SUITES", tmp_path / "results" / "suites")
    monkeypatch.delenv(driver.SUITE_OWNER_ENV, raising=False)
    calls = []
    monkeypatch.setattr(driver, "_run", lambda cmd, **kw: calls.append(cmd) or _Done())
    return tmp_path, calls


class _Done:
    returncode, stdout, stderr = 0, "", ""


def test_pid_check_is_local_and_fast():
    assert driver.pid_alive(os.getpid())
    assert not driver.pid_alive(2**22 - 1)  # not a real process
    started = time.perf_counter()
    for _ in range(30):
        driver.pid_alive(os.getpid())
    assert time.perf_counter() - started < 0.5


def _suite_status(root, sid, **st):
    d = root / "results" / "suites" / sid
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps(st), encoding="utf-8")
    (d / "params.json").write_text("{}", encoding="utf-8")
    return d


def _handle(root):
    (root / "configs").mkdir(exist_ok=True)
    (root / "configs" / "x.yaml").write_text("name: eco-test-lab\n", encoding="utf-8")
    h = driver.LabHandle(config="configs/x.yaml", stack="draft06")
    (root / "work" / h.tag / "zones").mkdir(parents=True, exist_ok=True)
    (h.work / "compose.yml").write_text("services: {}", encoding="utf-8")
    return h


# ───────────── suite recognised as active, including while starting ─────────────

def test_starting_suite_without_pid_is_active(eco):
    root, _ = eco
    _suite_status(root, "s1", state="starting", started=time.time())
    assert driver.active_suite_id() == "s1"
    assert suite.running_suite()["id"] == "s1"


def test_starting_suite_that_never_appears_ages_out(eco):
    root, _ = eco
    _suite_status(root, "s1", state="starting", started=time.time() - 3600)
    assert driver.active_suite_id() is None
    assert suite.status("s1")["state"] == "stopped"


def test_running_suite_with_dead_pid_is_not_active(eco, monkeypatch):
    root, _ = eco
    _suite_status(root, "s1", state="running", pid=4242, started=time.time())
    monkeypatch.setattr(driver, "pid_alive", lambda pid: False)
    assert driver.active_suite_id() is None


def test_second_suite_cannot_start_while_one_is_starting(eco, monkeypatch):
    root, _ = eco
    _suite_status(root, "s1", state="starting", started=time.time())
    with pytest.raises(driver.LabBusy):
        suite.start({"sizes": [20], "label": "x"})


def test_suite_start_is_refused_while_signing_runs(eco):
    root, _ = eco
    d = root / "jobs" / "eco-ui-small" / "prepare-live"
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"state": "starting", "started": time.time()}), encoding="utf-8")
    with pytest.raises(driver.LabBusy):
        suite.start({"sizes": [20], "label": "x"})


def test_start_records_pid_and_blocks_a_concurrent_start(eco, monkeypatch):
    root, _ = eco

    class _P:
        pid = 99999

    monkeypatch.setattr(suite.subprocess, "Popen", lambda *a, **k: _P())
    monkeypatch.setattr(driver, "pid_alive", lambda pid: True)
    sid = suite.start({"sizes": [20], "label": "x"})
    assert suite.status(sid)["state"] == "starting" and suite.status(sid)["pid"] == 99999
    with pytest.raises(driver.LabBusy):
        suite.start({"sizes": [20], "label": "y"})


def test_stale_start_lock_is_reclaimed(eco):
    root, _ = eco
    lock = root / "results" / "suites" / ".start.lock"
    lock.mkdir(parents=True)
    old = time.time() - 120
    import os
    os.utime(lock, (old, old))
    with suite._start_lock(timeout_s=1):
        pass


# ───────────── lab operations refuse to run during a suite ─────────────

@pytest.mark.parametrize("op", [
    lambda h: driver.up(h),
    lambda h: driver.down(h),
    lambda h: driver.flush(h),
    lambda h: driver.activate(h, "mtl"),
    lambda h: driver.probe(h, "a.test"),
    lambda h: driver.dig(h, "dig a.test"),
    lambda h: build_job.start("draft06"),
    lambda h: driver.pull_images(h, "reg.example"),
    lambda h: driver.clear_demo_data(h, work=True),
    lambda h: driver.clear_demo_data(h, suites=True),
    lambda h: driver.clear_suites(),
])
def test_lab_operations_are_refused_during_a_suite(eco, op):
    root, calls = eco
    _suite_status(root, "s1", state="running", pid=1, started=time.time())
    h = _handle(root)
    with pytest.raises(driver.LabBusy):
        op(h)
    assert calls == []  # nothing reached docker


def test_the_suite_process_itself_may_use_the_lab(eco, monkeypatch):
    root, calls = eco
    _suite_status(root, "s1", state="running", pid=1, started=time.time())
    monkeypatch.setenv(driver.SUITE_OWNER_ENV, "s1")
    driver.down(_handle(root))
    assert calls


def test_cancel_path_may_force_down(eco):
    root, calls = eco
    _suite_status(root, "s1", state="running", pid=1, started=time.time())
    driver.down(_handle(root), force=True)
    assert calls


def test_up_and_activate_refused_while_signing_the_same_lab(eco):
    root, calls = eco
    h = _handle(root)
    d = root / "jobs" / h.tag / "prepare-live"
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"state": "starting", "started": time.time()}), encoding="utf-8")
    for op in (lambda: driver.up(h), lambda: driver.activate(h, "mtl"),
               lambda: driver.clear_demo_data(h, work=True)):
        with pytest.raises(driver.LabBusy):
            op()
    assert calls == [] and h.work.is_dir()


def test_prepare_cannot_start_during_a_suite(eco):
    root, _ = eco
    _suite_status(root, "s1", state="running", pid=1, started=time.time())
    with pytest.raises(driver.LabBusy):
        pj.start(_handle(root), jobs=1, scenarios=["mtl"])


# ───────────── dig argument restrictions ─────────────

@pytest.mark.parametrize("line", [
    "dig -f /etc/passwd", "dig -4f /etc/passwd", "dig -vf /etc/passwd", "dig -xf/etc/passwd",
    "dig -k /etc/key", "dig -4k /etc/key", "dig -tA -f x", "dig a.test -h", "dig -r a.test",
    "dig -y hmac:k:s a.test", "dig -b 1.2.3.4 a.test", "dig -m a.test", "dig -46f x a.test",
    "dig -t", "dig -t -f", "dig -F x",
])
def test_dig_rejects_file_and_unlisted_options(line):
    with pytest.raises(ValueError):
        driver.dig_argv(line)


@pytest.mark.parametrize("line,expect", [
    ("dig host000000.zone00.test A +dnssec", ["@" + driver.UNBOUND_IP, "host000000.zone00.test", "A", "+dnssec"]),
    ("dig @10.53.0.1 x.test A +dnssec +norec", ["@10.53.0.1", "x.test", "A", "+dnssec", "+norec"]),
    ("dig -t A -c IN x.test", ["@" + driver.UNBOUND_IP, "-t", "A", "-c", "IN", "x.test"]),
    ("dig -4 x.test +ednsopt=65050", ["@" + driver.UNBOUND_IP, "-4", "x.test", "+ednsopt=65050"]),
])
def test_dig_accepts_normal_queries(line, expect):
    assert driver.dig_argv(line) == expect


def test_dig_presets_are_all_accepted(eco):
    root, _ = eco
    for line in driver.dig_presets(_handle(root)):
        driver.dig_argv(line)


# ───────────── Prepare must start even when Docker left work/ owned by root ─────────────

# POSIX permissions: os.geteuid() does not exist on Windows, and read-only directories do not block writes there
@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_prepare_starts_when_work_dir_is_not_writable(eco, monkeypatch):
    root, _ = eco
    h = _handle(root)
    monkeypatch.setattr(pj.subprocess, "Popen", lambda *a, **k: None)
    (root / "work").chmod(0o555)
    h.work.chmod(0o555)
    try:
        pj.start(h, jobs=1, scenarios=["mtl"])
    finally:
        (root / "work").chmod(0o755)
        h.work.chmod(0o755)
    assert pj.is_running(h)
    assert not str(pj.job_dir(h)).startswith(str(root / "work"))


def test_remove_tree_falls_back_to_docker_for_root_owned_files(eco, monkeypatch):
    root, calls = eco
    target = root / "work" / "stale"
    target.mkdir(parents=True)
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: None)  # as if the files belong to root
    assert driver.remove_tree(target) is False  # still there: the recorded docker call is a stub
    assert any(c[:2] == ["docker", "run"] and "rm" in c and c[-1] == "/eco/work/stale" for c in calls)


def test_remove_tree_refuses_paths_outside_ecosystem(eco, tmp_path_factory, monkeypatch):
    root, calls = eco
    outside = tmp_path_factory.mktemp("outside")
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: None)
    assert driver.remove_tree(outside) is False
    assert calls == []


# ───────────── image build runs as a background job ─────────────

def _build_status(root, **st):
    d = root / "jobs" / "_build"
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(json.dumps(st), encoding="utf-8")


def test_a_second_build_cannot_start_while_one_runs(eco):
    root, _ = eco
    _build_status(root, state="starting", stack="latest", started=time.time())
    assert build_job.is_running()
    with pytest.raises(driver.LabBusy):
        build_job.start("draft06")


def test_a_build_that_vanished_is_not_running(eco, monkeypatch):
    root, _ = eco
    _build_status(root, state="running", stack="latest", pid=4242, started=time.time())
    monkeypatch.setattr(driver, "pid_alive", lambda pid: False)
    assert not build_job.is_running()
    assert build_job.status()["state"] == "stopped"


def test_start_launches_a_detached_worker_and_returns(eco, monkeypatch):
    root, _ = eco
    (root / "stacks").mkdir()
    (root / "stacks" / "draft06.env").write_text("STACK=draft06\n", encoding="utf-8")
    launched = []
    monkeypatch.setattr(build_job.subprocess, "Popen", lambda *a, **k: launched.append(a))
    build_job.start("draft06")
    assert launched and build_job.status()["state"] == "starting" and build_job.is_running()


def test_up_and_signing_wait_for_a_build_of_the_same_stack(eco):
    root, calls = eco
    h = _handle(root)
    _build_status(root, state="starting", stack=h.stack, started=time.time())
    with pytest.raises(driver.LabBusy):
        driver.up(h)
    with pytest.raises(driver.LabBusy):
        pj.start(h, jobs=1, scenarios=["mtl"])
    assert calls == []
    _build_status(root, state="starting", stack="some-other-stack", started=time.time())
    driver.require_no_build(h.stack)  # a build of another stack does not block this one


def test_suite_cannot_start_while_images_build(eco):
    root, _ = eco
    _build_status(root, state="starting", stack="latest", started=time.time())
    with pytest.raises(driver.LabBusy):
        suite.start({"sizes": [20], "label": "x"})


class _FakeProc:
    def __init__(self, lines, code):
        self.stdout, self._code = iter(lines), code

    def poll(self):
        return self._code

    def wait(self):
        return self._code


@pytest.mark.parametrize("code,state", [(0, "done"), (1, "failed")])
def test_worker_streams_the_log_and_records_the_result(eco, monkeypatch, code, state):
    root, _ = eco
    (root / "stacks").mkdir()
    (root / "docker").mkdir()
    (root / "stacks" / "draft06.env").write_text("STACK=draft06\n", encoding="utf-8")
    (root / "docker" / "build.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "jobs" / "_build").mkdir(parents=True)
    rc = build_job.run({"stack": "draft06", "started": time.time()},
                       popen=lambda *a, **k: _FakeProc(["#1 step one\n", "#2 step two\n"], code))
    st = build_job.status()
    assert st["state"] == state and st["returncode"] == code and rc == (0 if code == 0 else 1)
    assert "#2 step two" in build_job.log_text() and build_job.log_text().startswith("$ docker compose")
    if code:
        assert "exited with 1" in st["error"]


def test_stacks_that_cannot_validate_a_scenario_are_flagged():
    assert driver.unvalidated_scenarios("latest", ["ecdsa", "slh-dsa", "mtl"]) == ["slh-dsa"]
    assert driver.unvalidated_scenarios("draft06", ["ecdsa", "slh-dsa", "mtl"]) == []
