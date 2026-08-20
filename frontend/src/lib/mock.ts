import type {
  AuctionListing,
  ItemListingsResponse,
  ItemHistoryResponse,
  MarketItem,
  MarketCatalog,
  MarketItemsResponse,
  MarketQuery,
  MarketStatus,
} from "../types";

const names = [
  "鎏金先驱纹章",
  "暗影之剑",
  "究极法力药水",
  "巨龙瓶",
  "阿加法力之油",
  "午夜鲑鱼",
  "雷霆之皮",
  "永恒混沌合剂",
  "附魔鎏金纹章",
  "虚空碎片",
  "精制秘法纤维",
  "星界草籽",
  "淬火守卫板甲",
  "迅捷战斗药水",
  "海妖之泪",
  "工匠的敏锐",
  "灿烂太阳水晶",
  "迷离的专注合剂",
  "皇家墨水",
  "厚重雷龙皮革",
  "秘法师的羽毛笔",
  "失落时光之砂",
  "精炼亚口鱼油",
  "天空魔像零件",
  "闪耀的符文石",
  "远古苔原合剂",
  "织法者丝线",
  "无瑕炉石碎片",
  "鎏金凤凰羽毛",
  "巨龙群岛补给品",
];

const items: MarketItem[] = names.map((name, index) => {
  const itemID = 220_000 + index;
  const quantity = Math.max(3, Math.round(45_000 / (index + 1)));
  const unitPrice = 450 + index * index * 83 + (index % 4) * 3_700;
  return {
    scanId: 1,
    realm: "本地演示数据",
    realmID: 707,
    region: "CN",
    regionID: 5,
    itemID,
    battlePetCreatureID: null,
    marketKey: String(itemID),
    marketScope: index % 4 === 0 ? "realm" : "region",
    name,
    quality: [4, 4, 2, 1, 3, 1, 2, 3][index % 8],
    texture: null,
    listingCount: Math.max(2, Math.round(quantity / (17 + index))),
    variantCount: index % 7 === 0 ? 3 : 1,
    totalQuantity: quantity,
    minUnitPrice: unitPrice,
    minBuyout: unitPrice * (1 + (index % 12)),
  };
});

export async function mockCatalog(): Promise<MarketCatalog> {
  const scannedAt = new Date(Date.now() - 4 * 60_000).toISOString();
  return {
    realms: [{
      key: "5:707",
      region: "CN",
      regionID: 5,
      realm: "本地演示数据",
      normalizedRealm: "本地演示数据",
      realmID: 707,
      latestScanId: 1,
      scans: [{ scanId: 1, scannedAt, scannedAtUnix: Math.floor(Date.now() / 1000) - 240, listingCount: 380_668 }],
    }],
  };
}

export async function mockStatus(): Promise<MarketStatus> {
  await delay(180);
  return {
    available: true,
    complete: true,
    scanId: 1,
    scannedAt: new Date(Date.now() - 4 * 60_000).toISOString(),
    listingCount: 380_668,
    uniqueItemCount: 12_759,
    marketItemCount: 13_303,
    totalQuantity: 42_894_416,
    region: "CN",
    realm: "本地演示数据",
  };
}

export async function mockItems(query: MarketQuery): Promise<MarketItemsResponse> {
  await delay(220);
  const term = query.q.toLocaleLowerCase("zh-CN");
  const filtered = items.filter(
    (item) =>
      (!query.collection || [271434, 271435, 271436, 271438, 271440, 271441, 271444, 271445, 271638].includes(item.itemID))
      && (!term || item.name.toLocaleLowerCase("zh-CN").includes(term) || String(item.itemID).includes(term)),
  );
  const sorted = [...filtered].sort((left, right) => {
    switch (query.sort) {
      case "price_desc": return (right.minUnitPrice ?? -1) - (left.minUnitPrice ?? -1);
      case "quantity_desc": return right.totalQuantity - left.totalQuantity;
      case "listings_desc": return right.listingCount - left.listingCount;
      case "name_asc": return left.name.localeCompare(right.name, "zh-CN");
      default: return (left.minUnitPrice ?? Infinity) - (right.minUnitPrice ?? Infinity);
    }
  });
  const start = (query.page - 1) * query.pageSize;
  return {
    scanId: 1,
    page: query.page,
    pageSize: query.pageSize,
    total: sorted.length,
    totalPages: Math.ceil(sorted.length / query.pageSize),
    items: sorted.slice(start, start + query.pageSize),
  };
}

