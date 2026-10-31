#!/usr/bin/env bash
# Share a read-mostly view of this CipherOps node with evaluators, without touching the lab or the team's dashboard.
#
#   sudo bash deploy/aws/minimal.sh start [ACCESS_CODE]
#       minimal dashboard on 127.0.0.1:8502 + a temporary public link (a Cloudflare quick tunnel)
#   TUNNEL_TOKEN=... sudo -E bash deploy/aws/minimal.sh start --domain eval.example.com [ACCESS_CODE]
#       the same, on a fixed address you own (a named Cloudflare tunnel; the token is asked for if TUNNEL_TOKEN is unset)
#   sudo bash deploy/aws/minimal.sh url      print the current link
#   sudo bash deploy/aws/minimal.sh status   both services and the link
#   sudo bash deploy/aws/minimal.sh stop     take the link and the minimal dashboard down
#
# The minimal dashboard runs with CIPHEROPS_MINIMAL=1 (see dashboard/live_lab/minimal.py): visitors can explore the
# saved results, Compare and Learn, and send DNS lookups to the running lab through a restricted dig terminal; every
# action that would change the lab is refused in code. With an ACCESS_CODE, visitors must type it first.
#
# Both kinds of link are an outbound Cloudflare tunnel from this node: no inbound port or security group rule. A quick
# tunnel is a temporary *.trycloudflare.com address that changes whenever the tunnel restarts (run `url` for the current
# one). A named tunnel keeps the address you chose. Both services start at boot and restart if they fail.
#
# `start` also asks Docker to bring the lab's containers back after a reboot (restart policy unless-stopped) and sets
# CIPHEROPS_LAB_RESTART for the team's dashboard, so a lab it starts later gets the same policy. The team's dashboard on
# 127.0.0.1:8501 is not restarted or exposed; `stop` undoes the restart policy.
set -euo pipefail

RUN_USER="${RUN_USER:-ec2-user}"
PORT="${PORT:-8502}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE=/etc/cipherops/minimal.env
TUNNEL_ENV=/etc/cipherops/minimal-tunnel.env   # TUNNEL_TOKEN of a named tunnel, root only
URL_FILE=/etc/cipherops/minimal.url            # the fixed address of a named tunnel
DASH_UNIT=cipherops-dashboard
DASH_DROPIN=/etc/systemd/system/cipherops-dashboard.service.d/lab-restart.conf
APP_UNIT=cipherops-minimal
TUNNEL_UNIT=cipherops-minimal-tunnel

log() { echo "[$(date -u +%FT%TZ)] $*"; }
need_root() { [ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }; }

link() {
  if [ -s "$URL_FILE" ]; then cat "$URL_FILE"; return 0; fi
  journalctl -u "$TUNNEL_UNIT" --no-pager -o cat 2>/dev/null \
    | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -n 1
}

# Lab containers come back after a reboot or a Docker restart. Containers that are running now are updated at once; the
# drop-in makes the team's dashboard do the same for a lab it starts later (it takes effect at that service's next start).
keep_lab_running() {
  local ids
  ids="$(docker ps -q --filter 'name=^eco-' 2>/dev/null || true)"
  if [ -n "$ids" ]; then
    # shellcheck disable=SC2086
    docker update --restart unless-stopped $ids >/dev/null && log "lab containers will restart after a reboot"
  else
    log "no lab is running now; start one from the team's dashboard (Simulated lab -> Prepare -> Start)"
  fi
  if systemctl cat "$DASH_UNIT" >/dev/null 2>&1; then
    install -d "$(dirname "$DASH_DROPIN")"
    printf '[Service]\nEnvironment=CIPHEROPS_LAB_RESTART=unless-stopped\n' > "$DASH_DROPIN"
    systemctl daemon-reload
  fi
}

release_lab() {
  local ids
  ids="$(docker ps -aq --filter 'name=^eco-' 2>/dev/null || true)"
  # shellcheck disable=SC2086
  [ -z "$ids" ] || docker update --restart no $ids >/dev/null || true
  rm -f "$DASH_DROPIN"; rmdir "$(dirname "$DASH_DROPIN")" 2>/dev/null || true
}

wait_link() {
  for _ in $(seq 1 45); do
    url="$(link || true)"
    if [ -n "$url" ]; then echo "$url"; return 0; fi
    sleep 2
  done
  return 1
}

