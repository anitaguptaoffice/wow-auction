--[[
  自动化可见 UI（DEVELOPMENT_PLAN §7）：大字号中文状态 + 背景色区分，供 wow-runner 截屏模板匹配。
  「扫描完成」与「登出提示」合并在同一屏双行，减少一个模板。
]]

AuctionSearchOverlay = AuctionSearchOverlay or {}

local frame ---@type Frame

-- l2 仅 complete 使用；started/scanning 仅单行
local PHASE = {
	idle = { l1 = "", l2 = "", r = 0.05, g = 0.05, b = 0.05, a = 0.85 },
	started = {
		l1 = "[扫描] 已开始扫描",
		l2 = "",
		r = 0.15, g = 0.25, b = 0.55, a = 0.92,
	},
	scanning = {
		l1 = "[扫描] 扫描中",
		l2 = "",
		r = 0.55, g = 0.45, b = 0.1, a = 0.92,
	},
	-- 单屏：上行=扫描完成，下行=登出说明（均为插件控制，一张截图即可覆盖 wow-runner 的 scan_complete + 登出链前状态）
	complete = {
		l1 = "[扫描] 扫描完成",
		l2 = "[登出] 请配合外部脚本返回角色",
		r = 0.12, g = 0.42, b = 0.28, a = 0.92,
	},
}

local currentPhase = "idle"

local function DebugPhase(name)
	print(format("[AuctionSearch] automation_phase=%s", name))
end

function AuctionSearchOverlay.GetPhase()
	return currentPhase
end

--- @param key "idle"|"started"|"scanning"|"complete"|"logout"（logout 视为 complete，兼容旧 uitest）
function AuctionSearchOverlay.SetPhase(key)
	if key == "logout" then
		key = "complete"
	end
	local p = PHASE[key] or PHASE.idle
	currentPhase = key
	if not frame then
		return
	end
	if key == "idle" then
		frame:Hide()
		DebugPhase(key)
		return
	end
	local bg = frame.bg
	if bg then
		bg:SetColorTexture(p.r, p.g, p.b, p.a)
	end
	frame.statusLine1:SetText(p.l1 or "")
	local l2 = p.l2 or ""
	frame.statusLine2:SetText(l2)
	if l2 ~= "" then
		frame.statusLine2:Show()
	else
		frame.statusLine2:Hide()
	end
	frame:Show()
	DebugPhase(key)
end

function AuctionSearchOverlay.Init()
	if frame then
		return
	end
	local f = CreateFrame("Frame", "AuctionSearchAutomationOverlay", UIParent)
	f:SetSize(560, 118)
	f:SetPoint("TOP", UIParent, "TOP", 0, -72)
	f:SetFrameStrata("FULLSCREEN_DIALOG")
	f:SetFrameLevel(5000)
	f:SetClampedToScreen(true)
	f:SetMovable(false)
	f:EnableMouse(false)

	f.bg = f:CreateTexture(nil, "BACKGROUND")
	f.bg:SetAllPoints()
	f.bg:SetColorTexture(0.05, 0.05, 0.05, 0.85)

	local border = f:CreateTexture(nil, "BORDER")
	border:SetPoint("TOPLEFT", -2, 2)
	border:SetPoint("BOTTOMRIGHT", 2, -2)
	border:SetColorTexture(1, 1, 1, 0.25)

	f.statusLine1 = f:CreateFontString(nil, "OVERLAY", "GameFontNormalHuge")
	f.statusLine1:SetPoint("TOP", f, "TOP", 0, -20)
	f.statusLine1:SetWidth(520)
	f.statusLine1:SetTextColor(1, 1, 1)
	f.statusLine1:SetJustifyH("CENTER")

	f.statusLine2 = f:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
	f.statusLine2:SetPoint("TOP", f.statusLine1, "BOTTOM", 0, -10)
	f.statusLine2:SetWidth(520)
	f.statusLine2:SetTextColor(0.95, 0.95, 0.85)
	f.statusLine2:SetJustifyH("CENTER")

	local sub = f:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
	sub:SetPoint("BOTTOM", 0, 10)
	sub:SetText("AuctionSearch / 自动化状态面板")
	sub:SetTextColor(0.85, 0.85, 0.85)

	f:Hide()
	frame = f
	AuctionSearchOverlay.SetPhase("idle")
end
