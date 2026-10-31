# Scope and limits

What CipherOps claims and what it does not. How the requirements of the problem statement (PS-036) are covered: [DELIVERABLES.md](../DELIVERABLES.md#ps-036-requirements).

## Scope

- Empirical timings and wire sizes on pinned stacks. This is **not** a security proof and **not** a production DNSSEC
  product. It calls published libraries and adds no cryptography.
- Two measurement paths that answer different questions:

  | | Offline harness | Simulated lab |
  | --- | --- | --- |
  | What it is | a C harness (`harness/mtlbench.c`) times each library call; DNS messages are built and counted in Python | a root → TLD → leaf hierarchy of unmodified NSD servers behind a validating Unbound, in Docker |
  | Best for | signing and verification time, signature and response sizes, cache arithmetic, zone updates | what a resolver does: bytes per hop from packet captures, truncation, TCP retry, Unbound's cache counters |
  | Environment | one pinned image, one dedicated core, no network | three images per stack; containers share all host CPUs |
  | Start it | `scripts/run_benchmark.sh`, or **Offline benchmark → Run** | **Simulated lab → Benchmark** |

  The two are never drawn on one chart: they use different libraries and versions.

## Limits

- **Profiles are not interchangeable.** Offline results are FIPS 205 SLH-DSA (`latest`); simulated results on `draft06`
  are the pre-standard SPHINCS+ form with the same sizes. Each run records its profile.
- **Simulated timings are indicative.** Containers are not pinned to cores and the CPU governor is recorded, not
  changed. Byte counts and relative comparisons are more reliable than absolute milliseconds.
- **Fixtures.** The reference library's known-answer vectors cover ML-DSA, not SLH-DSA. SLH-DSA is checked by round
  trips, negative cases and agreement between independent implementations (liboqs and OpenSSL, plus Verisign's
  `mtlverify`). That is an interoperability check, not a NIST known-answer test
  ([methodology §3](methodology.md#3-gono-go-gate)).
- **Few trials give wide intervals.** The full run uses 10 trials (the interval of the median covers 98%); the quick
  run uses 3 (75%) ([methodology §6.2](methodology.md#62-statistics)).
- **Synthetic zones and updates.** Zones are generated; the zone-update experiment applies same-size random changes
  with one signer and one resolver.
- **Placeholders, not assignments.** DNSSEC algorithm numbers and the `mtl-mode-full` EDNS option are lab values, not
  IANA assignments. Nothing here is for the Internet.
- **The drafts are drafts.** MTL mode and its DNSSEC profiles are individual Internet-Drafts, not RFCs. Status:
  [Learn: where the IETF stands](learn/README.md#where-the-ietf-stands).

## Benchmark practice

What a result needs so that someone else can trust and repeat it, and where each point is met:

- **Pinned, recorded environment.** Software comes from a stack profile; every run records versions, git commit
  (and whether the tree was modified), CPU, memory, kernel, governor and image IDs (`environment.json`, `suite.json`).
- **Controlled noise.** The offline harness runs on one dedicated core with no network, discards a warm-up run, seeds
  every order, and measures an A/A control (a mode against itself) so a difference smaller than the noise floor is not
  reported as a difference.
- **Honest statistics.** Medians with a distribution-free interval whose real coverage is stated; too few trials are
  labelled as such ([methodology §6.2](methodology.md#62-statistics)).
- **No cherry-picking.** Reports are generated from the data, not typed in; failed queries are excluded from latency but
  stay counted and warned about; measured, estimated and analytical numbers are labelled and never blended.
- **Fail safe.** The go/no-go gate blocks a run when the reference library or fixtures are not available.
- **Report like this.** Quote the run id and stack profile with every number, and say whether it is offline or
  simulated. Do not present results as Verisign's, or as a product comparison.

## Responsible use

- The offline harness runs with `network_mode: none`; the simulated lab runs on an internal Docker bridge network, so
  lab traffic does not leave your machine. Zones use reserved names (`bench.example.` offline, `.test` in the lab), not real domains.
- Do not point the query or capture tools at servers you do not operate.
- The software is built from third-party sources at pinned commits; see
  [third-party notices](../THIRD_PARTY_NOTICES.md) before you redistribute images or binaries.

## Software stacks

Every version lives in one file per **stack profile**, [`ecosystem/stacks/<profile>.env`](../ecosystem/stacks). The
offline image and the lab images are both built from that file, so a version cannot differ by accident, and every
report names its profile. Both use a Fedora 42 base and OpenSSL 3.6.0.

| Profile | MTL / liboqs | Signature primitive | Default for | Resolver validates plain SLH-DSA |
| --- | --- | --- | --- | --- |
| `latest` | `7cef019` / 0.16.0 | FIPS 205 SLH-DSA, one library call for MTL and the plain baseline | **offline harness** | **no** (SERVFAIL) |
| `draft06` | 1.2.0 `93b05ef` / 0.14.0 | SPHINCS+-SHA2-128s-simple, the pre-standard form (same sizes) | **simulated lab** | yes |

The harness uses `latest` so that MTL and the baseline are the same FIPS 205 primitive; on `draft06` its gate reports
NO-GO. The lab uses `draft06` because only there does a resolver validate all three algorithms. On `latest` the
Unbound binary still names a SPHINCS+ algorithm that liboqs 0.16 removed, which points to an upstream mismatch; this is
not confirmed from source and not yet reported upstream. Evidence and rejected alternatives:
[design decisions](design-decisions.md).

## PS-036 coverage

The requirements and where each is met are in [DELIVERABLES.md](../DELIVERABLES.md#ps-036-requirements).
