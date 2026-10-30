-- Read-only memory transport. No TTS, compression or inbound memory writes.
-- Two array slots are allocated once and never resized; strings are immutable.
AuctionSearchExport = {}
local Export = AuctionSearchExport
local RAW_BLOCK = 262144
local current, latest
local serial, sequence = 0, 0
local login = time() .. "-" .. math.floor(GetTime() * 1000) .. "-" .. math.random(100000, 999999)
AuctionSearchMemoryBridge = { 8675309123987, "" }
local bridge = AuctionSearchMemoryBridge

local function Adler(text, checksum, yieldFn)
    local a = (checksum or 1) % 65536
    local b = math.floor((checksum or 1) / 65536)
    for offset = 1, #text, 8192 do
        for i = offset, math.min(#text, offset + 8191) do
            a = (a + string.byte(text, i)) % 65521
            b = (b + a) % 65521
        end
        if yieldFn then yieldFn() end
    end
    return b * 65536 + a
end

local function Publish(session, kind, index, count, info)
    sequence = sequence + 1
    local payload = info and info.payload or ""
    local header = table.concat({ "WAM2", session, sequence, kind, index, count,
        #payload, string.format("%.0f", info and info.checksum or 1) }, "|") .. "\n"
    assert(#header <= 256, "导出头过长")
    -- One assignment publishes a complete immutable frame.
    bridge[2] = header .. string.rep(" ", 256 - #header) .. payload
end

local function Paint(job, detail)
    AuctionSearchOverlay.SetExportProgress(job.phase, job.processed or 0,
        job.totalItems or 0, job.sent or 0, job.sendTotal or 0,
        job.encodedBytes or 0, job.wireSent or 0,
        math.max(0.001, GetTime() - (job.started or GetTime())), detail)
end

local function Fail(message)
    if not current then return end
    current.busy, current.worker, current.snapshot = false, nil, nil
    if not current.manifest then current.blocks = nil end
    current.phase = "error"
    Publish(current.session, "H", 0, 0)
    Paint(current, tostring(message))
end

function Export.IsBusy() return current and current.busy or false end
function Export.SetSnapshot(scan)
    if Export.IsBusy() then return end
    latest, current = scan, nil
end
function Export.Clear()
    if Export.IsBusy() then return end
    latest, current = nil, nil
    Publish(login .. "-0", "H", 0, 0)
end

local escapes = { ["\\"] = "\\\\", ['"'] = '\\"', ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t" }
local function Quote(value)
    -- Reject unsupported control bytes rather than silently alter the snapshot.
    assert(not value:find("[%z\1-\8\11\12\14-\31]"), "字段包含不支持的控制字符")
    return '"' .. value:gsub('[\\"\n\r\t]', escapes) .. '"'
end

local function Prepare(job)
    local buffer, size, sliceStart = {}, 0, debugprofilestop()
    job.rawBytes, job.checksum, job.encodedBytes = 0, 1, 0
    job.blocks = {}
    local function Yield()
        if debugprofilestop() - sliceStart >= 5 then
            coroutine.yield()
            sliceStart = debugprofilestop()
        end
    end
    local function Flush()
        if size == 0 then return end
        local raw = table.concat(buffer)
        job.rawBytes = job.rawBytes + #raw
        local checksum = Adler(raw, nil, Yield)
        -- Combine Adler checksums without a second byte-by-byte Lua pass.
        local firstA, firstB = job.checksum % 65536, math.floor(job.checksum / 65536)
        local blockA, blockB = checksum % 65536, math.floor(checksum / 65536)
        job.checksum = ((firstB + blockB + #raw * (firstA - 1)) % 65521) * 65536
            + ((firstA + blockA - 1) % 65521)
        job.blocks[#job.blocks + 1] = { payload = raw, checksum = checksum }
        job.encodedBytes = job.rawBytes
        buffer, size = {}, 0
        Yield()
    end
    local function Emit(value)
        local offset = 1
        while offset <= #value do
            local piece = value:sub(offset, offset + RAW_BLOCK - size - 1)
            buffer[#buffer + 1] = piece
            size = size + #piece
            offset = offset + #piece
            if size == RAW_BLOCK then Flush() end
        end
    end
    local Serialize
    Serialize = function(value, depth)
        assert(depth < 32, "字段嵌套过深")
        local kind = type(value)
        if kind == "string" then Emit(Quote(value))
        elseif kind == "number" then
            assert(value == value and value ~= math.huge and value ~= -math.huge, "非有限数值")
            Emit(string.format("%.17g", value))
        elseif kind == "boolean" then Emit(value and "true" or "false")
        elseif kind == "table" then
            Emit("{")
            if value == job.snapshot.items then
                -- Implicit array keys avoid Lua 5.1's constant-table limit on large snapshots.
                for _, child in ipairs(value) do
                    Serialize(child, depth + 1); Emit(",\n")
                    job.processed = job.processed + 1
                    Yield()
                end
                local actual = 0
                for key in pairs(value) do
                    assert(type(key) == "number" and key >= 1 and key <= #value and key == math.floor(key), "挂单数组存在非连续键")
                    actual = actual + 1
                    if actual % 1000 == 0 then Yield() end
                end
                assert(actual == #value, "挂单数组存在空洞")
            else
                for key, child in pairs(value) do
                    assert(type(key) == "string" or type(key) == "number", "不支持的字段键")
                    Emit("["); Serialize(key, depth + 1); Emit("]=")
                    Serialize(child, depth + 1); Emit(",\n")
                    Yield()
                end
            end
            Emit("}")
        else error("不支持的数据类型: " .. kind) end
    end
    Emit('AuctionSearchDB={["lastScanTime"]=')
    Serialize(job.snapshot.timestamp, 0)
    Emit(',["lastScan"]=')
    local metadata = {}
    for key, value in pairs(job.snapshot) do
        if key ~= "items" and key ~= "itemMarketScopes" then metadata[key] = value end
    end
    Serialize(metadata, 0)
    Emit(',["auctions"]={[' .. Quote(date("%Y-%m-%d", job.snapshot.timestamp)) .. ']={["scans"]={')
    Serialize(job.snapshot, 0)
    Emit("}}}}\n")
    Flush()
    local manifest = table.concat({ "lua-raw-blocks", #job.blocks, job.rawBytes,
        string.format("%.0f", job.checksum), job.snapshot.timestamp, job.totalItems }, ":")
    job.manifest = { payload = manifest, checksum = Adler(manifest) }
    job.snapshot = nil
end


local function BeginSend(job, selected)
    job.queue = {0}
    for index in ipairs(job.blocks) do
        if not selected or selected[index] then
            job.queue[#job.queue + 1] = index
            if #job.queue % 16 == 0 then job.queue[#job.queue + 1] = 0 end
        end
    end
    job.queue[#job.queue + 1] = 0
    job.sent, job.wireSent, job.sendTotal = 0, 0, #job.queue
    job.started, job.phase, job.busy, job.round = GetTime(), "sending", true, 1
    job.generation = (job.generation or 0) + 1
    local generation = job.generation
    local function Next()
        if current ~= job or not job.busy or job.generation ~= generation then return end
        if job.sent >= #job.queue then
            if job.round == 3 then
                job.busy, job.phase = false, "sent"
                Paint(job, "已发布 3 轮；以电脑端“校验通过、已保存”为准。缺块用 /as resend all")
                return
            end
            job.sent, job.round = 0, job.round + 1
        end
        local index = job.queue[job.sent + 1]
        local info = index == 0 and job.manifest or job.blocks[index]
        Publish(job.session, index == 0 and "M" or "D", index, #job.blocks, info)
        job.sent = job.sent + 1
        job.wireSent = job.wireSent + #info.payload
        Paint(job, string.format("第 %d / 3 轮发布 · %.2f MiB · 电脑端显示实际接收进度", job.round, job.rawBytes / 1048576))
        C_Timer.After(0.05, Next)
    end
    Next()
end

function Export.Start()
    if Export.IsBusy() then return end
    if not latest then
        AuctionSearchOverlay.SetPhase("error", "没有可导出的快照，请先扫描")
        return
    end
    serial = serial + 1
    local job = { snapshot = latest, busy = true, session = login .. "-" .. serial,
        totalItems = #latest.items, processed = 0, phase = "preparing" }
    current = job
    Publish(job.session, "H", 0, 0)
    job.worker = coroutine.create(function() Prepare(job) end)
    local function Step()
        if current ~= job or not job.busy then return end
        local ok, reason = coroutine.resume(job.worker)
        if not ok then Fail(reason); return end
        Paint(job)
        if coroutine.status(job.worker) == "dead" then
            job.worker = nil
            BeginSend(job)
        else C_Timer.After(0, Step) end
    end
    Step()
end

function Export.Cancel()
    if not Export.IsBusy() then return end
    current.busy, current.worker, current.snapshot = false, nil, nil
    if not current.manifest then current.blocks = nil end
    current.phase = "stopped"
    Publish(current.session, "H", 0, 0)
    Paint(current, "发布已停止；已接收的数据保留在电脑端，可 /as resend all 继续补齐")
end

function Export.Test()
    if Export.IsBusy() then return end
    local saved = latest
    latest = { transportTest = true, timestamp = time(), itemCount = 0, recordCount = 0,
        realmName = "MEMORY_SELF_TEST", regionName = "TEST", items = {}, itemMarketScopes = {} }
    Export.Start()
    latest = saved
end

function Export.Resend(argument)
    if Export.IsBusy() then return end
    local job = current
    if not job or not job.manifest then
        AuctionSearchOverlay.SetPhase("error", "没有可重发的完整快照，请重新导出")
        return
    end
    local selected
    if argument ~= "all" then
        selected = {}
        if not argument:match("^%d[%d, ]*$") then
            AuctionSearchOverlay.SetPhase("error", "用法：/as resend all 或 /as resend 18,93")
            return
        end
        for value in argument:gmatch("%d+") do
            local index = tonumber(value)
            if index < 1 or index > #job.blocks then
                AuctionSearchOverlay.SetPhase("error", "重发块编号超出范围"); return
            end
            selected[index] = true
        end
    end
    BeginSend(job, selected)
end

function Export.RestoreLatest(database)
    local newest
    for _, day in pairs(database.auctions or {}) do
        for _, scan in pairs(day.scans or {}) do
            if type(scan.items) == "table" and (not newest or scan.timestamp > newest.timestamp) then newest = scan end
        end
    end
    latest = newest
end

-- Heartbeat lets the reader reject stale heap copies and locate before exporting.
local function Heartbeat()
    if not current or current.phase ~= "sending" or not current.busy then
        Publish(current and current.session or (login .. "-0"), "H", 0, 0)
    end
    C_Timer.After(0.5, Heartbeat)
end
Heartbeat()

