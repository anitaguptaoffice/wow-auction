export type MarketSort =
  | "price_asc"
  | "price_desc"
  | "quantity_desc"
  | "listings_desc"
  | "name_asc";

export type MarketCollection = "" | "raid_boe_12_1";

export interface MarketStatus {
  available: boolean;
  complete: boolean;
  scanId?: number | null;
  snapshotSha256?: string;
  scannedAt?: string;
  scannedAtUnix?: number;
  sourceDate?: string;
  listingCount?: number;
  uniqueItemCount?: number;
  marketItemCount?: number;
  totalQuantity?: number;
  linkedItemCount?: number;
  durationMs?: number | null;
  region?: string;
  regionID?: number | null;
  realm?: string | null;
  normalizedRealm?: string | null;
  realmID?: number | null;
}

export interface MarketScanOption {
  scanId: number;
  scannedAt: string;
  scannedAtUnix: number;
  listingCount: number;
}

export interface MarketRealmOption {
  key: string;
  region?: string | null;
  regionID: number;
  realm: string;
  normalizedRealm?: string | null;
  realmID: number;
  latestScanId: number;
  scans: MarketScanOption[];
}

export interface MarketCatalog {
  realms: MarketRealmOption[];
}

export interface MarketItem {
  scanId: number;
  scannedAt: string;
  scannedAtUnix: number;
  realm?: string | null;
  realmID?: number | null;
  region?: string | null;
  regionID?: number | null;
  itemID: number;
  battlePetCreatureID: number | null;
  petVariantKey?: string | null;
  petSpeciesID?: number | null;
  petLevel?: number | null;
  petQuality?: number | null;
  petQualityLabel?: string | null;
  petHealth?: number | null;
  petPower?: number | null;
  petSpeed?: number | null;
  petDisplayID?: number | null;
  petBreedCode?: string | null;
  petBreedLabel?: string | null;
  petBreedConfidence?: "exact" | "ambiguous" | "unknown" | null;
  marketKey: string;
  marketScope: "region" | "realm" | "unknown";
  itemContext?: number | null;
  difficulty?: string | null;
  name: string;
  quality: number | null;
  texture: number | null;
  craftingQuality?: number | null;
  listingCount: number;
  variantCount: number;
  totalQuantity: number;
  minUnitPrice: number | null;
  minBuyout: number | null;
}

export interface MarketItemsResponse {
  scanId: number | null;
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
  items: MarketItem[];
}

export interface AuctionListing {
  itemID: number;
  name: string;
  quality: number | null;
  texture: number | null;
  craftingQuality?: number | null;
  battlePetCreatureID: number | null;
  quantity: number;
  unitPrice: number | null;
  buyoutAmount: number;
  minBid: number;
  bidAmount: number;
  itemLink: string | null;
  timeLeftBand: number;
  timeLeftLabel: string;
  itemContext: number | null;
  difficulty: string | null;
  itemLevel: number | null;
  upgradeTrack: string | null;
  upgradeLevel: string | null;
  statLabel: string | null;
  hasSocket: boolean;
  petSpeciesID: number | null;
  petLevel: number | null;
  petQuality: number | null;
  petQualityLabel: string | null;
  petHealth: number | null;
  petPower: number | null;
  petSpeed: number | null;
  petDisplayID: number | null;
  petBreedCode: string | null;
  petBreedLabel: string | null;
  petBreedConfidence: "exact" | "ambiguous" | "unknown" | null;
}

export interface ItemListingsResponse {
  scanId: number;
  itemID: number;
  battlePetCreatureID: number | null;
  petVariantKey?: string | null;
  marketKey: string;
  itemContext?: number | null;
  difficulty?: string | null;
  name: string;
  quality: number | null;
  texture: number | null;
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
  displayColumns: {
    tier: boolean;
    attributes: boolean;
    petVariant: boolean;
  };
  items: AuctionListing[];
}

export interface HistoryMetricChange {
  previous: number;
  current: number;
  absolute: number;
  percent: number | null;
}

export interface ItemHistoryPoint {
  scanId: number;
  scannedAt: string;
  scannedAtUnix: number;
  sourceDate: string;
  minUnitPrice: number | null;
  minBuyout: number | null;
  listingCount: number;
  variantCount: number;
  totalQuantity: number;
  realm?: string | null;
  realmID?: number | null;
  region?: string | null;
  regionID?: number | null;
  marketScope: "region" | "realm" | "unknown";
}

export interface ItemHistoryResponse {
  itemID: number;
  battlePetCreatureID: number | null;
  petVariantKey?: string | null;
  marketKey: string;
  itemContext?: number | null;
  difficulty?: string | null;
  name: string;
  quality: number | null;
  texture: number | null;
  marketScope: "region" | "realm" | "unknown";
  pointCount: number;
  change: {
    minUnitPrice: HistoryMetricChange | null;
    minBuyout: HistoryMetricChange | null;
    listingCount: HistoryMetricChange | null;
    totalQuantity: HistoryMetricChange | null;
  };
  points: ItemHistoryPoint[];
}

export interface MarketQuery {
  q: string;
  collection: MarketCollection;
  page: number;
  pageSize: number;
  sort: MarketSort;
}
