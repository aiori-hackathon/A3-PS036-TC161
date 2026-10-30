#!/usr/bin/env bash
# Publish the images of one stack to a registry, so a node can "Pull from registry" in the dashboard's Setup instead of
# building them. Run it on a machine that has the images (built or pulled) and is logged in to the registry:
#   docker login ghcr.io -u <user>          # a token with write:packages
#   scripts/publish_images.sh latest                       # offline + lab images of the `latest` stack
#   scripts/publish_images.sh draft06 simulated            # the lab images only (the offline harness needs `latest`)
#   CIPHEROPS_REGISTRY=ghcr.io/me/benchmark scripts/publish_images.sh latest
# Only images that match the stack profile (python scripts/images.py status) are pushed. A new package starts private
# on GitHub: its owner makes it public once (Packages -> package settings -> Change visibility).
set -euo pipefail
cd "$(dirname "$0")/.."

stack="${1:?usage: scripts/publish_images.sh <stack> [offline|simulated|all]}"
mode="${2:-all}"
registry="${CIPHEROPS_REGISTRY:-ghcr.io/anithapriyanatarajan/benchmark}"
registry="${registry,,}"   # registry names must be lower case

python3 scripts/images.py status --stack "$stack" --mode "$mode" \
  || { echo "Some images are missing or do not match the '$stack' profile; build or pull them first." >&2; exit 1; }

case "$mode" in
  offline)   repos=(cipherops-bench) ;;
  simulated) repos=(cipherops-eco-nsd cipherops-eco-unbound cipherops-eco-tools) ;;
  all)       repos=(cipherops-bench cipherops-eco-nsd cipherops-eco-unbound cipherops-eco-tools) ;;
  *) echo "unknown mode '$mode'" >&2; exit 1 ;;
esac

for repo in "${repos[@]}"; do
  docker tag "$repo:$stack" "$registry/$repo:$stack"
  docker push "$registry/$repo:$stack"
done
echo "Pushed to $registry. Check that anonymous pulls work: docker logout ${registry%%/*}; python3 scripts/images.py pull --stack $stack --mode $mode --registry $registry"
