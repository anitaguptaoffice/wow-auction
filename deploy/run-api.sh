#!/usr/bin/env bash
# Run on the deployment host; the caller switches Nginx after health verification.
set -euo pipefail
image=${1:?Usage: run-api.sh IMAGE DATABASE [CONTAINER] [PORT]}
database=$(realpath "${2:?Database path required}")
container=${3:-wow-auction-api}
port=${4:-8180}
test -f "$database"
docker run -d --name "$container" --restart unless-stopped \
  --memory 2g --memory-swap 2g --cpus 2 --pids-limit 128 \
  --read-only --tmpfs /tmp:rw,nosuid,size=128m \
  --cap-drop ALL --security-opt no-new-privileges \
  --log-opt max-size=10m --log-opt max-file=3 \
  -p "127.0.0.1:${port}:8000" -v "$database:/data/market.duckdb:ro" "$image"
for attempt in $(seq 1 90); do
  if curl --fail --silent "http://127.0.0.1:${port}/health"; then
    exit 0
  fi
  sleep 2
done
docker logs --tail 40 "$container"
exit 1
