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

from live_lab import driver

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
BUILD_SEEN_RE = re.compile(r"#(\d+)\s+\[")
BUILD_DONE_RE = re.compile(r"#(\d+)\s+DONE\b")
# Written inside the container (results is a bind mount) so the page can see the stage
# without waiting for Docker to flush the container's stdout.
CONTAINER_PROGRESS = "/work/results/.cipherops-progress.json"
PROGRESS_NAME = ".cipherops-progress.json"
# Image build is the first slice of the bar; harness stages fill the rest, so the bar
# never jumps backwards when the build ends and timing starts.
BUILD_SHARE = 0.35


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
    """Last ``tail`` characters of the harness log, without reading the whole file.

    The log stays open and grows for the whole run. Reading it all on each page click made
    the Results tab wait on that file.
    """
    path = job_dir() / "progress.log"
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - tail * 4))
            data = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return data[-tail:]


def progress_path() -> Path:
    return RESULTS / PROGRESS_NAME


def clear_progress() -> None:
    try:
        progress_path().unlink()
    except OSError:
        pass


def read_harness_progress(started: float) -> dict | None:
    """The stage file the harness writes, if it belongs to this run."""
    try:
        data = json.loads(progress_path().read_text(encoding="utf-8"))
        updated = float(data.get("updated") or 0)
        step, total = int(data["step"]), int(data["total"])
    except (OSError, json.JSONDecodeError, TypeError, ValueError, KeyError):
        return None
    if total < 1 or step < 1 or updated + 30 < float(started or 0):
        return None
    return {"step": step, "total": total, "title": str(data.get("title") or ""), "updated": updated}


def _harness_fraction(current: int, total: int) -> float:
    span = 1.0 - BUILD_SHARE
    return min(1.0, BUILD_SHARE + span * (current / max(1, total)))


def display_progress(status: dict) -> tuple[float, str]:
    """Bar fraction and label for a running offline job.

    ``status["done"]`` is how many harness stages have *finished*, so it is 0 for the whole
    of stage 1 and the bar used to stay empty. The bar shows the stage that is underway.
    A stage file on the results mount wins over the worker's stdout parse: Docker can hold that
    stdout until the container exits, which left the bar empty for the whole run.
    """
    snap = read_harness_progress(float(status.get("started") or 0))
    current = None
    total = 7
    title = status.get("step") or "running"
    if snap is not None:
        total = max(1, snap["total"])
        current = min(max(snap["step"], 1), total)
        title = snap["title"] or title
    elif status.get("phase") == "harness" or (
            "done" in status and status.get("total") and status.get("phase") != "build"):
        total = max(1, int(status.get("total") or 1))
        current = min(max(0, int(status.get("done") or 0)) + 1, total)

    if current is None:
        # Docker names layers as it discovers them, so "5 of 8" then "10 of 12" is not a real
        # plan. Keep the label steady and only move the bar.
        frac = min(1.0, max(0.0, float(status.get("build_fraction") or 0)))
        label = status.get("step") or "Building the pinned image"
        if frac > 0 or int(status.get("build_total") or 0) > 0:
            return frac * BUILD_SHARE, label
        return 0.0, label
    return _harness_fraction(current, total), f"Step {current} of {total} · {title}"


