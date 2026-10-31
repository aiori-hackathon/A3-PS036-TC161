#!/usr/bin/env bash
# Full PS-036 runs on an EC2 (Linux) host.
#
# Offline  = pinned cipherops-bench:latest, one CPU core, no network (~5 h)
# Simulated = draft06 NSD/Unbound lab; separate suites for lan / wan / lossy
#
# Usage (from the repo root; use nohup/tmux so SSH drops do not kill the job):
#   chmod +x scripts/ec2_full_runs.sh
#   ./scripts/ec2_full_runs.sh              # same as "all": offline, then sim lan→wan→lossy
#   ./scripts/ec2_full_runs.sh all
#   ./scripts/ec2_full_runs.sh offline
#   ./scripts/ec2_full_runs.sh simulated    # lan, then wan+lossy reusing lan zones
#   ./scripts/ec2_full_runs.sh simulated-lan
#   ./scripts/ec2_full_runs.sh simulated-wan <lan-suite-id>
#   ./scripts/ec2_full_runs.sh simulated-lossy <lan-suite-id>
#
# Run names (folder suffix / suite label):
#   Offline:    <UTC>-ec2-ps036-offline-full10
#   Sim lan:    <stamp>_ec2-ps036-sim-50-500-5000-lan
#   Sim wan:    <stamp>_ec2-ps036-sim-50-500-5000-wan
#   Sim lossy:  <stamp>_ec2-ps036-sim-50-500-5000-lossy
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── identity ──────────────────────────────────────────────────────────────
OFFLINE_NAME="ec2-ps036-offline-full10"
SIM_BASE="ec2-ps036-sim-50-500-5000"
SIM_SIZES="50,500,5000"
SIM_SCENARIOS="ecdsa,ed25519,rsa,slh-dsa,mtl"
SIM_TRIALS=5
SIM_SIGN_TRIALS=5
SIM_QUERIES=2000
SIM_STACK="draft06"
CPUSET="${CIPHEROPS_CPUSET:-2}"          # offline harness pin; host needs ≥3 cores
LOG_DIR="${ROOT}/results/ec2-logs"
mkdir -p "$LOG_DIR"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "missing command: $1" >&2; exit 1; }
}

# ── offline (PS-036 scenarios.yaml: 50/500/5000, 10 trials) ────────────────
run_offline() {
  need_cmd docker
  need_cmd git

  # Relative to the repo: the bench container mounts ../configs → /work/configs.
  # An absolute host path is invisible inside the container (that is what broke the first EC2 run).
  local cfg_rel="configs/${OFFLINE_NAME}.yaml"
  local cfg_abs="${ROOT}/${cfg_rel}"
  sed "s/^run_name:.*/run_name: ${OFFLINE_NAME}/" \
    "${ROOT}/configs/scenarios.yaml" >"$cfg_abs"
  [ -f "$cfg_abs" ] || { echo "failed to write ${cfg_rel}" >&2; exit 1; }
  log "Wrote ${cfg_rel} (run_name=${OFFLINE_NAME})"

  log "OFFLINE preflight (stack=latest)…"
  CIPHEROPS_STACK=latest bash scripts/run_benchmark.sh preflight "$CPUSET" \
    2>&1 | tee "${LOG_DIR}/offline-preflight.log"

  # Image was just built (or already present); do not rebuild before the long run.
  log "OFFLINE full run → results/runs/*-${OFFLINE_NAME}/  (expect ~5 h)"
  CIPHEROPS_SKIP_BUILD=1 CIPHEROPS_STACK=latest \
    bash scripts/run_benchmark.sh "$cfg_rel" "$CPUSET" \
    2>&1 | tee "${LOG_DIR}/offline-full.log"

  local latest
  latest="$(ls -1dt results/runs/*-"${OFFLINE_NAME}" 2>/dev/null | head -1 || true)"
  if [ -n "$latest" ]; then
    log "OFFLINE done: ${latest}"
    log "  report: ${latest}/report.html"
  else
    log "OFFLINE finished but no results/runs/*-${OFFLINE_NAME} found — check ${LOG_DIR}/offline-full.log"
    exit 1
  fi
}

# ── simulated lab helpers ──────────────────────────────────────────────────
ensure_sim_images() {
  need_cmd docker
  need_cmd python3
  log "Building simulated lab images (stack=${SIM_STACK}) if needed…"
  python3 scripts/images.py build --stack "$SIM_STACK" --mode simulated \
    2>&1 | tee "${LOG_DIR}/sim-images-build.log"
}

suite_cmd() {
  # PYTHONPATH so `live_lab` imports from dashboard/
  PYTHONPATH="${ROOT}/dashboard${PYTHONPATH:+:$PYTHONPATH}" \
    python3 "${ROOT}/dashboard/live_lab/suite.py" "$@"
}

