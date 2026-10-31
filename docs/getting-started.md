# Getting started

How to install, run and test CipherOps on your own machine. In GitHub Codespaces instead? See
[Codespaces](codespaces.md).

## Requirements

- **Docker** (Desktop or Engine) with Compose v2: about 3 CPU cores and 6 GB RAM. The offline harness pins itself to
  core 2, so the host needs at least 3 cores; pass another core number as the script's second argument if not.
- **git**
- **Python 3.11 or newer** for the dashboard and the host tests only.

```bash
git clone https://github.com/aiori-hackathon/A3-PS036-TC161.git
cd A3-PS036-TC161
```

The first image build compiles OpenSSL, liboqs and MTL from source: several minutes up to tens of minutes. Later runs
reuse the build cache.

## Dashboard

```bash
pip install -r requirements-dashboard.txt
streamlit run dashboard/live_lab/app.py
```

The dashboard is the easiest way to use everything below: the **Offline benchmark** track runs the harness, the
**Simulated lab** track runs the Docker lab, **Compare** draws the results. What each track and step does:
[dashboard guide](../dashboard/live_lab/README.md).

## Offline benchmark (command line)

The harness times every signing and verification call on one dedicated core with no network, and counts DNS message
bytes.

**1. Preflight (go / no-go).** Builds the image and checks that the reference library works. If the gate fails, no
numbers are produced and the run exits with `NO-GO`.

```bash
bash scripts/run_benchmark.sh preflight
```

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File .\scripts\run_benchmark.ps1 -Command preflight
```

**2. Run.**

```bash
bash scripts/run_benchmark.sh configs/quick.yaml      # short demonstration (20 / 60 / 200 names, 3 trials)
bash scripts/run_benchmark.sh                         # full run (50 / 500 / 5,000 names, 10 trials), about 5 hours
```

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_benchmark.ps1 -Config configs/quick.yaml
powershell -ExecutionPolicy Bypass -File .\scripts\run_benchmark.ps1
```

Why the full run is long: plain SLH-DSA signs about 2,700 RRsets per trial at roughly 0.6 s each.

**3. Live validation (optional).** Serves a finished run's signed zone from a real NSD and replays lookups over LAN, WAN
and lossy network profiles, capturing the packets.

```bash
bash scripts/run_live.sh <run-folder> lan 1232 1      # one profile, one UDP size, one repeat
```

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_live.ps1 -Run <run-folder>
```

**4. Read the results.** Open `results/runs/<id>/report.html`, or use the dashboard (**Benchmark → Offline →
Results**, then **Compare**). Every file and column: [outputs reference](outputs-reference.md).

## Configuration

`configs/scenarios.yaml` is the full matrix and `configs/quick.yaml` the short demonstration. The knobs are trials,
modes, zone sizes, the query workload and the seed; both files are commented. Software versions come from a **stack
profile** chosen with `CIPHEROPS_STACK=<profile>` (default `latest` for the harness). See
[scope and limits](scope-and-limits.md#software-stacks).

## Tests

```bash
pip install -r requirements-dashboard.txt
python -m pytest -m "not harness"          # host tests, no Docker needed
```

The harness gates need the compiled library, so they run inside the image:

```bash
docker run --rm --network none cipherops-bench:latest \
  "python3 -m pytest tests/test_harness.py tests/test_crosscheck.py -q"
```

What each test file covers: [architecture §6.16](architecture.md#616-tests).

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Permission denied` under `ecosystem/work` or `results` | Docker creates files as root on Linux. The dashboard's **Reset** removes them; by hand use `sudo rm -rf`. |
| **Sign zones** or **Start** is disabled in Lab | The lab images for the chosen stack are not on this node, or do not match its profile. Open Setup and build or pull them. |
| Every query to `slh-dsa` returns SERVFAIL on `latest` | Known limitation of that stack; use `draft06` for the lab. |
| `cpuset` error when the harness container starts | The host has fewer than 3 cores; pass another core number as the script's second argument. |
| Buttons are greyed out or say "busy" | A benchmark, image build or signing job is running and owns the lab or the CPU; wait or cancel it. |
| An image build or pull seems idle | It runs in the background. Open Setup to see the live log, or cancel it. |
| A pulled image shows **does not match** | It was built from other pins than this checkout's stack profile, so it is never used. Pull a matching tag or build locally. |
