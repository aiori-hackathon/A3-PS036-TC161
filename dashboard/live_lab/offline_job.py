"""Run the offline harness (``scripts/run_benchmark.sh``) as a background job, so the dashboard stays usable.

The harness times every library call on one dedicated core with no network, inside the pinned ``cipherops-bench``
image. The job does the same steps as the script, in a detached worker that outlives the page. It does not build the
image: Setup puts it on the node (built here or pulled) and ``start`` refuses to run without a verified one.

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
import subprocess
import sys
import time
from pathlib import Path

from live_lab import driver, images
from live_lab import jobs as jb

ACTIVE_STATES = jb.ACTIVE_STATES
ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results"
# The offline harness needs the stack whose liboqs has the FIPS 205 SLH-DSA names (see scripts/run_benchmark.sh).
OFFLINE_STACK = images.OFFLINE_STACK
CONTAINER = "cipherops-offline-run"
PRESETS = {
    "quick": {"config": "configs/quick.yaml", "title": "Quick check", "note": "sizes 20, 60, 200 names · 3 trials"},
    "full": {"config": "configs/scenarios.yaml", "title": "Full run",
             "note": "sizes 50, 500, 5,000 names · repeated trials (long)"},
}
STEP_RE = re.compile(r"STEP\s+(\d+)/(\d+)\s+(.*)")
# Written inside the container (results is a bind mount) so the page can see the stage
# without waiting for Docker to flush the container's stdout.
CONTAINER_PROGRESS = "/work/results/.cipherops-progress.json"
PROGRESS_NAME = ".cipherops-progress.json"
HARNESS_STAGES = 7


def job_dir() -> Path:
    return driver.jobs_dir() / "_offline"


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


def display_progress(status: dict) -> tuple[float, str]:
    """Bar fraction and label for a running offline job.

    ``status["done"]`` is how many harness stages have *finished*, so it is 0 for the whole
    of stage 1 and the bar used to stay empty. The bar shows the stage that is underway.
    A stage file on the results mount wins over the worker's stdout parse: Docker can hold that
    stdout until the container exits, which left the bar empty for the whole run.
    """
    snap = read_harness_progress(float(status.get("started") or 0))
    total = HARNESS_STAGES
    title = status.get("step") or "running"
    if snap is not None:
        total = max(1, snap["total"])
        current = min(max(snap["step"], 1), total)
        title = snap["title"] or title
    elif "done" in status and status.get("total"):
        total = max(1, int(status.get("total") or 1))
        current = min(max(0, int(status.get("done") or 0)) + 1, total)
    else:
        return 0.0, str(title)
    return min(1.0, current / total), f"Step {current} of {total} · {title}"


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
    jb.mark_handled(job_dir())


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
    rows = images.status(stack, "offline")
    if not images.ready(rows):
        raise images.ImagesNotReady(images.explain(rows, "offline"))
    d = job_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "progress.log").write_text("", encoding="utf-8")
    clear_progress()
    params = {"preset": preset, "stack": stack, "trials": trials, "started": time.time()}
    jb.write_status(d, {"state": "starting", "preset": preset, "stack": stack, "started": params["started"],
                        "phase": "harness", "step": "Starting the harness", "done": 0, "total": HARNESS_STAGES})
    jb.spawn_worker("live_lab.offline_job", d, params)


def _remove_container() -> None:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, creationflags=driver.NO_WINDOW)


def cancel() -> None:
    """Stop the worker and the container it started."""
    st = jb.read_json(job_dir() / "status.json")
    jb.stop_worker(st.get("pid"), wait_s=10, alive=driver.pid_alive)
    _remove_container()
    st = jb.read_json(job_dir() / "status.json") or st
    st.update(state="cancelled", finished=time.time(), handled=True)
    jb.write_status(job_dir(), st)


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
          "phase": "harness", "step": "Starting the harness", "done": 0, "total": HARNESS_STAGES}
    jb.write_status(d, st)
    proc = None

    def stop() -> None:
        if proc is not None and proc.poll() is None:
            proc.terminate()
        _remove_container()

    jb.install_sigterm(stop)
    tail: list[str] = []

    def note(line: str) -> None:
        m = STEP_RE.search(line)
        if m:
            st.update(phase="harness", step=m.group(3).strip(),
                      done=int(m.group(1)) - 1, total=int(m.group(2)))
            jb.write_status(d, st)

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
        jb.write_status(d, st)
        return 1
    new = sorted(run_ids() - before)
    if code == 0:
        st.update(state="done", finished=time.time(), returncode=0, run_id=new[-1] if new else None)
    else:
        st.update(state="failed", finished=time.time(), returncode=code,
                  error=f"The harness exited with {code}\n{''.join(tail)[-1500:]}")
    jb.write_status(d, st)
    return 0 if code == 0 else 1


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    return run(json.loads(Path(argv[0]).read_text(encoding="utf-8")))


if __name__ == "__main__":
    sys.exit(main())
