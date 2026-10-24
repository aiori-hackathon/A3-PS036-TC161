"""Learning traces for Cipherops: the whole run (stages 1-7) on tiny zones, plus two stage-3 deep dives.

Whole pipeline, every variable printed (needs the container: mtltest, mtlbench and the libraries live there).
Run from the repository root in PowerShell:

    docker compose -f docker/compose.yml run --rm -v "${PWD}\\own.py:/work/own.py" -v "${PWD}\\src:/work/src" `
        bench "python3 own.py 2>&1 | tee results/own_trace.txt"

    options:  --skip-preflight   (stage 2 takes a few minutes)   --trials N   --names-list 3,5,8

The run is a real Cipherops run with a tiny config (3 / 5 / 8 names, all 5 modes, 2 trials). It writes to
results/runs/<time>-own-trace/ exactly like `cipherops run`, and the printed trace is kept in results/own_trace.txt.

Stage-3 deep dives (run anywhere, no container needed):

    python own.py --deep zone                         build_zone line by line, 5 names, seed 20261005
    python own.py --deep sign --mode rsa-2048         signing_messages line by line for one algorithm
    python own.py --deep zone --names 5 --seed 27     5 names that hit every branch: A, AAAA, 3 x MX, 4 x AAAA, TXT
"""

import argparse
import contextlib
import gzip
import hashlib
import io
import json
import math
import os
import random
import statistics
import string
import struct
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import dns.dnssec
import dns.name
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.rrset
from dns.rdtypes.ANY.DNSKEY import DNSKEY
from dns.rdtypes.ANY.RRSIG import RRSIG

from cipherops import workload
from cipherops.codepoints import MODES, Mode
from cipherops.workload import (EXPIRATION, INCEPTION, NSEC3_ITERATIONS, TTL, Item, Zone, _rrset, _wire_len,
                                read_blob_set, write_messages)

ORIGIN = "bench.example."


def section(title: str) -> None:
    print("\n" + "=" * 90)
    print(f" {title}")
    print("=" * 90)


def hx(b: bytes, limit: int = 96) -> str:
    more = f" ... (+{len(b) - limit} more bytes)" if len(b) > limit else ""
    return b[:limit].hex(" ") + more


def show_item(label: str, it: Item) -> None:
    print(f"{label} Item(")
    print(f"      owner        = {it.owner}")
    print(f"      rdtype       = {it.rdtype} ({dns.rdatatype.to_text(it.rdtype)})")
    print(f"      record_class = {it.record_class!r}")
    if it.rrset is None:
        print("      rrset        = None   (DNSKEY is filled in later, per mode)")
    else:
        for line in it.rrset.to_text().splitlines():
            print(f"      rrset        : {line[:150]}{' ...' if len(line) > 150 else ''}")
    print("    )")


# --------------------------------------------------------------------------------------------
# Part 1: build_zone
# --------------------------------------------------------------------------------------------

def data_rrset_traced(rng: random.Random, owner: dns.name.Name, i: int):
    print(f"  _data_rrset(rng, owner={owner}, i={i})")
    r = rng.random()
    print(f"    r = rng.random()            -> {r!r}")
    print(f"    r < 0.60 ?                  -> {r < 0.60}")
    if r < 0.60:
        coin = rng.random()
        print("    class                       -> 'small'")
        print(f"    coin = rng.random()         -> {coin!r}")
        print(f"    coin < 0.5 ?                -> {coin < 0.5}  ({'A' if coin < 0.5 else 'AAAA'})")
        if coin < 0.5:
            third, fourth = (i >> 8) & 0xFF, i & 0xFF
            addr = f"192.0.{third}.{fourth}"
            print(f"    (i >> 8) & 0xFF             -> {third}")
            print(f"    i & 0xFF                    -> {fourth}")
            print(f"    address                     -> {addr}")
            return "small", _rrset(owner, "A", [addr])
        addr = f"2001:db8::{i:x}"
        print(f"    i in hex                    -> {i:x}")
        print(f"    address                     -> {addr}")
        return "small", _rrset(owner, "AAAA", [addr])
    print(f"    r < 0.90 ?                  -> {r < 0.90}")
    if r < 0.90:
        coin = rng.random()
        print("    class                       -> 'medium'")
        print(f"    coin = rng.random()         -> {coin!r}")
        print(f"    coin < 0.5 ?                -> {coin < 0.5}  ({'3 x MX' if coin < 0.5 else '4 x AAAA'})")
        if coin < 0.5:
            texts = [f"{10 * (k + 1)} mx{k}.mail.example.net." for k in range(3)]
            for k, t in enumerate(texts):
                print(f"    k={k} preference={10 * (k + 1)}      -> {t}")
            return "medium", _rrset(owner, "MX", texts)
        texts = [f"2001:db8:{k:x}::{i:x}" for k in range(1, 5)]
        for k, t in zip(range(1, 5), texts):
            print(f"    k={k}                         -> {t}")
        return "medium", _rrset(owner, "AAAA", texts)
    print("    class                       -> 'large'")
    total = rng.randint(1024, 2048)
    print(f"    total = rng.randint(1024, 2048) -> {total}")
    alphabet = string.ascii_letters + string.digits + "+/="
    print(f"    alphabet ({len(alphabet)} chars)        -> {alphabet}")
    body = "".join(rng.choice(alphabet) for _ in range(total))
    print(f"    body = {total} x rng.choice  -> {body[:60]}... ({len(body)} chars)")
    chunks = [body[k:k + 255] for k in range(0, total, 255)]
    print(f"    split every 255 chars       -> {len(chunks)} strings, lengths {[len(c) for c in chunks]}")
    txt = " ".join(f'"{c}"' for c in chunks)
    return "large", _rrset(owner, "TXT", [txt])


