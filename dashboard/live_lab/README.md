# Dashboard guide

The CipherOps dashboard is one Streamlit app for building the lab, querying it, benchmarking, comparing results and
learning the background. Hover the ⓘ icons for what each setting and measurement means.

```bash
pip install -r requirements-dashboard.txt
streamlit run dashboard/live_lab/app.py
```

Needs Docker (except for **Learn** and for viewing saved results). On AWS or in Codespaces: [AWS](../../deploy/aws/README.md), [Codespaces](../../docs/codespaces.md).
For a first visit use the **Small** zone size, and use the **`draft06`** image version for any MTL-versus-plain
comparison: on `latest` the lab's Unbound answers SERVFAIL for plain SLH-DSA (the dashboard warns about it).

**Minimal mode** (`CIPHEROPS_MINIMAL=1`) is a read-mostly second instance for evaluators: results, Compare, Learn, and
lookups against a running lab; nothing that changes the lab. Full detail: [MINIMAL.md](../../deploy/aws/MINIMAL.md).

```bash
CIPHEROPS_MINIMAL=1 streamlit run dashboard/live_lab/app.py --server.address 127.0.0.1 --server.port 8502
```

```powershell
# Windows: set the process environment with $env:…  (`$CIPHEROPS_MINIMAL=1` is not inherited by Streamlit)
$env:CIPHEROPS_MINIMAL = "1"
python -m streamlit run dashboard/live_lab/app.py --server.address 127.0.0.1 --server.port 8502
```

## Tracks and pages

The home page shows this node's prerequisites and offers the two ways of benchmarking as tracks. A track is one page:
a **prerequisite panel** on top, then numbered steps; the sidebar shows the settings of the step you are on, and a tick
marks a finished step. Explanations are hidden unless **Help text** (sidebar) is on.

| Track / step | What it is for | Needs |
| --- | --- | --- |
| **Prerequisite** (offline) | `cipherops-bench:latest` is on this node and matches its stack profile; build or pull it | Docker |
| **Offline benchmark** ① Run | the harness on one pinned core, no network; pick *Quick check* or *Full run* | the image |
| ② Results | tables, charts and downloads of a saved run | a finished run |
| **Prerequisite** (simulated) | the three lab images for the chosen version (`draft06` or `latest`) are on this node | Docker |
| **Simulated lab** ① Prepare | choose zone size and algorithms, **Sign zones**, **Start** the lab | the lab images |
| ② Query | switch algorithm, send a query or `dig`, compare cold and warm cache, flush the cache, capture and decode packets | a started lab |
| ③ Benchmark | signs, starts, measures and stops its own lab for each zone size (steps ① and ② are not needed) | the lab images |
| **Compare** | *Algorithms side by side*, *Run against run*, and *Zone updates* | a finished benchmark |
| **Learn** | why DNSSEC needs MTL; what an operator does | nothing |
| **Node images** (*Manage images* on Home) | both image sets in one place, with details, stack pins and terminal commands | Docker |
| **Reset** | clear only the scopes you pick (session, captures, runs, signed zones, cache) | nothing |

Old links still work: `?tile=lab`, `?tile=live` and `?tile=benchmark` open the matching simulated-lab step, and
`?tile=offline&step=run` opens a step directly.

## Benchmark

### Offline (harness)

Times every signing and verification call on one dedicated core with no network, and counts DNS message bytes. Choose a
**Quick check** (20 / 60 / 200 names, 3 runs) or the **Full run** (50 / 500 / 5,000 names, 10 runs, about 5 hours) and
press **Run offline benchmark**. It builds the pinned image on first use, then runs as a background job: you can leave
the page, and **Results** fills in when it finishes. While it runs, image builds, signing and the simulated benchmark
are refused, because anything else on the CPU would distort the timings.

It is the same harness as `scripts/run_benchmark.sh`; the output is `results/runs/<id>/`
([outputs reference](../../docs/outputs-reference.md)).

### Simulated (Verisign stack)

For each zone size (default 50, 500 and 5,000 names) one press of **Run benchmark** builds the hierarchy, signs it with
every chosen algorithm (timed), repeats signing and verification on sample zones, starts the lab, replays the same
seeded query workload with repeated cold-cache and warm-cache runs, and stops the lab. It needs only the built images,
runs in the background, and writes `ecosystem/results/suites/<id>/`.

- **Algorithms:** SLH-DSA-SHA2-128s with MTL (`mtl`) and without (`slh-dsa`), using Verisign's MTL-mode ldns, NSD and
  Unbound. `ecdsa` is an optional classical reference.
- **Signing and verification time:** each signing repeat signs two sample zones; a straight line through them gives a
  fixed cost per zone and a cost per signature, applied to every zone of the size. This is an extrapolation and is
  labelled as one. The full zones are also signed once, and that time is shown next to it.
- **Response size, truncation, cache size, latency:** measured in the resolver run, per tier (root, TLD, leaf), cold and
  warm. Latency is reported as p50 and p95.
