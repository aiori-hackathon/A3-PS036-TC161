# Methodology

This document describes exactly what Cipherops measures, how, and which
numbers are estimates. It is the reference for every table in a run's
`report.md`.

## 1. Scope (PS-036)

- **Algorithm.** One supported PQC signature algorithm: **SLH-DSA-SHA2-128s**
  (FIPS 205, "pure" mode). It was chosen because its signatures are large
  (7,856 bytes) and slow to produce, the case MTL mode is designed for, and
  because a DNSSEC draft for SLH-DSA with MTL exists. [Design decisions](design-decisions.md)
  §1 gives the full rationale.
- **Reference implementation.** The **Verisign MTL C library**
  (`github.com/verisign/MTL`, pinned commit in the Dockerfile), the reference
  implementation of draft-harvey-cfrg-mtl-mode, selected with the mentor's
  approval. Cipherops
  contains no cryptographic code of its own. The harness calls only:
  - the library's public API (`mtllib.h`) for MTL mode
  - liboqs `OQS_SIG` for direct SLH-DSA
  - OpenSSL EVP for the classical reference
- **No resolver or server.** Transmitted bytes are computed on real DNS
  messages encoded by dnspython. No Internet deployment, no new cryptography
  and no security claims.

## 2. Pinned environment

| Component | Pin |
|---|---|
| Base image | `fedora-minimal:42` |
| OpenSSL, liboqs, MTL library | from the stack profile `ecosystem/stacks/<profile>.env` (`CIPHEROPS_STACK`). Offline default `latest`: OpenSSL 3.6.0, liboqs 0.16.0 (FIPS 205 `SLH_DSA_PURE_SHA2_128S`), MTL `7cef019`. The live lab is built from the same files and defaults to `draft06`. The MTL source is unmodified; it is compiled with `make CFLAGS="-fPIC -Wall -Wextra -O2"` |
| Python dependencies | exact versions in `requirements.txt` |
| CPU | one core via Docker `cpuset` (default core 2); `mem_limit` 4 GB; no network |

**Compiler optimisation.** Upstream `configure.ac` always forces the debug
flags `-g -O0`. liboqs, which does the SLH-DSA work in both PQC modes, is a
CMake Release build. To compare like with like, the MTL library is compiled at
`-O2` by passing `CFLAGS` to `make`; the source is untouched and its own test
suite is re-run on this build by the go/no-go gate. To reproduce the as-shipped
`-O0` build for an ablation, rebuild with
`docker compose --env-file ecosystem/stacks/latest.env -f docker/build.yml build bench --build-arg MTL_MAKE_CFLAGS=upstream`.
The flags actually used are recorded in `environment.json`.

Every run writes `environment.json` with:

- CPU model, affinity, governor and memory
- kernel and OS release
- compiler and library versions, and the MTL compile flags
- image ID, git SHA and the harness binary's sha256

## 3. Go/no-go gate

`cipherops preflight` runs before any measurement. The run is **deferred**
(exit code 3, `NO-GO (defer per PS-036)`) unless all of these pass:

1. The reference library, headers and liboqs are installed.
2. The reference implementation's own unit tests and bundled test vectors
   pass (`mtltest`).
3. The reference command-line tools round-trip with the chosen algorithm:
   - `mtlkeygen` and `mtlsign` on 16 messages
   - `mtlverify` passes for every condensed signature (with the ladder) and
     every full signature
   - a tampered message is rejected
4. Every harness mode signs and verifies a small zone, and rejects each
   negative case: a changed message, a damaged or truncated signature, a
   signature checked against a different message, a different public key, and
   (MTL) a damaged ladder, a changed series id and a full signature with a
   damaged ladder. ECDSA is also verified in the r||s form that goes on the wire.
5. The bytes the harness wrote are verified by a different program: MTL
   signatures by the reference tool `mtlverify`, and the non-MTL SLH-DSA
   signatures by OpenSSL's SLH-DSA provider (a different implementation from
   liboqs). A changed message must be rejected by both.

The bundled known-answer vectors cover the scheme named in
`mtltest_test_vectors.h` (ML-DSA), not SLH-DSA. Check 5 is an interoperability
check between two implementations. It is not a known-answer test against
published SLH-DSA vectors, and none are claimed.

