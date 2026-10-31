"""Host-side driver for the eco live lab (Docker Compose + client exec).

The lab network is internal, so the dashboard never talks DNS on the host:
every probe / flush / activate runs inside eco-client.

The lab control, zone shapes and on-disk artifacts live in ``lab``, ``shapes`` and ``artifacts``; their
names are re-exported at the bottom so ``driver.<name>`` keeps working.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from live_lab import images as images_mod

REPO = Path(__file__).resolve().parents[2]
ECO = REPO / "ecosystem"
DEFAULT_CONFIG = "configs/_dashboard.yaml"
DEFAULT_STACK = "draft06"
# Prefer this order in the UI; only stacks with ecosystem/stacks/<name>.env are shown.
STACK_CHOICES = ("draft06", "latest")
STACK_BLURBS = {
    "draft06": "Condensed MTL by default; full ladder on EDNS 65050 (MTL v1.2 / liboqs 0.14). "
               "Signs with SPHINCS+-SHA2-128s-simple, the pre-standard form of SLH-DSA; all scenarios validate",
    "latest": "Always-full MTL RRSIGs; FIPS 205 SLH-DSA (liboqs 0.16), same MTL commit as the offline harness. "
              "Plain SLH-DSA is not validated by its Unbound (SERVFAIL)",
}
# What the stack's signer, MTL library and resolver actually call in liboqs (read from the built binaries).
STACK_PRIMITIVE = {
    "draft06": "SPHINCS+-SHA2-128s-simple (pre-standard form of SLH-DSA, liboqs 0.14), in ldns, MTL and Unbound",
    "latest": "SLH_DSA_PURE_SHA2_128S (FIPS 205, liboqs 0.16) in ldns and MTL; Unbound still names SPHINCS+",
}
MTL_FULL = 65050
# Scenarios whose zones the stack's Unbound cannot validate. Seen on `latest`: Unbound logs "verify: Using PQ /
# PQ-MTL Algorithms 246 ... Failed to match any usable anchor" and answers SERVFAIL, while ldns-verify-zone accepts
# the same signed zone and the same scenario validates (AD) on draft06. Strong indication of the cause: liboqs 0.16
# removed the SPHINCS+ names, ldns signs with SLH_DSA_PURE_SHA2_128S, but the Unbound binary at this commit contains only
# the SPHINCS+ name. Not confirmed from source and not yet reported upstream. This records the observation so that a
# MTL vs non-MTL comparison is not run on a stack that cannot.
UNVALIDATED_SCENARIOS = {"latest": ("slh-dsa",)}


def unvalidated_scenarios(stack: str, scenarios) -> list[str]:
    return [s for s in scenarios if s in UNVALIDATED_SCENARIOS.get(stack, ())]
UNBOUND_IP = "10.53.0.53"
DASHBOARD_CONFIG = "configs/_dashboard.yaml"

# ─────────────────────────── single-user guard ───────────────────────────
# The lab uses fixed container names and one subnet, and the benchmark measures timing, so nothing else may
# touch the lab, the signed zones or the CPU while a benchmark suite runs. The suite process names itself
# through this variable; every other process is "someone else" and is refused.
SUITES_DIR = ECO / "results" / "suites"
SUITE_OWNER_ENV = "CIPHEROPS_SUITE_OWNER"
ACTIVE_STATES = ("starting", "running")
STARTING_GRACE_S = 60


class LabBusy(RuntimeError):
    """The lab is in use by a running benchmark suite or signing job."""


def pid_alive(pid: int | None) -> bool:
    """True if ``pid`` is still a process.

    The benchmark page calls this on every Results click and on the progress poll. Spawning
    ``tasklist`` for each call stalls that click while a benchmark is busy, so Windows uses
    OpenProcess instead.
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


def effective_job_state(st: dict, *, now: float | None = None) -> dict:
    """Normalise a job's status: a worker that vanished, or never appeared, reads as 'stopped'.

    'starting' has no worker pid for a moment after launch, so it counts as active for a short grace period.
    """
    st = dict(st)
    if st.get("state") in ACTIVE_STATES:
        now = time.time() if now is None else now
        if st.get("pid"):
            if not pid_alive(st["pid"]):
                st["state"] = "stopped"
                st["error"] = st.get("error") or "The process ended before finishing."
        elif now - float(st.get("started") or 0) > STARTING_GRACE_S:
            st["state"] = "stopped"
            st["error"] = st.get("error") or "The process never started."
    return st


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def active_suite_id() -> str | None:
    """Id of a benchmark suite that is starting or running, if any."""
    if not SUITES_DIR.is_dir():
        return None
    for d in sorted(SUITES_DIR.iterdir()):
        if d.is_dir() and effective_job_state(_read_json(d / "status.json")).get("state") in ACTIVE_STATES:
            return d.name
    return None


