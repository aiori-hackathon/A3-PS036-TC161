---
title: "Measuring and Operating SLH-DSA-MTL DNSSEC in a Multi-Zone Hierarchy"
abbrev: "MTL DNSSEC Multi-Zone Bench"
docname: draft-joshi-dnsop-mtl-dnssec-bench-00
category: info
ipr: trust200902
submissiontype: IETF
stand_alone: true
pi: [toc, sortrefs, symrefs]
keyword:
  - DNSSEC
  - MTL
  - SLH-DSA
  - post-quantum
  - benchmark
venue:
  group: dnsop
  type: "Working Group"
  mail: dnsop@ietf.org
author:
  -
    ins: A. Joshi
    name: Akash Joshi
    organization: Cipherops / AIORI Hackathon
    email: TBD
normative: {}
informative:
  RFC4033:
  RFC4034:
  RFC4035:
  RFC6891:
  RFC9276:
  FIPS205:
    title: "FIPS 205: Stateless Hash-Based Digital Signature Standard"
    author:
      - org: NIST
    date: 2024-08
    target: https://csrc.nist.gov/pubs/fips/205/final
  FIPS204:
    title: "FIPS 204: Module-Lattice-Based Digital Signature Standard"
    author:
      - org: NIST
    date: 2024-08
    target: https://csrc.nist.gov/pubs/fips/204/final
  I-D.fregly-dnsop-slh-dsa-mtl-dnssec:
    title: "Merkle Tree Ladder Mode (MTL) Signatures for DNSSEC using SLH-DSA"
    author:
      - ins: A. Fregly
      - ins: J. Harvey
      - ins: B. Kaliski
      - ins: D. Wessels
    date: 2025
    target: https://datatracker.ietf.org/doc/draft-fregly-dnsop-slh-dsa-mtl-dnssec/
  I-D.kaizer-dnsop-ml-dsa-mtl-dnssec:
    title: "Merkle Tree Ladder Mode (MTL) Signatures for DNSSEC using ML-DSA"
    author:
      - ins: A. Kaizer
    date: 2025
    target: https://datatracker.ietf.org/doc/draft-kaizer-dnsop-ml-dsa-mtl-dnssec/
  I-D.hdong-dnsop-ml-dsa-mtl-dnssec-sigtag-ext:
    title: "SigTag Extension for MTL Mode DNSSEC"
    author:
      - ins: H. Dong
    date: 2025
    target: https://datatracker.ietf.org/doc/draft-hdong-dnsop-ml-dsa-mtl-dnssec-sigtag-ext/
  I-D.harvey-cfrg-mtl-mode:
    title: "Merkle Tree Ladder Mode (MTL Mode)"
    author:
      - ins: J. Harvey
      - ins: B. Kaliski
      - ins: A. Fregly
      - ins: M. Zhao
    date: 2025
    target: https://datatracker.ietf.org/doc/draft-harvey-cfrg-mtl-mode/
  VERISIGN-MTL:
    title: "Verisign MTL reference library"
    author:
      - org: Verisign
    target: https://github.com/verisign/MTL
  VERISIGN-LDNS:
    title: "Verisign mtl-mode-ldns"
    author:
      - org: Verisign
    target: https://github.com/Verisign/mtl-mode-ldns
  VERISIGN-NSD:
    title: "Verisign mtl-mode-nsd"
    author:
      - org: Verisign
    target: https://github.com/Verisign/mtl-mode-nsd
  VERISIGN-UNBOUND:
    title: "Verisign mtl-mode-unbound"
    author:
      - org: Verisign
    target: https://github.com/Verisign/mtl-mode-unbound
---

# Abstract

