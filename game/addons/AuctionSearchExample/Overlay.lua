-- 拍卖行扫描状态面板。常规扫描状态只显示在面板中，不刷聊天框。

AuctionSearchOverlay = AuctionSearchOverlay or {}

local frame
local currentPhase = "idle"

local PHASE = {
	idle = {
		title = "拍卖行数据",
		detail = "等待打开拍卖行",
		color = { 0.45, 0.45, 0.45 },
	},
	started = {
		title = "正在请求拍卖快照",
		detail = "等待服务器返回全量拍卖列表（接口有 15 分钟限流）",
		color = { 0.20, 0.55, 0.95 },
	},
	scanning = {
		title = "正在采集拍卖数据",
		detail = "正在读取物品、价格与剩余时间",
		color = { 0.95, 0.68, 0.15 },
	},
	complete = {
		title = "扫描完成",
		detail = "拍卖数据已保存在游戏中",
		color = { 0.20, 0.80, 0.46 },
	},
	warning = {
		title = "扫描完成，但存在缺失",
		detail = "快照已保存，请查看缺失计数后再导出",
		color = { 0.95, 0.55, 0.12 },
	},
	error = {
		title = "扫描未完成",
		detail = "未能取得拍卖快照，请稍后重新打开拍卖行",
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
	frame.statusBar:SetStatusBarColor(phase.color[1], phase.color[2], phase.color[3], 1)
	frame.accent:SetColorTexture(phase.color[1], phase.color[2], phase.color[3], 1)
end

function AuctionSearchOverlay.GetPhase()
	return currentPhase
end

function AuctionSearchOverlay.SetPhase(key, detail)
	if key == "logout" then
		key = "complete"
	end
	if not PHASE[key] then
		key = "idle"
	end
	currentPhase = key
	if not frame then
		return
	end
	if key == "idle" then
		frame:Hide()
		return
	end

	ApplyPhaseStyle(key)
	if detail and detail ~= "" then
		frame.detail:SetText(detail)
	end
	if key == "started" or key == "error" then
		frame.statusBar:SetMinMaxValues(0, 1)
		frame.statusBar:SetValue(0)
		frame.progress:SetText(key == "started" and "等待服务器" or "需要重试")
		frame.count:SetText("")
	elseif key == "complete" then
		frame.statusBar:SetMinMaxValues(0, 1)
		frame.statusBar:SetValue(1)
		frame.progress:SetText("100%")
	end
	frame:Show()
end

function AuctionSearchOverlay.SetProgress(processed, total, detail)
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
	apiErrorCount
)
	local seconds = (tonumber(elapsedMs) or 0) / 1000
	missingCoreCount = math.max(0, tonumber(missingCoreCount) or 0)
	incompleteInfoCount = math.max(0, tonumber(incompleteInfoCount) or 0)
	apiErrorCount = math.max(0, tonumber(apiErrorCount) or 0)
	local detail = format("已保存 %s 条 · 用时 %.1f 秒", FormatNumber(total), seconds)
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
	AuctionSearchOverlay.SetPhase(hasWarning and "warning" or "complete", detail)
	AuctionSearchOverlay.SetProgress(total, total)
end

function AuctionSearchOverlay.Init()
	if frame then
		return
	end

	local f = CreateFrame("Frame", "AuctionSearchStatusPanel", UIParent)
	f:SetSize(470, 106)
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

	f.detail = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
	f.detail:SetPoint("TOPLEFT", f.title, "BOTTOMLEFT", 0, -5)
	f.detail:SetWidth(305)
	f.detail:SetTextColor(0.76, 0.80, 0.88)
	f.detail:SetJustifyH("LEFT")

	f.count = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
	f.count:SetPoint("TOPRIGHT", f.progress, "BOTTOMRIGHT", 0, -5)
	f.count:SetTextColor(0.76, 0.80, 0.88)
	f.count:SetJustifyH("RIGHT")

	f.barBackground = f:CreateTexture(nil, "ARTWORK")
	f.barBackground:SetPoint("BOTTOMLEFT", 18, 15)
	f.barBackground:SetPoint("BOTTOMRIGHT", -18, 15)
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