def trace_build_zone(dataset: str, n_names: int, seed: int) -> Zone:
    section(f"INPUTS  dataset={dataset!r}  n_names={n_names}  seed={seed}  origin={ORIGIN!r}")
    print(f"TTL               = {TTL}")
    print(f"NSEC3_ITERATIONS  = {NSEC3_ITERATIONS}")

    section("ZONE STEP 1  random generator and zone name")
    rng = random.Random(seed)
    print(f"rng = random.Random({seed})")
    org = dns.name.from_text(ORIGIN)
    print(f"org = dns.name.from_text({ORIGIN!r}) -> {org}")
    print(f"org.labels        = {org.labels}")
    print(f"len(org.labels)   = {len(org.labels)}  (the last label b'' is the DNS root)")

    section("ZONE STEP 2  the 4 apex RRsets (owner = the zone name itself)")
    items: list[Item] = [
        Item(org, dns.rdatatype.SOA, "apex",
             _rrset(org, "SOA", [f"ns1.{ORIGIN} hostmaster.{ORIGIN} 2026100501 7200 3600 1209600 3600"])),
        Item(org, dns.rdatatype.NS, "apex", _rrset(org, "NS", [f"ns1.{ORIGIN}", f"ns2.{ORIGIN}"])),
        Item(org, dns.rdatatype.DNSKEY, "apex", None),
        Item(org, dns.rdatatype.NSEC3PARAM, "apex", _rrset(org, "NSEC3PARAM", ["1 0 0 -"])),
    ]
    for k, it in enumerate(items):
        show_item(f"  items[{k}] =", it)

    types_at: dict[dns.name.Name, list[str]] = {org: ["SOA", "NS", "DNSKEY", "NSEC3PARAM", "RRSIG"]}
    data_names: list[dns.name.Name] = []
    print(f"\ntypes_at   = {{{org}: {types_at[org]}}}")
    print(f"data_names = {data_names}")
    print(f"len(items) = {len(items)}")

    section(f"ZONE STEP 3  one data RRset per host name, {n_names} iterations")
    for i in range(n_names):
        print(f"\n--- iteration i = {i} " + "-" * 60)
        label = f"host{i:06d}"
        print(f"  label = f'host{{i:06d}}'       -> {label!r}")
        owner = dns.name.from_text(label, org)
        print(f"  owner = from_text(label, org) -> {owner}")
        cls, rrset = data_rrset_traced(rng, owner, i)
        print(f"  returned cls                  -> {cls!r}")
        print(f"  returned rrset.rdtype         -> {rrset.rdtype} ({dns.rdatatype.to_text(rrset.rdtype)})")
        print(f"  returned rrset has {len(rrset)} record(s)")
        item = Item(owner, rrset.rdtype, cls, rrset)
        items.append(item)
        show_item(f"  items[{len(items) - 1}] =", item)
        types_at[owner] = [dns.rdatatype.to_text(rrset.rdtype), "RRSIG"]
        print(f"  types_at[{owner}] = {types_at[owner]}")
        data_names.append(owner)
        print(f"  data_names = {[str(n) for n in data_names]}")
        print(f"  len(items) = {len(items)}")

    section("ZONE STEP 4  NSEC3: hash every name that exists")
    print(f"names in types_at = {len(types_at)}  (1 apex + {n_names} hosts)")
    for name, types in types_at.items():
        print(f"  {str(name):<32} types {types}")
    hashes = {name: dns.dnssec.nsec3_hash(name, None, NSEC3_ITERATIONS, 1) for name in types_at}
    print("\nhashes = nsec3_hash(name, salt=None, iterations=0, algorithm=1 SHA-1):")
    for name, h in hashes.items():
        print(f"  {str(name):<32} -> {h}")

    section("ZONE STEP 5  sort names by hash (this order is the NSEC3 chain)")
    ordered = sorted(types_at, key=lambda n: hashes[n])
    for idx, name in enumerate(ordered):
        print(f"  ordered[{idx}] = {hashes[name]}  <- {name}")

    section("ZONE STEP 6  one NSEC3 RRset per name, each pointing to the next hash")
    nsec3_by_hash: dict[str, dns.rrset.RRset] = {}
    for idx, name in enumerate(ordered):
        h = hashes[name]
        nxt_idx = (idx + 1) % len(ordered)
        nxt = hashes[ordered[nxt_idx]]
        owner = dns.name.from_text(h.lower(), org)
        text = f"1 0 {NSEC3_ITERATIONS} - {nxt} {' '.join(types_at[name])}"
        print(f"\n  idx = {idx}   name = {name}")
        print(f"    h        = {h}")
        print(f"    nxt_idx  = ({idx} + 1) % {len(ordered)} = {nxt_idx}"
              f"{'   <- wraps to the first hash' if nxt_idx == 0 else ''}")
        print(f"    nxt      = {nxt}")
        print(f"    owner    = {owner}")
        print(f"    text     = {text!r}")
        rrset = _rrset(owner, "NSEC3", [text])
        nsec3_by_hash[h] = rrset
        items.append(Item(owner, dns.rdatatype.NSEC3, "nsec3", rrset))
        print(f"    rrset    = {rrset.to_text()}")
        print(f"    len(items) = {len(items)}")

    section("ZONE STEP 7  items BEFORE sort")
    for k, it in enumerate(items):
        print(f"  [{k:2d}] {str(it.owner):<52} {dns.rdatatype.to_text(it.rdtype):<10} {it.record_class}")

    items.sort(key=lambda it: (it.owner, it.rdtype))

    section("ZONE STEP 8  items AFTER sort by (owner, rdtype)  = order of messages.bin")
    for k, it in enumerate(items):
        print(f"  [{k:2d}] key=({str(it.owner):<52}, {it.rdtype:>2} {dns.rdatatype.to_text(it.rdtype):<10}) "
              f"{it.record_class}")

    zone = Zone(dataset, org, seed, items, data_names, nsec3_by_hash)

    section("ZONE STEP 9  returned Zone")
    print(f"zone.dataset        = {zone.dataset!r}")
    print(f"zone.origin         = {zone.origin}")
    print(f"zone.seed           = {zone.seed}")
    print(f"len(zone.items)     = {len(zone.items)}")
    print(f"zone.data_names     = {[str(n) for n in zone.data_names]}")
    print(f"zone.nsec3_by_hash  = {len(zone.nsec3_by_hash)} entries")
    counts: dict[str, int] = {}
    for it in zone.items:
        counts[it.record_class] = counts.get(it.record_class, 0) + 1
    print(f"record classes      = {counts}")
    print(f"2 * n_names + 5     = {2 * n_names + 5}")

    section("ZONE CHECK  same result as cipherops.workload.build_zone?")
    real = workload.build_zone(dataset, n_names, seed)

    def fingerprint(z: Zone):
        return [(it.owner, it.rdtype, it.record_class, it.rrset.to_text() if it.rrset is not None else None)
                for it in z.items]

    print("MATCH" if fingerprint(real) == fingerprint(zone) else "MISMATCH")
    return zone


# --------------------------------------------------------------------------------------------
# Part 2: signing_messages
# --------------------------------------------------------------------------------------------

def placeholder_public_key_traced(seed: int, mode: Mode, indent: str) -> bytes:
    print(f"{indent}placeholder_public_key(seed={seed}, mode={mode.key!r})")
    print(f"{indent}  mode.public_key_len = {mode.public_key_len}")
    out = b""
    counter = 0
    while len(out) < mode.public_key_len:
        text = f"{seed}:{mode.key}:{counter}"
        digest = hashlib.sha256(text.encode()).digest()
        out += digest
        print(f"{indent}  counter={counter}  sha256({text!r}) -> {digest.hex()}")
        print(f"{indent}            out is now {len(out)} bytes")
        counter += 1
    out = out[: mode.public_key_len]
    print(f"{indent}  cut to {mode.public_key_len} bytes -> {hx(out)}")
    if mode.key == "rsa-2048":
        out = b"\x03\x01\x00\x01" + bytes([out[4] | 0x80]) + out[5:]
        print(f"{indent}  RSA: first 4 bytes replaced by 03 01 00 01 (exponent length 3, exponent 65537),")
        print(f"{indent}       byte 4 gets its top bit set so the modulus is a full 2048 bits")
        print(f"{indent}       -> {hx(out)}")
    print(f"{indent}  returns {len(out)} bytes")
    return out


def dnskey_rrset_traced(zone: Zone, mode: Mode, indent: str) -> dns.rrset.RRset:
    print(f"{indent}zone.dnskey_rrset(mode={mode.key!r})")
    key = placeholder_public_key_traced(zone.seed, mode, indent + "  ")
    rdata = DNSKEY(dns.rdataclass.IN, dns.rdatatype.DNSKEY, 257, 3, mode.dnssec_alg, key)
    print(f"{indent}  rdata = DNSKEY(IN, DNSKEY, flags=257, protocol=3, algorithm={mode.dnssec_alg}, key=<{len(key)} B>)")
    print(f"{indent}    flags 257 = 256 (zone key) + 1 (secure entry point)")
    rrset = dns.rrset.RRset(zone.origin, dns.rdataclass.IN, dns.rdatatype.DNSKEY)
    rrset.update_ttl(TTL)
    rrset.add(rdata)
    print(f"{indent}  rrset = RRset({zone.origin}, IN, DNSKEY), ttl {TTL}, 1 record added")
    print(f"{indent}  returns: {rrset.to_text()}")
    return rrset


