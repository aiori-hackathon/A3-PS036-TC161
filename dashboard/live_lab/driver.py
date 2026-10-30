"""Host-side driver for the eco live lab (Docker Compose + client exec).

The lab network is internal, so the dashboard never talks DNS on the host:
every probe / flush / activate runs inside eco-client.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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
    for repo in ECO_IMAGE_REPOS:
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
    """Status of the background image build ({} if none ever ran); a vanished worker reads as 'stopped'."""
    return effective_job_state(_read_json(jobs_dir() / "_build" / "status.json"))


def active_build_stack() -> str | None:
    st = build_status()
    return (st.get("stack") or "?") if st.get("state") in ACTIVE_STATES else None


def require_no_build(stack: str | None = None) -> None:
    """Refuse to start containers or sign with images that are still being built."""
    live = active_build_stack()
    if live and (stack is None or stack == live):
        raise LabBusy(f"Images for {live} are still being built. Wait for the build to finish or cancel it first.")


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


def remove_tree(path: Path, stack: str = "draft06") -> bool:
    """Delete a folder under ecosystem/, including files Docker created as root.

    A plain delete fails silently on those, so the folder would look removed while its contents stayed.
    Falls back to ``rm`` inside the tools image, which runs as root. Returns True if the folder is gone.
    """
    import shutil

    path = Path(path)
    if not path.exists():
        return True
    shutil.rmtree(path, ignore_errors=True)
    if not path.exists():
        return True
    try:
        rel = path.resolve().relative_to(ECO.resolve())
    except ValueError:
        return False  # never delete outside ecosystem/
    if not rel.parts:
        return False
    _run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "rm", "-v", f"{ECO}:/eco:z",
          f"cipherops-eco-tools:{stack}", "-rf", "--", f"/eco/{rel.as_posix()}"], check=False, timeout=300)
    return not path.exists()


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

NAMES = {
    "10.53.0.100": "client",
    "10.53.0.53": "unbound",
    "10.53.0.2": "root",
}

# Zone-size presets for the UI (written into configs/_dashboard.yaml before Prepare).
ZONE_SIZE_PRESETS: dict[str, dict] = {
    "small": {
        "label": "Small (smoke)",
        "name": "eco-ui-small",
        "tlds": ["test"],
        "sld_count": 2,
        "sld_sizes": [5],
        "sld_servers": 1,
        "n_queries_default": 40,
    },
    "medium": {
        "label": "Medium",
        "name": "eco-ui-medium",
        "tlds": ["test"],
        "sld_count": 4,
        "sld_sizes": [25, 50],
        "sld_servers": 1,
        "n_queries_default": 200,
    },
    "large": {
        "label": "Large (lab-scale)",
        "name": "eco-ui-large",
        "tlds": ["test", "lab"],
        "sld_count": 8,
        "sld_sizes": [25, 100, 25, 250],
        "sld_servers": 2,
        "n_queries_default": 1000,
    },
    # Numbers come from the sidebar; see custom_shape(). Defaults mirror Medium.
    "custom": {
        "label": "Custom…",
        "name": "eco-ui-custom",
        "tlds": ["test"],
        "sld_count": 4,
        "sld_sizes": [25, 50],
        "sld_servers": 1,
        "n_queries_default": 200,
    },
}

CUSTOM_SIZE = "custom"
DEFAULT_SEED = 20261005

# The lab's address plan (ecolab.config) gives TLD servers 10.53.0.11-19 and leaf servers 10.53.0.21-29.
MAX_TLDS = 9
MAX_SLD_SERVERS = 9
_TLD_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,14}$")

# Exact RRSIG model (checked against real signed zones). Every name has its record plus an NSEC3 proof, so
# 2 per name, and a zone adds 7 of its own (SOA, NS, DNSKEY, NSEC3PARAM, apex NSEC3 and so on).
# A TLD signs one name per child, the root one per TLD, and the root has one extra record: see exact_rrsigs.
RRSIG_PER_NAME = 2
RRSIG_PER_ZONE = 7
# Rough sign-time model, calibrated on the Medium lab (353 RRSIGs across root, TLD and leaf zones).
# Only meant to warn about long Prepares, not to predict them.
SIGN_SECONDS_PER_RRSIG = {
    "rsa": 0.0058, "ecdsa": 0.0007, "ed25519": 0.0007, "slh-dsa": 0.33, "mtl": 0.0087, "mixed-mtl": 0.0087,
}
PREPARE_OVERHEAD_S = 1.5          # key generation and checks per zone and scenario

# Defaults for the numbers the Benchmark tile can change (match configs/lab.yaml).
WORKLOAD_DEFAULTS = {"nx_fraction": 0.10, "zone_zipf_s": 1.0, "name_zipf_s": 1.1, "timeout_s": 5.0}
PROFILE_DEFAULTS: dict[str, dict[str, float]] = {
    "lan": {"client_ms": 0, "root_ms": 0, "tld_ms": 0, "sld_ms": 0, "loss_pct": 0},
    "wan": {"client_ms": 2, "root_ms": 20, "tld_ms": 35, "sld_ms": 50, "loss_pct": 0},
    "lossy": {"client_ms": 2, "root_ms": 20, "tld_ms": 35, "sld_ms": 50, "loss_pct": 1},
}


def _int_list(text: str) -> list[int]:
    out = []
    for tok in re.split(r"[,\s;]+", (text or "").strip()):
        if tok:
            out.append(int(tok))
    return out


def custom_shape(*, tlds: str, sld_count: int, sizes: str, servers: int,
                 seed: int = DEFAULT_SEED) -> tuple[dict | None, list[str], list[str]]:
    """Validate the Custom zone numbers.

    Returns (shape, errors, warnings). ``shape`` is None when there are errors. Its ``name`` embeds a
    short hash of the numbers, so signed zones and results of different shapes never share a folder.
    """
    errors: list[str] = []
    warnings: list[str] = []

    tld_list: list[str] = []
    for tok in re.split(r"[,\s;]+", (tlds or "").strip().lower()):
        if tok:
            tld_list.append(tok.strip("."))
    if not tld_list:
        errors.append("Enter at least one TLD.")
    bad = [t for t in tld_list if not _TLD_RE.match(t)]
    if bad:
        errors.append(f"TLD names use letters, digits and '-' (max 15 chars): {', '.join(bad)}.")
    if len(set(tld_list)) != len(tld_list):
        errors.append("TLD names must be unique.")
    if len(tld_list) > MAX_TLDS:
        errors.append(f"At most {MAX_TLDS} TLDs (the lab's address plan).")

    try:
        size_list = _int_list(sizes)
    except ValueError:
        size_list = []
        errors.append("Names per leaf zone must be whole numbers, e.g. 25, 100, 250.")
    else:
        if not size_list:
            errors.append("Enter at least one size for names per leaf zone.")
        elif min(size_list) < 1 or max(size_list) > 20000:
            errors.append("Names per leaf zone must be between 1 and 20000.")
        elif len(size_list) > 64:
            errors.append("At most 64 size entries.")

    sld_count = int(sld_count)
    servers = int(servers)
    if not 1 <= sld_count <= 200:
        errors.append("Leaf zones must be between 1 and 200.")
    if not 1 <= servers <= MAX_SLD_SERVERS:
        errors.append(f"Leaf servers must be between 1 and {MAX_SLD_SERVERS}.")
    elif 1 <= sld_count <= 200 and servers > sld_count:
        errors.append("More leaf servers than leaf zones: some servers would host nothing.")

    if errors:
        return None, errors, warnings

    shape = {
        "label": "Custom…",
        "tlds": tld_list,
        "sld_count": sld_count,
        "sld_sizes": size_list,
        "sld_servers": servers,
        "seed": int(seed),
        "custom": True,
    }
    import hashlib
    digest = hashlib.sha1(json.dumps(
        [tld_list, sld_count, size_list, servers, int(seed)]).encode()).hexdigest()[:8]
    shape["name"] = f"eco-ui-custom-{digest}"
    names = _total_names(shape)
    shape["n_queries_default"] = max(40, min(2000, names * 2))
    if names > 2000:
        warnings.append(f"{names:,} names in total: signing with SLH-DSA will take a long time.")
    if len(tld_list) + servers + 3 > 12:
        warnings.append("Many containers will start; check Docker has enough memory.")
    return shape, errors, warnings


def _total_names(shape: dict) -> int:
    sizes = shape["sld_sizes"]
    return sum(int(sizes[i % len(sizes)]) for i in range(int(shape["sld_count"])))


def shape_for(zone_size: str, custom: dict | None = None) -> tuple[dict | None, list[str], list[str]]:
    """The zone shape for a preset key, or the validated Custom numbers."""
    if zone_size == CUSTOM_SIZE:
        return custom_shape(**(custom or {"tlds": "test", "sld_count": 4, "sizes": "25, 50", "servers": 1}))
    if zone_size not in ZONE_SIZE_PRESETS:
        raise ValueError(f"unknown zone size {zone_size!r}")
    return ZONE_SIZE_PRESETS[zone_size], [], []


def exact_rrsigs(names: int, zones: int) -> int:
    """RRSIGs for a whole hierarchy of ``zones`` zones whose leaf zones hold ``names`` names in total.

    Leaf zone = 2*names + 7, TLD = 2*children + 7, root = 2*TLDs + 8. Every zone except the root is a child
    of exactly one other, so the "children" terms add 2 per non-root zone, and the root has one extra.
    """
    return RRSIG_PER_NAME * names + RRSIG_PER_ZONE * zones + RRSIG_PER_NAME * (zones - 1) + 1


def shape_stats(shape: dict, scenarios: list[str], jobs: int = 2) -> dict:
    """Plain numbers describing a zone shape, plus a rough Prepare-time estimate."""
    zones = 1 + len(shape["tlds"]) + int(shape["sld_count"])
    names = _total_names(shape)
    rrsigs = exact_rrsigs(names, zones)
    cpu = sum(SIGN_SECONDS_PER_RRSIG.get(s, 0.01) for s in scenarios) * rrsigs
    wall = cpu / max(1, int(jobs)) + PREPARE_OVERHEAD_S * zones * max(1, len(scenarios)) / max(1, int(jobs))
    return {
        "zones": zones,
        "names": names,
        # root + TLD servers + leaf servers, plus the resolver and the client
        "containers": 1 + len(shape["tlds"]) + int(shape["sld_servers"]) + 2,
        "est_rrsigs": int(rrsigs),
        "est_prepare_s": wall,
    }


def format_duration(seconds: float) -> str:
    s = int(round(seconds))
    if s < 90:
        return f"~{max(s, 1)} s"
    if s < 5400:
        return f"~{round(s / 60)} min"
    return f"~{s / 3600:.1f} h"

# ldns-keygen algorithm names (signer already supports RSASHA256 + -b 2048).
# Classical trio matches main Cipherops: rsa-2048, ecdsa-p256, ed25519.
SCENARIO_ALGS: dict[str, dict[str, str]] = {
    "rsa": {"root": "RSASHA256", "tld": "RSASHA256", "sld": "RSASHA256"},
    "ecdsa": {"root": "ECDSAP256SHA256", "tld": "ECDSAP256SHA256", "sld": "ECDSAP256SHA256"},
    "ed25519": {"root": "ED25519", "tld": "ED25519", "sld": "ED25519"},
    "slh-dsa": {"root": "SLH-DSA-SHA2-128s", "tld": "SLH-DSA-SHA2-128s", "sld": "SLH-DSA-SHA2-128s"},
    "mtl": {
        "root": "SLH-DSA-SHA2-128s-MTL-SHA2-128",
        "tld": "SLH-DSA-SHA2-128s-MTL-SHA2-128",
        "sld": "SLH-DSA-SHA2-128s-MTL-SHA2-128",
    },
    "mixed-mtl": {
        "root": "ECDSAP256SHA256",
        "tld": "ECDSAP256SHA256",
        "sld": "SLH-DSA-SHA2-128s-MTL-SHA2-128",
    },
}

# Counters we surface after a query / cold-warm (unbound-control stats resets on read).
STAT_KEYS = (
    "total.num.queries",
    "total.num.cachehits",
    "total.num.cachemiss",
    "total.recursion.time.avg",
    "total.num.answer.secure",
    "total.num.answer.bogus",
    "mem.cache.rrset",
    "mem.cache.message",
    "mem.mod.subnet",
)


def _lab_name(config: str) -> str:
    text = (ECO / config).read_text(encoding="utf-8")
    m = re.search(r"^name:\s*(\S+)", text, re.M)
    if not m:
        raise RuntimeError(f"no name: in {config}")
    return m.group(1)


@dataclass
class LabHandle:
    config: str = DEFAULT_CONFIG
    stack: str = DEFAULT_STACK

    @property
    def lab(self) -> str:
        return _lab_name(self.config)

    @property
    def tag(self) -> str:
        return f"{self.lab}-{self.stack}"

    @property
    def compose(self) -> Path:
        return ECO / "work" / self.tag / "compose.yml"

    @property
    def work(self) -> Path:
        return ECO / "work" / self.tag

    @property
    def capture_dir(self) -> Path:
        # Pure path: reading it must not create folders, or every zone shape typed into the
        # Custom fields would leave an empty results folder behind. Writers call ensure_capture_dir().
        return ECO / "results" / self.tag / "live-dashboard"

    def ensure_capture_dir(self) -> Path:
        d = self.capture_dir
        d.mkdir(parents=True, exist_ok=True)
        return d

    def prepared(self) -> bool:
        """True only when Prepare finished: compose file plus at least one signed scenario."""
        zones = self.work / "zones"
        if not self.compose.is_file() or not zones.is_dir():
            return False
        return any(p.is_file() for p in zones.glob("*/manifest.json"))

    def scenarios(self) -> list[str]:
        import yaml
        return list(yaml.safe_load((ECO / self.config).read_text(encoding="utf-8"))["scenarios"])

    def active_scenario(self) -> str | None:
        p = self.work / "active" / "scenario.txt"
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8").split()[0]

    def cfg(self) -> dict:
        import yaml
        return yaml.safe_load((ECO / self.config).read_text(encoding="utf-8"))


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


def compose_cmd(h: LabHandle, *args: str) -> list[str]:
    if not h.compose.exists():
        raise RuntimeError(f"missing {h.compose}; run Prepare first")
    return ["docker", "compose", "-f", str(h.compose), *args]


def status(h: LabHandle) -> dict:
    out = {"prepared": h.prepared(), "compose": str(h.compose), "active": h.active_scenario(),
           "containers": {}, "images": {}}
    for img in (f"cipherops-eco-nsd:{h.stack}", f"cipherops-eco-unbound:{h.stack}",
                f"cipherops-eco-tools:{h.stack}"):
        cp = _run(["docker", "image", "inspect", img, "--format", "{{.Id}}"], check=False)
        out["images"][img] = cp.returncode == 0
    if not h.compose.exists():
        return out
    cp = _run([*compose_cmd(h, "ps", "--format", "json"), ], check=False, timeout=30)
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = row.get("Service") or row.get("Name", "")
        state = (row.get("State") or row.get("Status") or "").lower()
        out["containers"][name] = state.startswith("running") or "up" in state
    if not out["containers"]:
        cp2 = _run(compose_cmd(h, "ps"), check=False, timeout=30)
        for svc in ("root", "unbound", "client", "tld-test", "sld1", "sld2", "tld-lab"):
            out["containers"][svc] = svc in cp2.stdout and "Up" in cp2.stdout
    return out


ECO_IMAGE_REPOS = ("cipherops-eco-nsd", "cipherops-eco-unbound", "cipherops-eco-tools")


def build_command(stack: str) -> list[str]:
    """``docker compose build`` for cipherops-eco-{nsd,unbound,tools}:<stack> (same as eco.ps1 build).

    A first build compiles OpenSSL, liboqs and MTL from source and takes a long time, so the dashboard runs
    it as a background job (``build_job``).
    """
    env_file = ECO / "stacks" / f"{stack}.env"
    compose = ECO / "docker" / "build.yml"
    if not env_file.is_file():
        raise RuntimeError(f"missing stack env {env_file}")
    if not compose.is_file():
        raise RuntimeError(f"missing {compose}")
    return ["docker", "compose", "-f", str(compose), "--env-file", str(env_file), "build"]


def pull_images(h: LabHandle, registry: str) -> str:
    """Pull pre-built eco images from a registry and retag to local compose names.

    Expects remote tags like ``{registry}/cipherops-eco-nsd:{stack}`` (and unbound, tools).
    """
    require_no_suite()
    require_no_build()
    registry = (registry or "").strip().rstrip("/")
    if not registry:
        raise RuntimeError("registry is empty — set a host like ghcr.io/org/cipherops")
    chunks: list[str] = []
    for repo in ECO_IMAGE_REPOS:
        remote = f"{registry}/{repo}:{h.stack}"
        local = f"{repo}:{h.stack}"
        chunks.append(f"=== pull {remote} ===\n")
        cp = _run(["docker", "pull", remote], timeout=None, check=False)
        chunks.append((cp.stdout or "") + (cp.stderr or ""))
        if cp.returncode != 0:
            raise RuntimeError(
                f"docker pull failed for {remote}\n{(cp.stdout or '')[-1500:]}\n{(cp.stderr or '')[-1500:]}"
            )
        tag = _run(["docker", "tag", remote, local], timeout=60, check=False)
        chunks.append(f"tagged → {local}\n")
        if tag.returncode != 0:
            chunks.append((tag.stderr or "") + "\n")
    return "".join(chunks)


def prepare_summary(h: LabHandle, *, wall_s: float | None = None,
                    scenarios: list[str] | None = None) -> dict:
    """Human-facing summary of what Prepare produced on disk."""
    zones_root = h.work / "zones"
    scen = scenarios or ([p.name for p in zones_root.iterdir() if p.is_dir()] if zones_root.is_dir() else [])
    rows = []
    for s in scen:
        man = load_manifest(h, s)
        if not man:
            continue
        zs = man.get("zones") or []
        rows.append({
            "scenario": s,
            "zones": len(zs),
            "sign_seconds": round(sum(float(z.get("sign_seconds") or 0) for z in zs), 3),
            "verify_seconds": round(sum(float(z.get("verify_seconds") or 0) for z in zs), 3),
            "signed_bytes": sum(int(z.get("signed_bytes") or 0) for z in zs),
            "path": str(zones_root / s),
        })
    return {
        "lab": h.lab,
        "tag": h.tag,
        "config": h.config,
        "wall_s": wall_s,
        "work_dir": str(h.work),
        "zones_dir": str(zones_root),
        "active_dir": str(h.work / "active"),
        "compose": str(h.compose),
        "active_scenario": h.active_scenario(),
        "scenarios": rows,
    }


def write_ui_config(
    *,
    zone_size: str = "small",
    scenarios: list[str] | None = None,
    n_queries: int | None = None,
    trials: int = 1,
    network_run: list[str] | None = None,
    udp_limits: list[int] | None = None,
    seed: int = DEFAULT_SEED,
    custom: dict | None = None,
    workload: dict | None = None,
    profiles: dict | None = None,
    shape: dict | None = None,
    out_config: str = DASHBOARD_CONFIG,
) -> str:
    """Write a lab config (``configs/_dashboard.yaml`` by default) from UI options; return its relative path.

    ``custom`` holds the Custom zone numbers (used when ``zone_size == "custom"``); ``workload`` and
    ``profiles`` override WORKLOAD_DEFAULTS / PROFILE_DEFAULTS. A ready-made ``shape`` (the benchmark's
    own dataset sizes) replaces the zone-size choice.
    """
    import yaml

    if shape is not None:
        preset, errors, _warnings = shape, [], []
    else:
        preset, errors, _warnings = shape_for(zone_size, custom)
    if errors or preset is None:
        raise ValueError("; ".join(errors) or "invalid zone shape")
    seed = int(preset.get("seed", seed))
    wl = {**WORKLOAD_DEFAULTS, **(workload or {})}
    prof = {k: {**v, **((profiles or {}).get(k) or {})} for k, v in PROFILE_DEFAULTS.items()}
    # None → default trio; explicit [] must fail (do not treat [] as falsy default).
    if scenarios is None:
        chosen = ["ecdsa", "slh-dsa", "mtl"]
    else:
        chosen = list(scenarios)
    if not chosen:
        raise ValueError("select at least one scenario")
    bad = [s for s in chosen if s not in SCENARIO_ALGS]
    if bad:
        raise ValueError(f"unknown scenarios: {bad}")
    profiles_run = network_run or ["lan"]
    cfg = {
        "name": preset["name"],
        "seed": seed,
        "hierarchy": {
            "tlds": list(preset["tlds"]),
            "sld_count": int(preset["sld_count"]),
            "sld_sizes": list(preset["sld_sizes"]),
            "sld_servers": int(preset["sld_servers"]),
            "key_scheme": "csk",
        },
        "signing": {
            "inception": "20260101000000",
            "expiration": "20360101000000",
            "nsec3_iterations": 0,
        },
        "scenarios": {s: dict(SCENARIO_ALGS[s]) for s in chosen},
        "resolver": {
            "udp_limits": udp_limits or [1232],
            "cache_mb": 64,
            "ladder_cache_mb": 4,
            "qname_minimisation": True,
        },
        "workload": {
            "n_queries": int(n_queries if n_queries is not None else preset["n_queries_default"]),
            "nx_fraction": float(wl["nx_fraction"]),
            "zone_zipf_s": float(wl["zone_zipf_s"]),
            "name_zipf_s": float(wl["name_zipf_s"]),
            "stub_dnssec_ok": False,
            "timeout_s": float(wl["timeout_s"]),
        },
        "network": {
            "run": list(profiles_run),
            "profiles": prof,
        },
        "trials": int(trials),
        "cpusets": {"client": "3", "unbound": "2", "nsd": "4-5"},
    }
    path = ECO / out_config
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return out_config


def prepare_container_name(h: LabHandle) -> str:
    """Fixed name for the live-view Prepare container, so a stuck one can be removed."""
    return f"cipherops-prepare-{h.tag}"


def prepare_command(h: LabHandle, jobs: int = 2, scenarios: list[str] | None = None, *,
                    events: bool = False) -> list[str]:
    """The ``docker run`` command line for Prepare.

    ``events=True`` is for the dashboard's live view: the signer prints ``@@EVENT {json}`` lines (see
    ``ecolab.sign.emit``) as each zone starts and finishes, Python output is unbuffered, and the container
    gets a fixed name. The signing itself is identical either way.
    """
    extra = f" --scenarios {','.join(scenarios)}" if scenarios else ""
    cmd = ["docker", "run", "--rm", "--network", "none"]
    if events:
        cmd += ["--name", prepare_container_name(h), "-e", "ECOLAB_EVENTS=1", "-e", "PYTHONUNBUFFERED=1"]
    cmd += [
        "-v", f"{ECO}:/eco:z",
        "-v", f"{REPO / 'src'}:/work/src:ro,z",
        f"cipherops-eco-tools:{h.stack}",
        f"python3 -m ecolab prepare {h.config} --jobs {jobs}{extra}",
    ]
    return cmd


def prepare(h: LabHandle, jobs: int = 2, scenarios: list[str] | None = None) -> str:
    cp = _run(prepare_command(h, jobs, scenarios), timeout=None)
    return cp.stdout + cp.stderr


def sign_trials(h: LabHandle, scenarios: list[str], trials: int, names: list[int], out_container_path: str) -> str:
    """Repeat sign + verify of sample leaf zones in the tools image; CSV goes to ``out_container_path``."""
    require_no_suite()
    img = f"cipherops-eco-tools:{h.stack}"
    cp = _run([
        "docker", "run", "--rm", "--network", "none",
        "-v", f"{ECO}:/eco:z",
        "-v", f"{REPO / 'src'}:/work/src:ro,z",
        img,
        f"python3 -m ecolab signtrials {h.config} --scenarios {','.join(scenarios)} "
        f"--trials {int(trials)} --names {','.join(str(int(n)) for n in names)} --out {out_container_path}",
    ], timeout=None)
    return cp.stdout + cp.stderr


def run_benchmark(
    h: LabHandle,
    *,
    scenarios: list[str] | None = None,
    profiles: list[str] | None = None,
    trials: int | None = None,
    udp: int | None = None,
    run_label: str = "",
) -> str:
    """Full ecolab run inside the client (lab must be up). Can take a long time."""
    flags = [f"--stamp {time.strftime('%Y%m%d-%H%M%S')}"]
    label = sanitize_label(run_label)
    if label:
        flags.append(f"--run-label {label}")
    if scenarios:
        flags.append(f"--scenarios {','.join(scenarios)}")
    if profiles:
        flags.append(f"--profiles {','.join(profiles)}")
    if trials is not None:
        flags.append(f"--trials {int(trials)}")
    if udp is not None:
        flags.append(f"--udp {int(udp)}")
    cmd = f"python3 -m ecolab run {h.config} {' '.join(flags)}".strip()
    return client_exec(h, cmd, timeout=None)


def sanitize_label(label: str | None) -> str:
    """Reduce a user-typed run name to a path-safe token (letters, digits, '-', '_'), max 40 chars."""
    raw = "".join(c if (c.isascii() and c.isalnum()) or c in "-_" else "-" for c in (label or "").strip())
    return "-".join(p for p in raw.split("-") if p)[:40].strip("_")


def list_labs() -> list[dict]:
    """Every lab folder that has signing data and/or saved benchmark runs, newest activity first."""
    tags: dict[str, float] = {}
    for p in (ECO / "work").glob("*/zones/*/manifest.json"):
        t = p.parents[2].name
        tags[t] = max(tags.get(t, 0.0), p.stat().st_mtime)
    for p in (ECO / "results").glob("*/*/summary.csv"):
        t = p.parents[1].name
        tags[t] = max(tags.get(t, 0.0), p.stat().st_mtime)
    return [{"tag": t, "mtime": m} for t, m in sorted(tags.items(), key=lambda kv: kv[1], reverse=True)]


def list_run_dirs(h: LabHandle, tag: str | None = None) -> list[Path]:
    root = ECO / "results" / (tag or h.tag)
    if not root.is_dir():
        return []
    dirs = [p for p in root.iterdir() if p.is_dir() and p.name != "live-dashboard"]
    return sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)


def list_runs(h: LabHandle, tag: str | None = None) -> list[dict]:
    """Saved benchmark runs, newest first, with a friendly name, timestamp and what was measured."""
    out: list[dict] = []
    for p in list_run_dirs(h, tag):
        meta: dict = {}
        mj = p.joinpath("run.json")
        if mj.is_file():
            try:
                meta = json.loads(mj.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
        stamp, _, tail = p.name.partition("_")
        label = meta.get("run_label") or tail
        try:
            when = datetime.strptime(stamp, "%Y%m%d-%H%M%S").strftime("%d %b %Y, %H:%M:%S")
        except ValueError:
            when = datetime.fromtimestamp(p.stat().st_mtime).strftime("%d %b %Y, %H:%M:%S")
        out.append({
            "path": p,
            "folder": p.name,
            "label": label,
            "title": f"{label} - {when}" if label else when,
            "when": when,
            "scenarios": meta.get("scenarios") or [],
            "profiles": meta.get("profiles") or [],
            "trials": meta.get("trials"),
            "cfg": meta.get("lab") or {},
            "has_results": any(p.glob("*.csv")),
        })
    return out


def stop_other_labs(h: LabHandle) -> list[str]:
    """Bring down any other prepared lab.

    Container names (``eco-root``, …) and the ``10.53.0.0/24`` subnet are fixed, so only one
    lab can run at a time. We always ``compose down`` other work folders — even when their
    containers are already stopped — otherwise the old bridge network and name reservations
    block starting another stack (e.g. draft06 leftovers vs latest).
    """
    stopped: list[str] = []
    work_root = ECO / "work"
    if not work_root.is_dir():
        return stopped
    for compose in work_root.glob("*/compose.yml"):
        if compose.resolve() == h.compose.resolve():
            continue
        _run(
            ["docker", "compose", "-f", str(compose), "down", "--remove-orphans"],
            check=False, timeout=120,
        )
        stopped.append(compose.parent.name)
    return stopped


def _reclaim_fixed_lab_names() -> str:
    """Force-remove leftover eco-* containers that still hold the fixed names."""
    names = (
        "eco-root", "eco-unbound", "eco-client",
        *[f"eco-tld-{i}" for i in range(1, 10)],
        "eco-tld-test", "eco-tld-lab",
        *[f"eco-sld{i}" for i in range(1, 10)],
    )
    # Also discover any other eco-* from docker ps -a
    cp = _run(["docker", "ps", "-aq", "--filter", "name=^eco-"], check=False, timeout=30)
    ids = cp.stdout.split()
    extra = ""
    if ids:
        rm = _run(["docker", "rm", "-f", *ids], check=False, timeout=60)
        extra = (rm.stdout or "") + (rm.stderr or "")
    else:
        _run(["docker", "rm", "-f", *names], check=False, timeout=60)
    # Stale dnsnet from another compose project name (stack in the project name)
    nets = _run(["docker", "network", "ls", "--format", "{{.Name}}"], check=False, timeout=30)
    for net in nets.stdout.splitlines():
        net = net.strip()
        if "cipherops-eco-" in net and net.endswith("_dnsnet"):
            _run(["docker", "network", "rm", net], check=False, timeout=30)
    return extra


def up(h: LabHandle) -> str:
    require_no_suite()
    require_no_offline()
    require_no_build(h.stack)
    require_not_signing(h)
    stopped = stop_other_labs(h)
    # Own project may also have exited containers holding names after a failed up
    _run(compose_cmd(h, "down", "--remove-orphans"), check=False, timeout=120)
    reclaim = _reclaim_fixed_lab_names()
    cp = _run(compose_cmd(h, "up", "-d"), timeout=180)
    note = ""
    if stopped:
        note += f"Stopped other lab(s) first: {', '.join(stopped)}\n"
    if reclaim.strip():
        note += "Reclaimed leftover eco-* containers/networks\n"
    return note + cp.stdout + cp.stderr


def down(h: LabHandle, *, force: bool = False) -> str:
    require_no_suite(force=force)
    cp = _run(compose_cmd(h, "down"), timeout=120, check=False)
    return cp.stdout + cp.stderr


def client_exec(h: LabHandle, inner: str, timeout: int | None = 120) -> str:
    require_no_suite()
    cp = _run(compose_cmd(h, "exec", "-T", "client", "bash", "-lc", inner), timeout=timeout)
    return cp.stdout


def activate(h: LabHandle, scenario: str, udp: int = 1232) -> str:
    require_not_signing(h)
    return client_exec(h, f"python3 -m ecolab activate {h.config} --scenario {scenario} --udp {udp}", timeout=180)


def flush(h: LabHandle) -> str:
    return client_exec(h, "python3 -m ecolab flush", timeout=60)


def probe(h: LabHandle, qname: str, qtype: str = "A", *, server: str = "10.53.0.53",
          mtl_full: bool = False, dnssec: bool = True, recurse: bool = True,
          udp: int = 1232) -> dict:
    flags = [f"--server {server}", f"--udp {udp}"]
    if not dnssec:
        flags.append("--no-dnssec")
    if not recurse:
        flags.append("--norecurse --no-ad")
    if mtl_full:
        flags.append("--mtl-full")
    out = client_exec(
        h, f"python3 -m ecolab probe {qname} {qtype} {' '.join(flags)}", timeout=30
    ).strip()
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError(f"no JSON from probe:\n{out[-2000:]}")


DIG_PLAIN_FLAGS = frozenset({"-4", "-6", "-x"})
DIG_VALUE_FLAGS = frozenset({"-t", "-q", "-c", "-p"})   # the next argument is the value
DIG_MAX_ARGS = 40


def dig_argv(line: str) -> list[str]:
    """Parse a user-typed dig command into dig's arguments; defaults the server to Unbound."""
    parts = shlex.split(line)
    if parts and parts[0] == "dig":
        parts = parts[1:]
    if not parts:
        raise ValueError("Enter a dig command, e.g. dig host000000.zone00.test A +dnssec")
    if len(parts) > DIG_MAX_ARGS:
        raise ValueError(f"Too many dig arguments (max {DIG_MAX_ARGS}).")
    # dig bundles single-letter flags (-4f reads a file), so a deny-list on "-f"/"-k" is not enough:
    # only an explicit allow-list of dash options is accepted.
    i = 0
    while i < len(parts):
        p = parts[i]
        if p in DIG_VALUE_FLAGS:
            i += 1
            if i >= len(parts) or parts[i].startswith("-"):
                raise ValueError(f"dig {p} needs a value, e.g. {p} A")
        elif p.startswith("-") and p not in DIG_PLAIN_FLAGS:
            raise ValueError(f"dig option {p!r} is not allowed here "
                             f"(allowed: {', '.join(sorted(DIG_PLAIN_FLAGS | DIG_VALUE_FLAGS))}, and +options)")
        i += 1
    if not any(p.startswith("@") for p in parts):
        parts.insert(0, f"@{UNBOUND_IP}")
    return parts


