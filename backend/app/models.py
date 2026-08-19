from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.database import Base


class AuctionSnapshot(Base):
    """One successfully imported SavedVariables file."""

    __tablename__ = "wow_auction_snapshots"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    id = Column(Integer, primary_key=True)
    sha256 = Column(String(64), nullable=False, unique=True, index=True)
    source_path = Column(String(1024), nullable=False)
    source_size = Column(BigInteger, nullable=False)
    source_scan_count = Column(Integer, nullable=False)
    imported_scan_count = Column(Integer, nullable=False)
    imported_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class AuctionScan(Base):
    """Validated metadata for one complete auction-house replicate scan."""

    __tablename__ = "wow_auction_scans"
    __table_args__ = (
        UniqueConstraint("scan_fingerprint", name="uq_wow_auction_scans_fingerprint"),
        Index("ix_wow_auction_scans_complete_time", "complete", "scanned_at_unix"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id = Column(Integer, primary_key=True)
    snapshot_id = Column(Integer, ForeignKey("wow_auction_snapshots.id", ondelete="RESTRICT"), nullable=False)
    snapshot_sha256 = Column(String(64), nullable=False, index=True)
    scan_fingerprint = Column(String(64), nullable=False)
    source_date = Column(String(10), nullable=False)
    source_scan_index = Column(Integer, nullable=False)
    scanned_at = Column(DateTime, nullable=False)
    scanned_at_unix = Column(BigInteger, nullable=False, index=True)
    declared_item_count = Column(Integer, nullable=False)
    record_count = Column(Integer, nullable=False)
    imported_listing_count = Column(Integer, nullable=False)
    unique_item_count = Column(Integer, nullable=False)
    market_item_count = Column(Integer, nullable=False)
    total_quantity = Column(BigInteger, nullable=False)
    linked_item_count = Column(Integer, nullable=False, default=0)
    missing_core_count = Column(Integer, nullable=False, default=0)
    incomplete_info_count = Column(Integer, nullable=False, default=0)
    api_error_count = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Float, nullable=True)
    complete = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class AuctionListing(Base):
    """A single row returned by WoW's replicate auction-house API."""

    __tablename__ = "wow_auction_listings"
    __table_args__ = (
        Index("ix_wow_auction_listings_scan_item", "scan_id", "item_id"),
        Index(
            "ix_wow_auction_listings_scan_item_pet_price",
            "scan_id",
            "item_id",
            "battle_pet_creature_id",
            "unit_price",
        ),
        Index("ix_wow_auction_listings_scan_name", "scan_id", "name"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    scan_id = Column(Integer, ForeignKey("wow_auction_scans.id", ondelete="CASCADE"), nullable=False)
    source_index = Column(Integer, nullable=False)
    item_id = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    texture = Column(BigInteger, nullable=True)
    quantity = Column(Integer, nullable=False)
    quality_id = Column(Integer, nullable=True)
    usable = Column(Boolean, nullable=True)
    level = Column(Integer, nullable=True)
    level_type = Column(String(64), nullable=True)
    min_bid = Column(BigInteger, nullable=False)
    min_increment = Column(BigInteger, nullable=True)
    buyout_amount = Column(BigInteger, nullable=False)
    unit_price = Column(BigInteger, nullable=True)
    bid_amount = Column(BigInteger, nullable=False)
    high_bidder = Column(Boolean, nullable=True)
    bidder_full_name = Column(String(255), nullable=True)
    owner = Column(String(255), nullable=True)
    owner_full_name = Column(String(255), nullable=True)
    sale_status = Column(Integer, nullable=True)
    has_all_info = Column(Boolean, nullable=True)
    item_link = Column(Text, nullable=True)
    time_left_band = Column(Integer, nullable=False)
    battle_pet_creature_id = Column(Integer, nullable=True)
    battle_pet_display_id = Column(Integer, nullable=True)


class AuctionItemSummary(Base):
    """Per-item materialized aggregation for one scan's website listing."""

    __tablename__ = "wow_auction_item_summaries"
    __table_args__ = (
        UniqueConstraint(
            "scan_id", "item_id", "battle_pet_creature_id", name="uq_wow_auction_summaries_scan_market"
        ),
        Index("ix_wow_auction_summaries_scan_price", "scan_id", "min_unit_price"),
        Index("ix_wow_auction_summaries_scan_quantity", "scan_id", "total_quantity"),
        Index("ix_wow_auction_summaries_scan_listings", "scan_id", "listing_count"),
        Index("ix_wow_auction_summaries_scan_name", "scan_id", "name"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    scan_id = Column(Integer, ForeignKey("wow_auction_scans.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(Integer, nullable=False)
    battle_pet_creature_id = Column(Integer, nullable=False, default=0)
    name = Column(String(255), nullable=False)
    quality_id = Column(Integer, nullable=True)
    texture = Column(BigInteger, nullable=True)
    listing_count = Column(Integer, nullable=False)
    variant_count = Column(Integer, nullable=False)
    total_quantity = Column(BigInteger, nullable=False)
    min_unit_price = Column(BigInteger, nullable=True)
    min_buyout = Column(BigInteger, nullable=True)
