-- 拍卖行扫描状态面板。常规扫描状态只显示在面板中，不刷聊天框。

AuctionSearchOverlay = AuctionSearchOverlay or {}

local frame
local currentPhase = "idle"
local scanCallback
local exportCallback

local PHASE = {
	rate_wait = { title = "疑似限流 · 等待冷却", detail = "超过 60 秒仍未收到快照；请查看电脑控制器的冷却进度", token = "AS_RATE_WAIT", color = {0.95, 0.62, 0.15} },
	logout_ready = { title = "扫描完整 · 等待自动小退", detail = "等待电脑控制器小退并归档", token = "AS_LOGOUT_READY", color = {0.2, 0.8, 0.46} },
	export_preparing = { title = "准备导出 · 正在序列化", detail = "分批准备完整快照", token = "AS_EXPORT_PREPARE", color = {0.6, 0.5, 0.95} },
	export_sending = { title = "正在发布 · 电脑只读接收", detail = "", token = "AS_EXPORT_SENDING", color = {0.2, 0.65, 0.95} },
	export_sent = { title = "发布结束 · 请核对电脑端", detail = "", token = "AS_EXPORT_SENT", color = {0.3, 0.8, 0.5} },
	export_stopped = { title = "导出已停止", detail = "", token = "AS_EXPORT_STOPPED", color = {0.8, 0.6, 0.2} },
	export_error = { title = "导出中断", detail = "", token = "AS_EXPORT_ERROR", color = {0.9, 0.3, 0.2} },
	idle = {
		title = "拍卖行数据",
		detail = "等待打开拍卖行",
		token = "AS_IDLE",
		color = { 0.45, 0.45, 0.45 },
	},
	ready = {
		title = "拍卖采集已就绪",
		detail = "角色已进入世界，等待打开拍卖行",
		token = "AS_READY",
		color = { 0.32, 0.72, 0.88 },
	},
	started = {
		title = "正在请求拍卖快照",
		detail = "等待服务器返回全量拍卖列表；无响应时不自动重试",
		token = "AS_WAITING",
		color = { 0.20, 0.55, 0.95 },
	},
	scanning = {
		title = "正在采集拍卖数据",
		detail = "正在读取物品、价格与剩余时间",
		token = "AS_SCANNING",
		color = { 0.95, 0.68, 0.15 },
	},
	complete = {
		title = "扫描完成",
		detail = "拍卖数据已保存在游戏中",
		token = "AS_COMPLETE",
		color = { 0.20, 0.80, 0.46 },
	},
	warning = {
		title = "扫描完成，但存在缺失",
		detail = "快照已保存，请查看缺失计数后再导出",
		token = "AS_WARNING",
		color = { 0.95, 0.55, 0.12 },
	},
	error = {
		title = "扫描未完成",
		detail = "未能取得拍卖快照，请稍后重新打开拍卖行",
		token = "AS_ERROR",
		color = { 0.90, 0.25, 0.22 },
	},
}

local function FormatNumber(value)
	value = math.max(0, math.floor(tonumber(value) or 0))
	if BreakUpLargeNumbers then
		return BreakUpLargeNumbers(value)
	end
	local formatted = tostring(value)
	while true do
		local replaced, count = formatted:gsub("^(%-?%d+)(%d%d%d)", "%1,%2")
		formatted = replaced
		if count == 0 then
			return formatted
		end
	end
end

local function ApplyPhaseStyle(key)
	local phase = PHASE[key] or PHASE.idle
	frame.title:SetText(phase.title)
	frame.detail:SetText(phase.detail)
	frame.token:SetText(phase.token)
	-- OCR 标签始终使用最高对比度；阶段颜色由左侧强调条与进度条表达。
	frame.token:SetTextColor(1, 1, 1, 1)
	frame.statusBar:SetStatusBarColor(phase.color[1], phase.color[2], phase.color[3], 1)
	frame.accent:SetColorTexture(phase.color[1], phase.color[2], phase.color[3], 1)
end

function AuctionSearchOverlay.GetPhase()
	return currentPhase
end

