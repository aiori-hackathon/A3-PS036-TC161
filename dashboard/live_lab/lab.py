"""Lab control: the handle, Docker Compose, client exec, probes, captures and Unbound counters.

``live_lab.driver`` re-exports these names at its end, so ``driver.<name>`` keeps working.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from live_lab import images
from live_lab.driver import (
    DASHBOARD_CONFIG, DEFAULT_CONFIG, DEFAULT_STACK, ECO, REPO, UNBOUND_IP, _run,
    require_no_build, require_no_offline, require_no_suite, require_not_signing,
)
from live_lab.shapes import DEFAULT_SEED, PROFILE_DEFAULTS, WORKLOAD_DEFAULTS, shape_for

NAMES = {
    "10.53.0.100": "client",
    "10.53.0.53": "unbound",
    "10.53.0.2": "root",
}

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


def compose_cmd(h: LabHandle, *args: str) -> list[str]:
    if not h.compose.exists():
        raise RuntimeError(f"missing {h.compose}; run Prepare first")
    return ["docker", "compose", "-f", str(h.compose), *args]


def status(h: LabHandle) -> dict:
    out = {"prepared": h.prepared(), "compose": str(h.compose), "active": h.active_scenario(),
           "containers": {}, "images": {}, "image_rows": []}
    # The offline image is always the `latest` stack's; the lab images follow the selected version.
    rows = images.status(images.OFFLINE_STACK, "offline") + images.status(h.stack, "simulated")
    out["image_rows"] = [asdict(r) for r in rows]
    out["images"] = {r.ref: r.state == "ok" for r in rows if r.role in images.MODES["simulated"]}
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
