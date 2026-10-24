# Methodology

This document describes exactly what Cipherops measures, how, and which
numbers are estimates. It is the reference for every table in a run's
`report.md`.

## 1. Scope (PS-036)

- **Algorithm.** One supported PQC signature algorithm: **SLH-DSA-SHA2-128s**
  (FIPS 205, "pure" mode).
- **Reference implementation.** The **Verisign MTL C library**
  (`github.com/verisign/MTL`, pinned commit in the Dockerfile). Cipherops
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
| OpenSSL | `openssl-3.6.0` |
| liboqs | `0.16.0` |
| MTL library | commit `7cef019`, source unmodified; compiled with `make CFLAGS="-fPIC -Wall -Wextra -O2"` |
| Python dependencies | exact versions in `requirements.txt` |
| CPU | one core via Docker `cpuset` (default core 2); `mem_limit` 4 GB; no network |

**Compiler optimisation.** Upstream `configure.ac` always forces the debug
flags `-g -O0`. liboqs, which does the SLH-DSA work in both PQC modes, is a
CMake Release build. To compare like with like, the MTL library is compiled at
`-O2` by passing `CFLAGS` to `make`; the source is untouched and its own test
suite is re-run on this build by the go/no-go gate. To reproduce the as-shipped
`-O0` build for an ablation, rebuild with
`docker compose -f docker/compose.yml build --build-arg MTL_MAKE_CFLAGS=upstream`.
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
4. Every harness mode signs and verifies a small zone, and rejects tampered
   messages, signatures and ladders.

The bundled known-answer vectors cover the scheme named in
`mtltest_test_vectors.h`. SLH-DSA-SHA2-128s-MTL is validated by check 3 and
by the harness correctness gates.

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
  trial, preceded by warm-up trials. The clock is `CLOCK_MONOTONIC_RAW`
  around each library call.
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
- **Statistics.** Median, mean, standard deviation and min/max across trials,
  plus a bootstrap 95% confidence interval of the median (2,000 resamples,
  fixed seed).

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

## 10. Limitations

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
  cover a different scheme than SLH-DSA-SHA2-128s-MTL (see section 3).
- **No security claims.** None are made or implied.
