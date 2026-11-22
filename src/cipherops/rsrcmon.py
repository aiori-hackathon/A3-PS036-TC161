"""Orchestrate ``rsrcsamp`` (C, libc + /proc + cgroup v2) around benchmark phases.

Architecture
------------
* **Source of truth**: Linux kernel task and cgroup counters, read by ``harness/rsrcsamp.c``.
  No psutil. CPU % is CPU time over wall time (jiffies or cgroup ``usage_usec``), so 100 is one
  core fully busy. RAM is the cgroup's ``memory.current`` minus inactive file cache (what
  ``docker stats`` shows), or VmRSS when there is no cgroup.
* **Offline harness**: one sampler process writing JSONL beside the run, labels via stdin
  (``LABEL phase`` / ``QUIT``) aligned with runner stages. A label change closes the running
  interval, so every row belongs to one stage.
* **Simulated lab**: host polls ``docker exec <ctr> rsrcsamp --once --cgroup auto`` so
  Windows Docker Desktop still measures the Linux VM's containers. CPU % is computed from the
  cumulative CPU time between two polls, so nothing between polls is missed.
* **Host**: every sample also records machine CPU busy % (``/proc/stat``) and MemAvailable
  (``/proc/meminfo``) — capacity evidence for a continuously hosted deployment. On Docker
  Desktop this is the WSL2 VM; on a Linux testbed it is the hardware.
* **Summaries**: wall time, CPU seconds, time-weighted mean and peak CPU, solution memory,
  and host CPU / MemAvailable, per label.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

SAMPLER = os.environ.get("CIPHEROPS_RSRCSAMP", "/work/harness/rsrcsamp")
DEFAULT_INTERVAL_MS = int(os.environ.get("CIPHEROPS_RSRC_INTERVAL_MS", "1000"))

# Fixed Compose names used by the eco lab (see ecolab.topology).
LAB_CONTAINERS = ("eco-root", "eco-unbound", "eco-client", "eco-tld-test", "eco-sld1", "eco-sld2")


def find_sampler() -> str | None:
    for cand in (SAMPLER, "harness/rsrcsamp", "./harness/rsrcsamp",
                 "/usr/local/bin/rsrcsamp", shutil.which("rsrcsamp") or ""):
        if cand and Path(cand).is_file() and os.access(cand, os.X_OK):
            return str(Path(cand))
    return None


def _field(row: dict, key: str) -> int | None:
    """A counter from a sample row, or None when the sampler could not read it (it writes -1)."""
    v = row.get(key)
    if v is None:
        return None
    v = int(v)
    return v if v >= 0 else None


def memory_in_use(row: dict) -> tuple[int, str] | None:
    """Memory a sample row reports as in use, and which kernel figure it is.

    For a cgroup this is ``memory.current`` minus ``inactive_file``: the figure ``docker stats`` shows.
    ``memory.current`` alone also counts file cache the kernel can drop at any time, so it overstates
    what the workload holds. Without a cgroup it is the process's resident set.
    """
    current = _field(row, "cgroup_memory_current")
    if current is not None:
        inactive = _field(row, "cgroup_memory_inactive_file")
        if inactive is not None:
            return max(current - inactive, 0), "cgroup_working_set"
        return current, "cgroup_current"
    rss = _field(row, "rss_bytes")
    return (rss, "proc_rss") if rss is not None else None


def _aggregate(rows: list[dict]) -> dict:
    """Totals for a set of rows. Means are weighted by each row's wall time, because rows differ in length."""
    wall = cpu_s = mem_weight = mem_sum = 0.0
    host_wall = host_busy_s = 0.0
    cpu_peak = mem_peak = host_peak = kind = None
    peaks: dict[str, int] = {}   # largest value seen of each plain counter
    avail_low = None
    mem_total = None
    for r in rows:
        w = float(r.get("wall_ms") or 0.0) / 1000.0 or 1.0   # rows without a length count equally
        if r.get("cpu_pct") is not None:
            pct = float(r["cpu_pct"])
            wall += w
            cpu_s += pct / 100.0 * w
            cpu_peak = pct if cpu_peak is None else max(cpu_peak, pct)
        if r.get("host_cpu_pct") is not None and float(r["host_cpu_pct"]) >= 0:
            hp = float(r["host_cpu_pct"])
            host_wall += w
            host_busy_s += hp / 100.0 * w
            host_peak = hp if host_peak is None else max(host_peak, hp)
        mem = memory_in_use(r)
        if mem is not None:
            mem_sum += mem[0] * w
            mem_weight += w
            mem_peak = mem[0] if mem_peak is None else max(mem_peak, mem[0])
            kind = mem[1]
        for key in ("cgroup_memory_anon", "hwm_bytes", "cgroup_memory_peak", "host_mem_total_bytes"):
            v = _field(r, key)
            if v is not None:
                peaks[key] = max(peaks.get(key, v), v)
        avail = _field(r, "host_mem_available_bytes")
        if avail is not None:
            avail_low = avail if avail_low is None else min(avail_low, avail)
        if mem_total is None:
            mem_total = _field(r, "host_mem_total_bytes")
    timed = all(r.get("wall_ms") for r in rows) and bool(rows)
    return {
        "samples": len(rows),
        "wall_s": round(wall, 3) if timed else None,
        "cpu_seconds": round(cpu_s, 3) if timed else None,
        "cpu_pct_mean": round(100.0 * cpu_s / wall, 3) if wall else None,
        "cpu_pct_peak": round(cpu_peak, 3) if cpu_peak is not None else None,
        "mem_bytes_mean": int(mem_sum / mem_weight) if mem_weight else None,
        "mem_bytes_peak": mem_peak,
        "mem_kind": kind,
        "mem_anon_bytes_peak": peaks.get("cgroup_memory_anon"),
        "proc_hwm_bytes_peak": peaks.get("hwm_bytes"),
        "cgroup_memory_peak_max": peaks.get("cgroup_memory_peak"),
        "host_cpu_pct_mean": round(100.0 * host_busy_s / host_wall, 3) if host_wall else None,
        "host_cpu_pct_peak": round(host_peak, 3) if host_peak is not None else None,
        "host_mem_total_bytes": mem_total or peaks.get("host_mem_total_bytes"),
        "host_mem_available_bytes_min": avail_low,
        "source": rows[-1].get("source") if rows else None,
        "cpus_allowed": rows[-1].get("cpus_allowed") if rows else None,
        "cpus_online": rows[-1].get("cpus_online") if rows else None,
    }