Observed library behaviour: the reference library does not authenticate the
first two bytes of a condensed signature (the type byte and the byte after it);
flipping either is accepted. Everything from the series id onward is
authenticated. Condensed verification also trusts a ladder passed in as
pre-verified, so a wrong-key test must use the signed ladder or a full
signature. The harness records the first point as an observation.

## 4. Workloads

Each dataset is a deterministic synthetic zone, `bench.example.`, generated
from a fixed seed:

| Record class | Share | Content |
|---|---|---|
| small | 60% | a single A or AAAA |
| medium | 30% | 3 MX or 4 AAAA |
| large | 10% | one TXT of 1,024–2,048 characters |
| apex | 4 RRsets | SOA, NS, DNSKEY, NSEC3PARAM |
| nsec3 | one per name | NSEC3 chain, 0 iterations, no salt (RFC 9276) |

Each dataset therefore has 2·names + 5 RRsets, and every RRset is one signing
message. Three dataset sizes are used (default 50, 500 and 5,000 names).

The signing input for each RRset follows RFC 4034 section 3.1.8.1: the RRSIG
RDATA without the signature field, followed by the canonical RRset. It is
produced by dnspython, and the test suite checks that dnspython's standard
validator accepts signatures over these exact bytes. Inception and expiration
are fixed so inputs are byte-identical across runs.

The workloads are equivalent across modes. Every mode signs the same RRsets.
Only the RRSIG algorithm number, key tag and DNSKEY differ, because those are
part of the signed data by definition.

## 5. Modes compared

| Mode | Library call path | DNSSEC algorithm number |
|---|---|---|
| `slh-dsa-mtl` | `mtllib_*` (SLH-DSA-SHA2-128s-MTL-SHA2-128) | 50 (lab placeholder) |
| `slh-dsa` | `OQS_SIG` SLH_DSA_PURE_SHA2_128S | 249 (lab placeholder) |
| `ed25519` | OpenSSL EVP | 15 |
| `ecdsa-p256` | OpenSSL EVP (r‖s wire form, RFC 6605) | 13 |
| `rsa-2048` | OpenSSL EVP (RSA/SHA-256) | 8 |

The placeholder code points (algorithm numbers and the EDNS option 65001 for
`mtl-mode-full`) are not IANA-assigned. They affect only a few bytes.

## 6. Timing measurements (directly measured)

- **One process per trial.** Modes run in a seeded random order inside each
  trial. Before the measured trials, each mode runs once on the smallest
  dataset (`warmup_trials`) and the result is discarded. The clock is
  `CLOCK_MONOTONIC_RAW` around each library call; buffer allocation is kept
  outside the timed region.
- **MTL signing, whole zone.** The sum of the following, all measured:
  - `mtllib_sign_append` per RRset
  - one `mtllib_sign_get_signed_ladder`, which is the single SLH-DSA signature
  - `mtllib_sign_get_condensed_sig` per RRset
- **MTL verification, warm.** One `mtllib_verify_signed_ladder`, plus
  `mtllib_verify` of every condensed signature against the cached ladder. The
  verifier is built from the exported public key only.
- **MTL verification, cold.** Full signatures (condensed plus ladder) verified
  with no cached ladder, on a fixed sample (`full_verify_sample`).
- **Direct SLH-DSA and classical.** One sign and one verify per RRset.
- **Optional cap on direct signing.** One SLH-DSA-SHA2-128s signature takes
  about 0.6 s on one core. When `direct_sign_limit` caps direct signing for a
  dataset (by default only the large one, at 1,000 RRsets):
  - an evenly spaced sample across canonical order is signed and verified, so
    every record class is covered
  - the manifest's `signed_in_trials` column records which RRsets were in the
    sample
  - zone totals are reported as `*_extrapolated_ns` (per-RRset median ×
    RRsets) and always labelled as extrapolations
  - for the byte analysis, the measured 7,856-byte signatures are reused
    across RRsets; SLH-DSA signatures have a fixed length, so this does not
    change any byte count.
- **Statistics.** Median, mean, standard deviation, 5th/95th percentile and
  min/max across trials, plus a confidence interval of the median.
  See section 6.2.

### 6.1 Which verification numbers can be compared

MTL verification can be timed in two situations and the report keeps them apart:

| Quantity | What it includes | Compare it with |
|---|---|---|
| MTL verify, **warm** (whole zone) | validating the signed ladder once, plus every condensed signature against that already-validated ladder | direct SLH-DSA verifying every RRset once (also a whole-zone total) |
| MTL verify, **cold** (per answer) | one full signature: the condensed signature **and** validating the signed ladder, with no cached ladder | direct SLH-DSA verifying one RRset |

