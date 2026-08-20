import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Crown,
  Database,
  Moon,
  PackageSearch,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sun,
  TrendingUp,
  UserRound,
  X,
} from "lucide-react";
import { fetchItemHistory, fetchItemListings, fetchMarketCatalog, fetchMarketItems, fetchMarketStatus, getIconProxyUrl } from "./lib/api";
import iconMap from "./generated/icon-map.json";
import localIconMap from "./generated/local-icon-map.json";
import {
  formatDate,
  formatInteger,
  formatRelativeTime,
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
    q: params.get("q")?.trim() ?? "",
    collection,
    page: Math.max(1, Number(params.get("page")) || 1),
    pageSize,
    sort,
    scanId: Number(params.get("scan_id")) || null,
  };
}

export function App() {
  const queryClient = useQueryClient();
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const saved = localStorage.getItem("wow-auction-theme");
    if (saved === "light" || saved === "dark") return saved;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
  const [query, setQuery] = useState<MarketQuery>(initialQuery);
  const [searchDraft, setSearchDraft] = useState(query.q);
  const [selectedItem, setSelectedItem] = useState<MarketItem | null>(null);
  const [showAccountNotice, setShowAccountNotice] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("wow-auction-theme", theme);
  }, [theme]);

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
    if (query.collection) params.set("collection", query.collection);
    if (query.page > 1) params.set("page", String(query.page));
    if (query.pageSize !== 20) params.set("page_size", String(query.pageSize));
    if (query.sort !== "price_asc") params.set("sort", query.sort);
    if (query.scanId) params.set("scan_id", String(query.scanId));
    const suffix = params.size ? `?${params.toString()}` : window.location.pathname;
    window.history.replaceState(null, "", suffix);
  }, [query]);

  const catalogQuery = useQuery({
    queryKey: ["market-catalog"],
    queryFn: fetchMarketCatalog,
  });
  const statusQuery = useQuery({
    queryKey: ["market-status", query.scanId],
    queryFn: () => fetchMarketStatus(query.scanId),
    enabled: query.scanId != null,
  });
  const itemsQuery = useQuery({
    queryKey: ["market-items", query],
    queryFn: () => fetchMarketItems(query),
    placeholderData: (previous) => previous,
  });
  const popularQuery = useQuery({
    queryKey: ["market-items", "popular", query.scanId],
    queryFn: () =>
      fetchMarketItems({ q: "", collection: "", page: 1, pageSize: 3, sort: "quantity_desc", scanId: query.scanId }),
    enabled: query.scanId != null,
  });
  const raidBoeQuery = useQuery({
    queryKey: ["market-items", "raid-boe-12-1", query.scanId],
    queryFn: () =>
      fetchMarketItems({ q: "", collection: "raid_boe_12_1", page: 1, pageSize: 20, sort: "price_asc", scanId: query.scanId }),
    enabled: query.scanId != null,
  });

  useEffect(() => {
    const realms = catalogQuery.data?.realms ?? [];
    if (!realms.length) return;
    const scanExists = realms.some((realm) => realm.scans.some((scan) => scan.scanId === query.scanId));
    if (!scanExists) setQuery((current) => ({ ...current, scanId: realms[0].latestScanId, page: 1 }));
  }, [catalogQuery.data, query.scanId]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["market-status"] }),
      queryClient.invalidateQueries({ queryKey: ["market-items"] }),
      queryClient.invalidateQueries({ queryKey: ["market-catalog"] }),
    ]);
  };

  const status = statusQuery.data;
  const data = itemsQuery.data;
  const realms = catalogQuery.data?.realms ?? [];
  const selectedRealm = realms.find((realm) => realm.scans.some((scan) => scan.scanId === query.scanId));

  return (
    <div className="app-shell">
      <header className="site-header">
        <div className="header-inner page-width">
          <a className="brand" href="./" aria-label="艾泽拉斯交易所首页">
            <span className="brand-mark" aria-hidden="true">W</span>
            <span className="brand-copy">
              <strong>艾泽拉斯交易所</strong>
              <small>AZEROTH EXCHANGE</small>
            </span>
          </a>

          <label className="header-search">
            <Search size={19} aria-hidden="true" />
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
            <button
              className="icon-button"
              type="button"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              aria-label={theme === "dark" ? "切换浅色主题" : "切换深色主题"}
            >
              {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <button className="account-button" type="button" onClick={() => setShowAccountNotice(true)}>
              <UserRound size={17} />
              <span>账户</span>
            </button>
          </div>
        </div>
      </header>

      <main className="page-width main-content">
        <section className="intro-row">
          <div>
            <p className="eyebrow">REAL MARKET SNAPSHOT</p>
            <h1>看懂艾泽拉斯的真实行情</h1>
            <p className="intro-copy">
              从游戏内完整快照中搜索价格、库存和每一条挂单。数据保留原始记录，并通过完整性校验。
            </p>
          </div>
          <SnapshotBadge
            isLoading={statusQuery.isLoading}
            isError={statusQuery.isError}
            available={status?.available}
            complete={status?.complete}
          />
        </section>

        <section className="snapshot-selector" aria-label="选择服务器和快照时间">
          <label>
            <span>服务器</span>
            <select
              value={selectedRealm?.key ?? ""}
              disabled={catalogQuery.isLoading || realms.length === 0}
              onChange={(event) => {
                const realm = realms.find((candidate) => candidate.key === event.target.value);
                if (realm) {
                  setSelectedItem(null);
                  setQuery((current) => ({ ...current, scanId: realm.latestScanId, page: 1 }));
                }
              }}
            >
              {realms.map((realm) => (
                <option key={realm.key} value={realm.key}>{realm.realm} · {realm.region || `区域 ${realm.regionID}`}</option>
              ))}
            </select>
          </label>
          <label>
            <span>数据时间点</span>
            <select
              value={query.scanId ?? ""}
              disabled={!selectedRealm}
              onChange={(event) => {
                setSelectedItem(null);
                setQuery((current) => ({ ...current, scanId: Number(event.target.value), page: 1 }));
              }}
            >
              {selectedRealm?.scans.map((scan) => (
                <option key={scan.scanId} value={scan.scanId}>
                  {formatDate(scan.scannedAt)} · {formatInteger(scan.listingCount)} 条
                </option>
              ))}
            </select>
          </label>
          <p>价格、库存排行和挂单详情均来自当前服务器的所选快照。</p>
        </section>

        <section className="metric-grid" aria-label="市场快照摘要">
          <MetricCard
            icon={<Clock3 size={18} />}
            label="快照时间"
            value={statusQuery.isLoading ? "读取中…" : formatDate(status?.scannedAt)}
            detail={formatRelativeTime(status?.scannedAt)}
          />
          <MetricCard
            icon={<Database size={18} />}
            label="服务器 / 区域"
            value={statusQuery.isLoading ? "读取中…" : status?.realm || "未标注"}
            detail={status?.region ? `${status.region} 区拍卖数据` : "等待快照元数据"}
          />
          <MetricCard
            icon={<PackageSearch size={18} />}
            label="市场物品"
            value={statusQuery.isLoading ? "读取中…" : formatInteger(status?.marketItemCount)}
            detail={`${formatInteger(status?.listingCount)} 条原始挂单`}
          />
          <MetricCard
            icon={<Boxes size={18} />}
            label="商品总量"
            value={statusQuery.isLoading ? "读取中…" : formatInteger(status?.totalQuantity)}
            detail={`${formatInteger(status?.uniqueItemCount)} 个基础物品 ID`}
          />
        </section>

        <div className="workspace-grid">
          <aside className="market-sidebar">
            <button
              className={`sidebar-card raid-boe-entry ${query.collection === "raid_boe_12_1" ? "active" : ""}`}
              type="button"
              onClick={() => {
                const collection = query.collection === "raid_boe_12_1" ? "" : "raid_boe_12_1";
                setSearchDraft("");
                setQuery((current) => ({ ...current, q: "", collection, page: 1, sort: "price_asc" }));
                window.setTimeout(() => document.querySelector(".market-panel")?.scrollIntoView({ behavior: "smooth" }), 0);
              }}
            >
              <span className="raid-boe-icon"><Crown size={20} /></span>
              <span>
                <small>快捷行情</small>
                <strong>12.1 团本装绑</strong>
                <em>剧毒深渊 · 按难度与变体看价</em>
              </span>
              <b>
                {raidBoeQuery.isLoading
                  ? "读取中"
                  : `${raidBoeQuery.data?.total ?? 0}/9 种 · ${formatInteger(raidBoeQuery.data?.items.reduce((sum, item) => sum + item.listingCount, 0))} 条`}
              </b>
            </button>

            <section className="sidebar-card">
              <div className="card-heading compact">
                <span className="heading-icon"><TrendingUp size={17} /></span>
                <div>
                  <h2>库存热门</h2>
                  <p>当前快照数量排行</p>
                </div>
              </div>
              <ol className="popular-list">
                {popularQuery.isLoading && <li className="sidebar-placeholder">正在计算排行…</li>}
                {popularQuery.data?.items.map((item, index) => (
                  <li key={item.marketKey}>
                    <span className="rank">{index + 1}</span>
                    <ItemGlyph item={item} size="small" />
                    <button type="button" onClick={() => setSelectedItem(item)}>
                      <strong className={qualityClass(item.quality)}>{item.name}</strong>
                      <small>{formatInteger(item.totalQuantity)} 件</small>
                    </button>
                  </li>
                ))}
              </ol>
            </section>

            <section className="sidebar-card trust-card">
              <ShieldCheck size={21} />
              <div>
                <strong>完整快照校验</strong>
                <p>原始记录、聚合数量与价格字段均在导入时闭环校验。</p>
              </div>
            </section>
          </aside>

          <section className="market-panel" aria-labelledby="market-title">
            <div className="market-toolbar">
              <div className="card-heading">
                <div>
                  <h2 id="market-title">{query.collection === "raid_boe_12_1" ? "12.1 团本装绑" : "物品市场"}</h2>
                  <p>
                    {query.collection === "raid_boe_12_1" ? "剧毒深渊 · " : ""}
                    {query.q ? `“${query.q}” · ` : ""}
                    {itemsQuery.isLoading ? "正在读取…" : `共 ${formatInteger(data?.total)} 个市场条目`}
                  </p>
                </div>
              </div>
              <div className="market-controls">
                <label>
                  <SlidersHorizontal size={15} />
                  <span className="sr-only">排序方式</span>
                  <select
                    value={query.sort}
                    onChange={(event) =>
                      setQuery((current) => ({
                        ...current,
                        sort: event.target.value as MarketSort,
                        page: 1,
                      }))
                    }
                  >
                    {SORT_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>每页</span>
                  <select
                    value={query.pageSize}
                    onChange={(event) =>
                      setQuery((current) => ({
                        ...current,
                        pageSize: Number(event.target.value),
                        page: 1,
                      }))
                    }
                  >
                    {PAGE_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}
                  </select>
                </label>
              </div>
            </div>

            <MarketTable
              items={data?.items ?? []}
              isLoading={itemsQuery.isLoading}
              isFetching={itemsQuery.isFetching}
              error={itemsQuery.error}
              hasSearch={Boolean(query.q || query.collection)}
              onSelect={setSelectedItem}
              onRetry={() => itemsQuery.refetch()}
              onClearSearch={() => {
                setSearchDraft("");
                setQuery((current) => ({ ...current, q: "", collection: "", page: 1 }));
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
        </div>
      </main>

      <footer className="site-footer">
        <div className="page-width">
          <span>艾泽拉斯交易所</span>
          <p>价格以铜币为基础单位，实际市场可能在快照生成后发生变化。</p>
        </div>
      </footer>

      {selectedItem && <ItemDetails item={selectedItem} scanId={query.scanId} onClose={() => setSelectedItem(null)} />}
      {showAccountNotice && <AccountNotice onClose={() => setShowAccountNotice(false)} />}
    </div>
  );
}

function AccountNotice({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="notice-dialog" role="dialog" aria-modal="true" aria-labelledby="account-notice-title">
        <button className="icon-button" type="button" onClick={onClose} aria-label="关闭账户说明"><X size={18} /></button>
        <span className="notice-icon"><UserRound size={23} /></span>
        <p className="eyebrow">ACCOUNT MIGRATION</p>
        <h2 id="account-notice-title">账户功能正在迁移</h2>
        <p>市场浏览保持公开可用。CloudBase 登录、收藏和关注列表会在 React 基础页面稳定后接入。</p>
        <button className="notice-action" type="button" onClick={onClose}>知道了</button>
      </section>
    </div>
  );
}

function SnapshotBadge({
  isLoading,
  isError,
  available,
  complete,
}: {
  isLoading: boolean;
  isError: boolean;
  available?: boolean;
  complete?: boolean;
}) {
  let state = "ready";
  let text = "市场快照可用";
  if (isLoading) {
    state = "loading";
    text = "正在读取快照";
  } else if (isError) {
    state = "error";
    text = "快照状态读取失败";
  } else if (!available) {
    state = "error";
    text = "暂无可用快照";
  } else if (!complete) {
    state = "warning";
    text = "完整性校验未通过";
  }
  return (
    <div className={`snapshot-badge ${state}`} role="status">
      <span />
      {text}
    </div>
  );
}

function MetricCard({
  icon,
  label,
  value,
  detail,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <article className="metric-card">
      <span className="metric-icon">{icon}</span>
      <div>
        <span className="metric-label">{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
    </article>
  );
}

function ItemGlyph({ item, size }: { item: MarketItem; size?: "small" }) {
  const glyph = Array.from(item.name.trim())[0] || "?";
  const iconName = item.texture
    ? (iconMap as Record<string, string>)[String(item.texture)]
    : undefined;
  const cdnIconName = iconName && !iconName.startsWith("filedata-") ? iconName : undefined;
  const iconUrl = cdnIconName ? `${ICON_CDN_BASE}/${cdnIconName}.jpg` : undefined;
  const localIconExtension = iconName
    ? (localIconMap as Record<string, string>)[iconName]
    : undefined;
  const localIconUrl = iconName && localIconExtension
    ? `${import.meta.env.BASE_URL}wow-icons/${iconName}.${localIconExtension}`
    : undefined;
  const proxyIconUrl = cdnIconName ? getIconProxyUrl(cdnIconName) : undefined;
  const preferredIconUrl = localIconUrl ?? iconUrl;
  const [imageSource, setImageSource] = useState(preferredIconUrl);

  useEffect(() => setImageSource(preferredIconUrl), [preferredIconUrl]);

  return (
    <span
      className={`item-glyph ${qualityClass(item.quality)} ${size === "small" ? "small" : ""} ${imageSource ? "has-image" : "is-fallback"}`}
      aria-hidden="true"
      title={iconName ? `${item.name} · ${iconName}` : item.name}
    >
      {imageSource ? (
        <img
          src={imageSource}
          alt=""
          loading={size === "small" ? "eager" : "lazy"}
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setImageSource((current) => {
            if (current === localIconUrl) return iconUrl ?? proxyIconUrl;
            if (current === iconUrl) return localIconUrl ?? proxyIconUrl;
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
  onSelect,
  onRetry,
  onClearSearch,
}: {
  items: MarketItem[];
  isLoading: boolean;
  isFetching: boolean;
  error: Error | null;
  hasSearch: boolean;
  onSelect: (item: MarketItem) => void;
  onRetry: () => void;
  onClearSearch: () => void;
}) {
  if (isLoading) return <PanelState kind="loading" title="正在加载市场数据" message="正在读取最新快照中的物品…" />;
  if (error) return <PanelState kind="error" title="无法加载物品列表" message={error.message} action="重新加载" onAction={onRetry} />;
  if (!items.length) {
    return (
      <PanelState
        kind="empty"
        title={hasSearch ? "没有找到匹配物品" : "快照中没有可显示的物品"}
        message={hasSearch ? "试试缩短关键词，或者直接输入物品 ID。" : "请先导入一份完整拍卖行快照。"}
        action={hasSearch ? "清除搜索" : undefined}
        onAction={onClearSearch}
      />
    );
  }
  const showsDifficulty = items.some((item) => item.difficulty);
  return (
    <div className={`table-wrap ${isFetching ? "is-refreshing" : ""}`}>
      <table className="market-table">
        <thead>
          <tr>
            <th>物品</th>
            {showsDifficulty && <th>档位</th>}
            <th className="number-cell">最低单价</th>
            <th className="number-cell">最低一口价</th>
            <th className="number-cell">挂单</th>
            <th className="number-cell">总数量</th>
            <th><span className="sr-only">操作</span></th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.marketKey}>
              <td data-label="物品">
                <div className="item-identity">
                  <ItemGlyph item={item} />
                  <div className="item-copy">
                    <div className="item-title-line">
                      <strong className={qualityClass(item.quality)}>{item.name}</strong>
                      <CraftingQuality quality={item.craftingQuality} />
                      <MarketScopeBadge scope={item.marketScope} />
                    </div>
                    <small>
                      ID {item.itemID}
                      {item.battlePetCreatureID ? ` · 战宠 ${item.battlePetCreatureID}` : ""}
                      {item.petVariantKey
                        ? ` · ${item.petBreedCode || "未知 Breed"}${item.petBreedLabel ? ` ${item.petBreedLabel}` : ""} · ${item.petLevel}级 · ${item.petQualityLabel || "未知品质"}`
                        : !item.difficulty && item.variantCount > 1 ? ` · ${item.variantCount} 种变体` : ""}
                    </small>
                  </div>
                </div>
              </td>
              {showsDifficulty && (
                <td data-label="档位">
                  <span className={`difficulty-badge context-${item.itemContext ?? "unknown"}`}>
                    {item.difficulty || "未识别"}
                  </span>
                  {item.variantCount > 1 && <small className="tier-variants">{item.variantCount} 种属性变体</small>}
                </td>
              )}
              <td data-label="最低单价" className="number-cell"><Money value={item.minUnitPrice} /></td>
              <td data-label="最低一口价" className="number-cell"><Money value={item.minBuyout} /></td>
              <td data-label="挂单" className="number-cell count">{formatInteger(item.listingCount)}</td>
              <td data-label="总数量" className="number-cell count">{formatInteger(item.totalQuantity)}</td>
              <td className="action-cell">
                <button type="button" onClick={() => onSelect(item)}>查看挂单</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MarketScopeBadge({ scope }: { scope: MarketItem["marketScope"] }) {
  const label = scope === "region" ? "区域共享" : scope === "realm" ? "服务器独有" : "范围待确认";
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

function ItemDetails({ item, scanId, onClose }: { item: MarketItem; scanId: number | null; onClose: () => void }) {
  const [page, setPage] = useState(1);
  const historyQuery = useQuery({
    queryKey: ["item-history", item.marketKey, scanId],
    queryFn: () => fetchItemHistory(item, scanId),
  });
  const listingsQuery = useQuery({
    queryKey: ["item-listings", item.marketKey, scanId, page],
    queryFn: () => fetchItemListings(item, page, 25, scanId),
  });

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
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="details-dialog" role="dialog" aria-modal="true" aria-labelledby="details-title">
        <header>
          <div className="item-identity">
            <ItemGlyph item={item} />
            <div className="item-copy">
              <p className="eyebrow">AUCTION LISTINGS</p>
              <div className="details-title-line">
                <h2 id="details-title" className={qualityClass(item.quality)}>{item.name}</h2>
                <CraftingQuality quality={item.craftingQuality} />
              </div>
              <small>
                ID {item.itemID}
                {item.difficulty ? ` · ${item.difficulty}` : ""}
                {item.petVariantKey
                  ? ` · ${item.petBreedCode || "未知 Breed"}${item.petBreedLabel ? ` ${item.petBreedLabel}` : ""} · ${item.petLevel}级 · ${item.petQualityLabel || "未知品质"}`
                  : ""}
                {` · ${formatInteger(item.listingCount)} 条挂单`}
              </small>
            </div>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="关闭挂单详情"><X size={19} /></button>
        </header>

        <div className="details-content">
          <HistoryPanel
            data={historyQuery.data}
            isLoading={historyQuery.isLoading}
            error={historyQuery.error}
            onRetry={() => historyQuery.refetch()}
          />
          <div className="details-section-heading">
            <div>
              <h3>最新挂单</h3>
              <p>
                按当前快照的单位价格排列
                {item.difficulty
                  ? ` · 仅显示${item.difficulty}档`
                  : listingFeatureLabels.length
                    ? `，并展示${listingFeatureLabels.join("、")}`
                    : ""}
              </p>
            </div>
            <span>{formatInteger(item.listingCount)} 条</span>
          </div>
          {listingsQuery.isLoading && <PanelState kind="loading" title="正在加载挂单" message="读取该物品的原始拍卖记录…" />}
          {listingsQuery.error && <PanelState kind="error" title="无法加载挂单" message={listingsQuery.error.message} action="重试" onAction={() => listingsQuery.refetch()} />}
          {data && <ListingsTable listings={data.items} displayColumns={displayColumns} />}
        </div>

        <footer>
          <p>{data ? `共 ${formatInteger(data.total)} 条挂单` : "正在读取…"}</p>
          <div>
            <button type="button" disabled={!data || page <= 1} onClick={() => setPage((value) => value - 1)}><ChevronLeft size={15} /> 上一页</button>
            <span>{data ? `${page} / ${data.totalPages}` : "—"}</span>
            <button type="button" disabled={!data || page >= data.totalPages} onClick={() => setPage((value) => value + 1)}>下一页 <ChevronRight size={15} /></button>
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
    return <div className="history-loading"><span className="spinner" /><span>正在计算两份快照的行情变化…</span></div>;
  }
  if (error) {
    return (
      <div className="history-error">
        <div><strong>历史行情读取失败</strong><p>{error.message}</p></div>
        <button type="button" onClick={onRetry}>重试</button>
      </div>
    );
  }
  if (!data || !data.points.length) return null;

  return (
    <section className="history-panel" aria-labelledby="history-title">
      <div className="history-heading">
        <div>
          <p className="eyebrow">PRICE HISTORY</p>
          <h3 id="history-title">最低单价趋势</h3>
          <span>{data.pointCount} 份完整快照 · {formatShortDate(data.points[0].scannedAt)} 至 {formatShortDate(data.points.at(-1)?.scannedAt)}</span>
        </div>
        <ChangeBadge change={data.change.minUnitPrice} />
      </div>

      <div className="history-layout">
        <PriceChart data={data} />
        <div className="history-metrics">
          <HistoryMetric label="最低单价" change={data.change.minUnitPrice} format="money" />
          <HistoryMetric label="库存变化" change={data.change.totalQuantity} format="number" />
          <HistoryMetric label="挂单变化" change={data.change.listingCount} format="number" />
        </div>
      </div>
    </section>
  );
}

function PriceChart({ data }: { data: ItemHistoryResponse }) {
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
  const right = 594;
  const top = 23;
  const bottom = 132;
  const coordinates = priced.map((point, index) => ({
    x: priced.length === 1 ? (left + right) / 2 : left + (index / (priced.length - 1)) * (right - left),
    y: bottom - ((point.minUnitPrice - low) / range) * (bottom - top),
    point,
  }));
  const line = coordinates.map(({ x, y }, index) => `${index === 0 ? "M" : "L"} ${x} ${y}`).join(" ");
  const area = `${line} L ${coordinates.at(-1)!.x} ${bottom} L ${coordinates[0].x} ${bottom} Z`;

  return (
    <div className="price-chart">
      <svg viewBox="0 0 640 168" role="img" aria-label="各快照最低单价折线图">
        <defs>
          <linearGradient id="price-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="currentColor" stopOpacity="0.24" />
            <stop offset="1" stopColor="currentColor" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[top, (top + bottom) / 2, bottom].map((y) => <line key={y} className="chart-grid" x1={left} x2={right} y1={y} y2={y} />)}
        <path className="chart-area" d={area} fill="url(#price-area)" />
        <path className="chart-line" d={line} />
        {coordinates.map(({ x, y, point }) => (
          <g key={point.scanId}>
            <circle className="chart-halo" cx={x} cy={y} r="8" />
            <circle className="chart-dot" cx={x} cy={y} r="4" />
            <text className="chart-value" x={x} y={Math.max(14, y - 13)} textAnchor="middle">{compactCopper(point.minUnitPrice)}</text>
            <text className="chart-time" x={x} y="157" textAnchor="middle">{formatShortDate(point.scannedAt)}</text>
          </g>
        ))}
      </svg>
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
          {displayColumns.tier && <th>档位</th>}
          {displayColumns.attributes && <th>属性组合</th>}
          {displayColumns.petVariant && <th>宠物变体</th>}
          <th className="number-cell">单位价格</th>
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
              {displayColumns.tier && <td>
                <div className="variant-tier">
                  <strong>{listing.difficulty || "未标注档位"}</strong>
                  {tierDetails.length > 0 && <small>{tierDetails.join(" · ")}</small>}
                </div>
              </td>}
              {displayColumns.attributes && <td>
                <div className="variant-stats">
                  <strong>{listing.statLabel || (listing.hasSocket ? "棱彩插槽" : "—")}</strong>
                  {listing.statLabel && listing.hasSocket && <small>棱彩插槽</small>}
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
              <td className="number-cell"><Money value={listing.unitPrice} /></td>
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
