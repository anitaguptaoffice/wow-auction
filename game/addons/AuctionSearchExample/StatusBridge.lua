-- Small, immutable status frames. Contains no auction records or credentials.
AuctionSearchStatus = {}
AuctionSearchStatusBridge = { 8675309123981, "" }
local bridge = AuctionSearchStatusBridge
local S = AuctionSearchStatus
local state = { phase = "idle", run = "none", validated = 0, auctionOpen = 0, processed = 0, total = 0 }
local session = tostring(time()) .. "-" .. math.floor(GetTime() * 1000) .. "-" .. math.random(100000, 999999)
local sequence, ticker = 0, nil
local keys = { "phase", "run", "scanStartedAt", "validated", "auctionOpen", "processed", "total", "realmID", "character", "realm", "guid", "missingCore", "incomplete", "apiErrors", "unknownScope" }
for _, key in ipairs({ "world", "autoCollect", "prepReady", "combat", "mounted", "brutosaur", "casting", "targetAuctioneer", "chatOpen", "chatCommand", "keyboardFocus" }) do keys[#keys + 1] = key end
local function Escape(value)
	return tostring(value or ""):gsub("%%", "%%25"):gsub("|", "%%7C"):gsub("\n", "%%0A"):gsub("\r", "%%0D")
end
local function Publish()
	if AuctionSearchPreparation then
		for key, value in pairs(AuctionSearchPreparation.Sample()) do state[key] = value end
	end
	sequence = sequence + 1
	local parts = { "WAST1", session, tostring(sequence), tostring(time()), tostring(math.floor(GetTime() * 1000)) }
	for _, key in ipairs(keys) do parts[#parts + 1] = key .. "=" .. Escape(state[key]) end
	local payload = table.concat(parts, "|")
	local a, b = 1, 0
	for i = 1, #payload do a = (a + string.byte(payload, i)) % 65521; b = (b + a) % 65521 end
	local frame = payload .. "|adler=" .. string.format("%.0f", b * 65536 + a) .. "\n"
	assert(#frame <= 2048, "status frame exceeds limit")
	bridge[2] = frame
end
function S.Update(values)
	for key, value in pairs(values) do state[key] = value end
	Publish()
end
function S.Start()
	state.world = 1
	if ticker then return end
	state.realmID, state.realm = GetRealmID(), GetRealmName()
	state.character, state.guid = UnitName("player"), UnitGUID("player")
	Publish()
	ticker = C_Timer.NewTicker(1, Publish)
end
function S.Stop()
	state.world = 0
	state.phase, state.validated = "leaving", 0
	Publish()
	if ticker then ticker:Cancel(); ticker = nil end
end
function S.LeaveWorld()
	state.world, state.validated = 0, 0
	Publish()
end
