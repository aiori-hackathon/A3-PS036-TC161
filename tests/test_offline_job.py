"""The offline-harness background job: command, progress parsing, result pickup and exclusion. No Docker."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from live_lab import build_job, driver, offline_job as oj  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "ECO", tmp_path / "eco")
    monkeypatch.setattr(oj, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(oj, "ROOT", tmp_path)
    (tmp_path / "eco" / "stacks").mkdir(parents=True)
    (tmp_path / "eco" / "stacks" / "latest.env").write_text("STACK=latest\n", encoding="utf-8")
    oj.job_dir().mkdir(parents=True)
    return tmp_path


class FakeProc:
    def __init__(self, lines, code=0):
        self.stdout, self.code = iter(lines), code

    def wait(self):
        return self.code

    def poll(self):
        return self.code


def _run(sandbox, monkeypatch, outputs, **params):
    calls = []

    def popen(cmd, **kw):
        calls.append(cmd)
        if len(calls) == 2:  # the harness run creates its results folder
            (sandbox / "results" / "runs" / "20261030T000000Z-quick-demo").mkdir(parents=True)
        return FakeProc(*outputs[len(calls) - 1])

    monkeypatch.setattr(oj, "run_environment", lambda stack: {"CIPHEROPS_GIT_SHA": "abc"})
    code = oj.run({"preset": "quick", "stack": "latest", "started": 1.0, **params}, popen=popen)
    return code, calls, json.loads((oj.job_dir() / "status.json").read_text())


def test_harness_command_matches_the_shell_script():
    assert oj.harness_command("quick", None) == "python3 -m cipherops.runner run --config configs/quick.yaml"
    assert oj.harness_command("full", 4).endswith("--config configs/scenarios.yaml --trials 4")


def test_successful_run_records_progress_and_run_id(sandbox, monkeypatch):
    (sandbox / "results" / "runs").mkdir(parents=True)
    out = [(["building\n"], 0), ([" STEP 3/7  Timing\n", "done\n"], 0)]
    code, calls, st = _run(sandbox, monkeypatch, out)
    assert code == 0 and st["state"] == "done"
    assert st["run_id"] == "20261030T000000Z-quick-demo" and st["total"] == 7 and st["done"] == 2
    assert calls[0][-1] == "build" and oj.CONTAINER in calls[1]
    assert calls[1][-1].startswith("python3 -m cipherops.runner run")


def test_failed_build_stops_before_the_harness(sandbox, monkeypatch):
    (sandbox / "results" / "runs").mkdir(parents=True)
    code, calls, st = _run(sandbox, monkeypatch, [(["boom\n"], 1)])
    assert code == 1 and st["state"] == "failed" and len(calls) == 1 and "boom" in st["error"]


def test_a_running_offline_job_blocks_other_work(sandbox):
    (oj.job_dir() / "status.json").write_text(json.dumps({"state": "running", "pid": None, "started": 9e12}))
    assert driver.active_offline()
    with pytest.raises(driver.LabBusy):
        driver.require_no_offline()
    with pytest.raises(driver.LabBusy):
        oj.start("quick")


def test_unknown_preset_is_refused(sandbox):
    with pytest.raises(RuntimeError):
        oj.start("nope")


# ───────────── progress ─────────────

BUILD_LOG = """\
#1 [internal] load build definition from Dockerfile
#1 DONE 0.0s

#2 [build 1/3] FROM registry.example/fedora:42
#2 CACHED

#3 [build 2/3] RUN dnf -y install gcc
#3 0.4 Installing
#3 DONE 12.0s

#4 [build 3/3] RUN make
"""


def test_build_progress_counts_finished_steps_and_never_goes_back():
    from live_lab import build_job

    p = build_job.BuildProgress(expected=8)
    seen = []
    for line in BUILD_LOG.splitlines():
        if p.feed(line):
            seen.append(p.fraction)
    assert (len(p.done), len(p.steps)) == (3, 4)
    assert p.fraction == 3 / 8 and "make" in p.current  # not 3/4: steps are only seen once scheduled
    assert seen == sorted(seen)
    for i in range(5, 14):  # a longer build than expected grows the expectation; the bar never reaches 100% early
        p.feed(f"#{i} [more 1/1] RUN x")
        p.feed(f"#{i} DONE 1s")
    assert p.total == 13 and p.fraction == 12 / 13
    p.feed("#4 DONE 2s")  # every step seen is finished, but only the end of the build reaches 100%
    assert p.fraction == 0.99
    assert build_job.BuildProgress(expected=8).fraction == 0.0


def test_offline_progress_covers_the_build_then_every_harness_step():
    p = oj.OfflineProgress(expected_build_steps=4)
    for line in BUILD_LOG.splitlines():
        p.feed(line)
    assert 0 < p.fraction <= oj.BUILD_WEIGHT
    p.start_harness()
    p.feed("[08:00:00] measured trials: 2 x 1 datasets x 2 modes, more")
    p.feed("[08:00:00] A/A control: 'ed25519' also runs as 'x' on the same input")
    p.feed(" STEP 4/7  Sign and verify every RRset")
    start = p.fraction
    assert start > oj.BUILD_WEIGHT
    for k in range(1, 7):  # 2 trials x 1 dataset x (2 modes + control)
        p.feed(f"[08:00:0{k}] trial {(k + 2) // 3}/2 small slh-dsa     signed=10")
    assert p.fraction > start and p.detail() == "step 4 of 7, trial 6 of 6"
    p.feed(" STEP 7/7  Report and figures")
    assert 0.9 < p.fraction < 1.0  # only the finished run reaches 100%


def test_the_worker_writes_progress_and_finishes_at_100_percent(sandbox, monkeypatch):
    (sandbox / "results" / "runs").mkdir(parents=True)
    out = [(BUILD_LOG.splitlines(True), 0), ([" STEP 2/7  Gate\n", " STEP 5/7  Bytes\n"], 0)]
    code, calls, st = _run(sandbox, monkeypatch, out)
    assert code == 0 and st["progress"] == 1.0 and st["state"] == "done"
