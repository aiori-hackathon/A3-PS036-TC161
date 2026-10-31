# Deliverables

What the team submits for AIORI Hackathon 3 (problem statement PS-036), where each item lives, and how each PS-036
requirement is met.

## Submission

From the [hackathon guideline](https://portal.aiori.in/aiori-3/hackathon-guideline/).

| Deliverable | Where |
| --- | --- |
| **Presentation** (PDF) | [31 Oct 2026](hackathon/presentation/A3-PS036-TC161_2026-10-31.pdf) (earlier: [30 Oct](hackathon/presentation/A3-PS036-TC161_2026-10-30.pdf), [13 Oct](hackathon/presentation/A3-PS036-TC161_2026-10-13.pdf)) |
| **Team video** | [YouTube](https://youtu.be/FaVfeO5WTV8) |
| **Source code, documents, supporting material** | this repository, from the [README](README.md); the [readout](hackathon/readout.md) and the [findings](hackathon/feedback.md) |
| **Working demo** | read-only, hosted: [eval.cipherops.stream](https://eval.cipherops.stream) (saved results, Compare, Learn and live DNS lookups; reachable while the team's AWS node is running). Full copy: [AWS](deploy/aws/README.md), [Codespaces](docs/codespaces.md) or [locally](docs/getting-started.md) |
| **Final deliverable files** | [hackathon/results/](hackathon/results/README.md): the full offline run (10 trials) and the full simulated-lab suites (LAN, WAN, lossy), with their reports and the machine they ran on |

## PS-036 requirements

| Requirement | Where it is met |
| --- | --- |
| One supported PQC algorithm | SLH-DSA-SHA2-128s |
| One MTL reference implementation | Verisign MTL, pinned by commit (stack profile) |
| Three dataset sizes | 50 / 500 / 5,000 names by default; quick run 20 / 60 / 200 |
| With and without MTL | `slh-dsa-mtl` vs `slh-dsa`, plus classical references |
| Repeated trials, pinned environment | pinned image, dedicated core, seeded order, discarded warm-up (offline); the simulated lab is not core-pinned |
| Signing time, verification time, transmitted bytes | `trials.csv`, `responses.csv`, `workload.csv`; simulated-lab captures |
| MTL on a changing zone (beyond the minimum) | `evolution.csv` and the dashboard's **Zone updates** view |
| Record configuration and workload parameters | `config.yaml`, `environment.json`, `suite.json` in each run |
| Cache: measured vs analytical, kept apart | `cache.csv` has a `kind` column; the report never mixes them |
| Defer if implementation or fixtures are missing | the go/no-go gate (`preflight`) blocks the run |
| No new cryptography, resolver or deployment | the harness only calls libraries; the lab uses stock servers |

## Scope note

Offline results use the FIPS 205 stack (`latest`) and the simulated lab uses the pre-standard `draft06` stack, because the
`latest` lab's resolver answers SERVFAIL for plain SLH-DSA. There is therefore no single end-to-end FIPS 205 result; the
two are never compared on one chart. Evidence: [findings](hackathon/feedback.md); limits:
[scope and limits](docs/scope-and-limits.md).
