"""Cache usage: direct measurements kept separate from analytical estimates.

Measured (kind="measured"):
  * resolver-side: responses are parsed from wire and stored in dnspython's
    existing LRUCache; we record the Python heap retained by that population
    (tracemalloc) and count the RRSIG signature bytes and wire bytes it holds.
    MTL adds the one signed ladder a verifier keeps.
  * signer-side: resident memory growth of the reference library while
    appending the zone to its node set, reported by the harness.

Analytical (kind="analytical"): the same quantities from draft section 12
formulas and simple node-count arithmetic.
"""

from __future__ import annotations

import gc
import tracemalloc

import dns.message
import dns.rdatatype
import dns.resolver

from . import analytical
from .codepoints import MTL_SECURITY_PARAMETER_N, SLH_DSA_SHA2_128S_SIG_BYTES, Mode


def _signature_count_and_bytes(msgs: list[dns.message.Message]) -> tuple[int, int]:
    count = total = 0
    for m in msgs:
        for section in (m.answer, m.authority):
            for rrset in section:
                if rrset.rdtype == dns.rdatatype.RRSIG:
                    for rd in rrset:
                        count += 1
                        total += len(rd.signature)
    return count, total


def resolver_cache_measured(responses: list[dns.message.Message], signed_ladder: bytes | None) -> dict:
    wires = [r.to_wire(max_size=65535) for r in responses]
    gc.collect()
    tracemalloc.start()
    base, _ = tracemalloc.get_traced_memory()
    cache = dns.resolver.LRUCache(max_size=len(wires) + 16)
    parsed = []
    for w in wires:
        m = dns.message.from_wire(w)
        q = m.question[0]
        cache.put((q.name, q.rdtype, q.rdclass), dns.resolver.Answer(q.name, q.rdtype, q.rdclass, m))
        parsed.append(m)
    ladder_store = {"ladder": bytes(signed_ladder)} if signed_ladder else {}
    gc.collect()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    sig_count, sig_bytes = _signature_count_and_bytes(parsed)
    ladder_bytes = sum(len(v) for v in ladder_store.values())
    del cache, ladder_store
    return {
        "cached_responses": len(wires),
        "cached_rrsigs": sig_count,
        "cached_wire_bytes": sum(len(w) for w in wires),
        "cached_signature_bytes": sig_bytes,
        "ladder_bytes": ladder_bytes,
        "signature_material_bytes": sig_bytes + ladder_bytes,
        "python_heap_bytes": current - base,
        "python_heap_peak_bytes": peak - base,
    }


def resolver_cache_analytical(mode: Mode, n_rrsigs: int, zone_rrsets: int, classical_sig: int | None) -> dict:
    n = MTL_SECURITY_PARAMETER_N
    if mode.family == "mtl":
        per_sig = 1 + analytical.max_condensed_signature(n, zone_rrsets)
        ladder = analytical.max_signed_ladder(n, zone_rrsets, SLH_DSA_SHA2_128S_SIG_BYTES)
    elif mode.family == "pqc-direct":
        per_sig, ladder = SLH_DSA_SHA2_128S_SIG_BYTES, 0
    else:
        per_sig, ladder = classical_sig or 0, 0
    return {
        "cached_rrsigs": n_rrsigs,
        "per_signature_bytes": per_sig,
        "cached_signature_bytes": n_rrsigs * per_sig,
        "ladder_bytes": ladder,
        "signature_material_bytes": n_rrsigs * per_sig + ladder,
    }


def signer_state_measured(trial: dict) -> dict:
    mem = trial.get("memory", {})
    out = {"maxrss_bytes": mem.get("maxrss")}
    if "rss_after_append" in mem:
        out["node_set_rss_growth_bytes"] = mem["rss_after_append"] - mem["rss_after_keygen"]
    return out


def signer_state_analytical(zone_rrsets: int) -> dict:
    """Hash storage for a binary node set: 2N-1 nodes plus N randomizers, n bytes each."""
    n = MTL_SECURITY_PARAMETER_N
    return {"node_set_hash_bytes": (2 * zone_rrsets - 1) * n + zone_rrsets * n}
