"""Lossless dimensions and typed numeric facts for the existing importer model."""

import pyarrow as pa

DESCRIPTOR_FIELDS = (
    "item_id", "name", "texture", "quality_id", "usable", "level", "level_type",
    "item_link", "battle_pet_creature_id", "battle_pet_display_id",
)
PERSON_FIELDS = ("owner", "owner_full_name", "bidder_full_name")
FACT_FIELDS = (
    "scan_id", "source_index", "descriptor_id", "quantity", "min_bid",
    "min_increment", "buyout_amount", "unit_price", "bid_amount", "high_bidder",
    "owner_id", "owner_full_name_id", "bidder_full_name_id", "sale_status",
    "has_all_info", "time_left_band",
)
FACT_SCHEMA = pa.schema([
    ("scan_id", pa.uint32()), ("source_index", pa.uint32()),
    ("descriptor_id", pa.uint32()), ("quantity", pa.uint32()),
    ("min_bid", pa.uint64()), ("min_increment", pa.uint64()),
    ("buyout_amount", pa.uint64()), ("unit_price", pa.uint64()),
    ("bid_amount", pa.uint64()), ("high_bidder", pa.bool_()),
    ("owner_id", pa.uint32()), ("owner_full_name_id", pa.uint32()),
    ("bidder_full_name_id", pa.uint32()), ("sale_status", pa.int32()),
    ("has_all_info", pa.bool_()), ("time_left_band", pa.uint8()),
])
DESCRIPTOR_SCHEMA = pa.schema([
    ("descriptor_id", pa.uint32()), ("item_id", pa.uint32()), ("name", pa.string()),
    ("texture", pa.uint64()), ("quality_id", pa.int32()), ("usable", pa.bool_()),
    ("level", pa.int32()), ("level_type", pa.string()), ("item_link", pa.string()),
    ("battle_pet_creature_id", pa.uint32()), ("battle_pet_display_id", pa.uint32()),
    ("item_context", pa.int32()), ("crafting_quality", pa.int32()),
    ("pet_variant_key", pa.string()), ("market_key", pa.string()),
])
SCAN_SCHEMA = pa.schema([
    ("scan_id", pa.uint32()), ("snapshot_sha256", pa.string()),
    ("fingerprint", pa.string()), ("timestamp", pa.int64()),
    ("source_date", pa.string()), ("realm_name", pa.string()),
    ("realm_id", pa.int32()), ("region_name", pa.string()),
    ("region_id", pa.int32()), ("record_count", pa.uint64()),
    ("total_quantity", pa.uint64()), ("mapping_sha256", pa.string()),
])
SCOPE_SCHEMA = pa.schema([
    ("scan_id", pa.uint32()), ("item_id", pa.uint32()), ("market_scope", pa.string()),
])

# The projected columns match _listing_mapping exactly, including optional fields.
RESTORE_SQL = """
SELECT f.scan_id, f.source_index, d.item_id, d.name, d.texture, f.quantity,
       d.quality_id, d.usable, d.level, d.level_type, f.min_bid, f.min_increment,
       f.buyout_amount, f.unit_price, f.bid_amount, f.high_bidder,
       bidder.value AS bidder_full_name, owner.value AS owner,
       full_owner.value AS owner_full_name, f.sale_status, f.has_all_info,
       d.item_link, f.time_left_band, d.battle_pet_creature_id,
       d.battle_pet_display_id
FROM listings f JOIN descriptors d USING (descriptor_id)
LEFT JOIN people owner ON owner.person_id = f.owner_id
LEFT JOIN people full_owner ON full_owner.person_id = f.owner_full_name_id
LEFT JOIN people bidder ON bidder.person_id = f.bidder_full_name_id
"""
