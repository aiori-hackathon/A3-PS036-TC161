# Findings and interop matrix

What we found while measuring, with the evidence and the status of each item. None has been filed with the upstream
projects; each entry says what a report would contain.

## Interop and results matrix

What each stack does in the simulated lab. Read from the built binaries and from running the stacks; the evidence is in
[design decisions §4](../docs/design-decisions.md#4-stack-profiles-one-source-of-pins-a-deliberate-default-per-path).

| | `draft06` stack | `latest` stack |
| --- | --- | --- |
| Verisign MTL / liboqs | 1.2.0 (`93b05ef`) / 0.14.0 | `7cef019` / 0.16.0 |
| Wire semantics | draft-fregly-dnsop-slh-dsa-mtl-dnssec-06: condensed signature by default, full signature on the `mtl-mode-full` EDNS option | aligned with draft-kaizer-dnsop-ml-dsa-mtl-dnssec-02: no EDNS option, every MTL RRSIG served as a full signature (per the stack file) |
| Signature primitive | SPHINCS+-SHA2-128s-simple (pre-standard SLH-DSA, same sizes) | FIPS 205 SLH-DSA |
| Resolver validates MTL | yes | yes |
| Resolver validates ECDSA | yes | yes |
| Resolver validates plain SLH-DSA | yes | **no: SERVFAIL** |
| Offline harness go/no-go gate | **NO-GO** (needs `SLH_DSA_PURE_SHA2_128S`) | passes |
| Used here for | the simulated lab | the offline harness |

**There is no single end-to-end FIPS 205 result.** The offline harness runs the FIPS 205 build (`latest`); the simulated
lab runs the pre-standard `draft06` build, because the `latest` lab's resolver answers SERVFAIL for plain SLH-DSA (finding
1). Offline and lab numbers are therefore never drawn on one chart, and a FIPS 205 end-to-end run waits for a resolver
that validates it (follow-up 4). We present this as a finding, not a footnote.

Results (sizes, times, cache) are in each run's report; the frozen one is in [results/](results/README.md). Sizes in the
draft's formulas are maxima; the benchmark measures the real values.

## Findings

| # | Component and version | What we saw | Evidence | Status |
| --- | --- | --- | --- | --- |
| 1 | Verisign `mtl-mode-unbound` at `04eacb7` (`latest` stack) | Returns SERVFAIL for plain SLH-DSA (algorithm 246): "Failed to match any usable anchor". `ldns-verify-zone` accepts the same signed root, the key tag and trust-anchor digest match, and MTL and ECDSA validate. The ldns signer calls `SLH_DSA_PURE_SHA2_128S`; the Unbound binary at that commit contains only the removed SPHINCS+ name. | [design decisions §4](../docs/design-decisions.md#4-stack-profiles-one-source-of-pins-a-deliberate-default-per-path) | Likely an upstream mismatch with liboqs 0.16. **Not confirmed from source; not reported.** |
| 2 | Verisign `mtl-mode-unbound` at `fab43ad` (`draft06` stack) | With separate KSK and ZSK, a child DS RRset whose RRSIG is a condensed MTL signature made by the parent ZSK fails validation. The DS path treats `sec_status_extend` as bogus and does not request an updated ladder, so the chain ends in SERVFAIL. Signing every zone with a single CSK avoids it; the same hierarchy validates with ECDSA and with plain SLH-DSA under KSK+ZSK. | [draft working text §7.1](../ecosystem/drafts/draft-joshi-dnsop-mtl-dnssec-bench-00.md) | Reported in the working text as an interoperability defect of the examined build, not as a protocol prohibition. **No upstream issue is linked from this repository.** |
| 3 | Verisign MTL library (`mtlbench` checks) | The first two bytes of a condensed signature (type byte and the byte after it) are not authenticated: flipping either is accepted. Everything from the series id onward is authenticated. Condensed verification trusts a ladder passed in as pre-verified. | [methodology §3](../docs/methodology.md#3-gono-go-gate); the harness records it as an observation | Recorded library behaviour, not a failure. A draft or library note may be worth raising. |
| 4 | `draft06` Unbound, cold start | Several EDNS full-signature exchanges toward the root early in recursion, including parallel fetches for `. DNSKEY` and trust-anchor probe names. | [draft working text §7.3](../ecosystem/drafts/draft-joshi-dnsop-mtl-dnssec-bench-00.md) | An **observation** for resolver tuning, not a proven defect: the fetches may be independent validation tasks. |

Interoperability between independent implementations was also used as the check for SLH-DSA, since the reference
library's known-answer vectors cover ML-DSA, not SLH-DSA. That is not a NIST known-answer test.

## What a report to upstream would contain

For findings 1 and 2: the pinned commit and stack file (`ecosystem/stacks/*.env`), the exact lab command to reproduce
(**Manage images** to build or pull, then Simulated lab → Prepare → Sign zones → Start, then a query for the algorithm in
question), the Unbound log lines above and the `ldns-verify-zone` result. Finding 1 should first be checked against the
Unbound source at that commit.

## Follow-ups

1. File findings 1 and 2 with the Verisign repositories; raise finding 3 with the draft authors if they agree it is
   worth specifying.
2. Measure ML-DSA-MTL (draft-kaizer-dnsop-ml-dsa-mtl-dnssec), which the newest drafts centre on, and the always-full
   encoding, which the `latest` lab already serves.
3. Re-run on a resolver that validates plain SLH-DSA on the newer stack, so one stack can serve both paths.
4. Status of the drafts, as of 30 Oct 2026: [Learn](../docs/learn/README.md#where-the-ietf-stands).
