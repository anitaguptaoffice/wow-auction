#!/usr/bin/env bash

set -euo pipefail

database_path="${1:-data/wow-auction.db}"
listfile_path="${2:-}"
output_path="${3:-frontend/src/generated/icon-map.json}"
backend_output_path="${4:-backend/app/icon-map.json}"

if [[ -z "$listfile_path" ]]; then
  echo "Usage: $0 [database-path] <community-listfile.csv> [output-path]" >&2
  exit 1
fi

if [[ ! -f "$database_path" ]]; then
  echo "Database not found: $database_path" >&2
  exit 1
fi

if [[ ! -f "$listfile_path" ]]; then
  echo "Listfile not found: $listfile_path" >&2
  exit 1
fi

texture_ids_path="$(mktemp)"
trap 'rm -f "$texture_ids_path"' EXIT

sqlite3 "$database_path" \
  "SELECT DISTINCT texture FROM wow_auction_item_summaries WHERE texture IS NOT NULL ORDER BY texture;" \
  > "$texture_ids_path"

mkdir -p "$(dirname "$output_path")"

awk -F ';' '
  NR == FNR {
    requested[$1] = 1
    requested_order[++requested_count] = $1
    next
  }

  {
    sub(/\r$/, "", $2)
  }

  $1 in requested && $2 ~ /^(interface|housing)\/icons\/[^\/]+\.blp$/ {
    icon = $2
    sub(/^.*\//, "", icon)
    sub(/\.blp$/, "", icon)
    # Blizzard listfile paths can contain literal spaces while icon CDNs expose
    # the same slug with one hyphen per space (including trailing spaces).
    gsub(/[[:space:]]/, "-", icon)
    resolved[$1] = icon
  }

  END {
    print "{"
    separator = ""
    for (position = 1; position <= requested_count; position++) {
      id = requested_order[position]
      if (!(id in resolved)) {
        # Keep every FileDataID in the pipeline. A synthetic name bypasses
        # filename-based CDNs and lets the cache step export it from CASC.
        resolved[id] = "filedata-" id
      }
      printf "%s  \"%s\": \"%s\"", separator, id, resolved[id]
      separator = ",\n"
    }
    print "\n}"
  }
' "$texture_ids_path" "$listfile_path" > "$output_path"

mkdir -p "$(dirname "$backend_output_path")"
cp "$output_path" "$backend_output_path"

resolved_count="$(grep -c '": "' "$output_path" || true)"
requested_count="$(wc -l < "$texture_ids_path" | tr -d ' ')"
synthetic_count="$(grep -c '": "filedata-' "$output_path" || true)"
echo "Generated $output_path ($resolved_count/$requested_count textures covered; $synthetic_count use FileDataID fallback)"
echo "Mirrored icon allowlist to $backend_output_path"
