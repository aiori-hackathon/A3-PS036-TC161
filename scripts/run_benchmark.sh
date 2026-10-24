#!/usr/bin/env bash
# Build the pinned image and run the benchmark inside it (Linux/macOS host).
#   scripts/run_benchmark.sh [config] [cpuset]
#   scripts/run_benchmark.sh preflight
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose -f docker/compose.yml build

sha="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
[ -n "$(git status --porcelain 2>/dev/null)" ] && sha="${sha}-dirty"
export CIPHEROPS_GIT_SHA="$sha"
export CIPHEROPS_HOST_IMAGE_ID="$(docker image inspect cipherops-bench:pinned --format '{{.Id}}')"
export CIPHEROPS_CPUSET="${2:-2}"

if [ "${1:-}" = "preflight" ]; then
  inner="python3 -m cipherops.runner preflight --results results"
else
  inner="python3 -m cipherops.runner run --config ${1:-configs/scenarios.yaml}"
fi
docker compose -f docker/compose.yml run --rm bench "$inner"
