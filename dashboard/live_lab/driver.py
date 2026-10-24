"""Host-side driver for the eco live lab (Docker Compose + client exec).

The lab network is internal, so the dashboard never talks DNS on the host:
every probe / flush / activate runs inside eco-client.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ECO = REPO / "ecosystem"
DEFAULT_CONFIG = "configs/_dashboard.yaml"
DEFAULT_STACK = "draft06"
MTL_FULL = 65050
DASHBOARD_CONFIG = "configs/_dashboard.yaml"

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
}

SCENARIO_ALGS: dict[str, dict[str, str]] = {
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
        d = ECO / "results" / self.tag / "live-dashboard"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def prepared(self) -> bool:
        return self.compose.is_file() and (self.work / "zones").is_dir()

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


def _run(cmd: list[str], timeout: int | None = 120, check: bool = True) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("CIPHEROPS_GIT_SHA", "live-dashboard")
    cp = subprocess.run(cmd, cwd=ECO, capture_output=True, text=True, timeout=timeout, env=env)
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


def build_images(h: LabHandle) -> str:
    """Build cipherops-eco-{nsd,unbound,tools}:<stack> (same as eco.ps1 build).

    First build can take a long time (OpenSSL / liboqs / MTL from source).
    """
    env_file = ECO / "stacks" / f"{h.stack}.env"
    compose = ECO / "docker" / "build.yml"
    if not env_file.is_file():
        raise RuntimeError(f"missing stack env {env_file}")
    if not compose.is_file():
        raise RuntimeError(f"missing {compose}")
    cp = _run(
        ["docker", "compose", "-f", str(compose), "--env-file", str(env_file), "build"],
        timeout=None,
    )
    return (cp.stdout or "") + (cp.stderr or "")


def pull_images(h: LabHandle, registry: str) -> str:
    """Pull pre-built eco images from a registry and retag to local compose names.

    Expects remote tags like ``{registry}/cipherops-eco-nsd:{stack}`` (and unbound, tools).
    """
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
    seed: int = 20261005,
) -> str:
    """Write ``configs/_dashboard.yaml`` from UI options; return relative config path."""
    import yaml

    if zone_size not in ZONE_SIZE_PRESETS:
        raise ValueError(f"unknown zone size {zone_size!r}")
    preset = ZONE_SIZE_PRESETS[zone_size]
    chosen = scenarios or ["ecdsa", "slh-dsa", "mtl"]
    bad = [s for s in chosen if s not in SCENARIO_ALGS]
    if bad:
        raise ValueError(f"unknown scenarios: {bad}")
    if not chosen:
        raise ValueError("select at least one scenario")
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
            "nx_fraction": 0.10,
            "zone_zipf_s": 1.0,
            "name_zipf_s": 1.1,
            "stub_dnssec_ok": False,
            "timeout_s": 5.0,
        },
        "network": {
            "run": list(profiles_run),
            "profiles": {
                "lan": {"client_ms": 0, "root_ms": 0, "tld_ms": 0, "sld_ms": 0, "loss_pct": 0},
                "wan": {"client_ms": 2, "root_ms": 20, "tld_ms": 35, "sld_ms": 50, "loss_pct": 0},
                "lossy": {"client_ms": 2, "root_ms": 20, "tld_ms": 35, "sld_ms": 50, "loss_pct": 1},
            },
        },
        "trials": int(trials),
        "cpusets": {"client": "3", "unbound": "2", "nsd": "4-5"},
    }
    path = ECO / DASHBOARD_CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return DASHBOARD_CONFIG


def prepare(h: LabHandle, jobs: int = 2, scenarios: list[str] | None = None) -> str:
    img = f"cipherops-eco-tools:{h.stack}"
    extra = f" --scenarios {','.join(scenarios)}" if scenarios else ""
    cp = _run([
        "docker", "run", "--rm", "--network", "none",
        "-v", f"{ECO}:/eco",
        "-v", f"{REPO / 'src'}:/work/src:ro",
        img,
        f"python3 -m ecolab prepare {h.config} --jobs {jobs}{extra}",
    ], timeout=None)
    return cp.stdout + cp.stderr


def run_benchmark(
    h: LabHandle,
    *,
    scenarios: list[str] | None = None,
    profiles: list[str] | None = None,
    trials: int | None = None,
    udp: int | None = None,
) -> str:
    """Full ecolab run inside the client (lab must be up). Can take a long time."""
    flags = []
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


def list_run_dirs(h: LabHandle) -> list[Path]:
    root = ECO / "results" / h.tag
    if not root.is_dir():
        return []
    dirs = [p for p in root.iterdir() if p.is_dir() and p.name != "live-dashboard"]
    return sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)


def up(h: LabHandle) -> str:
    cp = _run(compose_cmd(h, "up", "-d"), timeout=180)
    return cp.stdout + cp.stderr


def down(h: LabHandle) -> str:
    cp = _run(compose_cmd(h, "down"), timeout=120, check=False)
    return cp.stdout + cp.stderr


def client_exec(h: LabHandle, inner: str, timeout: int | None = 120) -> str:
    cp = _run(compose_cmd(h, "exec", "-T", "client", "bash", "-lc", inner), timeout=timeout)
    return cp.stdout


def activate(h: LabHandle, scenario: str, udp: int = 1232) -> str:
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


def capture_target(server: str) -> tuple[str, str]:
    """Return (tcpdump_host, path_inside_that_container) for a probe server IP."""
    # Through Unbound: sniffer on unbound sees client + root/TLD/SLD.
    # Direct auth: sniffer on client sees stub ↔ root/SLD (unbound is idle).
    if server == "10.53.0.53":
        return "unbound", "/results"
    return "client", "/eco/results"


def start_capture(h: LabHandle, name: str | None = None, *, server: str = "10.53.0.53") -> Path:
    name = name or time.strftime("live-%Y%m%d-%H%M%S.pcap")
    path = h.capture_dir / name
    host, results_root = capture_target(server)
    dest = f"{results_root}/{h.tag}/live-dashboard/{name}"
    client_exec(
        h,
        "python3 -c \"from ecolab import control; "
        f"control.start_capture({dest!r}, host={host!r}); print('capturing on', {host!r})\"",
        timeout=30,
    )
    (h.capture_dir / ".current").write_text(
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


def load_manifest(h: LabHandle, scenario: str) -> dict | None:
    path = h.work / "zones" / scenario / "manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_manifests(h: LabHandle) -> dict[str, dict]:
    zones = h.work / "zones"
    if not zones.is_dir():
        return {}
    out: dict[str, dict] = {}
    for d in sorted(zones.iterdir()):
        m = d / "manifest.json"
        if d.is_dir() and m.is_file():
            out[d.name] = json.loads(m.read_text(encoding="utf-8"))
    return out


def manifest_rows(h: LabHandle) -> list[dict]:
    """Flat rows for Metrics tables: one per zone per scenario."""
    rows = []
    for scenario, man in list_manifests(h).items():
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
    p = session_path(h)
    p.write_text(json.dumps(session, indent=2, default=str), encoding="utf-8")
    return p


def append_session_query(h: LabHandle, row: dict) -> None:
    sess = load_session(h)
    sess.setdefault("queries", []).append({**row, "ts": datetime.now(timezone.utc).isoformat()})
    save_session(h, sess)


def append_activity_file(h: LabHandle, line: str) -> None:
    path = h.capture_dir / "activity.log"
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
) -> dict[str, int]:
    """Wipe live-dashboard artifacts; optionally wipe signed work/ too.

    When ``work`` is True, removes ``ecosystem/work/<tag>/`` so Prepare must run again.
    Does not remove Docker images.
    """
    d = h.capture_dir
    d.mkdir(parents=True, exist_ok=True)
    removed = {"session": 0, "activity": 0, "pcaps": 0, "work": 0}
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
        tag_dir = h.work
        if tag_dir.is_dir():
            import shutil
            shutil.rmtree(tag_dir, ignore_errors=True)
            removed["work"] = 1
    return removed


def set_show_prepare_metrics(h: LabHandle, visible: bool) -> None:
    sess = load_session(h)
    sess["show_prepare_metrics"] = bool(visible)
    save_session(h, sess)


def session_queries(h: LabHandle, ui_log: list | None) -> list:
    """Prefer in-memory query_log when the key exists (even if empty)."""
    if ui_log is not None:
        return list(ui_log)
    return list(load_session(h).get("queries") or [])