latest_suite_by_label() {
  local label="$1"
  ls -1dt ecosystem/results/suites/*_"${label}" 2>/dev/null | head -1 || true
}

run_sim_lan() {
  ensure_sim_images
  local name="${SIM_BASE}-lan"
  log "SIMULATED LAN start → suite label ${name}"
  suite_cmd \
    --sizes "$SIM_SIZES" \
    --scenarios "$SIM_SCENARIOS" \
    --trials "$SIM_TRIALS" \
    --sign-trials "$SIM_SIGN_TRIALS" \
    --queries "$SIM_QUERIES" \
    --networks lan \
    --udp 1232 \
    --jobs 1 \
    --stack "$SIM_STACK" \
    --name "$name" \
    --yes \
    2>&1 | tee "${LOG_DIR}/sim-lan.log"

  local dir
  dir="$(latest_suite_by_label "$name")"
  [ -n "$dir" ] && [ -f "${dir}/suite.json" ] || {
    log "SIMULATED LAN failed — no finished suite for ${name}"
    exit 1
  }
  log "SIMULATED LAN done: $(basename "$dir")"
  echo "$(basename "$dir")" >"${LOG_DIR}/sim-lan-suite-id.txt"
  log "  reuse id saved in ${LOG_DIR}/sim-lan-suite-id.txt"
}

run_sim_net() {
  # $1 = wan|lossy
  local net="$1"
  local reuse="${2:-}"
  if [ -z "$reuse" ] && [ -f "${LOG_DIR}/sim-lan-suite-id.txt" ]; then
    reuse="$(tr -d '[:space:]' <"${LOG_DIR}/sim-lan-suite-id.txt")"
  fi
  [ -n "$reuse" ] || {
    echo "usage: $0 simulated-${net} <lan-suite-id>" >&2
    echo "  or run simulated-lan first (writes ${LOG_DIR}/sim-lan-suite-id.txt)" >&2
    exit 1
  }
  [ -f "ecosystem/results/suites/${reuse}/suite.json" ] || {
    echo "no finished suite: ecosystem/results/suites/${reuse}" >&2
    exit 1
  }

  ensure_sim_images
  local name="${SIM_BASE}-${net}"
  log "SIMULATED ${net} start → reuse ${reuse}, label ${name}"
  suite_cmd \
    --sizes "$SIM_SIZES" \
    --scenarios "$SIM_SCENARIOS" \
    --trials "$SIM_TRIALS" \
    --sign-trials "$SIM_SIGN_TRIALS" \
    --queries "$SIM_QUERIES" \
    --networks "$net" \
    --udp 1232 \
    --jobs 1 \
    --stack "$SIM_STACK" \
    --name "$name" \
    --reuse "$reuse" \
    --yes \
    2>&1 | tee "${LOG_DIR}/sim-${net}.log"

  local dir
  dir="$(latest_suite_by_label "$name")"
  [ -n "$dir" ] && [ -f "${dir}/suite.json" ] || {
    log "SIMULATED ${net} failed — no finished suite for ${name}"
    exit 1
  }
  log "SIMULATED ${net} done: $(basename "$dir")"
}

run_simulated_all() {
  run_sim_lan
  local lan_id
  lan_id="$(tr -d '[:space:]' <"${LOG_DIR}/sim-lan-suite-id.txt")"
  run_sim_net wan "$lan_id"
  run_sim_net lossy "$lan_id"
  log "All simulated suites finished (lan / wan / lossy)."
}

run_all() {
  log "=== FULL PIPELINE: offline → simulated lan → wan → lossy ==="
  run_offline
  run_simulated_all
  log "=== ALL DONE ==="
  log "Offline:   results/runs/*-${OFFLINE_NAME}/"
  log "Sim lan:   $(cat "${LOG_DIR}/sim-lan-suite-id.txt" 2>/dev/null || echo '?')"
  log "Sim wan:   $(basename "$(latest_suite_by_label "${SIM_BASE}-wan")" 2>/dev/null || echo '?')"
  log "Sim lossy: $(basename "$(latest_suite_by_label "${SIM_BASE}-lossy")" 2>/dev/null || echo '?')"
  log "Logs:      ${LOG_DIR}/"
}

# ── entry ─────────────────────────────────────────────────────────────────
cmd="${1:-all}"
case "$cmd" in
  offline)           run_offline ;;
  simulated)         run_simulated_all ;;
  simulated-lan)     run_sim_lan ;;
  simulated-wan)     run_sim_net wan "${2:-}" ;;
  simulated-lossy)   run_sim_net lossy "${2:-}" ;;
  all)               run_all ;;
  -h|--help|help)
    cat <<EOF
Usage: $0 [all|offline|simulated|simulated-lan|simulated-wan|simulated-lossy] [lan-suite-id]

Default is "all": offline full run, then simulated lan → wan → lossy (no prompts).

EC2 one-shot (survives SSH disconnect):
  nohup ./scripts/ec2_full_runs.sh all > results/ec2-logs/pipeline.out 2>&1 &
EOF
    ;;
  *)
    echo "unknown command: $cmd (try --help)" >&2
    exit 1
    ;;
esac
