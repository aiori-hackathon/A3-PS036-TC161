"""The offline-harness background job: command, progress parsing, result pickup and exclusion. No Docker."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from live_lab import driver, images, offline_job as oj  # noqa: E402


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
        (sandbox / "results" / "runs" / "20261030T000000Z-quick-demo").mkdir(parents=True)  # the harness run
        return FakeProc(*outputs[len(calls) - 1])

    monkeypatch.setattr(oj, "run_environment", lambda stack: {"CIPHEROPS_GIT_SHA": "abc"})
    code = oj.run({"preset": "quick", "stack": "latest", "started": 1.0, **params}, popen=popen)
    return code, calls, json.loads((oj.job_dir() / "status.json").read_text())


def test_harness_command_matches_the_shell_script():
    assert oj.harness_command("quick", None) == "python3 -m cipherops.runner run --config configs/quick.yaml"
    assert oj.harness_command("full", 4).endswith("--config configs/scenarios.yaml --trials 4")


def test_successful_run_records_progress_and_run_id(sandbox, monkeypatch):
    (sandbox / "results" / "runs").mkdir(parents=True)
    out = [([" STEP 3/7  Timing\n", "done\n"], 0)]
    code, calls, st = _run(sandbox, monkeypatch, out)
    assert code == 0 and st["state"] == "done"
    assert st["run_id"] == "20261030T000000Z-quick-demo" and st["total"] == 7 and st["done"] == 2
    assert len(calls) == 1 and "build" not in calls[0] and oj.CONTAINER in calls[0]
    assert "-T" in calls[0]
    assert f"CIPHEROPS_PROGRESS_FILE={oj.CONTAINER_PROGRESS}" in calls[0]
    assert calls[0][-1].startswith("python3 -m cipherops.runner run")


def test_the_job_starts_in_the_harness_stage_without_a_build_phase(sandbox, monkeypatch):
    (sandbox / "results" / "runs").mkdir(parents=True)
    code, _calls, st = _run(sandbox, monkeypatch, [([" STEP 1/7  Configuration\n"], 0)])
    assert code == 0
    assert st["done"] == 0 and st["phase"] == "harness" and "build_total" not in st


def test_log_tail_does_not_read_the_whole_file(sandbox):
    path = oj.job_dir() / "progress.log"
    path.write_bytes(b"A" * 50_000 + b"TAIL-MARKER")
    assert oj.log_text(20).endswith("TAIL-MARKER")
    assert "A" * 1000 not in oj.log_text(20)


def test_output_lines_returns_each_line_as_chunks_arrive():
    class Chunks:
        def __init__(self):
            self.parts = [b" STEP 1/7  Config\n STEP ", b"2/7  Gate\r\n"]

        def read(self, _n):
            return self.parts.pop(0) if self.parts else b""

    assert list(oj._output_lines(Chunks())) == [" STEP 1/7  Config\n", " STEP 2/7  Gate\n"]


def test_bar_shows_the_stage_under_way_not_only_finished_ones(sandbox):
    base = {"started": 10.0, "phase": "harness", "step": "Starting the harness", "done": 0, "total": 7}
    frac, text = oj.display_progress(base)
    assert frac == 1 / 7
    assert text == "Step 1 of 7 · Starting the harness"
    frac, text = oj.display_progress({**base, "done": 2, "step": "Timing"})
    assert frac == 3 / 7
    assert text.startswith("Step 3 of 7")
    assert oj.display_progress({**base, "done": 6})[0] == 1.0


def test_bar_reads_the_stage_file_ahead_of_stdout(sandbox):
    now = 1_000.0
    oj.progress_path().parent.mkdir(parents=True, exist_ok=True)
    oj.progress_path().write_text(
        json.dumps({"step": 4, "total": 7, "title": "Sign and verify", "updated": now}),
        encoding="utf-8")
    frac, text = oj.display_progress({"started": now - 5, "phase": "harness", "step": "Starting the harness"})
    assert frac == 4 / 7
    assert text == "Step 4 of 7 · Sign and verify"


def test_bar_ignores_a_stage_file_from_an_older_run(sandbox):
    oj.progress_path().parent.mkdir(parents=True, exist_ok=True)
    oj.progress_path().write_text(
        json.dumps({"step": 7, "total": 7, "title": "Report", "updated": 1.0}),
        encoding="utf-8")
    frac, text = oj.display_progress({"started": 10_000.0, "step": "Starting the harness"})
    assert frac == 0.0
    assert text == "Starting the harness"


def test_stage_writes_a_progress_file_for_the_dashboard(tmp_path, monkeypatch):
    from cipherops import progress

    dest = tmp_path / "progress.json"
    monkeypatch.setenv("CIPHEROPS_PROGRESS_FILE", str(dest))
    progress.stage(2, 7, "Go/no-go gate", "preflight.py")
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["step"] == 2 and data["total"] == 7 and data["title"] == "Go/no-go gate"
    assert not dest.with_name("progress.json.tmp").exists()


def test_failed_harness_records_the_error(sandbox, monkeypatch):
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


def test_start_refuses_to_run_without_a_verified_image(sandbox, monkeypatch):
    missing = [images.ImageStatus(role="bench", ref="cipherops-bench:latest", problems=["not on this host"])]
    monkeypatch.setattr(images, "status", lambda stack, mode="all": missing)
    with pytest.raises(images.ImagesNotReady, match="Build or pull them"):
        oj.start("quick")
    assert not (oj.job_dir() / "params.json").exists()


def test_start_launches_the_worker_when_the_image_is_verified(sandbox, monkeypatch):
    ok = [images.ImageStatus(role="bench", ref="cipherops-bench:latest", present=True, state="ok")]
    monkeypatch.setattr(images, "status", lambda stack, mode="all": ok)
    launched = []
    monkeypatch.setattr(oj.jb, "spawn_worker", lambda module, d, params: launched.append((module, params)))
    oj.start("quick", trials=2)
    assert launched[0][0] == "live_lab.offline_job" and launched[0][1]["trials"] == 2
    st = json.loads((oj.job_dir() / "status.json").read_text())
    assert st["state"] == "starting" and st["phase"] == "harness"
