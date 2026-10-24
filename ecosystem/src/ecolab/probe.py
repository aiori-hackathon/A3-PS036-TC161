"""Single live lookup for the panel dashboard (runs inside eco-client).

Supports querying Unbound (validated, AD) or an authoritative IP (AA),
optional DO bit, and the MTL full-signature EDNS option 65050.
"""

from __future__ import annotations

import json
import socket
import struct
import time

import dns.edns
import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype

from .config import UNBOUND_IP

MTL_FULL = 65050


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("TCP connection closed early")
        buf += chunk
    return buf


def _rrsig_summary(msg: dns.message.Message) -> list[dict]:
    out = []
    for sec_name, section in (("answer", msg.answer), ("authority", msg.authority)):
        for rrset in section:
            if rrset.rdtype != dns.rdatatype.RRSIG:
                continue
            for rd in rrset:
                kind = ""
                if rd.algorithm == 130 and rd.signature:
                    kind = {0: "condensed", 1: "FULL"}.get(rd.signature[0], "unknown")
                out.append({
                    "section": sec_name,
                    "covers": dns.rdatatype.to_text(rd.type_covered),
                    "algorithm": int(rd.algorithm),
                    "key_tag": int(rd.key_tag),
                    "sig_bytes": len(rd.signature),
                    "mtl_kind": kind,
                })
    return out


def lookup(qname: str, qtype: str = "A", server: str = UNBOUND_IP, *,
           dnssec_ok: bool = True, want_ad: bool = True, recurse: bool = True,
           mtl_full: bool = False, udp_payload: int = 1232, timeout: float = 8.0) -> dict:
    opts = [dns.edns.GenericOption(MTL_FULL, b"")] if mtl_full else []
    q = dns.message.make_query(qname, dns.rdatatype.from_text(qtype), use_edns=True,
                               payload=udp_payload, want_dnssec=dnssec_ok, options=opts or None)
    if want_ad:
        q.flags |= dns.flags.AD
    if not recurse:
        q.flags &= ~dns.flags.RD
    wire = q.to_wire()
    row = {
        "qname": qname, "qtype": qtype, "server": server, "mtl_full": mtl_full,
        "transport": "udp", "query_bytes": len(wire), "response_bytes": 0,
        "tcp_query_bytes": 0, "tcp_response_bytes": 0, "tc": False, "timeout": False,
        "rcode": "", "ad": False, "aa": False, "answers": 0, "authority": 0,
        "flags": "", "rrsigs": [], "latency_ms": 0.0, "error": "",
    }
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
            row["tcp_query_bytes"] = len(wire) + 2
            row["tcp_response_bytes"] = len(data) + 2
            r = dns.message.from_wire(data)
        row["rcode"] = dns.rcode.to_text(r.rcode())
        row["ad"] = bool(r.flags & dns.flags.AD)
        row["aa"] = bool(r.flags & dns.flags.AA)
        row["flags"] = dns.flags.to_text(r.flags)
        row["answers"] = sum(len(rr) for rr in r.answer)
        row["authority"] = sum(len(rr) for rr in r.authority)
        row["rrsigs"] = _rrsig_summary(r)
    except (socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        row["timeout"] = True
        row["rcode"] = "TIMEOUT"
        row["error"] = str(e)
    row["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    row["dns_bytes"] = (row["query_bytes"] + row["response_bytes"]
                        + row["tcp_query_bytes"] + row["tcp_response_bytes"])
    return row


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="ecolab-probe")
    ap.add_argument("qname")
    ap.add_argument("qtype", nargs="?", default="A")
    ap.add_argument("--server", default=UNBOUND_IP)
    ap.add_argument("--no-dnssec", action="store_true")
    ap.add_argument("--no-ad", action="store_true")
    ap.add_argument("--norecurse", action="store_true")
    ap.add_argument("--mtl-full", action="store_true")
    ap.add_argument("--udp", type=int, default=1232)
    a = ap.parse_args(argv)
    print(json.dumps(lookup(a.qname, a.qtype, a.server, dnssec_ok=not a.no_dnssec,
                            want_ad=not a.no_ad, recurse=not a.norecurse,
                            mtl_full=a.mtl_full, udp_payload=a.udp)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
