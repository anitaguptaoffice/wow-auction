local ADDON_NAME = "AuctionSearchExample"
local SCAN_BATCH_SIZE = 1000 -- 低于 replicate 接口约 2000 次/帧的经验上限
local BATCH_DETAIL_TIMEOUT_SECONDS = 1
local BATTLE_PET_CAGE_ITEM_ID = 82800

local waitingForReplicate = false
local requestSerial = 0
local activeJob

AuctionSearchDB = AuctionSearchDB or {}

local function EnsureDatabase()
	AuctionSearchDB.auctions = AuctionSearchDB.auctions or {}
	AuctionSearchDB.lastScanTime = AuctionSearchDB.lastScanTime or 0
	AuctionSearchDB.settings = AuctionSearchDB.settings or {}
	AuctionSearchDB.settings.maxHistoryDays = AuctionSearchDB.settings.maxHistoryDays or 7
	-- 单次全量快照体积很大；同一天默认只保留最新一份，避免 SavedVariables 无限膨胀。
	AuctionSearchDB.settings.maxScansPerDay = AuctionSearchDB.settings.maxScansPerDay or 1
end

local function GetDateString()
	return date("%Y-%m-%d")
end

local function OverlayCall(method, ...)
	local callback = AuctionSearchOverlay and AuctionSearchOverlay[method]
	if type(callback) ~= "function" then
		return false
	end
	return pcall(callback, ...)
end

local function CleanOldData()
	local currentTime = time()
	local maxAge = AuctionSearchDB.settings.maxHistoryDays * 24 * 60 * 60
	for dateStr, dayData in pairs(AuctionSearchDB.auctions) do
		if dayData.timestamp and (currentTime - dayData.timestamp) > maxAge then
			AuctionSearchDB.auctions[dateStr] = nil
		end
	end
end

-- Replicate 接口沿用旧 Auction API 的 1..4 值，而不是 AuctionHouseTimeLeftBand 的 0..3。
local function TimeLeftLabel(value)
	local labels = {
		[1] = "短（30 分钟内）",
		[2] = "中（2 小时内）",
		[3] = "长（12 小时内）",
		[4] = "很长（48 小时内）",
	}
	return labels[tonumber(value)] or tostring(value or "?")
end

local function GetItemInfoDebug(itemID)
	local itemName, _, itemQuality, itemLevel, _, itemType, itemSubType,
		_, itemEquipLoc, _, _, classID, subclassID = C_Item.GetItemInfo(itemID)
	local result = {}
	if itemName then
		result.name = itemName
		result.quality = itemQuality
		result.itemLevel = itemLevel
		result.itemType = itemType
		result.itemSubType = itemSubType
		result.equipLoc = itemEquipLoc
		result.classID = classID
		result.subclassID = subclassID
	end
	return result
end

local function MarkApiError(job, index)
	if job and job.apiErrors then
		job.apiErrors[index + 1] = true
	end
end

local function ReadReplicateInfo(job, index)
	local values = { pcall(C_AuctionHouse.GetReplicateItemInfo, index) }
	if not values[1] then
		MarkApiError(job, index)
		return {}
	end
	local info = {}
	for valueIndex = 1, 18 do
		info[valueIndex] = values[valueIndex + 1]
	end
	return info
end

local function ReadReplicateValue(job, index, api)
	local ok, value = pcall(api, index)
	if not ok then
		MarkApiError(job, index)
		return nil
	end
	return value
end

local function BuildRecord(job, index, info)
	local itemLink = ReadReplicateValue(job, index, C_AuctionHouse.GetReplicateItemLink)
	local timeLeft = ReadReplicateValue(job, index, C_AuctionHouse.GetReplicateItemTimeLeft)
	local creatureID, displayID
	if info[17] == BATTLE_PET_CAGE_ITEM_ID then
		local ok
		ok, creatureID, displayID = pcall(C_AuctionHouse.GetReplicateItemBattlePetInfo, index)
		if not ok then
			creatureID, displayID = nil, nil
			MarkApiError(job, index)
		end
	end

	return {
		itemID = info[17],
		name = info[1],
		texture = info[2],
		quantity = info[3],
		qualityID = info[4],
		usable = info[5],
		level = info[6],
		levelType = info[7],
		minBid = info[8],
		minIncrement = info[9],
		buyoutAmount = info[10],
		bidAmount = info[11],
		highBidder = info[12],
		bidderFullName = info[13],
		owner = info[14],
		ownerFullName = info[15],
		saleStatus = info[16],
		hasAllInfo = info[18] and true or false,
		itemLink = itemLink,
		timeLeftBand = timeLeft,
		battlePetCreatureID = creatureID,
		battlePetDisplayID = displayID,
	}, itemLink ~= nil