This Informational Internet-Draft reports measured operational findings from a
private multi-zone DNS hierarchy that exercises SLH-DSA-MTL DNSSEC using
Verisign's reference stack (ldns, NSD, Unbound, and the MTL library). It does
not propose a new signature algorithm or change DNSSEC protocol. Relative to
direct SLH-DSA signing, MTL condensed signatures (draft-fregly-06 semantics)
substantially reduce cold-start upstream bytes and UDP truncations under a
seeded 1000-query workload, while warm-cache latency converges across
algorithms. The draft documents lab limitations, a DS/ladder validation
interoperability issue under separate KSK+ZSK, and how draft06 condensed-by-
default behaviour differs from always-full and SigTag evolution paths.

# Status of This Memo

This Internet-Draft is submitted in full conformance with the provisions of
BCP 78 and BCP 79.

Internet-Drafts are working documents of the Internet Engineering Task Force
(IETF). Note that other groups may also distribute working documents as
Internet-Drafts. The list of current Internet-Drafts is at
https://datatracker.ietf.org/drafts/current/.

Internet-Drafts are draft documents valid for a maximum of six months and may
be updated, replaced, or obsoleted by other documents at any time. It is
inappropriate to use Internet-Drafts as reference material or to cite them
other than as "work in progress."

This Internet-Draft will expire six months after publication.

# Copyright Notice

Copyright (c) 2026 IETF Trust and the persons identified as the document
authors. All rights reserved.

# Table of Contents

