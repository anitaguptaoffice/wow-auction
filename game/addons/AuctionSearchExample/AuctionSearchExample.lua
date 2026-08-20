local ADDON_NAME = "AuctionSearchExample"
local SCAN_BATCH_SIZE = 1000 -- 低于 replicate 接口约 2000 次/帧的经验上限
local BATCH_DETAIL_TIMEOUT_SECONDS = 1
local BATTLE_PET_CAGE_ITEM_ID = 82800
local MARKET_SCOPE_MAX_IN_FLIGHT = 32
local MARKET_SCOPE_LOOKUPS_PER_TICK = 64
local MARKET_SCOPE_REQUEST_TIMEOUT_SECONDS = 0.5
local MARKET_SCOPE_TOTAL_TIMEOUT_SECONDS = 180
local MARKET_SCOPE_TICK_SECONDS = 0.05

local waitingForReplicate = false
local auctionHouseOpen = false
local requestSerial = 0
local activeJob

AuctionSearchDB = AuctionSearchDB or {}

local function EnsureDatabase()
	AuctionSearchDB.auctions = AuctionSearchDB.auctions or {}
	AuctionSearchDB.lastScanTime = AuctionSearchDB.lastScanTime or 0
	AuctionSearchDB.settings = AuctionSearchDB.settings or {}
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

local function ReadItemMarketScope(itemID)
	if not itemID or not C_AuctionHouse.MakeItemKey or not C_AuctionHouse.GetItemKeyInfo then
		return nil
	end
	local keyOk, itemKey = pcall(C_AuctionHouse.MakeItemKey, itemID)
	if not keyOk or not itemKey then
		return nil
	end
	local infoOk, itemKeyInfo = pcall(C_AuctionHouse.GetItemKeyInfo, itemKey)
	if not infoOk or not itemKeyInfo or itemKeyInfo.isCommodity == nil then
		return nil
	end
	return itemKeyInfo.isCommodity and "region" or "realm"
end

local function GetScanContext()
	local regionID
	if C_BattleNet and C_BattleNet.GetGameAccountInfoByGUID and UnitGUID("player") then
		local ok, gameAccountInfo = pcall(C_BattleNet.GetGameAccountInfoByGUID, UnitGUID("player"))
		if ok and gameAccountInfo then
			regionID = gameAccountInfo.regionID
		end
	end
	if not regionID and GetCurrentRegion then
		local ok, value = pcall(GetCurrentRegion)
		if ok then
			regionID = value
		end
	end
	return {
		realmName = GetRealmName and GetRealmName() or nil,
		normalizedRealmName = GetNormalizedRealmName and GetNormalizedRealmName() or nil,
		realmID = GetRealmID and GetRealmID() or nil,
		regionID = regionID,
		regionName = GetCurrentRegionName and GetCurrentRegionName() or nil,
	}
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
	-- 先收集唯一 ItemID；核心拍卖行读取完成后再以有界并发补全市场范围。
	if record.itemID and not job.marketScopeSeen[record.itemID] then
		job.marketScopeSeen[record.itemID] = true
		table.insert(job.marketScopeItemIDs, record.itemID)
	end
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
	job.marketScopeState = nil
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

local function EnsureAllRecords(job)
	-- 防御性补齐：即使某个异步回调永不返回，也绝不丢失该拍卖行。
	for index = 0, job.total - 1 do
		if not job.records[index + 1] then
			StoreRecord(job, index, ReadReplicateInfo(job, index))
		end
	end
end

local function FinalizeMarketScopeCounts(job)
	local counts = { region = 0, realm = 0, unknown = 0 }
	for _, itemID in ipairs(job.marketScopeItemIDs) do
		local scope = job.itemMarketScopes[itemID]
		if scope ~= "region" and scope ~= "realm" then
			scope = "unknown"
			job.itemMarketScopes[itemID] = scope
		end
		counts[scope] = counts[scope] + 1
	end
	return counts
end

