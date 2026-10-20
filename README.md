# Cipherops

**PQC DNSSEC MTL Benchmark Using a Reference Implementation**
AIORI Hackathon 3 · Problem statement PS-036 · Team A3-PS036-TC161

[![Cipherops project video](https://img.youtube.com/vi/FaVfeO5WTV8/hqdefault.jpg)](https://youtu.be/FaVfeO5WTV8?si=PtrE4kbB7UlKj6lL)

▶ [Watch the project video on YouTube](https://youtu.be/FaVfeO5WTV8?si=PtrE4kbB7UlKj6lL)

---

## What this is

Post-quantum signatures are much larger than today's DNSSEC signatures, so DNS answers stop fitting in a single UDP
packet. **Merkle Tree Ladder (MTL) mode** signs a whole zone once and gives each record a short proof instead.

Cipherops is a benchmark that measures what MTL changes. It signs identical DNSSEC zones with five algorithms and
measures **signing time, verification time, transmitted bytes and cache usage**:

| Mode                                | Algorithm                     | Library                                                                  |
| ----------------------------------- | ----------------------------- | ------------------------------------------------------------------------ |
| `slh-dsa-mtl`                       | SLH-DSA-SHA2-128s in MTL mode | [Verisign MTL reference implementation](https://github.com/verisign/MTL) |
| `slh-dsa`                           | SLH-DSA-SHA2-128s directly    | liboqs                                                                   |
| `ed25519`, `ecdsa-p256`, `rsa-2048` | Classical DNSSEC algorithms   | OpenSSL                                                                  |

Everything runs inside one pinned Docker container (Fedora 42, OpenSSL 3.6.0, liboqs 0.16.0, Verisign MTL commit
`7cef019`). No cryptography is implemented here; the reference libraries are used unmodified.

![Cipherops architecture](docs/architecture-slide1.png)

---

## Requirements

- **Docker:** Docker Desktop on Windows and macOS, Docker Engine on Linux. Give it at least 3 CPUs and 6 GB of memory.
- **git**
- **Python 3.11+:** only for the dashboard

```bash
git clone https://github.com/aiori-hackathon/A3-PS036-TC161/
cd Cipherops
```

The first run builds the Docker image (OpenSSL, liboqs and MTL from source), which takes a few minutes. Later runs
reuse it.

---

## Usage

### 1. Check the setup (go/no-go gate)

Runs Verisign's own tests and a sign/verify round trip. If anything fails, the benchmark refuses to run.

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File .\scripts\run_benchmark.ps1 -Command preflight
```

```bash
# macOS / Linux
bash scripts/run_benchmark.sh preflight
```

### 2. Run the benchmark

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File .\scripts\run_benchmark.ps1 -Config configs/quick.yaml   # demo, a few minutes
powershell -ExecutionPolicy Bypass -File .\scripts\run_benchmark.ps1                              # full run, ~1.5 hours
```

```bash
# macOS / Linux
bash scripts/run_benchmark.sh configs/quick.yaml
bash scripts/run_benchmark.sh
```

Progress is printed for each of the 7 stages. At the end it prints the run folder, for example
`results/runs/20261010T130246Z-ps036-pinned`.

### 3. Live validation (optional)

Replays DNS lookups against a real NSD server over simulated LAN, WAN (50 ms) and lossy (1% loss) networks, and
captures every packet. Pass the run folder name from step 2.

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File .\scripts\run_live.ps1 -Run <run-folder>
```

```bash
# macOS / Linux
docker compose -f docker/compose.yml run --rm live \
  "python3 -m cipherops.runner live --run results/runs/<run-folder>"
```

### 4. View the results

```bash
pip install -r requirements-dashboard.txt
streamlit run dashboard/app.py
```

The dashboard opens in your browser. Pick a run in the sidebar.

---

## Output

Each run creates its own folder under `results/runs/`:

| Output                                              | What it contains                                                          |
| --------------------------------------------------- | ------------------------------------------------------------------------- |
| `report.html`, `report.md`                          | Full report: method, tables, figures, environment                         |
| `trials.csv`                                        | Signing and verification time per trial, per zone, per algorithm          |
| `responses.csv`                                     | Size of every DNS answer each algorithm produces                          |
| `workload.csv`                                      | Bytes, truncations and TCP fallbacks for the lookup workload              |
| `cache.csv`                                         | Resolver and signer cache usage (measured and estimated)                  |
| `sizes.csv`                                         | MTL signature and ladder sizes compared with the draft's formulas         |
| `summary.json`                                      | Medians and 95% confidence intervals                                      |
| `figures/`                                          | Charts used in the report                                                 |
| `config.yaml`, `environment.json`, `preflight.json` | Exact settings, software versions and gate results                        |
| `artifacts/`, `datasets/`, `raw/`                   | Signatures, ladders, keys, zones and raw timings                          |
| `live.csv`, `live_summary.csv`, `pcap/`             | Live mode only: every real lookup and packet captures (open in Wireshark) |

**Dashboard tabs:** signing and verification charts, transmitted bytes, cache usage, MTL sizes against the draft, a
live demo (build answers, browse captured packets, or send real queries) and reproducibility details. See the
[dashboard guide](docs/dashboard-guide.md).

---

## Configuration

Settings live in `configs/`. `scenarios.yaml` is the full run and `quick.yaml` the demo.

| Setting                   | Meaning                                                                                  |
| ------------------------- | ---------------------------------------------------------------------------------------- |
| `trials`, `warmup_trials` | Repeated measurements per zone and algorithm                                             |
| `modes`                   | Algorithms to compare                                                                    |
| `datasets`                | Zone sizes (number of names)                                                             |
| `direct_sign_limit`       | Cap on direct SLH-DSA signatures per trial (slow, ~0.55 s each); `null` signs everything |
| `wire.workload`           | Lookup workload: number of queries, share of non-existent names, popularity skew         |
| `seed`                    | Fixed seed so zones and workloads are identical across runs                              |

Use another config with `-Config <file>` (Windows) or as the first argument (macOS / Linux). Choose the CPU core with
`-Cpuset <n>` (Windows) or the second argument (macOS / Linux).

---

## Useful commands

```bash
# Open a shell inside the container
docker compose -f docker/compose.yml run --rm bench bash

# Run the tests
pytest                                                                     # host-side tests
docker compose -f docker/compose.yml run --rm bench "pytest -m harness -q"  # library and harness gates
```

If Windows says "running scripts is disabled", use the `powershell -ExecutionPolicy Bypass -File ...` form shown
above.

---

## Repository layout

```
configs/        run settings
docker/         pinned image and compose services
harness/        mtlbench.c, the C benchmark linked to the reference libraries
src/cipherops/  runner, gate, zone builder, DNS wire model, cache, statistics, report, live mode
dashboard/      Streamlit dashboard
scripts/        run, live and helper scripts
tests/          unit tests and library gates
docs/           methodology, architecture and guides
```

## Documentation

- [Methodology](docs/methodology.md): what is measured and how
- [Architecture](docs/architecture.md): end-to-end flow, file by file
- [Outputs reference](docs/outputs-reference.md): every result file and column
- [Dashboard guide](docs/dashboard-guide.md): every tab and option

## Team

Akash Joshi · Anitha · Induradhakrishnan

## License

MIT, see [LICENSE](LICENSE). The Verisign MTL library, liboqs, OpenSSL and NSD are fetched at build time under their
own licences.