def key_id_traced(rdata, indent: str) -> int:
    wire = rdata.to_wire()
    print(f"{indent}dns.dnssec.key_id(rdata)   (RFC 4034 Appendix B checksum)")
    print(f"{indent}  rdata wire = flags(2) protocol(1) algorithm(1) public key({len(wire) - 4}) = {len(wire)} bytes")
    print(f"{indent}  {hx(wire, 400)}")
    ac = 0
    for i, b in enumerate(wire):
        ac += (b << 8) if i % 2 == 0 else b
    print(f"{indent}  add the bytes as 16-bit words (even index = high byte, odd = low byte) -> {ac} ({ac:#x})")
    folded = (ac + ((ac >> 16) & 0xFFFF)) & 0xFFFF
    print(f"{indent}  add the carry above 16 bits back in, keep the low 16 bits      -> {folded}")
    real = dns.dnssec.key_id(rdata)
    print(f"{indent}  dnspython key_id returns {real}  ({'same' if real == folded else 'DIFFERENT'})")
    return real


def decode_signing_input(data: bytes, indent: str) -> int:
    tc, alg, labels, ttl, exp, inc, tag = struct.unpack_from(">HBBIIIH", data, 0)
    print(f"{indent}bytes  0- 1  type covered  {data[0:2].hex(' '):<12} -> {tc} ({dns.rdatatype.to_text(tc)})")
    print(f"{indent}byte   2     algorithm     {data[2:3].hex():<12} -> {alg}")
    print(f"{indent}byte   3     labels        {data[3:4].hex():<12} -> {labels}")
    print(f"{indent}bytes  4- 7  original TTL  {data[4:8].hex(' '):<12} -> {ttl}")
    print(f"{indent}bytes  8-11  expiration    {data[8:12].hex(' '):<12} -> {exp}")
    print(f"{indent}bytes 12-15  inception     {data[12:16].hex(' '):<12} -> {inc}")
    print(f"{indent}bytes 16-17  key tag       {data[16:18].hex(' '):<12} -> {tag}")
    signer, used = dns.name.from_wire(data, 18)
    print(f"{indent}bytes 18-{18 + used - 1}  signer name   {hx(data[18:18 + used])} -> {signer}")
    prefix = 18 + used
    print(f"{indent}RRSIG prefix = 18 fixed + {used} signer = {prefix} bytes")
    off = prefix
    n = 0
    while off < len(data):
        owner, used = dns.name.from_wire(data, off)
        rtype, rclass, rttl, rdlen = struct.unpack_from(">HHIH", data, off + used)
        rdata_start = off + used + 10
        rd = dns.rdata.from_wire(rclass, rtype, data, rdata_start, rdlen)
        print(f"{indent}record {n}  (bytes {off}-{rdata_start + rdlen - 1}, {used + 10 + rdlen} bytes)")
        print(f"{indent}  owner   {used:3d} B  {hx(data[off:off + used])} -> {owner}")
        print(f"{indent}  type      2 B  {data[off + used:off + used + 2].hex(' ')} -> {dns.rdatatype.to_text(rtype)}")
        print(f"{indent}  class     2 B  {data[off + used + 2:off + used + 4].hex(' ')} -> "
              f"{dns.rdataclass.to_text(rclass)}")
        print(f"{indent}  TTL       4 B  {data[off + used + 4:off + used + 8].hex(' ')} -> {rttl}")
        print(f"{indent}  rdlength  2 B  {data[off + used + 8:off + used + 10].hex(' ')} -> {rdlen}")
        print(f"{indent}  rdata   {rdlen:3d} B  {hx(data[rdata_start:rdata_start + rdlen])}")
        text = rd.to_text()
        print(f"{indent}                 -> {text[:120]}{' ...' if len(text) > 120 else ''}")
        off = rdata_start + rdlen
        n += 1
    return prefix


def trace_signing_messages(zone: Zone, mode: Mode) -> tuple[list[bytes], list[dict]]:
    section(f"SIGN INPUTS  signing_messages(zone={zone.dataset!r} with {len(zone.items)} items, mode={mode.key!r})")
    print(f"mode = {mode}")
    print(f"EXPIRATION = {EXPIRATION}   INCEPTION = {INCEPTION}   TTL = {TTL}")

    section("SIGN STEP 1  tag = zone.key_tag(mode)")
    print("  key_tag(mode) = dns.dnssec.key_id(self.dnskey_rrset(mode)[0])")
    dnskey = dnskey_rrset_traced(zone, mode, "  ")
    rdata = dnskey[0]
    print(f"  dnskey_rrset(...)[0] -> the single DNSKEY record: {rdata.to_text()[:100]}")
    tag = key_id_traced(rdata, "  ")
    print(f"  tag = {tag}")
    assert tag == zone.key_tag(mode)

    section("SIGN STEP 2  messages = [], manifest = []; zone.rrsets(mode)")
    print("  rrsets(mode) builds the DNSKEY again and pairs each item with its RRset;")
    print("  only the DNSKEY item (rrset=None) is replaced by that DNSKEY RRset.")
    pairs = zone.rrsets(mode)
    for idx, (item, rrset) in enumerate(pairs):
        replaced = "   <- was None, filled with this mode's DNSKEY" if item.rrset is None else ""
        print(f"  [{idx:2d}] {str(rrset.name):<52} {dns.rdatatype.to_text(rrset.rdtype):<10} "
              f"{item.record_class:<6} {len(rrset)} rr{replaced}")
    messages: list[bytes] = []
    manifest: list[dict] = []

    section(f"SIGN STEP 3  one signing input per RRset ({len(pairs)} iterations)")
    for idx, (item, rrset) in enumerate(pairs):
        print(f"\n--- idx = {idx}   {rrset.name} {dns.rdatatype.to_text(rrset.rdtype)} "
              f"({item.record_class}) " + "-" * 30)
        print("  input rrset:")
        for line in rrset.to_text().splitlines():
            print(f"    {line[:140]}{' ...' if len(line) > 140 else ''}")

        print(f"  rrsig_template(rrset, mode, key_tag={tag}, origin={zone.origin})")
        labels = len(rrset.name.labels) - 1
        print(f"    rrset.name.labels = {rrset.name.labels}")
        print(f"    labels = len(labels) - 1 = {labels}   (the root label is not counted)")
        print(f"    rrset.name.is_wild() = {rrset.name.is_wild()}")
        if rrset.name.is_wild():
            labels -= 1
        rrsig = RRSIG(dns.rdataclass.IN, dns.rdatatype.RRSIG, rrset.rdtype, mode.dnssec_alg, labels,
                      TTL, EXPIRATION, INCEPTION, tag, zone.origin, b"")
        print("    returns RRSIG(")
        print(f"      type_covered = {rrsig.type_covered} ({dns.rdatatype.to_text(rrsig.type_covered)})")
        print(f"      algorithm    = {rrsig.algorithm}")
        print(f"      labels       = {rrsig.labels}")
        print(f"      original_ttl = {rrsig.original_ttl}")
        print(f"      expiration   = {rrsig.expiration}")
        print(f"      inception    = {rrsig.inception}")
        print(f"      key_tag      = {rrsig.key_tag}")
        print(f"      signer       = {rrsig.signer}")
        print(f"      signature    = {rrsig.signature!r}   (empty: this is what gets signed)")
        print("    )")

        print("  data = dns.dnssec._make_rrsig_signature_data(rrset, rrsig, origin)")
        print("    = RRSIG fields without the signature + every record of the RRset in canonical form")
        print("      (lower-case owner, original TTL, records sorted by their wire bytes)")
        data = dns.dnssec._make_rrsig_signature_data(rrset, rrsig, zone.origin)
        print(f"    returns {len(data)} bytes:")
        print(f"    {hx(data, 160)}")
        print("    decoded:")
        prefix = decode_signing_input(data, "      ")

        wire_len = _wire_len(rrset)
        print(f"  _wire_len(rrset) = {wire_len} bytes   (rrset.to_wire, no compression)")
        print(f"  check: prefix {prefix} + rrset {len(data) - prefix} = {len(data)} = message_bytes")

        messages.append(data)
        row = {
            "index": idx,
            "owner": rrset.name.to_text(),
            "rdtype": dns.rdatatype.to_text(rrset.rdtype),
            "record_class": item.record_class,
            "rr_count": len(rrset),
            "rrset_wire_bytes": wire_len,
            "message_bytes": len(data),
        }
        manifest.append(row)
        print(f"  messages.append(<{len(data)} bytes>)  -> len(messages) = {len(messages)}")
        print(f"  manifest.append({row})")

    section("SIGN STEP 4  signing_messages returns (messages, manifest)")
    print(f"len(messages) = {len(messages)}")
    print(f"total bytes   = {sum(map(len, messages))}")
    print("manifest:")
    print(f"  {'index':>5} {'owner':<50} {'rdtype':<10} {'class':<6} {'rr':>2} {'rrset_B':>7} {'msg_B':>5}")
    for r in manifest:
        print(f"  {r['index']:>5} {r['owner']:<50} {r['rdtype']:<10} {r['record_class']:<6} "
              f"{r['rr_count']:>2} {r['rrset_wire_bytes']:>7} {r['message_bytes']:>5}")

    section("SIGN CHECK  same result as cipherops.workload.signing_messages?")
    real_msgs, real_manifest = workload.signing_messages(zone, mode)
    print("MATCH" if (real_msgs, real_manifest) == (messages, manifest) else "MISMATCH")

    section("NEXT  runner.py writes the messages: write_messages(ddir / 'messages.bin', msgs)")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "messages.bin"
        write_messages(path, messages)
        blob = path.read_bytes()
        print(f"file size = {len(blob)} = 4 (count) + {len(messages)} x 4 (lengths) + {sum(map(len, messages))} (data)")
        print(f"bytes 0-3 count = {blob[0:4].hex(' ')} -> {struct.unpack_from('>I', blob, 0)[0]}")
        off = 4
        for k, m in enumerate(messages[:3]):
            print(f"bytes {off}-{off + 3} length of message {k} = {blob[off:off + 4].hex(' ')} -> {len(m)}, "
                  f"then {len(m)} bytes of message {k}")
            off += 4 + len(m)
        print("...")
        back = read_blob_set(path)
        print(f"read_blob_set(path) (the format mtlbench reads) -> {len(back)} messages, "
              f"{'identical' if back == messages else 'DIFFERENT'}")
    return messages, manifest


