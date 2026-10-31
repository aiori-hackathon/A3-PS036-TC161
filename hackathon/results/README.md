# Frozen results

The results the submission quotes, copied from the AWS node so they cannot change when the tool is re-run. The data is not
edited. Only selected files are copied (what each folder holds, and what was left out, is listed below).

| Folder | Track | Stack | What it is |
| --- | --- | --- | --- |
| [`offline-full-20261030T213016Z/`](offline-full-20261030T213016Z) | Offline benchmark | `latest` (FIPS 205) | the full run: 50 / 500 / 5,000 names, 10 trials |
| [`lab-full-draft06/lan-…`](lab-full-draft06/lan-20261031-025321) | Simulated lab | `draft06` (pre-standard SLH-DSA) | 50 / 500 / 5,000 names, 5 algorithms, 5 trials, LAN profile |
| [`lab-full-draft06/wan-…`](lab-full-draft06/wan-20261031-033539) | Simulated lab | `draft06` | the same zones, WAN profile (50 ms to the leaf) |
| [`lab-full-draft06/lossy-…`](lab-full-draft06/lossy-20261031-052353) | Simulated lab | `draft06` | the same zones, WAN profile with 1% loss |

Offline and lab results use different stacks and are never drawn on one chart. There is no single end-to-end FIPS 205
result: the `latest` lab's resolver answers SERVFAIL for plain SLH-DSA ([finding 1](../feedback.md)).

## What the submission needs, and where it is

