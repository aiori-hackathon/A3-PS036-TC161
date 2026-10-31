# Design decisions

Why the benchmark is built the way it is. Each entry states the decision, the reason, what was rejected, and what it
costs. Measured facts are marked as such; where a cause is not established, that is stated.

## 1. What is measured: SLH-DSA-SHA2-128s with and without MTL

**Decision.** One PQC algorithm, SLH-DSA-SHA2-128s, in two modes (`slh-dsa-mtl` and `slh-dsa`), with Ed25519,
ECDSA P-256 and RSA-2048 as classical references, over three dataset sizes (50, 500 and 5,000 names).

**Why.** The with/without pair is the only comparison that isolates what MTL changes, since the algorithm, keys,
security level and zone content are the same. SLH-DSA-SHA2-128s has a 7,856-byte signature and slow signing, which is
the situation MTL is designed for. The classical modes show how far PQC moves the numbers. Three sizes one decade
apart show scaling without making a run unmanageable.

**Rejected.** A second PQC algorithm (the task asks for one; each adds a matrix dimension), and an ML-DSA-based pair
(its signatures are smaller, so the effect MTL has on UDP fit is less visible).

## 2. No new cryptography, no resolver written, no Internet deployment

**Decision.** The harness only calls the Verisign MTL library, liboqs and OpenSSL. The live lab runs stock
(Verisign-patched) ldns, NSD and Unbound. Algorithm numbers and the `mtl-mode-full` EDNS option are lab placeholders.

**Why.** The task forbids new cryptography, a resolver from scratch and Internet deployment, and a benchmark of a
reference implementation is only meaningful if the implementation is not altered. The preflight gate re-runs
Verisign's own tests on the exact build.

## 3. Two measurement paths

**Decision.** An offline harness and a live lab, kept separate and labelled.

**Why.** They answer different questions.

| Question | Best answered by | Because |
| --- | --- | --- |
| Cost of one sign or verify call | offline harness | dedicated core, no network, a clock around each library call |
| Size of every DNS message, with and without MTL | offline harness | messages are built and counted exactly |
| What a validating resolver actually fetches, truncates and caches | live lab | real NSD and Unbound, packets captured per hop, Unbound's own cache counters |

**Cost.** Two sets of numbers that must not be mixed (see decision 4), and the live lab's timings are indicative
because its containers are not core-pinned.

## 4. Stack profiles: one source of pins, a deliberate default per path

**Decision.** Every pinned version (OpenSSL, liboqs, MTL, ldns, NSD, Unbound) lives in
`ecosystem/stacks/<profile>.env`. The offline image and the live-lab images are both built from that file, so a
version can never differ by accident, and every report names its profile. The two paths have **different defaults on
purpose**: the offline harness uses `latest`, the live lab uses `draft06`. Choose with `CIPHEROPS_STACK=<profile>`.

**The evidence behind the defaults.** Read from the built binaries and from running the stacks:

| | `draft06` | `latest` |
| --- | --- | --- |
| Verisign MTL / liboqs | 1.2.0 (`93b05ef`) / 0.14.0 | `7cef019` / 0.16.0 |
| liboqs name called by ldns, MTL and Unbound | `SPHINCS+-SHA2-128s-simple` in all three | `SLH_DSA_PURE_SHA2_128S` in ldns and MTL; Unbound contains only `SPHINCS+-SHA2-128s-simple` |
| Signature primitive | SPHINCS+ round 3, the pre-standard form of SLH-DSA (same parameter set and sizes) | FIPS 205 SLH-DSA |
| liboqs names present | SPHINCS+ only (the FIPS 205 names are not enabled in its default build) | `SLH_DSA_PURE_*` only (SPHINCS+ removed) |
| Resolver validates: MTL / ECDSA / plain SLH-DSA | yes / yes / yes | yes / yes / **SERVFAIL** |
| Offline harness go/no-go gate | **NO-GO**: its plain SLH-DSA mode needs `SLH_DSA_PURE_SHA2_128S`, absent in liboqs 0.14 | passes |

**Why the offline harness uses `latest`.** It is the only profile with FIPS 205 end to end, and MTL and the plain
baseline then call the *same* liboqs function, so "equivalent workloads with and without MTL" holds by construction. The
gate correctly refuses `draft06` for the offline harness rather than silently benchmarking a different primitive.

**Why the live lab uses `draft06`.** It is the only profile on which the resolver validates all three scenarios, which
the MTL-versus-non-MTL comparison needs.

**What this costs, stated openly.** Live-lab results on `draft06` are for SPHINCS+-SHA2-128s-simple, not FIPS 205.
The parameter set and every size are the same (7,856-byte signatures, 32-byte keys), so byte, truncation and cache
results carry over. Timings can differ slightly because the two constructions differ in domain separation, so
live-lab timings are not substituted for the offline harness's FIPS 205 timings. Each report states its profile and the
primitive it measured.

