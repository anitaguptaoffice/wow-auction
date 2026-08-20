#!/usr/bin/env bash

set -euo pipefail

database_path="${1:-data/wow-auction.db}"
listfile_url="${WOW_LISTFILE_URL:-https://github.com/wowdev/wow-listfile/releases/latest/download/community-listfile.csv}"
listfile_path="$(mktemp)"
trap 'rm -f "$listfile_path"' EXIT

echo "Downloading latest WoW community listfile..."
curl --fail --location --silent --show-error --retry 3 --output "$listfile_path" "$listfile_url"

bash scripts/generate-icon-manifest.sh \
  "$database_path" \
  "$listfile_path" \
  frontend/src/generated/icon-map.json \
  backend/app/icon-map.json

python3 scripts/sync-icon-cache.py