A condensed signature is checked against a ladder that the library is told
has already been validated. The ladder's own signature is therefore never
re-checked per answer, which is the intended use of MTL, but it also means a
condensed-verify time must never be compared with a direct verify without the
ladder cost. The warm whole-zone total adds that cost once; the cold figure
includes it every time. Both are reported.

### 6.2 Statistics

Repeated trials are few (the default full run uses 10, the quick run 3). A
bootstrap of a median from so few values is not a valid 95% interval, so the
report uses an exact, distribution-free interval from order statistics
(`stats.median_ci`): the widest pair of sorted trial values that still
covers the true median with at least 95% probability, or the full range if
there are too few trials. Its coverage depends only on the number of trials:

| Trials | Coverage of the full range |
|---|---|
| 3 | 75% |
| 4 | 87.5% |
| 5 | 93.8% |
| 6 | 96.9% |
| 7 | 98.4% |
| 10 | 97.9% (interval from the 2nd to the 9th sorted value) |

With 5 trials the bracketed interval is therefore a 94% interval, not 95%,
and the report says so (`stats.ci_label`). Use at least 6 trials if a 95%
interval is required.

**Paired ratios.** Modes are interleaved inside each trial, so the trials are
paired: trial 3 of direct SLH-DSA and trial 3 of MTL saw the same machine
state. A statement such as "N times faster" is therefore taken from the ratio
computed **per trial** (`stats.paired_ratio`, `results.paired_ratio_table`).
The report gives the median of those ratios with the same order-statistic
interval. A slow trial that slows both modes alike leaves the ratio unchanged,
so this interval is tighter and more honest than dividing two medians. When
direct SLH-DSA signing is capped, the ratio uses its extrapolated whole-zone
total and the table says so.

### 6.3 The measurement's own noise

Two controls say how much of a difference is the measurement itself:

- **A/A control** (`control:` in the config, `ed25519` by default). That mode
  runs a second time as an identical copy, `ed25519-aa`, on the same input in
  a random position inside every trial. Both do the same work, so their
  per-trial ratio should be 1. The widest deviation from 1 inside the
  interval is the **noise band** for that dataset and metric
  (`results.noise_floor_table`). A paired ratio between two modes is marked
  "clearly outside" only if its whole interval lies outside the band
  (`results.exceeds_noise`); otherwise the report says it is not
  distinguishable from noise and it must not be claimed as a difference. The
  copy's rows go to `control.csv` and never enter the byte or cache analysis.
  Ed25519 is chosen because it is the fastest mode, where clock effects and
  background load show most.
- **Timer calibration** (`mtlbench calibrate`, run at the start of every
  offline run, stored under `timer` in `environment.json`). It times an empty
  region 200,000 times, which gives the cost of the clock and the smallest
  step it was seen to take. It then times the two fastest operations, Ed25519
  verification and one condensed MTL verification, both one call at a time
  (what the benchmark does) and as 1,000 calls in one region divided by
  1,000. If the two agree and an empty timed region is a small share of the
  operation (limit 5%), per-call timing is reliable for those operations. The
  report tabulates the result and warns if it is not.

The band describes the noise of one machine under the conditions of that run.
With few trials it is itself uncertain (see the coverage table above).

### 6.4 Evolving zone: MTL on a series that keeps growing

The dashboard shows this experiment as **Compare → Zone updates**, in operations terms (records changed per update, re-sign time, stale resolver cache).

Signing a whole zone once is MTL's best case. A real zone changes, so the experiment in `src/cipherops/evolution.py`
(`mtlbench evolve` and `evolve-direct`) starts from each dataset, then runs rounds in which **k** RRsets change
(`evolution:` in the config: `k`, `rounds`, `ages`). All of it is measured with the reference library and written
to `evolution.csv` and `evolution_direct.csv` with `kind = measured`.

- **Update model.** A changed RRset is the same-size signing input with its last 8 bytes replaced by (round, index),
  chosen uniformly at random from the zone with a seeded generator (the same RRsets in every trial). The cost of the
  update is real; its content is synthetic.
- **MTL round.** Append the k changed leaves (timed per call), then sign **one** new ladder (the single SLH-DSA
  signature). Nothing is re-signed for unchanged RRsets. Old leaves never leave the series.
