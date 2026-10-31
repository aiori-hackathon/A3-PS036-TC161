"""Make the pinned images available on this node, as a background job: build them here, or pull them from a registry.

Both can take a long time (a first build compiles OpenSSL, liboqs and MTL; a pull moves a few GB), so the work runs in
a detached worker and the page shows the log as it grows:

    ecosystem/jobs/_images/
        params.json    action (build | pull), stack, mode (offline | simulated | all), registry
        status.json    state: starting | running | done | failed | cancelled | stopped; step, done, total
        progress.log   the command output, line by line

Whichever way the images arrive, the job ends by checking them against the stack profile (``images.status``): a pulled
image built from other pins fails the job instead of being used. Only one runs at a time (any stack), because two would
fight over the same CPU and layer cache, and it never runs beside a benchmark, whose timings it would disturb.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from live_lab import minimal as _minimal
from live_lab import driver, images
from live_lab import jobs as jb

ACTIVE_STATES = jb.ACTIVE_STATES
ACTIONS = ("build", "pull")


def job_dir() -> Path:
    return driver.jobs_dir() / "_images"


def status() -> dict:
    """Current job status ({} if none ever ran); a worker that vanished without finishing reads as 'stopped'."""
    return driver.effective_job_state(jb.read_json(job_dir() / "status.json"))


def is_running() -> bool:
    return status().get("state") in ACTIVE_STATES


def log_text(tail: int = 6000) -> str:
    return jb.log_tail(job_dir(), tail)


def mark_handled() -> None:
    jb.mark_handled(job_dir())


def start(action: str, stack: str, *, mode: str = "all", registry: str | None = None) -> None:
    """Launch the job as a detached worker. Returns at once."""
    _minimal.refuse('Building or pulling images')
    if action not in ACTIONS:
        raise RuntimeError(f"unknown action {action!r}")
    images.roles_for(mode, stack)
    if not images.roles_for(mode, stack):
        raise RuntimeError(f"{images.MODE_TITLE[mode]} is not available for the {stack} stack: "
                           f"the offline harness runs on {images.OFFLINE_STACK} only")
    driver.require_no_suite()
    driver.require_no_offline()
    live = status()
    if live.get("state") in ACTIVE_STATES:
        doing = "pulled" if live.get("action") == "pull" else "built"
        raise driver.LabBusy(f"Images for {live.get('stack', '?')} are already being {doing}. "
                             "Wait for them to finish or cancel first.")
    if not (images.ECO / "stacks" / f"{stack}.env").is_file():
        raise RuntimeError(f"missing stack env for {stack}")
    if action == "pull":
        registry = images.normalize_registry(registry)
    d = job_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "progress.log").write_text("", encoding="utf-8")
    params = {"action": action, "stack": stack, "mode": mode, "registry": registry, "started": time.time()}
    jb.write_status(d, {"state": "starting", **params})
    jb.spawn_worker("live_lab.image_job", d, params)


def cancel() -> None:
    """Stop the worker and the docker command it started."""
    _minimal.refuse('Cancelling an image job')
    st = jb.read_json(job_dir() / "status.json")
    jb.stop_worker(st.get("pid"), wait_s=10, alive=driver.pid_alive)
    jb.mark_cancelled(job_dir(), st)


# ───────────────────────────── worker ─────────────────────────────

def _git_sha() -> str:
    cp = subprocess.run(["git", "rev-parse", "HEAD"], cwd=images.REPO, capture_output=True, text=True)
    return cp.stdout.strip() if cp.returncode == 0 else "unknown"


def run(params: dict, *, popen=subprocess.Popen) -> int:
    action, stack, mode = params["action"], params["stack"], params.get("mode", "all")
    d = job_dir()
    log_path = d / "progress.log"
    st = {"state": "running", "pid": os.getpid(), **{k: params.get(k) for k in ("action", "stack", "mode", "registry")},
          "started": params["started"]}
    if action == "build":
        commands = [images.build_command(stack, mode)]
    else:
        commands = images.pull_commands(params["registry"], stack, mode)
    st.update(done=0, total=len(commands), step="Building the images" if action == "build" else "Pulling the images")
    jb.write_status(d, st)

    child: list[subprocess.Popen] = []

    def stop() -> None:
        for proc in child:
            if proc.poll() is None:
                proc.terminate()

    jb.install_sigterm(stop)
    env = {**os.environ, "BUILDKIT_PROGRESS": "plain", "VCS_REF": _git_sha()}
    tail: list[str] = []
    try:
        for i, cmd in enumerate(commands):
            if action == "pull":
                st.update(done=i, step=("Pulling " if cmd[1] == "pull" else "Tagging ") + cmd[2])
                jb.write_status(d, st)
            code, tail = jb.run_logged(cmd, log_path, cwd=images.REPO, env=env, popen=popen, on_proc=child.append)
            if code != 0:
                st.update(state="failed", finished=time.time(), returncode=code,
                          error=f"{' '.join(cmd[:3])} exited with {code}\n{''.join(tail)[-1500:]}")
                jb.write_status(d, st)
                return 1
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        st.update(state="failed", error=str(e)[-1500:], finished=time.time())
        jb.write_status(d, st)
        return 1

    rows = images.status(stack, mode)
    if not images.ready(rows):
        problems = "\n".join(f"{r.ref}: {'; '.join(r.problems) or r.state}" for r in rows if r.state != "ok")
        st.update(state="failed", finished=time.time(), returncode=0,
                  error=f"The images are on this host but do not match the stack profile {stack}:\n{problems}")
    else:
        st.update(state="done", finished=time.time(), returncode=0, done=st["total"], step="Images ready")
    jb.write_status(d, st)
    return 0 if st["state"] == "done" else 1


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    return run(json.loads(Path(argv[0]).read_text(encoding="utf-8")))


if __name__ == "__main__":
    sys.exit(main())
