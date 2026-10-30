-- Observations only. Protected actions remain ordinary external key presses.
AuctionSearchPreparation = {}
local P = AuctionSearchPreparation
local mountName = "雄壮商队雷龙"
local mountID
local commands = {
	["/cast [nomounted,nocombat] " .. mountName] = "mount",
	["/targetexact 收集者温塔"] = "target",
	["/as scan"] = "scan",
	["/logout"] = "logout",
}

function P.Sample()
	-- Never publish arbitrary chat text; report only exact allowlisted commands.
	local result = { prepReady = 0, combat = 1, mounted = 0, brutosaur = 0,
		casting = 1, targetAuctioneer = 0, chatOpen = 0, chatCommand = "none", keyboardFocus = 1 }
	local ok = pcall(function()
		if InCombatLockdown() then return end
		result.combat = 0
		result.mounted = IsMounted() and 1 or 0
		result.casting = (UnitCastingInfo("player") or UnitChannelInfo("player")) and 1 or 0
		if not mountID then
			for _, id in ipairs(C_MountJournal.GetMountIDs()) do
				if C_MountJournal.GetMountInfoByID(id) == mountName then mountID = id; break end
			end
		end
		if mountID then
			local _, _, _, active = C_MountJournal.GetMountInfoByID(mountID)
			result.brutosaur = active and 1 or 0
		end
		result.targetAuctioneer = (UnitName("target") == "收集者温塔" and not UnitIsPlayer("target")) and 1 or 0
		local focus = GetCurrentKeyBoardFocus()
		result.keyboardFocus = focus and 1 or 0
		local edit = ChatEdit_GetActiveWindow()
		if edit and edit:IsShown() and focus == edit then
			result.chatOpen = 1
			local text = edit:GetText()
			result.chatCommand = text == "" and "empty" or (commands[text] or "other")
		end
		result.prepReady = 1
	end)
	if not ok then result.prepReady = 0 end
	return result
end
