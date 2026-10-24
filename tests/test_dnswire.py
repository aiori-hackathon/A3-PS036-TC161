import os

import dns.rdatatype
import pytest

from cipherops import dnswire
from cipherops.codepoints import MODES
from cipherops.workload import build_zone


@pytest.fixture(scope="module")
def zone():
    return build_zone("t", 40, 21)


def _sigs(zone, key, size, ladder=None):
    return dnswire.Signatures(MODES[key], [os.urandom(size) for _ in zone.items], ladder)


def test_positive_response_carries_signature(zone):
    sigs = _sigs(zone, "slh-dsa", 7856)
    b = dnswire.ResponseBuilder(zone, sigs)
    it = next(i for i in zone.items if i.record_class == "small")
    q, r = b.response("positive", it.owner, it.rdtype, 1232)
    assert dnswire.rrsig_bytes(r) == 7856
    assert dnswire.wire_len(r) > 7856


def test_mtl_full_adds_type_byte_and_ladder(zone):
    ladder = os.urandom(8000)
    sigs = _sigs(zone, "slh-dsa-mtl", 140, ladder)
    b = dnswire.ResponseBuilder(zone, sigs)
    it = next(i for i in zone.items if i.record_class == "small")
    _, cond = b.response("positive", it.owner, it.rdtype, 1232)
    _, full = b.response("positive", it.owner, it.rdtype, 1232, mtl_full=True)
    assert dnswire.rrsig_bytes(cond) == 1 + 140
    assert dnswire.rrsig_bytes(full) == 1 + 140 + 8000


def test_nxdomain_has_soa_and_nsec3_proof(zone):
    b = dnswire.ResponseBuilder(zone, _sigs(zone, "ed25519", 64))
    qname = dnswire.nx_names(zone, 1, 3)[0]
    _, r = b.response("nxdomain", qname, dns.rdatatype.A, 1232)
    types = [rr.rdtype for rr in r.authority if rr.rdtype != dns.rdatatype.RRSIG]
    assert types[0] == dns.rdatatype.SOA
    assert 2 <= types.count(dns.rdatatype.NSEC3) <= 3


def test_mtl_full_nxdomain_has_one_full_signature(zone):
    ladder = os.urandom(8000)
    b = dnswire.ResponseBuilder(zone, _sigs(zone, "slh-dsa-mtl", 140, ladder))
    qname = dnswire.nx_names(zone, 1, 3)[0]
    _, r = b.response("nxdomain", qname, dns.rdatatype.A, 1232, mtl_full=True)
    sig_lens = [len(rd.signature) for rr in r.authority if rr.rdtype == dns.rdatatype.RRSIG for rd in rr]
    assert sorted(sig_lens)[-1] == 1 + 140 + 8000
    assert all(s == 141 for s in sorted(sig_lens)[:-1])


def test_exchange_accounting():
    assert dnswire.exchange(50, 500, 30, 1232)["transport"] == "udp"
    ex = dnswire.exchange(50, 5000, 30, 1232)
    assert ex["truncated"] and ex["dns_bytes"] == 50 + 30 + 2 + 50 + 2 + 5000
    assert dnswire.exchange(50, 5000, 30, 1232, force_tcp=True)["dns_bytes"] == 2 + 50 + 2 + 5000


def test_workload_mtl_fetches_ladder_once(zone):
    wl = dnswire.query_workload(zone, 200, 0.1, 1.1, 5)
    sigs = _sigs(zone, "slh-dsa-mtl", 140, os.urandom(8000))
    totals, cached = dnswire.workload_accounting(zone, sigs, wl, 1232)
    assert totals["mtl_full_fetches"] == 1
    assert totals["transmitted_responses"] == len(cached)
    direct, _ = dnswire.workload_accounting(zone, _sigs(zone, "slh-dsa", 7856), wl, 1232)
    assert direct["mtl_full_fetches"] == 0
    assert direct["truncations"] == direct["transmitted_responses"]
    assert totals["dns_bytes"] < direct["dns_bytes"]