end

local function StoreRecord(job, index, info)
	local record, hasLink = BuildRecord(job, index, info)
	local slot = index + 1
	local previouslyLinked = job.linkedBySlot[slot] == true
	job.records[slot] = record
	job.linkedBySlot[slot] = hasLink
	if hasLink and not previouslyLinked then
		job.linkedItems = job.linkedItems + 1
	elseif previouslyLinked and not hasLink then
		job.linkedItems = job.linkedItems - 1
	end
	if not record.hasAllInfo then
		job.incompleteInfo[slot] = true
	else
		job.incompleteInfo[slot] = nil
	end
	if record.itemID == nil
		or record.name == nil
		or record.quantity == nil
		or record.minBid == nil
		or record.buyoutAmount == nil
		or record.bidAmount == nil
		or record.timeLeftBand == nil then
		job.missingCore[slot] = true
	else
		job.missingCore[slot] = nil
	end
end

local function CancelBatchContinuations(batch)
	if not batch then
		return
	end
	local cancelByIndex = batch.cancelByIndex or {}
	batch.cancelByIndex = {}
	for _, cancel in pairs(cancelByIndex) do
		if type(cancel) == "function" then
			pcall(cancel)
		end
	end
	-- 原地清空，确保已经捕获旧表的回调也看不到未决项；没有 cancelFunc 的索引同样会被清理。
	if batch.unresolved then
		wipe(batch.unresolved)
	else
		batch.unresolved = {}
	end
	batch.pending = 0
end

local function AbortJob(job, reason)
	if activeJob ~= job then
		return
	end
	job.finished = true
	if job.activeBatch then
		job.activeBatch.done = true
		CancelBatchContinuations(job.activeBatch)
	end
	activeJob = nil
	AuctionSearchDB.lastError = {
		timestamp = time(),
		message = tostring(reason or "unknown error"),
	}
	OverlayCall("SetPhase", "error", "扫描遇到客户端异常，任务已安全停止；请关闭并重新打开拍卖行")
end

local function CommitJob(job)
	if activeJob ~= job or job.finished then
		return
	end
	job.finished = true

	-- 防御性补齐：即使某个异步回调永不返回，也绝不丢失该拍卖行。
	for index = 0, job.total - 1 do
		if not job.records[index + 1] then
			StoreRecord(job, index, ReadReplicateInfo(job, index))
		end
	end

	local currentTime = time()
	local dateStr = GetDateString()
	local dayData = AuctionSearchDB.auctions[dateStr]
	if not dayData then
		dayData = { scans = {}, timestamp = currentTime }
		AuctionSearchDB.auctions[dateStr] = dayData
	end
	dayData.scans = dayData.scans or {}
	dayData.timestamp = currentTime

	local limit = math.max(1, tonumber(AuctionSearchDB.settings.maxScansPerDay) or 1)
	while #dayData.scans >= limit do
		table.remove(dayData.scans, 1)
	end

	local elapsedMs = debugprofilestop() - job.beginMs
	local incompleteCount = 0
	for _ in pairs(job.incompleteInfo) do
		incompleteCount = incompleteCount + 1
	end
	local missingCoreCount = 0
	for _ in pairs(job.missingCore) do
		missingCoreCount = missingCoreCount + 1
	end
	local apiErrorCount = 0
	for _ in pairs(job.apiErrors) do
		apiErrorCount = apiErrorCount + 1
	end
	local scanRecord = {
		timestamp = currentTime,
		itemCount = job.total,
		recordCount = #job.records,
		linkedItemCount = job.linkedItems,
		incompleteInfoCount = incompleteCount,
		missingCoreCount = missingCoreCount,
		apiErrorCount = apiErrorCount,
		durationMs = elapsedMs,
		items = job.records,
	}
	table.insert(dayData.scans, scanRecord)
	AuctionSearchDB.lastScanTime = currentTime
	AuctionSearchDB.lastScan = {
		itemCount = job.total,
		recordCount = #job.records,
		linkedItemCount = job.linkedItems,
		incompleteInfoCount = incompleteCount,
		missingCoreCount = missingCoreCount,
		apiErrorCount = apiErrorCount,
		durationMs = elapsedMs,
	}
	AuctionSearchDB.lastError = nil
	CleanOldData()

	activeJob = nil
	OverlayCall(
		"SetComplete",
		job.total,
		elapsedMs,
		job.linkedItems,
		missingCoreCount,
		incompleteCount,
		apiErrorCount
	)
