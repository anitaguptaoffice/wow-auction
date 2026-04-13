local initialQuery
local auctions = {}

-- 数据库初始化
AuctionSearchDB = AuctionSearchDB or {
	auctions = {},
	lastScanTime = 0,
	settings = {
		maxHistoryDays = 7, -- 保留7天的历史数据
		maxRecordsPerDay = 1000 -- 每天最多保存1000条记录
	}
}

-- 工具函数：获取当前日期字符串
local function GetDateString()
	return date("%Y-%m-%d")
end

-- 工具函数：获取当前时间戳
local function GetCurrentTime()
	return time()
end

-- 与 Enum.AuctionHouseTimeLeftBand 一致（见 Wiki GetTimeLeftBandInfo）：0 短 1 中 2 长 3 很长
local function TimeLeftBandLabel(band)
	if band == nil then
		return "?"
	end
	local labels = { [0] = "短", [1] = "中", [2] = "长", [3] = "很长" }
	return labels[band] or tostring(band)
end

-- 数据清理函数：清除过期数据
local function CleanOldData()
	local currentTime = GetCurrentTime()
	local maxAge = AuctionSearchDB.settings.maxHistoryDays * 24 * 60 * 60 -- 转换为秒

	for dateStr, dayData in pairs(AuctionSearchDB.auctions) do
		if dayData.timestamp and (currentTime - dayData.timestamp) > maxAge then
			AuctionSearchDB.auctions[dateStr] = nil
			print(format("AuctionSearch: 清理过期数据 %s", dateStr))
		end
	end
end

-- 调试：仅 C_Item 基础信息（词缀等一律在后端用 itemLink 解析）
local function GetItemInfoDebug(itemID)
	local itemName, _, itemQuality, itemLevel, _, itemType, itemSubType,
		_, itemEquipLoc, _, _, classID, subclassID = C_Item.GetItemInfo(itemID)
	local d = {}
	if itemName then
		d.name = itemName
		d.quality = itemQuality
		d.itemLevel = itemLevel
		d.itemType = itemType
		d.itemSubType = itemSubType
		d.equipLoc = itemEquipLoc
		d.classID = classID
		d.subclassID = subclassID
	end
	return d
end

-- 保存拍卖数据到持久化存储
local function SaveAuctionData(auctionData)
	local dateStr = GetDateString()
	local currentTime = GetCurrentTime()

	-- 初始化当天数据结构
	if not AuctionSearchDB.auctions[dateStr] then
		AuctionSearchDB.auctions[dateStr] = {
			scans = {},
			timestamp = currentTime
		}
	end

	-- 检查当天记录数量限制
	local dayData = AuctionSearchDB.auctions[dateStr]
	if #dayData.scans >= AuctionSearchDB.settings.maxRecordsPerDay then
		-- 移除最旧的记录
		table.remove(dayData.scans, 1)
	end

	local replicateCount = C_AuctionHouse.GetNumReplicateItems()

	-- 添加新的扫描记录（replicate 列表为 0..n-1，勿用 ipairs 以免漏掉索引 0）
	local scanRecord = {
		timestamp = currentTime,
		itemCount = replicateCount,
		items = {}
	}

	-- 每条拍卖：replicate 数值 + GetReplicateItemLink；词缀等在后端用 itemLink 解析
	for i = 0, math.max(0, replicateCount - 1) do
		local auction = auctionData[i]
		if auction and auction[17] then -- itemID存在
			local itemID = auction[17]
			local itemLink = C_AuctionHouse.GetReplicateItemLink(i)
			-- timeLeftBand：C_AuctionHouse.GetReplicateItemTimeLeft(i) 返回的是「剩余时间档位」枚举，不是秒数，也不是从本次扫描时刻起算；无单独「基准时刻」API
			local timeLeftBand = C_AuctionHouse.GetReplicateItemTimeLeft(i)

			-- GetReplicateItemInfo：… 8=minBid 9=minIncrement 10=buyoutPrice 11=bidAmount … 17=itemID 18=hasAllInfo
			local itemInfo = {
				itemID = itemID,
				minBid = auction[8],
				buyoutAmount = auction[10], -- buyoutPrice
				bidAmount = auction[11], -- 当前最高竞价
				quantity = auction[3], -- count
				name = auction[1],
				itemLink = itemLink,
				timeLeftBand = timeLeftBand,
			}

			table.insert(scanRecord.items, itemInfo)
		end
	end

	table.insert(dayData.scans, scanRecord)
	AuctionSearchDB.lastScanTime = currentTime

	print(format("AuctionSearch: 已保存 %d 件物品信息到 %s", scanRecord.itemCount, dateStr))
