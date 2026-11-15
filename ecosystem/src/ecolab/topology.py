"""Compose file for the hierarchy and the per-scenario server configurations.

Containers (fixed names and addresses on one internal network, no Internet):
  eco-root, eco-tld-<tld>, eco-sld<k>  Verisign MTL-mode NSD (default) or PowerDNS Auth
  eco-unbound                          Verisign MTL-mode Unbound (validating)
  eco-client                           stub client + controller (tools image)
The work directory is mounted read-only at /lab in every server; the
controller rewrites /lab/active/* before restarting the servers for a scenario.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .config import CLIENT_IP, ROOT_IP, ROOT_NS, SUBNET, UNBOUND_IP, Lab

PROJECT = "cipherops-eco"


def container(service: str) -> str:
    return f"eco-{service}"


def auth_backend(stack: str) -> str:
    """``nsd`` (presentation default) or ``powerdns`` from ecosystem/stacks/<stack>.env."""
    path = Path(__file__).resolve().parents[2] / "stacks" / f"{stack}.env"
    if not path.is_file():
        return "nsd"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("AUTH_BACKEND="):
            return line.partition("=")[2].strip().lower() or "nsd"
    return "nsd"


def compose(lab: Lab, stack: str) -> dict:
    cpus = lab.cfg.get("cpusets", {})
    backend = auth_backend(stack)
    net = {"dnsnet": {"driver": "bridge", "internal": True, "ipam": {"config": [{"subnet": SUBNET}]}}}
    services: dict[str, dict] = {}
    for s in lab.servers.values():
        if backend == "powerdns":
            # Entrypoint (docker/pdns-entrypoint.sh) creates bind-dnssec-db + PRESIGNED.
            services[s.name] = {
                "image": f"cipherops-eco-pdns:{stack}", "container_name": container(s.name), "hostname": s.name,
                "networks": {"dnsnet": {"ipv4_address": s.ip}}, "volumes": ["./:/lab:ro,z"],
                "environment": {
                    "PDNS_CONFIG_DIR": f"/lab/active/pdns/{s.name}",
                    "PDNS_DNSSEC_DB": f"/tmp/pdns-bind-dnssec-{s.name}.sqlite3",
                },
                **({"cpuset": str(cpus["nsd"])} if cpus.get("nsd") else {}),
            }
        else:
            services[s.name] = {
                "image": f"cipherops-eco-nsd:{stack}", "container_name": container(s.name), "hostname": s.name,
                "networks": {"dnsnet": {"ipv4_address": s.ip}}, "volumes": ["./:/lab:ro,z"],
                "command": [f"exec nsd -d -c /lab/active/nsd/{s.name}.conf"],
                **({"cpuset": str(cpus["nsd"])} if cpus.get("nsd") else {}),
            }
    services["unbound"] = {
        "image": f"cipherops-eco-unbound:{stack}", "container_name": container("unbound"), "hostname": "unbound",
        "networks": {"dnsnet": {"ipv4_address": UNBOUND_IP}}, "cap_add": ["NET_ADMIN"],
        "volumes": ["./:/lab:ro,z", "../../results:/results:z"],
        # Unbound is restarted in place for every cold pass (see control.flush_resolver), so the
        # container, its tc shaping and its network namespace outlive the process.
        "command": ["trap 'kill $$p 2>/dev/null; exit 0' TERM INT; "
                    "while :; do unbound -d -c /lab/active/unbound.conf & p=$$!; wait $$p; sleep 0.2; done"],
        "depends_on": list(lab.servers),
        **({"cpuset": str(cpus["unbound"])} if cpus.get("unbound") else {}),
    }
    services["client"] = {
        "image": f"cipherops-eco-tools:{stack}", "container_name": container("client"), "hostname": "client",
        "networks": {"dnsnet": {"ipv4_address": CLIENT_IP}},
        "volumes": ["../../:/eco:z", "../../../src:/work/src:ro,z", "/var/run/docker.sock:/var/run/docker.sock"],
        # SELinux denies container_t access to docker.sock; the client drives the lab via the Docker API.
        "security_opt": ["label=disable"],
        "environment": {
            "ECO_LAB": lab.name,
            "ECO_STACK": stack,
            "CIPHEROPS_GIT_SHA": "${CIPHEROPS_GIT_SHA:-unknown}",
        },
        "command": ["exec sleep infinity"],
        "depends_on": ["unbound"],
        **({"cpuset": str(cpus["client"])} if cpus.get("client") else {}),
    }
    return {"name": f"{PROJECT}-{stack}", "networks": net, "services": services}


def nsd_conf(lab: Lab, server: str, scenario: str, manifest: dict) -> str:
    files = {z["origin"]: z["signed_file"] for z in manifest["zones"]}
    lines = [
        "server:",
        "    ip-address: 0.0.0.0",
        "    port: 53",
        "    do-ip6: no",
        "    server-count: 1",
        '    username: ""',
        f'    zonesdir: "/lab/zones/{scenario}"',
        '    pidfile: "/tmp/nsd.pid"',
        '    xfrdfile: "/tmp/xfrd.state"',
        '    zonelistfile: "/tmp/zone.list"',
        "    verbosity: 1",
        "    ipv4-edns-size: 4096",       # the resolver's edns-buffer-size decides
        "    hide-version: yes",
        "    refuse-any: yes",
        "remote-control:",
        "    control-enable: no",
    ]
    for origin in lab.servers[server].zones:
        lines += ["zone:", f'    name: "{origin}"', f'    zonefile: "{files[origin]}"']
    return "\n".join(lines) + "\n"


def unbound_conf(lab: Lab, manifest: dict, udp_limit: int) -> str:
    r = lab.cfg["resolver"]
    cache = int(r.get("cache_mb", 64))
    lines = [
        "server:",
        "    interface: 0.0.0.0",
        "    port: 53",
        "    do-ip6: no",
        f"    access-control: {SUBNET} allow",
        '    username: ""',
        '    chroot: ""',
        '    directory: "/lab/active"',
        '    pidfile: "/tmp/unbound.pid"',
        '    logfile: ""',
        "    use-syslog: no",
        "    verbosity: 1",
        "    val-log-level: 2",
        "    log-servfail: yes",
        "    num-threads: 1",
        '    root-hints: "/lab/active/root.hints"',
        '    module-config: "validator iterator"',
        f"    edns-buffer-size: {udp_limit}",
        f"    qname-minimisation: {'yes' if r.get('qname_minimisation', True) else 'no'}",
        "    prefetch: no",
        "    prefetch-key: no",
        "    serve-expired: no",
        "    aggressive-nsec: no",
        f"    msg-cache-size: {cache}m",
        f"    rrset-cache-size: {cache}m",
        f"    key-cache-size: {max(4, cache // 4)}m",
        f"    ladder-cache-size: {int(r.get('ladder_cache_mb', 4))}m",
        "    extended-statistics: yes",
        "    statistics-cumulative: no",
        "    statistics-interval: 0",
    ]
    lines += [f'    trust-anchor: "{ds}"' for ds in manifest["trust_anchor"]]
    # Unbound answers RFC 6761/6762 names (test., local., ...) locally unless told not to.
    lines += [f'    local-zone: "{z.origin}" nodefault' for z in lab.zones if z.tier == "tld"]
    lines += [
        "remote-control:",
        "    control-enable: yes",
        "    control-interface: 127.0.0.1",
        "    control-use-cert: no",
    ]
    return "\n".join(lines) + "\n"


def root_hints() -> str:
    return f". 3600000 IN NS {ROOT_NS}\n{ROOT_NS} 3600000 IN A {ROOT_IP}\n"


def pdns_conf(lab: Lab, server: str, scenario: str, manifest: dict) -> tuple[str, str]:
    """PowerDNS ``pdns.conf`` + BIND-backend zone list for one auth node."""
    files = {z["origin"]: z["signed_file"] for z in manifest["zones"]}
    bind_lines = []
    for origin in lab.servers[server].zones:
        zonefile = f"/lab/zones/{scenario}/{files[origin]}"
        bind_lines += [
            f'zone "{origin}" IN {{',
            "  type master;",
            f'  file "{zonefile}";',
            "};",
            "",
        ]
    # bind-config must be absolute; PowerDNS does not resolve it relative to --config-dir.
    server_conf = "\n".join([
        "daemon=no",
        "guardian=no",
        "write-pid=no",
        "local-address=0.0.0.0",
        "local-port=53",
        "launch=bind",
        f"bind-config=/lab/active/pdns/{server}/named.conf",
        # Empty SQLite DNSSEC DB: required for BIND backend to treat zones as DNSSEC-aware
        # and serve RRSIGs already present in the pre-signed zone files.
        f"bind-dnssec-db=/tmp/pdns-bind-dnssec-{server}.sqlite3",
        "loglevel=4",
        "receiver-threads=1",
        "distributor-threads=1",
        "security-poll-suffix=",
        "",
    ])
    return server_conf, "\n".join(bind_lines)


def write_active(lab: Lab, scenario: str, manifest: dict, udp_limit: int) -> None:
    active = lab.work / "active"
    backend = auth_backend(lab.stack)
    if backend == "powerdns":
        (active / "pdns").mkdir(parents=True, exist_ok=True)
        for name in lab.servers:
            conf, bind = pdns_conf(lab, name, scenario, manifest)
            d = active / "pdns" / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "pdns.conf").write_text(conf)
            (d / "named.conf").write_text(bind)
    else:
        (active / "nsd").mkdir(parents=True, exist_ok=True)
        for name in lab.servers:
            (active / "nsd" / f"{name}.conf").write_text(nsd_conf(lab, name, scenario, manifest))
    (active / "unbound.conf").write_text(unbound_conf(lab, manifest, udp_limit))
    (active / "root.hints").write_text(root_hints())
    (active / "scenario.txt").write_text(f"{scenario} {udp_limit}\n")


def write_compose(lab: Lab, stack: str) -> Path:
    path = lab.work / "compose.yml"
    path.write_text(yaml.safe_dump(compose(lab, stack), sort_keys=False))
    return path