def dig(h: LabHandle, line: str, timeout: int = 30) -> dict:
    """Run dig inside the client container (no shell); returns the command, exit code and output."""
    argv = dig_argv(line)
    require_no_suite()
    cp = _run(compose_cmd(h, "exec", "-T", "client", "dig", *argv), timeout=timeout, check=False)
    out = (cp.stdout + cp.stderr).rstrip()
    if cp.returncode in (126, 127) and "executable file not found" in out:
        out = ("dig is not installed in the client image. Rebuild the tools image "
               "(bind-utils is in ecosystem/docker/Dockerfile) and recreate the client.")
    server = next(p[1:] for p in argv if p.startswith("@"))
    return {"command": "dig " + shlex.join(argv), "server": server, "exit": cp.returncode, "output": out}


def dig_presets(h: LabHandle) -> list[str]:
    tld = ((h.cfg().get("hierarchy") or {}).get("tlds") or ["test"])[0]
    name = f"host000000.zone00.{tld}"
    return [
        f"dig {name} A +dnssec",
        f"dig {name} A +dnssec +ednsopt=65050",
        f"dig {tld} DNSKEY +dnssec",
        "dig . DNSKEY +dnssec",
        f"dig @{sld_server_ip(h)} {name} A +dnssec +norec",
        f"dig nx4e50797e7b57.zone00.{tld} A +dnssec",
    ]


