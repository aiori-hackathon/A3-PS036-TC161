"""Seeded multi-zone workload and a plain stub client.

The workload picks a zone by Zipf popularity (zone order shuffled with the
seed, so popularity is independent of zone size), then a name inside it with
the same Zipf weighting as the main benchmark's query_workload. NXDOMAIN names
come from cipherops.dnswire.nx_names and are all distinct.

The stub behaves like an ordinary client of a validating resolver: RD and AD
set, EDNS 1232, DO only if configured. It retries over TCP on TC=1. Bytes are
the exact UDP/TCP DNS payload sizes it sent and received.
"""

from __future__ import annotations

import random
import socket
import struct
import time

import dns.flags
import dns.message
import dns.name
import dns.rcode
import dns.rdatatype

from cipherops.dnswire import nx_names

from .config import UNBOUND_IP, Lab
from .zones import DATA_CLASSES, content


def workload(lab: Lab) -> list[dict]:
    w = lab.cfg["workload"]
    seed = int(lab.cfg["seed"])
    rng = random.Random(seed ^ 0xEC0)
    slds = [z for z in lab.zones if z.tier == "sld"]
    order = slds[:]
    rng.shuffle(order)
    zone_w = [1.0 / (r + 1) ** float(w["zone_zipf_s"]) for r in range(len(order))]
    names, name_w, nx = {}, {}, {}
    for i, z in enumerate(order):
        zc = content(z)
        names[z.origin] = [it for it in zc.items if it.record_class in DATA_CLASSES]
        name_w[z.origin] = [1.0 / (r + 1) ** float(w["name_zipf_s"]) for r in range(len(names[z.origin]))]
        nx[z.origin] = iter(nx_names(zc, int(w["n_queries"]), seed + 101 * (i + 1)))
    out = []
    for k in range(int(w["n_queries"])):
        z = rng.choices(order, zone_w)[0]
        if rng.random() < float(w["nx_fraction"]):
            out.append({"seq": k, "zone": z.origin, "kind": "nxdomain", "qname": next(nx[z.origin]).to_text(),
                        "qtype": "A", "record_class": "nx"})
        else:
            it = rng.choices(names[z.origin], name_w[z.origin])[0]
            out.append({"seq": k, "zone": z.origin, "kind": "positive", "qname": it.owner.to_text(),
                        "qtype": dns.rdatatype.to_text(it.rdtype), "record_class": it.record_class})
    return out


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("TCP connection closed early")
        buf += chunk
    return buf


def lookup(qname: str, qtype: str, dnssec_ok: bool, timeout: float, server: str = UNBOUND_IP) -> dict:
    q = dns.message.make_query(qname, dns.rdatatype.from_text(qtype), use_edns=0, payload=1232,
                               want_dnssec=dnssec_ok)
    q.flags |= dns.flags.AD
    wire = q.to_wire()
    row = {"transport": "udp", "query_bytes": len(wire), "response_bytes": 0, "tcp_query_bytes": 0,
           "tcp_response_bytes": 0, "tc": False, "timeout": False, "rcode": "", "ad": False, "answers": 0}
    t0 = time.perf_counter()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(wire, (server, 53))
            while True:
                data, _ = s.recvfrom(65535)
                if data[:2] == wire[:2]:
                    break
        row["response_bytes"] = len(data)
        r = dns.message.from_wire(data, ignore_trailing=True)
        if r.flags & dns.flags.TC:
            row["tc"], row["transport"] = True, "udp+tcp"
            with socket.create_connection((server, 53), timeout=timeout) as s:
                s.sendall(struct.pack(">H", len(wire)) + wire)
                (ln,) = struct.unpack(">H", _recv_exact(s, 2))
                data = _recv_exact(s, ln)
            row["tcp_query_bytes"], row["tcp_response_bytes"] = len(wire) + 2, len(data) + 2
            r = dns.message.from_wire(data)
        row["rcode"] = dns.rcode.to_text(r.rcode())
        row["ad"] = bool(r.flags & dns.flags.AD)
        row["answers"] = sum(len(rr) for rr in r.answer)
    except (socket.timeout, TimeoutError, ConnectionError, OSError):
        row["timeout"], row["rcode"] = True, "TIMEOUT"
    row["latency_ms"] = (time.perf_counter() - t0) * 1000
    return row


def replay(lab: Lab, items: list[dict], log=None) -> list[dict]:
    w = lab.cfg["workload"]
    rows = []
    t_start = time.time()
    for i, it in enumerate(items):
        r = lookup(it["qname"], it["qtype"], bool(w.get("stub_dnssec_ok", False)), float(w["timeout_s"]))
        rows.append({**it, "t": time.time() - t_start, **r})
        if log and (i + 1) % max(1, len(items) // 10) == 0:
            log(f"      {i + 1}/{len(items)} queries")
    return rows
