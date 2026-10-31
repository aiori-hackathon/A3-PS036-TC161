# Readout

Five slides' worth. Full deck: [revised 31 Oct 2026](presentation/A3-PS036-TC161_2026-10-31.pdf).

## 1. Problem

DNSSEC answers must fit one unfragmented UDP packet (1,232 bytes by default). One SLH-DSA-SHA2-128s signature is 7,856
bytes, so a signed answer is truncated and retried over TCP, or fragmented and often dropped. Signing is slow too:
about 0.6 s per signature on one core. MTL mode signs a zone once and gives each record a short authentication path.
PS-036 asks: what does that change, compared with the same algorithm without MTL?

## 2. What we did

- Measured `slh-dsa-mtl` against `slh-dsa`, with Ed25519, ECDSA P-256 and RSA-2048 as references, on 50, 500 and 5,000
  names, using the Verisign MTL reference library unmodified.
- Two paths: an offline harness (one pinned core, no network) and a simulated lab (NSD and a validating Unbound in
  Docker, with packet capture). One stack profile pins both.
- Went beyond the minimum: zone updates, an A/A noise control, a go/no-go gate and intervals with stated coverage.

## 3. Results

From the frozen runs on one AWS node ([results/](results/README.md): machine, settings and what each folder holds). Offline
and lab results use different stacks and are not compared with each other.

**Offline benchmark** ([`offline-full-20261030T213016Z`](results/offline-full-20261030T213016Z/report.md): FIPS 205
`latest` stack, 10 trials, zones of 105 / 1,005 / 10,005 RRsets, benchmark pinned to one core):

- **Signing, medium zone (1,005 RRsets), fully measured.** MTL 419.60 ms; direct SLH-DSA 420.29 s. MTL was 1,002× faster
  (median of per-trial ratios, interval 999×–1,003×).
- **Signing, large zone (10,005 RRsets).** MTL 435.52 ms; direct SLH-DSA about 4,174 s (70 min), **extrapolated** from
  1,000 sampled RRsets at 417.17 ms each.
- **Verification, medium zone, warm.** MTL 4.12 ms for every RRSIG in the zone; direct SLH-DSA 418.71 ms.
- **Response size, large zone.** At a 1,232-byte EDNS buffer, 87% of MTL responses fit in UDP, against 0% for direct
  SLH-DSA.
- **Transmitted bytes, large zone, 762 lookups on the wire.** 0.70 MB with MTL against 11.08 MB with direct SLH-DSA; 762
  truncation and TCP-retry events became 245, plus 1 full-signature fetch.

**Simulated lab** (`lab-full-draft06/`: pre-standard `draft06` stack, real NSD servers and a validating Unbound, 5 trials,
2,000 lookups per pass; [LAN](results/lab-full-draft06/lan-20261031-025321/report.md),
[WAN](results/lab-full-draft06/wan-20261031-033539/report.md) and
[lossy](results/lab-full-draft06/lossy-20261031-052353/report.md) profiles). On the 5,000-name hierarchy, LAN:

- **Signing the lab's zones, measured once.** MTL 1.8 s; direct SLH-DSA 22.7 min (743× faster).
- **Resolver upstream bytes, cold.** 1.71 MB with MTL against 29.23 MB with direct SLH-DSA (17× less); truncated answers
  294 against 1,361.
- **Resolver RRset cache.** 1,003 KB with MTL against 10.65 MB (11× less memory).
- Median lookup latency is about the same for every algorithm (about 0.24 ms cold on LAN, 2.7 ms on WAN): the differences
  are in bytes and truncation, not median time.

The trade-offs are measured too (resolver without the ladder, zone updates, cache); see the reports and the dashboard's
**Compare** tile.

## 4. Issues found

Details, evidence and the interop matrix: [feedback.md](feedback.md).

- **There is no single end-to-end FIPS 205 result.** The offline harness runs the FIPS 205 build (`latest`); the lab runs
  the pre-standard `draft06` build, because on `latest` the lab's Unbound answers SERVFAIL for plain SLH-DSA although the
  signed zone verifies elsewhere. We present this as a finding.
- On the older stack, a parent zone with separate KSK and ZSK fails to validate a child DS signed with a condensed MTL
  signature.
- The reference library does not authenticate the first two bytes of a condensed signature and trusts a ladder passed in
  as pre-verified.
- Cold starts show several full-signature fetches toward the root (an observation, not a proven defect).

None is filed upstream yet.

## 5. Next steps

File the findings upstream; measure ML-DSA-MTL and the always-full encoding; re-run on a resolver that validates plain
SLH-DSA on the newer stack. The list with details: [feedback.md](feedback.md#follow-ups).