end

local ProcessNextBatch
local RunNextBatch

local function FinishBatch(job, batch)
	if activeJob ~= job or job.finished or batch.done or batch.enumerating or batch.pending > 0 then
		return
	end
	batch.done = true
	job.activeBatch = nil
	job.processed = batch.lastIndex + 1
	OverlayCall("SetProgress", job.processed, job.total)
	job.nextIndex = batch.lastIndex + 1
	if job.nextIndex >= job.total then
		CommitJob(job)
	else
		C_Timer.After(0, function()
			if activeJob == job and not job.finished then
				RunNextBatch(job)
			end
		end)
	end
end

local function ResolveBatchIndex(job, batch, index, cancelPending)
	if activeJob ~= job or job.finished or batch.done or not batch.unresolved[index] then
		return
	end
	batch.unresolved[index] = nil
	local cancel = batch.cancelByIndex[index]
	batch.cancelByIndex[index] = nil
	if cancelPending and type(cancel) == "function" then
		pcall(cancel)
	end
	StoreRecord(job, index, ReadReplicateInfo(job, index))
	batch.pending = math.max(0, batch.pending - 1)
	FinishBatch(job, batch)
end

local function ResolveBatchIndexSafely(job, batch, index, cancelPending)
	local ok, reason = pcall(ResolveBatchIndex, job, batch, index, cancelPending)
	if not ok then
		AbortJob(job, reason)
	end
end

ProcessNextBatch = function(job)
	if activeJob ~= job or job.finished then
		return
	end
	local firstIndex = job.nextIndex
	local lastIndex = math.min(firstIndex + SCAN_BATCH_SIZE - 1, job.total - 1)
	local batch = {
		firstIndex = firstIndex,
		lastIndex = lastIndex,
		pending = 0,
		enumerating = true,
		done = false,
		unresolved = {},
		cancelByIndex = {},
	}
	job.activeBatch = batch

	for index = firstIndex, lastIndex do
		if activeJob ~= job or job.finished then
			break
		end
		local currentIndex = index
		local info = ReadReplicateInfo(job, currentIndex)
		StoreRecord(job, currentIndex, info)
		if info[17] and not info[18] then
			batch.pending = batch.pending + 1
			batch.unresolved[currentIndex] = true
			local ok = pcall(function()
				local item = Item:CreateFromItemID(info[17])
				if not item.ContinueWithCancelOnItemLoad then
					error("ContinueWithCancelOnItemLoad is unavailable")
				end
				local cancel = item:ContinueWithCancelOnItemLoad(function()
					ResolveBatchIndexSafely(job, batch, currentIndex, false)
				end)
				-- 回调可能同步执行；只有仍未完成的索引才需要保存 cancel。
				if batch.unresolved[currentIndex] and type(cancel) == "function" then
					batch.cancelByIndex[currentIndex] = cancel
				elseif type(cancel) == "function" then
					pcall(cancel)
				end
			end)
			if not ok then
				MarkApiError(job, currentIndex)
				ResolveBatchIndexSafely(job, batch, currentIndex, true)
			end
		end
		-- ContinueWithCancelOnItemLoad 可能同步回调并在异常时中止 job；不要再枚举下一行。
		if activeJob ~= job or job.finished then
			break
		end
	end
	if activeJob ~= job or job.finished then
		batch.enumerating = false
		CancelBatchContinuations(batch)
		return
	end
	batch.enumerating = false
	FinishBatch(job, batch)

	if batch.pending > 0 then
		C_Timer.After(BATCH_DETAIL_TIMEOUT_SECONDS, function()
			local ok, reason = pcall(function()
				if activeJob ~= job or job.finished or batch.done then
					return
				end
				-- 部分物品可能永远没有完整缓存；保留其核心价格行并继续，不让整次扫描卡死。
				local unresolved = {}
				for index in pairs(batch.unresolved) do
					table.insert(unresolved, index)
				end
				for _, index in ipairs(unresolved) do
					ResolveBatchIndex(job, batch, index, true)
				end
			end)
			if not ok then
				AbortJob(job, reason)
			end
		end)
	end
