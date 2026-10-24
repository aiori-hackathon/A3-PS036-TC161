"""Transmitted bytes at the DNS message level.

Responses are real DNS messages encoded by dnspython (with name compression)
that carry the actual signature bytes produced by the harness. No resolver or
server is implemented: each response is built once and measured, then a
transport accounting model (UDP, TC=1, TCP retry) adds up a query workload.

Measured: query/response/truncated-response sizes (exact wire encoding).
Analytical: IP packet, fragment and TCP segment counts (header arithmetic).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import dns.dnssec
import dns.edns
import dns.flags
import dns.message
import dns.name
import dns.rcode
import dns.rdataclass
import dns.rdatatype
import dns.rrset

from . import analytical
from .codepoints import EDNS_MTL_MODE_FULL, MTL_TYPE_CONDENSED, MTL_TYPE_FULL, Mode
from .workload import TTL, Zone, rrsig_template

UDP_LIMITS = (1232, 4096) # 1232 is the default UDP limit for DNS and 4096 is the maximum UDP limit.


@dataclass
class Signatures:
    """Per-RRset signature bytes from one harness trial, indexed like Zone.rrsets()."""

    mode: Mode
    sigs: list[bytes]
    signed_ladder: bytes | None = None
    reused: bool = False  # True when sigs were cycled from a signing sample

    def rrsig_field(self, index: int, full: bool = False) -> bytes:
        sig = self.sigs[index % len(self.sigs)]
        if self.mode.family != "mtl":
            return sig
        if full:
            return bytes([MTL_TYPE_FULL]) + sig + self.signed_ladder
        return bytes([MTL_TYPE_CONDENSED]) + sig


class ResponseBuilder:
    def __init__(self, zone: Zone, sigs: Signatures):
        self.zone = zone
        self.sigs = sigs
        self.mode = sigs.mode
        self.key_tag = zone.key_tag(self.mode)
        self.index: dict[tuple[dns.name.Name, int], int] = {}
        self.rrsets: list[dns.rrset.RRset] = []
        for idx, (_, rrset) in enumerate(zone.rrsets(self.mode)):
            self.index[(rrset.name, rrset.rdtype)] = idx
            self.rrsets.append(rrset)
        self.sorted_hashes = zone.nsec3_sorted_hashes()

    def _signed(self, idx: int, full: bool) -> list[dns.rrset.RRset]:
        rrset = self.rrsets[idx]
        rrsig = rrsig_template(rrset, self.mode, self.key_tag, self.zone.origin,
                               self.sigs.rrsig_field(idx, full))
        return [rrset, dns.rrset.from_rdata(rrset.name, TTL, rrsig)]

    def query(self, qname: dns.name.Name, qtype: int, payload: int, mtl_full: bool) -> dns.message.QueryMessage:
        options = [dns.edns.GenericOption(EDNS_MTL_MODE_FULL, b"")] if mtl_full else []
        return dns.message.make_query(qname, qtype, use_edns=0, payload=payload,
                                      want_dnssec=True, options=options)

    def _nsec3_covering(self, name: dns.name.Name) -> int:
        h = dns.dnssec.nsec3_hash(name, None, 0, 1)
        lo, hi = 0, len(self.sorted_hashes)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.sorted_hashes[mid] <= h:
                lo = mid + 1
            else:
                hi = mid
        owner_hash = self.sorted_hashes[(lo - 1) % len(self.sorted_hashes)]
        nsec3 = self.zone.nsec3_by_hash[owner_hash]
        return self.index[(nsec3.name, dns.rdatatype.NSEC3)]

    def response(self, kind: str, qname: dns.name.Name, qtype: int, payload: int,
                 mtl_full: bool = False) -> tuple[dns.message.QueryMessage, dns.message.Message]:
        q = self.query(qname, qtype, payload, mtl_full)
        r = dns.message.make_response(q)
        full_budget = 1 if (mtl_full and self.mode.family == "mtl") else 0
        if kind in ("positive", "dnskey"):
            idx = self.index[(qname, qtype)]
            r.answer.extend(self._signed(idx, full_budget > 0))
        elif kind == "nxdomain":
            r.set_rcode(dns.rcode.NXDOMAIN)
            soa_idx = self.index[(self.zone.origin, dns.rdatatype.SOA)]
            proof: list[int] = []
            for name in (self.zone.origin, qname, dns.name.from_text("*", self.zone.origin)):
                idx = self._nsec3_covering(name)
                if idx not in proof:
                    proof.append(idx)
            for pos, idx in enumerate([soa_idx] + proof):
                r.authority.extend(self._signed(idx, full_budget > 0 and pos == 0))
        else:
            raise ValueError(kind)
        return q, r


def wire_len(msg: dns.message.Message) -> int:
    return len(msg.to_wire(max_size=65535))


def truncated_len(q: dns.message.QueryMessage) -> int:
    tc = dns.message.make_response(q)
    tc.flags |= dns.flags.TC
    return wire_len(tc)


def exchange(query_bytes: int, response_bytes: int, tc_bytes: int, udp_limit: int,
             force_tcp: bool = False) -> dict:
    """Transport outcome for one query/response pair."""
    if force_tcp:
        return {"transport": "tcp", "dns_bytes": 2 + query_bytes + 2 + response_bytes,
                "truncated": False, "round_trips_est": 2,
                "packets_est": 3 + 1 + analytical.tcp_segments(response_bytes + 2) + 4}
    if response_bytes <= udp_limit:
        frags = analytical.udp_fragments(response_bytes)
        return {"transport": "udp", "dns_bytes": query_bytes + response_bytes, "truncated": False,
                "round_trips_est": 1, "packets_est": 1 + frags}
    return {"transport": "udp+tcp", "dns_bytes": query_bytes + tc_bytes + 2 + query_bytes + 2 + response_bytes,
            "truncated": True, "round_trips_est": 3,
            "packets_est": 2 + 3 + 1 + analytical.tcp_segments(response_bytes + 2) + 4}


def rrsig_bytes(msg: dns.message.Message) -> int:
    total = 0
    for section in (msg.answer, msg.authority):
        for rrset in section:
            if rrset.rdtype == dns.rdatatype.RRSIG:
                total += sum(len(rd.signature) for rd in rrset)
    return total


def nx_names(zone: Zone, count: int, seed: int) -> list[dns.name.Name]:
    rng = random.Random(seed ^ 0x5EED)
    return [dns.name.from_text(f"nx{rng.getrandbits(48):012x}", zone.origin) for _ in range(count)]


def response_catalog(zone: Zone, sigs: Signatures, nx_sample: int, seed: int) -> list[dict]:
    """Measure every positive response, the DNSKEY response and sampled NXDOMAINs."""
    b = ResponseBuilder(zone, sigs)
    targets: list[tuple[str, str, dns.name.Name, int]] = [("dnskey", "apex", zone.origin, dns.rdatatype.DNSKEY)]
    for item in zone.items:
        if item.record_class in ("small", "medium", "large"):
            targets.append(("positive", item.record_class, item.owner, item.rdtype))
    targets += [("nxdomain", "nxdomain", n, dns.rdatatype.A) for n in nx_names(zone, nx_sample, seed)]

    rows: list[dict] = []
    variants = [False, True] if sigs.mode.family == "mtl" else [False]
    for kind, rclass, qname, qtype in targets:
        for full in variants:
            for limit in UDP_LIMITS:
                q, r = b.response(kind, qname, qtype, limit, mtl_full=full)
                qb, rb = wire_len(q), wire_len(r)
                tcb = truncated_len(q)
                ex = exchange(qb, rb, tcb, limit, force_tcp=full)
                rows.append({
                    "kind": kind, "record_class": rclass, "qname": qname.to_text(),
                    "variant": "mtl_full" if full else "default", "udp_limit": limit,
                    "query_bytes": qb, "response_bytes": rb, "truncated_response_bytes": tcb,
                    "rrsig_signature_bytes": rrsig_bytes(r), "fits_udp": rb <= limit,
                    "ip_fragments_est": analytical.udp_fragments(rb) if rb <= limit else 0,
                    **ex,
                })
    return rows


def query_workload(zone: Zone, n_queries: int, nx_fraction: float, zipf_s: float, seed: int) -> list[tuple]:
    """Deterministic lookup sequence: (kind, qname, qtype). DNSKEY is fetched first."""
    rng = random.Random(seed ^ 0xC0FFEE)
    names = [it for it in zone.items if it.record_class in ("small", "medium", "large")]
    weights = [1.0 / (rank + 1) ** zipf_s for rank in range(len(names))]
    nx = iter(nx_names(zone, n_queries, seed + 1))
    out: list[tuple] = [("dnskey", zone.origin, dns.rdatatype.DNSKEY)]
    for _ in range(n_queries):
        if rng.random() < nx_fraction:
            out.append(("nxdomain", next(nx), dns.rdatatype.A))
        else:
            it = rng.choices(names, weights)[0]
            out.append(("positive", it.owner, it.rdtype))
    return out


def workload_accounting(zone: Zone, sigs: Signatures, workload: list[tuple], udp_limit: int) -> tuple[dict, list]:
    """Sum transmitted bytes for a lookup sequence.

    Assumptions (stated in the report): the resolver's answer cache holds every
    response for the whole run (TTL 3600 s), so only the first lookup of each
    (qname, qtype) is transmitted; NXDOMAIN names are all distinct. For MTL the
    verifier starts with no ladder: its first response is condensed, so it
    re-queries once with mtl-mode-full over TCP (draft-fregly section 6.2).
    Every later condensed signature verifies against that cached ladder because
    the whole zone is one MTL series signed in one batch.
    """
    b = ResponseBuilder(zone, sigs)
    seen: set[tuple] = set()
    have_ladder = False
    totals = {"lookups": len(workload), "transmitted_responses": 0, "dns_bytes": 0, "response_bytes": 0,
              "rrsig_signature_bytes": 0, "truncations": 0, "tcp_exchanges": 0, "mtl_full_fetches": 0,
              "round_trips_est": 0, "packets_est": 0}
    cached: list[dns.message.Message] = []

    def account(q, r, force_tcp):
        qb, rb = wire_len(q), wire_len(r)
        ex = exchange(qb, rb, truncated_len(q), udp_limit, force_tcp)
        totals["dns_bytes"] += ex["dns_bytes"]
        totals["response_bytes"] += rb
        totals["rrsig_signature_bytes"] += rrsig_bytes(r)
        totals["truncations"] += int(ex["truncated"])
        totals["tcp_exchanges"] += int(ex["transport"] != "udp")
        totals["round_trips_est"] += ex["round_trips_est"]
        totals["packets_est"] += ex["packets_est"]

    for kind, qname, qtype in workload:
        key = (qname, qtype)
        if key in seen:
            continue
        seen.add(key)
        totals["transmitted_responses"] += 1
        q, r = b.response(kind, qname, qtype, udp_limit)
        account(q, r, False)
        if sigs.mode.family == "mtl" and not have_ladder:
            qf, rf = b.response(kind, qname, qtype, udp_limit, mtl_full=True)
            account(qf, rf, True)
            totals["mtl_full_fetches"] += 1
            have_ladder = True
        cached.append(r)
    return totals, cached
