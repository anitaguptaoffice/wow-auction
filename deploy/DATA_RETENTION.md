# Configurable snapshot retention

`wow-auction-retention.timer` submits a durable job to the existing sync worker.
The worker stages and verifies a new DuckDB file, then uses the existing blue/green
publication and rollback path. It never deletes rows from the live database.

## Policy

Copy `retention.example.json` to a private deployment configuration and set
`worker_config` to the upgraded worker's configuration file.

| Setting | Default | Meaning |
| --- | --- | --- |
| `policy.retention_days` | `14` | Rolling window in days; integer 1–14. Capture time, not upload time. Exactly at the cutoff is retained. |
| `policy.min_snapshots_per_day` | `1` | Protect the latest N observations per auction market and local date; integer 1–1000. This is a floor, not a target. |
| `policy.max_snapshots` | `200` | Global soft cap on raw source snapshots; integer 1–1000000. Remove oldest unprotected observations only when this cap is exceeded. Daily coverage wins if floors exceed the cap. |
| `policy.timezone` | `Asia/Shanghai` | IANA timezone for daily grouping. Expiration still compares Unix timestamps. |
| `policy.rollback_hours` | `24` | Grace period for managed job artifacts and recent rollback references; integer 1–336. |
| `on_calendar` | `hourly` | systemd `OnCalendar` expression, interpreted by the host. Example: `*-*-* 04:00:00`. |

First expire observations outside the window. If the remaining count is at or
below `max_snapshots`, retain every observation. Above the cap, protect daily
floors and remove only the oldest unprotected observations until the cap is met
or no removable observations remain. `cap_exceeded_by` reports the latter case.
For example, 30 snapshots of one market today are all kept; 205 such snapshots
retain the latest 200, not just one. If 220 market/date buckets each have one
snapshot, all 220 survive and the report records an overage of 20.

No observations are invented for dates without data. The latest snapshot of every
auction market/date inside the retention window is protected, including today.
Connected realms share one daily floor using the generation's frozen registry;
each captured observation still counts once toward the global cap. Independent
observations from different members are not deduplicated just because they share
a market. See [CONNECTED_REALMS.md](CONNECTED_REALMS.md) for evidence and migration.
Regional commodities are derived from the retained source snapshots; market 0 is
not an independent source realm. Expiration takes precedence over daily coverage:
a realm that has not supplied fresh data for 14 days disappears from the catalog.
If all snapshots expire, the API serves an empty catalog/list and `available: false`.
Deleted scan links return 404; IDs are never reused.

Cleanup is periodic: expired data may remain until the next successful timer run.
Busy uploads/publications cause the run to skip, with another attempt on the next
tick. No new database is built/published when the selection has not changed.

## Upgrade and installation

Upgrade the API image (empty-dataset support), sync ingress image, sync worker and
its `import_image` before enabling the timer. All use this release's source.
The host scripts remain compatible with Python 3.6 and systemd 219. Containers
provide Python 3.13, DuckDB and the IANA timezone database. Keep the previous
release/service configuration available for rollback.

```sh
# Read-only selection preview; no reservation, publication or policy change.
python3 deploy/retention_schedule.py --config /absolute/path/retention.local.json

# One explicit run; prints a durable job ID. The worker performs the operation.
python3 deploy/retention_schedule.py --config /absolute/path/retention.local.json --apply

# Enable the persistent timer (also runs after a missed tick during downtime).
python3 deploy/install_retention.py --config /absolute/path/retention.local.json
systemctl list-timers wow-auction-retention.timer
journalctl -u wow-auction-retention.service -u wow-auction-sync-worker.service
```

Each run reloads policy settings. Changes to `on_calendar` require rerunning the
installer. The last activated policy is stored at `<sync-root>/retention-policy.json`;
new uploads older than its rolling cutoff are rejected. Existing successful SHA
receipts remain idempotent even after their payload is reclaimed. Queued imports
check age again before staging. Manual snapshot management/rollback remains an
operator path and can temporarily restore old data until the next cleanup.

Pause cleanup with `systemctl disable --now wow-auction-retention.timer`.
This does not cancel an already queued job or disable expired-upload protection.
Check `<sync-root>/active.json` and the job's `phase`; do not remove a lease for an
uncertain publication. Recovery reuses the existing import container and publication
receipt just like ordinary sync. `publication_unknown` blocks further changes.

## Storage and audit

Each job retains `job.json`, its selection in `result.retention`, and an import log.
The selection includes cutoff, every observed market/date, protected and kept IDs,
expired IDs, capacity removals (`redundant_scan_ids`) and unresolved cap overage.
Candidate reports retain round-trip verification and database SHA.
Deletion compacts the candidate using `COPY FROM DATABASE`, because ordinary SQL
deletion does not guarantee a smaller file. Peak free disk must cover both staging
and compacted candidates in addition to the live database.

Artifact GC runs under the same retention reservation, including no-change runs.
After `rollback_hours`, it reclaims only terminal sync/retention jobs' `generation/`,
`snapshot.jsonl.gz` and `upload.part`, retaining job metadata and SHA receipts.
Live container mounts, unfinished jobs, incomplete switches and recent rollback
records protect their referenced files. Eligible stopped managed import/data
containers are removed without force before their artifacts are reclaimed. GC
errors are reported in `result.artifact_cleanup.error` and do not mislabel a
successful data publication as failed.

The 14-day limit applies to the active dataset. Rollback copies have the separate
grace period above. Legacy archives/releases and files outside `<sync-root>/jobs`
are intentionally not swept. Stopped non-managed containers can pin old files;
`protected_jobs` identifies skipped jobs. Reclamation does not imply deletion of
independent backups or client-side Lua archives.