function AuctionSearchOverlay.SetScanCallback(callback)
	scanCallback = type(callback) == "function" and callback or nil
	if frame and frame.scanButton then
		local showButton = currentPhase == "ready" and scanCallback ~= nil
		frame.scanButton:SetShown(showButton)
		frame.progress:SetShown(not showButton)
	end
end

function AuctionSearchOverlay.SetPhase(key, detail)
	if AuctionSearchExport and AuctionSearchExport.IsBusy() and not key:match("^export_") then return end
	if key == "logout" then
		key = "complete"
	end
	if not PHASE[key] then
		key = "idle"
	end
	currentPhase = key
	if AuctionSearchStatus then AuctionSearchStatus.Update({ phase = key }) end
	if not frame then
		return
	end
	if key == "idle" then
		frame:Hide()
		return
	end

	ApplyPhaseStyle(key)
	if frame.exportButton then
		frame.exportButton:SetText(AuctionSearchExport and AuctionSearchExport.IsBusy() and "停止导出" or "导出到电脑")
		frame.exportButton:SetEnabled(key ~= "started" and key ~= "scanning")
	end
	local showScanButton = key == "ready" and scanCallback ~= nil
	frame.scanButton:SetShown(showScanButton)
	frame.progress:SetShown(not showScanButton)
	if detail and detail ~= "" then
		frame.detail:SetText(detail)
	end
	if key == "ready" then
		frame.statusBar:SetMinMaxValues(0, 1)
		frame.statusBar:SetValue(0)
		frame.progress:SetText("就绪")
		frame.count:SetText("")
	elseif key == "started" or key == "error" then
		frame.statusBar:SetMinMaxValues(0, 1)
		frame.statusBar:SetValue(0)
		frame.progress:SetText(key == "started" and "等待服务器" or "需要重试")
		frame.count:SetText("")
	elseif key == "complete" or key == "logout_ready" then
		frame.statusBar:SetMinMaxValues(0, 1)
		frame.statusBar:SetValue(1)
		frame.progress:SetText("100%")
	end
	frame:Show()
end

function AuctionSearchOverlay.SetIdentity(runID, realm, character)
	if frame and frame.identity then
		frame.identity:SetText("RUN " .. tostring(runID) .. " · " .. tostring(realm) .. " · " .. tostring(character))
	end
end

function AuctionSearchOverlay.SetExportCallback(callback)
	exportCallback = callback
end

function AuctionSearchOverlay.SetExportProgress(phase, processed, items, sent, total, bytes, wireSent, elapsed, detail)
	AuctionSearchOverlay.SetPhase("export_" .. phase)
	if not frame then return end
	local preparing = phase == "preparing"
	AuctionSearchOverlay.SetProgress(preparing and processed or sent, preparing and items or total)
	local speed = wireSent / elapsed
	if preparing then
		frame.detail:SetText(detail or format("已准备 %s / %s 条 · 原始数据 %.2f MiB", FormatNumber(processed), FormatNumber(items), bytes / 1048576))
	else
		frame.count:SetText(format("%s / %s 块", FormatNumber(sent), FormatNumber(total)))
		local remaining = sent > 0 and math.max(0, (total - sent) * elapsed / sent) or 0
		frame.detail:SetText(detail or format("原始数据 %.2f MiB · 发布 %.1f KiB/s · 本轮剩余 %.0f 秒", bytes / 1048576, speed / 1024, remaining))
	end
end

function AuctionSearchOverlay.SetProgress(processed, total, detail)
	if AuctionSearchStatus then AuctionSearchStatus.Update({ processed = processed, total = total }) end
	if not frame then
		return
	end
	processed = math.max(0, tonumber(processed) or 0)
	total = math.max(0, tonumber(total) or 0)
	local denominator = math.max(1, total)
	local bounded = math.min(processed, denominator)
	local percent = math.min(100, math.floor((bounded / denominator) * 100 + 0.5))

	frame.statusBar:SetMinMaxValues(0, denominator)
	frame.statusBar:SetValue(bounded)
	frame.progress:SetText(format("%d%%", percent))
	frame.count:SetText(format("%s / %s 条", FormatNumber(math.min(processed, total)), FormatNumber(total)))
	if detail and detail ~= "" then
		frame.detail:SetText(detail)
	end
	frame:Show()