def summarize(samples: list[dict]) -> dict:
    """Aggregate JSONL samples: overall, and per label in the order the labels first appear."""
    by: dict[str, list[dict]] = {}
    for row in samples:
        by.setdefault(str(row.get("label") or "idle"), []).append(row)
    return {
        "n_samples": len(samples),
        "overall": _aggregate(samples),
        "by_label": {k: _aggregate(v) for k, v in by.items()},
    }


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


class ResourceMonitor:
    """Background ``rsrcsamp`` writing JSONL; phase labels over stdin."""

    def __init__(self, out_path: Path, *, interval_ms: int = DEFAULT_INTERVAL_MS,
                 pid: int | None = None, cgroup: str | None = "auto", label: str = "start"):
        self.out_path = Path(out_path)
        self.interval_ms = interval_ms
        self.pid = pid
        self.cgroup = cgroup
        self.label = label
        self.proc: subprocess.Popen | None = None
        self.binary = find_sampler()
        self.enabled = self.binary is not None

    def start(self) -> bool:
        if not self.enabled or self.binary is None:
            return False
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self.binary, "--out", str(self.out_path), "--interval-ms", str(self.interval_ms),
               "--label", self.label]
        if self.pid is not None:
            cmd += ["--pid", str(self.pid)]
        if self.cgroup:
            cmd += ["--cgroup", self.cgroup]
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        return True

    def set_label(self, label: str) -> None:
        self.label = label
        if not self.proc or self.proc.poll() is not None or not self.proc.stdin:
            return
        try:
            self.proc.stdin.write(f"LABEL {label}\n")
            self.proc.stdin.flush()
        except OSError:
            pass

    def summary(self) -> dict:
        """Summary of what has been written so far; the sampler keeps running."""
        out = summarize(load_jsonl(self.out_path))
        out["sampler"] = self.binary
        out["out"] = str(self.out_path)
        return out

    def snapshot(self, settle_s: float = 0.2) -> dict:
        """Summary for use mid-run, right after ``set_label``: waits for the row that closes the previous phase."""
        if self.proc and self.proc.poll() is None:
            time.sleep(settle_s)
        return self.summary()

    def stop(self) -> dict:
        if self.proc and self.proc.poll() is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.write("QUIT\n")   # the sampler writes its last partial interval, then exits
                    self.proc.stdin.flush()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    self.proc.send_signal(signal.SIGTERM)
                    self.proc.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    self.proc.kill()
                    self.proc.wait(timeout=2)
        return self.summary()