end

-- 数据查询函数
local function GetAuctionHistory(itemID, days)
	days = days or 7
	local results = {}
	local currentTime = GetCurrentTime()
	local timeLimit = currentTime - (days * 24 * 60 * 60)

	for dateStr, dayData in pairs(AuctionSearchDB.auctions) do
		if dayData.timestamp >= timeLimit then
			for _, scan in ipairs(dayData.scans) do
				for _, item in ipairs(scan.items) do
					if not itemID or item.itemID == itemID then
						table.insert(results, {
							date = dateStr,
							timestamp = scan.timestamp,
							itemID = item.itemID,
							minBid = item.minBid,
							buyoutAmount = item.buyoutAmount,
							bidAmount = item.bidAmount,
							quantity = item.quantity,
							name = item.name,
							itemLink = item.itemLink,
							timeLeftBand = item.timeLeftBand,
						})
					end
				end
			end
		end
	end

	-- 按时间排序
	table.sort(results, function(a, b) return a.timestamp > b.timestamp end)
	return results
end

-- 获取数据库统计信息
local function GetDatabaseStats()
	local totalScans = 0
	local totalItems = 0
	local oldestDate = nil
	local newestDate = nil

	for dateStr, dayData in pairs(AuctionSearchDB.auctions) do
		totalScans = totalScans + #dayData.scans
		for _, scan in ipairs(dayData.scans) do
			totalItems = totalItems + scan.itemCount
		end

		if not oldestDate or dateStr < oldestDate then
			oldestDate = dateStr
		end
		if not newestDate or dateStr > newestDate then
			newestDate = dateStr
		end
	end

	return {
		totalScans = totalScans,
		totalItems = totalItems,
		oldestDate = oldestDate,
		newestDate = newestDate,
		lastScanTime = AuctionSearchDB.lastScanTime
	}
end

