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
| [presentation/](presentation) | the deck: [revised 30 Oct 2026](presentation/A3-PS036-TC161_2026-10-30.pdf) and [original 13 Oct 2026](presentation/A3-PS036-TC161_2026-10-13.pdf) |
| [../DELIVERABLES.md](../DELIVERABLES.md) | what is submitted, the PS-036 requirements, and open items |
| [../ecosystem/drafts/](../ecosystem/drafts/README.md) | working text for an Informational Internet-Draft based on the lab (not an IETF document) |

Video: [YouTube](https://youtu.be/FaVfeO5WTV8). Run it: [Codespaces](../docs/codespaces.md) or
[locally](../docs/getting-started.md).

## Deck corrections

The deck is a PDF exported from slides that are not in this repository, so these changes are made in the slide source:

- **Slide 9.** Label it with its source run, trial count, zone size, lookup count and machine. It currently shows
  "medians of 5 trials" on a 10,005-RRset zone, which matches no frozen run (the full configuration is 10 trials); replace
  the numbers with the full run's report once frozen, see [results/](results/README.md#still-to-freeze).
- **Slide 10.** Replace the architecture figure with [docs/architecture.png](../docs/architecture.png)
  ([source](../docs/architecture.dot)): it shows the dashboard's two tracks (Offline benchmark, Simulated lab) and
  Compare, images pulled from the team registry or built, and no longer says "Setup · Live · Benchmark · Compare" or
  `phase1-demo-safety`.
- **Slide 11.** Update the caption and any dashboard wording the same way; it shows the offline runner sequence only.
- **A findings slide.** State that there is no single end-to-end FIPS 205 result: offline is FIPS 205 (`latest`), the lab is
  pre-standard (`draft06`), because the `latest` resolver answers SERVFAIL for plain SLH-DSA ([feedback.md](feedback.md)).

## Known limits

See [Scope and limits](../docs/scope-and-limits.md). In short: timings are on pinned stacks and not a security proof,
simulated timings are indicative, and the MTL drafts are not RFCs yet.