end

function AuctionSearchOverlay.SetComplete(
	total,
	elapsedMs,
	linkedItems,
	missingCoreCount,
	incompleteInfoCount,
	apiErrorCount,
	marketScopeRegionCount,
	marketScopeRealmCount,
	marketScopeUnknownCount
)
	local seconds = (tonumber(elapsedMs) or 0) / 1000
	missingCoreCount = math.max(0, tonumber(missingCoreCount) or 0)
	incompleteInfoCount = math.max(0, tonumber(incompleteInfoCount) or 0)
	apiErrorCount = math.max(0, tonumber(apiErrorCount) or 0)
	marketScopeRegionCount = math.max(0, tonumber(marketScopeRegionCount) or 0)
	marketScopeRealmCount = math.max(0, tonumber(marketScopeRealmCount) or 0)
	marketScopeUnknownCount = math.max(0, tonumber(marketScopeUnknownCount) or 0)
	local detail = format("已保存 %s 条 · 用时 %.1f 秒", FormatNumber(total), seconds)
	detail = detail .. format(
		" · 范围 区域 %s / 服务器 %s / 未知 %s",
		FormatNumber(marketScopeRegionCount),
		FormatNumber(marketScopeRealmCount),
		FormatNumber(marketScopeUnknownCount)
	)
	if linkedItems and linkedItems < total then
		detail = detail .. format(" · %s 条含物品链接", FormatNumber(linkedItems))
	end
	if missingCoreCount > 0 then
		detail = detail .. format(" · 核心字段缺失 %s 条", FormatNumber(missingCoreCount))
	end
	if incompleteInfoCount > 0 then
		detail = detail .. format(" · 详情未就绪 %s 条", FormatNumber(incompleteInfoCount))
	end
	if apiErrorCount > 0 then
		detail = detail .. format(" · 读取异常 %s 条", FormatNumber(apiErrorCount))
	end
	local hasWarning = missingCoreCount > 0 or incompleteInfoCount > 0 or apiErrorCount > 0
		or marketScopeUnknownCount > 0 or (linkedItems and linkedItems < total)
	AuctionSearchOverlay.SetPhase(hasWarning and "warning" or "complete", detail)
	AuctionSearchOverlay.SetProgress(total, total)
end

