# Frozen results

Results quoted by the submission are copied here so they cannot change when the tool is re-run. The data is not edited
after copying. The only change to the offline quick demo is that `report.md` and `report.html` were regenerated once
from the same data after a wording change to the report subtitle; every number and figure is identical.

| Frozen run | Track | Stack | What it is |
| --- | --- | --- | --- |
| [`quick-demo-20261030T074948Z`](#quick-demo-20261030t074948z) | Offline benchmark | `latest` (FIPS 205) | 3-trial quick demonstration |
| [`lab-small-draft06-20261031T043656Z`](#lab-small-draft06-20261031t043656z) | Simulated lab | `draft06` (pre-standard SLH-DSA) | small finished lab benchmark |
| full offline run | Offline benchmark | `latest` | **not frozen yet**, see [Still to freeze](#still-to-freeze) |

**Deck slide 9 is not backed by anything in this folder** (10,005-RRset zone, "medians of 5 trials", 762 lookups; the full
run is 10 trials). The numbers in the [readout](../readout.md) are the ones that trace to a report here.

## quick-demo-20261030T074948Z

A **quick demonstration**, not the full run.

| | |
| --- | --- |
| Run id | `20261030T074948Z-quick-demo` |
| Configuration | [`config.yaml`](quick-demo-20261030T074948Z/config.yaml): 3 trials plus 1 discarded warm-up, zones of 20 / 60 / 200 names (45 / 125 / 405 RRsets), seed 20261005 |
| Stack profile | `latest`: OpenSSL 3.6.0, liboqs 0.16.0, Verisign MTL `7cef0191da55048111d252a567b7614672424ed3` (FIPS 205 SLH-DSA) |
| Code version | git `90ecbc1f20cc3139d070ac97d943f05ea97ec50e` (clean tree) |
| Image | `sha256:d05eeb6684c037f5c48afe348a1fe1cacb7c580e4810e2ed9ba9a71643253524` |
| Machine | Intel Core Ultra 7 165H laptop, 22 logical CPUs, 62 GiB RAM, Fedora Linux host kernel 7.2.11 |
| Pinning | benchmark pinned to CPU core 2; **CPU governor `powersave`** |
| Network | none (container `network_mode: none`) |
| Gate | go/no-go: GO |
| Report | [`report.html`](quick-demo-20261030T074948Z/report.html) (self-contained), [`report.md`](quick-demo-20261030T074948Z/report.md) |

**How to read the numbers.** The machine is a laptop with a power-saving governor, so absolute milliseconds are
indicative; the ratios between algorithms measured in the same trial are more reliable. Three trials give a 75%
interval of the median, not 95% (see [methodology §6.2](../../docs/methodology.md#62-statistics)). The run's own
`environment.json` is the full record of versions and hardware. Sizes and byte counts do not depend on the machine.

### Contents

`report.html`, `report.md`, `figures/`, `config.yaml`, `environment.json`, `preflight.json`, `summary.json` and the CSV
tables (`trials`, `control`, `responses`, `workload`, `sizes`, `cache`, `datasets`, `evolution`, `evolution_direct`).
Column meanings: [outputs reference](../../docs/outputs-reference.md). The Parquet files, raw harness output and signature
artifacts of the original run are not copied.

## lab-small-draft06-20261031T043656Z

A **small finished simulated-lab benchmark**, not the full lab run. It exists so the lab track has frozen evidence of its
own.

| | |
| --- | --- |
| Suite id | `20261031-095503_small-lab-draft06` |
| Configuration | [`params.json`](lab-small-draft06-20261031T043656Z/params.json), [`suite.json`](lab-small-draft06-20261031T043656Z/suite.json): zone sizes 50 and 500 names (6 zones each: root, TLD, 4 leaf), 5 scenarios (ECDSA P-256, Ed25519, RSA-2048, SLH-DSA, SLH-DSA with MTL), 3 measurement trials (cold, then warm), 3 signing trials on 20-name samples, 500 lookups per pass, `lan` profile, UDP 1,232 |
| Stack profile | `draft06`: Verisign MTL `93b05ef73dab53b953cc4e262401a2ce9bc9e431` (v1.2.0), liboqs 0.14.0, SPHINCS+-SHA2-128s-simple (pre-standard SLH-DSA, **not** FIPS 205) |
| Code version | git `d01fa1b0bd4f9b4f000079b10d1474e94d60be8f`; the tree had uncommitted edits to documentation, docstrings and one report line, and none to the lab or the suite logic |
| Images | `cipherops-eco-nsd:draft06` `sha256:b21e598b…`, `-unbound:draft06` `sha256:642f9c8a…`, `-tools:draft06` `sha256:9b030477…` (full IDs in the report) |
| Machine | Intel Core Ultra 7 165H laptop, 22 logical CPUs, 62 GiB RAM, Fedora Linux host kernel 7.2.11; **CPU governor `powersave`**; containers share all host CPUs (not core-pinned by design) |
| Network | internal Docker bridge, no Internet |
| Wall time | 713 s |
| Report | [`report.html`](lab-small-draft06-20261031T043656Z/report.html) (self-contained), [`report.md`](lab-small-draft06-20261031T043656Z/report.md) |

**How to read it.** Signing, verification and latency are indicative (laptop, power-saving governor, shared CPUs); the
byte counts, truncations and cache sizes do not depend on the machine. The report's "medium" and "large" headings both
mean the 500-name dataset, because only two sizes were run. The `latest` stack's lab cannot be benchmarked for plain
SLH-DSA (its resolver answers SERVFAIL, [finding 1](../feedback.md)), so the lab result is on the pre-standard build.

Contents: `report.html`, `report.md`, `figures/`, `params.json`, `suite.json`, `signing_full.csv`,
`signing_trials_50.csv`, `signing_trials_500.csv`. The raw per-size run folders, packet captures and progress log are not
copied.

## Reference machine

Every headline number must name the machine it came from. **The reference machine for the full offline run is the AWS node
from [`deploy/aws`](../../deploy/aws/README.md)**, with these rules:

- One instance type for the whole run, written on slide 9 and recorded in the run's `environment.json` (CPU model, kernel,
  pinned core). A bare-metal type such as `m7i.metal-24xl` gives the cleanest timings; on a virtual type (the template's
  default `m7i.2xlarge`) an offline vCPU is a hyperthread on shared hardware and the numbers stay indicative.
- The offline benchmark pinned to one core, nothing else running (not the lab, not the dashboard's other jobs).
- Laptop runs, including the two frozen above, are demonstrations and say so.

## Still to freeze

- The **full offline run** (50 / 500 / 5,000 names, 10 trials, about 5 hours) on the reference machine:
  `scripts/ec2_full_runs.sh offline` on the node, then copy `results/runs/<id>/` here (report, figures, CSV tables,
  `config.yaml`, `environment.json`, `preflight.json`, `summary.json`), add a section like the ones above, and update slide 9
  from that report: its own trial count, zone size, lookups and machine.
- Optionally the **full lab run** on the same node (`scripts/ec2_full_runs.sh simulated`), to replace the small lab
  benchmark above.

Timings from a Codespace or other shared virtual CPUs are indicative only and should not be frozen as headline results.
