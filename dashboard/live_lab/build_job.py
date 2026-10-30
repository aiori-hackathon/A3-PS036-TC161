"""Build the lab images as a background job, so the dashboard stays usable and shows the log as it grows.

A first build compiles OpenSSL, liboqs and MTL from source and takes tens of minutes. Running it inside the
page blocked everything and showed no output until the end, so the Build button looked idle and was easy to
press twice. The job runs ``docker compose build`` in a detached worker instead:

    ecosystem/jobs/_build/
        params.json    which stack
        status.json    state: starting | running | done | failed | cancelled | stopped
        progress.log   the build output, line by line

Only one build runs at a time (any stack): two would fight over the same CPU and layer cache.
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

from live_lab import driver

ACTIVE_STATES = driver.ACTIVE_STATES


DEFAULT_STEPS = 36  # steps of a full build of this repository's three lab images (measured)
_STEP = re.compile(r"^#(\d+) (\[.*)$")
_DONE = re.compile(r"^#(\d+) (?:DONE|CACHED)\b")


class BuildProgress:
    """Progress of a BuildKit build, read from its plain log.

    BuildKit prints ``#12 [stage 3/9] RUN ...`` when a step starts and ``#12 DONE 4.1s`` (or ``CACHED``) when it
    ends. Steps are only seen once they are scheduled, so finished over seen is always close to 1 and says nothing.
    The bar therefore divides by an *expected* step count: what the last successful build of this image set needed
    (``remember_steps``), or a default measured on this repository's Dockerfiles. If a build turns out longer, the
    expectation grows with it. The fraction never goes backwards and stays below 1 until the build ends.
    """

    def __init__(self, expected: int = DEFAULT_STEPS) -> None:
        self.expected = max(1, int(expected))
        self.steps: dict[str, str] = {}
        self.done: set[str] = set()
        self.current = ""
        self._best = 0.0

    def feed(self, line: str) -> bool:
        """Take one log line; True if the progress changed."""
        m = _STEP.match(line)
        if m:
            if m.group(1) not in self.steps:
                self.steps[m.group(1)] = m.group(2).strip()
                self.current = m.group(2).strip()
                return True
            return False
        m = _DONE.match(line)
        if m and m.group(1) in self.steps and m.group(1) not in self.done:
            self.done.add(m.group(1))
            return True
        return False

    @property
    def total(self) -> int:
        return max(self.expected, len(self.steps))

    @property
    def fraction(self) -> float:
        if not self.steps:
            return 0.0
        self._best = max(self._best, min(len(self.done) / self.total, 0.99))
        return self._best

    def detail(self) -> str:
        return f"step {len(self.done)} of about {self.total}" if self.steps else "starting"


def _expected_file() -> Path:
    return driver.jobs_dir() / "expected_steps.json"


def expected_steps(key: str, default: int = DEFAULT_STEPS) -> int:
    """Steps the last successful build of ``key`` needed (a rough guide for the progress bar)."""
    try:
        return int(json.loads(_expected_file().read_text(encoding="utf-8")).get(key, default))
    except (OSError, ValueError, TypeError):
        return default


def remember_steps(key: str, n: int) -> None:
    try:
        known = json.loads(_expected_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    known[key] = int(n)
    try:
        _expected_file().parent.mkdir(parents=True, exist_ok=True)
        _expected_file().write_text(json.dumps(known), encoding="utf-8")
    except OSError:
        pass  # only a hint for the bar


def job_dir() -> Path:
    return driver.jobs_dir() / "_build"


def _write_status(st: dict) -> None:
    d = job_dir()
    tmp = d / f"status.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(st), encoding="utf-8")
    os.replace(tmp, d / "status.json")  # the page polls this file, so never leave it half-written


def status() -> dict:
    return driver.build_status()


def is_running() -> bool:
    return driver.active_build_stack() is not None


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


def start(stack: str) -> None:
    """Launch the build as a detached worker. Returns at once."""
    driver.require_no_suite()
    driver.require_no_offline()
    live = driver.active_build_stack()
    if live:
        raise driver.LabBusy(f"Images for {live} are already being built. Wait for them to finish or cancel first.")
    env_file = driver.ECO / "stacks" / f"{stack}.env"
    if not env_file.is_file():
        raise RuntimeError(f"missing stack env {env_file}")
    d = job_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "progress.log").write_text("", encoding="utf-8")
    params = {"stack": stack, "started": time.time()}
    (d / "params.json").write_text(json.dumps(params), encoding="utf-8")
    _write_status({"state": "starting", "stack": stack, "started": params["started"]})
    subprocess.Popen([sys.executable, "-m", "live_lab.build_job", str(d / "params.json")],
                     cwd=Path(__file__).resolve().parents[1], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     env={**os.environ, "PYTHONIOENCODING": "utf-8"}, **driver.background_process_kwargs())


def cancel() -> None:
    """Stop the worker and the docker build it started."""
    st = driver._read_json(job_dir() / "status.json")
    pid = st.get("pid")
    if driver.pid_alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True,
                           creationflags=driver.NO_WINDOW)
        else:
            os.kill(int(pid), signal.SIGTERM)  # the worker stops its child and records the result
            deadline = time.time() + 10
            while driver.pid_alive(pid) and time.time() < deadline:
                time.sleep(0.1)
    st = driver._read_json(job_dir() / "status.json") or st
    st.update(state="cancelled", finished=time.time(), handled=True)
    _write_status(st)


# ───────────────────────────── worker ─────────────────────────────

def run(params: dict, *, popen=subprocess.Popen) -> int:
    stack = params["stack"]
    d = job_dir()
    st = {"state": "running", "pid": os.getpid(), "stack": stack, "started": params["started"]}
    _write_status(st)
    cmd = driver.build_command(stack)
    env = {**os.environ, "BUILDKIT_PROGRESS": "plain"}
    proc = None

    def stop(*_):
        if proc is not None and proc.poll() is None:
            proc.terminate()
        raise SystemExit(143)

    if os.name != "nt":
        signal.signal(signal.SIGTERM, stop)
    tail: list[str] = []
    track, last_write = BuildProgress(expected_steps(f"lab-{stack}")), 0.0
    try:
        with open(d / "progress.log", "a", encoding="utf-8", errors="replace") as log:
            log.write("$ " + " ".join(cmd) + "\n")
            log.flush()
            proc = popen(cmd, cwd=driver.ECO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         errors="replace", env=env, creationflags=driver.NO_WINDOW)
            for line in proc.stdout:
                log.write(line)
                log.flush()
                tail = (tail + [line])[-40:]
                if track.feed(line) and time.time() - last_write > 1.0:
                    st.update(progress=track.fraction, detail=track.detail(), current=track.current[:90])
                    _write_status(st)
                    last_write = time.time()
            code = proc.wait()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        st.update(state="failed", error=str(e)[-1500:], finished=time.time())
        _write_status(st)
        return 1
    if code == 0:
        st.update(state="done", finished=time.time(), returncode=0, progress=1.0)
        if track.steps:
            remember_steps(f"lab-{stack}", len(track.steps))
    else:
        st.update(state="failed", finished=time.time(), returncode=code,
                  error=f"docker compose build exited with {code}\n{''.join(tail)[-1500:]}")
    _write_status(st)
    return 0 if code == 0 else 1


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    return run(json.loads(Path(argv[0]).read_text(encoding="utf-8")))


if __name__ == "__main__":
    sys.exit(main())