end

RunNextBatch = function(job)
	if activeJob ~= job or job.finished then
		return
	end
	local ok, reason = pcall(ProcessNextBatch, job)
	if not ok then
		AbortJob(job, reason)
	end
end

local function StartScan()
	local ok, total = pcall(C_AuctionHouse.GetNumReplicateItems)
	if not ok or not total or total <= 0 then
		OverlayCall("SetPhase", "error", "服务器返回了空快照，请等待接口限流结束后重试")
		return
	end

	local job = {
		id = requestSerial,
		total = total,
		nextIndex = 0,
		processed = 0,
		records = {},
		linkedBySlot = {},
		linkedItems = 0,
		incompleteInfo = {},
		missingCore = {},
		apiErrors = {},
		finished = false,
		beginMs = debugprofilestop(),
	}
	activeJob = job
	-- OverlayCall 会隔离 UI 错误；即使面板绘制失败，也必须继续采集完整快照。
	OverlayCall("SetPhase", "scanning")
	OverlayCall("SetProgress", 0, total)
	RunNextBatch(job)
end

local function StartScanSafely()
	local ok, reason = pcall(StartScan)
	if ok then
		return
	end
	if activeJob then
		AbortJob(activeJob, reason)
	else
		AuctionSearchDB.lastError = { timestamp = time(), message = tostring(reason) }
		OverlayCall("SetPhase", "error", "启动扫描时遇到客户端异常，请关闭并重新打开拍卖行")
	end
end

local function GetDatabaseStats()
	local totalScans, totalItems, totalLinks = 0, 0, 0
	local oldestDate, newestDate
	for dateStr, dayData in pairs(AuctionSearchDB.auctions) do
		for _, scan in ipairs(dayData.scans or {}) do
			totalScans = totalScans + 1
			totalItems = totalItems + #(scan.items or {})
			totalLinks = totalLinks + (scan.linkedItemCount or 0)
		end
		oldestDate = (not oldestDate or dateStr < oldestDate) and dateStr or oldestDate
		newestDate = (not newestDate or dateStr > newestDate) and dateStr or newestDate
	end
	return {
		totalScans = totalScans,
		totalItems = totalItems,
		totalLinks = totalLinks,
		oldestDate = oldestDate,
		newestDate = newestDate,
		lastScanTime = AuctionSearchDB.lastScanTime,
	}
end

local function GetAuctionHistory(itemID, days)
	local results = {}
	local timeLimit = time() - ((days or 7) * 24 * 60 * 60)
	for dateStr, dayData in pairs(AuctionSearchDB.auctions) do
		if (dayData.timestamp or 0) >= timeLimit then
			for _, scan in ipairs(dayData.scans or {}) do
				for _, item in ipairs(scan.items or {}) do
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
	table.sort(results, function(a, b) return a.timestamp > b.timestamp end)
	return results
end

