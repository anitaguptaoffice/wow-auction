# Unified compact market experiment

The production CloudBase application is unchanged. This experiment reads three real,
manifest-verified COS archives: two Silver Hand snapshots and one Deathwing snapshot.
The original 1,373,832 modeled listing rows round-trip exactly after dictionary encoding.
The offline repository also has a separate 418,978-row backup in its handoff release.

## Market identity

`(region_id, market_server_id, market_key)` identifies a price series.
`market_server_id=0` represents region commodities and is displayed as 全服.
Other market IDs use the realm identity captured in the source. Unknown market scopes
remain marked unknown and are not promoted to commodities. Connected-realm mappings
are not guessed.

Each complete source scan produces separate commodity and realm views. Commodity
observations from different source realms enter the same timeline, with timestamps
and source provenance intact. Inventories are never added across observations.
Latest means the newest complete observation for that market; items absent from that
observation are not filled from older snapshots. Equal timestamps are retained and
ordered by source scan ID; no claim is made that they are duplicate observations.

Item names, links, and static attributes are dictionary encoded. New item IDs and
new combinations of modeled attributes are discovered during import, without a
hardcoded catalog. Existing versions remain available. Changes to the source schema
or link grammar still require parser changes and validation.

## Storage and query paths

- DuckDB contains all original modeled facts, shared descriptors, and summaries.
- Arrow columns hold all imported summary history in memory, with string dictionary
  encoding. There is no Python dictionary or pre-rendered API object per stored point.
- Compact integer posting lists and precomputed sort arrays serve substring search,
  exact item ID search, and pagination.
- History reads the shared in-memory columns using a market/time index.
- Detail queries select and paginate numeric facts before joining descriptive fields.
- Only returned rows are converted to JSON objects.

This version uses column encoding, not a custom delta/RLE history block codec.
All imported summary history is resident; bounded cold-history eviction and monthly
partitioning are not implemented. The full listing archive is retained on disk.
Generation construction is explicit and immutable; automatic ingestion scheduling,
atomic live generation switching, and authentication migration are not implemented.
The original benchmark preview uses `/api/market/listings` and a virtual market scan ID, and splits equipment context
and crafting-quality groups explicitly. The React frontend now uses these routes,
plus `/api/market/history/view` for its chart contract. Default lists combine the
latest observation per market; collection filters and realm-isolated cache keys
are supported. Deployment instructions are in the root README.

## Run

From the repository root:

```sh
uv sync --group compact --frozen
uv run --group compact python -m compact_market.build \
  data/archive/compact-source/silvermoon-old/auction.lua.tgz \
  data/archive/compact-source/silvermoon-new/auction.lua.tgz \
  data/archive/compact-source/deathwing-new/auction.lua.tgz \
  --output data/compact/market.duckdb --baseline data/compact/baseline.sqlite
COMPACT_ENGINE=unified COMPACT_MEMORY_LIMIT=256MB \
  uv run --group compact python -m uvicorn compact_market.api:app \
  --host 127.0.0.1 --port 8170 --no-access-log
```

The builder refuses to overwrite an existing generation. Input archives and generated
files are ignored by Git. Place the matching `manifest.json` beside each archive.
The archive SHA-256 and extracted Lua SHA-256 are both verified. Failed builds are
left under a `.building-PID` name rather than published as a valid generation.

```sh
uv run --group compact python -m unittest discover -s backend/tests -v
COMPACT_ENGINE=unified COMPACT_MEMORY_LIMIT=256MB \
  uv run --group compact python -m compact_market.benchmark \
  --output data/compact/unified-final.json --concurrency 16 --requests 16000
```

## Measured results (2026-10-29)

| Metric | Initial prototype | Unified column prototype |
|---|---:|---:|
| Startup API RSS | 523.8 MiB | 418.6 MiB |
| Peak API RSS during load | 1192.2 MiB | 462.2 MiB |
| Search HTTP P95 | 28.7 ms | 19.8 ms |
| List HTTP P95 | 27.5 ms | 19.8 ms |
| History HTTP P95 | 50.1 ms | 18.7 ms |
| Detail HTTP P95 | 89.2 ms | 40.8 ms |
| Mixed throughput | 504 req/s | 986 req/s |
| Request errors | 0 / 16000 | 0 / 16000 |

The database including dictionaries, summaries, and indexes is 65.8 MiB. The comparison
SQLite row-store copy is 289.3 MiB; this is not an estimate of production MySQL size.
Build peak RSS was 2.52 GiB with the existing full-Lua parser.

The host is an AMD Ryzen 9 9950X with about 60 GiB RAM. Each benchmark API process was
restricted to four logical CPUs; there was no 8 GiB cgroup limit. DuckDB's buffer
budget changed from 1 GB to 256 MB and is not a whole-process memory cap. Query plans,
market grouping, and sampled markets differ between versions, so the comparison
measures the complete implementation rather than isolating one optimization.
Requests run over loopback, include JSON serialization, and exclude Internet latency.
OS page cache was not flushed. The test is a short load run, not a long-duration soak.
There are only three historical scan observations, not months of hourly samples.
Neither live import/query overlap nor a production failover has been benchmarked.

34 backend tests passed, including new item discovery, lossless round trips, commodity
history across source realms, realm isolation, pet variants, and substring search.
Browser checks covered market selection, commodity search, listing detail and history,
with no JavaScript errors. Machine-readable evidence is in `results/`.