export async function mockListings(item: MarketItem, page: number, pageSize: number): Promise<ItemListingsResponse> {
  await delay(180);
  const total = Math.min(item.listingCount, 78);
  const all: AuctionListing[] = Array.from({ length: total }, (_, index) => {
    const quantity = 1 + (index % 18);
    const unitPrice = (item.minUnitPrice ?? 1) + index * Math.max(1, Math.round((item.minUnitPrice ?? 1) * 0.012));
    return {
      itemID: item.itemID,
      name: item.name,
      quality: item.quality,
      texture: null,
      battlePetCreatureID: item.battlePetCreatureID,
      quantity,
      unitPrice,
      buyoutAmount: unitPrice * quantity,
      minBid: Math.round(unitPrice * quantity * 0.92),
      bidAmount: 0,
      itemLink: null,
      timeLeftBand: (index % 4) + 1,
      timeLeftLabel: ["短（少于 30 分钟）", "中（30 分钟–2 小时）", "长（2–12 小时）", "非常长（超过 12 小时）"][index % 4],
      itemContext: null,
      difficulty: null,
      itemLevel: null,
      upgradeTrack: null,
      upgradeLevel: null,
      statLabel: null,
      hasSocket: false,
      petSpeciesID: null,
      petLevel: null,
      petQuality: null,
      petQualityLabel: null,
      petHealth: null,
      petPower: null,
      petSpeed: null,
      petDisplayID: null,
      petBreedCode: null,
      petBreedLabel: null,
      petBreedConfidence: null,
    };
  });
  const start = (page - 1) * pageSize;
  return {
    scanId: 1,
    itemID: item.itemID,
    battlePetCreatureID: item.battlePetCreatureID,
    marketKey: item.marketKey,
    name: item.name,
    quality: item.quality,
    texture: null,
    page,
    pageSize,
    total,
    totalPages: Math.ceil(total / pageSize),
    displayColumns: {
      tier: all.some((listing) => Boolean(
        listing.difficulty || listing.itemLevel || listing.upgradeTrack || listing.upgradeLevel
      )),
      attributes: all.some((listing) => Boolean(listing.statLabel || listing.hasSocket)),
      petVariant: all.some((listing) => listing.petSpeciesID != null),
    },
    items: all.slice(start, start + pageSize),
  };
}

export async function mockHistory(item: MarketItem): Promise<ItemHistoryResponse> {
  await delay(160);
  const currentPrice = item.minUnitPrice ?? 0;
  const previousPrice = Math.max(1, Math.round(currentPrice * (0.82 + (item.itemID % 7) * 0.045)));
  const previousQuantity = Math.max(1, Math.round(item.totalQuantity * (0.78 + (item.itemID % 5) * 0.08)));
  const previousListings = Math.max(1, Math.round(item.listingCount * (0.86 + (item.itemID % 3) * 0.12)));
  const firstTime = new Date("2026-08-19T20:41:00Z");
  const secondTime = new Date("2026-08-20T05:21:36Z");
  const metric = (previous: number, current: number) => ({
    previous,
    current,
    absolute: current - previous,
    percent: previous === 0 ? null : Math.round(((current - previous) / previous) * 10_000) / 100,
  });
  return {
    itemID: item.itemID,
    battlePetCreatureID: item.battlePetCreatureID,
    marketKey: item.marketKey,
    name: item.name,
    quality: item.quality,
    texture: null,
    marketScope: item.marketScope,
    pointCount: 2,
    change: {
      minUnitPrice: metric(previousPrice, currentPrice),
      minBuyout: metric(Math.max(1, Math.round((item.minBuyout ?? 0) * 0.91)), item.minBuyout ?? 0),
      listingCount: metric(previousListings, item.listingCount),
      totalQuantity: metric(previousQuantity, item.totalQuantity),
    },
    points: [
      {
        scanId: 1,
        scannedAt: firstTime.toISOString(),
        scannedAtUnix: Math.round(firstTime.getTime() / 1000),
        sourceDate: "2026-08-20",
        minUnitPrice: previousPrice,
        minBuyout: Math.max(1, Math.round((item.minBuyout ?? 0) * 0.91)),
        listingCount: previousListings,
        variantCount: item.variantCount,
        totalQuantity: previousQuantity,
        realm: "本地演示数据",
        realmID: 707,
        region: "CN",
        regionID: 5,
        marketScope: item.marketScope,
      },
      {
        scanId: 2,
        scannedAt: secondTime.toISOString(),
        scannedAtUnix: Math.round(secondTime.getTime() / 1000),
        sourceDate: "2026-08-20",
        minUnitPrice: currentPrice,
        minBuyout: item.minBuyout,
        listingCount: item.listingCount,
        variantCount: item.variantCount,
        totalQuantity: item.totalQuantity,
        realm: "本地演示数据",
        realmID: 707,
        region: "CN",
        regionID: 5,
        marketScope: item.marketScope,
      },
    ],
  };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