def capture_target(server: str) -> tuple[str, str]:
    """Return (tcpdump_host, path_inside_that_container) for a probe server IP."""
    # Through Unbound: sniffer on unbound sees client + root/TLD/SLD.
    # Direct auth: sniffer on client sees stub ↔ root/SLD (unbound is idle).
    if server == "10.53.0.53":
        return "unbound", "/results"
    return "client", "/eco/results"


def start_capture(h: LabHandle, name: str | None = None, *, server: str = "10.53.0.53",
                  label: str = "") -> Path:
    if not name:
        tag = sanitize_label(label)
        name = time.strftime(f"live-{tag}-%Y%m%d-%H%M%S.pcap" if tag else "live-%Y%m%d-%H%M%S.pcap")
    path = h.ensure_capture_dir() / name
    host, results_root = capture_target(server)
    dest = f"{results_root}/{h.tag}/live-dashboard/{name}"
    client_exec(
        h,
        "python3 -c \"from ecolab import control; "
        f"control.start_capture({dest!r}, host={host!r}); print('capturing on', {host!r})\"",
        timeout=30,
    )
    (h.ensure_capture_dir() / ".current").write_text(
        json.dumps({"path": str(path), "host": host}), encoding="utf-8",
    )
    return path


def stop_capture(h: LabHandle) -> tuple[Path | None, int]:
    cur = h.capture_dir / ".current"
    host = "unbound"
    path: Path | None = None
    if cur.exists():
        raw = cur.read_text(encoding="utf-8").strip()
        try:
            meta = json.loads(raw)
            path = Path(meta["path"])
            host = meta.get("host") or "unbound"
        except json.JSONDecodeError:
            path = Path(raw)
    out = client_exec(
        h,
        "python3 -c \"from ecolab import control; "
        f"print(control.stop_capture(host={host!r}))\"",
        timeout=30,
    )
    dropped = 0
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.isdigit():
            dropped = int(line)
            break
    if cur.exists():
        cur.unlink()
    return path, dropped