def _out(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        cp = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    return cp.stdout.strip() if cp.returncode == 0 else "unavailable"


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return "unavailable"


def provenance(stack: str) -> dict:
    """What a reader needs to repeat or audit a benchmark: code version, host, Docker and the exact images.

    Everything is read from the machine at the time of the run. A value that cannot be read is recorded as
    ``unavailable`` rather than guessed, so a missing field is visible in the report.
    """
    import platform

    cpu = next((ln.split(":", 1)[1].strip() for ln in _read_text("/proc/cpuinfo").splitlines()
                if ln.startswith("model name")), platform.processor() or "unavailable")
    mem = next((ln for ln in _read_text("/proc/meminfo").splitlines() if ln.startswith("MemTotal")), "unavailable")
    images = {}
    for repo in images_mod.REPOS.values():
        info = _out(["docker", "image", "inspect", f"{repo}:{stack}", "--format", "{{.Id}} {{.Created}}"])
        image_id, _, created = info.partition(" ")
        images[repo] = {"id": image_id, "created": created}
    return {
        "git_commit": _out(["git", "rev-parse", "HEAD"], cwd=REPO),
        "git_dirty": bool(_out(["git", "status", "--porcelain"], cwd=REPO)),
        "platform": platform.platform(),
        "kernel": platform.release(),
        "cpu_model": cpu,
        "cpus": os.cpu_count(),
        "mem_total": mem,
        "cpu_governor": _read_text("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "python": platform.python_version(),
        "docker_server": _out(["docker", "version", "--format", "{{.Server.Version}}"]),
        "stack": stack,
        "stack_pins": stack_pins(stack),
        "images": images,
        # The live lab is NOT pinned to dedicated cores (the offline harness is): containers share every host CPU.
        "cpu_isolation": "none (lab containers share all host CPUs)",
    }


def jobs_dir() -> Path:
    """Where background-job state lives. Not under work/ or results/: Docker creates those as root, and a
    folder the dashboard cannot write to would stop Prepare from starting."""
    return ECO / "jobs"


def active_prepare_tags() -> list[str]:
    """Lab tags that have a signing job starting or running."""
    root = jobs_dir()
    if not root.is_dir():
        return []
    return [d.parent.parent.name for d in sorted(root.glob("*/prepare-live/status.json"))
            if effective_job_state(_read_json(d)).get("state") in ACTIVE_STATES]


def build_status() -> dict:
    """Status of the background image build or pull ({} if none ever ran); a vanished worker reads as 'stopped'."""
    return effective_job_state(_read_json(jobs_dir() / "_images" / "status.json"))


def active_build_stack() -> str | None:
    st = build_status()
    return (st.get("stack") or "?") if st.get("state") in ACTIVE_STATES else None


def require_no_build(stack: str | None = None) -> None:
    """Refuse to start containers or sign with images that are still being built or pulled."""
    live = active_build_stack()
    if live and (stack is None or stack == live):
        raise LabBusy(f"Images for {live} are still being built or pulled. Wait for that to finish or cancel it first.")


def offline_status() -> dict:
    """Status of the background offline-harness run ({} if none ever ran); a vanished worker reads as 'stopped'."""
    return effective_job_state(_read_json(jobs_dir() / "_offline" / "status.json"))


def active_offline() -> bool:
    return offline_status().get("state") in ACTIVE_STATES


def require_no_offline() -> None:
    """The offline harness times on one dedicated core; anything else CPU-heavy would distort it."""
    if active_offline():
        raise LabBusy("An offline benchmark is running on a dedicated core. Wait for it to finish or cancel it "
                      "first: running anything else now would distort its timings.")


def require_no_suite(*, force: bool = False) -> None:
    """Refuse an operation that would disturb a running benchmark suite (the suite's own process passes)."""
    if force:
        return
    sid = active_suite_id()
    if sid and sid != os.environ.get(SUITE_OWNER_ENV):
        raise LabBusy(f"Benchmark {sid} is running and is using the lab. Wait for it to finish or cancel it first.")


def require_not_signing(h: "LabHandle") -> None:
    if h.tag in active_prepare_tags():
        raise LabBusy(f"Signing is still running for lab {h.tag}. Wait for it to finish or cancel it first.")


def list_stacks() -> list[str]:
    """Image versions the dashboard can build/run (env file must exist)."""
    root = ECO / "stacks"
    out = [s for s in STACK_CHOICES if (root / f"{s}.env").is_file()]
    for p in sorted(root.glob("*.env")):
        if p.stem not in out:
            out.append(p.stem)
    return out or [DEFAULT_STACK]


def stack_label(stack: str) -> str:
    blur = STACK_BLURBS.get(stack)
    return f"{stack} — {blur}" if blur else stack


def stack_pins(stack: str) -> dict[str, str]:
    """Key pins from ecosystem/stacks/<stack>.env (MTL, liboqs, …)."""
    path = ECO / "stacks" / f"{stack}.env"
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


# Windows: a console program started by a process that has no console (every background worker) gets a
# new console window of its own. CREATE_NO_WINDOW stops that; it is 0 (a no-op) on other systems.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def background_process_kwargs() -> dict:
    """``Popen`` arguments for a worker that must outlive the page and show no window.

    On Windows the worker gets its own hidden console (not "detached", which has none), so the
    ``docker`` calls it makes inherit that console instead of each opening a visible one.
    """
    if os.name == "nt":
        return {"creationflags": NO_WINDOW | 0x00000200}   # hidden console + its own process group
    return {"start_new_session": True}


def _run(cmd: list[str], timeout: int | None = 120, check: bool = True) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("CIPHEROPS_GIT_SHA", "live-dashboard")
    cp = subprocess.run(cmd, cwd=ECO, capture_output=True, text=True, timeout=timeout, env=env,
                        creationflags=NO_WINDOW)
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"$ {' '.join(cmd)}\nexit {cp.returncode}\n{cp.stdout[-4000:]}\n{cp.stderr[-4000:]}"
        )
    return cp


# Building and pulling images live in images.py (what a stack needs, verification) and image_job.py (the background
# job); the lab only reads their status through the helpers above.


# ─────────────────────────── re-exports ───────────────────────────
# Everything that moved out of this module. These imports stay at the bottom: the modules import the
# constants and helpers above from here.
from live_lab.shapes import (  # noqa: E402,F401
    CUSTOM_SIZE, DEFAULT_SEED, MAX_SLD_SERVERS, MAX_TLDS, PREPARE_OVERHEAD_S, PROFILE_DEFAULTS,
    RRSIG_PER_NAME, RRSIG_PER_ZONE, SIGN_SECONDS_PER_RRSIG, WORKLOAD_DEFAULTS, ZONE_SIZE_PRESETS,
    _TLD_RE, _int_list, _total_names, custom_shape, exact_rrsigs, format_duration, shape_for, shape_stats,
)
from live_lab.lab import (  # noqa: E402,F401
    DIG_MAX_ARGS, DIG_PLAIN_FLAGS, DIG_VALUE_FLAGS, NAMES, SCENARIO_ALGS, STAT_KEYS, LabHandle,
    _lab_name, _reclaim_fixed_lab_names, activate, capture_target, client_exec, compose_cmd,
    check_probe_args, container_logs, dig, dig_argv, dig_presets, down, flush, probe, prepare, prepare_command,
    prepare_container_name, query_presets, run_benchmark, sanitize_label, sign_trials, sld_server_ip,
    start_capture, status, stop_capture, stop_other_labs, unbound_stats, unbound_stats_delta, up,
    write_ui_config,
)
from live_lab.artifacts import (  # noqa: E402,F401
    UI_PREFS_PATH, _dir_size, _empty_session, _read_manifest, _suite_dirs, append_activity_file,
    append_session_query, chunk_log, clear_demo_data, clear_suites, infer_ui_from_disk, list_labs,
    list_manifests, list_pcaps, list_run_dirs, list_runs, live_capture_inventory, load_manifest,
    load_session, load_ui_prefs, manifest_rows, prepare_summary, remove_tree, reset_preview, save_session,
    save_ui_prefs, session_path, session_queries, split_lab_tag, suite_footprint, zone_size_for_lab_name,
)