- **Direct round.** The baseline signs and verifies each of the k changed RRsets on its own. It does not depend on the
  dataset size, so it runs once per trial; it is the expensive part of the run (sum of `k × rounds` signatures at
  about 0.6 s each, per trial).
- **Cached ladders.** After every round, every RRset of the current zone gets a fresh condensed signature from the
  signer, and the library verifies it against the signed ladder from `age` rounds earlier (`mtllib_verify` with that
  ladder). An RRset whose leaf is older than that ladder must pass; a leaf appended after it has no rung in it and
  must fail. Both are compared with the leaf index, so a mismatch fails the run (`leaf_index_mismatch`), and a
  tampered message is rejected. This is the library's own answer, not a model.
- **Published signatures.** Separately, the condensed signature issued when a leaf was created is verified against
  the **newest** ladder. When rungs merge as the series grows, the earlier path no longer ends at a rung of the
  new ladder, so the signature must be reissued. This measures how much of a published zone that is.
- **What is derived.** Effective bytes per answer (measured condensed size plus the measured uncovered share times a
  full signature) are labelled *derived*: it is an upper bound, because a real resolver refreshes its ladder once and
  then covers the rest.
- **Expected mixed result.** At k = 1 MTL still signs one ladder per round, the same SLH-DSA signature direct signing
  needs for one changed RRset, so it does not win on signing time there; its advantage is bytes and verification. The
  advantage in signing grows with k.
- **Limits.** One resolver and one signer; uniform random changes, not a real update trace; changes assumed independent
  of lookup popularity, so the share of lookups affected equals the share of RRsets changed in expectation; one ladder
  signed per round. Real change patterns (bursts, re-signing before RRSIG expiry) are not replayed.

## 7. Transmitted bytes (directly measured at the DNS message level)

For every positive answer, the DNSKEY answer and a sample of NXDOMAIN names,
dnspython builds the real response message (with name compression), carrying
the real signature bytes from trial 0:

- **MTL RRSIG.** The signature field is `MTL-Type || condensed signature`, or
  for a full signature `MTL-Type || condensed || signed ladder`
  (draft-fregly).
- **NXDOMAIN.** The SOA and up to 3 NSEC3 records, each with its RRSIG.

We measure query, response and truncated (TC=1) response sizes, and whether
each response fits EDNS buffers of **1232** and **4096** bytes.

**Workload accounting.** A Zipf-distributed lookup sequence is summed under
these stated assumptions:

1. Cached answers are not re-sent within TTL.
2. Responses larger than the buffer cost a UDP query, a TC=1 reply and a
   TCP retry (each TCP message adds a 2-byte length prefix).
3. An MTL verifier starts without a ladder. It fetches one full signature
   over TCP with `mtl-mode-full`, then verifies every later condensed
   signature against that ladder.

**Analytical only:** IP fragment, TCP segment, packet and round-trip counts
are header arithmetic, labelled `*_est`.

### 7.1 Cross-check against a real authoritative server (optional)

`cipherops crosscheck --run DIR`:

1. Writes the largest zone as a signed zone file, using the RRSIGs exported
   in `artifacts/`.
2. Serves it with an unmodified NSD on the container's loopback interface.
3. For sampled positive, DNSKEY and NXDOMAIN queries, compares NSD's
   response sizes (TCP, and UDP at 4096 bytes) with the model, and NSD's TC
   bit at 1232 bytes with the model's fit decision.

This is an existing server used only as a byte-count oracle, not a resolver
implementation. It does not validate signatures. MTL full-signature fetches
use an EDNS option that NSD does not implement, so only condensed responses
are cross-checked. Results go to `crosscheck.csv` and Appendix D of the
report.

## 8. Cache usage (measured and analytical kept separate)

| Quantity | Measured | Analytical |
|---|---|---|
| Resolver cache | Responses parsed from wire and stored in dnspython's `LRUCache`. We record the Python heap retained (tracemalloc), plus exact counts of signature, wire and ladder bytes held | per-RRSIG maximum signature size × count, plus the maximum ladder (draft-harvey section 12) |
| Signer state | resident-set growth of the reference library while appending the zone | (2N−1)·n node hashes + N·n randomizers |

Every row has a `kind` column (`measured` / `analytical`). The report never
mixes the two in one figure.

## 9. Correctness gates (every trial)

A trial counts only if:

- every signature verifies
- tampered message, signature and ladder inputs are rejected
- the harness exits with code 0

