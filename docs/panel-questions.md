# Expected panel questions (DNS, DNSSEC, PQC experts)

Short model answers, using the full run (large zone = 5,000 names, 10,005 RRsets, 5 trials, live run on NSD).
Say the number, then the reason. If a question touches a limitation, say so plainly; the panel will respect that more
than a stretched claim.

---

## A. Scope and framing

**1. What exactly did you build, and what did you not build?**
A reproducible benchmark that drives the unmodified Verisign MTL reference library, liboqs and OpenSSL on identical
DNSSEC zones, and measures signing time, verification time, transmitted bytes and cache usage. We wrote no new
cryptography, no resolver, and made no security claims, as PS-036 requires.

**2. Why is this problem important?**
Post-quantum signatures are large. SLH-DSA-128s is 7,856 B, six times a 1,232-byte UDP packet, so almost every DNSSEC
answer would need TCP. MTL is a proposed way to keep answers small. We measure whether it actually does that.

**3. What is your single most important result?**
On the large zone, a "name does not exist" answer is 1.6 KB with MTL versus 32 KB without; the 762-lookup workload
moves 0.92 MB versus 11.08 MB (measured live); and on a 50 ms network it finishes in 63.5 s versus 125.6 s
(38.7 s with a 4096-byte buffer, close to ECDSA's 38.6 s).

**4. Why compare against RSA, ECDSA and Ed25519 at all?**
They are what DNSSEC uses today. They give the target: how close can a quantum-safe option get to current cost.

---

## B. MTL mode

**5. Explain MTL in one minute.**
Each RRset is hashed into a leaf of a Merkle tree. The top of the tree (the rungs, together the "ladder") is signed
once with SLH-DSA. Each RRSIG carries only the sibling hashes from its leaf to a rung (the condensed signature). A
resolver verifies the signed ladder once, caches it, then checks each answer with a few hash operations.

**6. How big is the condensed signature, and why does it grow?**
172 / 220 / 284 B for 105 / 1,005 / 10,005 RRsets. It holds one 16-byte hash per tree level, so it grows with
log2 of the zone size: 100× more RRsets added only about 110 bytes. The measured maximum matches the draft's
formula exactly.

**7. How big is the ladder, and how many rungs?**
8,024 / 8,152 / 8,120 B, with 4 / 8 / 7 rungs. It is one 7,856-byte SLH-DSA signature plus the rung hashes and a
header. It is at or below the draft's maximum (the formula assumes the most rungs possible).

**8. How does a resolver get the ladder?**
On first contact it gets a condensed signature it cannot check, so it asks again over TCP with the `mtl-mode-full`
EDNS option and receives the full signature (condensed + signed ladder, about 8.3 KB). We use a lab code point
(65001) for that option, because it has no IANA assignment.

**9. Is MTL a stateful signature scheme like XMSS or LMS? Is there a key-reuse risk?**
No one-time-key reuse risk: the underlying signature is SLH-DSA, which is stateless. The signer does keep the tree
(node set) to produce condensed signatures; losing it means rebuilding or re-signing, not a security failure. We make
no security claims beyond that.

**10. What happens when the zone changes?**
New records are appended as new leaves, and a new ladder is signed. Resolvers holding the old ladder must fetch the new
one when they see a signature they cannot verify. **We did not measure zone updates**; it is the most important next
step (see limitations).

**11. What does "warm" and "cold" verification mean in your numbers?**
Warm: the ladder is already cached; MTL verifies an answer in 7.2 µs (versus 541 µs for direct SLH-DSA and 117 µs for
Ed25519). Cold: nothing cached; MTL needs one SLH-DSA verification of the ladder, about 490 µs, the same as direct
(480 µs). Cold happens once per zone (per ladder) per resolver.

**12. Why is MTL signing 0.58 s regardless of zone size?**
The cost is dominated by the single ladder signature (about 0.5 s). Appending 10,005 leaves and extracting condensed
signatures is just hashing. Direct SLH-DSA needs one 0.555 s signature per RRset.

---

## C. DNSSEC specifics

**13. Which algorithm numbers did you use in RRSIG and DNSKEY?**
Lab values: 50 for SLH-DSA-MTL and 249 for direct SLH-DSA. Neither is IANA-assigned for this use. NSD serves them as
opaque data, which is all we need, since we do not validate in NSD.

**14. What exactly is signed? Is the comparison fair?**
The RFC 4034 §3.1.8.1 signing input (RRSIG RDATA without the signature + canonical RRset), byte-identical across all
five modes. Every mode signs the same messages.

**15. Did you use NSEC or NSEC3? Which parameters?**
NSEC3, hash algorithm 1, 0 extra iterations, no salt, following RFC 9276. An NXDOMAIN answer carries the SOA plus up to
three NSEC3 records (closest encloser, next closer, wildcard), each with its own RRSIG. That is why NXDOMAIN is the
largest answer: four signatures.

**16. KSK/ZSK split? DS records? Key rollover?**
One combined signing key per mode for the benchmark. The DNSKEY RRset is signed like everything else. DS, the parent
zone and rollovers are out of scope. SLH-DSA public keys are small (32 B), so DNSKEY size is not the problem.

**17. Why does MTL still fail to fit 1,232 bytes for 13% of answers?**
Those are the 1–2 KB TXT records (the data itself is too big) and NXDOMAIN answers (four condensed signatures plus four
records, about 1.6 KB). With a 4,096-byte buffer, every MTL answer fits.

**18. Why 1,232 and 4,096?**
1,232 is the DNS Flag Day 2020 recommendation: an IPv6 minimum-MTU packet (1,280) minus IPv6 and UDP headers, so it
never fragments. 4,096 is the older common default; it causes IP fragmentation, which we observed in the captures
(about 3 fragments per large answer).

**19. Does MTL change DNS amplification risk?**
Smaller UDP answers mean a lower amplification factor. Direct SLH-DSA answers do not fit even 4,096, so they are always
truncated (TC=1), which limits amplification but forces TCP. We did not measure amplification directly.

---

## D. Measurement method

**20. How did you measure time? How do you know it is stable?**
`CLOCK_MONOTONIC_RAW` around each library call in a C harness. One process per trial, a warm-up trial, modes in a
seeded random order, the container pinned to one CPU core. We report medians of 5 trials with bootstrap 95% confidence
intervals; for example, MTL signing is 0.58 s [0.56, 0.61].

**21. Your direct SLH-DSA zone signing for the large zone: is it measured?**
No, it is extrapolated: 1,000 of 10,005 RRsets were signed per trial (0.555 s each) and scaled up to ≈ 92 minutes. The
medium zone (1,005 RRsets) is fully measured: 558 s direct versus 0.57 s with MTL. A fully measured large-zone run takes
about 9 hours and is a config change.

**22. Are the byte counts calculated or measured?**
Both, and we compare them. The main run builds real DNS messages around the real signatures and counts their exact
size. The live run sends the workload to a real NSD server and counts every byte; tcpdump captures matched the client
counts. They agree exactly in 21 of 30 cases. The difference: NSD fills truncated replies with about 1 KB of records,
which the calculation assumed empty, so live is 30–40% higher for MTL and RSA at 1,232.

**23. Who is the resolver in the live test? Why not Unbound or BIND?**
A small Python client that behaves like a resolver towards an authoritative server: UDP first, TC→TCP, resends on
timeout, an answer cache, and MTL's one-time full-signature fetch. Unbound and BIND do not support MTL code points, and
their recursion and validation would hide the transport effects we measure. It does not validate signatures;
verification cost comes from the harness.

**24. Why a Zipf workload with s = 1.1 and 10% NXDOMAIN?**
DNS query popularity is well known to be heavy-tailed (Zipf-like): a few names get most queries. 10% non-existent names
is a moderate assumption; real shares vary by resolver. Both are configurable, and the seed makes the workload identical
across runs.

**25. Why does the large-zone workload send only 762 queries out of 2,001 lookups?**
The resolver cache: only the first lookup of each name and type is sent. 1 DNSKEY + 200 non-existent names (all
distinct) + 561 different existing names = 762. MTL adds one ladder fetch, so 763.

**26. What do LAN, WAN and lossy represent?**
No added delay; 50 ms round trip; 50 ms + 1% packet loss, applied with Linux tc-netem. Bytes do not change with the
network, but time and reliability do: each TC→TCP costs about two extra round trips, and lost fragments cost a 0.4 s
timeout and a resend.

**27. How did you measure cache usage?**
Answers from the workload are parsed from wire format into dnspython's resolver cache; we count signature and ladder
bytes held, and Python heap with tracemalloc. Large zone: MTL 376 KB (including the 8 KB ladder) versus 10.7 MB direct.
Draft §12 formula estimates are reported separately as analytical.

**28. Can someone reproduce your results?**
Yes: one pinned Docker image (OpenSSL 3.6.0, liboqs 0.16.0, MTL commit 7cef019, all built from source), a fixed seed,
and each run stores its config, environment, gate results, raw data, signatures and report. One script rebuilds and
reruns.

**29. How do you know the reference implementation works in your setup?**
A go/no-go gate runs Verisign's own 89 unit tests, an mtlkeygen → mtlsign → mtlverify round trip that must reject a
tampered message, and a smoke test of every harness mode. The harness also checks every signature verifies and every
tampered message, signature and ladder is rejected, in every trial.

---

## E. Alternatives and "why not ..."

**30. Why SLH-DSA and not ML-DSA or Falcon?**
SLH-DSA relies only on hash functions (conservative security assumptions) and MTL mode is specified for it in the
DNSSEC draft. ML-DSA-44 is 2,420 B (still above 1,232); Falcon-512 is about 666 B but needs careful floating-point
implementation. **We did not benchmark them**; adding them from liboqs is a natural extension.

**31. Could MTL be used with ML-DSA too?**
MTL is a mode that can wrap other signature schemes; the gain is largest when the underlying signature is large. We only
tested SLH-DSA-SHA2-128s, as PS-036 asks.

**32. Why the "s" (small) parameter set and not "f" (fast)?**
128s has the smaller signature (7,856 B vs about 17 KB for 128f) but slow signing. With MTL, signing happens once per
ladder, so the slow signing matters much less. 128f with MTL would be an interesting comparison.

**33. Why not just use TCP or DoT/DoH for everything?**
That is a deployment shift for the whole resolver ecosystem and costs connection setup on every new server. MTL keeps
most answers on plain UDP. We did not test DoT/DoH.

---

## F. Practicality and deployment

**34. Would this work on today's Internet?**
Not without standards and software support: algorithm numbers, the `mtl-mode-full` EDNS option, and resolver
implementations. Our results show the cost side looks favourable; deployment is outside PS-036.

**35. What about middleboxes dropping fragments or large UDP?**
That is exactly why 1,232 matters. With MTL, 87% of answers fit 1,232; direct SLH-DSA never does. We did not simulate
fragment-dropping firewalls; it is a good next test.

**36. What is the cost for the signer (authoritative operator)?**
Signing time drops from about 92 minutes (extrapolated) to 0.58 s for 10,005 RRsets. The signer keeps the tree in
memory: about 1 MB of growth measured (analytical node-set estimate about 480 KB); peak process memory about 14 MB.

**37. What is the cost for the resolver?**
One cold verification (about 0.5 ms) and about 8 KB of ladder cache per zone, then 7 µs per answer. Cache for the
workload: 376 KB versus 10.7 MB direct.

**38. What if the resolver talks to thousands of zones?**
Each zone needs its own ladder (about 8 KB) and one cold fetch. Benefit depends on how often each zone is queried
again. We tested one zone; multi-zone ladder reuse is future work.

---

## G. Limitations to state before they are asked

- Direct SLH-DSA whole-zone signing for the large zone is **extrapolated** from 1,000 measured signatures.
- **Zone updates** and ladder refresh are not measured.
- One zone, one resolver; no multi-zone cache behaviour.
- The live client does **not validate** signatures; verification cost is from the harness.
- Lab code points (algorithm 50/249, EDNS option 65001), not IANA-assigned.
- Calculated bytes assume empty truncated replies; NSD fills them, so live bytes are 30–40% higher for MTL and RSA at
  1,232. We report both.
- CPU pinning on Docker Desktop/WSL2 is best effort.
- Only SLH-DSA-SHA2-128s; ML-DSA and Falcon not benchmarked.
- No security claims (PS-036 constraint).

---

## H. Quick numbers to memorise (large zone)

| | Without MTL | With MTL | Ed25519 |
|---|---|---|---|
| Signature per RRSIG | 7,856 B | ≤ 284 B + 8,120 B ladder once | 64 B |
| Sign whole zone | ≈ 92 min (extrapolated) | 0.58 s | 0.37 s |
| Verify one answer, cached | 541 µs | 7.2 µs | 117 µs |
| Verify first answer, cold | 480 µs | 490 µs | 103 µs |
| Answers fitting 1,232 B | 0% | 87.4% | 91.5% |
| NXDOMAIN answer (median) | 32 KB | 1.6 KB | 0.8 KB |
| Workload bytes, live | 11.08 MB | 0.92 MB | 0.38 MB |
| 762 lookups, 50 ms RTT, 1,232 / 4,096 | 125.6 s / 125.6 s | 63.5 s / 38.7 s | 43.0 s / 38.6 s |