def unbound_stats(h: LabHandle) -> dict[str, float]:
    """Read (and reset) Unbound counters via ecolab.control.unbound_stats."""
    out = client_exec(
        h,
        'python3 -c "import json; from ecolab import control; print(json.dumps(control.unbound_stats()))"',
        timeout=30,
    )
    for line in reversed(out.splitlines()):
        if line.strip().startswith("{"):
            return json.loads(line)
    return {}


def unbound_stats_delta(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    """Numeric delta for known keys; averages (recursion.time.avg) take the after value."""
    delta: dict[str, float] = {}
    keys = set(STAT_KEYS) | set(before) | set(after)
    for k in keys:
        a, b = after.get(k), before.get(k)
        if a is None and b is None:
            continue
        a = 0.0 if a is None else float(a)
        b = 0.0 if b is None else float(b)
        if "time.avg" in k or k.startswith("mem."):
            delta[k] = a
        else:
            delta[k] = a - b
    return {k: delta[k] for k in STAT_KEYS if k in delta}


def _read_manifest(path: Path) -> dict:
    """Load a Prepare manifest, with every zone's ``rrsig_count`` corrected to the real RRSIG count.

    Before the fix in ``ecolab.sign._rrsig_count`` the manifest also counted NSEC3 records (their type
    bitmap lists RRSIG), about 1.5x too many. Older manifests stay on disk untouched; here the count is
    taken again from the signed file (cached by mtime) and the stored figure is kept as
    ``rrsig_count_manifest``. Manifests written by the fixed signer already match and are left as they are.
    """
    from live_lab import prepare_story

    man = json.loads(path.read_text(encoding="utf-8"))
    for z in man.get("zones") or []:
        prof = prepare_story.rrsig_profile(path.parent / str(z.get("signed_file") or ""))
        if prof is not None and prof["total"] != z.get("rrsig_count"):
            z["rrsig_count_manifest"] = z.get("rrsig_count")
            z["rrsig_count"] = prof["total"]
    return man


def load_manifest(h: LabHandle, scenario: str) -> dict | None:
    path = h.work / "zones" / scenario / "manifest.json"
    if not path.is_file():
        return None
    return _read_manifest(path)


def list_manifests(h: LabHandle, tag: str | None = None) -> dict[str, dict]:
    zones = (ECO / "work" / tag if tag else h.work) / "zones"
    if not zones.is_dir():
        return {}
    out: dict[str, dict] = {}
    for d in sorted(zones.iterdir()):
        m = d / "manifest.json"
        if d.is_dir() and m.is_file():
            out[d.name] = _read_manifest(m)
    return out


def manifest_rows(h: LabHandle, tag: str | None = None) -> list[dict]:
    """Flat rows for Metrics tables: one per zone per scenario."""
    rows = []
    for scenario, man in list_manifests(h, tag).items():
        for z in man.get("zones") or []:
            rows.append({
                "scenario": scenario,
                "origin": z.get("origin", ""),
                "tier": z.get("tier", ""),
                "algorithm": z.get("algorithm", ""),
                "sign_seconds": z.get("sign_seconds"),
                "verify_seconds": z.get("verify_seconds"),
                "signed_bytes": z.get("signed_bytes"),
                "rrsig_count": z.get("rrsig_count"),
                "n_names": z.get("n_names"),
            })
    return rows


def query_presets(h: LabHandle) -> list[tuple[str, str, str]]:
    """Presets aligned with the selected lab config (smoke vs lab)."""
    cfg = h.cfg()
    tlds = list((cfg.get("hierarchy") or {}).get("tlds") or ["test"])
    tld = tlds[0]
    presets = [
        (".", "DNSKEY", "Trust anchor / root keys"),
        (f"{tld}.", "DNSKEY", f"TLD keys ({tld})"),
        (f"host000000.zone00.{tld}.", "A", "Data name through full chain"),
        (f"nx4e50797e7b57.zone00.{tld}.", "A", "NXDOMAIN proof"),
    ]
    return presets


def sld_server_ip(h: LabHandle) -> str:
    """Primary SLD NSD IP used by Direct-to-SLD path (sld1 → 10.53.0.21)."""
    return "10.53.0.21"


def container_logs(h: LabHandle, service: str = "unbound", tail: int = 80) -> str:
    cp = _run(compose_cmd(h, "logs", "--no-color", "--tail", str(tail), service), check=False, timeout=30)
    return (cp.stdout or "") + (cp.stderr or "")


def session_path(h: LabHandle) -> Path:
    return h.capture_dir / "session.json"


def _empty_session(h: LabHandle, *, show_prepare_metrics: bool = False) -> dict:
    """Fresh session: Metrics stay empty until Prepare (or an explicit clear→Prepare)."""
    return {
        "started": datetime.now(timezone.utc).isoformat(),
        "queries": [],
        "prepare_wall_s": None,
        "cold_warm": None,
        "show_prepare_metrics": show_prepare_metrics,
        "activity_file": str(h.capture_dir / "activity.log"),
    }


def load_session(h: LabHandle) -> dict:
    p = session_path(h)
    if not p.is_file():
        return _empty_session(h)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _empty_session(h)
    data.setdefault("queries", [])
    data.setdefault("prepare_wall_s", None)
    data.setdefault("cold_warm", None)
    # Missing key → hidden (same as fresh session after results wipe)
    data.setdefault("show_prepare_metrics", False)
    return data


def save_session(h: LabHandle, session: dict) -> Path:
    h.ensure_capture_dir()
    p = session_path(h)
    p.write_text(json.dumps(session, indent=2, default=str), encoding="utf-8")
    return p


def append_session_query(h: LabHandle, row: dict) -> None:
    sess = load_session(h)
    sess.setdefault("queries", []).append({**row, "ts": datetime.now(timezone.utc).isoformat()})
    save_session(h, sess)


def append_activity_file(h: LabHandle, line: str) -> None:
    path = h.ensure_capture_dir() / "activity.log"
    with path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def chunk_log(text: str, max_chunk: int = 1200) -> list[str]:
    """Split long command output into readable chunks for the activity feed."""
    text = (text or "").strip()
    if not text:
        return []
    lines = text.splitlines()
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for ln in lines:
        if size + len(ln) + 1 > max_chunk and buf:
            chunks.append("\n".join(buf))
            buf, size = [], 0
        buf.append(ln)
        size += len(ln) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def clear_demo_data(
    h: LabHandle,
    *,
    session: bool = True,
    activity: bool = True,
    pcaps: bool = True,
    hide_prepare_metrics: bool = True,
    work: bool = False,
    benchmarks: bool = False,
    suites: bool = False,
) -> dict[str, int]:
    """Wipe live-dashboard artifacts; optionally wipe signed work/ and saved benchmark runs.

    When ``work`` is True, removes ``ecosystem/work/<tag>/`` so Prepare must run again.
    When ``benchmarks`` is True, removes every saved run folder under ``results/<tag>/``
    (the live-dashboard folder is handled by the other scopes).
    Does not remove Docker images.
    """
    if work or benchmarks or suites:
        require_no_suite()
    if work:
        require_not_signing(h)
    d = h.capture_dir
    d.mkdir(parents=True, exist_ok=True)
    removed = {"session": 0, "activity": 0, "pcaps": 0, "work": 0, "benchmarks": 0, "suites": 0}
    if suites:
        removed["suites"] = clear_suites()
    if benchmarks:
        for run_dir in list_run_dirs(h):
            remove_tree(run_dir, h.stack)
            removed["benchmarks"] += 1
    if session:
        p = session_path(h)
        if p.is_file():
            p.unlink()
            removed["session"] = 1
        save_session(h, _empty_session(h, show_prepare_metrics=not hide_prepare_metrics))
    if activity:
        ap = d / "activity.log"
        if ap.is_file():
            ap.unlink()
            removed["activity"] = 1
        (d / "activity.log").write_text("", encoding="utf-8")
    if pcaps:
        for pcap in d.glob("*.pcap"):
            try:
                pcap.unlink()
                removed["pcaps"] += 1
            except OSError:
                pass
        cur = d / ".current"
        if cur.is_file():
            cur.unlink(missing_ok=True)
    if work:
        # Stop this lab's compose if present, then delete work/<tag>
        try:
            if h.compose.is_file():
                _run(compose_cmd(h, "down"), timeout=120, check=False)
        except Exception:  # noqa: BLE001
            pass
        if h.work.is_dir():
            if not remove_tree(h.work, h.stack):
                raise RuntimeError(f"could not delete {h.work}; remove it with: sudo rm -rf {h.work}")
            removed["work"] = 1
        remove_tree(jobs_dir() / h.tag, h.stack)
    return removed


def _suite_dirs() -> list[Path]:
    """Everything the Benchmark tile creates: suite records, per-size run folders, per-size signed zones."""
    out: list[Path] = []
    suites = ECO / "results" / "suites"
    if suites.is_dir():
        out += [p for p in suites.iterdir() if p.is_dir()]
    out += [p for p in (ECO / "results").glob("eco-bench-*") if p.is_dir()]
    out += [p for p in (ECO / "work").glob("eco-bench-*") if p.is_dir()]
    return out


def suite_footprint() -> dict:
    dirs = _suite_dirs()
    return {"suites": len([p for p in dirs if p.parent.name == "suites"]),
            "bytes": sum(_dir_size(p) for p in dirs)}


def clear_suites() -> int:
    """Delete benchmark suites, their per-size runs and signed zones; returns the number of suites removed."""
    require_no_suite()
    for compose in (ECO / "work").glob("eco-bench-*/compose.yml"):
        _run(["docker", "compose", "-f", str(compose), "down"], check=False, timeout=120)
    n = suite_footprint()["suites"]
    for p in _suite_dirs():
        remove_tree(p)
    for cfgf in (ECO / "configs").glob("_bench-*.yaml"):
        cfgf.unlink(missing_ok=True)
    return n


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def reset_preview(h: LabHandle) -> dict:
    """What each Reset scope would remove right now (counts and bytes), without touching anything."""
    d = h.capture_dir
    pcaps = list(d.glob("*.pcap"))
    session = session_path(h)
    session_queries_n = len(load_session(h).get("queries") or []) if session.is_file() else 0
    activity_log = d / "activity.log"
    runs = list_run_dirs(h)
    sf = suite_footprint()
    return {
        "suites": sf["suites"],
        "suite_bytes": sf["bytes"],
        "session_queries": session_queries_n,
        "activity_bytes": activity_log.stat().st_size if activity_log.is_file() else 0,
        "pcaps": len(pcaps),
        "pcap_bytes": sum(p.stat().st_size for p in pcaps if p.is_file()),
        "runs": len(runs),
        "run_bytes": sum(_dir_size(r) for r in runs),
        "work_exists": h.work.is_dir(),
        "work_bytes": _dir_size(h.work) if h.work.is_dir() else 0,
    }


def session_queries(h: LabHandle, ui_log: list | None) -> list:
    """Merge UI log with on-disk session so a browser refresh does not wipe Queries."""
    disk = list(load_session(h).get("queries") or [])
    if ui_log is None:
        return disk
    if not ui_log:
        return disk
    # Prefer the longer/fresher view: UI may have rows not yet flushed, disk survives refresh.
    if len(disk) > len(ui_log):
        return disk
    return list(ui_log)


# ── UI prefs (survive Streamlit browser refresh; session_state does not) ──

UI_PREFS_PATH = ECO / "results" / ".dashboard_prefs.json"


def load_ui_prefs() -> dict:
    if not UI_PREFS_PATH.is_file():
        return {}
    try:
        data = json.loads(UI_PREFS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_ui_prefs(prefs: dict) -> None:
    UI_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    cur = load_ui_prefs()
    cur.update({k: v for k, v in prefs.items() if v is not None})
    UI_PREFS_PATH.write_text(json.dumps(cur, indent=2), encoding="utf-8")


def split_lab_tag(tag: str) -> tuple[str, str] | None:
    """eco-ui-small-latest → (eco-ui-small, latest)."""
    stacks = list_stacks()
    for s in sorted(stacks, key=len, reverse=True):
        suf = f"-{s}"
        if tag.endswith(suf) and len(tag) > len(suf):
            return tag[: -len(suf)], s
    return None


def zone_size_for_lab_name(lab: str) -> str | None:
    for key, preset in ZONE_SIZE_PRESETS.items():
        if preset.get("name") == lab:
            return key
    if lab.startswith("eco-ui-custom-"):
        return "custom"
    return None


def live_capture_inventory() -> list[dict]:
    """All live-dashboard folders that still have .pcap files (newest first)."""
    root = ECO / "results"
    rows: list[dict] = []
    if not root.is_dir():
        return rows
    for d in root.glob("*/live-dashboard"):
        pcaps = sorted(d.glob("*.pcap"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not pcaps:
            continue
        tag = d.parent.name
        parts = split_lab_tag(tag)
        newest = pcaps[0].stat().st_mtime
        rows.append({
            "tag": tag,
            "lab": parts[0] if parts else tag,
            "stack": parts[1] if parts else "",
            "zone_size": zone_size_for_lab_name(parts[0]) if parts else None,
            "count": len(pcaps),
            "newest": newest,
            "dir": str(d),
            "names": [p.name for p in pcaps[:12]],
        })
    rows.sort(key=lambda r: r["newest"], reverse=True)
    return rows


def infer_ui_from_disk() -> dict:
    """Best-effort stack/zone after a hard refresh: prefs, else newest live captures."""
    prefs = load_ui_prefs()
    out: dict = {}
    if prefs.get("stack") in list_stacks():
        out["stack"] = prefs["stack"]
    if prefs.get("zone_size") in ZONE_SIZE_PRESETS or prefs.get("zone_size") == "custom":
        out["zone_size"] = prefs["zone_size"]
    if prefs.get("scenarios"):
        out["scenarios"] = list(prefs["scenarios"])

    inv = live_capture_inventory()
    if not inv:
        return out

    pref_tag = None
    z = out.get("zone_size")
    if z in ZONE_SIZE_PRESETS and out.get("stack"):
        pref_tag = f"{ZONE_SIZE_PRESETS[z]['name']}-{out['stack']}"
    pref_has_pcaps = pref_tag is not None and any(r["tag"] == pref_tag for r in inv)

    # Keep saved lab when it still has captures. Otherwise (fresh browser / empty lab) follow newest.
    if pref_has_pcaps:
        return out
    top = inv[0]
    if top.get("stack"):
        out["stack"] = top["stack"]
    if top.get("zone_size"):
        out["zone_size"] = top["zone_size"]
    return out


def list_pcaps(h: LabHandle) -> list[Path]:
    d = h.capture_dir
    if not d.is_dir():
        return []
    return sorted(d.glob("*.pcap"), key=lambda p: p.stat().st_mtime, reverse=True)
