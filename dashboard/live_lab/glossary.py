"""One place for the words the dashboard uses, and what each of them means.

Labels follow what DNS operators already know from dnsperf, resperf, Flamethrower, DNS Shotgun and kxdpgun
(queries, latency percentiles, response size, truncation, timeouts, cache hit/miss) and from signing tools
(zone size, re-signing). ``tip`` is the text shown behind the info icon (Streamlit's ``help=``).
"""
from __future__ import annotations

# key -> (label, tip)
TERMS: dict[str, tuple[str, str]] = {
    # what is tested
    "zone_size": ("Zone size",
                  "How many names the signed zones hold in total. More names mean more RRsets to sign, more "
                  "signature bytes and a bigger resolver cache."),
    "algorithm": ("Algorithm",
                  "The DNSSEC signing setup under test: RSA, ECDSA and Ed25519 (classical); SLH-DSA (post-quantum, "
                  "one large signature per RRset); MTL (SLH-DSA with Merkle Tree Ladder: one signed ladder plus a "
                  "short path per RRset); mixed-mtl (ECDSA at root and TLD, MTL at the leaf zone); "
                  "ml-dsa / ml-dsa-mtl (ML-DSA-44 ± MTL, active kaizer draft)."),
    "key_scheme": ("Key scheme",
                   "CSK: one Combined Signing Key per zone (lab default). KSK+ZSK: separate key-signing and "
                   "zone-signing keys. On Condensed MTL, KSK+ZSK often fails Unbound DS/ladder validation; "
                   "Always-full MTL is the stack to re-check that path."),
    "sign_zones": ("Sign zones",
                   "Signs every zone of the hierarchy for each selected algorithm (what ldns-signzone or "
                   "dnssec-signzone do on a real zone) and writes the server configuration."),
    "zones": ("Zones", "Authoritative zones in the test hierarchy: the root, the TLDs and the leaf zones."),
    "leaf_zones": ("Leaf zones", "Zones at the bottom of the hierarchy, where the names being queried live."),
    "est_rrsigs": ("Estimated RRSIGs", "Signatures (RRSIG records) the signer will produce, estimated from the zone shape."),
    "est_signing": ("Estimated signing time", "Time to sign every zone, estimated from sample timings."),
    "simulated_steps": ("Steps", "Stages of the benchmark: sign, repeat signing, start the lab, measure, stop."),
    # how it is run
    "runs": ("Runs",
             "How many times the whole query workload is repeated, each time cold cache then warm cache. More runs "
             "give a tighter confidence interval around the median. One extra warm-up run is discarded."),
    "signing_repeats": ("Signing repeats",
                        "How often signing and verification are repeated on a sample zone to get a stable "
                        "per-signature time."),
    "signing_sample": ("Signing sample (names)",
                       "Size of the sample zone used for the repeats. The time for the whole zone is "
                       "extrapolated from it and labelled as an estimate; the full zones are also signed once."),
    "parallel_jobs": ("Parallel signing jobs",
                      "Zones signed at the same time. 1 gives clean timings; more is faster but inflates the time of "
                      "the one full-zone signing run (the repeats always run alone)."),
    "queries_per_run": ("Queries per run",
                        "How many queries each cold-cache and warm-cache pass sends (the query count of a dnsperf "
                        "run)."),
    "network_profile": ("Network profile",
                        "LAN adds no delay. WAN adds latency towards each server tier. Lossy adds latency and "
                        "packet loss on the resolver's queries."),
    "edns_buffer": ("EDNS buffer size",
                    "Largest UDP answer the resolver will accept (EDNS0). A bigger answer is truncated (TC bit) and "
                    "retried over TCP. 1232 B is the DNS Flag Day 2020 default."),
    "nx_share": ("NXDOMAIN share (%)",
                 "Share of queries for names that do not exist. Every such name is distinct, so none is cached."),
    "zone_skew": ("Zone popularity skew",
                  "0 = every zone equally likely; higher = a few zones receive most queries (Zipf)."),
    "name_skew": ("Name popularity skew",
                  "The same idea inside a zone. Higher skew means more cache hits."),
    "query_timeout": ("Query timeout (s)", "How long a query may wait for an answer before it counts as a timeout."),
    # what is measured
    "cold_cache": ("Cold cache", "The resolver cache was emptied first, so every answer is fetched from the servers."),
    "warm_cache": ("Warm cache", "The same queries again: answers come from the resolver cache."),
    "latency_p50": ("Latency p50 (ms)", "Median time from query to answer: half of the queries were faster."),
    "latency_p95": ("Latency p95 (ms)", "95% of the queries were answered at least this fast; it shows the slow tail."),
    "signing_time": ("Signing time",
                     "Time to sign the whole zone. In the offline harness it is the median of the runs; for large "
                     "SLH-DSA zones it is extrapolated from a sample."),
    "verification_time": ("Verification time",
                          "Time to validate every signature of the zone once (with a cached ladder for MTL)."),
    "signature_size": ("Signature size",
                       "Total bytes of all signatures in the zone. For MTL: the short signatures plus one signed "
                       "ladder."),
    "signed_zone_size": ("Signed zone size", "Size of the signed zone files on disk."),
    "response_size": ("Response size",
                      "DNS message bytes for the query workload, including TCP retries. Larger answers raise "
                      "bandwidth use and the chance of truncation or fragmentation."),
    "tc_responses": ("Truncated (TC) responses",
                     "Answers that did not fit in the UDP buffer. The resolver has to retry over TCP, which costs "
                     "an extra round trip. Fewer is better."),
    "cache_size": ("Cache size",
                   "Memory the validating resolver's cache holds after the workload. Signatures dominate it."),
    "resolver_auth_bytes": ("Resolver→authoritative bytes",
                            "Bytes exchanged between the resolver and the authoritative servers, counted from the "
                            "packet capture."),
    "resolver_auth_queries": ("Resolver→authoritative queries",
                              "Queries the resolver sent upstream while answering the workload."),
    "validated_pct": ("Validated (%)", "Share of answers the resolver marked as DNSSEC-validated (AD bit set)."),
    "timeouts": ("Timeouts", "Queries that got no answer within the query timeout."),
    "servfail": ("SERVFAIL", "Queries the resolver answered with SERVFAIL, for instance when validation failed."),
    "failed": ("Failed", "Queries that did not end in a usable answer."),
    "valid_runs": ("Valid runs", "Runs without lookup failures. Only valid runs are used for the statistics."),
    "udp_cache": ("Cache", "Cold: empty resolver cache. Warm: answered from the cache."),
    "confidence": ("Confidence interval",
                   "Range that contains the median with the stated probability, from the order statistics of the "
                   "runs."),
    "noise_floor": ("Noise floor",
                    "Spread of an A/A control (the same algorithm measured against itself). A difference smaller "
                    "than this is not a real difference."),
    # zone updates
    "records_changed": ("Records changed per update",
                        "How many records of the zone change in one update. MTL signs once per update; plain "
                        "SLH-DSA signs once per changed record."),
    "resign_time": ("Re-sign time",
                    "Time to produce the signatures needed after one update: one signature for MTL, one per changed "
                    "record for SLH-DSA."),
    "stale_cache": ("Stale resolver cache",
                    "A resolver that has not refreshed since earlier updates. Answers it cannot check with a short "
                    "MTL signature carry a full signature instead."),
    "mtl_internals": ("MTL internals",
                      "The Merkle Tree Ladder itself: its rungs and which leaves they cover. Only needed to follow "
                      "the draft."),
}


def label(key: str) -> str:
    return TERMS[key][0]


def tip(key: str) -> str:
    return TERMS[key][1]


BY_LABEL = {lab: t for lab, t in TERMS.values()}


def column_config(columns, extra: dict[str, str] | None = None) -> dict:
    """Info icons for the dataframe columns whose name is a glossary label (or starts with one before ':')."""
    import streamlit as st

    known = {**BY_LABEL, **(extra or {})}
    out = {}
    for col in columns:
        base = str(col).split(":")[0].strip()
        if base in known:
            out[col] = st.column_config.Column(help=known[base])
    return out