local function CommitJob(job)
	if activeJob ~= job or job.finished then
		return
	end
	job.finished = true

	EnsureAllRecords(job)
	local marketScopeCounts = FinalizeMarketScopeCounts(job)

	local currentTime = time()
	local dateStr = GetDateString()

	local elapsedMs = debugprofilestop() - job.beginMs
	local scanContext = GetScanContext()
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
		marketScopeRegionCount = marketScopeCounts.region,
		marketScopeRealmCount = marketScopeCounts.realm,
		marketScopeUnknownCount = marketScopeCounts.unknown,
		durationMs = elapsedMs,
		realmName = scanContext.realmName,
		normalizedRealmName = scanContext.normalizedRealmName,
		realmID = scanContext.realmID,
		regionID = scanContext.regionID,
		regionName = scanContext.regionName,
		itemMarketScopes = job.itemMarketScopes,
		items = job.records,
	}
	-- SavedVariables 只承担一次交接快照；历史追加由游戏外同步程序/数据库负责。
	AuctionSearchDB.auctions = {
		[dateStr] = {
			timestamp = currentTime,
			scans = { scanRecord },
		},
	}
	AuctionSearchDB.lastScanTime = currentTime
	AuctionSearchDB.lastScan = {
		itemCount = job.total,
		recordCount = #job.records,
		linkedItemCount = job.linkedItems,
		incompleteInfoCount = incompleteCount,
		missingCoreCount = missingCoreCount,
		apiErrorCount = apiErrorCount,
		marketScopeRegionCount = marketScopeCounts.region,
		marketScopeRealmCount = marketScopeCounts.realm,
		marketScopeUnknownCount = marketScopeCounts.unknown,
		durationMs = elapsedMs,
		realmName = scanContext.realmName,
		normalizedRealmName = scanContext.normalizedRealmName,
		realmID = scanContext.realmID,
		regionID = scanContext.regionID,
		regionName = scanContext.regionName,
	}
	AuctionSearchDB.lastError = nil

	activeJob = nil
	OverlayCall(
		"SetComplete",
		job.total,
		elapsedMs,
		job.linkedItems,
		missingCoreCount,
		incompleteCount,
		apiErrorCount,
		marketScopeCounts.region,
		marketScopeCounts.realm,
		marketScopeCounts.unknown
	)
end

local TickMarketScopeResolution

local function ScheduleMarketScopeTick(job, state, delay)
	if activeJob ~= job or job.finished or job.marketScopeState ~= state or state.tickScheduled then
		return
	end
	state.tickScheduled = true
	C_Timer.After(delay or MARKET_SCOPE_TICK_SECONDS, function()
		state.tickScheduled = false
		if activeJob ~= job or job.finished or job.marketScopeState ~= state then
			return
		end
		local ok, reason = pcall(TickMarketScopeResolution, job, state)
		if not ok then
			AbortJob(job, reason)
		end
	end)
end

local function CompleteMarketScopeItem(job, state, itemID, scope)
	local pending = state.inFlight[itemID]
	if not pending then
		return false
	end
	state.inFlight[itemID] = nil
	state.inFlightCount = math.max(0, state.inFlightCount - 1)
	job.itemMarketScopes[itemID] = scope or "unknown"
	state.completed = state.completed + 1
	return true
end

local function UpdateMarketScopeProgress(job, state)
	OverlayCall(
		"SetProgress",
		job.total,
		job.total,
		format(
			"正在补全市场范围：%d / %d 个物品（最多同时等待 %d 个）",
			state.completed,
			state.total,
			MARKET_SCOPE_MAX_IN_FLIGHT
		)
	)
end

local function FinishMarketScopeResolution(job, state)
	if activeJob ~= job or job.finished or job.marketScopeState ~= state then
		return
	end
	for itemID in pairs(state.inFlight) do
		job.itemMarketScopes[itemID] = "unknown"
	end
	for queueIndex = state.nextIndex, state.total do
		job.itemMarketScopes[job.marketScopeItemIDs[queueIndex]] = "unknown"
	end
	state.inFlight = {}
	state.inFlightCount = 0
	state.completed = state.total
	job.marketScopeState = nil
	CommitJob(job)
end

TickMarketScopeResolution = function(job, state)
	if activeJob ~= job or job.finished or job.marketScopeState ~= state then
		return
	end
	local now = GetTime()
	if now >= state.hardDeadline then
		FinishMarketScopeResolution(job, state)
		return
	end

	-- 事件未返回时释放槽位，避免某个 ItemID 永久阻塞整个队列。
	local expired = {}
	for itemID, pending in pairs(state.inFlight) do
		if now >= pending.deadline then
			table.insert(expired, itemID)
		end
	end
	for _, itemID in ipairs(expired) do
		local scope = ReadItemMarketScope(itemID)
		CompleteMarketScopeItem(job, state, itemID, scope or "unknown")
	end

	local lookupCount = 0
	while state.nextIndex <= state.total
		and state.inFlightCount < MARKET_SCOPE_MAX_IN_FLIGHT
		and lookupCount < MARKET_SCOPE_LOOKUPS_PER_TICK do
		local itemID = job.marketScopeItemIDs[state.nextIndex]
		state.nextIndex = state.nextIndex + 1
		lookupCount = lookupCount + 1
		local scope = ReadItemMarketScope(itemID)
		if scope then
			job.itemMarketScopes[itemID] = scope
			state.completed = state.completed + 1
		else
			state.inFlight[itemID] = { deadline = now + MARKET_SCOPE_REQUEST_TIMEOUT_SECONDS }
			state.inFlightCount = state.inFlightCount + 1
		end
	end

	UpdateMarketScopeProgress(job, state)
	if state.nextIndex > state.total and state.inFlightCount == 0 then
		job.marketScopeState = nil
		CommitJob(job)
		return
	end
	ScheduleMarketScopeTick(job, state, MARKET_SCOPE_TICK_SECONDS)