def other_modes_summary(zone: Zone) -> None:
    section("OTHER MODES  same zone, what changes per algorithm")
    print(f"  {'mode':<12} {'alg':>3} {'key_B':>5} {'key_tag':>7} {'total_B':>7} {'DNSKEY msg_B':>12}  first message prefix")
    for mk, m in MODES.items():
        msgs, man = workload.signing_messages(zone, m)
        dk = next(r for r in man if r["rdtype"] == "DNSKEY")
        print(f"  {mk:<12} {m.dnssec_alg:>3} {m.public_key_len:>5} {zone.key_tag(m):>7} "
              f"{sum(map(len, msgs)):>7} {dk['message_bytes']:>12}  {msgs[0][:18].hex(' ')}")
    print("  The algorithm byte (byte 2) and key tag (bytes 16-17) differ, so every message differs per mode.")


# --------------------------------------------------------------------------------------------
# Part 3: the whole run, stages 1-7, on a tiny configuration
# --------------------------------------------------------------------------------------------

def own_config(names: list[int], trials: int) -> dict:
    return {
        "run_name": "own-trace",
        "seed": 20261005,
        "trials": trials,
        "warmup_trials": 1,
        "modes": ["slh-dsa-mtl", "slh-dsa", "ed25519", "ecdsa-p256", "rsa-2048"],
        "datasets": [{"name": n, "n_names": k} for n, k in zip(("small", "medium", "large"), names)],
        "full_verify_sample": 4,
        "direct_sign_limit": {"small": None, "medium": None, "large": 8},
        "wire": {"nx_sample": 3, "workload": {"n_queries": 20, "nx_fraction": 0.10, "zipf_s": 1.1}},
    }


def stage_banner(n: int, title: str, code: str) -> None:
    print("\n" + "#" * 100)
    print(f"#  STAGE {n}/7  {title}")
    print(f"#  code: {code}")
    print("#" * 100)


def human_ns(ns) -> str:
    if ns is None or (isinstance(ns, float) and math.isnan(ns)):
        return ""
    if ns >= 1e9:
        return f"{ns / 1e9:.3f} s"
    if ns >= 1e6:
        return f"{ns / 1e6:.3f} ms"
    if ns >= 1e3:
        return f"{ns / 1e3:.1f} us"
    return f"{ns:.0f} ns"


def fmt(key: str, v) -> str:
    if isinstance(v, float) and math.isnan(v):
        return "NaN (not used by this mode)"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        text = f"{v:,.0f}" if float(v).is_integer() or abs(v) >= 100 else f"{v:,.3f}"
        if key.endswith("_ns"):
            text += f"   ({human_ns(v)})"
        return text
    return str(v)


def arr(v: list) -> str:
    if not v:
        return "[]  (empty)"
    return (f"list[{len(v)}]  first={v[:3]}  median={statistics.median(v):,.0f}  "
            f"min={min(v):,}  max={max(v):,}  sum={sum(v):,}")


def show_dict(d: dict, indent: str = "    ") -> None:
    for k, v in d.items():
        if isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v):
            print(f"{indent}{k:<36} = {arr(v)}")
        elif isinstance(v, dict):
            print(f"{indent}{k:<36} = {v}")
        else:
            print(f"{indent}{k:<36} = {fmt(k, v)}")


def show_res(res: dict, indent: str = "    ") -> None:
    for k, v in res.items():
        if k == "stderr_tail":
            lines = [ln for ln in v.splitlines() if ln.strip()]
            note = "  (library ERROR lines printed while rejecting the tampered inputs: expected)" if lines else ""
            print(f"{indent}{k:<22} = {len(lines)} line(s){note}")
        elif k == "command":
            print(f"{indent}{k:<22} = {' '.join(v)}")
        elif isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v):
            print(f"{indent}{k:<22} = {arr(v)}")
        else:
            print(f"{indent}{k:<22} = {fmt(k, v)}")


def short_owner(name, origin) -> str:
    return name.relativize(origin).to_text() if name != origin else "@"


def list_files(root: Path, rel_to: Path, indent: str = "    ") -> None:
    for p in sorted(root.rglob("*")):
        if p.is_file():
            print(f"{indent}{str(p.relative_to(rel_to)):<64} {p.stat().st_size:>10,} B")


