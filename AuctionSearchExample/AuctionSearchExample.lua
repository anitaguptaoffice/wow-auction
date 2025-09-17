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

-- 获取物品详细信息的函数
local function GetItemDetails(itemID, itemLink)
	local itemDetails = {}

	-- 获取基本物品信息
	local itemName, itemLinkFull, itemQuality, itemLevel, itemMinLevel, itemType, itemSubType,
	itemStackCount, itemEquipLoc, itemTexture, sellPrice, classID, subclassID, bindType,
	expacID, setID, isCraftingReagent = C_Item.GetItemInfo(itemID)

	if itemName then
		itemDetails.name = itemName
		itemDetails.quality = itemQuality -- 0=灰色, 1=白色, 2=绿色, 3=蓝色, 4=紫色, 5=橙色
		itemDetails.itemLevel = itemLevel
		itemDetails.itemType = itemType
		itemDetails.itemSubType = itemSubType
		itemDetails.equipLoc = itemEquipLoc
		itemDetails.classID = classID
		itemDetails.subclassID = subclassID
	end

	-- 如果有itemLink，解析更详细信息
	if itemLink then
		-- 解析itemLink获取宝石、附魔、词缀等信息
		-- itemLink格式: |cffffffff|Hitem:itemID:enchant:gem1:gem2:gem3:gem4:suffixID:uniqueID:level:specializationID:upgradeID:instanceDifficultyID:numBonusIDs:bonusID1:bonusID2:...|h[name]|h|r
		local linkParts = { strsplit(":", itemLink) }
		if #linkParts >= 15 then
			itemDetails.enchantID = tonumber(linkParts[3]) or 0
			itemDetails.suffixID = tonumber(linkParts[8]) or 0
			itemDetails.upgradeID = tonumber(linkParts[12]) or 0
			itemDetails.difficultyID = tonumber(linkParts[13]) or 0
			itemDetails.numBonusIDs = tonumber(linkParts[14]) or 0

			-- 解析bonusIDs (词缀)
			itemDetails.bonusIDs = {}
			if itemDetails.numBonusIDs > 0 then
				for i = 1, itemDetails.numBonusIDs do
					local bonusID = tonumber(linkParts[14 + i])
					if bonusID then
						table.insert(itemDetails.bonusIDs, bonusID)
					end
				end
			end
		end
	end

	return itemDetails
end