Measured MTL sizes are compared against the section 12 maximum formulas in
`sizes.csv`.

## 10. Live lab (second measurement path)

The dashboard calls this path **Simulated (Verisign stack)**.

`dashboard/live_lab` and `ecosystem/` run a multi-zone hierarchy (root, one
TLD, leaf zones) with unmodified NSD servers and a validating Unbound, so the
*resolver* behaviour of MTL can be observed. It is a separate pinned software
stack from the offline harness (see [scope and limits](scope-and-limits.md#software-stacks)) and is used for
different questions:

| Question | Offline harness | Live lab |
|---|---|---|
| Signing and verification cost per algorithm | measured per call, dedicated core | signing time of a full zone measured once; repeated on sample zones and extrapolated (labelled *estimated*) |
| Transmitted bytes | computed on real DNS messages | measured from packet captures per hop |
| Cache use | dnspython cache (measured) and formulas (analytical) | Unbound's own `mem.cache.*` counters (measured), formulas kept separate |

Rules the live-lab benchmark follows:

- **Dataset sizes.** The benchmark suite builds the same hierarchy for each
  size (one TLD, up to four leaf zones on one server) and changes only the
  number of names.
- **Cold and warm passes.** Each trial flushes the resolver, replays the seeded
  workload once (cold) and again (warm). One whole trial per scenario, UDP
  limit and network profile is run first and discarded; signing trials also
  start with a discarded warm-up.
- **Failed lookups are not data.** Latency is taken over answered lookups
  (NOERROR and NXDOMAIN) only. A pass with any SERVFAIL, timeout or other
  failure is marked invalid and left out of every summary figure; its
  failures stay counted in `summary.csv` and are shown as warnings in the
  dashboard. A scenario the resolver cannot validate therefore shows no
  numbers rather than fast-looking ones.
- **Provenance.** `suite.json` records the git commit (and whether the tree
  was modified), CPU model, memory, kernel, CPU governor, Docker version,
  the stack's pinned component versions and the image IDs used.
- **No CPU isolation.** Unlike the offline harness, lab containers share all
  host CPUs, and the host governor is recorded but not changed. Treat live-lab
  timings as indicative; use byte counts and relative comparisons.
- **Stack limitation.** On the `latest` stack, the lab's Unbound answers
  SERVFAIL for plain (non-MTL) SLH-DSA while ldns accepts the same zone, and
  `draft06` validates it. The MTL versus non-MTL live comparison is therefore
  run on `draft06`. The Unbound binary on `latest` names only the SPHINCS+
  algorithm that liboqs 0.16 removed, which strongly indicates an upstream
  mismatch; it is not confirmed from source and has not been reported.

## 11. Limitations

- **Single-machine CPU timing.** It reflects this hardware and these library
  builds. It is not a statement about optimised implementations.
- **Docker Desktop (WSL2) pinning.** `cpuset` pins to one *virtual* CPU,
  which Windows may move between physical cores, including efficiency cores
  on hybrid CPUs. Expect wider confidence intervals than on bare-metal Linux
  with a fixed `performance` governor; `environment.json` records the kernel,
  so this case is identifiable.
- **Transport model, not a resolver.** Real resolvers differ in retry,
  caching and EDNS behaviour. Response sizes can be cross-checked against
  NSD (section 7.1). Resolver-side behaviour is not emulated.
- **Placeholder code points.** The DNSSEC algorithm numbers for MTL and
  SLH-DSA are not assigned.
- **Fixture coverage.** The reference library's bundled known-answer vectors
  cover a different scheme than SLH-DSA-SHA2-128s-MTL (see section 3). The
  SLH-DSA check is cross-implementation agreement (OpenSSL), not published
  vectors.
- **Profiles.** Offline results are FIPS 205 SLH-DSA (`latest`). Live-lab results
  on `draft06` are for SPHINCS+-SHA2-128s-simple, the pre-standard form with
  the same sizes, so byte and cache results carry over but timings are not
  substituted for the offline ones. Each run records its profile. Why the
  defaults differ: `docs/design-decisions.md`.
- **Evolving zone.** Synthetic same-size updates, uniform random, one signer and one resolver (section 6.4).
- **Few trials.** The full offline run uses 10 trials; the quick run and the live
  benchmark use fewer (3 and 5), so their intervals are wide (section 6.2).
- **No security claims.** None are made or implied.