def _output_lines(stdout):
    """Yield text lines as soon as a newline arrives.

    A text-mode pipe buffers about 8 KiB before the first line is visible, and Docker may not
    fill that until the command exits. Reading the raw pipe returns whatever has already been
    written. Test doubles that are plain iterators are yielded as they are.
    """
    read = getattr(stdout, "read", None)
    if not callable(read):
        yield from stdout
        return
    pending = b""
    while True:
        chunk = read(4096)
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8", "replace")
        pending += chunk
        while True:
            cut = len(pending)
            for sep in (b"\n", b"\r"):
                at = pending.find(sep)
                if at >= 0:
                    cut = min(cut, at)
            if cut == len(pending):
                break
            line = pending[:cut].decode("utf-8", "replace").strip("\r")
            pending = pending[cut + 1:]
            if line:
                yield line + "\n"
    rest = pending.decode("utf-8", "replace").strip("\r")
    if rest:
        yield rest + "\n"


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
    clear_progress()
    params = {"preset": preset, "stack": stack, "trials": trials, "started": time.time()}
    (d / "params.json").write_text(json.dumps(params), encoding="utf-8")
    _write_status({"state": "starting", "preset": preset, "stack": stack, "started": params["started"],
                   "phase": "build", "step": "Building the pinned image"})
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
    clear_progress()
    st = {"state": "running", "pid": os.getpid(), "preset": preset, "stack": stack, "started": params["started"],
          "phase": "build", "step": "Building the pinned image"}
    _write_status(st)
    proc = None
    build_seen: set[int] = set()
    build_done: set[int] = set()

    def stop(*_):
        if proc is not None and proc.poll() is None:
            proc.terminate()
        _remove_container()
        raise SystemExit(143)

    if os.name != "nt":
        signal.signal(signal.SIGTERM, stop)
    tail: list[str] = []

    def note(line: str) -> None:
        m = STEP_RE.search(line)
        if m:
            st.update(phase="harness", step=m.group(3).strip(),
                      done=int(m.group(1)) - 1, total=int(m.group(2)))
            _write_status(st)
            return
        if st.get("phase") == "harness":
            return
        changed = False
        seen_m = BUILD_SEEN_RE.search(line)
        if seen_m and int(seen_m.group(1)) not in build_seen:
            build_seen.add(int(seen_m.group(1)))
            changed = True
        done_m = BUILD_DONE_RE.search(line)
        if done_m and int(done_m.group(1)) not in build_done:
            build_done.add(int(done_m.group(1)))
            changed = True
        if not changed:
            return
        seen, built = len(build_seen), len(build_done)
        ratio = min(0.08, 1 / seen) if built == 0 else built / max(seen, built)
        st.update(phase="build", step="Building the pinned image",
                  build_done=built, build_total=max(seen, built),
                  build_fraction=max(float(st.get("build_fraction") or 0), min(0.98, ratio)))
        _write_status(st)

    def stream(cmd: list[str], env: dict) -> int:
        nonlocal proc, tail
        log.write("$ " + " ".join(cmd) + "\n")
        log.flush()
        # bufsize=0: a text pipe hides the first lines until ~8 KiB arrives, so the bar never moved.
        proc = popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
                     env=env, creationflags=driver.NO_WINDOW)
        for line in _output_lines(proc.stdout):
            log.write(line)
            log.flush()
            tail = (tail + [line])[-40:]
            note(line)
        return proc.wait()

    code = 1
    try:
        with open(d / "progress.log", "a", encoding="utf-8", errors="replace") as log:
            env = {**os.environ, "BUILDKIT_PROGRESS": "plain"}
            code = stream(compose_base(stack) + ["build"], env)
            if code == 0:
                # Stage 1 has started even before its banner is visible: show that slice, not an empty bar.
                st.update(phase="harness", step="Starting the harness", done=0, total=7)
                _write_status(st)
                env = {**os.environ, **run_environment(stack)}
                code = stream(compose_base(stack) + [
                    "run", "--rm", "-T", "--name", CONTAINER,
                    "-e", "PYTHONUNBUFFERED=1",
                    "-e", f"CIPHEROPS_PROGRESS_FILE={CONTAINER_PROGRESS}",
                    "bench", harness_command(preset, params.get("trials")),
                ], env)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        st.update(state="failed", error=str(e)[-1500:], finished=time.time())
        _write_status(st)
        return 1
    new = sorted(run_ids() - before)
    if code == 0:
        st.update(state="done", finished=time.time(), returncode=0, run_id=new[-1] if new else None)
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