def stage1(cfg_dict: dict, trials_override: int | None):
    from cipherops import envinfo, runner
    import yaml

    stage_banner(1, "Configuration and pinned environment", "runner.load_config, envinfo.collect")
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        yaml.safe_dump(cfg_dict, fh, sort_keys=False)
        cfg_path = Path(fh.name)
    print(f"config file given to load_config: {cfg_path}")
    print(cfg_path.read_text())
    overrides = argparse.Namespace(trials=trials_override, modes=None, datasets=None)
    print(f"overrides (the --trials/--modes/--datasets flags of `cipherops run`) = {vars(overrides)}")
    cfg = runner.load_config(cfg_path, overrides)
    os.unlink(cfg_path)
    print("\ncfg = load_config(path, overrides)  ->")
    for k, v in cfg.items():
        print(f"    cfg[{k!r:<22}] = {v}")

    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + cfg["run_name"]
    root = Path("results") / "runs" / run_id
    root.mkdir(parents=True, exist_ok=True)
    print(f"\nrun_id = {run_id}")
    print(f"root   = {root}      (inside the container /work/{root}; on Windows results\\runs\\{run_id})")
    (root / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))

    print(f"\nenv = envinfo.collect(HARNESS={runner.HARNESS!r})  ->")
    env = envinfo.collect(runner.HARNESS)
    print("\n".join("    " + ln for ln in json.dumps(env, indent=2).splitlines()))
    (root / "environment.json").write_text(json.dumps(env, indent=2))
    print("\nwritten:")
    list_files(root, root)
    return cfg, root, env


PREFLIGHT_WHAT = {
    "reference_library_installed": "Path.exists() on libmtlslib.so, mtllib.h, liboqs.so",
    "reference_unit_tests_and_vectors": "runs Verisign's own test binary /opt/mtl/test/mtltest",
    "reference_fixture_scheme": "reads MTL_TEST_VECTOR_SCHEME_NAME from mtltest_test_vectors.h (warning only)",
    "reference_tools_roundtrip": "mtlkeygen -> mtlsign -r (16 random 64-byte messages) -> mtlverify condensed + full, "
                                 "plus one tampered message that must fail",
}


def stage2(root: Path, skip: bool) -> dict | None:
    from cipherops import preflight, runner

    stage_banner(2, "Go/no-go gate", "preflight.run")
    if skip:
        print("skipped (--skip-preflight). A real run would stop here with exit code 3 on NO-GO.")
        return None
    print(f"verdict = preflight.run(results_dir={root}, harness={runner.HARNESS!r})")
    print("progress lines from preflight go to stderr and appear interleaved below")
    t0 = time.time()
    verdict = preflight.run(root, runner.HARNESS)
    print(f"\npreflight took {time.time() - t0:.1f} s; it returned a dict with {len(verdict['checks'])} checks:")
    for c in verdict["checks"]:
        what = PREFLIGHT_WHAT.get(c["check"], "harness_smoke: run_trial() on a 6-name zone (seed 7), full_verify=4")
        mark = "ok  " if c["ok"] else ("FAIL" if c["critical"] else "warn")
        print(f"  [{mark}] {c['check']:<36} critical={c['critical']!s:<5} what: {what}")
        print(f"         detail: {c['detail'].splitlines()[0] if c['detail'] else ''}")
    print(f"\nverdict['verdict']   = {verdict['verdict']!r}")
    print(f"verdict['algorithm'] = {verdict['algorithm']!r}")
    print(f"go = all(ok for critical checks) -> {verdict['verdict'] == 'GO'}")
    print(f"written: {root / 'preflight.json'}")
    if verdict["verdict"] != "GO":
        print("NO-GO: cmd_run would print the verdict and return 3 here. Stopping.")
        sys.exit(3)
    return verdict


def prefix_fields(data: bytes) -> str:
    tc, alg, labels, ttl, exp, inc, tag = struct.unpack_from(">HBBIIIH", data, 0)
    return (f"type={dns.rdatatype.to_text(tc)} alg={alg} labels={labels} ttl={ttl} "
            f"exp={exp} inc={inc} key_tag={tag}")


def stage3(cfg: dict, root: Path):
    import pandas as pd
    from cipherops import runner
    from cipherops.workload import build_zone, signing_messages, zone_text

    stage_banner(3, "Build zones and RFC 4034 signing inputs", "workload.build_zone, workload.signing_messages")
    zones, paths, sizes, dataset_rows = {}, {}, {}, []
    for i, ds in enumerate(cfg["datasets"]):
        name, seed = ds["name"], cfg["seed"] + i
        print(f"\n{'=' * 100}\n dataset loop i={i}: ds={ds}\n{'=' * 100}")
        zone = build_zone(name, ds["n_names"], seed)
        zones[name] = zone
        sizes[name] = len(zone.items)
        classes = Counter(it.record_class for it in zone.items)
        print(f"zone = build_zone({name!r}, n_names={ds['n_names']}, seed=cfg['seed'] + {i} = {seed})")
        print(f"  len(zone.items) = {len(zone.items)}  = 2 * {ds['n_names']} + 5;  classes = {dict(classes)}")
        print(f"  {'idx':>3}  {'owner (relative to ' + str(zone.origin) + ')':<40} {'type':<10} {'class':<6} "
              f"{'rr':>2}  data")
        for k, it in enumerate(zone.items):
            data = "(filled per mode)" if it.rrset is None else " | ".join(rd.to_text() for rd in it.rrset)
            data = data if len(data) <= 60 else data[:57] + "..."
            print(f"  {k:>3}  {short_owner(it.owner, zone.origin):<40} {dns.rdatatype.to_text(it.rdtype):<10} "
                  f"{it.record_class:<6} {len(it.rrset) if it.rrset is not None else 1:>2}  {data}")
        print(f"sizes[{name!r}] = {sizes[name]}")

        for mk in cfg["modes"]:
            mode = MODES[mk]
            ddir = root / "datasets" / name / mk
            ddir.mkdir(parents=True, exist_ok=True)
            msgs, manifest = signing_messages(zone, mode)
            workload.write_messages(ddir / "messages.bin", msgs)
            paths[(name, mk)] = ddir / "messages.bin"
            limit = runner.direct_limit(cfg, name, mk)
            sample = runner.sample_indices(len(msgs), limit) if limit else list(range(len(msgs)))
            signed = set(sample)
            for j, row in enumerate(manifest):
                row["signed_in_trials"] = j in signed
            print(f"\n  --- mode loop mk={mk!r}  (alg {mode.dnssec_alg}, key {mode.public_key_len} B, "
                  f"key_tag {zone.key_tag(mode)})")
            print(f"  msgs, manifest = signing_messages(zone, MODES[{mk!r}])")
            print(f"    len(msgs) = {len(msgs)}   lengths = {[len(m) for m in msgs]}")
            print(f"    sum(lengths) = {sum(map(len, msgs)):,} B")
            print(f"    msgs[0] starts with: {prefix_fields(msgs[0])}")
            size = (ddir / "messages.bin").stat().st_size
            print(f"  write_messages -> {ddir / 'messages.bin'}   {size:,} B = 4 + {len(msgs)} x 4 + "
                  f"{sum(map(len, msgs)):,}")
            print(f"  limit = direct_limit(cfg, {name!r}, {mk!r}) = {limit}")
            print(f"  sample = {sample if limit else 'list(range(' + str(len(msgs)) + '))  (no cap)'}")
            if limit and len(sample) < len(msgs):
                workload.write_messages(ddir / "messages_sample.bin", [msgs[j] for j in sample])
                paths[(name, mk)] = ddir / "messages_sample.bin"
                print(f"  capped: messages_sample.bin holds {len(sample)} of {len(msgs)} messages")
            pd.DataFrame(manifest).to_csv(ddir / "manifest.csv", index=False)
            print(f"  paths[({name!r}, {mk!r})] = {paths[(name, mk)]}")
            if mk == cfg["modes"][0]:
                (root / "datasets" / name / "zone.txt").write_text(zone_text(zone, mode))
                print("  manifest (first mode only; every mode has the same rows, only message_bytes of DNSKEY differs):")
                print("\n".join("    " + ln for ln in pd.DataFrame(manifest).to_string(index=False).splitlines()))
                counts = pd.DataFrame(manifest).record_class.value_counts().to_dict()
                dataset_rows.append({"dataset": name, "n_names": ds["n_names"], "rrsets": len(msgs),
                                     "signing_input_bytes": sum(map(len, msgs)), **counts})

    datasets = pd.DataFrame(dataset_rows)
    runner.write_frame(datasets, root, "datasets")
    print(f"\n{'=' * 100}\n stage 3 results (kept in memory for stage 4/5, and on disk)\n{'=' * 100}")
    print(f"sizes = {sizes}")
    print("paths =")
    for k, v in paths.items():
        print(f"    {k!s:<28} -> {v}")
    print("datasets.csv =")
    print("\n".join("    " + ln for ln in datasets.to_string(index=False).splitlines()))
    return zones, paths, sizes


