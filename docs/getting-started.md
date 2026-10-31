# Getting started

Install, run and test CipherOps on your own machine. On AWS or in Codespaces instead? See [AWS](../deploy/aws/README.md)
and [Codespaces](codespaces.md).

## Requirements

- **Docker** (Desktop or Engine) with Compose v2: about 3 CPU cores and 6 GB RAM. The offline harness pins itself to
  core 2, so the host needs at least 3 cores; pass another core number as the script's second argument if not.
- **git**
- **Python 3.11 or newer** for the dashboard and the host tests only.

```bash
git clone https://github.com/aiori-hackathon/A3-PS036-TC161.git
cd A3-PS036-TC161
```

The first image build compiles OpenSSL, liboqs and MTL from source: tens of minutes. Later runs reuse the build cache,
and the dashboard can pull published images instead.

## Dashboard

```bash
pip install -r requirements-dashboard.txt
streamlit run dashboard/live_lab/app.py
```

The easiest way to use everything below; see the [dashboard guide](../dashboard/live_lab/README.md).

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

The full run is long because plain SLH-DSA signs about 0.6 s per RRset.

**3. Live validation (optional).** Serves a finished run's signed zone from a real NSD and replays lookups over LAN, WAN
and lossy network profiles, capturing the packets ([how it works](architecture.md#5-live-mode-flow-real-server-real-packets)).

```bash
bash scripts/run_live.sh <run-folder> lan 1232 1      # one profile, one UDP size, one repeat
```

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_live.ps1 -Run <run-folder>
```

**4. Read the results.** Open `results/runs/<id>/report.html`, or use the dashboard (**Offline benchmark →
Results**, then **Compare**). Every file and column: [outputs reference](outputs-reference.md).

## Configuration

`configs/scenarios.yaml` is the full matrix and `configs/quick.yaml` the short demonstration; both are commented. Software
versions come from a **stack profile** chosen with `CIPHEROPS_STACK=<profile>` (default `latest` for the harness), see
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

What each test file covers: [architecture §4.16](architecture.md#416-tests).

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Permission denied` under `ecosystem/work` or `results` | Docker creates files as root on Linux. The dashboard's **Reset** removes them; by hand use `sudo rm -rf`. |
| **Sign zones** or **Start** is disabled in Simulated lab | The lab images for the chosen stack are not on this node, or do not match its profile. Open **Manage images** on Home and build or pull them. |
| Every query to `slh-dsa` returns SERVFAIL on `latest` | Known limitation of that stack; use `draft06` for the lab. |
| `cpuset` error when the harness container starts | The host has fewer than 3 cores; pass another core number as the script's second argument. |
| Buttons are greyed out or say "busy" | A benchmark, image build or signing job is running and owns the lab or the CPU; wait or cancel it. |
| An image build or pull seems idle | It runs in the background. Open **Manage images** to see the live log, or cancel it. |
| A pulled image shows **does not match** | It was built from other pins than this checkout's stack profile, so it is never used. Pull a matching tag or build locally. |
