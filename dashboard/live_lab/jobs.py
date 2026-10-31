"""Plumbing shared by the dashboard's background jobs (image build, image pull, signing, offline run, suite).

Every job is a detached worker process that reports through a folder:

    <job dir>/
        params.json    what was asked for
        status.json    state: starting | running | done | failed | cancelled | stopped, plus job-specific fields
        progress.log   the worker's output, line by line

The page polls ``status.json`` and the log, so the worker may outlive it. This module holds the parts that were
copied into each job module: atomic status writes, the log tail, launching the worker, stopping it, and running a
command while teeing its output to the log.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from live_lab import minimal as _minimal

ACTIVE_STATES = ("starting", "running")
DASHBOARD_DIR = Path(__file__).resolve().parents[1]

# A console app started from the dashboard on Windows would open a new console window of its own. CREATE_NO_WINDOW
# stops that; it is 0 (a no-op) on other systems.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def background_process_kwargs() -> dict:
    """``subprocess.Popen`` options that detach a worker so it outlives the page."""
    if os.name == "nt":
        return {"creationflags": NO_WINDOW | 0x00000200}   # hidden console + its own process group
    return {"start_new_session": True}


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_status(directory: Path, st: dict) -> None:
    """Atomic: the launcher, the worker and the page all touch this file, and the page polls it."""
    tmp = directory / f"status.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(st), encoding="utf-8")
    os.replace(tmp, directory / "status.json")


def mark_handled(directory: Path) -> None:
    """Record that a page has taken over the finished job, so later sessions don't do it again."""
    if _minimal.enabled():  # a visitor must not take over the team's job notices
        return
    st = read_json(directory / "status.json")
    if st:
        st["handled"] = True
        write_status(directory, st)


def log_tail(directory: Path, tail: int = 6000) -> str:
    try:
        return (directory / "progress.log").read_text(encoding="utf-8", errors="replace")[-tail:]
    except OSError:
        return ""


def pid_alive(pid: int | None) -> bool:
    """True if ``pid`` is still a process.

    The page calls this on every poll. Spawning ``tasklist`` for each call stalls it while a job is busy, so
    Windows uses OpenProcess instead.
    """
    if not pid:
        return False
    pid = int(pid)
    if os.name == "nt":
        # PROCESS_QUERY_LIMITED_INFORMATION. Access denied (5) still means the process exists.
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            kernel.CloseHandle(handle)
            return True
        return kernel.GetLastError() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists but belongs to another user
    return True


def spawn_worker(module: str, directory: Path, params: dict) -> None:
    """Write ``params.json`` and launch ``python -m <module> params.json`` detached. Returns at once."""
    directory.mkdir(parents=True, exist_ok=True)
    params_path = directory / "params.json"
    params_path.write_text(json.dumps(params, indent=2), encoding="utf-8")
    subprocess.Popen([sys.executable, "-m", module, str(params_path)], cwd=DASHBOARD_DIR,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     env={**os.environ, "PYTHONIOENCODING": "utf-8"}, **background_process_kwargs())


def stop_worker(pid: int | None, *, wait_s: float = 0, alive: Callable[[int | None], bool] = pid_alive) -> None:
    """Terminate a worker and its children. With ``wait_s`` the call returns once it has exited (or the time is up)."""
    if not alive(pid):
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True,
                       creationflags=NO_WINDOW)
        return
    os.kill(int(pid), signal.SIGTERM)  # the worker stops its own child and records the result
    deadline = time.time() + wait_s
    while wait_s and alive(pid) and time.time() < deadline:
        time.sleep(0.1)


def mark_cancelled(directory: Path, fallback: dict | None = None) -> None:
    st = read_json(directory / "status.json") or dict(fallback or {})
    st.update(state="cancelled", updated=time.time(), finished=time.time(), handled=True)
    write_status(directory, st)


def run_logged(cmd: list[str], log_path: Path, *, cwd: Path, env: dict | None = None,
               popen=subprocess.Popen, on_proc: Callable[[subprocess.Popen], None] | None = None,
               on_line: Callable[[str], None] | None = None, keep: int = 40) -> tuple[int, list[str]]:
    """Run ``cmd`` writing its combined output to ``log_path``; return (exit code, last ``keep`` lines).

    ``on_proc`` receives the process as soon as it exists, so a SIGTERM handler can stop it.
    """
    tail: list[str] = []
    with open(log_path, "a", encoding="utf-8", errors="replace") as log:
        log.write("$ " + " ".join(cmd) + "\n")
        log.flush()
        proc = popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                     errors="replace", env=env, creationflags=NO_WINDOW)
        if on_proc:
            on_proc(proc)
        for line in proc.stdout:
            log.write(line)
            log.flush()
            tail = (tail + [line])[-keep:]
            if on_line:
                on_line(line)
        return proc.wait(), tail


def install_sigterm(stop: Callable[[], None]) -> None:
    """On SIGTERM run ``stop`` and exit 143 (Windows has no SIGTERM; cancel kills the process tree instead)."""
    if os.name == "nt":
        return

    def handler(*_):
        stop()
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, handler)
