import type {
  ItemListingsResponse,
  ItemHistoryResponse,
  MarketItem,
  MarketItemsResponse,
  MarketQuery,
  MarketStatus,
} from "../types";
import { mockHistory, mockItems, mockListings, mockStatus } from "./mock";

const useMockData = import.meta.env.DEV && import.meta.env.VITE_USE_MOCK_DATA === "true";

const productionApiByHost: Record<string, string> = {
  "raidbot-5gh3h2nx762bedc5-1251932919.tcloudbaseapp.com":
    "https://wow-auction-api-273424-4-1251932919.sh.run.tcloudbase.com/api",
};

function getApiBase(): string {
  const runtimeBase = (window as typeof window & { WOW_AUCTION_API_BASE?: string })
    .WOW_AUCTION_API_BASE;
  const buildTimeBase = import.meta.env.VITE_API_BASE as string | undefined;
  const configured =
    runtimeBase || buildTimeBase || productionApiByHost[window.location.hostname] || "/api";
  return configured.replace(/\/+$/, "");
}

export function getIconProxyUrl(iconName: string): string {
  return new URL(
    `${getApiBase()}/icons/${encodeURIComponent(iconName)}.jpg`,
    window.location.href,
  ).toString();
}

async function requestJson<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const search = new URLSearchParams();
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  });
  const suffix = search.size > 0 ? `?${search.toString()}` : "";
  const url = new URL(`${getApiBase()}${path}${suffix}`, window.location.href);
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  const data = (await response.json().catch(() => null)) as unknown;
  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : `请求失败（HTTP ${response.status}）`;
    throw new Error(detail);
  }
  if (!data || typeof data !== "object") {
    throw new Error("服务器返回了无法识别的数据");
  }
  return data as T;
}

export function fetchMarketStatus(): Promise<MarketStatus> {
  if (useMockData) return mockStatus();
  return requestJson<MarketStatus>("/market/status");
}

export function fetchMarketItems(query: MarketQuery): Promise<MarketItemsResponse> {
  if (useMockData) return mockItems(query);
  return requestJson<MarketItemsResponse>("/market/items", {
    q: query.q,
    collection: query.collection,
    page: query.page,
    page_size: query.pageSize,
    sort: query.sort,
  });
}

export function fetchItemListings(
  item: MarketItem,
  page: number,
  pageSize = 25,
): Promise<ItemListingsResponse> {
  if (useMockData) return mockListings(item, page, pageSize);
  return requestJson<ItemListingsResponse>(
    `/market/items/${encodeURIComponent(item.itemID)}/listings`,
    {
      battle_pet_creature_id: item.battlePetCreatureID,
      item_context: item.itemContext,
      pet_variant_key: item.petVariantKey,
      page,
      page_size: pageSize,
    },
  );
}

export function fetchItemHistory(item: MarketItem): Promise<ItemHistoryResponse> {
  if (useMockData) return mockHistory(item);
  return requestJson<ItemHistoryResponse>(
    `/market/items/${encodeURIComponent(item.itemID)}/history`,
    {
      battle_pet_creature_id: item.battlePetCreatureID,
      item_context: item.itemContext,
      pet_variant_key: item.petVariantKey,
    },
  );
}
