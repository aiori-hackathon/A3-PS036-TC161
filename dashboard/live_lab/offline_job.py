"""Run the offline harness (``scripts/run_benchmark.sh``) as a background job, so the dashboard stays usable.

The harness builds one pinned image and times every library call on one dedicated core with no network. The job
does the same steps as the script, in a detached worker that outlives the page:

    ecosystem/jobs/_offline/
        params.json    preset, stack, trials
        status.json    state: starting | running | done | failed | cancelled | stopped; step, total, run_id
        progress.log   the harness output, line by line

Only one runs at a time, and nothing CPU-heavy may run beside it (see ``driver.require_no_offline``).
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from live_lab import build_job, driver

ACTIVE_STATES = driver.ACTIVE_STATES
ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results"
# The harness needs the stack whose liboqs has the FIPS 205 SLH-DSA names (see scripts/run_benchmark.sh).
OFFLINE_STACK = "latest"
CONTAINER = "cipherops-offline-run"
PRESETS = {
    "quick": {"config": "configs/quick.yaml", "title": "Quick check", "note": "sizes 20, 60, 200 names · 3 trials"},
    "full": {"config": "configs/scenarios.yaml", "title": "Full run",
             "note": "sizes 50, 500, 5,000 names · repeated trials (long)"},
}
STEP_RE = re.compile(r"STEP\s+(\d+)/(\d+)\s+(.*)")
PLAN_RE = re.compile(r"measured trials:\s*(\d+)\s*x\s*(\d+)\s*datasets\s*x\s*(\d+)\s*modes")
CONTROL_RE = re.compile(r"A/A control:")  # the control runs as one extra mode in every trial
TRIAL_RE = re.compile(r"^\[[\d:]+\]\s+trial\s+\d+/\d+\s")
# Rough share of the whole run, so the bar moves smoothly: the image build, then the seven harness steps.
BUILD_WEIGHT = 0.15
DEFAULT_BUILD_STEPS = 35  # steps of the pinned harness image (measured)
STEP_WEIGHTS = {1: 0.01, 2: 0.02, 3: 0.02, 4: 0.60, 5: 0.12, 6: 0.03, 7: 0.05}


class OfflineProgress:
    """One progress bar over both phases: the image build, then the harness (its STEP n/7 lines and, inside the long
    step 4, one line per finished trial)."""

    def __init__(self, expected_build_steps: int = DEFAULT_BUILD_STEPS) -> None:
        self.build = build_job.BuildProgress(expected_build_steps)
        self.phase = "build"
        self.step = 0
        self.total_trials = 0
        self._plan = (0, 0, 0)
        self.trials = 0
        self._best = 0.0

    def start_harness(self) -> None:
        self.phase = "harness"

    def feed(self, line: str) -> bool:
        if self.phase == "build":
            return self.build.feed(line)
        m = STEP_RE.search(line)
        if m:
            self.step = int(m.group(1))
            return True
        m = PLAN_RE.search(line)
        if m:
            self._plan = tuple(int(x) for x in m.groups())
            self.total_trials = self._plan[0] * self._plan[1] * self._plan[2]
            return True
        if CONTROL_RE.search(line) and self._plan[0]:
            self.total_trials = self._plan[0] * self._plan[1] * (self._plan[2] + 1)
            return True
        if TRIAL_RE.match(line):
            self.trials += 1
            return True
        return False

    @property
    def fraction(self) -> float:
        if self.phase == "build":
            f = BUILD_WEIGHT * self.build.fraction
        else:
            f = BUILD_WEIGHT + sum(w for n, w in STEP_WEIGHTS.items() if n < self.step)
            if self.step == 4 and self.total_trials:
                f += STEP_WEIGHTS[4] * min(self.trials / self.total_trials, 1.0)
        self._best = max(self._best, min(f, 0.99))
        return self._best

    def detail(self) -> str:
        if self.phase == "build":
            return "building the image, " + self.build.detail()
        if self.step == 4 and self.total_trials:
            return f"step 4 of 7, trial {min(self.trials, self.total_trials)} of {self.total_trials}"
        return f"step {max(self.step, 1)} of 7"


def job_dir() -> Path:
    return driver.jobs_dir() / "_offline"


def _write_status(st: dict) -> None:
    d = job_dir()
    tmp = d / f"status.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(st), encoding="utf-8")
    os.replace(tmp, d / "status.json")  # the page polls this file, so never leave it half-written


def status() -> dict:
    return driver.offline_status()


def is_running() -> bool:
    return driver.active_offline()


def log_text(tail: int = 6000) -> str:
    try:
        return (job_dir() / "progress.log").read_text(encoding="utf-8", errors="replace")[-tail:]
    except OSError:
        return ""


def mark_handled() -> None:
    st = driver._read_json(job_dir() / "status.json")
    if st:
        st["handled"] = True
        _write_status(st)


def run_ids() -> set[str]:
    runs = RESULTS / "runs"
    return {p.name for p in runs.iterdir() if p.is_dir()} if runs.is_dir() else set()


def start(preset: str, *, trials: int | None = None, stack: str = OFFLINE_STACK) -> None:
    """Launch the harness as a detached worker. Returns at once."""
    if preset not in PRESETS:
        raise RuntimeError(f"unknown preset {preset!r}")
    if is_running():
        raise driver.LabBusy("An offline benchmark is already running.")
    driver.require_no_suite()
    driver.require_no_build()
    if driver.active_prepare_tags():
        raise driver.LabBusy("Signing is running in the lab. Wait for it to finish or cancel it first: it would "
                             "share the CPU and distort the timings.")
    if not (driver.ECO / "stacks" / f"{stack}.env").is_file():
        raise RuntimeError(f"missing stack env for {stack}")
    d = job_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "progress.log").write_text("", encoding="utf-8")
    params = {"preset": preset, "stack": stack, "trials": trials, "started": time.time()}
    (d / "params.json").write_text(json.dumps(params), encoding="utf-8")
    _write_status({"state": "starting", "preset": preset, "stack": stack, "started": params["started"]})
    subprocess.Popen([sys.executable, "-m", "live_lab.offline_job", str(d / "params.json")],
                     cwd=Path(__file__).resolve().parents[1], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     env={**os.environ, "PYTHONIOENCODING": "utf-8"}, **driver.background_process_kwargs())


def _remove_container() -> None:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, creationflags=driver.NO_WINDOW)


def cancel() -> None:
    """Stop the worker and the container it started."""
    st = driver._read_json(job_dir() / "status.json")
    pid = st.get("pid")
    if driver.pid_alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True,
                           creationflags=driver.NO_WINDOW)
        else:
            os.kill(int(pid), signal.SIGTERM)
            deadline = time.time() + 10
            while driver.pid_alive(pid) and time.time() < deadline:
                time.sleep(0.1)
    _remove_container()
    st = driver._read_json(job_dir() / "status.json") or st
    st.update(state="cancelled", finished=time.time(), handled=True)
    _write_status(st)


# ───────────────────────────── worker ─────────────────────────────

def compose_base(stack: str) -> list[str]:
    return ["docker", "compose", "--env-file", str(driver.ECO / "stacks" / f"{stack}.env"),
            "-f", str(ROOT / "docker" / "compose.yml")]


def harness_command(preset: str, trials: int | None) -> str:
    """The command run inside the container (one string, as ``scripts/run_benchmark.sh`` passes it)."""
    cmd = f"python3 -m cipherops.runner run --config {PRESETS[preset]['config']}"
    return cmd + (f" --trials {int(trials)}" if trials else "")


def _capture(cmd: list[str]) -> str:
    try:
        cp = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=30,
                            creationflags=driver.NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ""
    return cp.stdout.strip() if cp.returncode == 0 else ""


def run_environment(stack: str) -> dict:
    """What the harness records about where it ran (same values as the shell script)."""
    sha = _capture(["git", "rev-parse", "HEAD"]) or "unknown"
    if _capture(["git", "status", "--porcelain"]):
        sha += "-dirty"
    image = _capture(["docker", "image", "inspect", f"cipherops-bench:{stack}", "--format", "{{.Id}}"]) or "unknown"
    return {"CIPHEROPS_GIT_SHA": sha, "CIPHEROPS_HOST_IMAGE_ID": image,
            "CIPHEROPS_CPUSET": os.environ.get("CIPHEROPS_CPUSET", "2")}


def run(params: dict, *, popen=subprocess.Popen) -> int:
    stack, preset = params["stack"], params["preset"]
    d = job_dir()
    before = run_ids()
    st = {"state": "running", "pid": os.getpid(), "preset": preset, "stack": stack, "started": params["started"],
          "step": "Building the pinned image"}
    _write_status(st)
    proc = None

    def stop(*_):
        if proc is not None and proc.poll() is None:
            proc.terminate()
        _remove_container()
        raise SystemExit(143)

    if os.name != "nt":
        signal.signal(signal.SIGTERM, stop)
    tail: list[str] = []
    prog, last_write = OfflineProgress(build_job.expected_steps(f"bench-{stack}", DEFAULT_BUILD_STEPS)), 0.0

    def stream(cmd: list[str], env: dict) -> int:
        nonlocal proc, tail, last_write
        log.write("$ " + " ".join(cmd) + "\n")
        log.flush()
        proc = popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                     env=env, creationflags=driver.NO_WINDOW)
        for line in proc.stdout:
            log.write(line)
            log.flush()
            tail = (tail + [line])[-40:]
            m = STEP_RE.search(line)
            if m:
                st.update(step=m.group(3).strip(), done=int(m.group(1)) - 1, total=int(m.group(2)))
            if prog.feed(line) and (m or time.time() - last_write > 1.0):
                st.update(progress=prog.fraction, detail=prog.detail())
                _write_status(st)
                last_write = time.time()
        return proc.wait()

    code = 1
    try:
        with open(d / "progress.log", "a", encoding="utf-8", errors="replace") as log:
            env = {**os.environ, "BUILDKIT_PROGRESS": "plain"}
            code = stream(compose_base(stack) + ["build"], env)
            if code == 0:
                prog.start_harness()
                st.update(step="Starting the harness", progress=prog.fraction, detail="starting the harness")
                _write_status(st)
                env = {**os.environ, **run_environment(stack)}
                code = stream(compose_base(stack) + ["run", "--rm", "--name", CONTAINER, "bench",
                                                     harness_command(preset, params.get("trials"))], env)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        st.update(state="failed", error=str(e)[-1500:], finished=time.time())
        _write_status(st)
        return 1
    new = sorted(run_ids() - before)
    if code == 0 and prog.build.steps:
        build_job.remember_steps(f"bench-{stack}", len(prog.build.steps))
    if code == 0:
        st.update(state="done", finished=time.time(), returncode=0, run_id=new[-1] if new else None, progress=1.0)
    else:
        st.update(state="failed", finished=time.time(), returncode=code,
                  error=f"The harness exited with {code}\n{''.join(tail)[-1500:]}")
    _write_status(st)
    return 0 if code == 0 else 1


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    return run(json.loads(Path(argv[0]).read_text(encoding="utf-8")))


if __name__ == "__main__":
    sys.exit(main())