end

local function StartMarketScopeResolution(job)
	if activeJob ~= job or job.finished then
		return
	end
	EnsureAllRecords(job)
	local total = #job.marketScopeItemIDs
	if total == 0 or not C_AuctionHouse.MakeItemKey or not C_AuctionHouse.GetItemKeyInfo then
		CommitJob(job)
		return
	end
	local state = {
		total = total,
		nextIndex = 1,
		completed = 0,
		inFlight = {},
		inFlightCount = 0,
		hardDeadline = GetTime() + MARKET_SCOPE_TOTAL_TIMEOUT_SECONDS,
		tickScheduled = false,
	}
	job.marketScopeState = state
	UpdateMarketScopeProgress(job, state)
	ScheduleMarketScopeTick(job, state, 0)
end

local function HandleItemKeyInfoReceived(itemID)
	local job = activeJob
	local state = job and job.marketScopeState
	itemID = tonumber(itemID)
	if not job or job.finished or not state or not itemID or not state.inFlight[itemID] then
		return
	end
	local scope = ReadItemMarketScope(itemID)
	if scope and CompleteMarketScopeItem(job, state, itemID, scope) then
		ScheduleMarketScopeTick(job, state, 0)
	end
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
		StartMarketScopeResolution(job)
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

	-- 只有服务器真正返回新快照后才释放旧快照，避免一次被限流的请求误删上次成功数据。
	AuctionSearchDB.auctions = {}
	AuctionSearchDB.lastScanTime = 0
	AuctionSearchDB.lastScan = nil

	local job = {
		id = requestSerial,
		total = total,
		nextIndex = 0,
		processed = 0,
		records = {},
		itemMarketScopes = {},
		marketScopeItemIDs = {},
		marketScopeSeen = {},
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

local function RequestReplicateSnapshot()
	if not auctionHouseOpen then
		OverlayCall("SetPhase", "ready", "请先打开拍卖行，再点击开始扫描")
		return
	end
	if activeJob or waitingForReplicate then
		return
	end
	requestSerial = requestSerial + 1
	waitingForReplicate = true
	OverlayCall("SetPhase", "started")
	local ok, reason = pcall(C_AuctionHouse.ReplicateItems)
	if not ok then
		waitingForReplicate = false
		AuctionSearchDB.lastError = { timestamp = time(), message = tostring(reason) }
		OverlayCall("SetPhase", "error", "客户端拒绝了全量快照请求；本次不会自动重试")
		return
	end
	local thisRequest = requestSerial
	C_Timer.After(20, function()
		if waitingForReplicate and requestSerial == thisRequest and not activeJob then
			OverlayCall(
				"SetPhase",
				"started",
				"仍在等待服务器；本次不会自动重试，如遇限流请稍后手动再次点击"
			)
		end
	end)
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
	elseif command == "scan" then
		RequestReplicateSnapshot()
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
		print("  /as scan - 在拍卖行打开时手动开始扫描")
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
		OverlayCall("Init")
		OverlayCall("SetScanCallback", RequestReplicateSnapshot)
		self:UnregisterEvent("ADDON_LOADED")
	elseif event == "PLAYER_ENTERING_WORLD" then
		if not activeJob and not waitingForReplicate then
			OverlayCall("Init")
			OverlayCall("SetPhase", "ready")
		end
	elseif event == "AUCTION_HOUSE_SHOW" then
		auctionHouseOpen = true
		OverlayCall("Init")
		if not activeJob and not waitingForReplicate then
			OverlayCall("SetPhase", "ready", "拍卖行已打开；点击右上角“开始扫描”，或输入 /as scan")
		end
	elseif event == "AUCTION_HOUSE_CLOSED" then
		auctionHouseOpen = false
		if not activeJob then
			waitingForReplicate = false
			requestSerial = requestSerial + 1
			OverlayCall("SetPhase", "ready", "请打开拍卖行，再手动开始扫描")
		end
	elseif event == "REPLICATE_ITEM_LIST_UPDATE" and waitingForReplicate then
		waitingForReplicate = false
		StartScanSafely()
	elseif event == "ITEM_KEY_ITEM_INFO_RECEIVED" then
		HandleItemKeyInfoReceived(...)
	end
end

local eventFrame = CreateFrame("Frame")
eventFrame:RegisterEvent("ADDON_LOADED")
eventFrame:RegisterEvent("PLAYER_ENTERING_WORLD")
eventFrame:RegisterEvent("AUCTION_HOUSE_SHOW")
eventFrame:RegisterEvent("AUCTION_HOUSE_CLOSED")
eventFrame:RegisterEvent("REPLICATE_ITEM_LIST_UPDATE")
eventFrame:RegisterEvent("ITEM_KEY_ITEM_INFO_RECEIVED")
eventFrame:SetScript("OnEvent", OnEvent)
