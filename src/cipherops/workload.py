"""Deterministic DNSSEC zone workloads and their RFC 4034 signing inputs.

A dataset is a synthetic zone. Every RRset in it (including the NSEC3 chain)
is one message for the signer, exactly as DNSSEC signs one RRSIG per RRset.
The signing input for each RRset is built by dnspython following RFC 4034
section 3.1.8.1: RRSIG RDATA without the signature field, followed by the
RRset in canonical form.
"""

from __future__ import annotations

import hashlib
import io
import random
import string
import struct
from dataclasses import dataclass
from pathlib import Path

import dns.dnssec
import dns.name
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.rrset
from dns.rdtypes.ANY.DNSKEY import DNSKEY
from dns.rdtypes.ANY.RRSIG import RRSIG

from .codepoints import Mode

TTL = 3600
INCEPTION = 1767225600   # 2026-01-01T00:00:00Z, fixed for reproducible inputs
EXPIRATION = 1798761600  # 2027-01-01T00:00:00Z
NSEC3_ITERATIONS = 0     # RFC 9276
RECORD_CLASSES = ("small", "medium", "large")


@dataclass
class Item:
    owner: dns.name.Name
    rdtype: int
    record_class: str               # small | medium | large | nsec3 | apex
    rrset: dns.rrset.RRset | None   # None for DNSKEY, which depends on the mode


@dataclass
class Zone:
    dataset: str
    origin: dns.name.Name
    seed: int
    items: list[Item]
    data_names: list[dns.name.Name]
    nsec3_by_hash: dict[str, dns.rrset.RRset]

    def dnskey_rrset(self, mode: Mode) -> dns.rrset.RRset:
        rdata = DNSKEY(dns.rdataclass.IN, dns.rdatatype.DNSKEY, 257, 3, mode.dnssec_alg,
                       placeholder_public_key(self.seed, mode))
        rrset = dns.rrset.RRset(self.origin, dns.rdataclass.IN, dns.rdatatype.DNSKEY)
        rrset.update_ttl(TTL)
        rrset.add(rdata)
        return rrset

    def rrsets(self, mode: Mode) -> list[tuple[Item, dns.rrset.RRset]]:
        dnskey = self.dnskey_rrset(mode)
        return [(it, it.rrset if it.rrset is not None else dnskey) for it in self.items]

    def key_tag(self, mode: Mode) -> int:
        return dns.dnssec.key_id(self.dnskey_rrset(mode)[0])

    def nsec3_sorted_hashes(self) -> list[str]:
        return sorted(self.nsec3_by_hash)


def placeholder_public_key(seed: int, mode: Mode) -> bytes:
    """Fixed-length stand-in for the DNSKEY public key field.

    The real key pair is generated inside each harness trial. The DNSKEY RRset
    only needs the correct field length for size accounting; its bytes do not
    affect signing or verification cost.
    """
    out = b""
    counter = 0
    while len(out) < mode.public_key_len:
        out += hashlib.sha256(f"{seed}:{mode.key}:{counter}".encode()).digest()
        counter += 1
    out = out[: mode.public_key_len]
    if mode.key == "rsa-2048":
        out = b"\x03\x01\x00\x01" + bytes([out[4] | 0x80]) + out[5:]
    return out


def _rrset(owner: dns.name.Name, rdtype: str, texts: list[str]) -> dns.rrset.RRset:
    return dns.rrset.from_text_list(owner, TTL, "IN", rdtype, texts)


def _txt_strings(rng: random.Random, total: int) -> str:
    alphabet = string.ascii_letters + string.digits + "+/="
    body = "".join(rng.choice(alphabet) for _ in range(total))
    return " ".join(f'"{body[i:i + 255]}"' for i in range(0, total, 255))


def _data_rrset(rng: random.Random, owner: dns.name.Name, i: int) -> tuple[str, dns.rrset.RRset]:
    r = rng.random()
    if r < 0.60:
        if rng.random() < 0.5:
            return "small", _rrset(owner, "A", [f"192.0.{(i >> 8) & 0xFF}.{i & 0xFF}"])
        return "small", _rrset(owner, "AAAA", [f"2001:db8::{i:x}"])
    if r < 0.90:
        if rng.random() < 0.5:
            return "medium", _rrset(owner, "MX", [f"{10 * (k + 1)} mx{k}.mail.example.net." for k in range(3)])
        return "medium", _rrset(owner, "AAAA", [f"2001:db8:{k:x}::{i:x}" for k in range(1, 5)])
    return "large", _rrset(owner, "TXT", [_txt_strings(rng, rng.randint(1024, 2048))])