**The `latest` failure, and what is established.** On `latest`, Unbound's debug log reads `verify: Using PQ / PQ-MTL
Algorithms 246 ... Failed to match any usable anchor` and the query returns SERVFAIL. In the same containers
`ldns-verify-zone` accepts the same signed root zone, the DNSKEY keytag and trust-anchor digest match, and the MTL
(algorithm 130) and ECDSA scenarios validate. liboqs 0.16 no longer has the SPHINCS+ names; the ldns signer calls
`SLH_DSA_PURE_SHA2_128S`, while the Unbound binary at the pinned commit still contains only the SPHINCS+ name. That
strongly indicates an upstream mismatch in Verisign's Unbound at that commit: built against liboqs 0.16 but still asking
for an algorithm name that library no longer provides. The source has not been checked, so this is an inference from the
binary and the logs. If it holds, `latest` can serve both paths once Verisign's Unbound is rebuilt against the new name.

**Alternatives rejected.**

- *`draft06` for everything:* the offline harness would lose FIPS 205 and fail its gate.
- *Enabling SLH-DSA names in a liboqs 0.14 build for the offline image:* MTL 1.2.0 would still call SPHINCS+, so the
  baseline (FIPS 205) and MTL (SPHINCS+) would be different primitives, breaking equivalence.
- *`latest` for everything:* a resolver that cannot validate plain SLH-DSA cannot run the live comparison, and fixing it
  means modifying a resolver.
- *Two unrelated defaults with no shared source* (the earlier state): numbers could be mixed without anyone noticing.

## 5. Statistics: an exact interval of the median

**Decision.** Report the median and a distribution-free interval from order statistics, with its real coverage
(`stats.median_ci`).

**Why.** Trials are few (3 to 10). A bootstrap of a median from so few values is not a valid 95% interval (its
endpoints can only be observed values), and mean ± standard deviation assumes a normal distribution that timing data
does not have. The order-statistic interval is exact for any distribution. Its coverage depends only on the number of
trials: 94% for 5, 75% for 3, 97% for 6.

**Cost.** Five trials cannot honestly claim 95%; the report says 94%. More trials tighten it.

## 6. Failed lookups are not data

**Decision.** In the live lab, latency counts answered lookups (NOERROR, NXDOMAIN) only. A pass with any failed
lookup is invalid, excluded from every figure, and flagged, while its failures stay counted.

**Why.** A SERVFAIL returns fast and a timeout waits out the clock. Counting either makes a broken resolver look quick
or slow instead of failing the run. This was found when plain SLH-DSA on `latest` produced SERVFAIL timings that would
have entered the medians.

## 7. Measured, estimated and analytical are never blended

**Decision.** Every figure is labelled measured, estimated (extrapolated from sample trials) or analytical (the draft's
formulas). Cache usage in particular keeps its measured rows and its analytical rows apart.

**Why.** The task requires measured cache usage to be separated from analytical estimates, and an extrapolation
presented as a measurement is the commonest way a benchmark misleads.

## 8. Repeatability: pinned core offline, recorded provenance everywhere

**Decision.** The offline harness runs one process per trial on a dedicated core in a no-network container, with a
discarded warm-up and a seeded random mode order. Both paths record versions, hardware and settings. The live lab
does not pin cores and says so.

**Why.** Single-thread library timings are sensitive to scheduling, so they are isolated. A resolver lab needs
several containers running together, so isolating each one would change the thing being measured. Recording the host
(including the CPU governor, which was `powersave` on the development machine) lets a reader judge the timings.

## 9. A fail-safe go/no-go gate

**Decision.** `preflight` runs before any measurement and defers the run (exit code 3) unless the reference library
builds and passes its own tests, the tools round-trip, every harness mode passes its negative cases, and the harness
output is verified by a second implementation (`mtlverify`, and OpenSSL for SLH-DSA).

**Why.** The task says to defer when the implementation or fixtures are not available. The bundled known-answer
vectors cover ML-DSA, not SLH-DSA, so SLH-DSA is covered by interoperability between independent implementations
instead; this is stated wherever it matters and is not presented as a known-answer test.

## 10. The dashboard cannot disturb a measurement

**Decision.** While a benchmark suite or an offline run is active, every operation that touches the lab or the CPU is refused (an offline run times on a dedicated core); image builds and signing
run as background jobs with live logs; `dig` accepts only allow-listed options; clean-up removes files Docker created
as root.

**Why.** The lab uses fixed container names and one subnet, and a benchmark is timed, so a click in another browser tab
could stop the lab mid-run or distort the timings. These were reproducible defects, each now covered by a test.