- **Runs and statistics:** default 5 runs and 5 signing repeats, each preceded by one discarded warm-up. The bracketed
  interval is a distribution-free interval of the median; its real coverage is 94% for 5 runs (75% for 3; at least 6
  are needed for 95%). See [methodology §6.2](../../docs/methodology.md#62-statistics).
- **Failed queries are not data.** Latency counts answered queries only (NOERROR, NXDOMAIN). A run with any SERVFAIL,
  timeout or other failure is marked invalid and left out of every figure; its failures stay counted and a warning is
  shown. An algorithm the resolver cannot validate therefore shows no numbers.
- **Not core-pinned.** Lab containers share all host CPUs and the CPU governor is recorded, not changed. Use bytes and
  relative comparisons; treat absolute milliseconds as indicative.
- **Recorded.** Every setting, the workload, network profiles, pinned images and host are stored in `suite.json` (git
  commit and whether the tree was modified, CPU, memory, kernel, governor, Docker version, component versions, image
  IDs) and shown under *Settings and environment recorded*. A finished benchmark also writes `report.md`, `report.html`
  and `figures/`; regenerate with `python -m live_lab.suite_report <suite-id>` from a shell with `dashboard` on
  `PYTHONPATH`.

Every figure in Compare is badged **Measured**, **Estimated** or **Analytical**. The analytical ones (signature storage
from the draft's size formulas) are kept apart and never mixed with measured cache.

### The prerequisite: images on this node

Each track's panel (and the **Node images** page) only makes sure the pinned images are on the benchmark node; it signs
and measures nothing. There are two independent image sets:

- **Offline benchmark**: `cipherops-bench:latest`. The version is fixed, because the harness needs the stack whose
  liboqs has the FIPS 205 names.
- **Simulated lab**: `cipherops-eco-{nsd,unbound,tools}:<version>`, for the lab version picked in the panel
  (`draft06` or `latest`). Only the simulated lab uses it.

When something is missing the panel shows what and offers, inline, how to get it:

- **Build locally** compiles OpenSSL, liboqs and MTL once (tens of minutes) and the lab servers on top, all from
  `docker/Dockerfile`.
- **Pull from registry** downloads images someone published (default
  `ghcr.io/hackchamp25`, or the `CIPHEROPS_REGISTRY` environment variable) and tags them with the
  local names. Publish from a machine that has the images and is logged in (`docker login ghcr.io`) with
  `scripts/publish_images.sh <stack>`; it pushes only images that match the stack profile. A new GitHub package
  starts private, so its owner makes it public once.

Both run as a background job with a live log. Afterwards each image is checked against the stack profile
(`ecosystem/stacks/<stack>.env`) through the `io.cipherops.*` labels it carries; the **Images** tab shows ready,
missing or does-not-match for each, and Benchmark and Lab only use images that pass. The same is available as
`python scripts/images.py status|build|pull`. The offline harness needs the `latest` stack (its liboqs has the FIPS
205 names); the lab works with either.

### Numbers you can change

| Where | Numbers | Needs signing again |
| --- | --- | --- |
| Simulated lab ① Prepare → Zone size → **Custom…** | TLDs, leaf zones, names per leaf zone, leaf servers, seed | yes |
| Simulated lab ③ Benchmark → **Workload mix** | NXDOMAIN share, zone and name popularity skew, query timeout | no |
| Simulated lab ③ Benchmark → **Network profiles** | delay to client, root, TLD and leaf, and loss, for WAN and lossy | no |

Prepare shows a **Planned lab** card (zones, names, containers, rough signing time) and the exact YAML. A custom shape
gets its own lab folder named after a hash of its numbers (`eco-ui-custom-<hash>`), so results from different shapes
never mix. Only one lab runs at a time: Start stops any other.

Name a benchmark in the sidebar; it is stored with the date and time (`<YYYYMMDD-HHMMSS>_<name>`). Live captures take an
optional name the same way. Names keep letters, digits, `-` and `_`.

## Compare

- **Algorithms side by side:** every algorithm of one run, with the ratio to a baseline (paired per-run intervals for
  the offline timings).
- **Run against run:** two saved runs (stack profiles, repeats, machines) with what differs between them spelled out.
- **Zone updates:** what a zone change costs, from the offline zone-update experiment
  ([methodology §6.4](../../docs/methodology.md#64-evolving-zone-mtl-on-a-series-that-keeps-growing)): re-sign time per
  update, response size, stale resolver cache, and MTL internals (ladder and rungs) for readers of the draft.

Offline and simulated data are never drawn together.

## Signing replay and live progress

After **Sign zones**, step ① Prepare opens an animated replay of how the hierarchy was signed (also under its **Signing
replay**). It plays bottom-up: leaf zones, TLDs, then the root, with each DS record travelling to its parent. For `mtl`
the RRsets become leaves of a Merkle tree, the ladder is signed once and each RRset keeps a short path; for the other
algorithms every RRset is signed on its own, with a bar per signature on one scale. Counts, times and bytes are
**measured** from the signing manifest, signature and ladder sizes are **analytical**, and the tree shape is
**illustrative** (drawn from each zone's real signature count, because the signing library does not report its tree).
Code: `prepare_story.py`, `assets/prepare_player.html`.

Building images, signing and the offline run are background jobs (`build_job.py`, `prepare_job.py`, `offline_job.py`;
state in `ecosystem/jobs/`). The page shows their progress and log live, offers **Cancel**, and can be reloaded or
closed and picked up again.

A signature count is the number of real RRSIG records in the signed zone: a leaf zone has 2 per name (its record and its
NSEC3 proof) plus 7 of its own, a TLD 2 per child plus 7, the root 2 per TLD plus 8 (Small = 55, Medium = 353).