def build_zone(dataset: str, n_names: int, seed: int, origin: str = "bench.example.") -> Zone:
    """Build a zone with n_names data names plus apex records and an NSEC3 chain."""
    rng = random.Random(seed)
    org = dns.name.from_text(origin)
    items: list[Item] = [
        Item(org, dns.rdatatype.SOA, "apex",
             _rrset(org, "SOA", [f"ns1.{origin} hostmaster.{origin} 2026100501 7200 3600 1209600 3600"])),
        Item(org, dns.rdatatype.NS, "apex", _rrset(org, "NS", [f"ns1.{origin}", f"ns2.{origin}"])),
        Item(org, dns.rdatatype.DNSKEY, "apex", None),
        Item(org, dns.rdatatype.NSEC3PARAM, "apex", _rrset(org, "NSEC3PARAM", ["1 0 0 -"])),
    ]
    types_at: dict[dns.name.Name, list[str]] = {org: ["SOA", "NS", "DNSKEY", "NSEC3PARAM", "RRSIG"]}
    data_names: list[dns.name.Name] = []
    for i in range(n_names):
        owner = dns.name.from_text(f"host{i:06d}", org)
        cls, rrset = _data_rrset(rng, owner, i) #cls says whether its small, med or large
        items.append(Item(owner, rrset.rdtype, cls, rrset))
        types_at[owner] = [dns.rdatatype.to_text(rrset.rdtype), "RRSIG"]
        data_names.append(owner)

    hashes = {name: dns.dnssec.nsec3_hash(name, None, NSEC3_ITERATIONS, 1) for name in types_at}
    ordered = sorted(types_at, key=lambda n: hashes[n])
    nsec3_by_hash: dict[str, dns.rrset.RRset] = {}
    for idx, name in enumerate(ordered):
        h = hashes[name]
        nxt = hashes[ordered[(idx + 1) % len(ordered)]]
        owner = dns.name.from_text(h.lower(), org)
        rrset = _rrset(owner, "NSEC3", [f"1 0 {NSEC3_ITERATIONS} - {nxt} {' '.join(types_at[name])}"])
        nsec3_by_hash[h] = rrset
        items.append(Item(owner, dns.rdatatype.NSEC3, "nsec3", rrset))

    items.sort(key=lambda it: (it.owner, it.rdtype))
    return Zone(dataset, org, seed, items, data_names, nsec3_by_hash)


def rrsig_template(rrset: dns.rrset.RRset, mode: Mode, key_tag: int, origin: dns.name.Name,
                   signature: bytes = b"") -> RRSIG:
    labels = len(rrset.name.labels) - 1
    if rrset.name.is_wild():
        labels -= 1
    return RRSIG(dns.rdataclass.IN, dns.rdatatype.RRSIG, rrset.rdtype, mode.dnssec_alg, labels,
                 TTL, EXPIRATION, INCEPTION, key_tag, origin, signature)


def _wire_len(rrset: dns.rrset.RRset) -> int:
    buf = io.BytesIO()
    rrset.to_wire(buf)
    return buf.tell()


def signing_messages(zone: Zone, mode: Mode) -> tuple[list[bytes], list[dict]]:
    """Return one RFC 4034 signing input per RRset and a manifest row per message."""
    tag = zone.key_tag(mode)
    messages: list[bytes] = []
    manifest: list[dict] = []
    for idx, (item, rrset) in enumerate(zone.rrsets(mode)):
        rrsig = rrsig_template(rrset, mode, tag, zone.origin)
        data = dns.dnssec._make_rrsig_signature_data(rrset, rrsig, zone.origin)
        messages.append(data)
        manifest.append({
            "index": idx,
            "owner": rrset.name.to_text(),
            "rdtype": dns.rdatatype.to_text(rrset.rdtype),
            "record_class": item.record_class,
            "rr_count": len(rrset),
            "rrset_wire_bytes": _wire_len(rrset),
            "message_bytes": len(data),
        })
    return messages, manifest


def write_messages(path: Path, messages: list[bytes]) -> None:
    with open(path, "wb") as fh:
        fh.write(struct.pack(">I", len(messages)))
        for m in messages:
            fh.write(struct.pack(">I", len(m)))
            fh.write(m)


def read_blob_set(path: Path) -> list[bytes]:
    """Read the harness artifact format: u32be count, then (u32be len, bytes)*."""
    data = Path(path).read_bytes()
    (count,) = struct.unpack_from(">I", data, 0)
    off = 4
    out = []
    for _ in range(count):
        (ln,) = struct.unpack_from(">I", data, off)
        off += 4
        out.append(data[off:off + ln])
        off += ln
    return out


def zone_text(zone: Zone, mode: Mode) -> str:
    return "\n".join(rrset.to_text() for _, rrset in zone.rrsets(mode)) + "\n"