local function HandleSlashCommand(msg)
	local command, arg = msg:match("^(%S*)%s*(.-)$")
	command = command:lower()
	if command == "stats" then
		local stats = GetDatabaseStats()
		print("=== AuctionSearch 数据库统计 ===")
		print(format("总扫描次数: %d", stats.totalScans))
		print(format("总拍卖记录: %d（含 itemLink: %d）", stats.totalItems, stats.totalLinks))
		print(format("数据范围: %s 到 %s", stats.oldestDate or "无", stats.newestDate or "无"))
		if stats.lastScanTime > 0 then
			print(format("最后扫描时间: %s", date("%Y-%m-%d %H:%M:%S", stats.lastScanTime)))
		end
	elseif command == "history" then
		local itemID = tonumber(arg)
		if not itemID then
			print("用法: /auctionsearch history <物品ID>")
			return
		end
		local history = GetAuctionHistory(itemID, 7)
		print(format("=== 物品 %d 的拍卖历史（最近 7 天）===", itemID))
		for index = 1, math.min(10, #history) do
			local item = history[index]
			print(format(
				"%s: 一口 %s | 起拍 %s | 剩余 %s | 数量 %d",
				date("%m-%d %H:%M", item.timestamp),
				item.buyoutAmount and C_CurrencyInfo.GetCoinTextureString(item.buyoutAmount) or "无",
				item.minBid and C_CurrencyInfo.GetCoinTextureString(item.minBid) or "无",
				TimeLeftLabel(item.timeLeftBand),
				item.quantity or 0
			))
		end
	elseif command == "clear" then
		AuctionSearchDB.auctions = {}
		AuctionSearchDB.lastScanTime = 0
		AuctionSearchDB.lastScan = nil
		print("AuctionSearch: 已清空所有保存的数据")
	elseif command == "test" then
		local testItemID = tonumber(arg) or 238033
		print(format("=== 测试物品 %d ===", testItemID))
		for key, value in pairs(GetItemInfoDebug(testItemID)) do
			print(format("  %s: %s", key, tostring(value)))
		end
	elseif command == "uitest" then
		local phase = arg ~= "" and strlower(arg) or "scanning"
		OverlayCall("Init")
		OverlayCall("SetPhase", phase)
		if phase == "scanning" then
			OverlayCall("SetProgress", 157500, 384992)
		end
	else
		print("AuctionSearch 命令:")
		print("  /as stats - 显示数据库统计")
		print("  /as history <物品ID> - 显示最近历史")
		print("  /as test [物品ID] - 调试物品缓存")
		print("  /as clear - 清空保存的数据")
		print("  /as uitest [ready|started|scanning|complete|warning|error] - 测试状态面板")
	end
end

SLASH_AUCTIONSEARCH1 = "/auctionsearch"
SLASH_AUCTIONSEARCH2 = "/as"
SlashCmdList["AUCTIONSEARCH"] = HandleSlashCommand

local function OnEvent(self, event, ...)
	if event == "ADDON_LOADED" then
		if ... ~= ADDON_NAME then
			return
		end
		EnsureDatabase()
		CleanOldData()
		OverlayCall("Init")
		self:UnregisterEvent("ADDON_LOADED")
	elseif event == "PLAYER_ENTERING_WORLD" then
		if not activeJob and not waitingForReplicate then
			OverlayCall("Init")
			OverlayCall("SetPhase", "ready")
		end
	elseif event == "AUCTION_HOUSE_SHOW" then
		if activeJob or waitingForReplicate then
			return
		end
		requestSerial = requestSerial + 1
		waitingForReplicate = true
		OverlayCall("Init")
		OverlayCall("SetPhase", "started")
		local ok, reason = pcall(C_AuctionHouse.ReplicateItems)
		if not ok then
			waitingForReplicate = false
			AuctionSearchDB.lastError = { timestamp = time(), message = tostring(reason) }
			OverlayCall("SetPhase", "error", "客户端拒绝了全量快照请求，请关闭拍卖行后重试")
		end
		local thisRequest = requestSerial
		C_Timer.After(20, function()
			if waitingForReplicate and requestSerial == thisRequest and not activeJob then
				OverlayCall(
					"SetPhase",
					"started",
					"仍在等待服务器；全量接口可能处于 15 分钟限流，请保持拍卖行开启"
				)
			end
		end)
	elseif event == "AUCTION_HOUSE_CLOSED" then
		if not activeJob then
			waitingForReplicate = false
			requestSerial = requestSerial + 1
			OverlayCall("SetPhase", "ready")
		end
	elseif event == "REPLICATE_ITEM_LIST_UPDATE" and waitingForReplicate then
		waitingForReplicate = false
		StartScanSafely()
	end
end

local eventFrame = CreateFrame("Frame")
eventFrame:RegisterEvent("ADDON_LOADED")
eventFrame:RegisterEvent("PLAYER_ENTERING_WORLD")
eventFrame:RegisterEvent("AUCTION_HOUSE_SHOW")
eventFrame:RegisterEvent("AUCTION_HOUSE_CLOSED")
eventFrame:RegisterEvent("REPLICATE_ITEM_LIST_UPDATE")
eventFrame:SetScript("OnEvent", OnEvent)
