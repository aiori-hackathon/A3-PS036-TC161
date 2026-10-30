#!/usr/bin/env bash
# Provision an Amazon Linux 2023 (x86_64) node for CipherOps. Run as root from inside a checkout of the repository;
# deploy/aws/cipherops-node.yaml clones it to /opt/cipherops and calls this script from the instance's user data.
#
#   REGISTRY       registry namespace the images are pulled from   (default ghcr.io/anithapriyanatarajan/benchmark)
#   COMPOSE_VERSION  docker compose release to install             (default v5.6.1)
#
# It installs Docker, Compose and Python, pulls the pinned images (instead of compiling them for tens of minutes), and
# runs the dashboard as a service bound to 127.0.0.1:8501. Nothing is exposed to the network: reach it through an SSM
# port-forward or an SSH tunnel. Exits non-zero if anything fails, which fails the CloudFormation stack.
set -euo pipefail

REGISTRY="${REGISTRY:-ghcr.io/anithapriyanatarajan/benchmark}"
COMPOSE_VERSION="${COMPOSE_VERSION:-v5.6.1}"
RUN_USER="${RUN_USER:-ec2-user}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

log() { echo "[$(date -u +%FT%TZ)] $*"; }

log "node: $(nproc) vCPU, $(awk '/MemTotal/ {printf "%.0f", $2/1048576}' /proc/meminfo) GiB RAM, $(uname -r)"
if [ "$(nproc)" -lt 8 ]; then
  log "WARNING: the simulated lab pins containers to cores 2-5 and wants 8 vCPU; the offline harness needs at least 3."
fi

log "packages"
dnf -y install docker git python3.12 python3.12-pip
systemctl enable --now docker
usermod -aG docker "$RUN_USER"

log "docker compose $COMPOSE_VERSION"
install -d /usr/local/lib/docker/cli-plugins
curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
  "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64"
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version

log "python environment in $REPO_DIR/.venv"
chown -R "$RUN_USER": "$REPO_DIR"
runuser -u "$RUN_USER" -- python3.12 -m venv "$REPO_DIR/.venv"
runuser -u "$RUN_USER" -- "$REPO_DIR/.venv/bin/pip" install --quiet --upgrade pip
runuser -u "$RUN_USER" -- "$REPO_DIR/.venv/bin/pip" install --quiet -r "$REPO_DIR/requirements-dashboard.txt"
runuser -u "$RUN_USER" -- install -d "$REPO_DIR/results"

# The offline harness image exists for the latest stack only; the lab images exist for both stacks.
log "pulling images from $REGISTRY"
cd "$REPO_DIR"
.venv/bin/python scripts/images.py pull --stack latest --mode all --registry "$REGISTRY"
.venv/bin/python scripts/images.py pull --stack draft06 --mode simulated --registry "$REGISTRY"

log "dashboard service"
cat > /etc/systemd/system/cipherops-dashboard.service <<EOF
[Unit]
Description=CipherOps dashboard
After=docker.service network-online.target
Requires=docker.service

[Service]
User=$RUN_USER
SupplementaryGroups=docker
WorkingDirectory=$REPO_DIR
Environment=CIPHEROPS_REGISTRY=$REGISTRY
Environment=STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ExecStart=$REPO_DIR/.venv/bin/python -m streamlit run dashboard/live_lab/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now cipherops-dashboard

for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8501/_stcore/health >/dev/null 2>&1; then
    log "dashboard is up on 127.0.0.1:8501"
    exit 0
  fi
  sleep 2
done
log "dashboard did not answer on 127.0.0.1:8501"
journalctl -u cipherops-dashboard --no-pager -n 40 || true
exit 1
