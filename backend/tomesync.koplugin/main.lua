--[[
TomeSync KOReader Plugin — stable shim (frozen; do not edit on-device).
Loads main_impl.lua with same-boot + next-boot rollback so a bad self-update
can never leave TomeSync unloadable.
]]

local logger = require("logger")
logger.info("TomeSync: shim loading...")

local function selfDir()
    local source = debug.getinfo(1, "S").source
    return source:match("^@(.*)/[^/]+$") or "."
end

local DIR      = selfDir()
local IMPL     = DIR .. "/main_impl.lua"
local IMPL_BAK = DIR .. "/main_impl.lua.bak"

local function readFile(path)
    local f = io.open(path, "rb"); if not f then return nil end
    local d = f:read("*a"); f:close(); return d
end

local function writeFile(path, data)
    local f = io.open(path, "wb"); if not f then return false end
    f:write(data); f:close(); return true
end

local function restoreBackup()
    local bak = readFile(IMPL_BAK)
    if not bak then return false end
    return writeFile(IMPL, bak)
end

local function getState()
    local ok, s = pcall(function() return G_reader_settings:readSetting("tomesync_update") end)
    return ok and s or nil
end

local function setState(s)
    pcall(function()
        G_reader_settings:saveSetting("tomesync_update", s)
        G_reader_settings:flush()
    end)
end

local function notify(text)
    pcall(function()
        local InfoMessage = require("ui/widget/infomessage")
        local UIManager   = require("ui/uimanager")
        UIManager:show(InfoMessage:new{ text = text, timeout = 5 })
    end)
end

local function stubPlugin()
    local WidgetContainer = require("ui/widget/container/widgetcontainer")
    local Stub = WidgetContainer:extend{ name = "tomesync", is_doc_only = false }
    function Stub:init() pcall(function() self.ui.menu:registerToMainMenu(self) end) end
    function Stub:addToMainMenu(menu_items)
        menu_items.tomesync = {
            text = "TomeSync (failed to load)",
            callback = function() notify("TomeSync failed to load and could not roll back.\nPlease reinstall the plugin.") end,
        }
    end
    return Stub
end

-- ── Next-boot rollback: an unconfirmed build that never confirmed crashed at init ──
local state = getState()
if state and not state.confirmed then
    state.boots = (state.boots or 0) + 1
    if state.boots >= 2 then
        if restoreBackup() then
            logger.warn("TomeSync: build", state.build, "never confirmed — rolling back")
            setState({ build = state.prev_build, confirmed = true })
            notify("TomeSync update failed — rolled back to previous version.")
        else
            setState(state)  -- no backup to restore; keep the bumped count
        end
    else
        setState(state)
    end
end

-- ── Load impl; same-boot rollback on a load/syntax failure ────────────────────
local ok, plugin = pcall(dofile, IMPL)
if not ok then
    logger.warn("TomeSync: impl failed to load:", tostring(plugin))
    local cur = getState()
    if restoreBackup() then
        setState({ build = (cur and cur.prev_build) or 0, confirmed = true })
        notify("TomeSync update failed — rolled back to previous version.")
        ok, plugin = pcall(dofile, IMPL)
    end
end

if not ok or type(plugin) ~= "table" then
    logger.warn("TomeSync: returning inert stub plugin")
    local sok, stub = pcall(stubPlugin)
    return sok and stub or nil
end

logger.info("TomeSync: shim loaded impl successfully")
return plugin
