# Learn: why DNSSEC needs MTL

New to DNSSEC, post-quantum signatures or Merkle Tree Ladder (MTL) mode? Start here. This takes about ten
minutes and needs no prior knowledge. Everything on this page is background; the measured results are in the
run reports.

## The walkthroughs

Three self-contained animated pages, no network or dependencies. Open one in a browser (on GitHub, download the file
first, because GitHub shows HTML as source); arrow keys step, space plays. The dashboard shows the same pages in its
**Learn** tile. They teach; the measured results are in the run reports.

| Page | About |
| --- | --- |
| [why-dnssec-needs-mtl.html](why-dnssec-needs-mtl.html) | seven scenes from a poisoned cache to the packet-size problem, ending with where the IETF drafts stand |
| [mtl-dnssec-ops-walkthrough.html](mtl-dnssec-ops-walkthrough.html) | what an operator does to bring up a signed zone and add a record, for plain SLH-DSA and for MTL (byte figures are illustrative) |
| [what-we-measure.html](what-we-measure.html) | the four PS-036 measurements, how the offline harness and the simulated lab each measure them, what each cannot tell you, and which claims the results support; it contains no results |

## The story in six steps

| # | Scene | What to take away |
|---|---|---|
| 1 | **Plain DNS** | A resolver accepts the first reply that matches a 16-bit query ID and a source port. An attacker who guesses both can poison the cache, and everyone behind that resolver is sent to the wrong address until the TTL expires. |
| 2 | **With DNSSEC** | Every record carries a signature (RRSIG) that only the zone's private key can make. A forged reply cannot carry one, so the resolver drops it. DNSSEC proves who wrote an answer and that it was not changed. It does not encrypt. |
| 3 | **Chain of trust** | The resolver trusts one key, the root's. The root signs a hash (DS) of each top-level domain's key, which signs a hash of the next key down, and so on to the answer. To forge a valid answer an attacker needs a private key somewhere on that chain. |
| 4 | **Quantum threat** | Public keys are public. Shor's algorithm turns an RSA or elliptic-curve public key into its private key, so a large quantum computer could forge answers that validate. Replacing the algorithm everywhere takes years, so the replacement must be ready first. |
| 5 | **PQC size problem** | Post-quantum signatures are large. SLH-DSA-SHA2-128s signs with 7,856 bytes, against 64 bytes for ECDSA P-256. One signed answer is about 6.5 times the 1,232-byte UDP limit, so it is truncated and retried over TCP, or fragmented and often dropped. Signing is slow too: about 0.6 s per signature on one core, so about 100 minutes for a 10,005-RRset zone. |
| 6 | **How MTL helps** | MTL puts every RRset of the zone into one Merkle tree and signs the tree's top (the *ladder*) once. Each answer then carries only a short hash path, at most 284 bytes for 10,005 RRsets. A resolver fetches the full signature (about 8.7 KB) once, caches the ladder, and verifies every later answer with hashing alone. |

## What MTL does not solve

MTL moves the cost; it does not remove it. This is what the benchmark measures rather than assumes (the
*evolving zone* experiment, section 5.7 of a run's report and the **Zone updates** view in the dashboard's Compare tile):

- A resolver without the ladder pays more than plain SLH-DSA, once.
- New or changed records verify only after a new ladder is signed, so a zone that changes often signs more ladders
  and resolvers fetch full signatures more often.
- Every record ever added stays in the tree, so the series only grows.
- Condensed signatures published earlier can stop verifying against the newest ladder when rungs merge, and must be
  reissued.
- The draft's size figures are maxima. Real values are measured, not taken from the formulas.

## Where the IETF stands

Checked on the IETF Datatracker on 30 October 2026. This changes, so check the links below for the latest.

| Draft | What it covers | Status |
|---|---|---|
| [draft-harvey-cfrg-mtl-mode](https://datatracker.ietf.org/doc/draft-harvey-cfrg-mtl-mode/) | MTL mode for any signature scheme | Individual draft, revision 09 (March 2026), expired |
| [draft-fregly-dnsop-slh-dsa-mtl-dnssec](https://datatracker.ietf.org/doc/draft-fregly-dnsop-slh-dsa-mtl-dnssec/) | SLH-DSA-MTL keys and signatures in DNSSEC (the scheme this project measures) | Individual draft, revision 06 (March 2026), expired |
| [draft-kaizer-dnsop-ml-dsa-mtl-dnssec](https://datatracker.ietf.org/doc/draft-kaizer-dnsop-ml-dsa-mtl-dnssec/) | The same idea with ML-DSA-44, as a "conservative" algorithm | Individual draft, revision 03 (October 2026), active |
| [draft-sheth-pqc-dnssec-strategy](https://datatracker.ietf.org/doc/draft-sheth-pqc-dnssec-strategy/) | Run a conservative algorithm in MTL mode next to a low-impact "drop-in" algorithm | Informational individual draft, revision 02 (October 2026), active |

What this means:

- All four are work in progress. None is adopted by a working group or published as an RFC, and no DNSSEC
  algorithm number is assigned for these schemes yet.
- The newest drafts centre on ML-DSA-MTL, whose underlying signature is smaller (2,420 bytes for ML-DSA-44 against
  7,856 for SLH-DSA-SHA2-128s). This project measures SLH-DSA-MTL, the largest-signature case, so the effects are
  easy to see. The same trade-offs should apply to ML-DSA-MTL with smaller numbers; measuring it is a follow-up.
- The strategy draft asks for exactly this kind of testing: truncation, TCP fallback and latency in BIND, NSD and
  CoreDNS.

## Honest limits of the explainer

- It is a teaching aid, not a measurement. Sizes in it are the draft's maximum-size formulas for a 10,005-RRset zone
  and about 113 bytes of DNS overhead per answer.
- The documentation address ranges (192.0.2.0/24, 203.0.113.0/24) and `bank.example` are placeholders.
- The algorithm numbers and the EDNS option used for the full signature are lab placeholders, not IANA assignments.
- Nothing here is a security proof or a standards-compliance claim.

## Further reading

- [draft-harvey-cfrg-mtl-mode](https://datatracker.ietf.org/doc/draft-harvey-cfrg-mtl-mode/): the MTL mode itself
- [draft-fregly-dnsop-slh-dsa-mtl-dnssec](https://datatracker.ietf.org/doc/draft-fregly-dnsop-slh-dsa-mtl-dnssec/): how it is carried in DNSSEC
- [draft-kaizer-dnsop-ml-dsa-mtl-dnssec](https://datatracker.ietf.org/doc/draft-kaizer-dnsop-ml-dsa-mtl-dnssec/): MTL with ML-DSA
- [draft-sheth-pqc-dnssec-strategy](https://datatracker.ietf.org/doc/draft-sheth-pqc-dnssec-strategy/): the two-algorithm strategy
- [FIPS 204](https://csrc.nist.gov/pubs/fips/204/final): ML-DSA
- [FIPS 205](https://csrc.nist.gov/pubs/fips/205/final): SLH-DSA
- [RFC 4033](https://www.rfc-editor.org/rfc/rfc4033), [RFC 4034](https://www.rfc-editor.org/rfc/rfc4034), [RFC 4035](https://www.rfc-editor.org/rfc/rfc4035): DNSSEC
