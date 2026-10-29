import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Select } from "./components/Select";
import { WowheadLink } from "./components/WowheadLink";
import { enableWowheadTooltips } from "./lib/wowhead";
import { DIFFICULTY_OPTIONS, STAT_OPTIONS, SOCKET_OPTIONS, TERTIARY_OPTIONS, TERTIARY_LABELS,
  EMPTY_EQUIPMENT_FILTERS, readEquipmentFilters, equipmentFilterLabels } from "./lib/equipment";
import {
  ChevronLeft,
  ChevronRight,
  Clock3,
  Database,
  PackageSearch,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { fetchItemComparison, fetchItemHistory, fetchItemListings, fetchMarketCatalog, fetchMarketItems, fetchMarketStatus, getIconProxyUrl } from "./lib/api";
import iconMap from "./generated/icon-map.json";
import localIconMap from "./generated/local-icon-map.json";
import {
  formatDate,
  formatInteger,
  formatShortDate,
  qualityClass,
  splitCopper,
} from "./lib/format";
import type {
  AuctionListing,
  HistoryMetricChange,
  ItemHistoryResponse,
  MarketItem,
  MarketCollection,
  MarketQuery,
  MarketSort,
} from "./types";

const PAGE_SIZES = [20, 50, 100] as const;
const PAGE_SIZE_OPTIONS = PAGE_SIZES.map((value) => ({ value, label: `${value} 条` }));
const LISTING_PAGE_SIZE_OPTIONS = [25, 50, 100].map(value => ({ value, label: `${value} 条` }));
const ICON_CDN_BASE = (
  import.meta.env.VITE_WOW_ICON_BASE_URL
  ?? "https://render.worldofwarcraft.com/us/icons/56"
).replace(/\/$/, "");
const SORT_OPTIONS: Array<{ value: MarketSort; label: string }> = [
  { value: "price_asc", label: "价格最低" },
  { value: "price_desc", label: "价格最高" },
  { value: "quantity_desc", label: "库存最多" },
  { value: "listings_desc", label: "挂单最多" },
  { value: "name_asc", label: "物品名称" },
];

function observationAge(value: string): string {
  const seconds = (Date.now() - Date.parse(value)) / 1000;
  if (!Number.isFinite(seconds) || seconds < 0) return '';
  if (seconds < 3600) return `${Math.max(1, Math.floor(seconds / 60))} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

function initialQuery(): MarketQuery {
  const params = new URLSearchParams(window.location.search);
  const requestedSort = params.get("sort") as MarketSort | null;
  const sort = SORT_OPTIONS.some((option) => option.value === requestedSort)
    ? requestedSort!
    : "price_asc";
  const requestedSize = Number(params.get("page_size"));
  const requestedCollection = params.get("collection");
  const collection: MarketCollection = requestedCollection === "raid_boe_12_1"
    ? requestedCollection
    : "";
  const pageSize = PAGE_SIZES.includes(requestedSize as (typeof PAGE_SIZES)[number])
    ? requestedSize
    : 20;
  return {
    market: params.get("market") ?? "",
    scanId: Number.isSafeInteger(Number(params.get("scan_id"))) && Number(params.get("scan_id")) > 0 ? Number(params.get("scan_id")) : undefined,
    q: params.get("q")?.trim() ?? "",
    ...(collection ? readEquipmentFilters(params) : EMPTY_EQUIPMENT_FILTERS),
    collection,
    page: Math.max(1, Number(params.get("page")) || 1),
    pageSize,
    sort,
  };
}

export function App() {
  useEffect(enableWowheadTooltips, []);
  const queryClient = useQueryClient();
  const [query, setQuery] = useState<MarketQuery>(initialQuery);
  const [searchDraft, setSearchDraft] = useState(query.q);
  const [selectedItem, setSelectedItem] = useState<MarketItem | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = "parchment";
    localStorage.removeItem("wow-auction-theme");
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const q = searchDraft.trim();
      setQuery((current) => (current.q === q ? current : { ...current, q, page: 1 }));
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchDraft]);

  useEffect(() => {
    const params = new URLSearchParams();
    if (query.q) params.set("q", query.q);
    if (query.market) params.set("market", query.market);
    if (query.scanId) params.set("scan_id", String(query.scanId));
    if (query.collection) params.set("collection", query.collection);
    if (query.collection) {
      for (const key of ["difficulty", "stats", "socket", "tertiary"] as const) {
        if (query[key]) params.set(key, query[key]!);
      }
    }
    if (query.page > 1) params.set("page", String(query.page));
    if (query.pageSize !== 20) params.set("page_size", String(query.pageSize));
    if (query.sort !== "price_asc") params.set("sort", query.sort);
    const queryString = params.toString();
    const suffix = queryString ? `?${queryString}` : window.location.pathname;
    window.history.replaceState(null, "", suffix);
  }, [query]);

  const catalogQuery = useQuery({ queryKey: ["market-catalog"], queryFn: fetchMarketCatalog });
  const selectedMarket = catalogQuery.data?.realms.find(realm => realm.key === query.market);
  const selectedScan = selectedMarket?.scans.find(scan => scan.scanId === (query.scanId ?? selectedMarket.latestScanId));
  const invalidScope = Boolean(catalogQuery.data && ((query.market && !selectedScan) || (!query.market && query.scanId)));
  const scopeReady = !query.market || Boolean(selectedScan);
  const activeScanId = selectedScan?.scanId;
  const statusQuery = useQuery({
    queryKey: ["market-status", activeScanId],
    queryFn: () => fetchMarketStatus(activeScanId),
    enabled: scopeReady && !invalidScope,
  });
  const itemsQuery = useQuery({
    queryKey: ["market-items", query, activeScanId],
    queryFn: () => fetchMarketItems({ ...query, scanId: activeScanId }),
    enabled: scopeReady && !invalidScope,
  });
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["market-status"] }),
      queryClient.invalidateQueries({ queryKey: ["market-items"] }),
      queryClient.invalidateQueries({ queryKey: ["market-catalog"] }),
    ]);
  };

  const status = statusQuery.data;
  const data = itemsQuery.data;

  return (
    <div className="app-shell">
      <header className="site-header">
        <div className="header-inner page-width">
          <a className="brand" href="./" aria-label="艾泽拉斯交易所首页">
            <img className="brand-mark" src="/icons/wow-orc.png" alt="" width="44" height="44" />
            <span className="brand-copy">
              <strong>艾泽拉斯交易所</strong>
              <small>AZEROTH EXCHANGE</small>
            </span>
          </a>

          <div className="header-actions">
            <button
              className="icon-button"
              type="button"
              onClick={refresh}
              disabled={statusQuery.isFetching || itemsQuery.isFetching}
              aria-label="刷新市场数据"
            >
              <RefreshCw
                size={18}
                className={statusQuery.isFetching || itemsQuery.isFetching ? "spin-slow" : ""}
              />
            </button>
          </div>
        </div>
      </header>

      <main className="page-width main-content">
        <section className="market-panel" aria-labelledby="market-title">
          <div className="market-navigation">
            <nav className="market-tabs" aria-label="行情分类">
              {([['', '全部物品'], ['raid_boe_12_1', '12.1 团本装绑']] as const).map(([collection, label]) => (
                <button key={collection} type="button" aria-pressed={query.collection === collection}
                  onClick={() => setQuery(current => ({ ...current, ...EMPTY_EQUIPMENT_FILTERS, collection, page: 1 }))}>
                  {label}
                </button>
              ))}
            </nav>
            <div className="market-freshness">
              {!query.market ? <span>各市场采集时间见物品报价</span>
                : statusQuery.isLoading ? <span>正在读取数据时间…</span>
                : statusQuery.isError ? <><span>数据时间读取失败</span><button type="button" onClick={() => statusQuery.refetch()}>重试</button></>
                : status?.scannedAt ? <time dateTime={status.scannedAt} title={`${formatDate(status.scannedAt)}，${observationAge(status.scannedAt)}`}>采集于 {formatShortDate(status.scannedAt)}</time>
                : <span>暂无数据时间</span>}
            </div>
          </div>
          <div className="market-scope-controls" aria-label="市场与快照">
            <Select label="服务器与市场" prefix="市场" appearance="filter" value={query.market ?? ""}
              options={[{ value: "", label: "全部市场" }, ...(catalogQuery.data?.realms ?? []).map(realm => ({ value: realm.key, label: realm.realmID === 0 ? "全服商品" : realm.realm }))]}
              onChange={market => setQuery(current => ({ ...current, market, scanId: undefined, page: 1 }))} />
            {selectedMarket && <Select label="历史快照" prefix="快照" appearance="filter" value={query.scanId ?? 0}
              options={[{ value: 0, label: "最新快照" }, ...selectedMarket.scans.map(scan => ({ value: scan.scanId, label: formatDate(scan.scannedAt) }))]}
              onChange={scanId => setQuery(current => ({ ...current, scanId: scanId || undefined, page: 1 }))} />}
            {catalogQuery.isError && <button className="equipment-filter-reset" onClick={() => catalogQuery.refetch()}>服务器列表读取失败，重试</button>}
          </div>
          {invalidScope && <p className="market-data-warning" role="alert">该市场或快照已移除。<button onClick={() => setQuery(current => ({ ...current, market: "", scanId: undefined, page: 1 }))}>查看全部市场</button></p>}
          {status?.available && status.complete === false && <p className="market-data-warning" role="status">快照不完整，价格与数量可能有缺失。</p>}
          {status?.updateState && ['failed', 'publication_unknown', 'unknown'].includes(status.updateState) && <p className="market-data-warning" role="status">最近一次数据更新未完成，正在显示上次可用快照。</p>}
          <div className="market-toolbar">
            <label className="market-search">
              <Search size={17} aria-hidden="true" />
              <input
                type="search"
                value={searchDraft}
                onChange={(event) => setSearchDraft(event.target.value)}
                placeholder="搜索物品名称或物品 ID"
                aria-label="搜索拍卖物品"
              />
              {searchDraft && (
                <button type="button" onClick={() => setSearchDraft("")} aria-label="清除搜索">
                  <X size={16} />
                </button>
              )}
            </label>
            <div className="market-controls">
              <Select label="排序方式" prefix="排序" value={query.sort} options={SORT_OPTIONS}
                onChange={(sort) => setQuery(current => ({ ...current, sort, page: 1 }))} />
              <Select label="每页条数" prefix="每页" value={query.pageSize} options={PAGE_SIZE_OPTIONS}
                onChange={(pageSize) => setQuery(current => ({ ...current, pageSize, page: 1 }))} />
            </div>
          </div>
          {query.collection === "raid_boe_12_1" && (
            <div className="equipment-filters" aria-label="团本装绑筛选">
              <div className="equipment-filter-controls">
                {([
                  ["difficulty", "难度档位", "难度", DIFFICULTY_OPTIONS],
                  ["stats", "属性变体", "属性", STAT_OPTIONS],
                  ["socket", "棱彩插槽", "插槽", SOCKET_OPTIONS],
                  ["tertiary", "额外第三属性", "额外", TERTIARY_OPTIONS],
                ] as const).map(([key, label, prefix, options]) => (
                  <Select key={key} label={label} prefix={prefix} appearance="filter" value={query[key] ?? ""} options={options}
                    onChange={(value) => setQuery(current => ({ ...current, [key]: value, page: 1 }))} />
                ))}
              </div>
            </div>
          )}

          <div className="market-results-summary">
            <h1 id="market-title" className="sr-only">{query.collection === "raid_boe_12_1" ? "12.1 团本装绑行情" : "物品行情"}</h1>
            <p>{itemsQuery.isLoading ? "正在查找物品…" : itemsQuery.isError ? "物品列表读取失败" : <><strong>{formatInteger(data?.total)}</strong> 项结果</>}</p>
            {query.q && <span>搜索：{query.q}</span>}
            {equipmentFilterLabels(query).length > 0 && <span className="applied-filter-status" title="当前价格、数量与挂单仅统计同时符合全部筛选条件的装备">已筛选
              <button className="equipment-filter-reset" type="button"
                onClick={() => setQuery(current => ({ ...current, ...EMPTY_EQUIPMENT_FILTERS, page: 1 }))}>清除</button>
            </span>}
            {itemsQuery.isFetching && !itemsQuery.isLoading && <span role="status">正在更新结果…</span>}
          </div>

          <MarketTable
            items={invalidScope ? [] : data?.items ?? []}
            isLoading={!invalidScope && (itemsQuery.isLoading || (!scopeReady && catalogQuery.isLoading))}
            isFetching={itemsQuery.isFetching}
            error={itemsQuery.error}
            hasSearch={Boolean(query.q || query.collection)}
            hasFilters={equipmentFilterLabels(query).length > 0}
            equipmentView={query.collection === "raid_boe_12_1"}
            onSelect={(item) => { if (!itemsQuery.isFetching) setSelectedItem(item); }}
            onRetry={() => itemsQuery.refetch()}
            onClearSearch={() => {
              if (equipmentFilterLabels(query).length) {
                setQuery(current => ({ ...current, ...EMPTY_EQUIPMENT_FILTERS, page: 1 }));
              } else {
                setSearchDraft("");
                setQuery(current => ({ ...current, q: "", page: 1 }));
              }
            }}
          />

          {data && data.totalPages > 0 && (
            <Pagination
              page={data.page}
              pages={data.totalPages}
              pageSize={data.pageSize}
              total={data.total}
              onChange={(page) => {
                setQuery((current) => ({ ...current, page }));
                document.querySelector(".market-panel")?.scrollIntoView({ behavior: "smooth" });
              }}
            />
          )}
        </section>
      </main>

      <footer className="site-footer">
        <div className="page-width">
          <span>艾泽拉斯交易所</span>
          <p>实际市场可能在快照生成后发生变化。</p>
        </div>
      </footer>

      {selectedItem && <ItemDetails key={`${selectedItem.marketKey}:${selectedItem.scanId}`} item={selectedItem} onClose={() => setSelectedItem(null)} onSelect={setSelectedItem} />}
    </div>
  );
}

function ItemGlyph({ item }: { item: MarketItem }) {
  const glyph = Array.from(item.name.trim())[0] || "?";
  const iconName = item.iconName ?? (item.texture
    ? (iconMap as Record<string, string>)[String(item.texture)]
    : undefined);
  const cdnIconName = iconName && !iconName.startsWith("filedata-") ? iconName : undefined;
  const iconUrl = cdnIconName ? `${ICON_CDN_BASE}/${cdnIconName}.jpg` : undefined;
  const localIconExtension = iconName
    ? (localIconMap as Record<string, string>)[iconName]
    : undefined;
  const localIconUrl = iconName && localIconExtension
    ? `${import.meta.env.BASE_URL}wow-icons/${iconName}.${localIconExtension}`
    : undefined;
  const proxyIconUrl = iconName ? getIconProxyUrl(iconName) : undefined;
  const preferredIconUrl = localIconUrl ?? iconUrl ?? proxyIconUrl;
  const [imageSource, setImageSource] = useState(preferredIconUrl);

  useEffect(() => setImageSource(preferredIconUrl), [preferredIconUrl]);

  return (
    <span
      className={`item-glyph ${qualityClass(item.quality)} ${imageSource ? "has-image" : "is-fallback"}`}
      aria-hidden="true"
    >
      {imageSource ? (
        <img
          src={imageSource}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setImageSource((current) => {
            if (current === localIconUrl) return iconUrl ?? proxyIconUrl;
            if (current === iconUrl) return proxyIconUrl;
            return undefined;
          })}
        />
      ) : glyph}
    </span>
  );
}

function Money({ value }: { value?: number | null }) {
  const parts = splitCopper(value);
  if (!parts) return <span className="muted">—</span>;
  return (
    <span className="money" title={`${formatInteger(value)} 铜币`}>
      {parts.gold > 0 && <><b>{formatInteger(parts.gold)}</b><i className="coin gold" /></>}
      {(parts.gold > 0 || parts.silver > 0) && (
        <><b>{parts.gold > 0 ? String(parts.silver).padStart(2, "0") : parts.silver}</b><i className="coin silver" /></>
      )}
      <b>{parts.gold > 0 || parts.silver > 0 ? String(parts.copper).padStart(2, "0") : parts.copper}</b>
      <i className="coin copper" />
    </span>
  );
}

function CraftingQuality({ quality }: { quality?: number | null }) {
  if (!quality) return null;
  return (
    <span className="crafting-quality" aria-label={`制造品质 ${quality} 星`} title={`制造品质 ${quality} 星`}>
      {"★".repeat(quality)}
    </span>
  );
}

function MarketTable({
  items,
  isLoading,
  isFetching,
  error,
  hasSearch,
  hasFilters = false,
  equipmentView = false,
  onSelect,
  onRetry,
  onClearSearch,
}: {
  items: MarketItem[];
  isLoading: boolean;
  isFetching: boolean;
  error: Error | null;
  hasSearch: boolean;
  hasFilters?: boolean;
  equipmentView?: boolean;
  onSelect: (item: MarketItem) => void;
  onRetry: () => void;
  onClearSearch: () => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  useEffect(() => setExpanded(new Set()), [items]);
  if (isLoading) return <PanelState kind="loading" title="正在加载市场数据" message="正在读取最新快照中的物品…" />;
  if (error) return <PanelState kind="error" title="无法加载物品列表" message={error.message} action="重新加载" onAction={onRetry} />;
  if (!items.length) {
    return (
      <PanelState
        kind="empty"
        title={hasSearch ? "没有找到匹配物品" : "快照中没有可显示的物品"}
        message={hasFilters ? "试试放宽难度、属性或插槽条件。" : hasSearch ? "试试缩短关键词，或者直接输入物品 ID。" : "当前没有可查询的拍卖数据。"}
        action={hasFilters ? "重置筛选" : hasSearch ? "清除搜索" : undefined}
        onAction={onClearSearch}
      />
    );
  }
  const showsDifficulty = items.some((item) => item.difficulty);
  const grouped = items.some(item => item.marketGroup);
  return (
    <div className={`table-wrap ${isFetching ? "is-refreshing" : ""}`}>
      <table className="market-table">
        <thead>
          <tr>
            <th>物品</th>
            {showsDifficulty && <th>档位</th>}
            <th className="number-cell">最低单价</th>
            <th className="number-cell buyout-column">最低一口价</th>
            <th className="number-cell">{grouped ? "挂单合计" : "挂单"}</th>
            <th className="number-cell">{grouped ? "数量合计" : "总数量"}</th>
            <th><span className="sr-only">操作</span></th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const key = `${item.marketKey}:${item.regionID}:${item.scanId}`;
            const multiple = (item.marketGroup?.marketCount ?? 0) > 1;
            const showsQuotes = Boolean(item.marketGroup) && (equipmentView || Boolean(item.difficulty));
            const open = expanded.has(key);
            const panelId = `quotes-${item.scanId}-${encodeURIComponent(item.marketKey)}`;
            const actionLabel = showsQuotes ? (open ? "收起报价" : "各服报价") : "查看挂单";
            const activate = () => {
              if (isFetching) return;
              if (!showsQuotes) { onSelect(item); return; }
              setExpanded(current => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next; });
            };
            return <Fragment key={key}><tr className="market-item-row" tabIndex={isFetching ? -1 : 0}
              aria-label={`${item.name}，${actionLabel}`} aria-disabled={isFetching || undefined}
              aria-expanded={showsQuotes ? open : undefined} aria-controls={showsQuotes && open ? panelId : undefined}
              onClick={event => {
                if ((event.target as Element).closest('a, button, input, select, textarea, summary, [role="button"], [role="combobox"]')) return;
                const selection = window.getSelection();
                if (selection && !selection.isCollapsed && selection.anchorNode && event.currentTarget.contains(selection.anchorNode)) return;
                activate();
              }}
              onKeyDown={event => {
                if (event.target !== event.currentTarget || !["Enter", " "].includes(event.key)) return;
                event.preventDefault();
                if (!event.repeat) activate();
              }}>
              <td data-label="物品">
                <div className="item-identity">
                  <WowheadLink item={item} className="wowhead-icon-link"><ItemGlyph item={item} /></WowheadLink>
                  <div className="item-copy">
                    <div className="item-title-line">
                      <WowheadLink item={item}><strong className={qualityClass(item.quality)}>{item.name}</strong></WowheadLink>
                      <CraftingQuality quality={item.craftingQuality} />
                      {multiple ? <span className="market-scope-badge scope-realm">{item.marketGroup!.marketCount} 服报价</span>
                        : <MarketScopeBadge scope={item.marketScope} realm={item.realm} />}
                    </div>
                    <small>
                      ID {item.itemID}
                      {item.battlePetCreatureID ? ` · 战宠 ${item.battlePetCreatureID}` : ""}
                      {item.petVariantKey
                        ? ` · ${item.petBreedCode || "未知 Breed"}${item.petBreedLabel ? ` ${item.petBreedLabel}` : ""} · ${item.petLevel}级 · ${item.petQualityLabel || "未知品质"}`
                        : !item.difficulty && (item.statCombinationCount ?? 0) > 1 ? ` · ${item.statCombinationCount} 种副属性组合` : ""}
                    </small>
                    <small className="item-observed-at"><time dateTime={item.scannedAt} title={formatDate(item.scannedAt)}>{multiple && item.minUnitPrice != null ? "最低价采集于" : "采集于"} {formatShortDate(item.scannedAt)} · {observationAge(item.scannedAt)}</time></small>
                  </div>
                </div>
              </td>
              {showsDifficulty && (
                <td data-label="档位" className="tier-cell">
                  <span className={`difficulty-badge context-${item.itemContext ?? "unknown"}`}>
                    {item.difficulty || "未识别"}
                  </span>
                  {(item.statCombinationCount ?? 0) > 1 && <small className="tier-variants">{item.statCombinationCount} 种副属性组合</small>}
                </td>
              )}
              <td data-label="最低单价" className="number-cell"><div className="price-quote"><Money value={item.minUnitPrice} />
                {multiple && item.minUnitPrice != null && <small>{item.realm}</small>}</div></td>
              <td data-label="最低一口价" className="number-cell buyout-column"><div className="price-quote"><Money value={item.marketGroup?.minBuyout ?? item.minBuyout} />
                {multiple && item.marketGroup?.minBuyout != null && <small title={formatDate(item.marketGroup.buyoutScannedAt)}>{item.marketGroup.buyoutRealm}</small>}</div></td>
              <td data-label={grouped ? "挂单合计" : "挂单"} className="number-cell count">{formatInteger(item.marketGroup?.listingCount ?? item.listingCount)}</td>
              <td data-label={grouped ? "数量合计" : "总数量"} className="number-cell count">{formatInteger(item.marketGroup?.totalQuantity ?? item.totalQuantity)}</td>
              <td className="action-cell">
                <button type="button" disabled={isFetching} aria-expanded={showsQuotes ? open : undefined}
                  aria-controls={showsQuotes && open ? panelId : undefined} onClick={activate}>{actionLabel}</button>
              </td>
            </tr>
            {showsQuotes && open && <tr className="market-quotes-row"><td className="market-quotes-cell" colSpan={showsDifficulty ? 7 : 6}>
              <div id={panelId} className="inline-market-quotes"><RealmQuotePanel item={item} onSelect={onSelect} /></div>
            </td></tr>}</Fragment>;
          })}
        </tbody>
      </table>
    </div>
  );
}

function RealmQuotePanel({ item, onSelect }: { item: MarketItem; onSelect: (item: MarketItem) => void }) {
  const query = useQuery({
    queryKey: ["item-comparison", item.regionID, item.marketKey, item.variantFilters],
    queryFn: ({ signal }) => fetchItemComparison(item, signal), staleTime: 60_000, retry: false,
  });
  return <>
    <p className="quote-scope-note">各服采集时间可能不同；沿用当前装备筛选，点击报价查看该服挂单。</p>
    {query.isLoading && <p role="status">正在读取各服报价…</p>}
    {query.isError && <button type="button" className="equipment-filter-reset" onClick={() => query.refetch()}>报价读取失败，重试</button>}
    {query.data?.items.map(row => <button type="button" className="comparison-row" key={row.scanId} disabled={!row.listingCount} onClick={() => onSelect(row)}>
      <span>{row.realm}<small title={formatDate(row.scannedAt)}>{formatShortDate(row.scannedAt)}</small></span>
      <Money value={row.minUnitPrice} /><span>{row.listingCount ? `${formatInteger(row.listingCount)} 条挂单` : '无匹配挂单'}</span>
    </button>)}
  </>;
}

function MarketScopeBadge({ scope, realm }: { scope: MarketItem["marketScope"]; realm?: string | null }) {
  const label = scope === "region" ? "区域共享" : scope === "realm" ? `${realm || "本服"}报价` : "范围待确认";
  return <span className={`market-scope-badge scope-${scope}`}>{label}</span>;
}

function PanelState({
  kind,
  title,
  message,
  action,
  onAction,
}: {
  kind: "loading" | "error" | "empty";
  title: string;
  message: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className={`panel-state ${kind}`}>
      {kind === "loading" ? <span className="spinner" /> : <PackageSearch size={30} />}
      <strong>{title}</strong>
      <p>{message}</p>
      {action && <button type="button" onClick={onAction}>{action}</button>}
    </div>
  );
}

function Pagination({
  page,
  pages,
  pageSize,
  total,
  onChange,
}: {
  page: number;
  pages: number;
  pageSize: number;
  total: number;
  onChange: (page: number) => void;
}) {
  const tokens = useMemo(() => paginationTokens(page, pages), [page, pages]);
  const start = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);
  return (
    <nav className="pagination" aria-label="物品列表分页">
      <p>显示 {formatInteger(start)}–{formatInteger(end)}，共 {formatInteger(total)} 项</p>
      <div>
        <button type="button" disabled={page <= 1} onClick={() => onChange(page - 1)} aria-label="上一页"><ChevronLeft size={16} /></button>
        {tokens.map((token, index) =>
          token === "…" ? (
            <span key={`ellipsis-${index}`}>…</span>
          ) : (
            <button
              type="button"
              key={token}
              aria-current={token === page ? "page" : undefined}
              onClick={() => onChange(token)}
            >
              {token}
            </button>
          ),
        )}
        <button type="button" disabled={page >= pages} onClick={() => onChange(page + 1)} aria-label="下一页"><ChevronRight size={16} /></button>
      </div>
    </nav>
  );
}

function paginationTokens(current: number, pages: number): Array<number | "…"> {
  if (pages <= 7) return Array.from({ length: pages }, (_, index) => index + 1);
  const values = [...new Set([1, pages, current - 1, current, current + 1])]
    .filter((value) => value >= 1 && value <= pages)
    .sort((a, b) => a - b);
  const result: Array<number | "…"> = [];
  values.forEach((value, index) => {
    if (index > 0 && value - values[index - 1] > 1) result.push("…");
    result.push(value);
  });
  return result;
}

function ItemDetails({ item, onClose, onSelect }: { item: MarketItem; onClose: () => void; onSelect: (item: MarketItem) => void }) {
  const backdropPress = useRef(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [pageDraft, setPageDraft] = useState("1");
  const [historyOpen, setHistoryOpen] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const listingsHeadingRef = useRef<HTMLDivElement>(null);
  const [historyDays, setHistoryDays] = useState(0);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const comparisonQuery = useQuery({
    queryKey: ["item-comparison", item.regionID, item.marketKey, item.variantFilters],
    queryFn: ({ signal }) => fetchItemComparison(item, signal), enabled: comparisonOpen, staleTime: 60_000, retry: false,
  });
  const historyQuery = useQuery({
    queryKey: ["item-history", item.regionID, item.realmID, item.scanId, item.marketKey, item.variantFilters, historyDays],
    queryFn: ({ signal }) => fetchItemHistory(item, signal, historyDays ? item.scannedAtUnix - historyDays * 86400 : undefined),
    enabled: historyOpen,
    staleTime: 60_000,
    retry: false,
  });
  const listingsQuery = useQuery({
    queryKey: ["item-listings", item.marketKey, item.scanId, item.variantFilters, page, pageSize],
    queryFn: ({ signal }) => fetchItemListings(item, page, pageSize, signal),
    placeholderData: (previous) => previous,
    staleTime: 60_000,
    retry: false,
  });

  function changePage(nextPage: number) {
    setPage(nextPage);
    setPageDraft(String(nextPage));
    const content = contentRef.current;
    const heading = listingsHeadingRef.current;
    if (content && heading) {
      const top = heading.getBoundingClientRect().top - content.getBoundingClientRect().top + content.scrollTop;
      content.scrollTo({ top, behavior: "auto" });
    }
  }

  useEffect(() => {
    document.body.classList.add("modal-open");
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.classList.remove("modal-open");
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose]);

  const data = listingsQuery.data;
  const displayColumns = data?.displayColumns ?? {
    tier: Boolean(item.difficulty),
    attributes: false,
    petVariant: item.battlePetCreatureID != null,
  };
  const listingFeatureLabels = [
    displayColumns.tier ? "档位与装等" : null,
    displayColumns.attributes ? "属性组合" : null,
    displayColumns.petVariant ? "宠物 Breed、等级、品质与三围" : null,
  ].filter((label): label is string => Boolean(label));
  return (
    <div className="modal-backdrop" role="presentation"
      onPointerDown={event => { backdropPress.current = event.button === 0 && event.target === event.currentTarget; }}
      onPointerCancel={() => { backdropPress.current = false; }}
      onClick={event => {
        const startedOutside = backdropPress.current;
        backdropPress.current = false;
        if (startedOutside && event.target === event.currentTarget) onClose();
      }}>
      <section className="details-dialog" role="dialog" aria-modal="true" aria-labelledby="details-title">
        <header>
          <div className="item-identity">
            <WowheadLink item={item} className="wowhead-icon-link"><ItemGlyph item={item} /></WowheadLink>
            <div className="item-copy">
              <div className="details-title-line">
                <h2 id="details-title" className={qualityClass(item.quality)}><WowheadLink item={item}>{item.name}</WowheadLink></h2>
                <CraftingQuality quality={item.craftingQuality} />
              </div>
              <small>
                ID {item.itemID}
                {item.difficulty ? ` · ${item.difficulty}` : ""}
                {item.marketScope === "realm" && item.realm ? ` · ${item.realm}` : ""}
                {item.petVariantKey
                  ? ` · ${item.petBreedCode || "未知 Breed"}${item.petBreedLabel ? ` ${item.petBreedLabel}` : ""} · ${item.petLevel}级 · ${item.petQualityLabel || "未知品质"}`
                  : ""}
                {` · ${formatInteger(item.listingCount)} 条挂单`}
              </small>
              <div className="details-market-context" aria-label="当前挂单数据范围">
                <span><Database size={13} />{item.marketScope === "region" ? `${item.region || "区域"}共享市场` : item.realm || "服务器未知"}</span>
                <span><Clock3 size={13} />数据时间 {formatDate(item.scannedAt)}</span>
              </div>
            </div>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="关闭挂单详情"><X size={19} /></button>
        </header>

        <div className="details-content" ref={contentRef}>
          {item.variantFilters && <div className="equipment-detail-note">
            <strong>挂单筛选：{equipmentFilterLabels(item.variantFilters).join(" · ")}</strong>
          </div>}
          {item.marketScope === "realm" && <details className="history-disclosure" open={comparisonOpen} onToggle={event => setComparisonOpen(event.currentTarget.open)}>
            <summary>各服最新报价<ChevronRight size={16} aria-hidden="true" /></summary>
            {comparisonOpen && <div className="market-comparison">
              <p className="history-scope-note">同物品与档位，沿用当前属性筛选；未筛选的变体合并统计。各服采集时间可能不同。</p>
              {comparisonQuery.isLoading && <p role="status">正在读取各服报价…</p>}
              {comparisonQuery.isError && <button onClick={() => comparisonQuery.refetch()}>比价读取失败，重试</button>}
              {comparisonQuery.data?.items.map(row => <button className="comparison-row" key={row.scanId} disabled={!row.listingCount} onClick={() => onSelect(row)}>
                <span>{row.realm}<small>{formatShortDate(row.scannedAt)}</small></span>
                <Money value={row.minUnitPrice} /><span>{row.listingCount ? `${formatInteger(row.listingCount)} 条挂单` : '无匹配挂单'}</span>
              </button>)}
            </div>}
          </details>}
          <details className="history-disclosure" open={historyOpen} onToggle={event => setHistoryOpen(event.currentTarget.open)}>
            <summary>历史价格<ChevronRight size={16} aria-hidden="true" /></summary>
            {historyOpen && <>
              <div className="history-range-control"><Select label="历史时间范围" prefix="时间" appearance="filter" value={historyDays}
                options={[{ value: 0, label: "全部历史" }, { value: 7, label: "近 7 天" }, { value: 30, label: "近 30 天" }, { value: 90, label: "近 90 天" }]}
                onChange={setHistoryDays} /><span>截至当前快照</span></div>
              <p className="history-scope-note">{item.variantFilters ? "沿用当前装备筛选。" : "同物品与档位的全部变体。"}仅显示实际采集记录，无挂单或无一口价处断线；未采集时段行情未知。</p>
              <HistoryPanel data={historyQuery.data} isLoading={historyQuery.isLoading} error={historyQuery.error}
                onRetry={() => historyQuery.refetch()} />
            </>}
          </details>
          <div className="details-section-heading" ref={listingsHeadingRef}>
            <div>
              <h3>快照挂单</h3>
              <p>
                按{item.marketScope === "realm" && item.realm ? item.realm : "区域共享"}当前快照的单位价格排列
                {item.difficulty
                  ? ` · 仅显示${item.difficulty}档`
                  : listingFeatureLabels.length
                    ? `，并展示${listingFeatureLabels.join("、")}`
                    : ""}
              </p>
              <p className="listing-scroll-hint">左右滑动查看属性与更多信息</p>
            </div>
            <Select label="挂单每页条数" prefix="每页" value={pageSize} options={LISTING_PAGE_SIZE_OPTIONS}
              onChange={(size) => { setPageSize(size); changePage(1); }} />
          </div>
          {listingsQuery.isLoading && <PanelState kind="loading" title="正在加载挂单" message="读取该物品的原始拍卖记录…" />}
          {listingsQuery.error && <PanelState kind="error" title="无法加载挂单" message={listingsQuery.error.message} action="重试" onAction={() => listingsQuery.refetch()} />}
          {data && <div className={listingsQuery.isFetching ? "listing-results is-refreshing" : "listing-results"}
            aria-busy={listingsQuery.isFetching}>
            <ListingsTable listings={data.items} displayColumns={displayColumns} />
          </div>}
        </div>

        <footer className="listing-pagination" aria-label="挂单分页">
          <p role="status" aria-live="polite">{listingsQuery.isFetching ? `正在加载第 ${page} 页…`
            : data ? data.total ? `显示 ${formatInteger((data.page - 1) * data.pageSize + 1)}–${formatInteger((data.page - 1) * data.pageSize + data.items.length)}，共 ${formatInteger(data.total)} 条`
              : "没有匹配挂单" : "正在读取…"}</p>
          <div className="listing-pagination-controls">
            <div className="listing-page-buttons">
              <button type="button" disabled={!data || listingsQuery.isFetching || page <= 1} onClick={() => changePage(page - 1)}><ChevronLeft size={15} /> 上一页</button>
              <span>{data ? `${data.page} / ${Math.max(1, data.totalPages)}` : "—"}</span>
              <button type="button" disabled={!data || listingsQuery.isFetching || page >= data.totalPages} onClick={() => changePage(page + 1)}>下一页 <ChevronRight size={15} /></button>
            </div>
            <form className="listing-page-jump" onSubmit={(event) => {
              event.preventDefault();
              const target = Number(pageDraft);
              if (data && Number.isInteger(target) && target >= 1 && target <= data.totalPages && !listingsQuery.isFetching) changePage(target);
            }}>
              <label htmlFor="listing-page-jump">跳至</label>
              <input id="listing-page-jump" type="number" inputMode="numeric" min={1} max={Math.max(1, data?.totalPages ?? 1)} step={1}
                value={pageDraft} onChange={event => setPageDraft(event.target.value)} aria-label="挂单页码"
                disabled={!data?.total || listingsQuery.isFetching} required />
              <button type="submit" disabled={!data?.total || listingsQuery.isFetching}>跳转</button>
            </form>
          </div>
        </footer>
      </section>
    </div>
  );
}

function HistoryPanel({
  data,
  isLoading,
  error,
  onRetry,
}: {
  data?: ItemHistoryResponse;
  isLoading: boolean;
  error: Error | null;
  onRetry: () => void;
}) {
  if (isLoading) {
    return <div className="history-loading"><span className="spinner" /><span>正在读取各服务器的历史行情…</span></div>;
  }
  if (error) {
    return (
      <div className="history-error">
        <div><strong>历史行情读取失败</strong><p>{error.message}</p></div>
        <button type="button" onClick={onRetry}>重试</button>
      </div>
    );
  }
  if (!data) return null;
  if (!data.points.length) return <p className="history-scope-note">该时间范围没有采集记录，请扩大时间范围。</p>;
  const realmSeries = groupRealmHistory(data);
  const isRealmMarket = data.marketScope === "realm";

  return (
    <section className="history-panel" aria-labelledby="history-title">
      <div className="history-heading">
        <div>
          <p className="eyebrow">PRICE HISTORY</p>
          <h3 id="history-title">最低单价趋势</h3>
          <span>
            {data.pointCount} 个时间点
            {isRealmMarket ? ` · ${realmSeries.length} 个服务器` : " · 区域共享"}
            {` · ${formatShortDate(data.points[0].scannedAt)} 至 ${formatShortDate(data.points.at(-1)?.scannedAt)}`}
          </span>
        </div>
        {!isRealmMarket && <ChangeBadge change={data.change.minUnitPrice} />}
      </div>

      <div className="history-layout">
        <PriceChart data={data} />
        {isRealmMarket ? (
          <div className="history-metrics realm-history-metrics">
            {realmSeries.map((series) => {
              const first = series.points[0];
              const latest = series.points.at(-1)!;
              const change = first.minUnitPrice != null && latest.minUnitPrice != null
                ? {
                    previous: first.minUnitPrice,
                    current: latest.minUnitPrice,
                    absolute: latest.minUnitPrice - first.minUnitPrice,
                    percent: first.minUnitPrice === 0 ? null : ((latest.minUnitPrice - first.minUnitPrice) / first.minUnitPrice) * 100,
                  }
                : null;
              return <HistoryMetric key={series.key} label={series.name} change={change} format="money" />;
            })}
          </div>
        ) : (
          <div className="history-metrics">
            <HistoryMetric label="最低单价" change={data.change.minUnitPrice} format="money" />
            <HistoryMetric label="库存变化" change={data.change.totalQuantity} format="number" />
            <HistoryMetric label="挂单变化" change={data.change.listingCount} format="number" />
          </div>
        )}
      </div>
    </section>
  );
}

const CHART_COLORS = ["#c58b2a", "#2e8b70", "#5d6fc2", "#a35c82", "#cc6b3d", "#54869c"];

function groupRealmHistory(data: ItemHistoryResponse) {
  const groups = new Map<string, { key: string; name: string; points: ItemHistoryResponse["points"] }>();
  data.points.forEach((point) => {
    const key = `${point.regionID ?? "?"}:${point.realmID ?? point.realm ?? "?"}`;
    const current = groups.get(key) ?? { key, name: point.realm || `服务器 ${point.realmID ?? "未知"}`, points: [] };
    current.points.push(point);
    groups.set(key, current);
  });
  return [...groups.values()].map((group) => ({
    ...group,
    points: group.points.sort((left, right) => left.scannedAtUnix - right.scannedAtUnix),
  }));
}

function PriceChart({ data }: { data: ItemHistoryResponse }) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [chartWidth, setChartWidth] = useState(640);
  useEffect(() => {
    const element = chartRef.current;
    if (!element) return;
    const resize = () => setChartWidth(Math.max(280, Math.min(640, element.clientWidth)));
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    return () => observer.disconnect();
  }, [data.points.length]);
  const priced = data.points.filter(
    (point): point is typeof point & { minUnitPrice: number } => point.minUnitPrice != null,
  );
  if (!priced.length) return <div className="chart-empty">这几份快照没有可用的一口价。</div>;

  const prices = priced.map((point) => point.minUnitPrice);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const padding = Math.max(1, (max - min) * 0.2, max * 0.025);
  const low = Math.max(0, min - padding);
  const high = max + padding;
  const range = Math.max(1, high - low);
  const left = 46;
  const right = chartWidth - 46;
  const top = 23;
  const bottom = 132;
  const minTime = Math.min(...priced.map((point) => point.scannedAtUnix));
  const maxTime = Math.max(...priced.map((point) => point.scannedAtUnix));
  const timeRange = Math.max(1, maxTime - minTime);
  const segments = new Map<number, number>();
  let segment = 0;
  for (const point of data.points) {
    if (point.breakBefore || point.minUnitPrice == null) segment++;
    segments.set(point.scanId, segment);
  }
  const coordinates = priced.map((point, index) => ({
    x: minTime === maxTime ? (left + right) / 2 : left + ((point.scannedAtUnix - minTime) / timeRange) * (right - left),
    y: bottom - ((point.minUnitPrice - low) / range) * (bottom - top),
    point,
    segment: segments.get(point.scanId),
    showLabel: index % Math.max(1, Math.ceil(priced.length / 5)) === 0 || index === priced.length - 1,
  }));
  const groupKey = (coordinate: (typeof coordinates)[number]) => `${data.marketScope === "realm"
    ? `${coordinate.point.regionID ?? "?"}:${coordinate.point.realmID ?? coordinate.point.realm ?? "?"}`
    : "region"}:${coordinate.segment}`;
  const groups = new Map<string, typeof coordinates>();
  coordinates.forEach((coordinate) => {
    const key = groupKey(coordinate);
    groups.set(key, [...(groups.get(key) ?? []), coordinate]);
  });
  const series = [...groups.entries()].map(([key, values], index) => ({
    key,
    name: values[0].point.realm || "区域共享",
    color: CHART_COLORS[Math.max(0, groupRealmHistory(data).findIndex(g => g.name === (values[0].point.realm || '区域共享'))) % CHART_COLORS.length],
    coordinates: values.sort((leftValue, rightValue) => leftValue.point.scannedAtUnix - rightValue.point.scannedAtUnix),
  }));

  return (
    <div className="price-chart" ref={chartRef}>
      <svg viewBox={`0 0 ${chartWidth} 168`} role="img" aria-label="按时间排列的最低单价折线图">
        <defs>
          <linearGradient id="price-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="currentColor" stopOpacity="0.24" />
            <stop offset="1" stopColor="currentColor" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[top, (top + bottom) / 2, bottom].map((y) => <line key={y} className="chart-grid" x1={left} x2={right} y1={y} y2={y} />)}
        {series.map((entry) => {
          const line = entry.coordinates.map(({ x, y }, index) => `${index === 0 ? "M" : "L"} ${x} ${y}`).join(" ");
          const area = entry.key.startsWith("region:") && entry.coordinates.length > 1
            ? `${line} L ${entry.coordinates.at(-1)!.x} ${bottom} L ${entry.coordinates[0].x} ${bottom} Z`
            : null;
          return (
            <g key={entry.key}>
              {area && <path className="chart-area" d={area} fill="url(#price-area)" />}
              {entry.coordinates.length > 1 && <path className="chart-line" d={line} style={{ stroke: entry.color }} />}
              {entry.coordinates.map(({ x, y, point, showLabel }) => (
                <g key={point.scanId}>
                  <circle className="chart-halo" cx={x} cy={y} r="8" style={{ color: entry.color }} />
                  <circle className="chart-dot" cx={x} cy={y} r="4" style={{ fill: entry.color }} />
                  {showLabel && <>
                    <text className="chart-value" x={x} y={Math.max(14, y - 13)} textAnchor="middle">{compactCopper(point.minUnitPrice)}</text>
                    <text className="chart-time" x={x} y="157" textAnchor="middle">{formatShortDate(point.scannedAt)}</text>
                  </>}
                </g>
              ))}
            </g>
          );
        })}
      </svg>
      {data.marketScope === "realm" && (
        <div className="chart-legend">
          {groupRealmHistory(data).map((entry, index) => (
            <span key={entry.key}><i style={{ background: CHART_COLORS[index % CHART_COLORS.length] }} />{entry.name}</span>
          ))}
        </div>
      )}
    </div>
  );
}

function HistoryMetric({
  label,
  change,
  format,
}: {
  label: string;
  change: HistoryMetricChange | null;
  format: "money" | "number";
}) {
  if (!change) return <div className="history-metric"><span>{label}</span><strong>暂无数据</strong></div>;
  const isUp = change.absolute > 0;
  const isDown = change.absolute < 0;
  return (
    <div className="history-metric">
      <span>{label}</span>
      <strong>{format === "money" ? <Money value={change.current} /> : formatInteger(change.current)}</strong>
      <small className={isUp ? "up" : isDown ? "down" : "flat"}>
        {signedPercent(change.percent)}
        <em>前值 {format === "money" ? compactCopper(change.previous) : formatInteger(change.previous)}</em>
      </small>
    </div>
  );
}

function ChangeBadge({ change }: { change: HistoryMetricChange | null }) {
  if (!change) return <span className="change-badge flat">暂无对比</span>;
  const direction = change.absolute > 0 ? "up" : change.absolute < 0 ? "down" : "flat";
  return <span className={`change-badge ${direction}`}>{signedPercent(change.percent)}</span>;
}

function signedPercent(value: number | null): string {
  if (value == null) return "—";
  if (value === 0) return "0.00%";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function compactCopper(value: number): string {
  const parts = splitCopper(value);
  if (!parts) return "—";
  if (parts.gold > 0) return `${formatInteger(parts.gold)}金`;
  if (parts.silver > 0) return `${parts.silver}银`;
  return `${parts.copper}铜`;
}

function ListingsTable({
  listings,
  displayColumns,
}: {
  listings: AuctionListing[];
  displayColumns: { tier: boolean; attributes: boolean; petVariant: boolean };
}) {
  if (!listings.length) return <PanelState kind="empty" title="没有挂单记录" message="当前快照中没有可显示的数据。" />;
  const variantColumnCount = Number(displayColumns.tier) + Number(displayColumns.attributes) + Number(displayColumns.petVariant);
  return (
    <div className="table-wrap">
      <table className={`listing-table variants-${variantColumnCount}`}>
        <thead><tr>
          <th>数量</th>
          <th className="number-cell listing-unit-price">单位价格</th>
          {displayColumns.tier && <th>档位</th>}
          {displayColumns.attributes && <th>属性组合</th>}
          {displayColumns.petVariant && <th>宠物变体</th>}
          <th className="number-cell">一口价</th>
          <th className="number-cell">竞价 / 起拍</th>
          <th>剩余时间</th>
        </tr></thead>
        <tbody>
          {listings.map((listing, index) => {
            const tierDetails = [
              listing.itemLevel ? `装等 ${listing.itemLevel}` : null,
              listing.upgradeTrack
                ? `${listing.upgradeTrack}${listing.upgradeLevel ? ` ${listing.upgradeLevel}` : ""}`
                : null,
            ].filter((detail): detail is string => Boolean(detail));
            return <tr key={`${listing.itemID}-${index}`}>
              <td className="count">{formatInteger(listing.quantity)}</td>
              <td className="number-cell listing-unit-price"><Money value={listing.unitPrice} /></td>
              {displayColumns.tier && <td>
                <div className="variant-tier">
                  <WowheadLink item={listing}><strong>{listing.difficulty || "未标注档位"}</strong></WowheadLink>
                  {tierDetails.length > 0 && <small>{tierDetails.join(" · ")}</small>}
                </div>
              </td>}
              {displayColumns.attributes && <td>
                <div className="variant-stats">
                  <strong>{listing.statLabel || (listing.hasSocket ? "棱彩插槽" : "—")}</strong>
                  {listing.statLabel && listing.hasSocket && <small>棱彩插槽</small>}
                  {!!listing.tertiaryStats?.length && <small>{listing.tertiaryStats.map(stat => TERTIARY_LABELS[stat] || stat).join(" · ")}</small>}
                </div>
              </td>}
              {displayColumns.petVariant && <td>
                <div className="pet-variant">
                  <strong>
                    {listing.petBreedCode || "Breed 待识别"}
                    {listing.petBreedLabel ? ` · ${listing.petBreedLabel}` : ""}
                  </strong>
                  <small>
                    {listing.petLevel != null ? `${listing.petLevel}级` : "等级未知"}
                    {listing.petQualityLabel ? ` · ${listing.petQualityLabel}` : ""}
                  </small>
                  <small className="pet-stats">
                    生命 {listing.petHealth ?? "—"} · 攻击 {listing.petPower ?? "—"} · 速度 {listing.petSpeed ?? "—"}
                  </small>
                </div>
              </td>}
              <td className="number-cell"><Money value={listing.buyoutAmount} /></td>
              <td className="number-cell"><Money value={listing.bidAmount || listing.minBid} /></td>
              <td>{listing.timeLeftLabel || "未知"}</td>
            </tr>;
          })}
        </tbody>
      </table>
    </div>
  );
}