start() {
  need_root
  local code="" host="" token=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --domain) host="${2:?--domain needs a host name, for example eval.example.com}"; shift 2 ;;
      -*) echo "unknown option: $1" >&2; exit 2 ;;
      *) code="$1"; shift ;;
    esac
  done
  if [ -n "$host" ]; then
    [[ "$host" =~ ^([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}$ ]] || { echo "not a host name: $host" >&2; exit 2; }
    token="${TUNNEL_TOKEN:-}"
    if [ -z "$token" ] && [ -t 0 ]; then read -rsp "Cloudflare tunnel token (input hidden): " token; echo; fi
    [[ "$token" =~ ^[A-Za-z0-9_=+/.-]{40,}$ ]] || { echo "missing or malformed tunnel token (set TUNNEL_TOKEN)" >&2; exit 2; }
  fi
  [ -x "$REPO_DIR/.venv/bin/python" ] || { echo "no $REPO_DIR/.venv: run deploy/aws/bootstrap.sh first" >&2; exit 1; }

  if ! command -v cloudflared >/dev/null 2>&1; then
    log "installing cloudflared"
    dnf -y install "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-x86_64.rpm"
  fi
  cloudflared --version

  install -d -m 700 /etc/cipherops
  umask 077
  if [ -n "$host" ]; then
    printf 'TUNNEL_TOKEN=%s\n' "$token" > "$TUNNEL_ENV"
    printf 'https://%s\n' "$host" > "$URL_FILE"
    log "named tunnel for https://$host (token stored in $TUNNEL_ENV, readable by root only)"
  else
    rm -f "$TUNNEL_ENV" "$URL_FILE"
  fi
  if [ -n "$code" ]; then
    printf 'CIPHEROPS_MINIMAL_PASSWORD=%s\n' "$code" > "$ENV_FILE"
    log "access code set (stored in $ENV_FILE, readable by root only)"
  elif [ ! -f "$ENV_FILE" ]; then
    : > "$ENV_FILE"
    log "no access code: anyone with the link can open the minimal dashboard"
  fi

  log "minimal dashboard service on 127.0.0.1:$PORT"
  cat > "/etc/systemd/system/$APP_UNIT.service" <<EOF
[Unit]
Description=CipherOps minimal dashboard (evaluators)
After=docker.service network-online.target
Requires=docker.service

[Service]
User=$RUN_USER
SupplementaryGroups=docker
WorkingDirectory=$REPO_DIR
Environment=CIPHEROPS_MINIMAL=1
Environment=STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
EnvironmentFile=-$ENV_FILE
ExecStart=$REPO_DIR/.venv/bin/python -m streamlit run dashboard/live_lab/app.py --server.address 127.0.0.1 --server.port $PORT --server.headless true --server.fileWatcherType none --server.maxUploadSize 1
Restart=always
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full

[Install]
WantedBy=multi-user.target
EOF

  local tunnel_exec="/usr/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$PORT" tunnel_env="" tunnel_kind="quick tunnel"
  if [ -n "$host" ]; then
    tunnel_exec="/usr/bin/cloudflared tunnel --no-autoupdate run"
    tunnel_env="EnvironmentFile=$TUNNEL_ENV"
    tunnel_kind="named tunnel"
  fi
  cat > "/etc/systemd/system/$TUNNEL_UNIT.service" <<EOF
[Unit]
Description=CipherOps minimal dashboard: public HTTPS link (Cloudflare $tunnel_kind)
After=$APP_UNIT.service network-online.target
Requires=$APP_UNIT.service

[Service]
DynamicUser=yes
$tunnel_env
ExecStart=$tunnel_exec
Restart=always
RestartSec=5
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "$APP_UNIT" "$TUNNEL_UNIT" >/dev/null
  systemctl restart "$APP_UNIT"
  for _ in $(seq 1 60); do
    curl -fsS "http://127.0.0.1:$PORT/_stcore/health" >/dev/null 2>&1 && break
    sleep 2
  done
  curl -fsS "http://127.0.0.1:$PORT/_stcore/health" >/dev/null || {
    journalctl -u "$APP_UNIT" --no-pager -n 40; echo "minimal dashboard did not start" >&2; exit 1; }
  log "minimal dashboard is up"

  systemctl restart "$TUNNEL_UNIT"
  keep_lab_running
  if [ -n "$host" ]; then
    wait_named "$host"
  elif url="$(wait_link)"; then
    log "share this link with the evaluators: $url (temporary: it changes if the tunnel restarts)"
  else
    journalctl -u "$TUNNEL_UNIT" --no-pager -n 30
    echo "the tunnel did not report a link yet; try: sudo bash $0 url" >&2
    exit 1
  fi
}

# Read the whole log into a variable before matching: `journalctl | grep -q` under `set -o pipefail` reports failure when
# grep stops at the first match and journalctl is killed by SIGPIPE, even though the line was there.
tunnel_connected() {
  local out
  out="$(journalctl -u "$TUNNEL_UNIT" --no-pager -o cat 2>/dev/null || true)"
  [[ "$out" == *"Registered tunnel connection"* ]]
}

# A named tunnel is up when cloudflared has registered a connection; the address answers once the public hostname in
# the Cloudflare dashboard points at http://localhost:$PORT and DNS has caught up (usually a minute or two).
wait_named() {
  local host="$1"
  for _ in $(seq 1 30); do
    tunnel_connected && break
    sleep 2
  done
  if ! tunnel_connected; then
    journalctl -u "$TUNNEL_UNIT" --no-pager -n 30
    echo "the tunnel did not connect: check the token, and that this node can reach Cloudflare (outbound 443/7844)" >&2
    exit 1
  fi
  log "tunnel connected"
  for _ in $(seq 1 30); do
    if curl -fsS "https://$host/_stcore/health" >/dev/null 2>&1; then
      log "share this fixed link with the evaluators: https://$host"
      return 0
    fi
    sleep 4
  done
  log "tunnel is connected but https://$host does not answer yet: in Cloudflare (Zero Trust > Networks > Tunnels > your tunnel >"
  log "Public Hostname) add $host with service HTTP, URL localhost:$PORT, then try: curl https://$host/_stcore/health"
}

stop() {
  need_root
  systemctl disable --now "$TUNNEL_UNIT" "$APP_UNIT" 2>/dev/null || true
  rm -f "/etc/systemd/system/$TUNNEL_UNIT.service" "/etc/systemd/system/$APP_UNIT.service" "$TUNNEL_ENV" "$URL_FILE"
  release_lab
  systemctl daemon-reload
  log "the public link and the minimal dashboard are down; the lab keeps running but no longer restarts after a reboot"
}

status() {
  systemctl --no-pager --lines 0 status "$APP_UNIT" "$TUNNEL_UNIT" || true
  echo "link: $(link || echo none)"
}

# Sourcing the file (the tests do) only defines the functions.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-}" in
    start) shift; start "$@" ;;
    stop) stop ;;
    url) link || { echo "no link yet" >&2; exit 1; } ;;
    status) status ;;
    *) sed -n '2,10p' "$0"; exit 2 ;;
  esac
fi
