"""Build a verified immutable DuckDB generation from Lua or single-file tgz inputs."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import resource
import tarfile
import tempfile
import time

import duckdb
import pyarrow as pa

from app.services.auction_importer import decode_snapshot, validate_snapshot, _listing_mapping, sha256_file
from app.services.market_queries import _parse_item_variant, _parse_battle_pet_variant, _pet_variant_key
from compact_market.schema import (
    DESCRIPTOR_FIELDS, PERSON_FIELDS, FACT_FIELDS, FACT_SCHEMA,
    DESCRIPTOR_SCHEMA, SCAN_SCHEMA, SCOPE_SCHEMA, RESTORE_SQL,
)


def log(phase: str, **fields) -> None:
    print(json.dumps({"phase": phase, **fields}, ensure_ascii=False), flush=True)


def update_digest(digest, mapping: dict) -> None:
    digest.update(json.dumps(mapping, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8"))
    digest.update(b"\n")


def insert_arrow(db, name: str, table: pa.Table, *, create: bool = False) -> None:
    db.register("incoming", table)
    try:
        if create:
            db.execute(f"CREATE TABLE {name} AS SELECT * FROM incoming")
        else:
            db.execute(f"INSERT INTO {name} SELECT * FROM incoming")
    finally:
        db.unregister("incoming")


def extract_source(source: Path, directory: Path) -> tuple[Path, dict]:
    manifest_path = source.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if source.suffix == ".lua":
        lua = source
    else:
        if manifest.get("archiveSha256") and sha256_file(source) != manifest["archiveSha256"]:
            raise ValueError(f"Archive SHA-256 mismatch: {source}")
        with tarfile.open(source, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) != 1 or members[0].name != "auction.lua" or not members[0].isfile():
                raise ValueError("Expected one regular auction.lua member")
            if members[0].size > 1024 * 1024 * 1024:
                raise ValueError("Lua source exceeds 1 GiB")
            lua = directory / "auction.lua"
            with archive.extractfile(members[0]) as src, lua.open("wb") as dst:
                while block := src.read(1024 * 1024):
                    dst.write(block)
    actual = sha256_file(lua)
    if manifest.get("snapshotSha256") and actual != manifest["snapshotSha256"]:
        raise ValueError(f"Snapshot SHA-256 mismatch: {source}")
    if manifest.get("sourceSize") and lua.stat().st_size != manifest["sourceSize"]:
        raise ValueError("Source size mismatch")
    return lua, manifest


def build(sources: list[Path], destination: Path, *, threads: int = 2,
          memory_limit: str = "1GB", baseline: Path | None = None) -> dict:
    """Never replace an existing generation; publish only after full round-trip checks."""
    if destination.exists():
        raise ValueError(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    # Keep failed generations separate from the published destination.
    staging = destination.with_name(destination.name + f".building-{os.getpid()}")
    db = duckdb.connect(str(staging), config={"threads": threads, "memory_limit": memory_limit})
    baseline_db = None
    if baseline:
        import sqlite3
        if baseline.exists():
            raise ValueError("Baseline already exists")
        baseline_db = sqlite3.connect(baseline)
        baseline_db.execute("""CREATE TABLE listings (
            scan_id INTEGER, source_index INTEGER, item_id INTEGER, name TEXT,
            texture INTEGER, quantity INTEGER, quality_id INTEGER, usable INTEGER,
            level INTEGER, level_type TEXT, min_bid INTEGER, min_increment INTEGER,
            buyout_amount INTEGER, unit_price INTEGER, bid_amount INTEGER,
            high_bidder INTEGER, bidder_full_name TEXT, owner TEXT, owner_full_name TEXT,
            sale_status INTEGER, has_all_info INTEGER, item_link TEXT,
            time_left_band INTEGER, battle_pet_creature_id INTEGER, battle_pet_display_id INTEGER)""")
    descriptors: list[dict] = []
    descriptor_ids: dict[tuple, int] = {}
    people: list[dict] = []
    person_ids: dict[str | None, int] = {}
    scan_rows: list[dict] = []
    scope_rows: list[dict] = []
    seen_fingerprints: set[str] = set()
    source_reports = []
    plain_string_bytes = 0
    insert_arrow(db, "listings", pa.Table.from_pylist([], schema=FACT_SCHEMA), create=True)
    try:
        for source in sources:
            source_started = time.perf_counter()
            with tempfile.TemporaryDirectory(prefix="compact-auction-") as tmp:
                lua, manifest = extract_source(source, Path(tmp))
                source_sha = sha256_file(lua)
                log("parse", source=str(source), source_bytes=lua.stat().st_size,
                    sha256=source_sha, manifest_verified=bool(manifest))
                decoded = decode_snapshot(lua)
                scans = validate_snapshot(decoded)
                log("validated", source=str(source), scans=len(scans),
                    listings=sum(s.record_count for s in scans),
                    seconds=round(time.perf_counter() - source_started, 3))
                for scan in scans:
                    if scan.fingerprint in seen_fingerprints:
                        raise ValueError("Duplicate scan fingerprint in input set")
                    seen_fingerprints.add(scan.fingerprint)
                    scan_id = len(scan_rows) + 1
                    digest = hashlib.sha256()
                    facts: list[dict] = []
                    baseline_batch = []
                    for index, item in enumerate(scan.items):
                        mapping = _listing_mapping(scan_id, index, item)
                        update_digest(digest, mapping)
                        plain_string_bytes += sum(len(v.encode("utf-8")) for v in mapping.values()
                                                  if isinstance(v, str))
                        key = tuple(mapping[k] for k in DESCRIPTOR_FIELDS)
                        descriptor_id = descriptor_ids.get(key)
                        if descriptor_id is None:
                            descriptor_id = len(descriptors) + 1
                            descriptor_ids[key] = descriptor_id
                            variant = _parse_item_variant(mapping["item_link"])
                            pet = _parse_battle_pet_variant(mapping["item_link"])
                            pet_key = _pet_variant_key(pet) if mapping["battle_pet_creature_id"] else ""
                            market_key = json.dumps([
                                mapping["item_id"], mapping["battle_pet_creature_id"] or 0,
                                variant["itemContext"], variant["craftingQuality"], pet_key,
                            ], separators=(",", ":"))
                            descriptors.append({
                                "descriptor_id": descriptor_id,
                                **dict(zip(DESCRIPTOR_FIELDS, key)),
                                "item_context": variant["itemContext"],
                                "crafting_quality": variant["craftingQuality"],
                                "pet_variant_key": pet_key, "market_key": market_key,
                            })
                        fact = {k: mapping[k] for k in FACT_FIELDS if k in mapping}
                        fact["descriptor_id"] = descriptor_id
                        for field in PERSON_FIELDS:
                            value = mapping[field]
                            if value not in person_ids:
                                person_ids[value] = len(people) + 1
                                people.append({"person_id": person_ids[value], "value": value})
                            fact[field + "_id"] = person_ids[value]
                        facts.append(fact)
                        if baseline_db is not None:
                            baseline_batch.append(tuple(mapping.values()))
                        if len(facts) >= 10000:
                            insert_arrow(db, "listings", pa.Table.from_pylist(facts, schema=FACT_SCHEMA))
                            if baseline_db is not None:
                                baseline_db.executemany("INSERT INTO listings VALUES (" + ",".join(["?"] * 25) + ")", baseline_batch)
                            facts.clear()
                            baseline_batch.clear()
                    if facts:
                        insert_arrow(db, "listings", pa.Table.from_pylist(facts, schema=FACT_SCHEMA))
                        if baseline_db is not None:
                            baseline_db.executemany("INSERT INTO listings VALUES (" + ",".join(["?"] * 25) + ")", baseline_batch)
                    scan_rows.append({
                        "scan_id": scan_id, "snapshot_sha256": source_sha,
                        "fingerprint": scan.fingerprint, "timestamp": scan.timestamp,
                        "source_date": scan.source_date, "realm_name": scan.realm_name,
                        "realm_id": scan.realm_id, "region_name": scan.region_name,
                        "region_id": scan.region_id, "record_count": scan.record_count,
                        "total_quantity": scan.total_quantity, "mapping_sha256": digest.hexdigest(),
                    })
                    scope_rows.extend({"scan_id": scan_id, **scope} for scope in scan.item_scopes)
                source_reports.append({
                    "path": str(source), "archive_bytes": source.stat().st_size,
                    "source_bytes": lua.stat().st_size, "sha256": source_sha,
                    "manifest_verified": bool(manifest),
                    "seconds": round(time.perf_counter() - source_started, 3),
                })
                del scans, decoded, scan, item, facts
                gc.collect()
                log("source_imported", **source_reports[-1])
        insert_arrow(db, "descriptors", pa.Table.from_pylist(descriptors, schema=DESCRIPTOR_SCHEMA), create=True)
        insert_arrow(db, "people", pa.Table.from_pylist(people, schema=pa.schema([
            ("person_id", pa.uint32()), ("value", pa.string()),
        ])), create=True)
        insert_arrow(db, "scans", pa.Table.from_pylist(scan_rows, schema=SCAN_SCHEMA), create=True)
        insert_arrow(db, "scopes", pa.Table.from_pylist(scope_rows, schema=SCOPE_SCHEMA), create=True)
        db.execute("""CREATE TABLE summaries AS
            SELECT f.scan_id, d.market_key, first(d.item_id) AS item_id,
                   first(d.name) AS name, first(d.texture) AS texture,
                   max(d.quality_id) AS quality_id,
                   first(d.battle_pet_creature_id) AS battle_pet_creature_id,
                   first(d.item_context) AS item_context,
                   first(d.crafting_quality) AS crafting_quality,
                   first(d.pet_variant_key) AS pet_variant_key,
                   first(d.item_link) AS representative_link,
                   count(*)::UBIGINT AS listing_count,
                   count(DISTINCT d.item_link)::UINTEGER AS variant_count,
                   sum(f.quantity)::UBIGINT AS total_quantity,
                   min(f.unit_price) AS min_unit_price,
                   min(nullif(f.buyout_amount, 0)) AS min_buyout
            FROM listings f JOIN descriptors d USING (descriptor_id)
            GROUP BY f.scan_id, d.market_key""")
        db.execute("CREATE INDEX listing_lookup ON listings(scan_id, descriptor_id)")
        db.execute("CREATE INDEX descriptor_lookup ON descriptors(market_key)")
        db.execute("CREATE INDEX summary_lookup ON summaries(scan_id, market_key)")
        db.execute("CHECKPOINT")
        checks = []
        for scan in scan_rows:
            log("verify_roundtrip", scan_id=scan["scan_id"], rows=scan["record_count"])
            cursor = db.execute(RESTORE_SQL + " WHERE f.scan_id = ? ORDER BY f.source_index", [scan["scan_id"]])
            columns = [c[0] for c in cursor.description]
            digest = hashlib.sha256()
            count = 0
            quantity = 0
            while rows := cursor.fetchmany(10000):
                for row in rows:
                    restored = dict(zip(columns, row))
                    update_digest(digest, restored)
                    count += 1
                    quantity += restored["quantity"]
            if (count != scan["record_count"] or quantity != scan["total_quantity"]
                    or digest.hexdigest() != scan["mapping_sha256"]):
                raise ValueError(f"Lossless round-trip verification failed: scan {scan['scan_id']}")
            checks.append({"scan_id": scan["scan_id"], "rows": count,
                           "total_quantity": quantity, "mapping_sha256": digest.hexdigest(),
                           "all_modeled_fields_match": True})
        if baseline_db is not None:
            baseline_db.execute("CREATE INDEX scan_item_price ON listings(scan_id,item_id,battle_pet_creature_id,unit_price)")
            baseline_db.execute("CREATE INDEX scan_name ON listings(scan_id,name)")
            baseline_db.commit()
            baseline_db.close()
            baseline_db = None
        stats = {
            "listings": db.execute("SELECT count(*) FROM listings").fetchone()[0],
            "descriptors": len(descriptors), "people": len(people),
            "summaries": db.execute("SELECT count(*) FROM summaries").fetchone()[0],
            "scans": len(scan_rows),
        }
        db.close()
        staging.rename(destination)
        report = {
            "format_version": 1, "source_commit": "a2950eb7d1e9f4ef07be388c77474a7ce8009c33",
            "database": str(destination), "duckdb_version": duckdb.__version__,
            "threads": threads, "duckdb_memory_limit": memory_limit,
            "build_seconds": round(time.perf_counter() - started, 3),
            "build_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            "database_bytes": destination.stat().st_size,
            "baseline_sqlite_bytes": baseline.stat().st_size if baseline else None,
            "baseline_note": "Denormalized SQLite copy with two indexes; not a MySQL size or latency benchmark.",
            "repeated_string_payload_bytes": plain_string_bytes,
            "sources": source_reports, "counts": stats, "scans": scan_rows,
            "roundtrip_checks": checks,
        }
        destination.with_suffix(".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        log("complete", **report)
        return report
    finally:
        if baseline_db is not None:
            baseline_db.close()
        try:
            db.close()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--memory-limit", default="1GB")
    args = parser.parse_args()
    build(args.sources, args.output, threads=args.threads,
          memory_limit=args.memory_limit, baseline=args.baseline)


if __name__ == "__main__":
    main()
