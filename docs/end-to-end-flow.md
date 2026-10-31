# Cipherops end-to-end flow

What goes into each of the 7 stages and what comes out, shown for `configs/quick.yaml` (sizes and counts follow from the
configuration; timings and byte totals are not shown, because they are results: each run's own numbers are in its
`report.html`). Column-level detail for every output is in
[outputs-reference.md](outputs-reference.md). To watch every variable being filled on tiny zones, run `scripts/own.py`.

```
quick.yaml ──► 1 config + environment ──► 2 go/no-go ──► 3 zones + signing inputs ──► 4 sign + verify (mtlbench)
                                                                     │                         │
                                                                     ▼                         ▼
                                        7 report ◄── 6 statistics ◄── 5 DNS answers, bytes, cache
```

## Launch

| | |
|---|---|
| Command (Windows) | `.\scripts\run_benchmark.ps1 -Config configs/quick.yaml` |
| What it does | `docker compose --env-file ecosystem/stacks/<stack>.env -f docker/build.yml build bench` (pinned image for the stack profile, default `latest`; skipped with `CIPHEROPS_SKIP_BUILD=1`), sets `CIPHEROPS_GIT_SHA`, `CIPHEROPS_HOST_IMAGE_ID`, `CIPHEROPS_CPUSET=2`, then `docker compose run --rm bench "python3 -m cipherops.runner run --config configs/quick.yaml"` |
| Container | Fedora 42, OpenSSL, liboqs and Verisign MTL as pinned by the stack profile (-O2), `mtlbench` compiled at build; 1 CPU core (cpuset 2), 4 GB, no network |
| Where results land | container `/work/results` = the repository's `results/` folder on the host (volume mount) |
| Entry point | `runner.main()` → `cmd_run(args)` runs stages 1–7 |

---

## Stage 1 – Configuration and environment

**Code:** `runner.load_config`, `envinfo.collect`

| Input | Value |
|---|---|
| `configs/quick.yaml` | seed 20261005, trials 3, warm-up 1, 5 modes, datasets small 20 / medium 60 / large 200 names, `full_verify_sample` 16, `direct_sign_limit` medium 60 / large 100, wire: `nx_sample` 50, 300 queries, 10% NXDOMAIN, Zipf 1.1 |
| command-line overrides | none |
| `HARNESS` | `/work/harness/mtlbench` |

| Output | Value |
|---|---|
| `run_id` / folder | `<timestamp>-quick-demo` → `results/runs/<timestamp>-quick-demo/` |
| `config.yaml` | the config above, as used |
| `environment.json` | CPU, pinned core, kernel, governor, library versions and MTL commit (`-O2`), git commit and whether the tree was modified, image ID, `mtlbench` SHA-256, Python package versions |
| in memory | `cfg` (dict), `root` (run folder) |

## Stage 2 – Go/no-go gate (PS-036: defer if the reference implementation fails)

**Code:** `preflight.run(root, HARNESS)`

| Input | Value |
|---|---|
| reference tree | `/opt/mtl` (Verisign source, tests, tools) |
| algorithm | `SLH-DSA-SHA2-128s-MTL-SHA2-128` |
| harness | `mtlbench` |

| Check | Result |
|---|---|
| library files installed | ok |
| Verisign test suite `mtltest` | 89 passed, 0 failed |
| bundled test-vector scheme (warning only) | ML-DSA-44-MTL-SHAKE-128 (our algorithm is covered by the round trip) |
| `mtlkeygen` → `mtlsign` → `mtlverify`, 16 messages + 1 tampered | all verified, tampered rejected |
| `mtlbench` smoke test, 6-name zone, each of the 5 modes | all exit 0, no failures, every negative case rejected |
| `mtlverify` on the harness's own MTL signatures | accepted; a changed message rejected |
| OpenSSL on the harness's own SLH-DSA signatures (non-MTL) | accepted; a changed message rejected (interoperability check, not a NIST known-answer test) |

| Output | Value |
|---|---|
| `preflight.json` | `verdict: "GO"` + the checks listed above |
| control flow | GO → continue. NO-GO → print verdict, exit code 3, no benchmark numbers |

## Stage 3 – Zones and signing inputs

**Code:** `workload.build_zone`, `workload.signing_messages`, `write_messages` – loop: 3 datasets × 5 modes

| Input | Value |
|---|---|
| names per zone | 20 / 60 / 200 |
| zone seeds | 20261005 / 20261006 / 20261007 (`seed + dataset index`) |
| origin | `bench.example.` |
| modes | algorithm numbers 50 (MTL), 249 (SLH-DSA), 15, 13, 8 |
| caps | direct SLH-DSA signs 60 of 125 (medium) and 100 of 405 (large) |

What happens: each zone gets 4 apex RRsets, one data RRset per name (60% small A/AAAA, 30% medium MX/AAAA,
10% large TXT, drawn from the seed) and an NSEC3 chain. For every mode, each RRset becomes one signing input:
RRSIG fields with an empty signature (33 B) + the RRset in canonical form (RFC 4034 §3.1.8.1). Keys do not exist
yet; the DNSKEY carries a placeholder of the right length.

