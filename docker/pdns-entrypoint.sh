#!/bin/bash
# PowerDNS Auth for CipherOps: serve ldns pre-signed zones via BIND backend.
# Without bind-dnssec-db + PRESIGNED, Auth silently strips RRSIGs from zone files.
set -euo pipefail

CONFIG_DIR="${PDNS_CONFIG_DIR:?PDNS_CONFIG_DIR required}"
DB="${PDNS_DNSSEC_DB:-/tmp/pdns-bind-dnssec.sqlite3}"

mkdir -p "$(dirname "$DB")"
if [[ ! -f "$DB" ]]; then
  pdnsutil create-bind-db "$DB"
fi

# pdnsutil reads bind-dnssec-db from pdns.conf in --config-dir.
named="${CONFIG_DIR}/named.conf"
if [[ -f "$named" ]]; then
  # shellcheck disable=SC2013
  for z in $(sed -n 's/^[[:space:]]*zone[[:space:]]*"\([^"]*\)".*/\1/p' "$named"); do
    # Root is "." — pdnsutil accepts it. Ignore if already marked.
    pdnsutil --config-dir="$CONFIG_DIR" set-presigned "$z" 2>/dev/null \
      || pdnsutil --config-dir="$CONFIG_DIR" zone set-presigned "$z" 2>/dev/null \
      || true
    # Match ecolab.sign nsec3_iterations=0 empty-salt NSEC3PARAM (1 0 0 -).
    pdnsutil --config-dir="$CONFIG_DIR" set-nsec3 "$z" '1 0 0 -' 2>/dev/null \
      || pdnsutil --config-dir="$CONFIG_DIR" zone set-nsec3 "$z" '1 0 0 -' 2>/dev/null \
      || true
  done
fi

exec pdns_server --config-dir="$CONFIG_DIR" --daemon=no