1. [Introduction](#1-introduction)
2. [Terminology](#2-terminology)
3. [Lab Architecture](#3-lab-architecture)
4. [Methodology](#4-methodology)
5. [Algorithms Compared](#5-algorithms-compared)
6. [Results](#6-results)
7. [Interoperability Findings](#7-interoperability-findings)
8. [Relation to Existing Drafts](#8-relation-to-existing-drafts)
9. [Security Considerations](#9-security-considerations)
10. [IANA Considerations](#10-iana-considerations)
11. [Acknowledgements](#11-acknowledgements)
12. [Informative References](#12-informative-references)
Appendix A. [Pinned Software Versions](#appendix-a-pinned-software-versions)
Appendix B. [Result Artifact Paths](#appendix-b-result-artifact-paths)
Authors' Addresses

# 1. Introduction

DNSSEC ([RFC4033], [RFC4034], [RFC4035]) authenticates DNS data with public-key
signatures. Stateless hash-based signatures standardized as SLH-DSA
([FIPS205]) produce much larger signatures than classical DNSSEC algorithms
such as ECDSA P-256 or Ed25519. Large RRSIGs increase the chance of UDP
truncation ([RFC6891] EDNS buffer limits), TCP fallback, and cold-start
bandwidth cost for validating resolvers.

Merkle Tree Ladder (MTL) mode ([I-D.harvey-cfrg-mtl-mode],
[I-D.fregly-dnsop-slh-dsa-mtl-dnssec]) amortizes one underlying signature over
many RRsets by publishing a signed ladder and short condensed authentication
paths. Whether that amortization helps in a multi-zone hierarchy depends on
resolver ladder caching, EDNS options that request full signatures, zone key
schemes, and network delay—not only on per-RRset signature size tables.

This document records what a Docker-based ecosystem lab measured when comparing
classical DNSSEC algorithms, direct SLH-DSA, and SLH-DSA-MTL across a private
root → TLD → SLD hierarchy using Verisign's MTL-mode reference components.
The intended status is Informational. This is not a Standards Track document
and does not claim RFC status.

The lab lives under a separate `ecosystem/` tree and does not modify the main
Cipherops single-zone live-mode benchmark. Numbers in Section 6 are taken from
the full lab run `eco-default-draft06/20261014-142918` (see Appendix B).

# 2. Terminology

The key words used in this document are descriptive; this Informational draft
does not introduce normative requirements.

* **Condensed signature**: An MTL authentication path without the underlying
  signed ladder, as returned by default under draft-fregly-06 behaviour.
* **Full signature / signed ladder**: The larger MTL form that includes a
  ladder signed by the underlying SLH-DSA key; requested by the resolver with
  the experimental EDNS option code 65050 (`mtl-mode-full`) on the draft06
  stack.
* **Cold pass**: Workload replay after a full Unbound process restart that
  empties message, RRset, key, and MTL ladder caches. A mere `reload` is not
  used for cold starts because it retains the ladder cache.
* **Warm pass**: Immediate second replay of the same workload with caches
  populated.
* **Upstream**: Traffic between the validating Unbound and authoritative NSD
  servers (root, TLD, and SLD hops), excluding the stub↔resolver hop.
* **CSK**: Combined Signing Key (single DNSKEY used as both KSK and ZSK roles
  for signing in this lab).
* **AD**: Authentic Data bit returned to the stub when Unbound successfully
  validates the answer.

# 3. Lab Architecture

## 3.1. Hierarchy and Addressing

The lab builds a private DNS hierarchy on an isolated Docker bridge network
`10.53.0.0/24` with no Internet connectivity:

| Role | Container pattern | Example address |
|---|---|---|
| Root NSD | `eco-root` | 10.53.0.2 |
| TLD NSD | `eco-tld-test`, `eco-tld-lab` | 10.53.0.11, 10.53.0.12 |
| SLD NSD | `eco-sld1`, `eco-sld2` | 10.53.0.21, 10.53.0.22 |
| Validating Unbound | `eco-unbound` | 10.53.0.53 |
| Stub client / controller | `eco-client` | 10.53.0.100 |

Configuration (`configs/lab.yaml`, lab name `eco-default`) uses two TLDs
(`test.`, `lab.`), eight second-level zones distributed round-robin across the
TLDs and two SLD servers, and per-SLD data-name counts cycled from
`{25, 100, 25, 250}`. Zones are signed once and frozen for the run. The stub
queries Unbound only; Unbound uses root hints pointing at the lab root and a
configured trust anchor (root DS).

## 3.2. Software Stack

Authoritative and recursive components are Verisign's MTL-mode forks built
against the Verisign MTL library and liboqs, pinned in `stacks/draft06.env`:

* mtl-mode-ldns (signing / key generation)
* mtl-mode-nsd (authoritative)
* mtl-mode-unbound (validating recursive)
* verisign/MTL and open-quantum-safe/liboqs

Pinned commits for the reported run are listed in Appendix A. The stack is
configured for **draft-fregly-06** behaviour: condensed signatures by default;
full signatures only when the resolver sets EDNS option 65050.

## 3.3. Key Scheme

All scenarios in the reported run use a **CSK** key scheme for every zone.
Separate KSK+ZSK was rejected for cross-algorithm fairness on this Unbound
build because DS RRsets signed by a parent ZSK fail MTL validation (Section 7.1).
Classical and plain SLH-DSA scenarios also use CSK so that cache and query
patterns remain comparable.

# 4. Methodology

## 4.1. Workload

Each pass replays a seeded workload of 1000 stub queries (`seed: 20261005`):

* approximately 10% distinct NXDOMAIN names;
* Zipf zone and in-zone name popularity (`zone_zipf_s=1.0`, `name_zipf_s=1.1`);
* stub does not set DO; it expects Unbound to validate and return AD;
* query timeout 5 s.

## 4.2. Factors

The full matrix crosses:

* **Scenarios**: `ecdsa`, `ed25519`, `slh-dsa`, `mtl`, `mixed-mtl` (Section 5);
* **UDP limits**: Unbound `edns-buffer-size` 1232 and 4096 toward authorities;
* **Network profiles**: `lan` (no added delay) and `wan` (Linux `tc netem` on
  Unbound's eth0: client 2 ms, root 20 ms, TLD 35 ms, SLD 50 ms one-way toward
  those peers; 0% loss);
* **Trials**: 3; each trial runs cold then warm.

NSD servers advertise a large EDNS size; the resolver's buffer size is the
experimental control for truncation toward authorities.

## 4.3. Cold Versus Warm

Before each trial, the controller stops Unbound and waits for a new process
PID (`flush_resolver`). That restart clears the MTL ladder cache. The cold
pass then runs; the warm pass follows immediately without flushing.

## 4.4. Metrics

Three independent sources are recorded per pass:

1. **pcap** on Unbound eth0 (`tcpdump`, DNS port 53), later split into hops
   (client / root / TLD / SLD) for bytes, queries, TC bits, and TCP SYNs;
2. **client per-query log** (latency, rcode, AD, timeouts);
3. **unbound-control** extended statistics after the pass.

Summary tables aggregate the three trials with bootstrap confidence intervals
where applicable (`summary.csv`). Upstream byte totals are pcap payload sums
on root+TLD+SLD hops.

## 4.5. Lab Limitations (Non-Exhaustive)

Readers MUST treat results as lab measurements, not Internet-wide predictions:

* **Docker / WSL2 host**: containers share a virtualized host; cpusets pin
  client, Unbound, and NSD processes but do not model carrier networks.
* **Synthetic delay**: WAN delays are `tc netem` one-way delays on the
  resolver's egress toward fixed peer IPs; there is no jitter model in the
  reported profiles and no packet loss in `lan`/`wan`.
* **Frozen zones**: zones are pre-signed; there is no online resigning, key
  rollover, or AXFR/IXFR churn during measurement.
* **CSK only**: results do not characterize production KSK/ZSK splits for MTL
  on this Unbound build (see Section 7.1).
* **Stub model**: a single sequential stub; no concurrent client fan-out.
* **draft06 semantics only**: size wins attributed to MTL here are specifically
  condensed-by-default plus opportunistic full fetches, not always-full MTL
  encodings.

# 5. Algorithms Compared

| Scenario | Root | TLD | SLD | Notes |
|---|---|---|---|---|
| `ecdsa` | ECDSAP256SHA256 | same | same | Classical baseline |
| `ed25519` | ED25519 | same | same | Classical baseline |
| `slh-dsa` | SLH-DSA-SHA2-128s (alg 246) | same | same | Direct PQ signatures |
| `mtl` | SLH-DSA-SHA2-128s-MTL-SHA2-128 (alg 130) | same | same | draft06 MTL |
| `mixed-mtl` | ECDSA P-256 | ECDSA P-256 | MTL (alg 130) | Hybrid deployment sketch |

ML-DSA ([FIPS204]) MTL variants ([I-D.kaizer-dnsop-ml-dsa-mtl-dnssec]) are out
of scope for this run; the lab signs with SLH-DSA-MTL as implemented by the
pinned Verisign stack.

# 6. Results

Unless noted, figures are **median-of-trials** values from
`summary.csv` for the full run `20261014-142918`. Upstream bytes and truncation
counts are identical across trials for most classical/SLH-DSA cells; MTL cells
show small trial-to-trial variation in full-ladder fetch patterns (confidence
intervals in the CSV).

## 6.1. WAN, UDP 1232, Cold Start (Primary Comparison)

| Scenario | Upstream bytes | Upstream queries | UDP truncations (TC) | Sum client latency (s) | Client p95 (ms) | Median (ms) |
|---|---:|---:|---:|---:|---:|---:|
| ecdsa | 347,939 | 589 | 35 | 33.76 | 106.1 | 3.58 |
| ed25519 | 347,760 | 589 | 35 | 33.83 | 106.6 | 3.60 |
| mtl | 767,566 | 701 | 114 | 38.63 | 106.9 | 3.62 |
| mixed-mtl | 717,486 | 692 | 108 | 38.59 | 106.8 | 3.65 |
| slh-dsa | 12,184,600 | 1,108 | 554 | 63.31 | 212.3 | 3.63 |

Observations:

* Direct SLH-DSA moves about **35×** more upstream bytes than ECDSA and about
  **16×** more than MTL on this workload, with far more truncations and roughly
  1.9× the summed client latency of ECDSA.
* MTL is heavier than classical DNSSEC on cold start (about **2.2×** ECDSA
  upstream bytes) but remains far closer to classical than to naked SLH-DSA.
* `mixed-mtl` (ECDSA above the cut, MTL at SLD) is within a few percent of
  all-MTL upstream cost, because most answer bytes in this Zipf workload come
  from SLD authorities (`hop_sld_bytes` dominates).
* Client **median** latency is dominated by the WAN shaping (~3.6 ms) for all
  algorithms; algorithm cost appears mainly in the **tail** (p95) and in
  **summed** latency / wall time under many truncations and TCP fallbacks.
* Validation: **100% AD**, **0 SERVFAIL**, **0 timeouts**, and **0 pcap
  capture drops** across the reported cells of this run.

## 6.2. Warm Pass (WAN, UDP 1232)

After the cold pass, warm upstream bytes are **0** for every scenario (cache
hits). Client medians cluster near **2.9 ms** (ecdsa 2.87 ms; ed25519 2.89 ms;
slh-dsa 2.91 ms; mtl 2.95 ms; mixed-mtl 2.91 ms), with p95 near 8 ms. Warm
behaviour therefore does not distinguish algorithms under this stub workload;
the operational contrast is cold-start fetch and validation cost.

## 6.3. UDP 4096 and LAN

Raising the resolver EDNS buffer to 4096 removes classical truncations (ecdsa/
ed25519 TC count 0) and reduces MTL TC counts (e.g., WAN cold mtl: 22 TC vs
114 at 1232) but does **not** shrink SLH-DSA upstream bytes in this lab: SLH-
DSA still records 12,184,600 upstream bytes and 554 truncations at both 1232
and 4096 on WAN cold, consistent with answers that remain larger than even a
4096-byte UDP payload and fall back to TCP. LAN profiles show the same byte
and truncation structure with much smaller summed latencies when delays are
zeroed.

## 6.4. Interpretation Bounds

These measurements support an operational claim of the form: *under draft06
condensed-default MTL, a validating resolver cold-starting over a multi-zone
hierarchy can authenticate a 1000-query Zipf workload with upstream cost far
below direct SLH-DSA and moderately above ECDSA/Ed25519*. They do not by
themselves prove Internet deployment readiness, rollover safety, or behaviour
under always-full MTL encodings.

# 7. Interoperability Findings

## 7.1. DS Validation With Separate KSK and ZSK (draft06 Unbound)

On Verisign mtl-mode-unbound at commit
`fab43ad682301633fa119158ecd22924f042cb26`, a parent zone signed with separate
KSK and ZSK fails to validate a child DS RRset whose RRSIG is a condensed MTL
signature made by the parent ZSK.

Mechanism (abbreviated; full write-up in the lab report
`ecosystem/reports/verisign-unbound-ds-ladder-issue.md`):

1. Unbound validates the parent DNSKEY RRset and caches the **KSK** ladder.
2. The DS RRSIG verifies as `sec_status_extend` (needs an updated ladder for
   the ZSK), but `ds_response_to_ke()` treats every non-`sec_status_secure`
   result as bogus.
3. Unlike DNSKEY and ordinary answer paths, the DS path does not call
   `request_updated_ladder()` / EDNS 65050, so the chain becomes SERVFAIL.

Workaround used for all scenarios in this paper: sign every zone with a single
**CSK**, so the ladder obtained for DNSKEY also covers DS RRSIGs. The same
hierarchy validates with ECDSA and with plain SLH-DSA (algorithm 246) under
KSK+ZSK; the failure mode is specific to MTL condensed DS handling on this
build.

This is reported as an interoperability defect in the examined reference
resolver, not as a protocol prohibition on KSK/ZSK.

## 7.2. Protocol Evolution: draft06 Versus Always-Full Versus SigTag

Three related design directions affect size and round-trips:

1. **draft-fregly-06** ([I-D.fregly-dnsop-slh-dsa-mtl-dnssec]): condensed by
   default; full signed ladder on explicit EDNS request (code 65050 in the
   Verisign draft06 stack). **The size advantage measured in Section 6 is
   specifically this semantics.**
2. **draft-kaizer-02** ([I-D.kaizer-dnsop-ml-dsa-mtl-dnssec]): always-full MTL
   signature presentation. Operators should expect materially different
   cold-start byte counts; this lab does not measure that encoding.
3. **SigTag** ([I-D.hdong-dnsop-ml-dsa-mtl-dnssec-sigtag-ext]): a path to omit
   full signatures when the client already knows the ladder, recovering size
   savings when defaults move toward always-full.

A SigTag-aware client/resolver implementation as described in that draft was
**not found in the public Verisign mtl-mode-unbound branches examined at
commit `fab43ad682301633fa119158ecd22924f042cb26`** (IETF-125-Hackathon tip
used for this lab). That statement is limited to the branches and commit
inspected; it is not a proof that no private or later branch exists.

## 7.3. Multiple Full Root Signature Fetches on Cold Start

Packet walks of MTL cold starts show multiple EDNS-65050 full-signature
exchanges toward the root early in recursion, including parallel fetches for
`. DNSKEY` and trust-anchor signaling names such as `_ta-….` (NXDOMAIN proofs
that also carry large FULL RRSIGs when upgraded). Example excerpts appear in
lab walk logs under `ecosystem/work/_walk.txt`.

This document reports that pattern as an **observation** useful for resolver
tuning discussions. It does not assert a proven defect without a controlled
root-cause patch; duplicate full fetches may reflect independent validation
tasks (DNSKEY vs. trust-anchor probe) rather than a single missed cache lookup.

# 8. Relation to Existing Drafts

| Document | Relation |
|---|---|
| [I-D.fregly-dnsop-slh-dsa-mtl-dnssec] (-06) | Protocol semantics exercised by the pinned stack; primary reference for condensed/full behaviour measured here |
| [I-D.harvey-cfrg-mtl-mode] | Underlying MTL mode construction |
| [I-D.kaizer-dnsop-ml-dsa-mtl-dnssec] (-02) | Always-full contrast; ML-DSA algorithm family not measured |
| [I-D.hdong-dnsop-ml-dsa-mtl-dnssec-sigtag-ext] | Size-savings path when clients already hold ladders |
| [RFC4033]–[RFC4035], [RFC6891] | DNSSEC and EDNS baseline |
| [FIPS205], [FIPS204] | Underlying PQ signature standards (SLH-DSA used; ML-DSA cited for context only) |

This draft intentionally invents **no** new cryptographic primitive and
**no** new DNS wire format. It is measurement and operations experience for
dnsop readers evaluating MTL DNSSEC deployments.

# 9. Security Considerations

DNSSEC security properties remain those of the algorithms and protocols cited
above. Lab results about bytes and latency do not imply cryptographic strength
comparisons beyond the known designs of ECDSA, Ed25519, SLH-DSA, and MTL mode.

Operational cautions specific to this work:

* Using CSK to work around the DS/ladder bug changes key-compromise impact
  relative to a split KSK/ZSK; it is a lab equalizer, not a deployment
  recommendation.
* Experimental EDNS option 65050 is stack-specific scaffolding for draft06
  full-ladder requests; production code points require IETF/IANA process.
* Synthetic delay and frozen zones can hide failure modes (loss, middlebox
  EDNS filtering, rollover races) that matter on the public Internet.
* Validating resolvers that mishandle `sec_status_extend` on DS responses can
  cause availability failures (SERVFAIL) that look like outages rather than
  crypto breaks.

# 10. IANA Considerations

This document has no IANA actions.

# 11. Acknowledgements

This work was produced in the context of the AIORI Hackathon (Cipherops /
team A3-PS036-TC161) and relies on Verisign's publicly available MTL-mode
reference implementations and the open-quantum-safe liboqs project. Reviewers
and hackathon mentors are thanked for discussion of condensed versus full
MTL encodings and resolver ladder caching. Any errors in measurement or
interpretation are the author's.

# 12. Informative References

* [RFC4033] Arends, R., et al., "DNS Security Introduction and Requirements",
  RFC 4033, March 2005.
* [RFC4034] Arends, R., et al., "Resource Records for the DNS Security
  Extensions", RFC 4034, March 2005.
* [RFC4035] Arends, R., et al., "Protocol Modifications for the DNS Security
  Extensions", RFC 4035, March 2005.
* [RFC6891] Damas, J., Graff, M., and P. Vixie, "Extension Mechanisms for
  DNS (EDNS(0))", RFC 6891, April 2013.
* [RFC9276] Lewis, E. and K. Larson, "Guidance for NSEC3 Parameter Settings",
  RFC 9276, August 2022.
* [FIPS205] NIST, "Stateless Hash-Based Digital Signature Standard", FIPS 205,
  August 2024.
* [FIPS204] NIST, "Module-Lattice-Based Digital Signature Standard", FIPS 204,
  August 2024.
* [I-D.fregly-dnsop-slh-dsa-mtl-dnssec] Fregly, A., et al., "Merkle Tree Ladder
  Mode (MTL) Signatures for DNSSEC using SLH-DSA", Work in Progress,
  draft-fregly-dnsop-slh-dsa-mtl-dnssec-06.
* [I-D.kaizer-dnsop-ml-dsa-mtl-dnssec] Kaizer, A., et al., "Merkle Tree Ladder
  Mode (MTL) Signatures for DNSSEC using ML-DSA", Work in Progress,
  draft-kaizer-dnsop-ml-dsa-mtl-dnssec-02.
* [I-D.hdong-dnsop-ml-dsa-mtl-dnssec-sigtag-ext] Dong, H., et al., "SigTag
  Extension for MTL Mode DNSSEC", Work in Progress,
  draft-hdong-dnsop-ml-dsa-mtl-dnssec-sigtag-ext-00.
* [I-D.harvey-cfrg-mtl-mode] Harvey, J., et al., "Merkle Tree Ladder Mode
  (MTL Mode)", Work in Progress, draft-harvey-cfrg-mtl-mode.
* [VERISIGN-MTL] https://github.com/verisign/MTL
* [VERISIGN-LDNS] https://github.com/Verisign/mtl-mode-ldns
* [VERISIGN-NSD] https://github.com/Verisign/mtl-mode-nsd
* [VERISIGN-UNBOUND] https://github.com/Verisign/mtl-mode-unbound

# Appendix A. Pinned Software Versions

From `stacks/draft06.env` and `run.json` for `20261014-142918`:

| Component | Pin |
|---|---|
| Stack label | draft06 |
| liboqs | tag 0.14.0 |
| MTL | `93b05ef73dab53b953cc4e262401a2ce9bc9e431` (v1.2.0) |
| mtl-mode-ldns | `64906672ae0d80ae03933240daab27e19ceccf70` |
| mtl-mode-nsd | `09ebb4e551b5029f7ddf7e381b7bb8a966fa1320` (`--enable-draft-mtl`) |
| mtl-mode-unbound | `fab43ad682301633fa119158ecd22924f042cb26` |
| Host capture environment | Docker Desktop 29.7.2, Linux VM kernel 6.18.x (WSL2) |

# Appendix B. Result Artifact Paths

Relative to the Cipherops repository root:

* `ecosystem/results/eco-default-draft06/20261014-142918/summary.csv` —
  trial-aggregated metrics used in Section 6;
* `ecosystem/results/eco-default-draft06/20261014-142918/passes.csv` —
  per-trial cold/warm rows including `capture_dropped`;
* `ecosystem/results/eco-default-draft06/20261014-142918/run.json` —
  full config, manifests, and image digests;
* `ecosystem/reports/verisign-unbound-ds-ladder-issue.md` — DS/ladder defect
  report corresponding to Section 7.1.

# Authors' Addresses

Akash Joshi  
Cipherops / AIORI Hackathon  
Email: TBD