def stage4(cfg: dict, root: Path, paths: dict, sizes: dict):
    import pandas as pd
    from cipherops import runner
    from cipherops.harness import run_trial

    stage_banner(4, "Sign and verify every RRset, repeated trials",
                 "runner.run_trials -> harness.run_trial -> mtlbench -> libmtl / liboqs / OpenSSL")
    modes = cfg["modes"]
    smallest = min(cfg["datasets"], key=lambda d: d["n_names"])["name"]
    print(f"smallest = {smallest!r}; warm-up: each mode once, out_dir=None, result thrown away")
    for mk in modes:
        for w in range(cfg.get("warmup_trials", 1)):
            t0 = time.time()
            res = run_trial(MODES[mk], paths[(smallest, mk)], None, cfg["full_verify_sample"])
            print(f"  warm-up {mk:<12} exit={res['exit_code']} n_messages={res['n_messages']} "
                  f"({time.time() - t0:.1f} s)  -> discarded")

    rng = random.Random(cfg["seed"])
    trials, ops = [], []
    raw_dir = root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    iteration = 0
    for t in range(cfg["trials"]):
        for ds in cfg["datasets"]:
            order = list(modes)
            rng.shuffle(order)
            name = ds["name"]
            print(f"\n{'=' * 100}\n trial t={t}, dataset {name!r}: order = rng.shuffle(modes) -> {order}\n{'=' * 100}")
            for mk in order:
                iteration += 1
                out_dir = root / "artifacts" / name / mk if t == 0 else None
                print(f"\n  --- iteration {iteration}: t={t} ds={name!r} mk={mk!r}")
                print(f"  out_dir = {out_dir}" + ("" if t == 0 else "   (only trial 0 keeps signatures)"))
                print(f"  res = run_trial(MODES[{mk!r}], {paths[(name, mk)]}, out_dir, "
                      f"full_verify={cfg['full_verify_sample']})")
                t0 = time.time()
                res = run_trial(MODES[mk], paths[(name, mk)], out_dir, cfg["full_verify_sample"])
                wall = time.time() - t0
                if t == 0:
                    print(f"  res (the JSON mtlbench printed, plus exit_code/stderr_tail/command), {wall:.2f} s:")
                    show_res(res)
                raw = raw_dir / f"{name}__{mk}__t{t}.json.gz"
                with gzip.open(raw, "wt") as fh:
                    json.dump(res, fh)
                row = runner.trial_metrics(res, name, mk, t, sizes[name])
                trials.append(row)
                orows = runner.op_rows(res, name, mk, t)
                ops.extend(orows)
                print(f"  saved {raw}")
                if t == 0:
                    print(f"  row = trial_metrics(res, {name!r}, {mk!r}, t={t}, n_total={sizes[name]})  ->")
                    show_dict(row)
                    print(f"  op_rows(...) -> {len(orows)} rows, e.g. {orows[0]}")
                    print("  artifacts written by mtlbench:")
                    list_files(out_dir, root)
                else:
                    print(f"  row: sign_total_ns={fmt('sign_total_ns', row['sign_total_ns'])}  "
                          f"verify_warm_total_ns={fmt('verify_warm_total_ns', row['verify_warm_total_ns'])}  "
                          f"failures={row['failures_total']} tamper_ok={row['tamper_all_rejected']}  "
                          f"op_rows={len(orows)}")

    trials_df, ops_df = pd.DataFrame(trials), pd.DataFrame(ops)
    runner.write_frame(trials_df, root, "trials")
    runner.write_frame(ops_df, root, "ops")
    print(f"\n{'=' * 100}\n stage 4 results\n{'=' * 100}")
    print(f"trials.csv: {len(trials_df)} rows = {cfg['trials']} trials x {len(cfg['datasets'])} datasets x "
          f"{len(modes)} modes;  ops.parquet: {len(ops_df)} rows (one per timed library call)")
    cols = ["dataset", "mode", "trial", "n_signed", "dataset_rrsets", "limited", "sign_total_ns",
            "verify_warm_total_ns", "signature_bytes_max", "failures_total", "tamper_all_rejected"]
    print("\n".join("    " + ln for ln in trials_df[cols].to_string(index=False).splitlines()))
    return trials_df


def print_message(msg, indent: str = "      ") -> None:
    for ln in msg.to_text().splitlines():
        print(f"{indent}{ln[:130]}{' ...' if len(ln) > 130 else ''}")


def traced_accounting(zone, sigs, wl, limit: int, real_totals: dict) -> None:
    from cipherops import dnswire

    b = dnswire.ResponseBuilder(zone, sigs)
    seen, have_ladder, total = set(), False, 0
    print(f"    replaying the {len(wl)} lookups at udp_limit={limit} (same logic as workload_accounting):")
    for n, (kind, qname, qtype) in enumerate(wl):
        key = (qname, qtype)
        label = f"[{n:2d}] {kind:<8} {short_owner(qname, zone.origin):<16} {dns.rdatatype.to_text(qtype):<6}"
        if key in seen:
            print(f"      {label} already answered -> resolver cache, nothing sent")
            continue
        seen.add(key)
        q, r = b.response(kind, qname, qtype, limit)
        qb, rb, tcb = dnswire.wire_len(q), dnswire.wire_len(r), dnswire.truncated_len(q)
        ex = dnswire.exchange(qb, rb, tcb, limit)
        total += ex["dns_bytes"]
        print(f"      {label} q={qb} r={rb:>6} -> {ex['transport']:<7} {ex['dns_bytes']:>7,} B   running total {total:,}")
        if sigs.mode.family == "mtl" and not have_ladder:
            qf, rf = b.response(kind, qname, qtype, limit, mtl_full=True)
            exf = dnswire.exchange(dnswire.wire_len(qf), dnswire.wire_len(rf), dnswire.truncated_len(qf), limit, True)
            total += exf["dns_bytes"]
            have_ladder = True
            print(f"           first MTL answer, no ladder yet: re-ask with mtl-mode-full over TCP, "
                  f"r={dnswire.wire_len(rf):,} -> {exf['dns_bytes']:,} B   running total {total:,}   have_ladder=True")
    print(f"    replay total {total:,} B vs workload_accounting dns_bytes {real_totals['dns_bytes']:,} B -> "
          f"{'MATCH' if total == real_totals['dns_bytes'] else 'MISMATCH'}")


