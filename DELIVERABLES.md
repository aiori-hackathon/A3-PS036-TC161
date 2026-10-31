# Deliverables

What this team submits and where each item lives, the open items, and how each PS-036 requirement is met.

## AIORI Hackathon 3 deliverables

From the [hackathon guideline](https://portal.aiori.in/aiori-3/hackathon-guideline/).

| Deliverable | Where | State |
| --- | --- | --- |
| **Presentation** (PDF) | [revised 31 Oct 2026](hackathon/presentation/A3-PS036-TC161_2026-10-31.pdf) ([30 Oct](hackathon/presentation/A3-PS036-TC161_2026-10-30.pdf), [13 Oct](hackathon/presentation/A3-PS036-TC161_2026-10-13.pdf)) | done |
| **Team video** | [YouTube](https://youtu.be/FaVfeO5WTV8) | done |
| **Source code, documents, supporting material** | this repository, from the [README](README.md); also the [readout](hackathon/readout.md) and [findings](hackathon/feedback.md) | done |
| **Working demo** | built by the visitor, not hosted: [AWS](deploy/aws/README.md), [Codespaces](docs/codespaces.md) or [locally](docs/getting-started.md) | done |
| **Final deliverable files** | [hackathon/results/](hackathon/results/README.md): the full offline run and the full simulated-lab suites (LAN, WAN, lossy), frozen with their reports and the machine they ran on | done |

## Open items

- **Team registry.** `ghcr.io/hackchamp25` lacks the `latest` lab images; an owner of that namespace must run
  `scripts/publish_images.sh latest simulated`.
- **No single FIPS 205 end-to-end result.** Offline runs the FIPS 205 build (`latest`); the lab runs the pre-standard
  `draft06`, because the `latest` resolver answers SERVFAIL for plain SLH-DSA. This is presented as a finding
  ([feedback.md](hackathon/feedback.md)), not something this repository can fix.
- **Upstream reports.** The findings are written up with evidence but not yet reported to the upstream projects.
- **Fixed state.** Tag the repository (for example `hackathon-submission`) when the submission is final.

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
