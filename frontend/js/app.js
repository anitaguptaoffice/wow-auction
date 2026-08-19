(function () {
    "use strict";

    const DEFAULT_PAGE_SIZE = 20;
    const DETAIL_PAGE_SIZE = 25;
    const SEARCH_DELAY_MS = 350;
    const VALID_SORTS = new Set([
        "price_asc",
        "price_desc",
        "quantity_desc",
        "listings_desc",
        "name_asc",
    ]);
    const VALID_PAGE_SIZES = new Set([20, 50, 100]);
    const numberFormatter = new Intl.NumberFormat("zh-CN");
    const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
    });

    const elements = {};
    const state = {
        q: "",
        page: 1,
        pageSize: DEFAULT_PAGE_SIZE,
        sort: "price_asc",
        total: 0,
        pages: 0,
        itemsController: null,
        statusController: null,
        snapshotAvailable: false,
        searchTimer: null,
    };
    const details = {
        item: null,
        page: 1,
        pageSize: DETAIL_PAGE_SIZE,
        total: 0,
        pages: 0,
        controller: null,
        returnFocus: null,
    };

    document.addEventListener("DOMContentLoaded", init);

    function init() {
        cacheElements();
        restoreTheme();
        restoreListStateFromUrl();
        bindEvents();
        syncControls();
        Promise.allSettled([loadStatus(), loadItems()]);
    }

    function cacheElements() {
        const ids = [
            "refresh-button", "theme-button", "snapshot-state", "snapshot-state-text",
            "snapshot-time", "snapshot-relative", "snapshot-server", "snapshot-records",
            "snapshot-items", "search-form", "search-input", "clear-search", "sort-select",
            "page-size-select", "result-summary", "items-body", "table-state", "table-spinner",
            "table-state-title", "table-state-message", "table-state-action", "pagination",
            "pagination-summary", "previous-page", "next-page", "page-numbers", "details-modal",
            "modal-close", "modal-title", "modal-subtitle", "modal-glyph", "listings-body",
            "modal-state", "modal-spinner", "modal-state-title", "modal-state-message",
            "modal-retry", "listing-page-summary", "listing-previous", "listing-next",
            "listing-page-number",
        ];
        ids.forEach(function (id) {
            elements[toCamelCase(id)] = document.getElementById(id);
        });
    }

    function toCamelCase(value) {
        return value.replace(/-([a-z])/g, function (_, letter) { return letter.toUpperCase(); });
    }

    function bindEvents() {
        elements.themeButton.addEventListener("click", toggleTheme);
        elements.refreshButton.addEventListener("click", refreshAll);

        elements.searchForm.addEventListener("submit", function (event) {
            event.preventDefault();
            clearTimeout(state.searchTimer);
            applySearch(elements.searchInput.value);
        });
        elements.searchInput.addEventListener("input", function () {
            elements.clearSearch.hidden = !elements.searchInput.value;
            clearTimeout(state.searchTimer);
            state.searchTimer = setTimeout(function () {
                applySearch(elements.searchInput.value);
            }, SEARCH_DELAY_MS);
        });
        elements.clearSearch.addEventListener("click", function () {
            clearTimeout(state.searchTimer);
            elements.searchInput.value = "";
            elements.clearSearch.hidden = true;
            elements.searchInput.focus();
            applySearch("");
        });
        elements.sortSelect.addEventListener("change", function () {
            state.sort = VALID_SORTS.has(elements.sortSelect.value) ? elements.sortSelect.value : "price_asc";
            state.page = 1;
            loadItems();
        });
        elements.pageSizeSelect.addEventListener("change", function () {
            const size = Number(elements.pageSizeSelect.value);
            state.pageSize = VALID_PAGE_SIZES.has(size) ? size : DEFAULT_PAGE_SIZE;
            state.page = 1;
            loadItems();
        });
        elements.previousPage.addEventListener("click", function () { goToPage(state.page - 1); });
        elements.nextPage.addEventListener("click", function () { goToPage(state.page + 1); });

        elements.modalClose.addEventListener("click", closeDetails);
        elements.detailsModal.addEventListener("click", function (event) {
            if (event.target === elements.detailsModal) closeDetails();
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !elements.detailsModal.hidden) closeDetails();
        });
        elements.modalRetry.addEventListener("click", function () { loadListings(); });
        elements.listingPrevious.addEventListener("click", function () {
            if (details.page > 1) {
                details.page -= 1;
                loadListings();
            }
        });
        elements.listingNext.addEventListener("click", function () {
            if (details.page < details.pages) {
                details.page += 1;
                loadListings();
            }
        });
    }

    function getApiBase() {
        const configured = typeof window.WOW_AUCTION_API_BASE === "string"
            ? window.WOW_AUCTION_API_BASE.trim()
            : "/api";
        return (configured || "/api").replace(/\/+$/, "");
    }

    async function requestJson(path, params, signal) {
        const query = new URLSearchParams();
        Object.keys(params || {}).forEach(function (key) {
            const value = params[key];
            if (value !== undefined && value !== null && value !== "") {
                query.set(key, String(value));
            }
        });
        const suffix = query.toString() ? "?" + query.toString() : "";
        const url = new URL(getApiBase() + path + suffix, window.location.href);
        const response = await fetch(url.toString(), {
            method: "GET",
            headers: { Accept: "application/json" },
            cache: "no-store",
            signal: signal,
        });
        const data = await response.json().catch(function () { return null; });
        if (!response.ok) {
            const error = new Error(apiErrorMessage(data, response.status));
            error.status = response.status;
            throw error;
        }
        if (!data || typeof data !== "object") {
            throw new Error("服务器返回了无法识别的数据");
        }
        return data;
    }

    function apiErrorMessage(data, status) {
        if (data && typeof data.detail === "string") return data.detail;
        if (data && Array.isArray(data.detail)) {
            const messages = data.detail.map(function (entry) {
                return entry && (entry.msg || entry.message);
            }).filter(Boolean);
            if (messages.length) return messages.join("；");
        }
        if (data && typeof data.message === "string") return data.message;
        if (status === 404) return "接口不存在或数据尚未生成";
        if (status >= 500) return "市场服务暂时不可用";
        return "请求失败（HTTP " + status + "）";
    }

    async function loadStatus() {
        if (state.statusController) state.statusController.abort();
        const controller = new AbortController();
        state.statusController = controller;
        setSnapshotState("loading", state.snapshotAvailable ? "正在检查新快照" : "正在读取快照");
        try {
            const data = await requestJson("/market/status", {}, controller.signal);
            if (state.statusController !== controller) return;
            applySnapshot(data);
        } catch (error) {
            if (error.name === "AbortError") return;
            if (!state.snapshotAvailable) {
                setSnapshotState("error", "快照状态读取失败");
                setSnapshotPlaceholders("读取失败");
            }
        } finally {
            if (state.statusController === controller) state.statusController = null;
        }
    }

    function applySnapshot(snapshot) {
        if (!snapshot || typeof snapshot !== "object") return;
        const available = snapshot.available !== false;
        const complete = snapshot.complete !== false;
        const timestamp = firstDefined(
            snapshot.scannedAt,
            snapshot.scanned_at,
            snapshot.timestamp,
            snapshot.snapshotTimestamp,
            snapshot.lastUpdated
        );
        const server = firstDefined(
            snapshot.server,
            snapshot.serverName,
            snapshot.realm,
            snapshot.realmName,
            snapshot.connectedRealm
        );
        const region = firstDefined(snapshot.region, snapshot.gameRegion);
        const recordCount = firstNumber(
            snapshot.listingCount,
            snapshot.recordCount,
            snapshot.auctionCount,
            snapshot.totalListings
        );
        const uniqueItems = firstNumber(
            snapshot.uniqueItemCount,
            snapshot.itemCount,
            snapshot.totalItems
        );

        if (timestamp !== undefined) {
            elements.snapshotTime.textContent = formatDateTime(timestamp);
            elements.snapshotRelative.textContent = formatRelativeTime(timestamp);
        }
        if (server !== undefined && server !== null && String(server).trim()) {
            elements.snapshotServer.textContent = String(server);
        } else if (elements.snapshotServer.textContent === "加载中…") {
            elements.snapshotServer.textContent = region ? "未标注（" + region + "）" : "未标注";
        }
        if (recordCount !== null) elements.snapshotRecords.textContent = formatNumber(recordCount) + " 条";
        if (uniqueItems !== null) elements.snapshotItems.textContent = formatNumber(uniqueItems) + " 种物品";

        state.snapshotAvailable = available;
        if (!available) {
            if (timestamp === undefined) {
                elements.snapshotTime.textContent = "暂无快照";
                elements.snapshotRelative.textContent = "等待导入拍卖数据";
            }
            if (elements.snapshotServer.textContent === "加载中…") elements.snapshotServer.textContent = "未标注";
            if (recordCount === null) elements.snapshotRecords.textContent = "0 条";
            if (uniqueItems === null) elements.snapshotItems.textContent = "0 种物品";
            setSnapshotState("error", "暂无可用快照");
        } else if (!complete) {
            setSnapshotState("error", "快照未通过完整性校验");
        } else {
            setSnapshotState("ready", "市场快照可用");
        }
    }

    function setSnapshotPlaceholders(text) {
        [elements.snapshotTime, elements.snapshotServer, elements.snapshotRecords].forEach(function (node) {
            if (node.textContent === "加载中…") node.textContent = text;
        });
    }

    function setSnapshotState(kind, text) {
        elements.snapshotState.dataset.state = kind;
        elements.snapshotStateText.textContent = text;
    }

    async function loadItems() {
        if (state.itemsController) state.itemsController.abort();
        const controller = new AbortController();
        state.itemsController = controller;
        setItemsState("loading", "正在加载市场数据", "正在读取最新快照中的物品…");
        elements.resultSummary.textContent = "正在加载物品…";

        try {
            const data = await requestJson("/market/items", {
                q: state.q,
                page: state.page,
                page_size: state.pageSize,
                sort: state.sort,
            }, controller.signal);
            if (state.itemsController !== controller) return;

            const items = Array.isArray(data.items) ? data.items : [];
            const total = normalizedNonNegativeInteger(data.total, items.length);
            const pageSize = normalizedPositiveInteger(firstDefined(data.pageSize, data.page_size), state.pageSize);
            const currentPage = normalizedPositiveInteger(data.page, state.page);
            const totalPagesValue = firstDefined(data.totalPages, data.pages, data.total_pages);
            const pages = normalizedNonNegativeInteger(
                totalPagesValue,
                total > 0 ? Math.ceil(total / pageSize) : 0
            );

            if (pages > 0 && currentPage > pages) {
                state.page = pages;
                loadItems();
                return;
            }

            state.page = currentPage;
            state.pageSize = pageSize;
            state.total = total;
            state.pages = pages;
            if (data.scan && typeof data.scan === "object") applySnapshot(data.scan);

            if (!items.length) {
                renderItems([]);
                const hasSearch = Boolean(state.q);
                setItemsState(
                    "empty",
                    hasSearch ? "没有找到匹配物品" : "快照中没有可显示的物品",
                    hasSearch ? "请尝试缩短关键词，或直接输入物品 ID。" : "请先生成并导入拍卖行快照。",
                    hasSearch ? "清除搜索" : "重新加载",
                    hasSearch ? clearSearchAndReload : loadItems
                );
            } else {
                renderItems(items);
                hideItemsState();
            }
            renderListSummary();
            renderPagination();
            updateUrl();
            elements.pageSizeSelect.value = String(state.pageSize);
        } catch (error) {
            if (error.name === "AbortError") return;
            renderItems([]);
            state.total = 0;
            state.pages = 0;
            renderPagination();
            elements.resultSummary.textContent = "市场数据加载失败";
            setItemsState(
                "error",
                "无法加载物品列表",
                error.message || "请稍后重试。",
                "重新加载",
                loadItems
            );
        } finally {
            if (state.itemsController === controller) state.itemsController = null;
        }
    }

    function renderItems(items) {
        const fragment = document.createDocumentFragment();
        items.forEach(function (item) {
            const itemId = firstDefined(item.itemID, item.itemId, item.item_id);
            const battlePetCreatureId = firstDefined(
                item.battlePetCreatureID,
                item.battlePetCreatureId,
                item.battle_pet_creature_id
            );
            const name = String(firstDefined(item.name, item.itemName, "物品 #" + itemId));
            const quality = normalizeQuality(firstDefined(item.qualityID, item.qualityId, item.quality));
            const row = document.createElement("tr");

            const itemCell = document.createElement("td");
            itemCell.className = "item-cell";
            itemCell.dataset.label = "物品";
            const identity = document.createElement("div");
            identity.className = "item-identity quality-" + quality;
            const glyph = document.createElement("span");
            glyph.className = "item-glyph";
            glyph.setAttribute("aria-hidden", "true");
            glyph.textContent = itemGlyph(name);
            const copy = document.createElement("div");
            copy.className = "item-copy";
            const title = document.createElement("span");
            title.className = "item-name";
            title.textContent = name;
            title.title = name;
            const id = document.createElement("span");
            id.className = "item-id";
            id.textContent = "物品 ID " + (itemId == null ? "未知" : itemId)
                + (battlePetCreatureId === undefined || battlePetCreatureId === null || battlePetCreatureId === ""
                    ? ""
                    : " · 宠物物种 ID " + battlePetCreatureId);
            copy.append(title, id);
            identity.append(glyph, copy);
            itemCell.append(identity);

            const unitPriceCell = priceCell(item.minUnitPrice, "最低单价");
            const minBuyoutCell = priceCell(item.minBuyout, "最低一口价");
            const listingCell = countCell(item.listingCount, "拍卖条目");
            const quantityCell = countCell(item.totalQuantity, "总数量");

            const actionCell = document.createElement("td");
            actionCell.className = "number-cell action-cell";
            actionCell.dataset.label = "操作";
            const detailButton = document.createElement("button");
            detailButton.className = "details-button";
            detailButton.type = "button";
            detailButton.textContent = "查看拍卖";
            detailButton.disabled = itemId === undefined || itemId === null;
            detailButton.addEventListener("click", function () { openDetails(item, detailButton); });
            actionCell.append(detailButton);

            row.append(itemCell, unitPriceCell, minBuyoutCell, listingCell, quantityCell, actionCell);
            fragment.append(row);
        });
        elements.itemsBody.replaceChildren(fragment);
    }

    function priceCell(value, label) {
        const cell = document.createElement("td");
        cell.className = "number-cell";
        cell.dataset.label = label;
        cell.append(createMoney(value));
        return cell;
    }

    function countCell(value, label) {
        const cell = document.createElement("td");
        cell.className = "number-cell";
        cell.dataset.label = label;
        const number = finiteNumber(value);
        const span = document.createElement("span");
        span.className = number === null ? "muted-value" : "count-value";
        span.textContent = number === null ? "—" : formatNumber(number);
        cell.append(span);
        return cell;
    }

    function createMoney(value) {
        const wrapper = document.createElement("span");
        wrapper.className = "money";
        const numeric = finiteNumber(value);
        if (numeric === null || numeric <= 0) {
            wrapper.classList.add("muted-value");
            wrapper.textContent = "—";
            return wrapper;
        }

        const copper = Math.floor(numeric);
        const gold = Math.floor(copper / 10000);
        const silver = Math.floor((copper % 10000) / 100);
        const remainder = copper % 100;
        if (gold > 0) appendCoinPart(wrapper, formatNumber(gold), "gold", "金");
        if (silver > 0 || gold > 0) appendCoinPart(wrapper, gold > 0 ? String(silver).padStart(2, "0") : silver, "silver", "银");
        appendCoinPart(wrapper, (gold > 0 || silver > 0) ? String(remainder).padStart(2, "0") : remainder, "copper", "铜");
        wrapper.title = formatNumber(copper) + " 铜币";
        return wrapper;
    }

    function appendCoinPart(wrapper, amount, type, label) {
        const value = document.createElement("span");
        value.textContent = String(amount);
        const coin = document.createElement("i");
        coin.className = "coin coin-" + type;
        coin.title = label;
        coin.setAttribute("aria-label", label);
        wrapper.append(value, coin);
    }

    function setItemsState(kind, title, message, actionText, action) {
        elements.itemsBody.replaceChildren();
        elements.tableState.hidden = false;
        elements.tableState.dataset.state = kind;
        elements.tableSpinner.hidden = kind !== "loading";
        elements.tableStateTitle.textContent = title;
        elements.tableStateMessage.textContent = message;
        elements.tableStateAction.hidden = !actionText;
        elements.tableStateAction.textContent = actionText || "";
        elements.tableStateAction.onclick = typeof action === "function" ? action : null;
        elements.pagination.hidden = true;
    }

    function hideItemsState() {
        elements.tableState.hidden = true;
    }

    function renderListSummary() {
        const queryLabel = state.q ? "“" + state.q + "” · " : "";
        elements.resultSummary.textContent = queryLabel + "共 " + formatNumber(state.total) + " 种物品";
    }

    function renderPagination() {
        if (state.total <= 0 || state.pages <= 0) {
            elements.pagination.hidden = true;
            return;
        }
        elements.pagination.hidden = false;
        const start = (state.page - 1) * state.pageSize + 1;
        const end = Math.min(state.page * state.pageSize, state.total);
        elements.paginationSummary.textContent = "显示 " + formatNumber(start) + "–" + formatNumber(end) + "，共 " + formatNumber(state.total) + " 种物品";
        elements.previousPage.disabled = state.page <= 1;
        elements.nextPage.disabled = state.page >= state.pages;

        const fragment = document.createDocumentFragment();
        paginationTokens(state.page, state.pages).forEach(function (token) {
            if (token === "…") {
                const ellipsis = document.createElement("span");
                ellipsis.className = "page-ellipsis";
                ellipsis.textContent = token;
                fragment.append(ellipsis);
                return;
            }
            const button = document.createElement("button");
            button.className = "page-button";
            button.type = "button";
            button.textContent = String(token);
            button.setAttribute("aria-label", "第 " + token + " 页");
            if (token === state.page) button.setAttribute("aria-current", "page");
            button.addEventListener("click", function () { goToPage(token); });
            fragment.append(button);
        });
        elements.pageNumbers.replaceChildren(fragment);
    }

    function paginationTokens(current, pages) {
        if (pages <= 7) return Array.from({ length: pages }, function (_, index) { return index + 1; });
        const values = new Set([1, pages, current - 2, current - 1, current, current + 1, current + 2]);
        const sorted = Array.from(values).filter(function (page) { return page >= 1 && page <= pages; }).sort(function (a, b) { return a - b; });
        const result = [];
        sorted.forEach(function (page, index) {
            if (index > 0 && page - sorted[index - 1] > 1) result.push("…");
            result.push(page);
        });
        return result;
    }

    function goToPage(page) {
        const target = Math.max(1, Math.min(normalizedPositiveInteger(page, 1), state.pages || 1));
        if (target === state.page) return;
        state.page = target;
        loadItems();
        document.querySelector(".market-card").scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function applySearch(value) {
        const normalized = String(value || "").trim();
        if (normalized === state.q) return;
        state.q = normalized;
        state.page = 1;
        loadItems();
    }

    function clearSearchAndReload() {
        elements.searchInput.value = "";
        elements.clearSearch.hidden = true;
        state.q = "";
        state.page = 1;
        loadItems();
    }

    function openDetails(item, trigger) {
        details.item = item;
        details.page = 1;
        details.total = 0;
        details.pages = 0;
        details.returnFocus = trigger || document.activeElement;
        const itemId = firstDefined(item.itemID, item.itemId, item.item_id);
        const battlePetCreatureId = firstDefined(
            item.battlePetCreatureID,
            item.battlePetCreatureId,
            item.battle_pet_creature_id
        );
        const name = String(firstDefined(item.name, item.itemName, "物品 #" + itemId));
        const quality = normalizeQuality(firstDefined(item.qualityID, item.qualityId, item.quality));
        const listingCount = firstNumber(item.listingCount);

        elements.modalTitle.textContent = name;
        elements.modalTitle.className = "quality-" + quality;
        elements.modalSubtitle.textContent = "物品 ID " + itemId
            + (battlePetCreatureId === undefined || battlePetCreatureId === null || battlePetCreatureId === ""
                ? ""
                : " · 宠物物种 ID " + battlePetCreatureId)
            + (listingCount === null ? "" : " · " + formatNumber(listingCount) + " 条拍卖");
        elements.modalGlyph.textContent = itemGlyph(name);
        elements.modalGlyph.parentElement.className = "item-identity quality-" + quality;
        elements.detailsModal.hidden = false;
        document.body.classList.add("modal-open");
        elements.modalClose.focus();
        loadListings();
    }

    function closeDetails() {
        if (elements.detailsModal.hidden) return;
        if (details.controller) details.controller.abort();
        details.controller = null;
        details.item = null;
        elements.detailsModal.hidden = true;
        document.body.classList.remove("modal-open");
        elements.listingsBody.replaceChildren();
        if (details.returnFocus && typeof details.returnFocus.focus === "function") details.returnFocus.focus();
    }

    async function loadListings() {
        if (!details.item) return;
        if (details.controller) details.controller.abort();
        const controller = new AbortController();
        details.controller = controller;
        const itemId = firstDefined(details.item.itemID, details.item.itemId, details.item.item_id);
        const battlePetCreatureId = firstDefined(
            details.item.battlePetCreatureID,
            details.item.battlePetCreatureId,
            details.item.battle_pet_creature_id
        );
        const query = {
            page: details.page,
            page_size: details.pageSize,
        };
        if (battlePetCreatureId !== undefined && battlePetCreatureId !== null && battlePetCreatureId !== "") {
            query.battle_pet_creature_id = battlePetCreatureId;
        }
        setModalState("loading", "正在加载拍卖明细", "正在读取该物品的拍卖记录…");

        try {
            const data = await requestJson(
                "/market/items/" + encodeURIComponent(itemId) + "/listings",
                query,
                controller.signal
            );
            if (details.controller !== controller) return;

            const listings = Array.isArray(data.listings)
                ? data.listings
                : (Array.isArray(data.items) ? data.items : []);
            const total = normalizedNonNegativeInteger(data.total, listings.length);
            const pageSize = normalizedPositiveInteger(firstDefined(data.pageSize, data.page_size), details.pageSize);
            const currentPage = normalizedPositiveInteger(data.page, details.page);
            const pages = normalizedNonNegativeInteger(
                firstDefined(data.totalPages, data.pages, data.total_pages),
                total > 0 ? Math.ceil(total / pageSize) : 0
            );
            details.page = currentPage;
            details.pageSize = pageSize;
            details.total = total;
            details.pages = pages;

            if (pages > 0 && currentPage > pages) {
                details.page = pages;
                loadListings();
                return;
            }
            if (!listings.length) {
                renderListings([]);
                setModalState("empty", "没有拍卖明细", "该物品在当前快照中没有可显示的记录。", false);
            } else {
                renderListings(listings);
                hideModalState();
            }
            renderListingPagination();
        } catch (error) {
            if (error.name === "AbortError") return;
            renderListings([]);
            setModalState("error", "无法加载拍卖明细", error.message || "请稍后重试。", true);
            elements.listingPageSummary.textContent = "";
            elements.listingPrevious.disabled = true;
            elements.listingNext.disabled = true;
            elements.listingPageNumber.textContent = "—";
        } finally {
            if (details.controller === controller) details.controller = null;
        }
    }

    function renderListings(listings) {
        const fragment = document.createDocumentFragment();
        listings.forEach(function (listing) {
            const quantity = Math.max(1, normalizedPositiveInteger(firstDefined(listing.quantity, listing.stackSize), 1));
            const buyout = firstDefined(listing.buyoutAmount, listing.buyout, listing.buyout_amount);
            const explicitUnitPrice = firstDefined(listing.unitPrice, listing.unit_price);
            const buyoutNumber = finiteNumber(buyout);
            const unitPrice = explicitUnitPrice !== undefined && explicitUnitPrice !== null
                ? explicitUnitPrice
                : (buyoutNumber !== null && buyoutNumber > 0 ? Math.floor(buyoutNumber / quantity) : null);
            const bid = positiveFirst(listing.bidAmount, listing.currentBid, listing.minBid, listing.min_bid);
            const row = document.createElement("tr");

            const quantityCell = document.createElement("td");
            quantityCell.className = "count-value";
            quantityCell.textContent = formatNumber(quantity);
            const unitCell = document.createElement("td");
            unitCell.className = "number-cell";
            unitCell.append(createMoney(unitPrice));
            const buyoutCell = document.createElement("td");
            buyoutCell.className = "number-cell";
            buyoutCell.append(createMoney(buyout));
            const bidCell = document.createElement("td");
            bidCell.className = "number-cell";
            bidCell.append(createMoney(bid));
            const timeCell = document.createElement("td");
            timeCell.textContent = listingTimeLabel(listing);

            row.append(quantityCell, unitCell, buyoutCell, bidCell, timeCell);
            fragment.append(row);
        });
        elements.listingsBody.replaceChildren(fragment);
    }

    function setModalState(kind, title, message, retry) {
        elements.listingsBody.replaceChildren();
        elements.modalState.hidden = false;
        elements.modalState.dataset.state = kind;
        elements.modalSpinner.hidden = kind !== "loading";
        elements.modalStateTitle.textContent = title;
        elements.modalStateMessage.textContent = message;
        elements.modalRetry.hidden = !retry;
    }

    function hideModalState() {
        elements.modalState.hidden = true;
    }

    function renderListingPagination() {
        if (details.total <= 0 || details.pages <= 0) {
            elements.listingPageSummary.textContent = "0 条拍卖记录";
            elements.listingPrevious.disabled = true;
            elements.listingNext.disabled = true;
            elements.listingPageNumber.textContent = "0 / 0";
            return;
        }
        const start = (details.page - 1) * details.pageSize + 1;
        const end = Math.min(details.page * details.pageSize, details.total);
        elements.listingPageSummary.textContent = "显示 " + formatNumber(start) + "–" + formatNumber(end) + "，共 " + formatNumber(details.total) + " 条";
        elements.listingPrevious.disabled = details.page <= 1;
        elements.listingNext.disabled = details.page >= details.pages;
        elements.listingPageNumber.textContent = details.page + " / " + details.pages;
    }

    function listingTimeLabel(listing) {
        const explicit = firstDefined(listing.timeLeftLabel, listing.time_left_label, listing.timeLeft);
        if (explicit !== undefined && explicit !== null && String(explicit).trim()) return String(explicit);
        const band = Number(firstDefined(listing.timeLeftBand, listing.time_left_band));
        const labels = {
            1: "短（少于 30 分钟）",
            2: "中（30 分钟–2 小时）",
            3: "长（2–12 小时）",
            4: "非常长（超过 12 小时）",
        };
        return labels[band] || "未知";
    }

    async function refreshAll() {
        elements.refreshButton.disabled = true;
        try {
            await Promise.allSettled([loadStatus(), loadItems()]);
        } finally {
            elements.refreshButton.disabled = false;
        }
    }

    function restoreListStateFromUrl() {
        const params = new URLSearchParams(window.location.search);
        const page = Number(params.get("page"));
        const pageSize = Number(params.get("page_size"));
        const sort = params.get("sort");
        state.q = (params.get("q") || "").trim();
        state.page = Number.isInteger(page) && page > 0 ? page : 1;
        state.pageSize = VALID_PAGE_SIZES.has(pageSize) ? pageSize : DEFAULT_PAGE_SIZE;
        state.sort = VALID_SORTS.has(sort) ? sort : "price_asc";
    }

    function syncControls() {
        elements.searchInput.value = state.q;
        elements.clearSearch.hidden = !state.q;
        elements.sortSelect.value = state.sort;
        elements.pageSizeSelect.value = String(state.pageSize);
    }

    function updateUrl() {
        try {
            const url = new URL(window.location.href);
            if (state.q) url.searchParams.set("q", state.q); else url.searchParams.delete("q");
            if (state.page > 1) url.searchParams.set("page", String(state.page)); else url.searchParams.delete("page");
            if (state.pageSize !== DEFAULT_PAGE_SIZE) url.searchParams.set("page_size", String(state.pageSize)); else url.searchParams.delete("page_size");
            if (state.sort !== "price_asc") url.searchParams.set("sort", state.sort); else url.searchParams.delete("sort");
            window.history.replaceState(null, "", url.toString());
        } catch (_) {
            // file:// 预览或受限浏览器中，地址栏状态同步失败不影响查询。
        }
    }

    function restoreTheme() {
        let preferred = "";
        try { preferred = localStorage.getItem("wow-auction-theme") || ""; } catch (_) { /* ignore */ }
        const dark = preferred ? preferred === "dark" : window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
        document.documentElement.classList.toggle("dark", Boolean(dark));
    }

    function toggleTheme() {
        const dark = document.documentElement.classList.toggle("dark");
        try { localStorage.setItem("wow-auction-theme", dark ? "dark" : "light"); } catch (_) { /* ignore */ }
    }

    function formatDateTime(value) {
        const date = normalizeDate(value);
        return date ? dateFormatter.format(date) : "未提供";
    }

    function formatRelativeTime(value) {
        const date = normalizeDate(value);
        if (!date) return "未提供更新时间";
        const diffSeconds = Math.round((Date.now() - date.getTime()) / 1000);
        const future = diffSeconds < 0;
        const absolute = Math.abs(diffSeconds);
        let amount;
        let unit;
        if (absolute < 60) { amount = absolute; unit = "秒"; }
        else if (absolute < 3600) { amount = Math.floor(absolute / 60); unit = "分钟"; }
        else if (absolute < 86400) { amount = Math.floor(absolute / 3600); unit = "小时"; }
        else { amount = Math.floor(absolute / 86400); unit = "天"; }
        return future ? amount + " " + unit + "后" : amount + " " + unit + "前";
    }

    function normalizeDate(value) {
        if (value === undefined || value === null || value === "") return null;
        let date;
        if (typeof value === "number" || /^\d+(\.\d+)?$/.test(String(value))) {
            let timestamp = Number(value);
            if (timestamp < 100000000000) timestamp *= 1000;
            date = new Date(timestamp);
        } else {
            date = new Date(value);
        }
        return Number.isNaN(date.getTime()) ? null : date;
    }

    function itemGlyph(name) {
        const cleaned = String(name || "").trim();
        return cleaned ? Array.from(cleaned)[0].toUpperCase() : "?";
    }

    function normalizeQuality(value) {
        if (typeof value === "number" && Number.isFinite(value)) return Math.max(0, Math.min(8, Math.floor(value)));
        if (/^\d+$/.test(String(value || ""))) return Math.max(0, Math.min(8, Number(value)));
        const names = { poor: 0, common: 1, uncommon: 2, rare: 3, epic: 4, legendary: 5, artifact: 6, heirloom: 7, token: 8 };
        return names[String(value || "").toLowerCase()] ?? 1;
    }

    function firstDefined() {
        for (let index = 0; index < arguments.length; index += 1) {
            if (arguments[index] !== undefined && arguments[index] !== null) return arguments[index];
        }
        return undefined;
    }

    function finiteNumber(value) {
        if (value === undefined || value === null || value === "") return null;
        const number = Number(value);
        return Number.isFinite(number) ? number : null;
    }

    function firstNumber() {
        for (let index = 0; index < arguments.length; index += 1) {
            const number = finiteNumber(arguments[index]);
            if (number !== null) return number;
        }
        return null;
    }

    function positiveFirst() {
        for (let index = 0; index < arguments.length; index += 1) {
            const number = finiteNumber(arguments[index]);
            if (number !== null && number > 0) return number;
        }
        return null;
    }

    function normalizedNonNegativeInteger(value, fallback) {
        const number = Number(value);
        return Number.isFinite(number) && number >= 0 ? Math.floor(number) : Math.max(0, Math.floor(Number(fallback) || 0));
    }

    function normalizedPositiveInteger(value, fallback) {
        const number = Number(value);
        return Number.isFinite(number) && number > 0 ? Math.floor(number) : Math.max(1, Math.floor(Number(fallback) || 1));
    }

    function formatNumber(value) {
        const number = finiteNumber(value);
        return number === null ? "—" : numberFormatter.format(Math.floor(number));
    }
}());