| Output | small | medium | large |
|---|---|---|---|
| RRsets (`sizes`) | 45 | 125 | 405 |
| apex / small / medium / large / NSEC3 | 4 / 14 / 3 / 3 / 21 | 4 / 32 / 17 / 11 / 61 | 4 / 117 / 70 / 13 / 201 |
| signing input bytes (MTL) | 9,311 | 33,941 | 69,840 |
| `messages.bin` (MTL) | 9,495 B | 34,445 B | 71,464 B |
| `messages_sample.bin` (direct only) | – | 60 messages | 100 messages |

Files: `datasets/<ds>/zone.txt`, `datasets/<ds>/<mode>/messages.bin` + `manifest.csv` (15 folders),
`datasets.csv`.
In memory for later stages: `zones` (3 Zone objects), `paths` (15 entries, e.g.
`("large","slh-dsa") → datasets/large/slh-dsa/messages_sample.bin`), `sizes = {small: 45, medium: 125, large: 405}`.

## Stage 4 – Sign and verify, repeated trials

**Code:** `runner.run_trials` → `harness.run_trial` → `mtlbench` (C) → Verisign MTL / liboqs / OpenSSL

| Input | Value |
|---|---|
| `paths`, `sizes` | from stage 3 |
| trials × datasets × modes | 3 × 3 × 5 = 45 measured runs, + 5 warm-up runs (small zone, discarded) |
| order | modes shuffled per (trial, dataset) with seed 20261005, e.g. trial 0 small: ecdsa-p256, slh-dsa-mtl, slh-dsa, rsa-2048, ed25519 |
| per run | `mtlbench <mtl|direct|classical> --messages … --algo … --full-verify 16 [--out-dir artifacts/<ds>/<mode>]` (out-dir on trial 0 only) |

What happens in one run: new process, new key pair; every message signed and verified, each library call
timed in nanoseconds; MTL additionally signs one ladder, extracts condensed signatures, verifies warm (ladder
cached) and cold (16 full signatures); every negative case (changed message, damaged or truncated signature,
signature for another message, wrong key, and for MTL a damaged ladder) must be rejected; one JSON line printed.

| Output | Value |
|---|---|
| `raw/*.json.gz` | 45 files, the exact JSON of each run |
| `artifacts/<ds>/<mode>/` | real signatures from trial 0 (MTL: `condensed.bin` + `signed_ladder.bin`; SLH-DSA on the large zone: 100 × 7,856 B) |
| `trials.csv` | 45 rows (one per run) |
| `ops.parquet` | one row per timed call |
| result | every run: exit 0, 0 verification failures, tampering rejected |

## Stage 5 – DNS answers, transmitted bytes, cache

**Code:** `runner.analyse` → `dnswire.query_workload`, `response_catalog`, `workload_accounting`; `cache.py`; `analytical.py`

| Input | Value |
|---|---|
| `zones` | from stage 3 (in memory) |
| real signatures | `artifacts/` from stage 4 (direct SLH-DSA on capped zones reuses its 100 signatures; all are 7,856 B) |
| `trials` | from stage 4 (limited flag, sizes, memory) |
| wire settings | UDP limits 1232 and 4096; 50 NXDOMAIN samples; 301 lookups (1 DNSKEY + 300, 10% non-existent, Zipf 1.1) |

What happens: dnspython builds real DNS queries and answers with the real signatures inside, and measures their
exact size. A transport model adds UDP, truncation (TC=1) and TCP retry. The lookup stream is replayed through a
resolver cache; MTL fetches the ladder once over TCP. Cached answers are loaded into dnspython's cache and measured.

| Output | Rows | What it holds |
|---|---|---|
| `responses.csv` | 5,196 | the exact size of every answer, per dataset, mode, record class and EDNS size |
| `workload.csv` | 30 | bytes on the wire for the lookup stream, per dataset × mode × UDP limit |
| `cache.csv` | 36 | resolver signature material, measured and analytical |
| `sizes.csv` | 27 | MTL signature and ladder sizes against the draft's formulas, and zone storage |

## Stage 6 – Statistics

**Code:** `runner.summarise`, `stats.summarize`, `stats.median_ci`

| Input | Value |
|---|---|
| `trials` | 45 rows (3 values per dataset × mode) |
| `frames` | `responses`, `workload` from stage 5 |

What happens: for every dataset × mode and timing column, the 3 trial values become n, mean, median, std,
p05, p95, min, max and a distribution-free CI of the median with its coverage (see methodology §6.2). Checks are combined into one
`all_checks_passed`. Workload totals and UDP fit percentages are copied in.

| Output | Example keys |
|---|---|
| `summary.json` | `trials.large.slh-dsa-mtl.sign_total_ns` (median, interval, n), `transmitted.large.slh-dsa-mtl.1232.dns_bytes`, `response_fit.large.slh-dsa-mtl.small` |

## Stage 7 – Report

**Code:** `report.write(root)`

| Input | Value |
|---|---|
| the run folder | `config.yaml`, `environment.json`, `preflight.json`, `summary.json`, all `.parquet` tables |

| Output | Value |
|---|---|
| `report.md`, `report.html` | full report: problem, method, results tables, discussion, limitations, appendices |
| `figures/` | `sign.png`, `verify.png`, `responses.png`, `workload.png`, `cache.png` |
| exit code | 0 (no trial failed a check); 1 if any did; 3 if stage 2 said NO-GO |

Open `report.html` in a browser; it is a single file with the figures embedded.