class DockerLabSampler:
    """Poll eco-* containers with ``rsrcsamp --once --cgroup auto`` (kernel cgroup v2)."""

    def __init__(self, out_path: Path, *, interval_s: float = 2.0, containers: tuple[str, ...] = LAB_CONTAINERS):
        self.out_path = Path(out_path)
        self.interval_s = interval_s
        self.containers = containers
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.label = "lab"
        self.binary_in_ctr = "/usr/local/bin/rsrcsamp"

    def start(self, label: str = "lab") -> None:
        self.label = label
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="docker-rsrcsamp", daemon=True)
        self._thread.start()

    def set_label(self, label: str) -> None:
        self.label = label

    def stop(self) -> dict:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval_s + 5 + 8 * len(self.containers))
        samples = load_jsonl(self.out_path)
        by: dict[str, list[dict]] = {}
        for row in samples:
            by.setdefault(str(row.get("container") or "?"), []).append(row)
        # One summary per container: adding CPU % or memory across different containers has no meaning per row.
        return {
            "n_samples": len(samples),
            "mode": "docker_exec_once",
            "out": str(self.out_path),
            "by_container": {name: summarize(rows) for name, rows in by.items()},
        }

    def _loop(self) -> None:
        last: dict[str, tuple[int, int]] = {}   # container -> (cumulative CPU usec, kernel clock ns) at its last poll
        while not self._stop.is_set():
            for name in self.containers:
                row = self._once(name)
                if row:
                    row["label"] = self.label
                    row["container"] = name
                    self._rate_since_last_poll(row, last)
                    with self.out_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(row) + "\n")
            self._stop.wait(self.interval_s)

    @staticmethod
    def _rate_since_last_poll(row: dict, last: dict[str, tuple[int, int]]) -> None:
        """Replace the row's CPU % by the exact average since this container's previous poll.

        ``--once`` measures over a 0.1 s window, which would miss whatever happens between polls. The row also
        carries the container's cumulative CPU time and the kernel clock, so the difference between two polls gives
        the CPU used over the whole gap. The first poll, and a poll after a container restart, keep the short window.
        """
        total, ts = _field(row, "cpu_total_usec"), _field(row, "ts_mono_ns")
        if total is None or ts is None:
            return
        prev = last.get(row["container"])
        last[row["container"]] = (total, ts)
        if prev is None or total < prev[0] or ts <= prev[1]:
            row["window"] = "once"
            return
        wall_s = (ts - prev[1]) / 1e9
        row["cpu_pct"] = round(100.0 * (total - prev[0]) / 1e6 / wall_s, 4)
        row["cpu_pct_per_cpu"] = round(row["cpu_pct"] / max(int(row.get("cpus_allowed") or 1), 1), 4)
        row["wall_ms"] = round(wall_s * 1000.0, 3)
        row["window"] = "since_last_poll"

    def _once(self, container: str) -> dict | None:
        cmd = [
            "docker", "exec", container, self.binary_in_ctr,
            "--once", "--cgroup", "auto", "--label", self.label,
        ]
        try:
            cp = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.SubprocessError):
            return None
        if cp.returncode != 0 or not cp.stdout.strip():
            return None
        line = cp.stdout.strip().splitlines()[-1]
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None


def sample_self_once(*, cgroup: str | None = "auto", label: str = "probe") -> dict | None:
    binary = find_sampler()
    if not binary:
        return None
    cmd = [binary, "--once", "--pid", str(os.getpid()), "--label", label]
    if cgroup:
        cmd += ["--cgroup", cgroup]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0 or not cp.stdout.strip():
        return None
    try:
        return json.loads(cp.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        return None