def exchange_formula(qb: int, rb: int, tcb: int, limit: int, force_tcp: bool) -> str:
    if force_tcp:
        return f"TCP: 2 + {qb} + 2 + {rb} = {4 + qb + rb:,}"
    if rb <= limit:
        return f"UDP, {rb} <= {limit}: {qb} + {rb} = {qb + rb:,}"
    return (f"{rb:,} > {limit}: UDP {qb} + truncated {tcb}, then TCP 2 + {qb} + 2 + {rb:,} "
            f"= {qb + tcb + 4 + qb + rb:,}")


def stage5(cfg: dict, root: Path, zones: dict, trials_df):
    import numpy as np
    from cipherops import analytical, cache, dnswire, runner
    from cipherops.codepoints import MTL_SECURITY_PARAMETER_N, SLH_DSA_SHA2_128S_SIG_BYTES
    from cipherops.harness import load_signatures

    stage_banner(5, "DNS answers, transmitted bytes and cache usage",
                 "runner.analyse -> dnswire.query_workload / response_catalog / workload_accounting, cache.py")
    wcfg = cfg["wire"]
    print(f"wcfg = cfg['wire'] = {wcfg}")
    for ds in cfg["datasets"]:
        name = ds["name"]
        zone = zones[name]
        n_total = len(zone.items)
        print(f"\n{'=' * 100}\n dataset {name!r}: zone of {n_total} RRsets\n{'=' * 100}")
        wl = dnswire.query_workload(zone, wcfg["workload"]["n_queries"], wcfg["workload"]["nx_fraction"],
                                    wcfg["workload"]["zipf_s"], cfg["seed"])
        print(f"wl = query_workload(zone, n_queries={wcfg['workload']['n_queries']}, "
              f"nx_fraction={wcfg['workload']['nx_fraction']}, zipf_s={wcfg['workload']['zipf_s']}, "
              f"seed={cfg['seed']})")
        for n, (kind, qname, qtype) in enumerate(wl):
            print(f"    wl[{n:2d}] = ({kind!r:<11} {short_owner(qname, zone.origin):<16} "
                  f"{dns.rdatatype.to_text(qtype)})")
        print(f"    kinds = {dict(Counter(k for k, _, _ in wl))};  distinct (qname, qtype) = "
              f"{len({(q, t) for _, q, t in wl})}")

        for mk in cfg["modes"]:
            mode = MODES[mk]
            print(f"\n  --- mode {mk!r} " + "-" * 70)
            sub = trials_df[(trials_df.dataset == name) & (trials_df["mode"] == mk)]
            limited = bool(sub.limited.any())
            sigs = load_signatures(mode, root / "artifacts" / name / mk, limited)
            print(f"  sub = trials rows for ({name}, {mk}): {len(sub)} rows;  limited = {limited}")
            print(f"  sigs = load_signatures(MODES[{mk!r}], artifacts/{name}/{mk}, limited={limited})")
            print(f"    len(sigs.sigs) = {len(sigs.sigs)}   lengths = {sorted(set(len(s) for s in sigs.sigs))}")
            print(f"    sigs.signed_ladder = {len(sigs.signed_ladder):,} B" if sigs.signed_ladder
                  else "    sigs.signed_ladder = None")
            print(f"    sigs.reused = {sigs.reused}" + ("   (sample signatures cycled over all RRsets)" if sigs.reused
                                                         else ""))
            print(f"    len(rrsig_field(0)) = {len(sigs.rrsig_field(0))} B" +
                  (f";  len(rrsig_field(0, full=True)) = {len(sigs.rrsig_field(0, full=True)):,} B "
                   f"(1 type byte + condensed + ladder)" if mode.family == "mtl" else ""))

            if name == cfg["datasets"][0]["name"]:
                b = dnswire.ResponseBuilder(zone, sigs)
                target = zone.data_names[0]
                qtype = next(it.rdtype for it in zone.items if it.owner == target)
                variants = [False, True] if mode.family == "mtl" else [False]
                for full in variants:
                    q, r = b.response("positive", target, qtype, 1232, mtl_full=full)
                    qb, rb, tcb = dnswire.wire_len(q), dnswire.wire_len(r), dnswire.truncated_len(q)
                    print(f"  example: q, r = ResponseBuilder.response('positive', {target}, "
                          f"{dns.rdatatype.to_text(qtype)}, payload=1232, mtl_full={full})")
                    print("    query:")
                    print_message(q)
                    print("    response:")
                    print_message(r)
                    print(f"    wire_len(q)={qb}  wire_len(r)={rb}  truncated_len(q)={tcb}  "
                          f"rrsig_bytes(r)={dnswire.rrsig_bytes(r)}")
                    print(f"    exchange -> {exchange_formula(qb, rb, tcb, 1232, full)} bytes on the wire")

            catalog = dnswire.response_catalog(zone, sigs, wcfg["nx_sample"], cfg["seed"])
            print(f"  catalog = response_catalog(zone, sigs, nx_sample={wcfg['nx_sample']}, seed) -> {len(catalog)} rows "
                  f"(1 DNSKEY + {len(zone.data_names)} names + {wcfg['nx_sample']} NXDOMAIN, x 2 UDP limits"
                  f"{', x 2 variants' if mode.family == 'mtl' else ''}); rows at 1232:")
            for row in catalog:
                if row["udp_limit"] == 1232:
                    print(f"    {row['kind']:<8} {row['record_class']:<8} {row['qname'][:30]:<30} {row['variant']:<8} "
                          f"q={row['query_bytes']:>3} r={row['response_bytes']:>6,} sig={row['rrsig_signature_bytes']:>6,} "
                          f"fits={row['fits_udp']!s:<5} {row['transport']:<7} wire={row['dns_bytes']:>7,}")
            default = [r for r in catalog if r["variant"] == "default" and r["udp_limit"] == 1232]
            fit = 100 * sum(r["fits_udp"] for r in default) / len(default)
            print(f"    -> {fit:.0f}% of default answers fit in 1232 B; median answer "
                  f"{int(np.median([r['response_bytes'] for r in default])):,} B")

            cached_1232 = None
            for limit in dnswire.UDP_LIMITS:
                totals, cached = dnswire.workload_accounting(zone, sigs, wl, limit)
                print(f"  totals, cached = workload_accounting(zone, sigs, wl, udp_limit={limit})  ->")
                show_dict(totals, "      ")
                if limit == 1232:
                    cached_1232 = cached
                    traced_accounting(zone, sigs, wl, limit, totals)
            measured = cache.resolver_cache_measured(cached_1232, sigs.signed_ladder)
            print(f"  measured = cache.resolver_cache_measured({len(cached_1232)} cached responses, ladder="
                  f"{len(sigs.signed_ladder) if sigs.signed_ladder else None})  ->")
            show_dict(measured, "      ")
            ana = cache.resolver_cache_analytical(mode, measured["cached_rrsigs"], n_total,
                                                  runner.CLASSICAL_SIG_BYTES.get(mk))
            print(f"  analytical = cache.resolver_cache_analytical(mode, n_rrsigs={measured['cached_rrsigs']}, "
                  f"zone_rrsets={n_total}, classical_sig={runner.CLASSICAL_SIG_BYTES.get(mk)})  ->")
            show_dict(ana, "      ")
            n = MTL_SECURITY_PARAMETER_N
            if mode.family == "mtl":
                print(f"  MTL size checks (n = {n} bytes per hash, N = {n_total} RRsets):")
                print(f"      measured condensed max = {int(sub.signature_bytes_max.max())}   "
                      f"max_condensed_signature = 28 + 3*{n} + {n}*floor_log2({n_total}) = "
                      f"{analytical.max_condensed_signature(n, n_total)}")
                print(f"      measured signed ladder = {int(sub.signed_ladder_bytes.max()):,}   "
                      f"max_signed_ladder = {analytical.max_signed_ladder(n, n_total, SLH_DSA_SHA2_128S_SIG_BYTES):,}")
                print(f"      rungs = popcount({n_total}) = popcount({n_total:b}) = {analytical.rung_count(n_total)}")
                print(f"      signer_state_analytical = {cache.signer_state_analytical(n_total)}  "
                      f"measured node_set_rss_growth_bytes median = {sub.node_set_rss_growth_bytes.median():,.0f}")
            if mode.family in ("mtl", "pqc-direct"):
                storage = analytical.zone_signature_storage(n_total, SLH_DSA_SHA2_128S_SIG_BYTES, n)
                print(f"  zone_signature_storage({n_total}, {SLH_DSA_SHA2_128S_SIG_BYTES}, {n}) = {storage}")

    print(f"\n{'=' * 100}\n frames = runner.analyse(cfg, root, zones, trials)  (the real function, same calls as above)"
          f"\n{'=' * 100}")
    with contextlib.redirect_stderr(io.StringIO()):
        frames = runner.analyse(cfg, root, zones, trials_df)
    for fname, df in frames.items():
        runner.write_frame(df, root, fname)
        print(f"  {fname}.csv: {len(df)} rows x {len(df.columns)} columns: {list(df.columns)}")
    return frames


