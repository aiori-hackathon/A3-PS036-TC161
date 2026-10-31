# CipherOps at the AIORI Hackathon 3

Team **A3-PS036-TC161** (Akash Joshi · Anitha Natarajan · Indu Radhakrishnan, faculty) · problem statement **PS-036**:
*PQC DNSSEC MTL benchmark using a reference implementation*. This folder is the submission material; the benchmark tool and
its documentation, in the rest of the repository, stay generic and reusable.

| | |
| --- | --- |
| [readout.md](readout.md) | the short readout: problem, what was done, results, issues found, next steps |
| [feedback.md](feedback.md) | findings, the interop matrix and follow-ups |
| [results/](results/README.md) | frozen results, with where and how they ran, and what is still to freeze |
| [presentation/](presentation) | the deck: [revised 31 Oct 2026](presentation/A3-PS036-TC161_2026-10-31.pdf), [30 Oct](presentation/A3-PS036-TC161_2026-10-30.pdf), [original 13 Oct](presentation/A3-PS036-TC161_2026-10-13.pdf) |
| [../DELIVERABLES.md](../DELIVERABLES.md) | what is submitted, the PS-036 requirements, and the open items |
| [../ecosystem/drafts/](../ecosystem/drafts/README.md) | working text for an Informational Internet-Draft based on the lab (not an IETF document) |

Video: [YouTube](https://youtu.be/FaVfeO5WTV8). Run it: [AWS](../deploy/aws/README.md), [Codespaces](../docs/codespaces.md)
or [locally](../docs/getting-started.md). Limits: [Scope and limits](../docs/scope-and-limits.md).

**The 31 Oct deck** is the 30 Oct deck patched in the PDF (the slide source is not available). Slide 9 now quotes the
frozen full run (10 trials, AWS node), and [results/](results/README.md#deck-slide-9-where-each-number-comes-from) traces
each number to a file. Slide 10 shows the two-track dashboard and the FIPS 205 finding (source:
[architecture-slide.dot](../docs/architecture-slide.dot)). The footer date changed. Slide 12 still says "762 lookups
measured on a real NSD server", which refers to a live capture that is not in the frozen runs.