-- 斜杠命令处理
local function HandleSlashCommand(msg)
	local command, arg = msg:match("^(%S*)%s*(.-)$")
	command = command:lower()

	if command == "stats" then
		local stats = GetDatabaseStats()
		print("=== AuctionSearch 数据库统计 ===")
		print(format("总扫描次数: %d", stats.totalScans))
		print(format("总物品记录: %d", stats.totalItems))
		print(format("数据范围: %s 到 %s", stats.oldestDate or "无", stats.newestDate or "无"))
		if stats.lastScanTime > 0 then
			print(format("最后扫描时间: %s", date("%Y-%m-%d %H:%M:%S", stats.lastScanTime)))
		end
	elseif command == "history" then
		local itemID = tonumber(arg)
		if itemID then
			local history = GetAuctionHistory(itemID, 7)
			print(format("=== 物品 %d 的拍卖历史 (最近7天) ===", itemID))
			for i = 1, math.min(10, #history) do
				local item = history[i]
				print(format(
					"%s: 一口 %s | 竞拍 %s | 起拍 %s | 剩余档 %s | 数量 %d",
					date("%m-%d %H:%M", item.timestamp),
					item.buyoutAmount and C_CurrencyInfo.GetCoinTextureString(item.buyoutAmount) or "无",
					item.bidAmount and C_CurrencyInfo.GetCoinTextureString(item.bidAmount) or "无",
					item.minBid and C_CurrencyInfo.GetCoinTextureString(item.minBid) or "无",
					TimeLeftBandLabel(item.timeLeftBand),
					item.quantity
				))
			end
			if #history > 10 then
				print(format("... 还有 %d 条记录", #history - 10))
			end
		else
			print("用法: /auctionsearch history <物品ID>")
		end
	elseif command == "clear" then
		AuctionSearchDB.auctions = {}
		print("AuctionSearch: 已清空所有保存的数据")
	elseif command == "test" then
		local testItemID = tonumber(arg) or 238033
		print(format("=== 测试物品 %d ===", testItemID))
		local itemDetails = GetItemInfoDebug(testItemID)
		print("C_Item.GetItemInfo (客户端缓存命中时才有数据):")
		for key, value in pairs(itemDetails) do
			print(format("  %s: %s", key, tostring(value)))
		end
		print("若拍卖行已打开且含该物品，取 itemLink（完整描述以链接为准，后端解析）:")
		local foundIndex = nil
		for i = 0, C_AuctionHouse.GetNumReplicateItems() - 1 do
			local auctionInfo = { C_AuctionHouse.GetReplicateItemInfo(i) }
			if auctionInfo[17] == testItemID then
				foundIndex = i
				break
			end
		end
		if foundIndex then
			local itemLink = C_AuctionHouse.GetReplicateItemLink(foundIndex)
			print(format("ItemLink: %s", tostring(itemLink)))
		else
			print("当前拍卖行 replicate 中无此物品（请先扫描拍卖行）")
		end
	elseif command == "uitest" then
		local valid = { idle = true, started = true, scanning = true, complete = true, logout = true } -- logout 同 complete
		local p = "started"
		if arg and arg ~= "" then
			p = strlower((strmatch(arg, "^%s*(%S+)") or "started"))
		end
		if not valid[p] then
			p = "started"
		end
		if AuctionSearchOverlay and AuctionSearchOverlay.Init and AuctionSearchOverlay.SetPhase then
			AuctionSearchOverlay.Init()
			AuctionSearchOverlay.SetPhase(p)
			print(format("AuctionSearch: uitest phase=%s （idle|started|scanning|complete；logout 等同 complete）", AuctionSearchOverlay.GetPhase and AuctionSearchOverlay.GetPhase() or p))
		end
	else
		print("AuctionSearch 命令:")
		print("  /auctionsearch stats - 显示数据库统计信息")
		print("  /auctionsearch history <物品ID> - 显示物品拍卖历史")
		print("  /auctionsearch test [物品ID] - 查看 C_Item 缓存与 itemLink (默认238033)")
		print("  /auctionsearch clear - 清空所有保存的数据")
		print("  /auctionsearch uitest [phase] - 调试自动化面板 (idle|started|scanning|complete；logout=complete)")
	end
end

-- 注册斜杠命令
SLASH_AUCTIONSEARCH1 = "/auctionsearch"
SLASH_AUCTIONSEARCH2 = "/as"
SlashCmdList["AUCTIONSEARCH"] = HandleSlashCommand

-- 扫描完成后的回调函数
local function OnScanComplete(beginTime)
	local scanTime = debugprofilestop() - beginTime
	-- 勿用 #auctions：索引从 0 起，Lua 的 # 对非 1..n 序列不可靠
	local n = C_AuctionHouse.GetNumReplicateItems()
	print(format("Scanned %d auctions in %d milliseconds", n, scanTime))

	-- 保存扫描数据到持久化存储
	SaveAuctionData(auctions)

	-- 执行数据清理
	CleanOldData()

	-- 自动化：单屏双行 — 扫描完成 + 登出说明（与 wow-runner 一屏模板即可）
	if AuctionSearchOverlay and AuctionSearchOverlay.SetPhase then
		AuctionSearchOverlay.SetPhase("complete")
	end
end

-- 限流参数
local REPLICATE_ITEMS_PER_FRAME = 1800 -- 每帧最多处理1800个物品（留点余量）
local SCAN_BATCH_SIZE = 500            -- 每批处理的物品数量
local scanIndex = 0
local totalItems = 0
local currentBatchContinuables = {}

-- 分批处理函数
local function ScanBatch(beginTime, allContinuables)
	local batchStart = scanIndex
	local batchEnd = math.min(scanIndex + SCAN_BATCH_SIZE - 1, totalItems - 1)
	local batchHasAsync = false

	print(format("AuctionSearch: 处理批次 %d-%d", batchStart, batchEnd))

	-- 处理当前批次
	for i = batchStart, batchEnd do
		auctions[i] = { C_AuctionHouse.GetReplicateItemInfo(i) }
		if not auctions[i][18] then                    -- hasAllInfo
			batchHasAsync = true
			local item = Item:CreateFromItemID(auctions[i][17]) -- itemID
			allContinuables[item] = true
			currentBatchContinuables[item] = true

			item:ContinueOnItemLoad(function()
				auctions[i] = { C_AuctionHouse.GetReplicateItemInfo(i) }
				allContinuables[item] = nil
				currentBatchContinuables[item] = nil

				-- 检查当前批次是否完成
				if not next(currentBatchContinuables) then
					-- 当前批次完成，继续下一批次
					scanIndex = batchEnd + 1
					if scanIndex < totalItems then
						-- 延迟执行下一批次，避免单帧处理过多
						C_Timer.After(0.1, function()
							ScanBatch(beginTime, allContinuables)
						end)
					else
						-- 所有批次完成，检查是否还有其他异步任务
						if not next(allContinuables) then
							OnScanComplete(beginTime)
						end
					end
				end
			end)
		end
	end

	-- 如果当前批次没有异步任务，直接继续下一批次
	if not batchHasAsync then
		scanIndex = batchEnd + 1
		if scanIndex < totalItems then
			-- 延迟执行下一批次
			C_Timer.After(0.1, function()
				ScanBatch(beginTime, allContinuables)
			end)
		else
			-- 所有批次完成
			if not next(allContinuables) then
				OnScanComplete(beginTime)
			end
		end
	end
end

local function ScanAuctions()
	local beginTime = debugprofilestop()
	local continuables = {}
	local hasAsyncItems = false
	wipe(auctions)

	-- 重置扫描状态
	scanIndex = 0
	totalItems = C_AuctionHouse.GetNumReplicateItems()
	currentBatchContinuables = {}

	print(format("AuctionSearch: 开始扫描 %d 件拍卖物品", totalItems))

	if AuctionSearchOverlay and AuctionSearchOverlay.SetPhase then
		AuctionSearchOverlay.SetPhase("scanning")
	end

	-- 如果物品数量超过限制，使用分批处理
	if totalItems > REPLICATE_ITEMS_PER_FRAME then
		print(format("AuctionSearch: 物品数量(%d)超过限制，启用分批处理模式", totalItems))
		ScanBatch(beginTime, continuables)
	else
		-- 物品数量在限制内，直接处理
		for i = 0, totalItems - 1 do
			auctions[i] = { C_AuctionHouse.GetReplicateItemInfo(i) }
			if not auctions[i][18] then                 -- hasAllInfo
				hasAsyncItems = true
				local item = Item:CreateFromItemID(auctions[i][17]) -- itemID
				continuables[item] = true

				item:ContinueOnItemLoad(function()
					auctions[i] = { C_AuctionHouse.GetReplicateItemInfo(i) }
					continuables[item] = nil
					-- 检查是否所有异步加载都完成了
					if not next(continuables) then
						OnScanComplete(beginTime)
					end
				end)
			end
		end

		-- 只有在完全没有异步加载任务时才直接完成
		if not hasAsyncItems then
			OnScanComplete(beginTime)
		end
	end
end

local function OnEvent(self, event, ...)
	if event == "ADDON_LOADED" then
		local addonName = ...
		if addonName == "AuctionSearchExample" then
			print("AuctionSearch: 插件已加载")

			-- 初始化数据库
			if not AuctionSearchDB then
				AuctionSearchDB = {
					auctions = {},
					lastScanTime = 0,
					settings = {
						maxHistoryDays = 7,
						maxRecordsPerDay = 1000
					}
				}
			end

			-- 启动时清理过期数据
			CleanOldData()

			-- 显示统计信息
			local stats = GetDatabaseStats()
			if stats.totalScans > 0 then
				print(format("AuctionSearch: 数据库包含 %d 次扫描记录，%d 件物品", stats.totalScans, stats.totalItems))
			end

			if AuctionSearchOverlay and AuctionSearchOverlay.Init then
				AuctionSearchOverlay.Init()
			end

			-- 取消注册ADDON_LOADED事件
			self:UnregisterEvent("ADDON_LOADED")
		end
	elseif event == "AUCTION_HOUSE_SHOW" then
		print("AuctionSearch: 拍卖行已打开，开始复制物品列表")
		if AuctionSearchOverlay and AuctionSearchOverlay.Init then
			AuctionSearchOverlay.Init()
		end
		if AuctionSearchOverlay and AuctionSearchOverlay.SetPhase then
			AuctionSearchOverlay.SetPhase("started")
		end
		C_AuctionHouse.ReplicateItems()
		initialQuery = true
	elseif event == "AUCTION_HOUSE_CLOSED" then
		if AuctionSearchOverlay and AuctionSearchOverlay.SetPhase then
			AuctionSearchOverlay.SetPhase("idle")
		end
	elseif event == "REPLICATE_ITEM_LIST_UPDATE" then
		if initialQuery then
			ScanAuctions()
			initialQuery = false
		end
	end
end

local f = CreateFrame("Frame")
f:RegisterEvent("ADDON_LOADED")
f:RegisterEvent("AUCTION_HOUSE_SHOW")
f:RegisterEvent("AUCTION_HOUSE_CLOSED")
f:RegisterEvent("REPLICATE_ITEM_LIST_UPDATE")
f:SetScript("OnEvent", OnEvent)
