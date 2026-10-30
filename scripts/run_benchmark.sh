#!/usr/bin/env bash
# Build the pinned image and run the benchmark inside it (Linux/macOS host).
#   scripts/run_benchmark.sh [config] [cpuset]
#   scripts/run_benchmark.sh preflight
# The software stack is chosen with CIPHEROPS_STACK (default latest): ecosystem/stacks/<stack>.env is the single
# source of the pinned versions, and the live lab is built from the same file. The offline harness needs the stack
# whose liboqs has the FIPS 205 SLH-DSA names (latest); on draft06 the go/no-go gate reports NO-GO.
#   CIPHEROPS_STACK=draft06 scripts/run_benchmark.sh preflight
set -euo pipefail
cd "$(dirname "$0")/.."

stack="${CIPHEROPS_STACK:-latest}"
env_file="ecosystem/stacks/${stack}.env"
[ -f "${env_file}" ] || { echo "unknown stack '${stack}': no ${env_file}" >&2; exit 1; }
compose=(docker compose --env-file "${env_file}" -f docker/compose.yml)

# The image is the pinned artefact: build it here, or set CIPHEROPS_SKIP_BUILD=1 to run the one already on this host
# (built earlier, or pulled from a registry in the dashboard's Setup).
if [ "${CIPHEROPS_SKIP_BUILD:-0}" = "1" ]; then
  docker image inspect "cipherops-bench:${stack}" >/dev/null 2>&1 \
    || { echo "cipherops-bench:${stack} is not on this host; build or pull it first (unset CIPHEROPS_SKIP_BUILD to build)." >&2; exit 1; }
else
  docker compose --env-file "${env_file}" -f docker/build.yml build bench
fi

sha="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
[ -n "$(git status --porcelain 2>/dev/null)" ] && sha="${sha}-dirty"
export CIPHEROPS_GIT_SHA="$sha"
export CIPHEROPS_HOST_IMAGE_ID="$(docker image inspect "cipherops-bench:${stack}" --format '{{.Id}}')"
export CIPHEROPS_CPUSET="${2:-2}"

if [ "${1:-}" = "preflight" ]; then
  inner="python3 -m cipherops.runner preflight --results results"
else
  inner="python3 -m cipherops.runner run --config ${1:-configs/scenarios.yaml}"
fi
"${compose[@]}" run --rm bench "$inner"
