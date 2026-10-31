# Scope and limits

What CipherOps claims and what it does not. How each PS-036 requirement is covered: [DELIVERABLES.md](../DELIVERABLES.md#ps-036-requirements).

## Scope

Empirical timings and wire sizes on pinned stacks. This is **not** a security proof and **not** a production DNSSEC
product; it calls published libraries and adds no cryptography. Two tracks answer different questions, and their results
are never drawn on one chart because they use different library versions:

- **Offline harness** (`scripts/run_benchmark.sh`, or **Offline benchmark** in the dashboard): crypto cost, exact message
  sizes and cache arithmetic, on one pinned core with no network.
- **Simulated lab** (**Simulated lab** in the dashboard): what a validating resolver does, from packet captures. The
  containers share all host CPUs.

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

## Reporting results

How runs are made repeatable and honest is in the [methodology](methodology.md) and the
[design decisions](design-decisions.md). When you quote a number, quote the run id and stack profile, say whether it is
offline or simulated, and do not present it as Verisign's or as a product comparison.

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
Unbound binary still names a SPHINCS+ algorithm that liboqs 0.16 removed, which points to an upstream mismatch; the source
has not been checked. Evidence and rejected alternatives:
[design decisions](design-decisions.md).
