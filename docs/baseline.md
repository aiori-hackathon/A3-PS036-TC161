# Pre-fix baseline

This snapshot records existing behavior before benchmark fixes. It is not an
official performance result or an approval of the current measurement methods.

## Source and host

- Commit: `4724f89cd95416dfbd5b92a23da756854b96a660` (clean worktree when the offline run started).
- Host: Intel Core Ultra 7 165H, 16 physical cores, 22 online logical CPUs, 62 GiB RAM.
- Host OS: Fedora Linux 43 Workstation; kernel `7.2.11-100.fc43.x86_64`.
- Docker client and server: `29.8.2`.
- Offline image: `cipherops-bench:pinned`, image ID `sha256:ba4ee37018d20b68e5a36ebfcaaa6e31f15c75259e549d73111c3d227d54e1b0`.
- Offline CPU set: logical CPU 2. Other workloads on the host were not controlled.
- Lab tools image: `cipherops-eco-tools:draft06`, image ID `sha256:61c87980c1cfe7c82dd24185b1081396a970f7eca49d2c784570868a0ef11acc`.

## Unit tests

```bash
PYTHONPATH=src:dashboard:ecosystem/src python -m pytest -q -m "not harness" -rs
```

Result: 84 passed, 3 skipped, 15 deselected; one Pillow deprecation warning.
The skipped tests require a local finished suite or Prepare outputs. The
deselected tests require the compiled harness. After Small Prepare, the same
command returned 86 passed, 1 skipped and 15 deselected: the two tests needing
Prepare outputs became runnable, while the finished-suite test still skipped.

## Offline quick run

```bash
bash scripts/run_benchmark.sh configs/quick.yaml
```

Run ID: `20261029T102947Z-quick-demo`. The run completed 45 trials with no
reported failed checks; preflight returned `GO`. Its bundled reference vectors
cover ML-DSA-44-MTL-SHAKE-128, while the target SLH-DSA configuration was
checked by tool round trips and harness smoke tests.

The configuration uses 20, 60 and 200 names, three trials and one warm-up.
Direct SLH-DSA signing is capped for medium and large, so those whole-zone
totals are extrapolations. The snapshot is in
[`tests/golden/quick/`](../tests/golden/quick/); each file was compared
byte-for-byte with the run output after copying.

## Small lab Prepare

The dashboard's current default is `draft06`; this records the default, not a
mentor-approved primary stack. The existing Small dashboard configuration was
prepared directly using the same two signing jobs as the dashboard, without
starting DNS services:

```bash
docker compose -f ecosystem/docker/build.yml --env-file ecosystem/stacks/draft06.env build tools
docker run --rm --network none -v "$PWD/ecosystem:/eco:z" -v "$PWD/src:/work/src:ro,z" cipherops-eco-tools:draft06 'python3 -m ecolab prepare configs/_dashboard.yaml --jobs 2'
```

The three scenario manifests are in
[`tests/golden/lab-small/`](../tests/golden/lab-small/). Each has four zones
with RRSIG counts 10, 11, 17 and 17. The source manifests were copied exactly.

## Comparison limits

The snapshots include timings, memory measurements, generated keys, hashes and
other run-dependent values. They are retained for provenance, not as exact
golden assertions. Candidate byte and analytical fields still need repeat-run
validation before an automated check can treat them as deterministic. No
repeat-run timing spread or lab network benchmark was collected in this phase.