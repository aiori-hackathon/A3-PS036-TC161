"""Shared background-job plumbing and the image build/pull job. No Docker."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from live_lab import driver, image_job, images, jobs  # noqa: E402


def test_status_is_written_atomically_and_read_back(tmp_path):
    jobs.write_status(tmp_path, {"state": "running", "n": 1})
    assert jobs.read_json(tmp_path / "status.json") == {"state": "running", "n": 1}
    assert list(tmp_path.glob("*.tmp")) == []
    assert jobs.read_json(tmp_path / "missing.json") == {}


def test_mark_handled_and_cancelled_update_the_status(tmp_path):
    jobs.write_status(tmp_path, {"state": "done"})
    jobs.mark_handled(tmp_path)
    assert jobs.read_json(tmp_path / "status.json")["handled"] is True
    jobs.mark_cancelled(tmp_path)
    st = jobs.read_json(tmp_path / "status.json")
    assert st["state"] == "cancelled" and st["handled"] is True and "finished" in st


def test_log_tail_returns_the_end_of_the_log(tmp_path):
    assert jobs.log_tail(tmp_path) == ""
    (tmp_path / "progress.log").write_text("a" * 100 + "END", encoding="utf-8")
    assert jobs.log_tail(tmp_path, 10) == "a" * 7 + "END"


def test_run_logged_tees_the_output_and_returns_the_tail(tmp_path):
    class Proc:
        stdout = iter(["one\n", "two\n"])

        def wait(self):
            return 3

    code, tail = jobs.run_logged(["echo", "x"], tmp_path / "p.log", cwd=tmp_path, popen=lambda *a, **k: Proc())
    assert code == 3 and tail == ["one\n", "two\n"]
    assert (tmp_path / "p.log").read_text(encoding="utf-8") == "$ echo x\none\ntwo\n"


def test_pid_alive_knows_this_process_and_a_gone_one():
    import os
    assert jobs.pid_alive(os.getpid()) and not jobs.pid_alive(None) and not jobs.pid_alive(0)


# ───────────────────────────── image job ─────────────────────────────

@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "ECO", tmp_path)
    monkeypatch.setattr(images, "ECO", tmp_path)
    monkeypatch.setattr(driver, "SUITES_DIR", tmp_path / "results" / "suites")
    (tmp_path / "stacks").mkdir()
    (tmp_path / "stacks" / "latest.env").write_text("STACK=latest\n", encoding="utf-8")
    image_job.job_dir().mkdir(parents=True)
    return tmp_path


class FakeProc:
    def __init__(self, lines, code=0):
        self.stdout, self.code = iter(lines), code

    def wait(self):
        return self.code

    def poll(self):
        return self.code


def _run(monkeypatch, outputs, rows, **params):
    calls = []

    def popen(cmd, **kw):
        calls.append(cmd)
        return FakeProc(*outputs[len(calls) - 1])

    monkeypatch.setattr(images, "status", lambda stack, mode="all": rows)
    monkeypatch.setattr(images, "build_command", lambda stack, mode="all": ["docker", "compose", "build"])
    code = image_job.run({"action": "build", "stack": "latest", "mode": "offline", "registry": None,
                          "started": 1.0, **params}, popen=popen)
    return code, calls, json.loads((image_job.job_dir() / "status.json").read_text())


def _ok(role="bench"):
    return [images.ImageStatus(role=role, ref=images.ref(role, "latest"), present=True, state="ok")]


def test_a_build_that_ends_with_matching_images_is_done(sandbox, monkeypatch):
    code, calls, st = _run(monkeypatch, [(["#1 DONE\n"], 0)], _ok())
    assert code == 0 and st["state"] == "done" and calls == [["docker", "compose", "build"]]


def test_a_failed_command_fails_the_job_with_the_tail(sandbox, monkeypatch):
    code, _calls, st = _run(monkeypatch, [(["boom\n"], 1)], _ok())
    assert code == 1 and st["state"] == "failed" and "boom" in st["error"]


def test_pulled_images_that_do_not_match_the_profile_fail_the_job(sandbox, monkeypatch):
    bad = [images.ImageStatus(role="bench", ref="cipherops-bench:latest", present=True, state="mismatch",
                              problems=["MTL_COMMIT is aaaa, the stack profile pins bbbb"])]
    outputs = [(["pulled\n"], 0), (["tagged\n"], 0)]
    code, calls, st = _run(monkeypatch, outputs, bad, action="pull", registry="ghcr.io/org/b")
    assert code == 1 and st["state"] == "failed" and "do not match" in st["error"] and "MTL_COMMIT" in st["error"]
    assert calls[0] == ["docker", "pull", "ghcr.io/org/b/cipherops-bench:latest"]
    assert calls[1][:2] == ["docker", "tag"]


def test_pull_reports_which_image_is_being_fetched(sandbox, monkeypatch):
    seen = []
    real = jobs.write_status
    monkeypatch.setattr(image_job.jb, "write_status", lambda d, st: (seen.append(st.get("step")), real(d, st)))
    _run(monkeypatch, [(["ok\n"], 0), (["ok\n"], 0)], _ok(), action="pull", registry="ghcr.io/org/b")
    assert any(s and s.startswith("Pulling ghcr.io/org/b/cipherops-bench:latest") for s in seen)


def test_only_one_image_job_runs_at_a_time(sandbox, monkeypatch):
    (image_job.job_dir() / "status.json").write_text(
        json.dumps({"state": "running", "pid": None, "started": 9e12, "stack": "latest", "action": "pull"}))
    with pytest.raises(driver.LabBusy, match="pulled"):
        image_job.start("build", "latest")


def test_start_validates_the_action_and_the_registry(sandbox):
    with pytest.raises(RuntimeError, match="unknown action"):
        image_job.start("push", "latest")
    with pytest.raises(RuntimeError, match="registry is empty"):
        image_job.start("pull", "latest", registry="")


def test_start_launches_a_detached_worker(sandbox, monkeypatch):
    launched = []
    monkeypatch.setattr(image_job.jb, "spawn_worker", lambda module, d, params: launched.append((module, params)))
    image_job.start("pull", "latest", mode="simulated", registry="https://ghcr.io/org/b/")
    module, params = launched[0]
    assert module == "live_lab.image_job" and params["registry"] == "ghcr.io/org/b" and params["mode"] == "simulated"
    assert image_job.status()["state"] == "starting" and image_job.is_running()
