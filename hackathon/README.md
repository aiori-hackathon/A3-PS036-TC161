# CipherOps at the AIORI Hackathon 3

Team **A3-PS036-TC161** · problem statement **PS-036**: *PQC DNSSEC MTL benchmark using a reference implementation*.

This folder is the submission material. The benchmark tool and its documentation, in the rest of the repository, stay
generic and reusable.

## Goal

Post-quantum DNSSEC signatures are large: one SLH-DSA-SHA2-128s signature is 7,856 bytes, so a signed answer no longer
fits in one UDP packet. Merkle Tree Ladder (MTL) mode signs a zone once and gives each record a short proof. The goal
was a reproducible measurement of what MTL changes, compared with using the same PQC algorithm directly, using the
Verisign MTL reference implementation unmodified and adding no cryptography of our own.

## Team

Akash Joshi · Anitha Natarajan · Indu Radhakrishnan (faculty).

## What was attempted

- An **offline harness**: a C program that times each library call on one dedicated core, with DNS messages built and
  counted in Python, for `slh-dsa-mtl`, `slh-dsa`, `ed25519`, `ecdsa-p256` and `rsa-2048` on three zone sizes.
- A **simulated lab**: unmodified Verisign MTL-mode NSD servers and a validating Unbound in Docker, with packet
  capture, to see what a resolver does (bytes per hop, truncation, TCP retry, cache).
- **Beyond the minimum**: the cost of updating a zone (re-signing and stale resolver caches), a go/no-go gate that
  refuses to run without the reference library, an A/A noise control, and statistics with stated coverage.
- A **dashboard** to run and compare both paths, and **Learn** pages for newcomers, including where the IETF drafts
  stand.

## What is here

| | |
| --- | --- |
| [readout.md](readout.md) | the short readout: problem, what was done, results, issues found, next steps |
| [feedback.md](feedback.md) | findings, the interop and results matrix, follow-ups |
| [results/](results/README.md) | frozen results, with where and how they ran |
| [presentation/](presentation) | the deck: [revised 31 Oct 2026](presentation/A3-PS036-TC161_2026-10-31.pdf), [30 Oct 2026](presentation/A3-PS036-TC161_2026-10-30.pdf) and [original 13 Oct 2026](presentation/A3-PS036-TC161_2026-10-13.pdf) |
| [../DELIVERABLES.md](../DELIVERABLES.md) | what is submitted, the PS-036 requirements, and open items |
| [../ecosystem/drafts/](../ecosystem/drafts/README.md) | working text for an Informational Internet-Draft based on the lab (not an IETF document) |

Video: [YouTube](https://youtu.be/FaVfeO5WTV8). Run it: [Codespaces](../docs/codespaces.md) or
[locally](../docs/getting-started.md).

## Deck revision of 31 Oct

[The 31 Oct deck](presentation/A3-PS036-TC161_2026-10-31.pdf) is the 30 Oct deck with three edits, made directly in the PDF
because the slide source is not available. Everything else is unchanged, apart from the footer date.

- **Slide 9** has a source line: the numbers come from a run that is not frozen in the repository, and the full 10-trial run
  on the reference machine will replace them. The table itself is unchanged, including "medians of 5 trials", which no
  frozen run supports.
- **Slide 10** shows the two-track dashboard (Offline benchmark, Simulated lab, Compare), images pulled from the team
  registry or built, and no longer names `phase1-demo-safety`. Its source is [docs/architecture-slide.dot](../docs/architecture-slide.dot).
  The caption states that there is no single end-to-end FIPS 205 result: offline is FIPS 205 (`latest`), the lab is
  pre-standard (`draft06`), because the `latest` resolver answers SERVFAIL for plain SLH-DSA ([feedback.md](feedback.md)).
- **Slide 11** is unchanged: it shows the offline runner sequence, which still matches the code.

Still to do once the full run is frozen: replace slide 9's table and subtitle from that run's report, and name the machine.

## Known limits

See [Scope and limits](../docs/scope-and-limits.md). In short: timings are on pinned stacks and not a security proof,
simulated timings are indicative, and the MTL drafts are not RFCs yet.
