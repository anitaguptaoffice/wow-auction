-- One automatic request per character login. Logout is performed by the Go
-- controller via a normal /logout macro, never by calling protected Logout().
AuctionSearchAutomation = {}
local A = AuctionSearchAutomation
local enabled = true
local attempted = false
local houseOpen = false
local generation = 0
local runID
local function Overlay(method, ...)
	local fn = AuctionSearchOverlay and AuctionSearchOverlay[method]
	if fn then pcall(fn, ...) end
end

function A.Init(settings)
	enabled = settings.autoCollect ~= false
	settings.autoCollect = enabled
	AuctionSearchStatus.Update({ autoCollect = enabled and 1 or 0 })
end

function A.IsEnabled() return enabled end

function A.SetEnabled(value, settings)
	enabled = value == true
	settings.autoCollect = enabled
	AuctionSearchStatus.Update({ autoCollect = enabled and 1 or 0 })
	generation = generation + 1
	-- Disabling also withdraws a pending logout signal.
	if not enabled then
		AuctionSearchStatus.Update({ validated = 0 })
		Overlay("SetPhase", "ready", "自动采集已关闭；可手动开始扫描")
	end
end

function A.OnHouseClosed()
	houseOpen = false
	generation = generation + 1
end

function A.OnHouseOpen(request)
	houseOpen = true
	if not enabled or attempted then return end
	generation = generation + 1
	local thisGeneration = generation
	C_Timer.After(0.5, function()
		if not enabled or attempted or not houseOpen or generation ~= thisGeneration then return end
		-- Consume before invoking: duplicate events and failures cannot auto retry.
		attempted = true
		request()
	end)
end

function A.RequestStarted(serial)
	attempted = true
	runID = tostring(time()) .. "-" .. tostring(serial)
	AuctionSearchStatus.Update({ run = runID, scanStartedAt = time(), validated = 0, processed = 0, total = 0 })
	Overlay("SetIdentity", runID, GetRealmName(), UnitName("player"))
	return runID
end

function A.OnCommitted(scan)
	if not enabled or not runID or scan.runID ~= runID then return false end
	local complete = scan.itemCount > 0 and scan.recordCount == scan.itemCount
		and #scan.items == scan.itemCount and scan.linkedItemCount == scan.itemCount
		and scan.missingCoreCount == 0 and scan.incompleteInfoCount == 0
		and scan.apiErrorCount == 0 and scan.marketScopeUnknownCount == 0
	if not complete then
		AuctionSearchStatus.Update({ validated = 0 })
		Overlay("SetPhase", "warning", "快照存在缺失；停止自动流程，不执行小退")
		return false
	end
	AuctionSearchStatus.Update({ validated = 1, missingCore = scan.missingCoreCount,
		incomplete = scan.incompleteInfoCount, apiErrors = scan.apiErrorCount,
		unknownScope = scan.marketScopeUnknownCount })
	Overlay("SetPhase", "logout_ready", "本轮完整性检查通过；等待电脑控制器小退并归档")
	Overlay("SetProgress", scan.recordCount, scan.itemCount)
	return true
end