function AuctionSearchOverlay.Init()
	if frame then
		return
	end

	local f = CreateFrame("Frame", "AuctionSearchStatusPanel", UIParent)
	f:SetSize(470, 190)
	f:SetPoint("TOP", UIParent, "TOP", 0, -28)
	f:SetFrameStrata("FULLSCREEN_DIALOG")
	f:SetFrameLevel(5000)
	f:SetClampedToScreen(true)
	f:EnableMouse(false)

	f.shadow = f:CreateTexture(nil, "BACKGROUND", nil, -2)
	f.shadow:SetPoint("TOPLEFT", -5, 5)
	f.shadow:SetPoint("BOTTOMRIGHT", 5, -5)
	f.shadow:SetColorTexture(0, 0, 0, 0.55)

	f.border = f:CreateTexture(nil, "BACKGROUND", nil, -1)
	f.border:SetPoint("TOPLEFT", -1, 1)
	f.border:SetPoint("BOTTOMRIGHT", 1, -1)
	f.border:SetColorTexture(0.58, 0.51, 0.32, 0.95)

	f.bg = f:CreateTexture(nil, "BACKGROUND")
	f.bg:SetAllPoints()
	f.bg:SetColorTexture(0.035, 0.045, 0.065, 0.96)

	f.accent = f:CreateTexture(nil, "ARTWORK")
	f.accent:SetPoint("TOPLEFT")
	f.accent:SetPoint("BOTTOMLEFT")
	f.accent:SetWidth(4)

	f.title = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightLarge")
	f.title:SetPoint("TOPLEFT", 18, -13)
	f.title:SetTextColor(1, 0.88, 0.48)
	f.title:SetJustifyH("LEFT")

	f.progress = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightLarge")
	f.progress:SetPoint("TOPRIGHT", -18, -13)
	f.progress:SetTextColor(1, 1, 1)
	f.progress:SetJustifyH("RIGHT")

	f.scanButton = CreateFrame("Button", "AuctionSearchStartButton", f, "UIPanelButtonTemplate")
	f.scanButton:SetSize(116, 28)
	f.scanButton:SetPoint("TOPRIGHT", -14, -8)
	f.scanButton:SetText("开始扫描")
	f.scanButton:SetFrameLevel(f:GetFrameLevel() + 2)
	f.scanButton:EnableMouse(true)
	f.scanButton:SetScript("OnClick", function()
		if scanCallback then
			pcall(scanCallback)
		end
	end)
	f.scanButton:Hide()
	f.exportButton = CreateFrame("Button", "AuctionSearchExportButton", f, "UIPanelButtonTemplate")
	f.exportButton:SetSize(124, 26)
	f.exportButton:SetPoint("BOTTOMRIGHT", -14, 10)
	f.exportButton:SetText("导出到电脑")
	f.exportButton:SetFrameLevel(f:GetFrameLevel() + 2)
	f.exportButton:EnableMouse(true)
	f.exportButton:SetScript("OnClick", function() if exportCallback then exportCallback() end end)
	local note = f:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
	note:SetPoint("BOTTOMLEFT", 18, 17)
	note:SetText("小退后由电脑校验归档")
	note:SetTextColor(0.65, 0.7, 0.8)

	-- 固定位置的 ASCII 标签供 Windows OCR 稳定识别；中文标题仍作为主要用户界面。
	f.token = f:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
	f.token:SetPoint("TOPLEFT", f.title, "BOTTOMLEFT", 0, -4)
	f.token:SetJustifyH("LEFT")

	f.detail = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
	f.detail:SetPoint("TOPLEFT", f.token, "BOTTOMLEFT", 0, -3)
	f.detail:SetWidth(434)
	f.detail:SetTextColor(0.76, 0.80, 0.88)
	f.detail:SetJustifyH("LEFT")

	f.count = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
	f.count:SetPoint("TOPRIGHT", f.progress, "BOTTOMRIGHT", 0, -7)
	f.count:SetTextColor(0.76, 0.80, 0.88)
	f.count:SetJustifyH("RIGHT")
	f.identity = f:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
	f.identity:SetPoint("BOTTOMLEFT", 18, 78)
	f.identity:SetWidth(434)
	f.identity:SetJustifyH("LEFT")
	f.identity:SetTextColor(1, 1, 1)
	f.identity:SetText("")

	f.barBackground = f:CreateTexture(nil, "ARTWORK")
	f.barBackground:SetPoint("BOTTOMLEFT", 18, 51)
	f.barBackground:SetPoint("BOTTOMRIGHT", -18, 51)
	f.barBackground:SetHeight(17)
	f.barBackground:SetColorTexture(0.12, 0.14, 0.18, 1)

	f.statusBar = CreateFrame("StatusBar", nil, f)
	f.statusBar:SetPoint("TOPLEFT", f.barBackground, "TOPLEFT", 2, -2)
	f.statusBar:SetPoint("BOTTOMRIGHT", f.barBackground, "BOTTOMRIGHT", -2, 2)
	f.statusBar:SetStatusBarTexture("Interface\\TargetingFrame\\UI-StatusBar")
	f.statusBar:SetMinMaxValues(0, 1)
	f.statusBar:SetValue(0)

	f.spark = f.statusBar:CreateTexture(nil, "OVERLAY")
	f.spark:SetTexture("Interface\\CastingBar\\UI-CastingBar-Spark")
	f.spark:SetBlendMode("ADD")
	f.spark:SetSize(16, 26)
	f.spark:SetPoint("CENTER", f.statusBar:GetStatusBarTexture(), "RIGHT", 0, 0)

	f:Hide()
	frame = f
	ApplyPhaseStyle("idle")
end
