# Connected auction markets

The source `realm_id` is immutable provenance, not the auction-house identity.
`auction_market_id` is a project-owned retail/region/connection identity, **not** a
claimed Blizzard connected-realm API ID. `marketKey` still identifies an item variant.
The numeric `realmID` exposed for a connection is a representative member for
backward-compatible filters; catalog aliases include every member's source ID/name.

## Evidence and coverage

- [Blizzard Connected Realms explanation](https://worldofwarcraft.blizzard.com/en-us/news/10551009)
  explicitly describes one shared auction house for connected realms.
- [Authenticated China WoW account, 2024-08-04 11:56 CST](https://www.sina.cn/news/detail/5063616833782955.html)
  names Tosedrin including Phoenix, and Frostmourne including Forgotten Coast,
  Sacred Song and Echo Ridge in its capacity expansion announcement. This is
  official grouping evidence; applying connected-auction semantics to these groups
  is an inference, not a fresh authenticated auction API response.
- The initial official-announcement entries cover 托塞德林 (1515) / 凤凰之神 (1794), and
  霜之哀伤 (877) / 遗忘海岸 (887) / 神圣之歌 (941) / 回音山 (718).
  Numeric name mappings were cross-checked against
  [LibRealmInfo's realm dictionary](https://github.com/phanx-wow/LibRealmInfo/blob/master/LibRealmInfo.lua).
  The original upstream's Chinese connection data is incomplete.
- The production baseline now also includes the explicit CN connection table from
  [janekjl/LibRealmInfo, pinned revision](https://github.com/janekjl/LibRealmInfo/blob/4f605baae89cacf841d854aab0bed70eb1e13bd8/LibRealmInfo.lua).
  Its Lua file was last updated on 2020-11-18. The table covers 360 distinct realms
  in 137 groups: 105 connected groups and 32 singletons. All 105 connected groups
  are bundled, including 风暴之眼 (953) / 雷霆号角 (818) and
  燃烧之刃 (829) / 埃霍恩 (866) / 格瑞姆巴托 (1222). Singletons are recorded in
  `community_baseline.independent_realms` and keep the ordinary fallback identity.
  Community provenance is exposed with each group; this is a historical baseline,
  not a claim that the current CN topology was authenticated against Blizzard.
  Unknown realms remain independent. Same prices, cross-realm parties, guild
  membership and shared open-world zones are not connection evidence.

Existing official-announcement identities and their `valid_from` dates are preserved.
New community entries use the source file's last update time as their conservative
`valid_from`, not a claimed original connection date. Older observations are not
retroactively merged. The
source was re-read on 2026-10-30; this is not a claim of a live client verification
on that date. Evidence URL/type/publication time and the registry version are
included in `backend/compact_market/auction-markets.json` and the public catalog.

[Blizzard's Game Data API](https://community.developer.battle.net/documentation/world-of-warcraft/game-data-apis)
documents `/data/wow/connected-realm/index`, `/data/wow/connected-realm/{connectedRealmId}`
and auctions scoped to the same connected realm. The CN gateway's unauthenticated
probe returned 404 during research; no live authenticated CN roster was obtained.
Future validated updates should replace this historical baseline through explicit
versioned migrations, not silently change running generations.

The addon captures `C_AutoComplete.GetAutoCompleteRealms()` (with the older global
fallback) after scans and with `/as realms`. The generated Blizzard API declaration
is available in the [UI source mirror](https://github.com/Gethe/wow-ui-source/blob/live/Interface/AddOns/Blizzard_APIDocumentationGenerated/AutoCompleteDocumentation.lua).
`AuctionSearchDB.realmEvidence` stores capture time, source realm/region, project,
client build and returned names. These local reports require review and are not
automatically applied to server identities or forwarded in the v1 JSONL protocol.
An empty response is unavailable evidence, not proof that a realm is independent.

## Query and retention semantics

Only the latest **complete** snapshot of a connected market supplies current
non-commodity listings, comparison totals and all-market search. Never sum two
members' complete auction listings. Older observations remain one market timeline,
with source realm IDs/names preserved on each point. Same-time captures remain
distinct observations, with source scan ID as a deterministic tie-breaker.

Regional commodities remain a separate region-wide market and are not duplicated
per connection. Retention counts source observations, using the same resolved
market identity and capture time as queries to enforce the daily minimum.

Intervals are half-open `[valid_from, valid_to)`. Overlapping membership, missing
evidence and duplicate market identities are rejected. A later split receives a
new independent epoch so history is not joined across the connection. Historical
markets remain explicitly selectable but do not contribute to current totals.
This registry supports retail only; Classic needs its own identities and faction
rules, not reuse of these realm IDs.

## Frozen migration, no deletion

New builds embed the bundled registry in DuckDB. Append/retention generations
inherit the exact embedded registry, even if code defaults have changed. Legacy
databases without a registry remain independent until explicitly migrated.

```sh
python -m compact_market.manage stage \
  --database /path/to/current/market.duckdb \
  --output /path/to/new-generation/market.duckdb \
  --market-registry backend/compact_market/auction-markets.json
```

This creates a new candidate without removing snapshots or modifying the source.
Its report includes source identities, resolved markets and registry hash/version.
Prepare icons and run ordinary candidate/API acceptance before activating with the
existing dataset publisher. API, worker/import image and host `dataset.py` must all
be upgraded together so publication verifies the canonical catalog. Retention stays
opt-in; migrating identities does not install or enable its timer.

For future topology changes, retain earlier interval entries, close their validity
intervals, add a new stable market identity with evidence, and stage a new generation.
Never edit a running database or silently replace its embedded registry.