-- 判断是否为团本装备
local function IsRaidGear(itemDetails, itemID)
	-- 特殊处理已知的团本物品ID
	local knownRaidItems = {
		[238033] = true, -- 添加您确认的团本物品
		-- 可以继续添加其他已知的团本物品ID
	}

	-- 如果是已知的团本物品，直接返回true
	if knownRaidItems[itemID] then
		print(format("DEBUG: 物品 %d 在已知团本物品列表中", itemID))
		return true
	end

	-- 添加调试信息
	if itemID == 238033 then
		print("=== DEBUG: 分析物品 238033 ===")
		print(format("classID: %s", tostring(itemDetails.classID)))
		print(format("quality: %s", tostring(itemDetails.quality)))
		print(format("itemLevel: %s", tostring(itemDetails.itemLevel)))
		print(format("itemType: %s", tostring(itemDetails.itemType)))
		print(format("itemSubType: %s", tostring(itemDetails.itemSubType)))
		if itemDetails.bonusIDs then
			print(format("bonusIDs count: %d", #itemDetails.bonusIDs))
			for i, bonusID in ipairs(itemDetails.bonusIDs) do
				print(format("  bonusID[%d]: %d", i, bonusID))
			end
		else
			print("bonusIDs: nil")
		end
		print(format("difficultyID: %s", tostring(itemDetails.difficultyID)))
	end

	-- 检查装备类型和品质
	if not itemDetails.classID or not itemDetails.quality then
		if itemID == 238033 then
			print("DEBUG: 物品信息不完整 (classID或quality为空)")
		end
		return false
	end

	-- 装备类型：2=武器, 4=护甲
	if itemDetails.classID ~= 2 and itemDetails.classID ~= 4 then
		if itemID == 238033 then
			print(format("DEBUG: 物品类型不符 (classID=%d, 需要2或4)", itemDetails.classID))
		end
		return false
	end

	-- 品质检查：3=蓝色(稀有), 4=紫色(史诗), 5=橙色(传说)
	if itemDetails.quality < 3 then
		if itemID == 238033 then
			print(format("DEBUG: 物品品质不符 (quality=%d, 需要>=3)", itemDetails.quality))
		end
		return false
	end

	-- 检查装等范围（团本装备通常装等较高）
	if itemDetails.itemLevel and itemDetails.itemLevel < 620 then -- 根据当前版本调整
		if itemID == 238033 then
			print(format("DEBUG: 装等不符 (itemLevel=%d, 需要>=400)", itemDetails.itemLevel))
		end
		return false
	end

	-- 检查bonusID来判断来源（团本特有的bonusID）
	if itemDetails.bonusIDs then
		for _, bonusID in ipairs(itemDetails.bonusIDs) do
			-- 这些是一些常见的团本bonusID，需要根据当前版本更新
			if bonusID == 10844 or bonusID == 10353 or bonusID == 10355 or bonusID == 10356 then -- LFR, Normal, Heroic, Mythic
				if itemID == 238033 then
					print(format("DEBUG: 通过bonusID检查 (bonusID=%d)", bonusID))
				end
				return true
			end
		end
		if itemID == 238033 then
			print("DEBUG: 没有找到匹配的团本bonusID")
		end
	else
		if itemID == 238033 then
			print("DEBUG: 没有bonusIDs信息")
		end
	end

	if itemID == 238033 then
		print("DEBUG: 所有检查都未通过，判定为非团本装备")
	end

	return false
end

-- 获取难度描述
local function GetDifficultyText(difficultyID, bonusIDs)
	local difficultyText = "未知"

	-- 根据difficultyID判断
	if difficultyID == 17 then
		difficultyText = "LFR"
	elseif difficultyID == 14 then
		difficultyText = "普通"
	elseif difficultyID == 15 then
		difficultyText = "英雄"
	elseif difficultyID == 16 then
		difficultyText = "史诗"
	else
		-- 如果difficultyID不明确，根据bonusID判断
		if bonusIDs then
			for _, bonusID in ipairs(bonusIDs) do
				if bonusID == 40 then
					difficultyText = "LFR"
				elseif bonusID == 41 then
					difficultyText = "普通"
				elseif bonusID == 42 then
					difficultyText = "英雄"
				elseif bonusID == 43 then
					difficultyText = "史诗"
				end
			end
		end
	end

	return difficultyText
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

	-- 添加新的扫描记录
	local scanRecord = {
		timestamp = currentTime,
		itemCount = #auctionData,
		items = {}
	}

	-- 保存物品信息（仅保存关键信息以节省空间）
	for i, auction in ipairs(auctionData) do
		if auction[17] then -- itemID存在
			local itemID = auction[17]

			-- 先获取基本物品信息进行初步判断
			local itemDetails = GetItemDetails(itemID, nil)

			local itemInfo = {
				itemID = itemID,
				buyoutAmount = auction[10],
				bidAmount = auction[8],
				quantity = auction[3],
				name = auction[1]
			}

			-- 如果是团本装备，获取完整itemLink并解析详细信息
			if IsRaidGear(itemDetails, itemID) then
				-- 获取完整的itemLink来解析bonusID等详细信息
				local itemLink = C_AuctionHouse.GetReplicateItemLink(i)
				if itemLink then
					-- 重新解析包含itemLink的详细信息
					itemDetails = GetItemDetails(itemID, itemLink)
				end

				itemInfo.isRaidGear = true
				-- itemInfo.itemName = itemDetails.name
				itemInfo.itemLevel = itemDetails.itemLevel
				itemInfo.quality = itemDetails.quality
				itemInfo.itemType = itemDetails.itemType
				itemInfo.itemSubType = itemDetails.itemSubType
				itemInfo.difficulty = GetDifficultyText(itemDetails.difficultyID, itemDetails.bonusIDs)
				itemInfo.bonusIDs = itemDetails.bonusIDs
				itemInfo.enchantID = itemDetails.enchantID
				itemInfo.upgradeID = itemDetails.upgradeID
				itemInfo.itemLink = itemLink -- 保存完整的itemLink
			end

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
							buyoutAmount = item.buyoutAmount,
							bidAmount = item.bidAmount,
							quantity = item.quantity,
							name = item.name
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
				print(format("%s: 一口价 %s, 竞拍价 %s, 数量 %d",
					date("%m-%d %H:%M", item.timestamp),
					item.buyoutAmount and C_CurrencyInfo.GetCoinTextureString(item.buyoutAmount) or "无",
					item.bidAmount and C_CurrencyInfo.GetCoinTextureString(item.bidAmount) or "无",
					item.quantity))
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
		-- 测试特定物品ID
		local testItemID = tonumber(arg) or 238033
		print(format("=== 测试物品 %d ===", testItemID))

		-- 先获取基本物品信息
		local itemDetails = GetItemDetails(testItemID, nil)
		print("基本物品信息:")
		for key, value in pairs(itemDetails) do
			if type(value) == "table" then
				print(format("  %s: {%s}", key, table.concat(value, ", ")))
			else
				print(format("  %s: %s", key, tostring(value)))
			end
		end

		-- 测试团本装备判断
		local isRaid = IsRaidGear(itemDetails, testItemID)
		print(format("团本装备判断结果: %s", isRaid and "是" or "否"))

		-- 如果是团本装备，尝试从当前拍卖行扫描中获取itemLink
		if isRaid then
			print("正在查找当前拍卖行中的该物品...")
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
				print(format("找到物品，索引: %d", foundIndex))
				print(format("ItemLink: %s", tostring(itemLink)))

				if itemLink then
					-- 重新解析包含itemLink的详细信息
					local fullDetails = GetItemDetails(testItemID, itemLink)
					print("完整物品信息 (包含bonusID):")
					for key, value in pairs(fullDetails) do
						if type(value) == "table" then
							print(format("  %s: {%s}", key, table.concat(value, ", ")))
						else
							print(format("  %s: %s", key, tostring(value)))
						end
					end
				end
			else
				print("在当前拍卖行扫描中未找到该物品")
				print("提示: 请先打开拍卖行并进行扫描")
			end
		end
	elseif command == "raid" then
		-- 查询团本装备
		local minLevel = tonumber(arg) or 400 -- 默认最低装等400
		print(format("=== 团本装备 (装等 >= %d) ===", minLevel))

		local raidItems = {}
		for dateStr, dayData in pairs(AuctionSearchDB.auctions) do
			for _, scan in ipairs(dayData.scans) do
				for _, item in ipairs(scan.items) do
					if item.isRaidGear and item.itemLevel and item.itemLevel >= minLevel then
						table.insert(raidItems, {
							name = item.itemName or format("物品%d", item.itemID),
							itemLevel = item.itemLevel,
							difficulty = item.difficulty or "未知",
							quality = item.quality,
							buyout = item.buyoutAmount,
							timestamp = scan.timestamp
						})
					end
				end
			end
		end

		-- 按装等排序
		table.sort(raidItems, function(a, b) return a.itemLevel > b.itemLevel end)

		local shown = 0
		for _, item in ipairs(raidItems) do
			if shown >= 20 then break end -- 最多显示20件
			local qualityColor = ""
			if item.quality == 3 then
				qualityColor = "|cff0070dd" -- 蓝色
			elseif item.quality == 4 then
				qualityColor = "|cffa335ee" -- 紫色
			elseif item.quality == 5 then
				qualityColor = "|cffff8000" -- 橙色
			end

			print(format("%s%s|r (%d级) [%s] - %s",
				qualityColor,
				item.name,
				item.itemLevel,
				item.difficulty,
				item.buyout and C_CurrencyInfo.GetCoinTextureString(item.buyout) or "无一口价"))
			shown = shown + 1
		end

		if #raidItems == 0 then
			print("未找到符合条件的团本装备")
		elseif #raidItems > 20 then
			print(format("... 还有 %d 件装备", #raidItems - 20))
		end
	else
		print("AuctionSearch 命令:")
		print("  /auctionsearch stats - 显示数据库统计信息")
		print("  /auctionsearch history <物品ID> - 显示物品拍卖历史")
		print("  /auctionsearch test [物品ID] - 测试物品信息 (默认238033)")
		print("  /auctionsearch raid [最低装等] - 显示团本装备 (默认400+)")
		print("  /auctionsearch clear - 清空所有保存的数据")
	end
end

-- 注册斜杠命令
SLASH_AUCTIONSEARCH1 = "/auctionsearch"
SLASH_AUCTIONSEARCH2 = "/as"
SlashCmdList["AUCTIONSEARCH"] = HandleSlashCommand

-- 扫描完成后的回调函数
local function OnScanComplete(beginTime)
	local scanTime = debugprofilestop() - beginTime
	print(format("Scanned %d auctions in %d milliseconds", #auctions + 1, scanTime))

	-- 保存扫描数据到持久化存储
	SaveAuctionData(auctions)

	-- 执行数据清理
	CleanOldData()
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

			-- 取消注册ADDON_LOADED事件
			self:UnregisterEvent("ADDON_LOADED")
		end
	elseif event == "AUCTION_HOUSE_SHOW" then
		print("AuctionSearch: 拍卖行已打开，开始复制物品列表")
		C_AuctionHouse.ReplicateItems()
		initialQuery = true
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
f:RegisterEvent("REPLICATE_ITEM_LIST_UPDATE")
f:SetScript("OnEvent", OnEvent)