def stage6(cfg: dict, root: Path, trials_df, frames: dict) -> dict:
    from cipherops import runner, stats

    stage_banner(6, "Statistics: medians and bootstrap 95% confidence intervals", "runner.summarise, stats.py")
    ds0, mk0 = cfg["datasets"][0]["name"], "slh-dsa-mtl"
    g = trials_df[(trials_df.dataset == ds0) & (trials_df["mode"] == mk0)]
    vals = g["sign_total_ns"].dropna()
    print(f"example ({ds0}, {mk0}), column sign_total_ns:")
    print(f"  vals = {list(vals)}   (one value per trial)")
    print(f"  stats.summarize(vals) = {stats.summarize(vals)}")
    print(f"  stats.bootstrap_ci(vals) = {stats.bootstrap_ci(vals)}")
    print("    = resample the trial values 2000 times with replacement (seed 0), take the median of each,")
    print("      report the 2.5th and 97.5th percentile of those 2000 medians")

    summary = runner.summarise(trials_df, frames)
    (root / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print("\nsummary = summarise(trials, frames); summary['trials'] (median over trials, [95% CI]):")
    for ds in cfg["datasets"]:
        for mk in cfg["modes"]:
            e = summary["trials"][ds["name"]][mk]
            s, v = e["sign_total_ns"], e["verify_warm_total_ns"]
            print(f"  {ds['name']:<7} {mk:<12} sign {human_ns(s['median']):>11} "
                  f"[{human_ns(s['median_ci95'][0])}, {human_ns(s['median_ci95'][1])}]   "
                  f"verify {human_ns(v['median']):>10}   checks_passed={e['all_checks_passed']}  limited={e['limited']}")
    first = cfg["datasets"][0]["name"]
    print(f"\nsummary['transmitted'][{first!r}] (per mode, per UDP limit):")
    for mk, by_limit in summary["transmitted"][first].items():
        for lim, d in by_limit.items():
            print(f"  {mk:<12} {lim}: {d}")
    print(f"\nsummary['response_fit'][{first!r}] (default answers at 1232, per record class):")
    for mk, d in summary["response_fit"][first].items():
        print(f"  {mk:<12} {d}")
    print(f"\nwritten: {root / 'summary.json'} ({(root / 'summary.json').stat().st_size:,} B)")
    return summary


def stage7(root: Path) -> None:
    from cipherops import report

    stage_banner(7, "Report and figures", "report.write")
    with contextlib.redirect_stderr(io.StringIO()):
        out = report.write(root)
    print(f"report.write(root) -> {out}")
    md = (root / "report.md").read_text(encoding="utf-8")
    print("report.md section headings:")
    in_code = False
    for ln in md.splitlines():
        if ln.startswith("```"):
            in_code = not in_code
        elif ln.startswith("#") and not in_code:
            print(f"    {ln}")
    print("figures:")
    list_files(root / "figures", root)


def pipeline(args) -> None:
    from cipherops.runner import HARNESS

    if not Path(HARNESS).exists():
        print(f"mtlbench not found at {HARNESS}. Stages 2 and 4 need the container; run from the repo root:")
        print('  docker compose -f docker/compose.yml run --rm -v "${PWD}\\own.py:/work/own.py" '
              '-v "${PWD}\\src:/work/src" bench "python3 own.py 2>&1 | tee results/own_trace.txt"')
        print("Stage-3 deep dives work anywhere: python own.py --deep zone | --deep sign")
        sys.exit(2)
    import pandas as pd
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)

    names = [int(x) for x in args.names_list.split(",")]
    started = time.time()
    cfg, root, _ = stage1(own_config(names, 2), args.trials)
    stage2(root, args.skip_preflight)
    zones, paths, sizes = stage3(cfg, root)
    trials_df = stage4(cfg, root, paths, sizes)
    frames = stage5(cfg, root, zones, trials_df)
    stage6(cfg, root, trials_df, frames)
    stage7(root)
    print("\n" + "#" * 100)
    print(f"#  DONE in {time.time() - started:.0f} s. Everything this run wrote ({root}):")
    print("#" * 100)
    list_files(root, root)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--deep", choices=["zone", "sign"], help="stage-3 deep dive instead of the whole pipeline")
    p.add_argument("--names", type=int, default=5, help="--deep: number of host names")
    p.add_argument("--seed", type=int, default=20261005, help="--deep: zone seed")
    p.add_argument("--dataset", default="small", help="--deep: dataset label")
    p.add_argument("--mode", default="slh-dsa-mtl", choices=list(MODES), help="--deep sign: algorithm")
    p.add_argument("--names-list", default="3,5,8", help="pipeline: names in the small,medium,large zones")
    p.add_argument("--trials", type=int, help="pipeline: override the 2 trials")
    p.add_argument("--skip-preflight", action="store_true", help="pipeline: skip stage 2")
    args = p.parse_args()

    if args.deep is None:
        pipeline(args)
        return
    if args.deep == "sign":
        with contextlib.redirect_stdout(io.StringIO()):
            zone = trace_build_zone(args.dataset, args.names, args.seed)
        print(f"(zone built silently: {args.dataset}, {args.names} names, seed {args.seed}, {len(zone.items)} RRsets)")
        trace_signing_messages(zone, MODES[args.mode])
        other_modes_summary(zone)
    else:
        trace_build_zone(args.dataset, args.names, args.seed)


if __name__ == "__main__":
    main()
