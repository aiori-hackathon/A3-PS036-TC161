#!/usr/bin/env bash
# Share a read-mostly view of this CipherOps node with evaluators, without touching the lab or the team's dashboard.
#
#   sudo bash deploy/aws/minimal.sh start [ACCESS_CODE]   minimal dashboard on 127.0.0.1:8502 + a public HTTPS link
#   sudo bash deploy/aws/minimal.sh url                   print the current link
#   sudo bash deploy/aws/minimal.sh status                both services and the link
#   sudo bash deploy/aws/minimal.sh stop                  take the link and the minimal dashboard down
#
# The minimal dashboard runs with CIPHEROPS_MINIMAL=1 (see dashboard/live_lab/minimal.py): visitors can explore the
# saved results, Compare and Learn, and send DNS lookups to the running lab through a restricted dig terminal; every
# action that would change the lab is refused in code. With an ACCESS_CODE, visitors must type it first.
#
# The link comes from a Cloudflare quick tunnel: an outbound connection from this node, so no inbound port, security
# group rule or domain is needed. It is a temporary *.trycloudflare.com address that changes whenever the tunnel
# restarts; run `url` again to get the current one. The team's dashboard on 127.0.0.1:8501 is not changed or exposed.
set -euo pipefail

RUN_USER="${RUN_USER:-ec2-user}"
PORT="${PORT:-8502}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE=/etc/cipherops/minimal.env
APP_UNIT=cipherops-minimal
TUNNEL_UNIT=cipherops-minimal-tunnel

log() { echo "[$(date -u +%FT%TZ)] $*"; }
need_root() { [ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }; }

link() {
  journalctl -u "$TUNNEL_UNIT" --no-pager -o cat 2>/dev/null \
    | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -n 1
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
  local code="${1:-}"
  [ -x "$REPO_DIR/.venv/bin/python" ] || { echo "no $REPO_DIR/.venv: run deploy/aws/bootstrap.sh first" >&2; exit 1; }

  if ! command -v cloudflared >/dev/null 2>&1; then
    log "installing cloudflared"
    dnf -y install "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-x86_64.rpm"
  fi
  cloudflared --version

  install -d -m 700 /etc/cipherops
  umask 077
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
Restart=on-failure
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full

[Install]
WantedBy=multi-user.target
EOF

  cat > "/etc/systemd/system/$TUNNEL_UNIT.service" <<EOF
[Unit]
Description=CipherOps minimal dashboard: public HTTPS link (Cloudflare quick tunnel)
After=$APP_UNIT.service network-online.target
Requires=$APP_UNIT.service

[Service]
DynamicUser=yes
ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$PORT
Restart=on-failure
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
  if url="$(wait_link)"; then
    log "share this link with the evaluators: $url"
  else
    journalctl -u "$TUNNEL_UNIT" --no-pager -n 30
    echo "the tunnel did not report a link yet; try: sudo bash $0 url" >&2
    exit 1
  fi
}

stop() {
  need_root
  systemctl disable --now "$TUNNEL_UNIT" "$APP_UNIT" 2>/dev/null || true
  rm -f "/etc/systemd/system/$TUNNEL_UNIT.service" "/etc/systemd/system/$APP_UNIT.service"
  systemctl daemon-reload
  log "the public link and the minimal dashboard are down; the team's dashboard and the lab are unchanged"
}

status() {
  systemctl --no-pager --lines 0 status "$APP_UNIT" "$TUNNEL_UNIT" || true
  echo "link: $(link || echo none)"
}

case "${1:-}" in
  start) shift; start "${1:-}" ;;
  stop) stop ;;
  url) link || { echo "no link yet" >&2; exit 1; } ;;
  status) status ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
