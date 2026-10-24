"""Drive the lab containers from the client container through the Docker socket.

Network shaping and packet capture both happen on the resolver's interface
(eco-unbound, eth0): every packet between the stub and the resolver and
between the resolver and each authoritative server crosses it. A prio qdisc
sends packets for each tier to its own netem band, so each tier gets its own
added delay; loss applies to the resolver's outgoing packets.
"""

from __future__ import annotations

import time

import dns.message
import dns.query
import dns.rdatatype
import docker

from .config import CLIENT_IP, Lab
from .topology import container

_client = None


def _docker():
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def get(service: str):
    return _docker().containers.get(container(service))


def exec_(service: str, cmd: str, check: bool = True) -> str:
    code, out = get(service).exec_run(["/bin/bash", "-c", cmd])
    text = out.decode(errors="replace") if out else ""
    if check and code != 0:
        raise RuntimeError(f"[{service}] {cmd} -> exit {code}\n{text}")
    return text


def restart(services: list[str]) -> None:
    for s in services:
        get(s).restart(timeout=3)


def wait_dns(ip: str, qname: str = ".", qtype: str = "SOA", timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    q = dns.message.make_query(qname, dns.rdatatype.from_text(qtype))
    last = None
    while time.time() < deadline:
        try:
            dns.query.udp(q, ip, timeout=1.0)
            return
        except Exception as e:  # noqa: BLE001 - server still starting
            last = e
            time.sleep(0.3)
    raise RuntimeError(f"no DNS answer from {ip} within {timeout_s} s: {last}")


def wait_servers(lab: Lab) -> None:
    for s in lab.servers.values():
        wait_dns(s.ip, s.zones[0], "SOA")


def unbound_control(cmd: str) -> str:
    return exec_("unbound", f"unbound-control -c /lab/active/unbound.conf {cmd}")


def unbound_ready(timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if "is running" in exec_("unbound", "unbound-control -c /lab/active/unbound.conf status", check=False):
            return
        time.sleep(0.3)
    raise RuntimeError("unbound did not come up:\n" + get("unbound").logs(tail=40).decode(errors="replace"))


def unbound_stats() -> dict[str, float]:
    """Read and reset Unbound's counters (statistics-cumulative: no)."""
    out = {}
    for line in unbound_control("stats").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            try:
                out[k.strip()] = float(v)
            except ValueError:
                pass
    return out


def _unbound_pid() -> str | None:
    out = exec_("unbound", "unbound-control -c /lab/active/unbound.conf status", check=False)
    return next((l.split("pid")[1].strip(" )") for l in out.splitlines() if "is running" in l and "pid" in l), None)


def flush_resolver(timeout_s: float = 30.0) -> None:
    """Empty every Unbound cache by restarting the process. `reload` keeps the MTL ladder cache."""
    old = _unbound_pid()
    unbound_control("stop")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pid = _unbound_pid()
        if pid and pid != old:
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("unbound did not restart:\n" + get("unbound").logs(tail=40).decode(errors="replace"))
    unbound_stats()


def shape(lab: Lab, profile: dict) -> None:
    ips = lab.tier_ips()
    bands = [("client", [CLIENT_IP]), ("root", ips["root"]), ("tld", ips["tld"]), ("sld", ips["sld"])]
    loss = float(profile.get("loss_pct", 0))
    cmds = ["tc qdisc del dev eth0 root 2>/dev/null || true",
            "tc qdisc add dev eth0 root handle 1: prio bands 5 priomap " + " ".join(["0"] * 16)]
    for k, (tier, addrs) in enumerate(bands, start=2):
        delay = float(profile.get(f"{tier}_ms", 0))
        tier_loss = loss if tier != "client" else 0.0
        if delay <= 0 and tier_loss <= 0:
            continue
        netem = f"netem delay {delay}ms" + (f" loss {tier_loss}%" if tier_loss > 0 else "")
        cmds.append(f"tc qdisc add dev eth0 parent 1:{k} handle {k}0: {netem} limit 10000")
        for ip in addrs:
            cmds.append(f"tc filter add dev eth0 parent 1: protocol ip prio 1 u32 match ip dst {ip}/32 flowid 1:{k}")
    exec_("unbound", " && ".join(cmds))


def shaping_report() -> str:
    return exec_("unbound", "tc qdisc show dev eth0; tc filter show dev eth0", check=False)


def start_capture(path_in_container: str, host: str = "unbound") -> None:
    """Start port-53 tcpdump on ``host`` (``unbound`` or ``client``).

    Capture to container-local /tmp first (bind mounts drop packets on Docker Desktop),
    then ``stop_capture`` moves the file to ``path_in_container``.

    Use ``unbound`` for Through-Unbound queries (sees client + upstream hops).
    Use ``client`` for Direct-to-root / Direct-to-SLD (Unbound is idle on that path).
    """
    # Writing straight to the bind-mounted results folder is slow enough on Docker Desktop for the
    # kernel to drop packets, so capture to container-local /tmp and move the file on stop.
    exec_(host, f"mkdir -p $(dirname {path_in_container}) && echo {path_in_container} >/tmp/capture.dest && "
                f"echo {host} >/tmp/capture.host && "
                ": >/tmp/tcpdump.log && rm -f /tmp/capture.pcap && "
                "(nohup tcpdump -i eth0 -s 0 -B 32768 -U --immediate-mode -w /tmp/capture.pcap 'port 53' "
                ">/tmp/tcpdump.log 2>&1 &) && "
                "for i in $(seq 1 100); do grep -q 'listening on' /tmp/tcpdump.log && exit 0; sleep 0.1; done; "
                "cat /tmp/tcpdump.log; exit 1")
    time.sleep(0.2)


def stop_capture(host: str | None = None) -> int:
    """Stop tcpdump, move the capture into place and return the packets the kernel dropped."""
    if host is None:
        # Prefer unbound's marker (legacy callers); fall back to client.
        host = "unbound"
        marker = exec_("unbound", "cat /tmp/capture.host 2>/dev/null", check=False).strip()
        if marker in ("unbound", "client", "root"):
            host = marker
        else:
            marker = exec_("client", "cat /tmp/capture.host 2>/dev/null", check=False).strip()
            if marker:
                host = marker
    exec_(host, "pkill -INT tcpdump; for i in $(seq 1 50); do pgrep tcpdump >/dev/null || break; sleep 0.1; done; "
                "mv /tmp/capture.pcap \"$(cat /tmp/capture.dest)\"")
    log = exec_(host, "cat /tmp/tcpdump.log", check=False)
    return sum(int(l.split()[0]) for l in log.splitlines() if l.endswith("dropped by kernel"))