| The submission needs | Where | Source files |
| --- | --- | --- |
| Signing time, verification time, signature and response size, truncation, cache, zone updates, with and without MTL, three sizes, repeated trials (PS-036 core) | [`offline-full-…/report.html`](offline-full-20261030T213016Z/report.html) (one file, figures embedded), [`report.md`](offline-full-20261030T213016Z/report.md) | `trials.csv`, `responses.csv`, `workload.csv`, `cache.csv`, `sizes.csv`, `evolution*.csv`, `summary.json` |
| Pinned, recorded environment, and the go/no-go gate | same folder | `environment.json`, `config.yaml`, `preflight.json` (verdict GO) |
| Noise floor of the measurement | same folder | `control.csv` (the A/A control), report section 4 |
| What a validating resolver does: bytes per hop, truncation, TCP retry, cache, on a real NSD + Unbound hierarchy | [`lab-full-draft06/lan-…/report.html`](lab-full-draft06/lan-20261031-025321/report.html), and the `wan-…` and `lossy-…` reports | `suite.json`, `params.json`, `signing_*.csv`, `runs/<size>/{summary,passes,hops}.csv` and `run.json` |
| The numbers on deck slide 9 | the offline run's `summary.json`, `sizes.csv`, `workload.csv` (slide table below) | see [slide 9](#deck-slide-9-where-each-number-comes-from) |
| Which machine they came from | [Reference machine](#reference-machine) | `environment.json`, each `suite.json` (`provenance`) |

### Left out (and why)

| Not copied | Size | Why |
| --- | --- | --- |
| Offline `ops.parquet`, `raw/`, `artifacts/`, `datasets/` and the `*.parquet` copies of the CSVs | about 47 MB | per-call timings, raw harness output and signature files; the CSVs and `summary.json` hold the results |
| Lab `queries.csv` (every lookup) and `pcap/` captures | about 1.5 GB | evidence for the summary tables, which are copied; regenerate by re-running |
| Lab `progress.log`, `status.json` | small | runtime state; `progress.log` ends with the node's failed report step (see below) |
| The node's `20261030T204956Z-quick-demo` and `eco-ui-small-draft06` | about 5 MB | a short demonstration and the dashboard's scratch lab, not results |

## Reference machine

All four runs came from one AWS EC2 node (the [`deploy/aws`](../../deploy/aws/README.md) template). It is a virtual
machine, so timings are indicative; the ratios between algorithms measured in the same trial are the reliable figure,
and the report measures its own noise floor (A/A control). Sizes and byte counts do not depend on the machine.

| | |
| --- | --- |
| CPU and memory | Intel Xeon Platinum 8488C, 8 vCPU, 32 GiB (the template's default `m7i.2xlarge`) |
| Operating system | Amazon Linux 2023, kernel 6.18.56; runs inside the pinned Fedora 42 images |
| Offline pinning | benchmark pinned to core 2 (`cpu_affinity [2]`), no network (`network_mode: none`); CPU governor not exposed by the VM |
| Lab | containers share all 8 vCPUs by design (not core-pinned) |
| Code | git `f12bb437582ce7816a5da9b3ed998d57f197f131` (on `main`); the node's tree had uncommitted changes, recorded as `-dirty` |
| Offline stack | `latest`: OpenSSL 3.6.0, liboqs 0.16.0, Verisign MTL `7cef0191da55048111d252a567b7614672424ed3`, `-O2` (FIPS 205 SLH-DSA); image `sha256:cf50b7a6…` |
| Lab stack | `draft06`: Verisign MTL 1.2.0 `93b05ef`, liboqs 0.14.0 (SPHINCS+-SHA2-128s-simple, same sizes as SLH-DSA) |

## Offline run: `offline-full-20261030T213016Z`

Run id `20261030T213016Z-ec2-ps036-offline-full10`, captured 2026-10-30 21:30 UTC. Configuration
([`config.yaml`](offline-full-20261030T213016Z/config.yaml)): zones of 50 / 500 / 5,000 names (105 / 1,005 / 10,005
RRsets), 10 trials plus 1 discarded warm-up, seed 20261005, 2,000 lookups per workload, direct SLH-DSA signs a capped sample
on the large zone (1,000 RRsets) and the whole-zone time is labelled *extrapolated*. Go/no-go gate: GO.

## Lab runs: `lab-full-draft06/`

Three suites of the same benchmark, one per network profile. The WAN and lossy suites reuse the zones the LAN suite signed
(`reuse` in their `suite.json`), so their signing and verification figures are the LAN suite's; they differ in the resolver
measurements.

| Suite | Wall time | Settings |
| --- | --- | --- |
| `lan-20261031-025321` | 42 min | 50 / 500 / 5,000 names (6 zones each), ECDSA, Ed25519, RSA, SLH-DSA, SLH-DSA + MTL; 5 measurement trials (cold, then warm), 5 signing trials on 100-name samples, 2,000 lookups per pass, UDP 1,232 |
| `wan-20261031-033539` | 108 min | as above, WAN profile: 2 ms to the client, 20 / 35 / 50 ms to root / TLD / leaf |
| `lossy-20261031-052353` | 111 min | as above, WAN profile plus 1% packet loss |

**The reports were generated here, not on the node.** The node's suites finished, but their report step failed
(`No module named 'pandas'`: the run script used the system Python instead of the virtualenv). The reports in these folders
were built from the node's downloaded suite data with the repository's own report code; `scripts/ec2_full_runs.sh` now
uses the virtualenv.

## Deck slide 9: where each number comes from

Zone of 10,005 RRsets, medians of 10 trials, offline run above, unless a row says otherwise.

| Slide 9 row | Direct SLH-DSA | SLH-DSA + MTL | Ed25519 | Source |
| --- | --- | --- | --- | --- |
| Signature in each RRSIG | 7,856 B | ≤ 284 B + one 8,120 B signed ladder | 64 B | `sizes.csv` |
| Sign the whole zone | ≈ 70 min (extrapolated from 1,000 signed) | 0.44 s | 0.27 s | `summary.json` `sign_total_ns` |
| Verify one answer, ladder cached | 417 µs | 5.2 µs | 88 µs | `verify_per_rrset_ns` |
| Verify the first answer, nothing cached | 418 µs | 418 µs | 88 µs | `verify_full_median_ns` |
| Signature storage for the zone | 78.6 MB | 2.7 MB (29× less) | 0.64 MB | `sizes.csv` |
| Answers that fit one 1,232-byte UDP packet | 0% (median 7,982 B) | 87.4% (median 411 B) | 91.5% (median 190 B) | `summary.json` `response_fit` |
| DNS bytes for 762 lookups | 11.08 MB; 762 TCP retries | 0.70 MB (16× less); 246 TCP | 0.38 MB; 43 TCP | `workload.csv` |
| Resolver upstream bytes, 2,000 lookups, 5,000-name lab hierarchy, cold (lab, LAN) | 29.23 MB; 1,361 truncated | 1.71 MB (17× less); 294 truncated | 0.81 MB; 64 truncated | `lab-…/lan-…/report.md` table 4 |

Not used: the old slide's "time for 762 lookups at a 50 ms round trip" and its "0.92 MB" wire figure came from a live
capture that is not in these runs. Median lookup latency in the lab is about the same for every algorithm (about 0.24 ms cold
on LAN, 2.7 ms on WAN), so the differences are in bytes and truncation, not in median time.

## Reproduce

`scripts/ec2_full_runs.sh offline` and `scripts/ec2_full_runs.sh simulated` on the node, then download the run folders from
the dashboard (**Offline benchmark → Results**, **Simulated lab → Benchmark → Results**).
