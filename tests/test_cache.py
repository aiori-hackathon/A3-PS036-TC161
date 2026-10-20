import os

from cipherops import cache, dnswire
from cipherops.codepoints import MODES
from cipherops.workload import build_zone


def test_measured_cache_counts_exact_signature_bytes():
    zone = build_zone("t", 30, 8)
    sigs = dnswire.Signatures(MODES["slh-dsa"], [os.urandom(7856) for _ in zone.items])
    wl = dnswire.query_workload(zone, 100, 0.1, 1.1, 2)
    _, cached = dnswire.workload_accounting(zone, sigs, wl, 1232)
    m = cache.resolver_cache_measured(cached, None)
    assert m["cached_responses"] == len(cached)
    assert m["cached_signature_bytes"] == m["cached_rrsigs"] * 7856
    assert m["python_heap_bytes"] > m["cached_signature_bytes"]


def test_mtl_measured_includes_ladder_once():
    zone = build_zone("t", 30, 8)
    ladder = os.urandom(8100)
    sigs = dnswire.Signatures(MODES["slh-dsa-mtl"], [os.urandom(140) for _ in zone.items], ladder)
    wl = dnswire.query_workload(zone, 100, 0.1, 1.1, 2)
    _, cached = dnswire.workload_accounting(zone, sigs, wl, 1232)
    m = cache.resolver_cache_measured(cached, ladder)
    assert m["ladder_bytes"] == 8100
    assert m["cached_signature_bytes"] == m["cached_rrsigs"] * 141


def test_analytical_cache_per_family():
    a = cache.resolver_cache_analytical(MODES["slh-dsa"], 10, 1000, None)
    assert a["signature_material_bytes"] == 78560
    b = cache.resolver_cache_analytical(MODES["slh-dsa-mtl"], 10, 1000, None)
    assert b["ladder_bytes"] > 7856 and b["per_signature_bytes"] < 300
    c = cache.resolver_cache_analytical(MODES["ed25519"], 10, 1000, 64)
    assert c["signature_material_bytes"] == 640
