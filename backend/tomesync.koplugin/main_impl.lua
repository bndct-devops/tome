--[[
TomeSync KOReader Plugin — implementation (replaced in place by self-update).
Syncs reading progress and sessions with a Tome library server.
Browse and download series. Tracks reading sessions and syncs position across devices.

Loaded by the frozen shim (main.lua). The connection it starts with is read
from config.lua beside it, which self-update never replaces.
]]

local logger = require("logger")
logger.info("TomeSync: main_impl.lua loading...")

local WidgetContainer = require("ui/widget/container/widgetcontainer")
local InfoMessage      = require("ui/widget/infomessage")
local Notification     = require("ui/widget/notification")
local UIManager        = require("ui/uimanager")
local Device           = require("device")
local NetworkMgr       = require("ui/network/manager")
local http             = require("socket.http")
local ltn12            = require("ltn12")
local mime             = require("mime")
local rapidjson        = require("rapidjson")
local lfs              = require("libs/libkoreader-lfs")
local util             = require("util")
local Menu             = require("ui/widget/menu")
local InputDialog      = require("ui/widget/inputdialog")
local ConfirmBox       = require("ui/widget/confirmbox")
local socketutil       = require("socketutil")
local Dispatcher       = require("dispatcher")
local Event            = require("ui/event")
local LuaSettings      = require("luasettings")
local DataStorage      = require("datastorage")
local ButtonDialog     = require("ui/widget/buttondialog")

-- ── Register in wrench menu (tools tab, after calibre) ──────────────────────
-- Runs once per KOReader process via require() caching.
do
    local reader_order = require("ui/elements/reader_menu_order")
    local fm_order = require("ui/elements/filemanager_menu_order")
    local function insert_after(order_table, section, after_item, new_item)
        local list = order_table[section]
        if not list then return end
        for _, v in ipairs(list) do
            if v == new_item then return end  -- already present
        end
        for i, v in ipairs(list) do
            if v == after_item then
                table.insert(list, i + 1, new_item)
                return
            end
        end
        table.insert(list, new_item)  -- fallback: append
    end
    insert_after(reader_order, "tools", "calibre", "tomesync")
    insert_after(fm_order, "tools", "calibre", "tomesync")
end

-- ── Config (config.lua beside this file) ────────────────────────────────────

-- TOMESYNC_CONFIG_BEGIN (plain Lua and io only; extracted and run under luajit
-- by the backend test suite)
--
-- The connection a plugin starts with lives in config.lua next to this file:
-- written when the plugin is made (Tome's zip download, the reader link's
-- bake) and never touched by self-update, which replaces main_impl.lua only.
-- Every value is optional. Without a config.lua the plugin is unconfigured
-- and asks to sign in. Settings > Server overrides all of it, as before.
local PluginConfig = {
    FIELDS = { SERVER_URL = "string", API_KEY = "string", USERNAME = "string",
               AUTO_DELIVER = "boolean", SERVER_FINGERPRINT = "string",
               LINK_HOSTS = "table" },
    ORDER  = { "SERVER_URL", "API_KEY", "USERNAME", "AUTO_DELIVER", "SERVER_FINGERPRINT" },
    -- LINK_HOSTS (build 55, the reader link): addresses of the phone to try
    -- when SERVER_URL does not answer, or when it is one of them anyway; a
    -- list of "https://host:port". Written only when not empty, so a Tome
    -- download's config.lua stays five lines.
    LINK_HOSTS_MAX = 8,
}

-- Keeps the https:// strings of a LINK_HOSTS list, in order, without
-- repeats; anything else in it is dropped.
function PluginConfig.linkHosts(v)
    if type(v) ~= "table" then return nil end
    local out, seen = {}, {}
    for _, h in ipairs(v) do
        if type(h) == "string" and h:match("^https://[^%s/]+$") and not seen[h]
                and #out < PluginConfig.LINK_HOSTS_MAX then
            seen[h] = true
            out[#out + 1] = h
        end
    end
    if #out == 0 then return nil end
    return out
end

-- The plugin's own directory, found the way the shim finds it.
function PluginConfig.dir()
    local source = debug.getinfo(1, "S").source
    return source:match("^@(.*)/[^/]+$") or "."
end

-- config.lua is data: it runs with nothing in scope, and only the five names,
-- each with its own type, are kept. Anything that fails reads as no value.
function PluginConfig.parse(text)
    if type(text) ~= "string" then return nil end
    local chunk = load(text, "=config.lua")
    if not chunk then return nil end
    local env = {}
    setfenv(chunk, env)
    if not pcall(chunk) then return nil end
    local out = {}
    for k, ty in pairs(PluginConfig.FIELDS) do
        if type(env[k]) == ty then out[k] = env[k] end
    end
    out.LINK_HOSTS = PluginConfig.linkHosts(out.LINK_HOSTS)
    return out
end

-- Builds up to 53 baked these values into main_impl.lua itself. Their
-- updater keeps the impl it replaces as main_impl.lua.bak, so the first load
-- of a newer impl reads them back from there.
function PluginConfig.fromBakedImpl(text)
    if type(text) ~= "string" then return nil end
    text = "\n" .. text
    local function str(name)
        local lit = text:match("\nlocal " .. name .. "%s*=%s*(\"[^\"\n]*\")")
        if not lit then return nil end
        local chunk = load("return " .. lit, "=baked")
        if not chunk then return nil end
        setfenv(chunk, {})
        local ok, v = pcall(chunk)
        return (ok and type(v) == "string") and v or nil
    end
    local key = str("DEFAULT_API_KEY")
    if not key then return nil end
    local out = {
        SERVER_URL         = str("DEFAULT_SERVER_URL"),
        API_KEY            = key,
        USERNAME           = str("DEFAULT_USERNAME"),
        SERVER_FINGERPRINT = str("SERVER_FINGERPRINT"),
    }
    local auto = text:match("\nlocal DEFAULT_AUTO_DELIVER%s*=%s*(%a+)")
    if auto == "true" then out.AUTO_DELIVER = true
    elseif auto == "false" then out.AUTO_DELIVER = false end
    return out
end

-- The text of a config.lua (%q quotes, backslashes and newlines safely).
function PluginConfig.render(cfg)
    local lines = { "-- TomeSync connection. Self-update never replaces this file." }
    for _, k in ipairs(PluginConfig.ORDER) do
        local v = cfg[k]
        if PluginConfig.FIELDS[k] == "boolean" then
            lines[#lines + 1] = k .. " = " .. tostring(v == true)
        else
            lines[#lines + 1] = k .. " = " .. string.format("%q", type(v) == "string" and v or "")
        end
    end
    local hosts = PluginConfig.linkHosts(cfg.LINK_HOSTS)
    if hosts then
        local q = {}
        for i, h in ipairs(hosts) do q[i] = string.format("%q", h) end
        lines[#lines + 1] = "LINK_HOSTS = { " .. table.concat(q, ", ") .. " }"
    end
    return table.concat(lines, "\n") .. "\n"
end

function PluginConfig.write(dir, cfg)
    local path = dir .. "/config.lua"
    local f = io.open(path .. ".new", "wb")
    if not f then return false end
    local ok = f:write(PluginConfig.render(cfg))
    f:close()
    if not ok or not os.rename(path .. ".new", path) then
        os.remove(path .. ".new")
        return false
    end
    return true
end

-- The config table and where it came from: "config" (config.lua), "baked"
-- (recovered from a pre-54 impl's backup, and written to config.lua so the
-- next update cannot lose it), "invalid" (a config.lua that does not parse)
-- or "none".
function PluginConfig.load(dir)
    local f = io.open(dir .. "/config.lua", "rb")
    if f then
        local text = f:read("*a")
        f:close()
        local cfg = PluginConfig.parse(text)
        if cfg then return cfg, "config" end
        return {}, "invalid"
    end
    local baked
    f = io.open(dir .. "/main_impl.lua.bak", "rb")
    if f then
        baked = PluginConfig.fromBakedImpl(f:read("*a"))
        f:close()
    end
    if not baked then return {}, "none" end
    PluginConfig.write(dir, baked)
    return baked, "baked"
end
-- TOMESYNC_CONFIG_END

local PLUGIN_CONFIG, PLUGIN_CONFIG_FROM = PluginConfig.load(PluginConfig.dir())
logger.info("TomeSync: connection config:", PLUGIN_CONFIG_FROM)

-- Defaults from config.lua; overridable in Settings > Server.
local DEFAULT_SERVER_URL = PLUGIN_CONFIG.SERVER_URL or ""
local DEFAULT_API_KEY    = PLUGIN_CONFIG.API_KEY or ""
local DEFAULT_USERNAME   = PLUGIN_CONFIG.USERNAME or ""
-- Inbox auto-pickup default (Settings > "Download inbox automatically" wins).
local DEFAULT_AUTO_DELIVER = PLUGIN_CONFIG.AUTO_DELIVER == true
-- Pinned TLS certificate (reader link): SHA-256, empty = no pin. A saved
-- tomesync_server_fingerprint (trusted at "Sign in with code") wins.
local SERVER_FINGERPRINT = PLUGIN_CONFIG.SERVER_FINGERPRINT or ""

local function liveSetting(key, default)
    local v = G_reader_settings:readSetting(key)
    if v == nil or v == "" then return default end
    return v
end
-- Connection settings. The server URL is user-editable in Settings so a device
-- whose baked address went stale can be repointed without reinstalling (it
-- cannot self-update its way out — updates come from the old, dead URL). The
-- API key and username are never typed: "Sign in with code" (Quick Connect
-- pairing) writes both, and the username is only ever the server's answer to
-- "who does this key belong to" — the key alone decides the account.
local function apiKey()        return liveSetting("tomesync_api_key",    DEFAULT_API_KEY) end
local function username()      return liveSetting("tomesync_username",   DEFAULT_USERNAME) end
local function hasOverrides()
    return G_reader_settings:readSetting("tomesync_server_url") ~= nil
        or G_reader_settings:readSetting("tomesync_fallback_url") ~= nil
        or G_reader_settings:readSetting("tomesync_api_key") ~= nil
        or G_reader_settings:readSetting("tomesync_username") ~= nil
        or G_reader_settings:readSetting("tomesync_server_fingerprint") ~= nil
end

-- TOMESYNC_PIN_BEGIN (pure string functions; extracted and run under luajit
-- by the backend test suite)
--
-- TLS: one table for the policy's parts (the main chunk is at its 200-local
-- limit). The pure ones are here; the rest follows the block.
local TLS = {}
--
-- Certificate fingerprints: SHA-256 as 64 hex digits, written any way
-- (AB:CD:.., ab cd .., abcd..). Anything else is "no fingerprint".
local function normFingerprint(s)
    if type(s) ~= "string" then return nil end
    local hex = (s:gsub("[%s:]", "")):lower()
    if #hex ~= 64 or hex:find("[^0-9a-f]") then return nil end
    return hex
end
local function fingerprintMatches(got, pinned)
    local a, b = normFingerprint(got), normFingerprint(pinned)
    return a ~= nil and a == b
end
-- "AB:CD:..", the first `groups` bytes (all 32 by default).
local function shortFingerprint(s, groups)
    local hex = normFingerprint(s)
    if not hex then return "?" end
    local out = {}
    for i = 1, math.min(groups or 32, 32) do out[i] = hex:sub(2 * i - 1, 2 * i):upper() end
    return table.concat(out, ":")
end
-- "AB:CD:EF:01..9A:BC:DE:F0": the first and the last 4 bytes, so two
-- fingerprints that differ only at the end still read differently in a log.
local function edgeFingerprint(s)
    local hex = normFingerprint(s)
    if not hex then return "?" end
    local function colon(h)
        local out = {}
        for i = 1, #h, 2 do out[#out + 1] = h:sub(i, i + 1):upper() end
        return table.concat(out, ":")
    end
    return colon(hex:sub(1, 8)) .. ".." .. colon(hex:sub(-8))
end
-- The TLS policy (build 56), as one decision. t.https, t.pin (the pinned
-- fingerprint or nil), t.link (a reader link address: the phone), t.got (the
-- certificate's SHA-256 or nil), t.ca (true: a CA in the bundle signed it for
-- this host name; false: none did, or not for this name; nil: no bundle).
--   http                                       -> "plain"   no TLS at all
--   https + pin, certificate is the pin        -> "pinned"  go on
--   https + pin, another one, reader link      -> "refuse"  never asks
--   https + pin, another one, any other server -> "ask"     it changed: ask again
--   https, no pin, CA-verified for the host    -> "ca"      go on, unpinned
--   https, no pin, CA check failed             -> "ask"
--   https, no pin, no CA bundle on the device  -> "ask"
-- "ask": nothing is sent. A user action shows the trust prompt (Trust pins
-- the certificate); a background pass skips the request quietly.
function TLS.decision(t)
    if not t.https then return "plain" end
    if normFingerprint(t.pin) then
        if fingerprintMatches(t.got, t.pin) then return "pinned" end
        if t.link then return "refuse" end
        return "ask"
    end
    if t.ca == true then return "ca" end
    return "ask"
end
-- Does a CA-verified certificate name this host? names: its subjectAltName
-- DNS and IP entries, else its common name. "*.example.com" covers one
-- leftmost label, never an IP address. Case and a trailing dot ignored.
function TLS.hostMatches(host, names)
    if type(host) ~= "string" or host == "" or type(names) ~= "table" then return false end
    host = (host:lower():gsub("%.$", ""))
    local is_ip = host:match("^[%d%.]+$") or host:find(":", 1, true)
    for _, n in ipairs(names) do
        if type(n) == "string" and n ~= "" then
            n = (n:lower():gsub("%.$", ""))
            if n == host then return true end
            local rest = n:match("^%*%.(.+%..+)$")
            if rest and not is_ip and host:match("^[^%.]+%.(.+)$") == rest then return true end
        end
    end
    return false
end
-- TOMESYNC_PIN_END

-- ── TLS policy (build 56) ───────────────────────────────────────────────────
-- Every https request is made by tlsCreateFor's `create`; TLS.decision above
-- is the whole rule set.
--  * Pinned (a fingerprint saved at a trust prompt, or baked in by Tome
--    Reader): LuaSec without CA checks (the pin is the check); after the
--    handshake, before a byte of the request is sent, the certificate's
--    SHA-256 must equal the pin.
--  * Not pinned: the certificate is checked against the device's CA bundle
--    (KOReader's data/ca-bundle.crt, else the usual system files; looked up
--    once per process) and must name the host. A CA-signed server goes on
--    exactly as before, unpinned.
--  * Not trusted (no bundle, no CA signed it, wrong name, or a pinned
--    certificate that changed on an ordinary Tome server): the request is not
--    sent. At the next user-visible moment (Sign in with code, Sync now and
--    every other TomeSync action, the TomeSync menu opening) the trust prompt
--    shows the fingerprint; Trust saves it as tomesync_server_fingerprint and
--    from then on the server is pinned. Background passes never prompt: they
--    skip the request with one logged line ("certificate not trusted yet"),
--    and it does not count as a miss, so no back-off window opens.
--  * The reader link (Tome Reader's addresses: LINK_HOSTS, or the baked
--    SERVER_URL with its baked pin) never asks: a different certificate there
--    is refused, logged with the head and tail of both fingerprints.
-- http is untouched: tlsCreateFor returns nil and LuaSocket runs as before.
local PIN_REFUSED   = "certificate fingerprint mismatch"
TLS.UNTRUSTED = "certificate not trusted yet"
TLS.RECHECK = 900      -- seconds a "not trusted" verdict is kept before a new handshake
TLS.state   = {}       -- base -> { verdict = "ok" | "ask", digest, changed, why, at, logged_at, asked }
TLS.logged  = {}       -- base .. verdict -> true: "fingerprint ok" / "CA ok" once per process
local function pinnedFingerprint()
    return normFingerprint(liveSetting("tomesync_server_fingerprint", SERVER_FINGERPRINT))
end
-- The CA bundle, looked up once per process; nil when the device has none.
function TLS.bundle()
    if TLS.cafile == nil then
        TLS.cafile = false
        for _, f in ipairs({ "data/ca-bundle.crt", "/etc/ssl/certs/ca-certificates.crt",
                              "/etc/ssl/cert.pem", "/etc/pki/tls/certs/ca-bundle.crt" }) do
            if lfs.attributes(f, "mode") == "file" then TLS.cafile = f; break end
        end
        logger.info("TomeSync: CA bundle for https servers: " .. (TLS.cafile or "none found"))
    end
    return TLS.cafile or nil
end
-- A reader link address (the phone): pinned by the bake, never asks.
function TLS.isLink(base)
    for _, h in ipairs(PLUGIN_CONFIG.LINK_HOSTS or {}) do
        if h == base then return true end
    end
    return normFingerprint(SERVER_FINGERPRINT) ~= nil and base == DEFAULT_SERVER_URL
end
-- The names a certificate is for: subjectAltName DNS and IP entries, else CN.
function TLS.names(cert)
    local names = {}
    pcall(function()
        local san = (cert:extensions() or {})["2.5.29.17"]
        if type(san) == "table" then
            for _, kind in ipairs({ "dNSName", "iPAddress" }) do
                for _, n in ipairs(type(san[kind]) == "table" and san[kind] or {}) do
                    table.insert(names, n)
                end
            end
        end
        if #names == 0 then
            for _, e in ipairs(cert:subject() or {}) do
                if e.name == "commonName" or e.oid == "2.5.4.3" then table.insert(names, e.value) end
            end
        end
    end)
    return names
end
-- The CA verdict of a finished handshake: true, false (why), or nil (no bundle).
function TLS.caVerdict(sock, host, cert, cafile)
    if not cafile then return nil, "no CA bundle on this device" end
    if sock:getpeerverification() ~= true then return false, "no authority in " .. cafile .. " signed it" end
    if not TLS.hostMatches(host, TLS.names(cert)) then return false, "it is not for " .. tostring(host) end
    return true
end
-- One line at most every 60 s per address, so a pass logs it once.
function TLS.noteUntrusted(base, st)
    if st.logged_at and os.time() - st.logged_at < 60 then return end
    st.logged_at = os.time()
    logger.info("TomeSync: " .. base .. ": " .. TLS.UNTRUSTED .. " ("
                .. (st.changed and "the pinned certificate changed: " or "")
                .. edgeFingerprint(st.digest) .. ", " .. tostring(st.why)
                .. "); request skipped, TomeSync asks at the next Sync now or menu open")
end
function TLS.markUntrusted(base, digest, changed, why)
    local st = TLS.state[base]
    if not (st and st.verdict == "ask" and st.digest == digest) then st = {} end
    st.verdict, st.digest, st.changed, st.why, st.at = "ask", digest, changed, why, os.time()
    TLS.state[base] = st
    TLS.noteUntrusted(base, st)
    return st
end
function TLS.markOk(base, verdict, text)
    TLS.state[base] = { verdict = "ok", at = os.time() }
    if not TLS.logged[base .. verdict] then
        TLS.logged[base .. verdict] = true
        logger.info("TomeSync: " .. text .. " for " .. base .. ", once per process")
    end
end
local function tlsCreateFor(base)
    if type(base) ~= "string" or not base:lower():match("^https://") then return nil end
    local st = TLS.state[base]
    if st and st.verdict == "ask" and os.time() - st.at < TLS.RECHECK then
        TLS.noteUntrusted(base, st)
        return function() return nil, TLS.UNTRUSTED end
    end
    local pin  = pinnedFingerprint()
    local link = TLS.isLink(base)
    local cafile = (not pin) and TLS.bundle() or nil
    local make = require("ssl.https").tcp(cafile
        and { verify = "peer", verifyext = { "lsec_continue" }, cafile = cafile }
        or  { verify = "none" })
    return function()
        local conn = make()
        local connect = conn.connect
        conn.connect = function(self, host, port)
            connect(self, host, port)   -- TCP + handshake; raises (socket.try) on failure
            local cert = self.sock.getpeercertificate and self.sock:getpeercertificate()
            local got = cert and cert:digest("sha256")
            local ca, why
            if not pin and cert then ca, why = TLS.caVerdict(self.sock, host, cert, cafile) end
            local verdict = TLS.decision({ https = true, pin = pin, link = link, got = got, ca = ca })
            if verdict == "pinned" then
                TLS.markOk(base, verdict, "fingerprint ok (" .. shortFingerprint(pin, 8) .. "..)")
                return 1
            elseif verdict == "ca" then
                TLS.markOk(base, verdict, "certificate verified against " .. cafile)
                return 1
            end
            pcall(self.sock.close, self.sock)
            if verdict == "refuse" then
                logger.warn("TomeSync: refused " .. base .. ": certificate "
                            .. edgeFingerprint(got) .. " is not the pinned "
                            .. edgeFingerprint(pin))
                return nil, PIN_REFUSED
            end
            TLS.markUntrusted(base, got, pin ~= nil,
                pin and ("pinned " .. edgeFingerprint(pin)) or why or "no certificate")
            return nil, TLS.UNTRUSTED
        end
        return conn
    end
end

-- One handshake to an https address for a trust decision (a user action):
-- { digest, ca, why } (ca as TLS.caVerdict gives it) or nil, why when the
-- server did not answer. Never sends a request.
function TLS.probe(base)
    local u = require("socket.url").parse(base)
    if not u or not u.host then return nil, "bad url" end
    local cafile = TLS.bundle()
    local socket, ssl = require("socket"), require("ssl")
    local tcp = socket.tcp()
    tcp:settimeout(5)
    local ok, err = tcp:connect(u.host, tonumber(u.port) or 443)
    if not ok then tcp:close(); return nil, err end
    local params = { mode = "client", protocol = "any",
        options = { "all", "no_sslv2", "no_sslv3", "no_tlsv1" }, verify = "none" }
    if cafile then
        params.verify, params.verifyext, params.cafile = "peer", { "lsec_continue" }, cafile
    end
    local s, werr = ssl.wrap(tcp, params)
    if not s then tcp:close(); return nil, werr end
    s:sni(u.host)
    s:settimeout(5)
    local hok, herr = s:dohandshake()
    if not hok then s:close(); return nil, herr end
    local cert = s:getpeercertificate()
    if not cert then s:close(); return nil, "no certificate" end
    local ca, why = TLS.caVerdict(s, u.host, cert, cafile)
    s:close()
    return { digest = cert:digest("sha256"), ca = ca, why = why }
end

-- TOMESYNC_NET_BEGIN (uses only liveSetting, DEFAULT_SERVER_URL,
-- G_reader_settings, socketutil, http, ltn12, logger, tlsCreateFor,
-- PIN_REFUSED and TLS (its UNTRUSTED) — extracted and run under luajit by the
-- backend test suite with fakes for those)
--
-- Every request runs on KOReader's UI thread, so a server that does not
-- answer while the device is online freezes the screen for the whole connect
-- timeout (issue #249). Offline costs nothing (apiRequest returns before
-- touching the network); "online but Tome unreachable" is what this guards.
--
-- Back-off: after ONE request that got no answer, requests are skipped for a
-- while, and the wait doubles with each further miss (1, 2, 4, 8 min, capped
-- at 15). Time-based, never a latch: once the window passes the next request
-- goes through as the probe. A success, WiFi reconnecting, or any interactive
-- action (whenConnected) clears it. Builds before 49 waited for three misses
-- with a flat 60 s window, so a dead server froze the reader about once a
-- minute and up to three times in a row on book open.
local BACKOFF_BASE  = 60    -- seconds after the first miss
local BACKOFF_MAX   = 900   -- cap: one probe every 15 minutes at most
local net_failures  = 0     -- requests in a row that got no answer
local backoff_until = 0     -- os.time() before which requests are skipped

local function backoffSeconds(failures)
    return math.min(BACKOFF_BASE * 2 ^ (failures - 1), BACKOFF_MAX)
end
local function noteNetFailure()
    net_failures = net_failures + 1
    backoff_until = os.time() + backoffSeconds(net_failures)
end
local function resetBackoff()
    net_failures = 0
    backoff_until = 0
end
local function inBackoff()
    return net_failures > 0 and os.time() < backoff_until
end

-- Server addresses. The main URL is user-editable in Settings so a device
-- whose baked address went stale can be repointed without reinstalling (it
-- cannot self-update its way out — updates come from the old, dead URL). An
-- optional fallback URL covers networks where the main one does not answer:
-- a router without hairpin NAT, a VPN address only reachable from outside
-- (issue #249). The address that last answered is tried first and remembered
-- across restarts (tomesync_active_url), so a device on one network pays for
-- the dead address once per network switch, not on every request.
local function primaryURL() return liveSetting("tomesync_server_url", DEFAULT_SERVER_URL) end
local function fallbackURL()
    local v = G_reader_settings:readSetting("tomesync_fallback_url")
    if v == nil or v == "" or v == primaryURL() then return nil end
    return v
end
-- The reader link's address for this session (build 55): a LINK_HOSTS entry
-- that answered device-info (the phone on its hotspot or its last Wi-Fi
-- address). While set, every request goes there and nowhere else; it is
-- dropped when it stops answering and when the network changes, and never
-- saved, so a restart starts from SERVER_URL again.
local LinkSession = { url = nil }
-- Addresses in the order to try them: the one that last answered first.
local function serverURLs()
    if LinkSession.url then return { LinkSession.url } end
    local primary, fallback = primaryURL(), fallbackURL()
    if not fallback then return { primary } end
    if G_reader_settings:readSetting("tomesync_active_url") == fallback then
        return { fallback, primary }
    end
    return { primary, fallback }
end
-- The address requests go to right now (also what the menus show).
local function serverURL() return serverURLs()[1] end
local function rememberURL(base)
    local v = (base ~= primaryURL()) and base or nil
    if G_reader_settings:readSetting("tomesync_active_url") ~= v then
        if v then
            G_reader_settings:saveSetting("tomesync_active_url", v)
        else
            G_reader_settings:delSetting("tomesync_active_url")
        end
    end
end

-- One HTTP exchange with Tome, trying each address in turn while there is
-- no answer at all (an HTTP error status IS an answer and is returned as-is).
-- spec: method, path (e.g. "/api/health"), headers, body (string or nil),
-- sink (a function returning a fresh ltn12 sink per attempt — a failed
-- attempt may have written into the previous one), block / total timeouts.
-- Returns ok, code exactly like http.request. Does not check the back-off
-- window: background callers do that (apiRequest), interactive ones do not.
local function serverRequest(spec)
    local ok, code
    local refused, missed = false, false
    for _, base in ipairs(serverURLs()) do
        socketutil:set_timeout(spec.block, spec.total)
        ok, code = http.request({
            url     = base .. spec.path,
            method  = spec.method,
            headers = spec.headers,
            source  = spec.body and ltn12.source.string(spec.body) or nil,
            sink    = spec.sink(),
            create  = tlsCreateFor(base),   -- nil unless https with a pinned certificate
        })
        socketutil:reset_timeout()
        if ok then
            if base ~= LinkSession.url then rememberURL(base) end
            resetBackoff()
            return ok, code
        end
        -- A pin refusal was logged once where it happened; it still counts
        -- as no answer (back-off), so a wrong peer is not asked every second.
        -- A certificate not trusted yet (TLS policy) was logged there too and
        -- is no miss: nothing was sent, and a trust prompt fixes it at once.
        if code == TLS.UNTRUSTED then
            -- (nothing: no miss)
        elseif code == PIN_REFUSED then
            refused, missed = true, true
        else
            missed = true
            logger.warn("TomeSync: no answer from", base, tostring(code))
        end
    end
    if not missed then return ok, code end
    if LinkSession.url then
        logger.info("TomeSync: reader link via " .. LinkSession.url .. " stopped answering; back to the configured server")
        LinkSession.url = nil
    end
    noteNetFailure()
    if not refused then
        logger.warn("TomeSync: server unreachable, next try in",
                    backoffSeconds(net_failures), "s (miss " .. net_failures .. ")")
    end
    return ok, code
end
-- TOMESYNC_NET_END

-- The trust prompt (TLS policy above), at a user-visible moment: then runs
-- fn (may be nil) once the server is trusted, at once when nothing needs
-- asking. opts.no_network: only ask about what a request already found (the
-- menu opening never touches the network); opts.once: ask about one
-- certificate once per process (the menu opens often).
function TLS.trustThen(fn, opts)
    opts = opts or {}
    local base = serverURL()
    local function run() if fn then return fn() end end
    if type(base) ~= "string" or not base:lower():match("^https://") or TLS.isLink(base) then
        return run()
    end
    local st = TLS.state[base]
    if not (st and st.verdict == "ask") then
        -- Every user action checks afresh (one handshake): a certificate
        -- that changed since the last request is asked about now, not after
        -- the action failed.
        if opts.no_network or not NetworkMgr:isConnected() then return run() end
        local probe, why = TLS.probe(base)
        if not probe then
            -- No answer: the action fails as it always did, at once instead
            -- of after a second timeout (unless a fallback address may answer).
            logger.info("TomeSync: certificate check of " .. base .. ": no answer (" .. tostring(why) .. ")")
            if not fallbackURL() then noteNetFailure() end
            return run()
        end
        local pin = pinnedFingerprint()
        local verdict = TLS.decision({ https = true, pin = pin, got = probe.digest, ca = probe.ca })
        if verdict ~= "ask" then
            TLS.markOk(base, verdict, verdict == "ca" and ("certificate verified against " .. tostring(TLS.cafile))
                                       or ("fingerprint ok (" .. shortFingerprint(pin, 8) .. "..)"))
            return run()
        end
        st = TLS.markUntrusted(base, probe.digest, pin ~= nil,
            pin and ("pinned " .. edgeFingerprint(pin)) or probe.why)
    end
    if opts.once and st.asked == st.digest then return end
    st.asked = st.digest
    local digest = st.digest
    local head = st.changed
        and "This server's certificate has changed since you trusted it.\n\n"
        or  "This server's certificate is not signed by an authority this reader knows.\n\n"
    logger.info("TomeSync: asking to trust " .. base .. " (" .. shortFingerprint(digest) .. ")")
    UIManager:show(ConfirmBox:new{
        text = head .. base .. "\n"
               .. "Fingerprint (SHA-256):\n" .. shortFingerprint(digest, 8) .. "\u{2026}\n"
               .. edgeFingerprint(digest) .. "\n\n"
               .. "If this is the Tome app on your phone, it shows the same "
               .. "fingerprint. Trust this server?",
        ok_text = "Trust",
        ok_callback = function()
            G_reader_settings:saveSetting("tomesync_server_fingerprint", shortFingerprint(digest))
            G_reader_settings:flush()
            TLS.state[base] = nil
            TLS.logged[base .. "pinned"] = nil
            logger.info("TomeSync: trusted " .. base .. " with fingerprint " .. shortFingerprint(digest))
            run()
        end,
        cancel_callback = function()
            logger.info("TomeSync: " .. base .. " not trusted; its requests stay off until it is")
        end,
    })
end

-- Chunk-local (shared across plugin instances): dedupes _initSession when the
-- ReaderReady event reaches more than one live TomeSync instance for the same
-- open. Cleared in onCloseDocument so an immediate reopen still inits.
local last_session_init = { book_id = nil, at = 0 }
-- Once-per-process guard for the state prune (init runs per reader instance).
local state_pruned = false
-- Library-sweep latch: one sweep at a time per KOReader process.
local sweep_running = false
local meta_sync_running = false
local meta_sync_launched = false
local meta_last_auto = 0     -- os.time() of the last automatic run (debounce)
-- Device inventory (build 50): one launch push per process, one pending
-- debounced push shared by every TomeSync instance (file manager + reader).
local inventory_launched = false
local inventory_task = nil
local SWEEP_EXTS = { epub = true, pdf = true, cbz = true, cbr = true, mobi = true, azw3 = true }

-- ── HTTP client ──────────────────────────────────────────────────────────────

local HEARTBEAT_PAGES = 50
-- Longest credible gap between page turns. Anything beyond it is idle time
-- (device left awake, reader asleep) and is not booked to the session. The
-- user can change or disable this in settings; 0 = off.
local IDLE_CAP_DEFAULT_MINUTES = 10
local PLUGIN_VERSION  = "56"
local BUILD           = 56      -- monotonic; the only thing compared
local SEMVER          = "1.19.2"   -- human-facing display only

local function urlEncode(s)
    return s:gsub("([^%w%-%.%_%~])", function(c)
        return string.format("%%%02X", string.byte(c))
    end)
end

-- rapidjson decodes JSON null to a sentinel (rapidjson.null), not Lua nil.
-- Normalize it so "no rating" compares equal to an absent baseline value.
local function jval(v)
    if v == nil or v == rapidjson.null then return nil end
    return v
end

-- A non-empty string, else nil. JSON null is a truthy userdata, so `a or b`
-- alone lets a null win over a real fallback (issue #259).
local function jstr(v)
    if type(v) == "string" and v ~= "" then return v end
    return nil
end

-- Device identity as reported to Tome: the label on live sessions and imported
-- history, and the key of this device's reading-history sync watermark. A
-- user-set name (Settings > Device name) wins; otherwise KOReader's
-- Device.model ("KindlePaperWhite4", "Kobo_clara", an Android product name);
-- "KOReader" only when neither exists. Builds before 40 always reported the
-- literal "KOReader" (they called a Device method that does not exist in
-- KOReader), so every device of a user shared ONE history watermark - see
-- _adoptDeviceName for the one-time migration.
local LEGACY_DEVICE_NAME = "KOReader"
local function autoDeviceName()
    local model = Device and Device.model
    if type(model) == "string" and model ~= "" then return model end
    return LEGACY_DEVICE_NAME
end
local function deviceName()
    local custom = G_reader_settings:readSetting("tomesync_device_name")
    if type(custom) == "string" and custom ~= "" then return custom end
    return autoDeviceName()
end

-- A random id for this KOReader install (build 55), made once and kept in
-- its settings: a server that sees the device under two keys (installed by
-- the phone, then paired by code) knows it is one reader. Sent in
-- device-info and in the Quick Connect initiate body; never shown.
-- (A field of LinkSession, not a local: the main chunk is at its 200-local limit.)
function LinkSession.deviceID()
    local id = G_reader_settings:readSetting("tomesync_device_id")
    if type(id) == "string" and id:match("^%x+%-%x+%-%x+%-%x+%-%x+$") then return id end
    local bytes
    local f = io.open("/dev/urandom", "rb")
    if f then bytes = f:read(16); f:close() end
    if type(bytes) ~= "string" or #bytes ~= 16 then
        math.randomseed(os.time() + math.floor((os.clock() * 1e6) % 1e6))
        local t = {}
        for i = 1, 16 do t[i] = string.char(math.random(0, 255)) end
        bytes = table.concat(t)
    end
    local b = { bytes:byte(1, 16) }
    b[7] = (b[7] % 16) + 64           -- version 4
    b[9] = (b[9] % 64) + 128          -- RFC 4122 variant
    local hex = {}
    for i = 1, 16 do hex[i] = string.format("%02x", b[i]) end
    id = table.concat(hex, "", 1, 4) .. "-" .. table.concat(hex, "", 5, 6) .. "-"
        .. table.concat(hex, "", 7, 8) .. "-" .. table.concat(hex, "", 9, 10) .. "-"
        .. table.concat(hex, "", 11, 16)
    G_reader_settings:saveSetting("tomesync_device_id", id)
    G_reader_settings:flush()
    logger.info("TomeSync: device id made: " .. id)
    return id
end

local function idleCapSeconds()
    local mins = G_reader_settings:readSetting("tomesync_idle_cap_minutes")
    if mins == nil then mins = IDLE_CAP_DEFAULT_MINUTES end
    if mins == 0 then return math.huge end
    return mins * 60
end

-- A table sink that refuses a body over 8 MB (the request then fails like a
-- dropped connection), so a server cannot fill the device's memory. Every
-- JSON answer and the self-update text go through it; downloads stream to a file.
local API_BODY_MAX = 8 * 1024 * 1024
local function cappedTableSink(chunks)
    local inner, got = ltn12.sink.table(chunks), 0
    return function(chunk, err)
        if chunk then
            got = got + #chunk
            if got > API_BODY_MAX then
                logger.warn("TomeSync: answer over 8 MB, dropped")
                return nil, "response over 8 MB"
            end
        end
        return inner(chunk, err)
    end
end

-- opts.token: bearer override for the few calls that authenticate with
-- something other than the plugin API key (the Quick Connect pairing JWT).
-- opts.block / opts.total: tighter timeouts (the reader link's poll uses 2 s;
-- every request here blocks KOReader's UI thread).
local function apiRequest(method, path, body, opts)
    -- Skip immediately if WiFi is not connected — zero blocking
    if not NetworkMgr:isConnected() then
        return nil, "offline"
    end

    -- Skip requests while the back-off window is open (see TOMESYNC_NET).
    -- Once it expires the next request goes through as the probe.
    if inBackoff() then
        logger.dbg("TomeSync: skipping request (server unreachable, backing off)")
        return nil, "backoff"
    end

    local req_body = body and rapidjson.encode(body) or nil
    local resp_chunks

    local headers = {
        ["Authorization"] = "Bearer " .. ((opts and opts.token) or apiKey()),
        ["Content-Type"]  = "application/json",
        ["Accept"]        = "application/json",
    }
    if req_body then
        headers["Content-Length"] = tostring(#req_body)
    end

    -- Bounded per-request timeouts (block, total) via socketutil — no global
    -- http.TIMEOUT mutation. Block 5s catches a dead route fast; the total is
    -- deliberately generous: annotation-sync responses can be large and slow
    -- device wifi through an HTTPS proxy must not get truncated mid-body.
    local ok, code = serverRequest({
        method  = method,
        path    = "/api" .. path,
        headers = headers,
        body    = req_body,
        sink    = function()
            resp_chunks = {}
            return cappedTableSink(resp_chunks)
        end,
        block   = (opts and opts.block) or 5,
        total   = (opts and opts.total) or 45,
    })
    if not ok then return nil, code end

    local resp_body = table.concat(resp_chunks)
    if code == 404 then return nil, 404 end
    if code >= 200 and code < 300 then
        local ok2, parsed = pcall(rapidjson.decode, resp_body)
        if ok2 then return parsed, code end
        return {}, code
    end

    logger.warn("TomeSync: HTTP", code, resp_body)
    return nil, code
end

-- ── Format preference & download helpers ────────────────────────────────────

local FORMAT_PREFERENCE = {"epub", "kepub.epub", "cbz", "pdf", "mobi", "azw3"}

local function pickBestFile(files)
    if not files or #files == 0 then return nil end
    for _, fmt in ipairs(FORMAT_PREFERENCE) do
        for _, f in ipairs(files) do
            if f.format == fmt then return f end
        end
    end
    return files[1]
end

local function downloadFile(book_id, file_id, dest_path, total_size, progress_cb)
    if not NetworkMgr:isConnected() then
        return false, "offline"
    end

    -- Opened per attempt ("wb" truncates), so bytes from an attempt that
    -- broke off on one address never end up in front of the next one's.
    local fh
    local function makeSink()
        if fh then pcall(fh.close, fh) end
        fh = io.open(dest_path, "wb")
        if not fh then error("cannot open file for writing") end
        -- Count bytes as they stream to disk; repaint at most every 5% (known
        -- size) or 256 KB (unknown) so e-ink isn't flooded — a 300 MB CBZ
        -- used to sit mute for minutes with no sign of life.
        local sink = ltn12.sink.file(fh)
        if not progress_cb then return sink end
        local base_sink, received, last_bucket = sink, 0, 0
        return function(chunk, err)
            if chunk then
                received = received + #chunk
                local bucket
                if type(total_size) == "number" and total_size > 0 then
                    bucket = math.floor(received * 20 / total_size)
                else
                    bucket = math.floor(received / 262144)
                end
                if bucket ~= last_bucket then
                    last_bucket = bucket
                    pcall(progress_cb, received, total_size)
                end
            end
            return base_sink(chunk, err)
        end
    end

    -- Generous total budget for large files; the block timeout still catches
    -- a stalled connection quickly (no global http.TIMEOUT mutation).
    local rok, ok, code = pcall(serverRequest, {
        method  = "GET",
        path    = "/api/tome-sync/download/" .. urlEncode(tostring(book_id)) .. "/" .. urlEncode(tostring(file_id)),
        headers = { ["Authorization"] = "Bearer " .. apiKey() },
        sink    = makeSink,
        block   = 15,
        total   = 900,
    })
    -- ltn12 closes the file when a body completes; an attempt that got no
    -- answer leaves it open. pcall: closing a closed file raises.
    if fh then pcall(fh.close, fh) end
    if not rok then
        socketutil:reset_timeout()
        os.remove(dest_path)
        return false, tostring(ok)
    end

    if not ok or (type(code) == "number" and code >= 300) then
        os.remove(dest_path)
        return false, tostring(code or "request failed")
    end

    return true
end

-- ── Download path templates ──────────────────────────────────────────────────

-- mkdir -p: create every missing directory level of an absolute path.
local function mkdirp(path)
    local acc = path:sub(1, 1) == "/" and "/" or ""
    for seg in path:gmatch("[^/]+") do
        acc = acc .. seg
        lfs.mkdir(acc)
        acc = acc .. "/"
    end
end

-- TOMESYNC_TEMPLATE_BEGIN (kept dependency-free except util — extracted and
-- unit-tested standalone by the backend test suite)
-- Render a download path template into a relative path (no extension).
-- Tokens: {book_type} {series} {title} {author} {volume} {volume:00}
-- plus {Lower(token)} / {Upper(token)}. "/" separates folders. Empty tokens
-- are dropped along with orphaned ASCII separators around them, so one
-- template works for series books and standalones alike. Every segment is
-- sanitized, so a template can never escape the base directory. Returns nil
-- when a token is unknown or the whole template renders to nothing usable —
-- callers fall back to the built-in layout.
local function renderDownloadPath(template, ctx)
    local function fmtVolume(spec)
        local vol = ctx.volume
        if type(vol) ~= "number" then return "" end
        if vol == math.floor(vol) then
            local pad = spec and spec:match("^:(0+)$")
            if pad then return string.format("%0" .. #pad .. "d", vol) end
            return tostring(math.floor(vol))
        end
        return tostring(vol)
    end
    local function tokenValue(name)
        local case
        local inner = name:match("^Lower%((.+)%)$")
        if inner then case, name = "lower", inner end
        if not case then
            inner = name:match("^Upper%((.+)%)$")
            if inner then case, name = "upper", inner end
        end
        local val
        if name == "volume" or name:match("^volume:") then
            val = fmtVolume(name:match("^volume(:.*)$"))
        elseif name == "book_type" then val = ctx.book_type or ""
        elseif name == "series" then val = ctx.series or ""
        elseif name == "title" then val = ctx.title or ""
        elseif name == "author" then val = ctx.author or ""
        else return nil end
        if case == "lower" then val = val:lower()
        elseif case == "upper" then val = val:upper() end
        -- A "/" inside a value must not create folders (only template
        -- slashes separate segments).
        return (val:gsub("/", "-"))
    end
    local unknown = false
    local rendered = template:gsub("%{(.-)%}", function(name)
        local v = tokenValue(name)
        if v == nil then unknown = true; return "" end
        return v
    end)
    if unknown then return nil end
    local segments = {}
    for seg in rendered:gmatch("[^/]+") do
        seg = seg:gsub("%s+", " ")
        -- Collapse separator runs left behind by empty tokens ("a - - b"),
        -- then strip orphaned separators at the ends. ASCII-only on purpose:
        -- multibyte chars in Lua pattern classes match stray bytes.
        local prev
        repeat
            prev = seg
            seg = seg:gsub("([%-_,])%s*[%-_,]", "%1")
        until seg == prev
        seg = seg:gsub("^[%s%-_,.]+", ""):gsub("[%s%-_,.]+$", "")
        seg = util.getSafeFilename(seg)
        if seg ~= "" and seg ~= "." and seg ~= ".." then
            table.insert(segments, seg)
        end
    end
    if #segments == 0 then return nil end
    return table.concat(segments, "/")
end
-- TOMESYNC_TEMPLATE_END

-- Preset for "Flat in home folder": no folders, series + volume in the
-- filename so flat layouts cannot collide across series. Separator-only
-- between tokens (no glued literals like "Vol."), so empty tokens collapse
-- cleanly for standalones. ASCII separators only (see the cleanup note in
-- renderDownloadPath).
local FLAT_TEMPLATE = "{series} - {volume:00} - {title}"

-- ── Connectivity (issue #38) ─────────────────────────────────────────────────
-- Interactive entry points route through this instead of hitting the network
-- directly. Default: run the action as-is, so the existing isConnected guards
-- behave exactly as before. With "Auto-connect WiFi" enabled, KOReader brings
-- the connection back up first (per the user's wifi_enable_action setting) and
-- runs the action once connected — devices like PocketBook aggressively sleep
-- the radio, which otherwise leaves every action failing with "offline".
local function whenConnected(fn)
    -- Something the user asked for always really tries: it clears the
    -- back-off window (issue #249), so "Sync now" right after the server
    -- comes back does not wait out the background retry timer.
    resetBackoff()
    -- A certificate not trusted yet is asked about first (TLS policy).
    local function run() TLS.trustThen(fn) end
    if G_reader_settings:isTrue("tomesync_auto_connect")
            and not NetworkMgr:isConnected() then
        NetworkMgr:runWhenConnected(run)
        return
    end
    run()
end

-- ── Self-update helpers ──────────────────────────────────────────────────────

local function implDir()
    local source = debug.getinfo(1, "S").source
    return source:match("^@(.*)/[^/]+$") or "."
end

local IMPL_PATH = implDir() .. "/main_impl.lua"
local IMPL_BAK  = IMPL_PATH .. ".bak"

local function readWhole(path)
    local f = io.open(path, "rb"); if not f then return nil end
    local d = f:read("*a"); f:close(); return d
end

local function writeWhole(path, data)
    local f = io.open(path, "wb"); if not f then return false end
    f:write(data); f:close(); return true
end

-- Raw (non-JSON) authenticated GET, used to fetch the new impl text.
local function fetchText(path)
    if not NetworkMgr:isConnected() then return nil, "offline" end
    local chunks
    local ok, code = serverRequest({
        method  = "GET",
        path    = "/api" .. path,
        headers = { ["Authorization"] = "Bearer " .. apiKey() },
        sink    = function()
            chunks = {}
            return cappedTableSink(chunks)
        end,
        block   = 10,
        total   = 60,
    })
    if not ok then return nil, code end
    if type(code) == "number" and code >= 300 then return nil, code end
    return table.concat(chunks), code
end

-- Reject anything that isn't a plausible, compilable impl before swapping it in.
local function validateImpl(body)
    if not body or #body < 15000 then return false, "too small" end
    if not load(body) then return false, "does not compile" end
    if not body:find("function TomeSync:init", 1, true) then return false, "missing init" end
    if not body:find("return TomeSync", 1, true) then return false, "missing return" end
    return true
end

-- ── Plugin widget ────────────────────────────────────────────────────────────

local TomeSync = WidgetContainer:extend{
    name        = "tomesync",
    is_doc_only = false,
}

function TomeSync:init()
    self.book_id        = nil
    self.session_start  = nil
    self.page_count     = 0
    self.active_seconds = 0
    self.last_activity  = nil
    self.progress_start = nil
    self.last_progress  = nil
    self.enabled        = true
    -- Dedicated state file: the plugin's data tables live in their own
    -- LuaSettings file, NOT in G_reader_settings — KOReader parses the global
    -- settings file at every boot, and these tables grow with the library.
    -- (tomesync_update stays global: the frozen shim reads it and is never
    -- replaced by self-update.)
    self.state = LuaSettings:open(DataStorage:getSettingsDir() .. "/tomesync_state.lua")
    self:_migrateState()
    self.book_map       = self.state:readSetting("tomesync_book_map") or {}
    self.pending_sessions = self.state:readSetting("tomesync_pending_sessions") or {}
    -- Positions that failed to PUT, latest per book (string book_id ->
    -- { pc=, xp=, t= }). Only the sync code reads these; see _putPosition.
    self.pending_positions = self.state:readSetting("tomesync_pending_positions") or {}
    -- Send-to-KOReader inbox (beta): enabled only if the server reports the
    -- feature; count drives the menu badge. Populated by the launch poll below.
    self.inbox_enabled  = false
    self.inbox_count    = 0
    self.inbox_items    = {}
    -- Web-adoption ledger: real_anchor -> provisional "web:" anchor, persisted so a
    -- failed push retries next sync (baseline alone would swallow the adoption).
    self.adopt_pending = self.state:readSetting("tomesync_adopt_pending") or {}
    -- "<book_id>|<local pos0>" -> { anchor, anchor_end } of the SERVER
    -- identity for foreign highlights that had to be re-anchored on this copy
    -- (see _applyForeign). Keys are book-scoped: xPointers are only unique
    -- WITHIN a book, and structurally common positions (p[1]/text().0) would
    -- otherwise collide across books and mistranslate pushes. Persisted so
    -- repairs survive restarts.
    self.repair_map = self.state:readSetting("tomesync_repair_map") or {}
    self._heartbeat_armed = false
    self._heartbeat_task = function() self:_heartbeatNow() end
    -- Per-book annotation sync baseline: book_id -> { anchor -> mtime } as of last
    -- sync. Lets a diff tell "I deleted this" from "this is new from another device".
    self.annot_baseline = self.state:readSetting("tomesync_annot_baseline") or {}
    -- Per-book rating sync baseline: book_id (string) -> { rating=, review= } as of
    -- the last reconcile. Lets a diff tell which side (device or Tome) changed.
    self.rating_baseline = self.state:readSetting("tomesync_rating_baseline") or {}
    -- Ratings set offline (or lost to a server error) that never reached Tome.
    -- Keyed by book_id (string) so re-rating the same book before a flush keeps
    -- only the latest value. Flushed on resume / Sync now / close like sessions:
    -- the per-book open/close push alone misses a book you rate and never reopen
    -- (e.g. a finished book), so the rating would otherwise sit unsent forever.
    self.pending_ratings = self.state:readSetting("tomesync_pending_ratings") or {}
    -- Metadata sync ledger: device path -> what we last wrote (see the
    -- "Metadata sync" section). Pruned with book_map.
    self.meta_ledger = self.state:readSetting("tomesync_meta_ledger") or {}
    self:_pruneState()
    self:onDispatcherRegisterActions()
    self.ui.menu:registerToMainMenu(self)
    logger.info("TomeSync: init complete, menu registered,",
                #self.pending_sessions, "pending sessions")

    -- Anti-brick confirm (§3): init() reached the end, so this build is good.
    -- Mark it confirmed now (and flush) so the shim never rolls it back.
    local ustate = G_reader_settings:readSetting("tomesync_update")
    if ustate and ustate.build == BUILD and not ustate.confirmed then
        ustate.confirmed = true
        G_reader_settings:saveSetting("tomesync_update", ustate)
        G_reader_settings:flush()
        logger.info("TomeSync: confirmed build", BUILD)
    end

    -- Opt-in: a deferred, non-blocking update check shortly after startup.
    if G_reader_settings:isTrue("tomesync_auto_check") then
        UIManager:scheduleIn(8, function()
            self:checkForUpdate(function(avail)
                if avail then self:_promptUpdate(avail) end
            end)
        end)
    end

    -- Send-to-KOReader inbox: a deferred, non-blocking poll so the menu can show
    -- an "Inbox (N)" badge. Guarded (offline = no-op; 404 = feature off → no
    -- badge), so it is safe to run on every launch regardless of server support.
    UIManager:scheduleIn(8, function() pcall(function() self:_refreshInbox() end) end)

    -- Reading-history backfill: opt-in, deferred, non-blocking. First run pushes
    -- the entire KOReader history (chunked + resumable); later runs only new rows.
    if G_reader_settings:isTrue("tomesync_auto_sync_stats") then
        UIManager:scheduleIn(12, function() pcall(function() self:_syncReadingStats(false) end) end)
    end

    -- Metadata sync (Tome -> device): opt-in, deferred, once per KOReader
    -- process (init runs again for every ReaderUI, so a chunk-local latch
    -- keeps a book open from re-running the whole library pass).
    -- Device inventory: once per process, after the launch-time requests.
    if not inventory_launched then
        inventory_launched = true
        UIManager:scheduleIn(15, function() pcall(function() self:_pushDeviceBooks(false) end) end)
    end
    -- Reader link (build 53): the launch pass and this instance as its owner.
    self:_linkInit()

    if not meta_sync_launched and G_reader_settings:isTrue("tomesync_meta_sync") then
        meta_sync_launched = true
        UIManager:scheduleIn(20, function() pcall(function() self:_syncMetadata(false) end) end)
    end
end

-- The data tables that live in the dedicated state file (tomesync_update and
-- the boolean preferences stay in G_reader_settings — the frozen shim reads
-- the former, and the latter are what user-settings files are for).
local STATE_KEYS = {
    "tomesync_book_map", "tomesync_pending_sessions", "tomesync_adopt_pending",
    "tomesync_repair_map", "tomesync_annot_baseline", "tomesync_rating_baseline",
    "tomesync_pending_ratings", "tomesync_meta_ledger",
    "tomesync_pending_positions", "tomesync_scan_mark",
}

function TomeSync:_saveState(key, value)
    self.state:saveSetting(key, value)
    -- G_reader_settings flushed on app close; our file must flush itself. Save
    -- sites are already at meaningful boundaries (sync done, queue changed), so
    -- write-through is the right durability trade for a file this small.
    self.state:flush()
    -- Every way a book lands on the device (download, inbox, resolve on open,
    -- library sweep) goes through a book_map save: tell Tome a little later.
    if key == "tomesync_book_map" then self:_scheduleDeviceBooks() end
end

function TomeSync:_migrateState()
    -- One-time move of the data tables out of G_reader_settings. Crash-safe
    -- order: write + flush the new file FIRST, delete the old keys after — a
    -- crash in between leaves a harmless duplicate, and the marker branch
    -- below re-deletes leftovers on the next boot.
    if self.state:readSetting("migrated_from_global") then
        local leftover = false
        for _, k in ipairs(STATE_KEYS) do
            if G_reader_settings:has(k) then
                G_reader_settings:delSetting(k)
                leftover = true
            end
        end
        if leftover then G_reader_settings:flush() end
        return
    end
    local found = false
    for _, k in ipairs(STATE_KEYS) do
        local v = G_reader_settings:readSetting(k)
        if v ~= nil then
            self.state:saveSetting(k, v)
            found = true
        end
    end
    self.state:saveSetting("migrated_from_global", true)
    self.state:flush()
    for _, k in ipairs(STATE_KEYS) do G_reader_settings:delSetting(k) end
    G_reader_settings:flush()
    if found then
        logger.info("TomeSync: migrated plugin state to tomesync_state.lua")
    end
end

function TomeSync:_pruneState()
    -- Drop per-book state whose file is gone so the state file can't grow
    -- unboundedly. Queues (pending_sessions/pending_ratings) are never pruned —
    -- they are owed to the server regardless of the local file's fate. Baseline
    -- loss is delete-safe by construction: deletes are only ever pushed FROM
    -- baseline entries, so a wrongly-pruned baseline can only cause a harmless
    -- re-upsert echo, never a delete.
    if state_pruned then return end
    state_pruned = true
    local changed = false
    local ids = {}
    for path, id in pairs(self.book_map) do
        if lfs.attributes(path, "mode") == "file" then
            ids[tostring(id)] = true
        else
            self.book_map[path] = nil
            changed = true
        end
    end
    local function pruneById(tbl)
        for key in pairs(tbl) do
            -- repair_map keys are "<book_id>|<anchor>"; baselines use "<book_id>"
            local id = key:match("^(%d+)|") or key
            if not ids[id] then
                tbl[key] = nil
                changed = true
            end
        end
    end
    pruneById(self.annot_baseline)
    pruneById(self.rating_baseline)
    pruneById(self.repair_map)
    -- Sweep ledger is keyed by file path directly.
    local sweep = self.state:readSetting("tomesync_sweep")
    if sweep and sweep.done then
        for path in pairs(sweep.done) do
            if lfs.attributes(path, "mode") ~= "file" then
                sweep.done[path] = nil
                changed = true
            end
        end
        if changed then self.state:saveSetting("tomesync_sweep", sweep) end
    end
    for path in pairs(self.meta_ledger) do
        if lfs.attributes(path, "mode") ~= "file" then
            self.meta_ledger[path] = nil
            changed = true
        end
    end
    if changed then
        self.state:saveSetting("tomesync_book_map", self.book_map)
        self.state:saveSetting("tomesync_meta_ledger", self.meta_ledger)
        self.state:saveSetting("tomesync_annot_baseline", self.annot_baseline)
        self.state:saveSetting("tomesync_rating_baseline", self.rating_baseline)
        self.state:saveSetting("tomesync_repair_map", self.repair_map)
        self.state:flush()
        logger.info("TomeSync: pruned state for books no longer on disk")
    end
end

function TomeSync:onDispatcherRegisterActions()
    Dispatcher:registerAction("tome_open_menu", {
        category = "none",
        event    = "TomeOpenMenu",
        title    = "TomeSync: Open menu",
        general  = true,
    })
    Dispatcher:registerAction("tome_browse_series", {
        category = "none",
        event    = "TomeBrowseSeries",
        title    = "TomeSync: Browse series",
        general  = true,
    })
    Dispatcher:registerAction("tome_sync_annotations", {
        category = "none",
        event    = "TomeSyncAnnotations",
        title    = "TomeSync: Sync highlights",
        reader   = true,
    })
    Dispatcher:registerAction("tome_sync_stats", {
        category = "none",
        event    = "TomeSyncStats",
        title    = "TomeSync: Sync reading history",
        general  = true,
    })
    Dispatcher:registerAction("tome_show_sync_code", {
        category = "none",
        event    = "TomeShowSyncCode",
        title    = "TomeSync: Show sync code",
        general  = true,
    })
end

function TomeSync:onTomeShowSyncCode()
    self:_showSyncCode()
    return true
end

function TomeSync:onTomeOpenMenu()
    self:_openMenu()
    return true
end

function TomeSync:onTomeBrowseSeries()
    self:_browseSeriesMenu()
    return true
end

function TomeSync:onTomeSyncAnnotations()
    if not self.book_id then
        UIManager:show(InfoMessage:new{ text = "No book resolved. Open a book first.", timeout = 3 })
        return true
    end
    whenConnected(function()
        local resp = self:_syncAnnotations()
        if resp == nil and not NetworkMgr:isConnected() then
            UIManager:show(InfoMessage:new{ text = "Offline — highlights will sync later.", timeout = 3 })
        else
            local n = (resp and resp.annotations) and #resp.annotations or 0
            UIManager:show(InfoMessage:new{ text = "Highlights synced (" .. n .. " on this book).", timeout = 3 })
        end
    end)
    return true
end

function TomeSync:onTomeSyncStats()
    whenConnected(function() self:_syncReadingStats(true) end)
    return true
end

function TomeSync:onReaderReady()
    if not self.enabled then return end
    local doc = self.ui and self.ui.document
    if not doc then return end

    self._reader_ready = true
    self.book_id = self.book_map[doc.file]

    if self.book_id then
        self:_afterOpen()
        return
    end

    -- No cached mapping: resolve by filename. Deferred so the book is on
    -- screen first — the lookup is a network call on the UI thread, and an
    -- online-but-unreachable server would hold the open for the whole connect
    -- timeout (issue #249).
    local file = doc.file
    UIManager:scheduleIn(1, function()
        pcall(function()
            if not (self.ui and self.ui.document and self.ui.document.file == file) then
                return
            end
            if not self.book_id then self:_tryResolve() end
            if self.book_id then self:_afterOpen() end
        end)
    end)
end

-- The per-open sync once the book is known. Only local state is set here;
-- every network call is scheduled, so opening a book never waits on Tome.
function TomeSync:_afterOpen()
    self:_initSession()
    -- Pull highlights from other devices (and push any local changes). This is the
    -- moment a device picks up annotations made elsewhere. Deferred a tick so the
    -- annotation module is fully settled before we merge into it.
    UIManager:scheduleIn(1, function() pcall(function() self:_syncAnnotations() end) end)
    -- Reconcile the book's rating with Tome (pull web rating onto the device, or
    -- push a device rating that never reached the server). Deferred likewise.
    UIManager:scheduleIn(1, function() pcall(function() self:_pullRatingAtOpen() end) end)
    self:_scheduleLinkSync("open", 4)
end

function TomeSync:onPageUpdate(pageno)
    if not self.enabled then return end
    if pageno == false then return end

    -- Retry resolve if book wasn't matched on open (e.g. WiFi was not ready).
    -- Not before ReaderReady: KOReader sends the first PageUpdate while the
    -- book is still opening, before onReaderReady has looked up the cached
    -- mapping, so this used to fire a resolve request (and wait out the
    -- connect timeout on a dead server) on every open, mapped books included
    -- (issue #249).
    if not self.book_id then
        if not self._reader_ready then return end
        self:_tryResolve()
        if self.book_id then
            self:_initSession()
        end
        return
    end

    self:_creditActivity(os.time())
    self.page_count = self.page_count + 1
    -- Idle-debounced heartbeat: reaching the page threshold ARMS the push, and
    -- every further turn re-delays it — the HTTP call runs 10s after the LAST
    -- page turn, never on the page-turn path itself (which used to stall the
    -- turn for up to the request timeout on a flaky network).
    if self.page_count % HEARTBEAT_PAGES == 0 or self._heartbeat_armed then
        self._heartbeat_armed = true
        UIManager:unschedule(self._heartbeat_task)
        UIManager:scheduleIn(10, self._heartbeat_task)
    end
end

-- Credit the time spent on the page just finished, capped at the idle
-- limit — a gap longer than the cap means the device sat awake unread.
function TomeSync:_creditActivity(now)
    if self.last_activity then
        local gap = now - self.last_activity
        if gap > 0 then
            self.active_seconds = (self.active_seconds or 0) + math.min(gap, idleCapSeconds())
        end
    end
    self.last_activity = now
end

-- Active reading time and honest end for the current session. Every gap
-- between page turns is credited at most the idle cap, including the tail
-- after the last turn — a device left awake overnight books the evening's
-- reading, not the night, and the session ends when the reading did. With
-- the cap off this degrades to exactly now - session_start / now.
function TomeSync:_sessionTotals(now)
    if not self.session_start then return 0, now end
    local last = self.last_activity or self.session_start
    local tail = now - last
    if tail > 0 then tail = math.min(tail, idleCapSeconds()) else tail = 0 end
    return (self.active_seconds or 0) + tail, last + tail
end

function TomeSync:_heartbeatNow()
    self._heartbeat_armed = false
    if not self.enabled or not self.book_id then return end
    local pct = self:_getCurrentPercentage()
    self.last_progress = pct
    self:_putPosition(self.book_id, self:_getCurrentProgress(), pct, deviceName())
    -- Flush any offline sessions while we know WiFi is up
    self:_flushPendingSessions()
    self:_flushPendingRatings()
end

function TomeSync:onSuspend()
    UIManager:unschedule(self._heartbeat_task)
    self._heartbeat_armed = false
    -- Reader link poll: never across a sleep; resume runs a pass that re-arms it.
    if TomeSync._linkStopPoll then TomeSync._linkStopPoll("suspend") end
    if not self.enabled then return end

    if self.book_id then
        -- Record the reading session (lid close = end of session)
        local pct      = self:_getCurrentPercentage()
        local cfi      = self:_getCurrentProgress()
        local duration, session_end = self:_sessionTotals(os.time())
        local dev      = deviceName()

        self:_putPosition(self.book_id, cfi, pct, dev)

        -- Sync highlights/notes alongside position (bidirectional merge with the server).
        pcall(function() self:_syncAnnotations() end)
        -- Push a rating set this session (lid close ends the session like a book close).
        pcall(function() self:_pushRatingOnLeave() end)

        if duration > 10 then
            local session = {
                book_id          = self.book_id,
                started_at       = os.date("!%Y-%m-%dT%H:%M:%SZ", self.session_start),
                ended_at         = os.date("!%Y-%m-%dT%H:%M:%SZ", session_end),
                duration_seconds = duration,
                progress_start   = self.progress_start,
                progress_end     = pct,
                pages_turned     = self.page_count,
                device           = dev,
                session_uuid     = string.format("%d-%d-%s", self.book_id, self.session_start or 0, dev),
            }
            self:_postSessionOrQueue(session)
        end
    end

    self:_suspendSync()
end

-- Sync on suspend (issue #128): before the device sleeps, catch up everything
-- still queued — pending sessions/ratings and, when the launch backfill is
-- enabled, the KOReader reading-history import — so stats are current in the
-- morning without waking the device. Two opt-in levels:
--   tomesync_sync_on_suspend   sync only if WiFi is already connected
--   tomesync_suspend_wifi      also bring the radio up first (aggressive)
-- The device may finish suspending before WiFi associates or mid-transfer;
-- every step here is queue-or-resume safe (sessions/ratings re-flush, the
-- history import resumes from the server watermark), so a cut connection just
-- finishes on the next opportunity.
function TomeSync:_suspendSync()
    if not G_reader_settings:isTrue("tomesync_sync_on_suspend") then return end
    local function catchUp()
        self:_flushPendingSessions()
        self:_flushPendingRatings()
        if G_reader_settings:isTrue("tomesync_auto_sync_stats") then
            pcall(function() self:_syncReadingStats(false) end)
        end
    end
    if NetworkMgr:isConnected() then
        catchUp()
    elseif G_reader_settings:isTrue("tomesync_suspend_wifi") then
        -- Deliberately NOT beforeWifiAction/whenConnected: those honour the
        -- user's wifi_enable_action, and "prompt" would pop a dialog onto the
        -- sleep screen. The toggle's promise is "turn WiFi on", so do exactly
        -- that. The system powers the radio back down as part of suspend.
        pcall(function() NetworkMgr:turnOnWifiAndWaitForConnection(catchUp) end)
    end
end

function TomeSync:onResume()
    -- Device inventory catches deletes made while the device slept (or in the
    -- file manager); runs for the file manager instance too, no book needed.
    if self.enabled then
        self:_scheduleDeviceBooks()
        self:_scheduleLinkSync("resume", 6)
    end
    if not self.enabled or not self.book_id then return end

    -- Start a fresh session (lid open = new session)
    self.session_start  = os.time()
    self.page_count     = 0
    self.active_seconds = 0
    self.last_activity  = self.session_start
    self.progress_start = self:_getCurrentPercentage()
    self.last_progress  = self.progress_start

    -- Push position on wake — catches up after offline periods
    self:_pushPosition()

    -- Flush any pending sessions / ratings from offline periods
    self:_flushPendingSessions()
    self:_flushPendingRatings()
end

-- WiFi just came back: clear the back-off immediately so we don't sit out
-- the window, then catch up anything queued while we were offline. The state
-- is chunk-local, so clearing it here unblocks every TomeSync instance at once.
function TomeSync:onNetworkConnected()
    resetBackoff()
    -- No book_id guard: pending sessions/ratings and the history import are
    -- device-level, and WiFi often comes up in the file manager (morning
    -- catch-up) before any book is opened.
    if not self.enabled then return end
    self:_flushPendingSessions()
    self:_flushPendingRatings()
    self:_scheduleDeviceBooks()
    self:_linkNetworkUp()
    self:_scheduleLinkSync("network", 5)
    -- Catch the reading-history backfill up too — previously launch-only, so
    -- turning WiFi on without restarting KOReader left stats stale (issue #128).
    -- Deferred so it doesn't contend with whatever brought the network up.
    if G_reader_settings:isTrue("tomesync_auto_sync_stats") then
        UIManager:scheduleIn(4, function()
            pcall(function() self:_syncReadingStats(false) end)
        end)
    end
    if G_reader_settings:isTrue("tomesync_meta_sync") then
        UIManager:scheduleIn(6, function() pcall(function() self:_syncMetadata(false) end) end)
    end
end

-- ── KOReader statistics.sqlite3 import (reading-history backfill) ────────────
-- Pushes KOReader's own per-page reading log to Tome (time & pages ONLY — never
-- read-status). Chunked + resumable: the server keeps a per-device watermark, so
-- an interrupted run resumes next launch and never re-sends (idempotent server
-- side). First run backfills the whole history; later runs send only new rows.
function TomeSync:_statsDbPath()
    return require("datastorage"):getSettingsDir() .. "/statistics.sqlite3"
end

-- Move this device's imported reading history (page-stat rows + sync
-- watermark) from the name it previously reported to the one it reports now.
-- Live sessions keep the label they were recorded with: on a multi-device
-- setup the old label was shared, so they can't be attributed after the fact.
-- Idempotent server-side; a failed call is simply retried on the next sync.
function TomeSync:_renameDeviceHistory(from, to)
    if not from or from == "" or from == to then return true end
    local ok, resp = pcall(apiRequest, "POST", "/tome-sync/stats/rename-device",
        { from_device = from, to_device = to })
    return ok and type(resp) == "table"
end

-- `tomesync_device_name_adopted` remembers the last name whose history was
-- migrated. First run on build 40+ moves the pre-40 "KOReader" history; a
-- later rename in Settings moves it again from the previous name.
-- Returns true when this device's history is known to be filed under its
-- current name (nothing to do, or the move succeeded). False = the server did
-- not confirm; the caller must NOT sync history from watermark 0 in that state,
-- or sittings the user deleted in Tome would be re-imported.
function TomeSync:_adoptDeviceName()
    local name = deviceName()
    if name == LEGACY_DEVICE_NAME then return true end
    local adopted = G_reader_settings:readSetting("tomesync_device_name_adopted")
    if adopted == name then return true end
    if self:_renameDeviceHistory(adopted or LEGACY_DEVICE_NAME, name) then
        G_reader_settings:saveSetting("tomesync_device_name_adopted", name)
        return true
    end
    return false
end

-- Settings > Device name: the label Tome shows for this device. Empty restores
-- the automatic name (KOReader's device model).
function TomeSync:_editDeviceName()
    local current = G_reader_settings:readSetting("tomesync_device_name") or ""
    local dialog
    dialog = InputDialog:new{
        title       = "Device name",
        input       = current,
        input_hint  = autoDeviceName(),
        description = "How this device is labelled in Tome's reading log and "
                      .. "stats. Leave empty to use the automatic name "
                      .. "(\"" .. autoDeviceName() .. "\"). Give each "
                      .. "device a distinct name - the reading-history sync "
                      .. "keeps its place per device name.",
        buttons     = {{
            {
                text     = "Cancel",
                id       = "close",
                callback = function() UIManager:close(dialog) end,
            },
            {
                text             = "Save",
                is_enter_default = true,
                callback         = function()
                    -- No commas: Tome's device filters list several devices
                    -- comma-separated (on_device=Kindle,Boox).
                    local name = dialog:getInputText():gsub(",", " ")
                        :gsub("^%s+", ""):gsub("%s+$", "")
                    if name == "" then
                        G_reader_settings:delSetting("tomesync_device_name")
                    else
                        G_reader_settings:saveSetting("tomesync_device_name", name)
                    end
                    UIManager:close(dialog)
                    if NetworkMgr:isConnected() then
                        pcall(function() self:_adoptDeviceName() end)
                    end
                    UIManager:show(InfoMessage:new{
                        text = "Device name: " .. deviceName(), timeout = 2 })
                end,
            },
        }},
    }
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

-- cleared_text: what the confirmation shows when the field is saved empty
-- (defaults to the hint, which for the server URL is the baked-in default).
function TomeSync:_editStringSetting(key, title, hint, description, cleared_text)
    local current = G_reader_settings:readSetting(key) or ""
    local dialog
    dialog = InputDialog:new{
        title       = title,
        input       = current,
        input_hint  = hint,
        description = description,
        buttons     = {{
            {
                text     = "Cancel",
                id       = "close",
                callback = function() UIManager:close(dialog) end,
            },
            {
                text             = "Save",
                is_enter_default = true,
                callback         = function()
                    local v = dialog:getInputText():gsub("^%s+", ""):gsub("%s+$", "")
                    -- A new server address is a new trust decision: a pinned
                    -- certificate belongs to the old one.
                    if key == "tomesync_server_url" and v ~= (G_reader_settings:readSetting(key) or "") then
                        G_reader_settings:delSetting("tomesync_server_fingerprint")
                    end
                    if v == "" then
                        G_reader_settings:delSetting(key)
                    else
                        G_reader_settings:saveSetting(key, v)
                    end
                    -- Written at once, like the pairing: a crash must not
                    -- lose a server address.
                    G_reader_settings:flush()
                    UIManager:close(dialog)
                    UIManager:show(InfoMessage:new{
                        text = title .. ": " .. (v ~= "" and v or cleared_text or hint),
                        timeout = 2 })
                end,
            },
        }},
    }
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

-- Settings > Reset connection: back to the server, key and account baked in
-- at download, written at once like every connection setting.
function TomeSync:_resetConnection()
    G_reader_settings:delSetting("tomesync_server_url")
    G_reader_settings:delSetting("tomesync_fallback_url")
    G_reader_settings:delSetting("tomesync_active_url")
    G_reader_settings:delSetting("tomesync_api_key")
    G_reader_settings:delSetting("tomesync_username")
    G_reader_settings:delSetting("tomesync_server_fingerprint")
    G_reader_settings:flush()
    UIManager:show(InfoMessage:new{
        text = "Connection reset.\nServer: " .. serverURL()
               .. "\nAccount: " .. username(),
        timeout = 4,
    })
end

-- Settings > Sign in with code: Quick Connect pairing. The device asks the
-- server for a short code, the user enters it in the Tome web UI on an
-- already-signed-in browser, and the device exchanges the resulting JWT for a
-- freshly minted plugin API key. No token typing on the device, ever.
function TomeSync:_signInWithCode(trusted)
    if not NetworkMgr:isConnected() then
        UIManager:show(InfoMessage:new{ text = "Connect to WiFi first.", timeout = 3 })
        return
    end
    resetBackoff()
    -- Trust on first use (TLS policy): an https address whose certificate
    -- is not trusted yet (no pin, and no CA in the device's bundle signed it
    -- for this name; or a pinned one that changed) is shown and asked about
    -- first; Trust pins it. A CA-signed or pinned server goes on at once.
    if not trusted then
        return TLS.trustThen(function() self:_signInWithCode(true) end)
    end
    -- device_id and device (build 55): Tome Reader keys the reader by them, so
    -- pairing again replaces this reader's key instead of adding a second
    -- reader. Tome ignores them.
    local resp = apiRequest("POST", "/auth/quick-connect/initiate",
                            { device_id = LinkSession.deviceID(), device = deviceName() })
    if type(resp) ~= "table" or not resp.code or not resp.poll_token then
        UIManager:show(InfoMessage:new{
            text = "Could not reach " .. serverURL()
                   .. "\nCheck the server URL, then try again.",
            timeout = 5,
        })
        return
    end
    local qc_code, poll_token = resp.code, resp.poll_token
    local deadline = os.time() + 5 * 60   -- codes expire server-side after 5 min
    local cancelled = false
    local waiting
    -- Tome Reader (the phone) says so in its answer: the code is approved on
    -- the phone, not typed into Tome's web UI.
    local how
    if resp.server_kind == "tome-reader" then
        how = "On your phone, open Tome Reader:\n"
              .. "Settings > Readers\n"
              .. "and approve this code.\n\n"
    else
        how = "In Tome (signed in, any browser) open\n"
              .. "Settings > Security > Quick Connect\n"
              .. "and enter this code.\n\n"
    end
    logger.info("TomeSync: pairing code shown (" .. (resp.server_kind == "tome-reader" and "tome-reader" or "tome") .. " wording)")
    waiting = InfoMessage:new{
        text = "Pairing code:  " .. qc_code .. "\n\n" .. how
               .. "Waiting\u{2026}  Tap to cancel.",
        dismiss_callback = function() cancelled = true end,
    }
    UIManager:show(waiting)

    local function finish(jwt)
        local key_resp = apiRequest("POST", "/plugin/api-keys",
            { label = "KOReader Plugin (" .. deviceName() .. ", paired)" },
            { token = jwt })
        if type(key_resp) ~= "table" or not key_resp.key then
            UIManager:close(waiting)
            UIManager:show(InfoMessage:new{
                text = "Pairing failed while creating the API key.", timeout = 5 })
            return
        end
        local me = apiRequest("GET", "/auth/me", nil, { token = jwt })
        G_reader_settings:saveSetting("tomesync_api_key", key_resp.key)
        if type(me) == "table" and type(me.username) == "string" and me.username ~= "" then
            G_reader_settings:saveSetting("tomesync_username", me.username)
        else
            G_reader_settings:delSetting("tomesync_username")
        end
        -- KOReader writes its settings file only on a clean exit, so a crash
        -- before then lost the new key and the device asked to pair again.
        G_reader_settings:flush()
        UIManager:close(waiting)
        UIManager:show(InfoMessage:new{
            text = "Signed in as " .. username() .. "\n" .. serverURL(),
            timeout = 4,
        })
    end

    local function poll()
        if cancelled then return end
        if os.time() > deadline then
            UIManager:close(waiting)
            UIManager:show(InfoMessage:new{
                text = "Pairing code expired. Try again.", timeout = 4 })
            return
        end
        local r, c = apiRequest("POST", "/auth/quick-connect/poll",
            { code = qc_code, poll_token = poll_token })
        if type(r) == "table" and r.status == "authorized" and r.access_token then
            finish(r.access_token)
        elseif c == 410 then   -- consumed or expired server-side
            UIManager:close(waiting)
            UIManager:show(InfoMessage:new{
                text = "Pairing code expired. Try again.", timeout = 4 })
        else
            -- pending, or a transient network error: keep polling to the deadline
            UIManager:scheduleIn(3, poll)
        end
    end
    UIManager:scheduleIn(3, poll)
end

function TomeSync:_syncReadingStats(manual)
    local function tell(msg)
        if manual then UIManager:show(InfoMessage:new{ text = msg, timeout = 3 }) end
    end
    if self._stats_syncing then tell("Reading-history sync already running."); return end
    if not NetworkMgr:isConnected() then tell("Offline - reading history will sync later."); return end
    local path = self:_statsDbPath()
    if lfs.attributes(path, "mode") ~= "file" then tell("No KOReader statistics database found."); return end

    -- Carry this device's history + watermark over from the name it used to
    -- report, so a name change (incl. the build-40 move off "KOReader") resumes
    -- instead of re-sending the whole database. Until the server confirms the
    -- move, don't sync at all: a run from watermark 0 would re-import sittings
    -- the user deleted in Tome.
    if not self:_adoptDeviceName() then
        tell("Could not register this device's name with Tome - reading-history sync postponed.")
        return
    end

    -- Server is the source of truth for "how far did we get".
    local wm = apiRequest("GET", "/tome-sync/stats/watermark?device=" .. urlEncode(deviceName()))
    local since = 0
    if type(wm) == "table" and tonumber(wm.last_start_time_synced) then
        since = tonumber(wm.last_start_time_synced)
    end

    local SQ3 = require("lua-ljsqlite3/init")

    -- One cheap metadata pass: the book table is one row per book, and COUNT(*)
    -- sizes the progress message. Page rows are windowed below — memory stays
    -- flat no matter how many years of history the device holds (a real Kindle
    -- DB measures tens of thousands of page_stat_data rows; the old slurp-all
    -- approach materialised every one of them as a Lua table at once).
    local books_by_id, total = {}, 0
    do
        local opened, conn = pcall(SQ3.open, path, "ro")
        if not opened or not conn then tell("Could not open statistics database."); return end
        local read_ok = pcall(function()
            -- ljsqlite3 returns INTEGER columns as int64 cdata, which rapidjson can't
            -- encode — tonumber() every numeric field. (TEXT comes back as Lua strings.)
            local bstmt = conn:prepare("SELECT id, md5, title, authors, pages, total_read_pages FROM book")
            for r in bstmt:rows() do
                books_by_id[tonumber(r[1])] = { ko_id = tonumber(r[1]), md5 = r[2] or "", title = r[3] or "",
                                                 authors = r[4], pages = tonumber(r[5]), total_read_pages = tonumber(r[6]) }
            end
            bstmt:close()
            local cstmt = conn:prepare("SELECT COUNT(*) FROM page_stat_data WHERE start_time >= " .. since)
            for r in cstmt:rows() do total = tonumber(r[1]) or 0 end
            cstmt:close()
        end)
        pcall(function() conn:close() end)
        if not read_ok then tell("Could not read statistics database."); return end
    end
    if total == 0 then tell("Reading history already up to date."); return end

    self._stats_syncing = true
    local dev   = deviceName()
    local CHUNK = 500
    local sent  = 0
    -- Keyset cursor, strictly after (start_time, rowid). Starting at
    -- (watermark, -1) keeps the old ">= watermark" boundary refetch: rows that
    -- share the watermark second are re-sent and no-op server-side (the import
    -- is INSERT OR IGNORE on the identity key). An interrupted run resumes
    -- from the server watermark on the next launch, exactly as before.
    local cur_start, cur_rowid = since, -1

    local function readWindow()
        local opened, conn = pcall(SQ3.open, path, "ro")
        if not opened or not conn then return nil end
        local rows = {}
        local ok = pcall(function()
            local stmt = conn:prepare(string.format(
                "SELECT rowid, id_book, page, start_time, duration, total_pages FROM page_stat_data "
                .. "WHERE start_time > %d OR (start_time = %d AND rowid > %d) "
                .. "ORDER BY start_time, rowid LIMIT %d", cur_start, cur_start, cur_rowid, CHUNK))
            for r in stmt:rows() do
                rows[#rows + 1] = { rowid = tonumber(r[1]), ko_id = tonumber(r[2]), page = tonumber(r[3]),
                                     start_time = tonumber(r[4]), duration = tonumber(r[5]), total_pages = tonumber(r[6]) }
            end
            stmt:close()
        end)
        pcall(function() conn:close() end)
        if not ok then return nil end
        return rows
    end

    local function finish(msg, force)
        self._stats_syncing = false
        if manual or force then UIManager:show(InfoMessage:new{ text = msg, timeout = 3 }) end
    end
    local sendNext
    sendNext = function()
        if not NetworkMgr:isConnected() then
            finish("Reading-history sync paused (offline). Resumes later.")
            return
        end
        local rows = readWindow()
        if rows == nil then
            finish("Could not read statistics database.")
            return
        end
        if #rows == 0 then
            finish(string.format("Reading history synced (%d records).", sent))
            return
        end
        -- Send only the books this window references, not the whole table
        -- with every chunk. Rows whose book row has vanished are still sent
        -- (server skips them, same as before) but never block the cursor.
        local chunk_books, seen = {}, {}
        local payload = {}
        for i = 1, #rows do
            local r = rows[i]
            if books_by_id[r.ko_id] and not seen[r.ko_id] then
                seen[r.ko_id] = true
                chunk_books[#chunk_books + 1] = books_by_id[r.ko_id]
            end
            payload[#payload + 1] = { ko_id = r.ko_id, page = r.page, start_time = r.start_time,
                                       duration = r.duration, total_pages = r.total_pages }
        end
        local resp = apiRequest("POST", "/tome-sync/stats/import",
            { device = dev, books = chunk_books, page_stats = payload })
        if type(resp) ~= "table" then
            finish("Reading-history sync interrupted. Resumes next launch.")
            return
        end
        local last = rows[#rows]
        cur_start, cur_rowid = last.start_time, last.rowid
        sent = sent + #rows
        -- Yield to the UI between chunks (the device stays responsive).
        UIManager:scheduleIn(0.05, sendNext)
    end
    if manual then
        UIManager:show(InfoMessage:new{
            text = string.format("Syncing reading history (%d records)...", total), timeout = 2 })
    end
    sendNext()
end

-- ── Offline queues ───────────────────────────────────────────────────────────
-- Sessions that failed to POST wait here for the next flush (resume, Sync
-- now, WiFi up, suspend). The cap keeps the state file bounded; a very long
-- offline stretch that hits it drops the oldest sessions.
local PENDING_SESSIONS_CAP = 200

function TomeSync:_postSessionOrQueue(session)
    local sok, sresult, scode = pcall(apiRequest, "POST", "/tome-sync/session", session)
    if sok and sresult and not (type(scode) == "number" and scode >= 300) then
        return true
    end
    table.insert(self.pending_sessions, session)
    while #self.pending_sessions > PENDING_SESSIONS_CAP do
        table.remove(self.pending_sessions, 1)
    end
    self:_saveState("tomesync_pending_sessions", self.pending_sessions)
    logger.info("TomeSync: session queued for retry, pending:", #self.pending_sessions)
    return false
end

-- Position PUT that remembers the write when the server cannot be reached,
-- so an offline stretch still has a position per book to hand over in the
-- sync code. Latest write per book only. An entry is dropped by the next
-- successful PUT for that book (the device caught up on its own) or when a
-- code carrying it is marked scanned. Positions are deliberately NOT
-- re-flushed on reconnect: the server is last-write-wins for positions, and
-- a stale device position must not overwrite what the phone did meanwhile.
function TomeSync:_putPosition(book_id, progress, pct, dev)
    local ok, resp, code = pcall(apiRequest, "PUT", "/tome-sync/position/" .. book_id, {
        progress = progress, percentage = pct, device = dev,
    })
    local key = tostring(book_id)
    if ok and resp and type(code) == "number" and code < 300 then
        if self.pending_positions[key] ~= nil then
            self.pending_positions[key] = nil
            self:_saveState("tomesync_pending_positions", self.pending_positions)
        end
        return true
    end
    -- Only transport failures (offline, backoff, timeout) and server errors
    -- are worth carrying; a 4xx (book gone, key revoked) would never succeed.
    if type(code) == "number" and code < 500 then return false end
    self.pending_positions[key] = { pc = pct, xp = progress, t = os.time() }
    self:_saveState("tomesync_pending_positions", self.pending_positions)
    return false
end

-- ── Sync code (QR hand-off from an offline device) ──────────────────────────
-- Everything queued while offline (sessions, positions, ratings) rendered as
-- QR pages for a phone to scan and post to Tome. Format and write rules live
-- in backend/services/sync_code.py; the phone only reads the page header and
-- the server decodes. "Scanned" moves a watermark so the next code carries
-- only newer items; the queues themselves are untouched and still flush over
-- WiFi, where the server dedups them.
-- base64 chars per page. With medium error correction this lands around QR
-- version 17 (85 modules): big modules for a phone camera on e-ink, and a
-- quarter of the symbol can be unreadable before a page fails.
local SYNC_CODE_CHUNK = 480
local SYNC_CODE_EC_LEVEL = 2  -- luaqrcode: 1 = L, 2 = M, 3 = Q, 4 = H

local function daysFromCivil(y, m, d)
    if m <= 2 then y = y - 1 end
    local era = math.floor(y / 400)
    local yoe = y - era * 400
    local mp = (m + 9) % 12
    local doy = math.floor((153 * mp + 2) / 5) + d - 1
    local doe = yoe * 365 + math.floor(yoe / 4) - math.floor(yoe / 100) + doy
    return era * 146097 + doe - 719468
end

-- "2026-09-24T13:05:00Z" -> epoch seconds. Queued sessions store ISO strings
-- (what the session endpoint takes); the code carries epochs to stay small.
local function isoUtcToEpoch(str)
    if type(str) ~= "string" then return nil end
    local y, mo, d, h, mi, se = str:match("^(%d+)%-(%d+)%-(%d+)T(%d+):(%d+):(%d+)")
    if not y then return nil end
    return daysFromCivil(tonumber(y), tonumber(mo), tonumber(d)) * 86400
        + tonumber(h) * 3600 + tonumber(mi) * 60 + tonumber(se)
end

-- include_live: add the open book's live position (the code wants it; the
-- menu count does not, it would read "(1)" whenever a book is open).
function TomeSync:_syncCodeItems(include_live)
    local mark = self.state:readSetting("tomesync_scan_mark") or 0
    local sessions, positions, ratings = {}, {}, {}
    for _, s in ipairs(self.pending_sessions) do
        -- The start epoch comes from the dedup key, so the server rebuilds the
        -- exact "book-start-device" key the WiFi flush will send later.
        local st = tonumber((s.session_uuid or ""):match("^%d+%-(%d+)%-")) or isoUtcToEpoch(s.started_at)
        local en = isoUtcToEpoch(s.ended_at) or (st and s.duration_seconds and st + s.duration_seconds) or st
        if st and en > mark then
            table.insert(sessions, {
                b = s.book_id, st = st, en = en, d = s.duration_seconds,
                ps = s.progress_start, pe = s.progress_end, pg = s.pages_turned,
            })
        end
    end
    for key, p in pairs(self.pending_positions) do
        if (p.t or 0) > mark and tonumber(key) then
            table.insert(positions, { b = tonumber(key), pc = p.pc or 0, xp = p.xp, t = p.t })
        end
    end
    -- The open book's live position, replacing any queued entry for it.
    if include_live and self.book_id and self.ui and self.ui.document then
        for i = #positions, 1, -1 do
            if positions[i].b == self.book_id then table.remove(positions, i) end
        end
        table.insert(positions, {
            b = self.book_id, pc = self:_getCurrentPercentage(),
            xp = self:_getCurrentProgress(), t = os.time(),
        })
    end
    for key, r in pairs(self.pending_ratings) do
        if tonumber(key) then
            table.insert(ratings, {
                b = tonumber(key), r = r.rating or rapidjson.null, rv = r.review or rapidjson.null,
            })
        end
    end
    return sessions, positions, ratings
end

function TomeSync:_syncCodePages()
    local sessions, positions, ratings = self:_syncCodeItems(true)
    if #sessions == 0 and #positions == 0 and #ratings == 0 then
        return nil, "empty"
    end
    local zok, zlib = pcall(require, "ffi/zlib")
    if not zok or type(zlib) ~= "table" or not zlib.zlib_compress then
        return nil, "This KOReader build cannot compress the sync code."
    end
    local now = os.time()
    local id = string.format("%08x", (now * 7919 + math.floor((os.clock() * 1000) % 1000)) % 0x7fffffff)
    local payload = { v = 1, id = id, dev = deviceName(), t = now }
    if #sessions  > 0 then payload.s = sessions  end
    if #positions > 0 then payload.p = positions end
    if #ratings   > 0 then payload.r = ratings   end
    local eok, json = pcall(rapidjson.encode, payload)
    if not eok or type(json) ~= "string" then return nil, "Could not encode the sync code." end
    local cok, packed = pcall(zlib.zlib_compress, json)
    if not cok or type(packed) ~= "string" then return nil, "Could not compress the sync code." end
    local b64 = (mime.b64(packed))
    local n = math.max(1, math.ceil(#b64 / SYNC_CODE_CHUNK))
    local pages = {}
    for i = 1, n do
        pages[i] = string.format("TSC1:%s:%d/%d:%s", id, i, n,
            b64:sub((i - 1) * SYNC_CODE_CHUNK + 1, i * SYNC_CODE_CHUNK))
    end
    return pages, nil, { t = now, sessions = #sessions, positions = #positions, ratings = #ratings }
end

-- "Show sync code" ends the sitting in progress and starts a fresh one, as a
-- lid close followed by a lid open would: the reading done so far in the open
-- book becomes a queued session the code can carry, instead of staying
-- invisible until the next suspend. The later close or suspend then posts a
-- new session with a new start key, so nothing collides on the server. Same
-- steps as onSuspend minus the annotation sync (highlights are not part of
-- the code by design).
function TomeSync:_cutSittingForSyncCode()
    if not self.enabled or not self.book_id or not self.ui or not self.ui.document then return end
    local now      = os.time()
    local pct      = self:_getCurrentPercentage()
    local cfi      = self:_getCurrentProgress()
    local duration, session_end = self:_sessionTotals(now)
    local dev      = deviceName()

    self:_putPosition(self.book_id, cfi, pct, dev)
    pcall(function() self:_pushRatingOnLeave() end)

    if duration > 10 then
        self:_postSessionOrQueue({
            book_id          = self.book_id,
            started_at       = os.date("!%Y-%m-%dT%H:%M:%SZ", self.session_start),
            ended_at         = os.date("!%Y-%m-%dT%H:%M:%SZ", session_end),
            duration_seconds = duration,
            progress_start   = self.progress_start,
            progress_end     = pct,
            pages_turned     = self.page_count,
            device           = dev,
            session_uuid     = string.format("%d-%d-%s", self.book_id, self.session_start or 0, dev),
        })
    end

    self.session_start  = now
    self.page_count     = 0
    self.active_seconds = 0
    self.last_activity  = now
    self.progress_start = pct
    self.last_progress  = pct
end

function TomeSync:_showSyncCode()
    self:_cutSittingForSyncCode()
    local pages, err, info = self:_syncCodePages()
    if not pages then
        UIManager:show(InfoMessage:new{
            text = err == "empty"
                and "Nothing to hand over: every session and position since the last scan has already reached Tome."
                or err,
            timeout = 5,
        })
        return
    end
    self:_showSyncCodePage(pages, 1, info)
end

local function plural(n, word)
    return n .. " " .. word .. (n == 1 and "" or "s")
end

-- KOReader's QRWidget picks the error-correction level itself (whatever fits
-- the smallest symbol, usually L); a code read off e-ink under glass wants M.
-- Same painting as QRWidget, with the level pinned.
local function syncCodeImage(qrencode, ImageWidget, Blitbuffer, text, size)
    local ok, grid = qrencode.qrcode(text, SYNC_CODE_EC_LEVEL)
    if not ok then
        logger.warn("TomeSync: QR encode failed:", tostring(grid))
        return ImageWidget:new{ image = Blitbuffer.new(size, size) }
    end
    local n = #grid
    local sq = math.max(1, math.floor(size / n))
    local bb = Blitbuffer.new(sq * n, sq * n)
    bb:fill(Blitbuffer.COLOR_BLACK)
    for x, col in ipairs(grid) do
        for y, v in ipairs(col) do
            if v < 0 then bb:paintRect((x - 1) * sq, (y - 1) * sq, sq, sq, Blitbuffer.COLOR_WHITE) end
        end
    end
    return ImageWidget:new{ image = bb, width = sq * n, height = sq * n }
end

function TomeSync:_showSyncCodePage(pages, index, info)
    local Screen          = Device.screen
    local Blitbuffer      = require("ffi/blitbuffer")
    local CenterContainer = require("ui/widget/container/centercontainer")
    local FrameContainer  = require("ui/widget/container/framecontainer")
    local InputContainer  = require("ui/widget/container/inputcontainer")
    local VerticalGroup   = require("ui/widget/verticalgroup")
    local VerticalSpan    = require("ui/widget/verticalspan")
    local TextBoxWidget   = require("ui/widget/textboxwidget")
    local ImageWidget     = require("ui/widget/imagewidget")
    local qrencode        = require("ffi/qrencode")
    local Geom            = require("ui/geometry")
    local GestureRange    = require("ui/gesturerange")
    local Font            = require("ui/font")
    local Size            = require("ui/size")

    local n = #pages
    local screen_w, screen_h = Screen:getWidth(), Screen:getHeight()
    local text_w = math.floor(screen_w * 0.85)
    -- Leave room for the two lines above and below; the code itself is the
    -- largest square that fits so the modules stay big for the camera.
    local qr_size = math.min(screen_w - Screen:scaleBySize(40), screen_h - Screen:scaleBySize(220))
    local summary = {}
    if info.sessions  > 0 then table.insert(summary, plural(info.sessions, "session")) end
    if info.positions > 0 then table.insert(summary, plural(info.positions, "position")) end
    if info.ratings   > 0 then table.insert(summary, plural(info.ratings, "rating")) end
    local title = n > 1 and string.format("Sync code, page %d of %d", index, n) or "Sync code"
    local hint = index < n
        and "Scan with the Tome app or the web UI, then tap for the next page."
        or "Scan with the Tome app or the web UI, then tap."

    local body = VerticalGroup:new{
        align = "center",
        TextBoxWidget:new{ text = title, face = Font:getFace("tfont", 22), width = text_w, alignment = "center" },
        TextBoxWidget:new{ text = table.concat(summary, ", "), face = Font:getFace("cfont", 16), width = text_w, alignment = "center" },
        VerticalSpan:new{ width = Size.padding.large },
        syncCodeImage(qrencode, ImageWidget, Blitbuffer, pages[index], qr_size),
        VerticalSpan:new{ width = Size.padding.large },
        TextBoxWidget:new{ text = hint, face = Font:getFace("cfont", 16), width = text_w, alignment = "center" },
    }
    local widget = InputContainer:new{
        modal = true,
        dimen = Geom:new{ x = 0, y = 0, w = screen_w, h = screen_h },
        FrameContainer:new{
            background = Blitbuffer.COLOR_WHITE,
            bordersize = 0,
            padding = 0,
            width = screen_w,
            height = screen_h,
            CenterContainer:new{ dimen = Geom:new{ w = screen_w, h = screen_h }, body },
        },
    }
    local function advance()
        -- Close with a full (flashing) refresh: a partial one leaves the
        -- dense code ghosting on e-ink until the next full repaint.
        UIManager:close(widget, "full")
        if index < n then
            self:_showSyncCodePage(pages, index + 1, info)
        else
            self:_syncCodeDone(pages, info)
        end
        return true
    end
    widget.ges_events.Tap = { GestureRange:new{ ges = "tap", range = widget.dimen } }
    widget.onTap = advance
    if Device:hasKeys() then
        widget.key_events.AnyKeyPressed = { { Device.input.group.Any } }
        widget.onAnyKeyPressed = advance
    end
    -- Full refresh: a partial one leaves the page behind ghosting through the
    -- code, and a camera reads ghosting as modules.
    UIManager:show(widget, "full")
end

function TomeSync:_syncCodeDone(pages, info)
    local dialog
    dialog = ButtonDialog:new{
        title = "Did the phone confirm the scan?\n\n"
             .. "Scanned: the next code only carries what is new. Everything "
             .. "stays queued for the next WiFi sync either way; Tome ignores "
             .. "what it has already seen.",
        buttons = {
            {
                { text = "Show again", callback = function()
                    UIManager:close(dialog)
                    self:_showSyncCodePage(pages, 1, info)
                end },
                { text = "Later", callback = function() UIManager:close(dialog) end },
            },
            {
                { text = "Scanned", callback = function()
                    UIManager:close(dialog, "full")
                    self:_markSyncCodeScanned(info.t)
                end },
            },
        },
    }
    UIManager:show(dialog)
end

function TomeSync:_markSyncCodeScanned(t)
    self:_saveState("tomesync_scan_mark", t)
    -- Positions carried by that code are done with. Sessions stay for the
    -- WiFi flush, which the server turns into a no-op.
    local kept = {}
    for key, p in pairs(self.pending_positions) do
        if (p.t or 0) > t then kept[key] = p end
    end
    self.pending_positions = kept
    self:_saveState("tomesync_pending_positions", kept)
    UIManager:show(InfoMessage:new{
        text = "Marked as scanned. The next code only carries newer reading.",
        timeout = 3,
    })
end

function TomeSync:_flushPendingSessions()
    if #self.pending_sessions == 0 then return end
    if not NetworkMgr:isConnected() then return end

    local remaining = {}
    for _, session in ipairs(self.pending_sessions) do
        local ok, result, code = pcall(apiRequest, "POST", "/tome-sync/session", session)
        if not ok or not result or (type(code) == "number" and code >= 300) then
            table.insert(remaining, session)
        end
    end

    self.pending_sessions = remaining
    self:_saveState("tomesync_pending_sessions", remaining)
    if #remaining == 0 then
        logger.info("TomeSync: all pending sessions flushed")
    else
        logger.info("TomeSync:", #remaining, "sessions still pending")
    end
end

function TomeSync:onCloseDocument()
    UIManager:unschedule(self._heartbeat_task)
    self._heartbeat_armed = false
    if self.enabled then self:_scheduleLinkSync("close", 3) end
    if not self.enabled or not self.book_id then return end

    local pct      = self:_getCurrentPercentage()
    local cfi      = self:_getCurrentProgress()
    local duration, session_end = self:_sessionTotals(os.time())
    local dev      = deviceName()

    self:_putPosition(self.book_id, cfi, pct, dev)

    -- Flush + merge highlights/notes before the book closes.
    pcall(function() self:_syncAnnotations() end)
    -- Push a rating the reader set this session up to Tome before we drop book_id.
    pcall(function() self:_pushRatingOnLeave() end)

    if duration > 10 then
        -- Queued on failure like the suspend path: closing a book while
        -- offline used to drop the session on the floor.
        self:_postSessionOrQueue({
            book_id          = self.book_id,
            started_at       = os.date("!%Y-%m-%dT%H:%M:%SZ", self.session_start),
            ended_at         = os.date("!%Y-%m-%dT%H:%M:%SZ", session_end),
            duration_seconds = duration,
            progress_start   = self.progress_start,
            progress_end     = pct,
            pages_turned     = self.page_count,
            device           = dev,
            session_uuid     = string.format("%d-%d-%s", self.book_id, self.session_start or 0, dev),
        })
    end

    self.book_id        = nil
    self.session_start  = nil
    self.page_count     = 0
    self.active_seconds = 0
    self.last_activity  = nil
    self.progress_start = nil
    self.last_progress  = nil
    last_session_init.book_id = nil
end

-- ── Helpers ──────────────────────────────────────────────────────────────────

function TomeSync:_tryResolve()
    local doc = self.ui and self.ui.document
    if not doc then return end
    local filename = doc.file:match("([^/]+)$") or doc.file
    -- KOReader's own file identity, computed FRESH from the file (~12KB of
    -- reads). The sidecar's partial_md5_checksum is only a fallback: metadata
    -- archive restores can inherit it from ANOTHER copy of the book, so it is
    -- not a reliable hash of these bytes (observed in emulator testing). The
    -- server matches against the hashes recorded when it scanned or served
    -- the artifact; the filename heuristics remain as final fallback.
    local mok, md5 = pcall(function() return require("util").partialMD5(doc.file) end)
    if not mok or type(md5) ~= "string" then
        md5 = self.ui.doc_settings and self.ui.doc_settings:readSetting("partial_md5_checksum")
    end
    logger.info("TomeSync: resolving filename:", filename)
    local rok, result, rcode = pcall(apiRequest, "GET",
        "/tome-sync/resolve?filename=" .. urlEncode(filename)
        .. (md5 and ("&ko_md5=" .. urlEncode(md5)) or ""))
    if rok and result and type(rcode) == "number" and rcode == 200 and result.book_id then
        self.book_id = result.book_id
        self.book_map[doc.file] = self.book_id
        self:_saveState("tomesync_book_map", self.book_map)
        logger.info("TomeSync: resolved to book_id", self.book_id)
    else
        logger.dbg("TomeSync: could not resolve", filename)
    end
end

function TomeSync:_initSession()
    if self.book_id == last_session_init.book_id
            and (os.time() - last_session_init.at) < 5 then
        logger.dbg("TomeSync: duplicate session init skipped, id =", self.book_id)
        return
    end
    last_session_init.book_id = self.book_id
    last_session_init.at = os.time()

    logger.dbg("TomeSync: book opened, id =", self.book_id)
    self.session_start  = os.time()
    self.page_count     = 0
    self.active_seconds = 0
    self.last_activity  = self.session_start
    -- The device position is the baseline until (and unless) the server's
    -- answer moves it; the pull itself runs once the book is on screen.
    self.progress_start = self:_getCurrentPercentage()
    self.last_progress  = self.progress_start

    local book_id = self.book_id
    UIManager:scheduleIn(0.5, function()
        pcall(function() self:_pullPositionAtOpen(book_id) end)
    end)
end

-- Book-open position pull, scheduled by _initSession so the open never waits
-- on the network (issue #249: a dead server used to hold the book closed for
-- the full connect timeout).
function TomeSync:_pullPositionAtOpen(book_id)
    if self.book_id ~= book_id or not (self.ui and self.ui.document) then return end
    local ok, pos, code = pcall(apiRequest, "GET", "/tome-sync/position/" .. book_id)
    if self.book_id ~= book_id or not (self.ui and self.ui.document) then return end
    if ok and pos and code == 200 then
        local server_pct = pos.percentage or 0
        local local_pct  = self:_getCurrentPercentage()
        -- Pull-conflict strategy (like stock kosync): forward and backward
        -- pulls each get prompt / silent / never. Defaults keep the historic
        -- behavior: forward silent, backward never.
        local mode = nil
        if server_pct > (local_pct + 0.01) and server_pct < 0.99 then
            mode = G_reader_settings:readSetting("tomesync_pull_forward") or "silent"
        elseif server_pct < (local_pct - 0.01) and server_pct > 0.01 then
            mode = G_reader_settings:readSetting("tomesync_pull_backward") or "never"
        end
        if mode == "silent" then
            self.progress_start = server_pct
            self.last_progress  = server_pct
            -- Must be a toast (Notification), not an InfoMessage: this shows
            -- right when Profiles auto-exec ("on book opening") dispatches its
            -- actions, and UIManager:sendEvent drops events at the topmost
            -- non-toast window — an InfoMessage here silently eats the user's
            -- layout profile (font size, margins, columns).
            UIManager:show(Notification:new{
                text = string.format(
                    "TomeSync: Server at %.0f%% (device: %.0f%%).",
                    server_pct * 100, local_pct * 100
                ),
                timeout = 3,
            })
            self:_gotoServerPosition(pos, server_pct)
        elseif mode == "prompt" then
            -- Deferred: a ConfirmBox is a non-toast window, and showing one at
            -- open time would eat the Profiles auto-exec dispatch exactly like
            -- the InfoMessage bug above. 1.5s lets the open settle first.
            UIManager:scheduleIn(1.5, function()
                if not self.ui or not self.ui.document then return end
                UIManager:show(ConfirmBox:new{
                    text = string.format(
                        "TomeSync: Server position is at %.0f%% (this device: %.0f%%).\nJump there?",
                        server_pct * 100, local_pct * 100
                    ),
                    ok_text = "Jump",
                    ok_callback = function()
                        self.progress_start = server_pct
                        self:_gotoServerPosition(pos, server_pct)
                    end,
                })
            end)
        end
        -- Otherwise the open-time baseline set by _initSession stands.
    end
end

function TomeSync:_gotoServerPosition(pos, server_pct)
    if not (self.ui and self.ui.rolling) then return end
    if type(pos.progress) == "string" and pos.progress:sub(1, 1) == "/" then
        pcall(function()
            self.ui.rolling:onGotoXPointer(pos.progress, pos.progress)
        end)
    else
        -- Not a crengine xpointer (e.g. the web reader stores a
        -- foliate epubcfi here) — onGotoXPointer with it lands on
        -- page 1, so jump by percentage instead.
        pcall(function()
            self.ui.rolling:onGotoPercent(server_pct * 100)
        end)
    end
end

function TomeSync:_getCurrentPercentage()
    if not self.ui or not self.ui.document then return 0 end
    local ok, result = pcall(function()
        if self.ui.document.info.has_pages then
            return self.ui.paging:getLastPercent()
        else
            return self.ui.rolling:getLastPercent()
        end
    end)
    return (ok and result) or 0
end

function TomeSync:_getCurrentProgress()
    if not self.ui or not self.ui.document then return nil end
    local ok, result = pcall(function()
        if self.ui.document.info.has_pages then
            return tostring(self.ui.paging:getLastProgress())
        else
            return self.ui.rolling:getLastProgress()
        end
    end)
    return ok and result or nil
end

function TomeSync:_pushPosition()
    local pct = self:_getCurrentPercentage()
    self.last_progress = pct
    self:_putPosition(self.book_id, self:_getCurrentProgress(), pct, deviceName())
end

-- "Sync now" position sync (issue #175): pull first, then push. This was
-- push-only, so a device that was behind overwrote newer progress from
-- another device on the server. The pull reuses the same conflict strategy
-- as book open (forward silent/prompt, backward never, by default); the push
-- is deferred until the pull has settled — including through the prompt — so
-- the final server state reflects the outcome.
function TomeSync:_syncPositionNow()
    local ok, pos, code = pcall(apiRequest, "GET", "/tome-sync/position/" .. self.book_id)
    if ok and pos and code == 200 then
        local server_pct = pos.percentage or 0
        local local_pct  = self:_getCurrentPercentage()
        -- Shifts the session's start by the jump so the jumped-over range is
        -- not credited as reading (progress made before the jump stays counted).
        local function shiftSessionStart()
            if self.progress_start then
                self.progress_start = math.max(0, math.min(1,
                    self.progress_start + (server_pct - local_pct)))
            end
        end
        local mode = nil
        if server_pct > (local_pct + 0.01) and server_pct < 0.99 then
            mode = G_reader_settings:readSetting("tomesync_pull_forward") or "silent"
        elseif server_pct < (local_pct - 0.01) and server_pct > 0.01 then
            mode = G_reader_settings:readSetting("tomesync_pull_backward") or "never"
        end
        if mode == "silent" then
            shiftSessionStart()
            self:_gotoServerPosition(pos, server_pct)
            self:_pushPosition()
            return
        elseif mode == "prompt" then
            -- Menu context, book already open — no Profiles auto-exec dispatch
            -- to protect here, unlike the open-time prompt.
            UIManager:show(ConfirmBox:new{
                text = string.format(
                    "TomeSync: Server position is at %.0f%% (this device: %.0f%%).\nJump there?",
                    server_pct * 100, local_pct * 100
                ),
                ok_text = "Jump",
                ok_callback = function()
                    shiftSessionStart()
                    self:_gotoServerPosition(pos, server_pct)
                    self:_pushPosition()
                end,
                cancel_callback = function()
                    -- Explicit keep-local: the device wins, last-write-wins.
                    self:_pushPosition()
                end,
            })
            return
        end
    end
    self:_pushPosition()
end

-- ── Rating sync (bidirectional, per open book) ───────────────────────────────
-- KOReader's native Book status screen stores a 1–5 star rating + a free-text
-- review in the per-book sidecar under `summary` (`rating` / `note`). Tome holds
-- the same per-user rating/review on UserBookStatus. We mirror both directions
-- using a saved baseline to tell which side changed since the last reconcile:
--   only device changed  -> push device → Tome
--   only Tome changed     -> write Tome → device sidecar
--   both changed (tie)    -> Tome wins (single source of truth)
-- `summary.status` (reading/complete/abandoned) is left untouched — reading
-- status already syncs via TomeSyncPosition.
--
-- Half-stars (build 31): Tome ratings can be 4.5 etc.; KOReader's sidecar is
-- whole-star, so a pulled half-star is rounded to the NEAREST star for the
-- device (halves round up: 4.5 → 5). The baseline
-- therefore keeps TWO values per book — `remote` (Tome's exact value) and
-- `device` (what we wrote to the sidecar) — so the rounded copy is never
-- mistaken for a local edit and pushed back (which would destroy the half-star
-- server-side). Old single-value baselines ({rating=...}) migrate on read.

local function ratingBase(tbl, key)
    local base = tbl[key] or {}
    if base.rating ~= nil and base.remote == nil and base.device == nil then
        base.remote, base.device = base.rating, base.rating
        base.rating = nil
    end
    return base
end

local function deviceStars(rating)
    -- Sidecar value for a (possibly half-star) Tome rating: nearest whole
    -- star, halves rounding UP (4.5 → 5).
    if type(rating) ~= "number" then return rating end
    return math.floor(rating + 0.5)
end

-- Read the live sidecar summary's rating/review for the open book.
function TomeSync:_localRating()
    local ds = self.ui and self.ui.doc_settings
    if not ds then return nil end
    local summary = ds:readSetting("summary") or {}
    return { rating = summary.rating, review = summary.note }, summary
end

-- Reconcile on open: pull Tome's rating into the device sidecar, or push a
-- device rating that hasn't reached Tome yet (e.g. set last session, offline).
function TomeSync:_pullRatingAtOpen()
    if not self.enabled or not self.book_id then return end
    local loc = self:_localRating()
    if not loc then return end

    local ok, status, code = pcall(apiRequest, "GET", "/tome-sync/rating/" .. self.book_id)
    if not ok or not status or code ~= 200 then return end
    local remote_rating = jval(status.rating)
    local remote_review = jval(status.review)

    local key  = tostring(self.book_id)
    local base = ratingBase(self.rating_baseline, key)
    -- Local edits compare against what we last WROTE to the sidecar (whole
    -- stars); remote changes compare against Tome's exact value. A pulled 4.5
    -- rounded to 4 on the device is neither.
    local local_changed  = (loc.rating ~= base.device) or (loc.review ~= base.review)
    local remote_changed = (remote_rating ~= base.remote) or (remote_review ~= base.review)

    if remote_changed then
        -- Tome changed (and, on a both-changed tie, Tome wins): write the sidecar.
        local device_rating = deviceStars(remote_rating)
        local _, summary = self:_localRating()
        summary.rating   = device_rating
        summary.note     = remote_review
        summary.modified = os.date("%Y-%m-%d")
        self.ui.doc_settings:saveSetting("summary", summary)
        if type(device_rating) == "number" then
            pcall(function()
                require("ui/widget/booklist")
                    .setBookInfoCacheProperty(self.ui.document.file, "rating", device_rating)
            end)
        end
        base.remote, base.device, base.review = remote_rating, device_rating, remote_review
        self.rating_baseline[key] = base
        self:_saveState("tomesync_rating_baseline", self.rating_baseline)
        -- Tome's value supersedes any device rating still queued for this book.
        if self.pending_ratings[key] ~= nil then
            self.pending_ratings[key] = nil
            self:_saveState("tomesync_pending_ratings", self.pending_ratings)
        end
        logger.info("TomeSync: applied Tome rating to device for book", self.book_id)
    elseif local_changed then
        self:_pushRating(loc.rating, loc.review)
    end
end

-- PUT a rating/review for an arbitrary book up to Tome and advance its baseline
-- on success. Returns true on success, false otherwise (offline / server error).
-- nil must go on the wire as JSON null (an absent Lua key would be dropped from
-- the body, leaving Tome's old value in place instead of clearing it). rating is
-- always nil or 1–5, never 0, so `or` is safe.
function TomeSync:_putRating(book_id, rating, review)
    local sok, resp, code = pcall(apiRequest, "PUT",
        "/tome-sync/rating/" .. book_id,
        { rating = rating or rapidjson.null, review = review or rapidjson.null })
    if not (sok and resp and type(code) == "number" and code < 300) then
        return false
    end
    local key = tostring(book_id)
    local base = ratingBase(self.rating_baseline, key)
    -- A device push is whole-star, so remote and device coincide.
    base.remote, base.device, base.review = rating, rating, review
    self.rating_baseline[key] = base
    self:_saveState("tomesync_rating_baseline", self.rating_baseline)
    return true
end

-- Push the open book's rating up to Tome. On failure, persist it to the pending
-- queue so a later flush retries it even if this book is never reopened (the
-- close-time trigger only ever fires for the book you're currently in).
function TomeSync:_pushRating(rating, review)
    if not self.book_id then return end
    local key = tostring(self.book_id)
    if self:_putRating(self.book_id, rating, review) then
        if self.pending_ratings[key] ~= nil then
            self.pending_ratings[key] = nil
            self:_saveState("tomesync_pending_ratings", self.pending_ratings)
        end
        logger.info("TomeSync: pushed device rating to Tome for book", self.book_id)
    else
        self.pending_ratings[key] = { rating = rating, review = review }
        self:_saveState("tomesync_pending_ratings", self.pending_ratings)
        logger.info("TomeSync: rating queued for retry for book", self.book_id)
    end
end

-- Flush ratings that failed to send earlier (set offline, or while the server
-- was unreachable). Mirrors _flushPendingSessions; survives reboots via
-- G_reader_settings. A blind last-write-wins push, same as the close-time path.
function TomeSync:_flushPendingRatings()
    if not next(self.pending_ratings) then return end
    if not NetworkMgr:isConnected() then return end

    local remaining = {}
    local flushed = false
    for key, entry in pairs(self.pending_ratings) do
        if self:_putRating(key, entry.rating, entry.review) then
            flushed = true
        else
            remaining[key] = entry
        end
    end
    self.pending_ratings = remaining
    self:_saveState("tomesync_pending_ratings", remaining)
    if flushed then logger.info("TomeSync: pending ratings flushed") end
end

-- Reconcile on close/suspend: if the device rating changed during the session,
-- push it up. Tome-wins at open already settled any conflict, so a divergence
-- here is a fresh on-device edit and is safe to send.
function TomeSync:_pushRatingOnLeave()
    if not self.enabled or not self.book_id then return end
    local loc = self:_localRating()
    if not loc then return end
    local base = ratingBase(self.rating_baseline, tostring(self.book_id))
    -- Compare against what we wrote to the sidecar (whole stars): a rounded
    -- half-star pull must not read as a local edit.
    if loc.rating == base.device and loc.review == base.review then return end
    self:_pushRating(loc.rating, loc.review)
end

-- ── Annotation sync (bidirectional: KOReader <-> Tome <-> KOReader) ──────────
-- Identity is the anchor (pos0 xPointer). Edits resolve last-write-wins by
-- KOReader's modification time; deletes use server tombstones + a per-book
-- baseline so a highlight removed on one device can't be resurrected by another's
-- stale copy. All timestamps are KOReader local wall-clock strings (sortable).

local function annotMtime(a)
    return a.datetime_updated or a.datetime or ""
end

local function annotAnchor(a)
    -- pos0 is an xPointer string for EPUB (stable identity); PDF uses table
    -- positions, so fall back to the creation datetime (can't render cross-device).
    return (type(a.pos0) == "string" and a.pos0) or a.datetime
end

local function isWebAnchor(anchor)
    -- Provisional anchor of a highlight created in Tome's web reader; carries no
    -- usable position. This device "adopts" it by locating the text natively.
    return type(anchor) == "string" and anchor:sub(1, 4) == "web:"
end

function TomeSync:_annotItem(a)
    local anchor = annotAnchor(a)
    if not anchor then return nil end
    return {
        anchor           = anchor,
        anchor_end       = (type(a.pos1) == "string" and a.pos1) or nil,
        highlighted_text = a.text,
        note             = a.note,
        chapter          = a.chapter,
        color            = a.color,
        datetime         = a.datetime,
        datetime_updated = a.datetime_updated,
    }
end

function TomeSync:_localAnnotationMap()
    -- anchor -> { item = <koreader annotation>, mtime }; nil if module unavailable
    -- (so we never sync a state we can't read).
    local ann = self.ui and self.ui.annotation
    local list = ann and ann.annotations
    if type(list) ~= "table" then return nil end
    local map = {}
    for _, a in ipairs(list) do
        local anchor = annotAnchor(a)
        if anchor then
            -- Repaired foreign highlights render at a local position but keep
            -- their SERVER identity: index them under the server anchor so
            -- edits/deletes/tombstones from other devices reach them, and our
            -- own pushes never mint a duplicate identity for the same words.
            local alias = self.repair_map and self.book_id
                and self.repair_map[tostring(self.book_id) .. "|" .. anchor]
            map[(alias and alias.anchor) or anchor] = { item = a, mtime = annotMtime(a) }
        end
    end
    return map
end

function TomeSync:_applyServerState(alive, tombstones)
    -- Merge the server's reconciled state into the local annotation set so this
    -- device shows highlights made on other devices, and drops ones deleted there.
    local ann = self.ui and self.ui.annotation
    if not ann or type(ann.annotations) ~= "table" then return end
    local changed = false
    local localmap = self:_localAnnotationMap() or {}

    -- Clamp incoming stamps to this device's clock: server-minted stamps
    -- already arrive shifted into our frame (device_time), but stamps from a
    -- third device with a fast clock can still be "in the future" here — and a
    -- future stamp stored locally would outrank every later local edit. Never
    -- store or compare a stamp ahead of now.
    local device_now = os.date("%Y-%m-%d %H:%M:%S")
    local function clampStamp(stamp)
        if type(stamp) == "string" and stamp > device_now then return device_now end
        return stamp
    end

    local pending_web = {}
    for _, s in ipairs(alive or {}) do
        if isWebAnchor(s.anchor) then
            -- Not a real position — never addItem it; adopt it below instead.
            table.insert(pending_web, s)
        elseif s.anchor then
            s.datetime         = clampStamp(s.datetime)
            s.datetime_updated = clampStamp(s.datetime_updated)
            local L = localmap[s.anchor]
            local smtime = s.datetime_updated or s.datetime or ""
            if not L then
                -- New highlight from another device: verify it reproduces its
                -- text on THIS copy before drawing; repair or skip otherwise
                -- (paging docs reconstruct nothing — see _applyForeign).
                changed = self:_applyForeign(ann, s) or changed
            elseif smtime > L.mtime then
                -- Newer edit from elsewhere wins (note/color/text).
                L.item.text  = s.highlighted_text
                L.item.note  = s.note
                L.item.color = s.color
                L.item.datetime_updated = s.datetime_updated
                changed = true
            end
        end
    end

    for _, t in ipairs(tombstones or {}) do
        local map2 = self:_localAnnotationMap() or {}
        local L = map2[t.anchor]
        if L and L.mtime <= clampStamp(t.deleted_at or "") then
            for i = #ann.annotations, 1, -1 do
                if ann.annotations[i] == L.item then
                    table.remove(ann.annotations, i); changed = true; break
                end
            end
        end
    end

    if #pending_web > 0 then
        local adopted = self:_adoptWebAnnotations(pending_web)
        changed = changed or (adopted > 0)
    end

    if changed then
        pcall(function() self.ui:handleEvent(Event:new("AnnotationsModified", { nb_highlights_added = 0 })) end)
        pcall(function() UIManager:setDirty(self.ui.dialog, "full") end)
    end
end

function TomeSync:_locateText(text, chapter)
    -- Find `text` in the open (rolling) document; when several places carry
    -- the same words prefer the hit inside `chapter`. Returns start/end
    -- xPointers or nil. Shared by web adoption and foreign-highlight repair.
    local ok, results = pcall(function()
        -- (pattern, case_insensitive, nb_context_words, max_hits, regex)
        return self.ui.document:findAllText(text, false, 2, 5, false)
    end)
    if not ok or type(results) ~= "table" then return nil end
    local hit = results[1]
    if #results > 1 and chapter then
        for _, r in ipairs(results) do
            local okc, title = pcall(function()
                local page = self.ui.document:getPageFromXPointer(r.start)
                return self.ui.toc:getTocTitleByPage(page)
            end)
            if okc and title == chapter then hit = r; break end
        end
    end
    if hit and type(hit.start) == "string" and type(hit["end"]) == "string" then
        return hit.start, hit["end"]
    end
    return nil
end

function TomeSync:_applyForeign(ann, s)
    -- A highlight made on another device. NEVER paint it on the wrong words:
    -- verify that the anchor reproduces the highlighted text on THIS copy of
    -- the book, repair by text search when it doesn't (a different bake of the
    -- same book shifts xPointers), and skip entirely when the text can't be
    -- located. Repairs keep the SERVER identity (repair_map) so the origin
    -- device's anchor is never rewritten — no cross-device anchor ping-pong.
    local function norm(t)
        if type(t) ~= "string" then return nil end
        t = t:gsub("94{", "")       -- soft hyphens (U+00AD)
        t = t:gsub("%s+", " ")
        return t:match("^%s*(.-)%s*$")
    end
    local function add(p0, p1)
        return pcall(function()
            ann:addItem({
                page = p0, pos0 = p0, pos1 = p1,
                text = s.highlighted_text, note = s.note, chapter = s.chapter,
                color = s.color, drawer = "lighten",
                datetime = s.datetime, datetime_updated = s.datetime_updated,
            })
        end) == true
    end

    if not self.ui.rolling then
        -- Paging (PDF/CBZ) docs can't reconstruct foreign annotations at all:
        -- they need a numeric page and rect tables, which sync anchors don't
        -- carry — planting strings corrupts the annotation list and crashes
        -- the reader on repaint. They stay server-side only (skipped items
        -- never enter the local map or baseline, so nothing is resurrected
        -- or spuriously deleted).
        return false
    end

    local want = norm(s.highlighted_text)
    if s.anchor:sub(1, 5) == "/body" then
        local okx, got = pcall(function()
            return self.ui.document:getTextFromXPointers(s.anchor, s.anchor_end or s.anchor)
        end)
        if okx and (not want or norm(got) == want) then
            -- verified (or nothing to verify against): draw at the real anchor
            return add(s.anchor, s.anchor_end or s.anchor)
        end
    end

    if not want or want == "" then return false end
    local p0, p1 = self:_locateText(s.highlighted_text, s.chapter)
    if not p0 then return false end   -- unlocatable here: skip, never wrong words
    for _, a in ipairs(ann.annotations or {}) do
        if a.pos0 == p0 then return false end   -- already rendered by an earlier repair
    end
    if not add(p0, p1) then return false end
    self.repair_map[tostring(self.book_id) .. "|" .. p0] = { anchor = s.anchor, anchor_end = s.anchor_end }
    self:_saveState("tomesync_repair_map", self.repair_map)
    logger.info("TomeSync: repaired foreign highlight to", p0)
    return true
end

function TomeSync:_adoptWebAnnotations(pending)
    -- Highlights created in Tome's web reader arrive with a provisional "web:"
    -- anchor and no position. Locate each one's text with crengine and create a
    -- NATIVE annotation (real xPointers); the next push carries adopted_from so
    -- the server retires the provisional row. Anchors are deterministic per book
    -- copy, so two devices adopting the same highlight converge on one anchor.
    -- EPUB (rolling) only — PDF positions aren't xPointer strings.
    local ann = self.ui and self.ui.annotation
    if not ann or not self.ui.rolling or not self.ui.document then return 0 end
    local adopted = 0
    -- Text-level dedupe: a passage already highlighted locally (e.g. adopted on a
    -- previous pull whose push failed) must not be adopted twice.
    local seen_text = {}
    for _, a in ipairs(ann.annotations or {}) do
        if a.text then seen_text[a.text] = true end
    end
    for _, s in ipairs(pending) do
        local text = s.highlighted_text
        if type(text) == "string" and text ~= "" and not seen_text[text] then
            local hit_start, hit_end = self:_locateText(text, s.chapter)
            if hit_start then
                local okAdd = pcall(function()
                    ann:addItem({
                        page = hit_start, pos0 = hit_start, pos1 = hit_end,
                        text = text, note = s.note, chapter = s.chapter,
                        color = s.color, drawer = "lighten",
                        datetime = s.datetime, datetime_updated = s.datetime_updated,
                    })
                end)
                if okAdd then
                    seen_text[text] = true
                    self.adopt_pending[hit_start] = s.anchor
                    adopted = adopted + 1
                end
            end
            -- Unlocatable text (e.g. selection spanning a page-break element):
            -- leave the provisional alone — it stays web-only, never wrong.
        end
    end
    if adopted > 0 then
        self:_saveState("tomesync_adopt_pending", self.adopt_pending)
    end
    return adopted
end

function TomeSync:_syncAnnotations()
    -- Push local changes (diff vs baseline) and pull everyone else's, in one call.
    if not self.book_id then return nil end
    local localmap = self:_localAnnotationMap()
    if not localmap then return nil end
    local bk = tostring(self.book_id)
    local baseline = self.annot_baseline[bk] or {}

    -- Bind the baseline to THIS sidecar instance. A fresh sidecar (new
    -- download of the book, or a wiped sidecar) starts without our marker:
    -- its empty annotation list reflects a NEW FILE, not the user deleting
    -- every highlight — diffing the old baseline against it would push
    -- deletes and tombstone the book's highlights server-side (observed live:
    -- re-downloading a book wiped its synced highlight). Reset the baseline
    -- instead; the pull below re-applies the server state. True deletions
    -- still propagate: once the marker is set, baseline diffs work as before.
    if self.ui.doc_settings then
        if not self.ui.doc_settings:readSetting("tomesync_annot_bound") and next(baseline) ~= nil then
            baseline = {}
            self.annot_baseline[bk] = {}
        end
        self.ui.doc_settings:saveSetting("tomesync_annot_bound", true)
    end

    -- Future-watermark guard: no local stamp may sit ahead of this device's
    -- clock. Future stamps arrive via server-minted datetimes applied before
    -- the clock-offset guard existed (or a third device's fast clock) and would
    -- outrank every later local edit until the clock catches up. Clamp the
    -- annotation AND its baseline entry to now — equal values, so no spurious
    -- re-push, and the user's next edit is strictly newer again.
    local now = os.date("%Y-%m-%d %H:%M:%S")   -- local wall-clock, matches KOReader's
    for anchor, L in pairs(localmap) do
        if L.mtime > now then
            if L.item.datetime_updated and L.item.datetime_updated > now then
                L.item.datetime_updated = now
            end
            if L.item.datetime and L.item.datetime > now then
                L.item.datetime = now
            end
            L.mtime = annotMtime(L.item)
            if baseline[anchor] ~= nil then baseline[anchor] = L.mtime end
        end
    end
    for anchor, m in pairs(baseline) do
        if m > now then baseline[anchor] = now end
    end

    local upserts, deletes = {}, {}
    for anchor, L in pairs(localmap) do
        if baseline[anchor] == nil or baseline[anchor] ~= L.mtime then
            local it = self:_annotItem(L.item)
            if it then
                if it.anchor ~= anchor then
                    -- repaired item: push under its server identity, with the
                    -- ORIGIN device's positions (ours are local rendering only)
                    local alias = self.repair_map[bk .. "|" .. it.anchor]
                    it.anchor = anchor
                    it.anchor_end = (alias and alias.anchor_end) or it.anchor_end
                end
                table.insert(upserts, it)
            end
        end
    end
    for anchor, _ in pairs(baseline) do
        if localmap[anchor] == nil then
            table.insert(deletes, { anchor = anchor, datetime = now })
        end
    end

    -- device_time lets the server shift its own (web-minted) stamps into THIS
    -- device's clock frame — see the server's clock-offset guard.
    local resp = apiRequest("POST", "/tome-sync/annotations/" .. self.book_id .. "/sync",
                            { upserts = upserts, deletes = deletes,
                               device_time = os.date("%Y-%m-%d %H:%M:%S") })
    if not resp then return nil end   -- offline/failed: keep baseline so we retry

    self:_applyServerState(resp.annotations, resp.tombstones)

    -- Push freshly-adopted web annotations right away (adopted_from tells the
    -- server to retire the provisional). On failure adopt_pending persists, so
    -- the next sync retries — the baseline alone would swallow the adoption.
    local adopts = {}
    local after1 = self:_localAnnotationMap() or {}
    for anchor, prov in pairs(self.adopt_pending) do
        local L = after1[anchor]
        if L then
            local it = self:_annotItem(L.item)
            if it then it.adopted_from = prov; table.insert(adopts, it) end
        else
            self.adopt_pending[anchor] = nil   -- adopted copy gone locally; drop
        end
    end
    if #adopts > 0 then
        local resp2 = apiRequest("POST", "/tome-sync/annotations/" .. self.book_id .. "/sync",
                                 { upserts = adopts, deletes = {} })
        if resp2 then
            for _, it in ipairs(adopts) do self.adopt_pending[it.anchor] = nil end
        end
    end
    self:_saveState("tomesync_adopt_pending", self.adopt_pending)

    -- Rebuild the baseline from the post-merge local state.
    local newbase = {}
    local after = self:_localAnnotationMap() or {}
    for anchor, L in pairs(after) do newbase[anchor] = L.mtime end
    self.annot_baseline[bk] = newbase
    self:_saveState("tomesync_annot_baseline", self.annot_baseline)

    -- Drop aliases whose local rendering is gone (a local delete already went
    -- out under the server identity above) so the map can't grow stale.
    local raw = {}
    for _, a in ipairs((self.ui.annotation and self.ui.annotation.annotations) or {}) do
        if type(a.pos0) == "string" then raw[a.pos0] = true end
    end
    local pruned = false
    local prefix = bk .. "|"
    for key in pairs(self.repair_map) do
        if key:sub(1, #prefix) == prefix and not raw[key:sub(#prefix + 1)] then
            self.repair_map[key] = nil; pruned = true
        end
    end
    if pruned then self:_saveState("tomesync_repair_map", self.repair_map) end
    return resp
end

function TomeSync:registerBookId(file_path, book_id)
    self.book_map[file_path] = book_id
    self:_saveState("tomesync_book_map", self.book_map)
    logger.info("TomeSync: registered book_id", book_id, "for", file_path)
end

-- ── Series download ─────────────────────────────────────────────────────────

function TomeSync:_downloadSeriesBooks(series_name, books, min_index, book_type, quiet, guarded)
    -- `quiet` suppresses the summary popups (used by the inbox, which shows its
    -- own roll-up). Returns {downloaded, skipped, failed} so callers can tell
    -- success (file is now on device) from failure.
    -- The server sends "__unserialized__" as the No Series sentinel. Standalone
    -- books are filed per-author (matching Tome's own library layout), so there is
    -- no single folder for the bucket — "No Series" is only a popup label.
    local is_no_series = (series_name == "__unserialized__")
    local batch_label  = is_no_series and "No Series" or series_name

    -- home_dir is the user-set library root (File Manager → long-press → "Set as HOME").
    -- Fall back to download_dir / lastdir for installs where home_dir isn't set.
    local base_dir = G_reader_settings:readSetting("home_dir")
                  or G_reader_settings:readSetting("download_dir")
                  or G_reader_settings:readSetting("lastdir")
    if not base_dir then
        UIManager:show(InfoMessage:new{
            text = "No download directory configured.",
            timeout = 4,
        })
        return { downloaded = 0, skipped = 0, failed = #books }
    end

    -- A user template (Settings → Download location & naming) replaces the
    -- built-in layout below; empty/unset means built-in.
    local template = G_reader_settings:readSetting("tomesync_download_template") or ""

    -- Built-in layout: organize by book-type subfolder. A real series shares
    -- one folder; the No Series bucket files each book under its author
    -- (resolved per book below). Dirs are created lazily so a template run
    -- doesn't leave empty default folders behind. The type is resolved PER BOOK
    -- (book.book_type), not from the batch: the No Series bucket mixes types, so
    -- a single batch type would misfile standalone books (issue #88). The batch
    -- `book_type` is only a fallback for older servers that omit the per-book field.
    -- (Paths only: the folders are made just before the download, after the
    -- final path has been checked.)
    local function ensureDefaultDirs(effective_type)
        local type_dir = base_dir .. "/" .. effective_type
        local series_dir
        if not is_no_series then
            series_dir = type_dir .. "/" .. util.getSafeFilename(series_name)
        end
        return type_dir, series_dir
    end

    -- Build reverse lookup: book_id → local path (to skip already-downloaded books)
    local id_to_path = {}
    for path, bid in pairs(self.book_map) do
        id_to_path[bid] = path
    end

    -- Pre-compute the download queue so progress counts only real work.
    local queue = {}
    local skipped = 0
    -- Representative "saved to" folder for the summary popup. With per-book
    -- types the No Series bucket can span several folders, so this is just the
    -- first real destination — a sensible "look here" pointer, not the only one.
    local save_location
    for _, book in ipairs(books) do
        if min_index and type(book.series_index) == "number" and book.series_index <= min_index then
            skipped = skipped + 1
        elseif id_to_path[book.id] and lfs.attributes(id_to_path[book.id]) then
            skipped = skipped + 1
        else
            local file = pickBestFile(book.files)
            if not file then
                table.insert(queue, {book = book, file = nil, dest = nil})
            else
                local ext = file.format or "epub"
                -- Per-book type (server >= build 24); fall back to the batch
                -- type for older servers. This is what fixes mixed-type No
                -- Series downloads landing in the wrong folder (issue #88).
                -- Both are checked: mixed-list downloads pass the per-book
                -- value, possibly null, as the batch type (issue #259).
                local effective_type = jstr(book.book_type) or jstr(book_type) or "book"
                local dest
                if template ~= "" then
                    local rel = renderDownloadPath(template, {
                        book_type = effective_type,
                        series    = (not is_no_series) and series_name or "",
                        volume    = type(book.series_index) == "number"
                                    and book.series_index or nil,
                        title     = book.title or "",
                        author    = type(book.author) == "string" and book.author or "",
                    })
                    if rel then
                        dest = base_dir .. "/" .. rel .. "." .. ext
                    end
                end
                if not dest then
                    -- Built-in layout (also the fallback when a template
                    -- renders to nothing usable for this book).
                    local type_dir, series_dir = ensureDefaultDirs(effective_type)
                    local display_title
                    if type(book.series_index) == "number" then
                        local vol = book.series_index
                        if vol == math.floor(vol) then vol = math.floor(vol) end
                        display_title = "Vol. " .. tostring(vol) .. " — " .. book.title
                    else
                        display_title = book.title
                    end
                    local fname = util.getSafeFilename(display_title .. "." .. ext)
                    -- Real series → shared series_dir. No Series → per-author folder,
                    -- falling back to the type dir when the book has no author.
                    local dest_dir = series_dir
                    if is_no_series then
                        if type(book.author) == "string" and book.author ~= "" then
                            dest_dir = type_dir .. "/" .. util.getSafeFilename(book.author)
                        else
                            dest_dir = type_dir
                        end
                    end
                    dest = dest_dir .. "/" .. fname
                end
                -- A delivery (guarded) whose title or series starts with a
                -- dot is saved without the dot instead of being refused
                -- (hidden files never show in KOReader). Not for the user's
                -- own downloads: their names stay what earlier builds saved.
                if guarded and TomeSync._linkSafeDest then
                    dest = TomeSync._linkSafeDest(base_dir, dest, ext, book.id)
                end
                local refused = TomeSync._linkDownloadRefused
                    and TomeSync._linkDownloadRefused(base_dir, dest, ext, guarded)
                if refused then
                    logger.warn("TomeSync: download refused:", book.title, "->", dest, "(" .. refused .. ")")
                    table.insert(queue, {book = book, file = nil, dest = nil})
                elseif lfs.attributes(dest) then
                    skipped = skipped + 1
                else
                    save_location = save_location or dest:match("^(.*)/[^/]+$")
                    table.insert(queue, {book = book, file = file, dest = dest})
                end
            end
        end
    end

    if #queue == 0 then
        if not quiet then
            UIManager:show(InfoMessage:new{
                text = string.format(
                    "%s\n\nNothing to download.\nSkipped: %d",
                    batch_label, skipped
                ),
                timeout = 5,
            })
        end
        -- Nothing queued means every book is already on disk → success.
        return { downloaded = 0, skipped = skipped, failed = 0 }
    end

    -- Live progress popup — replace the message between each book.
    -- forceRePaint guarantees the widget is drawn before the blocking HTTP call.
    local progress_msg
    local function showProgress(text)
        if progress_msg then UIManager:close(progress_msg) end
        progress_msg = InfoMessage:new{ text = text }
        UIManager:show(progress_msg)
        UIManager:forceRePaint()
    end

    local function fmtMB(n)
        return string.format("%.1f MB", n / 1048576)
    end
    local downloaded, failed = 0, 0
    local failed_books = {}
    for i, item in ipairs(queue) do
        local head = string.format("%s\n\nDownloading %d of %d\n%s",
                                   batch_label, i, #queue, item.book.title)
        showProgress(head)
        if not item.file then
            failed = failed + 1
            table.insert(failed_books, item.book)
        else
            local total = type(item.file.file_size) == "number" and item.file.file_size or nil
            mkdirp(item.dest:match("^(.*)/[^/]+$"))
            local ok, err = downloadFile(item.book.id, item.file.id, item.dest, total,
                function(received, size)
                    if size then
                        showProgress(string.format("%s\n%d%% of %s", head,
                            math.floor(received * 100 / size), fmtMB(size)))
                    else
                        showProgress(string.format("%s\n%s", head, fmtMB(received)))
                    end
                end)
            if ok then
                downloaded = downloaded + 1
                self.book_map[item.dest] = item.book.id
            else
                logger.warn("TomeSync: download failed for", item.book.title, err)
                failed = failed + 1
                table.insert(failed_books, item.book)
            end
        end
    end
    if progress_msg then UIManager:close(progress_msg) end

    -- Persist book_map
    self:_saveState("tomesync_book_map", self.book_map)

    if not quiet then
        if failed > 0 and #failed_books > 0 then
            -- Offer a retry instead of a one-shot failure count: transient
            -- WiFi drops are the usual culprit and a second pass fixes them.
            UIManager:show(ConfirmBox:new{
                text = string.format(
                    "%s\n\nDownloaded: %d\nSkipped: %d\nFailed: %d",
                    batch_label, downloaded, skipped, failed
                ),
                ok_text = "Retry failed",
                cancel_text = "Close",
                ok_callback = function()
                    self:_downloadSeriesBooks(series_name, failed_books, min_index, book_type, quiet)
                end,
            })
        else
            UIManager:show(InfoMessage:new{
                text = string.format(
                    "%s\n\nDownloaded: %d\nSkipped: %d\nFailed: %d\n\nSaved to: %s",
                    batch_label, downloaded, skipped, failed, save_location or base_dir
                ),
                timeout = 8,
            })
            if downloaded == 1 and #queue == 1 and queue[1].dest then
                -- Single fresh download: offer to jump straight into it.
                local dest = queue[1].dest
                UIManager:show(ConfirmBox:new{
                    text = string.format('Open "%s" now?', queue[1].book.title or "book"),
                    ok_text = "Open",
                    cancel_text = "Later",
                    ok_callback = function()
                        require("apps/reader/readerui"):showReader(dest)
                    end,
                })
            end
        end
    end
    return { downloaded = downloaded, skipped = skipped, failed = failed }
end

-- Shared drill-down list: a "Download all" row plus one row per book. Used by
-- the series browser, the author browser, and search results. Tap downloads a
-- book; hold sets its read status. `data`:
--   title        menu title
--   books        list of server book entries
--   series_name  filing default for books without their own series (optional)
--   book_type    filing fallback for older servers (optional)
--   mixed        true → prefix each row with the book's own series
--   reload       called after a status write-back to rebuild with fresh data
function TomeSync:_bookListMenu(data)
    local items = {}
    table.insert(items, {
        text     = string.format("Download all (%d)", #data.books),
        callback = function() self:_downloadListBooks(data, nil) end,
    })
    -- book_id → on-device path (same signal the download queue uses to skip)
    local id_to_path = {}
    for path, bid in pairs(self.book_map) do
        if lfs.attributes(path) then id_to_path[bid] = path end
    end
    for _, book in ipairs(data.books) do
        local label
        if type(book.series_index) == "number" then
            local vol = book.series_index
            if vol == math.floor(vol) then vol = math.floor(vol) end
            label = "Vol. " .. tostring(vol) .. " — " .. book.title
        else
            label = book.title
        end
        if data.mixed and type(book.series) == "string" and book.series ~= "" then
            label = book.series .. " · " .. label
        end
        if id_to_path[book.id] then
            label = label .. "  · on device"
        end
        -- Status marker (build 33): only for the non-default states.
        if book.status == "reading" or book.status == "read" then
            label = label .. "  · " .. book.status
        end
        table.insert(items, {
            text     = label,
            book     = book,
            callback = function() self:_downloadListBooks(data, book) end,
        })
    end

    local menu
    menu = Menu:new{
        title       = data.title,
        item_table  = items,
        width       = Device.screen:getWidth() - 20,
        height      = Device.screen:getHeight() - 20,
    }
    -- Hold a book row to set its read status (write-back to Tome).
    menu.onMenuHold = function(_, item)
        if item.book then
            self:_statusDialog(item.book, function()
                UIManager:close(menu)
                if data.reload then data.reload() end
            end)
        end
        return true
    end
    UIManager:show(menu)
end

-- Route one book (or the whole list) into the download machinery with the
-- right filing identity: a book with its own series files under that series;
-- a standalone files like the No Series bucket (book-type/author folders).
function TomeSync:_downloadListBooks(data, book)
    local function seriesOf(b)
        if type(b.series) == "string" and b.series ~= "" then return b.series end
        return data.series_name or "__unserialized__"
    end
    if book then
        self:_downloadSeriesBooks(seriesOf(book), { book }, nil,
                                  jstr(book.book_type) or jstr(data.book_type))
        return
    end
    if data.series_name then
        -- Homogeneous list (one series / the No Series bucket): one batch.
        self:_downloadSeriesBooks(data.series_name, data.books, nil, data.book_type)
        return
    end
    -- Mixed list (author / search): file each book by its own identity, then
    -- roll the counts up into one summary.
    local downloaded, skipped, failed = 0, 0, 0
    for _, b in ipairs(data.books) do
        local r = self:_downloadSeriesBooks(seriesOf(b), { b }, nil, b.book_type, true)
        downloaded = downloaded + (r.downloaded or 0)
        skipped    = skipped + (r.skipped or 0)
        failed     = failed + (r.failed or 0)
    end
    UIManager:show(InfoMessage:new{
        text = string.format("%s\n\nDownloaded: %d\nSkipped: %d\nFailed: %d",
                             data.title or "Download", downloaded, skipped, failed),
        timeout = 6,
    })
end

-- Hold-a-row dialog: set unread/reading/read on the server (deliberate user
-- action — unlike telemetry, which only suggests status).
function TomeSync:_statusDialog(book, on_done)
    local dialog
    local function setStatus(status)
        UIManager:close(dialog)
        whenConnected(function()
            local ok, resp, code = pcall(apiRequest, "PUT", "/tome-sync/status/" .. book.id,
                                         { status = status })
            if ok and type(code) == "number" and code < 300 then
                book.status = status
                UIManager:show(Notification:new{
                    text = string.format('"%s" marked %s.', book.title or "Book", status),
                    timeout = 2,
                })
                if on_done then on_done() end
            else
                UIManager:show(InfoMessage:new{
                    text = "Could not update status (" .. tostring(code) .. ").",
                    timeout = 4,
                })
            end
        end)
    end
    dialog = ButtonDialog:new{
        title = (book.title or "Book") .. "\nSet read status",
        buttons = {
            { { text = "Unread",  callback = function() setStatus("unread") end } },
            { { text = "Reading", callback = function() setStatus("reading") end } },
            { { text = "Read",    callback = function() setStatus("read") end } },
        },
    }
    UIManager:show(dialog)
end

-- Adapter kept for the series flow (name/shape unchanged for its callers).
function TomeSync:_seriesBooksMenu(data)
    local display = data.series_name
    if display == "__unserialized__" then display = "No Series" end
    self:_bookListMenu{
        title       = display,
        books       = data.books,
        series_name = data.series_name,
        book_type   = data.book_type,
        reload      = function()
            if #data.books > 0 then self:_openSeriesBooks(data.books[1].id) end
        end,
    }
end

-- ── Author browse axis (build 33) ────────────────────────────────────────────

function TomeSync:_authorsMenu()
    whenConnected(function() self:_authorsMenuImpl() end)
end

function TomeSync:_authorsMenuImpl()
    local ok, authors, code = pcall(apiRequest, "GET", "/tome-sync/authors")
    if not ok or type(authors) ~= "table" or (type(code) == "number" and code >= 300) then
        UIManager:show(ConfirmBox:new{
            text = "Failed to load authors.",
            ok_text = "Retry",
            cancel_text = "Close",
            ok_callback = function() self:_authorsMenuImpl() end,
        })
        return
    end
    local items = {}
    for _, a in ipairs(authors) do
        local name = a.name
        if name == "__unknown__" then name = "Unknown author" end
        table.insert(items, {
            text = name .. " (" .. a.book_count .. ")",
            callback = function() self:_openAuthorBooks(a.name) end,
        })
    end
    UIManager:show(Menu:new{
        title = "Authors",
        item_table = items,
        width = Device.screen:getWidth() - 20,
        height = Device.screen:getHeight() - 20,
    })
end

function TomeSync:_openAuthorBooks(author)
    local ok, data, code = pcall(apiRequest, "GET",
        "/tome-sync/author-books?author=" .. urlEncode(author))
    if ok and type(data) == "table" and data.books then
        local display = author
        if display == "__unknown__" then display = "Unknown author" end
        self:_bookListMenu{
            title  = display,
            books  = data.books,
            mixed  = true,
            reload = function() self:_openAuthorBooks(author) end,
        }
    else
        UIManager:show(ConfirmBox:new{
            text = "Failed to load author's books.",
            ok_text = "Retry",
            cancel_text = "Close",
            ok_callback = function() self:_openAuthorBooks(author) end,
        })
    end
    local _ = code
end

-- ── Shelves axis (build 36) ──────────────────────────────────────────────────

function TomeSync:_shelvesMenu()
    whenConnected(function() self:_shelvesMenuImpl() end)
end

function TomeSync:_shelvesMenuImpl()
    local ok, shelves, code = pcall(apiRequest, "GET", "/tome-sync/shelves")
    if not ok or type(shelves) ~= "table" or (type(code) == "number" and code >= 300) then
        UIManager:show(ConfirmBox:new{
            text = "Failed to load shelves.",
            ok_text = "Retry",
            cancel_text = "Close",
            ok_callback = function() self:_shelvesMenuImpl() end,
        })
        return
    end
    if #shelves == 0 then
        UIManager:show(InfoMessage:new{
            text = "No shelves yet.\nCreate one in the web app - any filtered view can be saved as a shelf.",
            timeout = 5,
        })
        return
    end
    local items = {}
    for _, s in ipairs(shelves) do
        table.insert(items, {
            text = s.name .. " (" .. s.book_count .. ")",
            callback = function() self:_openShelfBooks(s.id, s.name) end,
        })
    end
    UIManager:show(Menu:new{
        title = "Shelves",
        item_table = items,
        width = Device.screen:getWidth() - 20,
        height = Device.screen:getHeight() - 20,
    })
end

function TomeSync:_openShelfBooks(shelf_id, shelf_name)
    local ok, data, code = pcall(apiRequest, "GET",
        "/tome-sync/shelf-books?shelf_id=" .. tostring(shelf_id))
    if ok and type(data) == "table" and data.books then
        -- Shelves mix series, so books file by their own identity on download.
        self:_bookListMenu{
            title  = shelf_name,
            books  = data.books,
            mixed  = true,
            reload = function() self:_openShelfBooks(shelf_id, shelf_name) end,
        }
    else
        UIManager:show(ConfirmBox:new{
            text = "Failed to load shelf.",
            ok_text = "Retry",
            cancel_text = "Close",
            ok_callback = function() self:_openShelfBooks(shelf_id, shelf_name) end,
        })
    end
    local _ = code
end

-- ── Search from the device (build 33) ────────────────────────────────────────

function TomeSync:_recentSearches()
    return self.state:readSetting("tomesync_recent_searches") or {}
end

function TomeSync:_rememberSearch(q)
    local recents = self:_recentSearches()
    for i = #recents, 1, -1 do
        if recents[i] == q then table.remove(recents, i) end
    end
    table.insert(recents, 1, q)
    while #recents > 8 do table.remove(recents) end
    self:_saveState("tomesync_recent_searches", recents)
end

function TomeSync:_searchMenu()
    -- Submit-based input (the right call on e-ink), with recent searches one
    -- tap away underneath.
    local dialog
    dialog = InputDialog:new{
        title = "Search library",
        input_hint = "title, author, or series",
        buttons = {{
            { text = "Cancel", id = "close",
              callback = function() UIManager:close(dialog) end },
            { text = "Search", is_enter_default = true,
              callback = function()
                  local q = dialog:getInputText()
                  if q and q:match("%S") then
                      UIManager:close(dialog)
                      self:_runSearch(q)
                  end
              end },
        }},
    }
    local recents = self:_recentSearches()
    if #recents > 0 then
        -- A second row of up to 3 recent queries; the full list lives in the
        -- results menu title history anyway, and 3 covers the muscle-memory case.
        local row = {}
        for i = 1, math.min(3, #recents) do
            local q = recents[i]
            table.insert(row, { text = q, callback = function()
                UIManager:close(dialog)
                self:_runSearch(q)
            end })
        end
        table.insert(dialog.buttons, 1, row)
    end
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

function TomeSync:_runSearch(q)
    whenConnected(function()
        local ok, data, code = pcall(apiRequest, "GET",
            "/tome-sync/search?q=" .. urlEncode(q))
        if not ok or type(data) ~= "table" or not data.books
                or (type(code) == "number" and code >= 300) then
            UIManager:show(ConfirmBox:new{
                text = "Search failed.",
                ok_text = "Retry",
                cancel_text = "Close",
                ok_callback = function() self:_runSearch(q) end,
            })
            return
        end
        self:_rememberSearch(q)
        if #data.books == 0 then
            UIManager:show(InfoMessage:new{
                text = 'No results for "' .. q .. '".',
                timeout = 3,
            })
            return
        end
        local title = string.format('Search: %s (%d)', q, data.total or #data.books)
        if (data.total or 0) > #data.books then
            title = string.format('Search: %s (%d of %d)', q, #data.books, data.total)
        end
        self:_bookListMenu{
            title  = title,
            books  = data.books,
            mixed  = true,
            reload = function() self:_runSearch(q) end,
        }
    end)
end

function TomeSync:_browseSeriesMenu()
    whenConnected(function() self:_browseSeriesMenuImpl() end)
end

function TomeSync:_browseSeriesMenuImpl()
    if not NetworkMgr:isConnected() then
        UIManager:show(InfoMessage:new{
            text = "WiFi not connected.",
            timeout = 3,
        })
        return
    end

    local ok, series_list, code = pcall(apiRequest, "GET", "/tome-sync/series")
    if not ok or type(series_list) ~= "table" or (type(code) == "number" and code >= 300) then
        UIManager:show(ConfirmBox:new{
            text = "Failed to load series list.",
            ok_text = "Retry",
            cancel_text = "Close",
            ok_callback = function() self:_browseSeriesMenuImpl() end,
        })
        return
    end

    local items = {}
    -- Cross-axis entry points (build 33): search and the author axis live at
    -- the top of the browser, so standalones aren't stuck behind "No Series".
    table.insert(items, {
        text     = "Search library…",
        callback = function() self:_searchMenu() end,
    })
    table.insert(items, {
        text     = "Browse by author",
        callback = function() self:_authorsMenu() end,
    })
    table.insert(items, {
        text      = "Shelves",
        separator = true,
        callback  = function() self:_shelvesMenu() end,
    })
    for _, s in ipairs(series_list) do
        local name = s.name
        if name == "__unserialized__" then name = "No Series" end
        local text = name .. " (" .. s.book_count .. ")"
        -- type-check guards against JSON null, which rapidjson decodes to a
        -- truthy userdata sentinel rather than nil.
        if type(s.author) == "string" and s.author ~= "" then
            text = text .. " - " .. s.author
        end
        table.insert(items, {
            text = text,
            callback = function()
                self:_openSeriesBooks(s.first_book_id)
            end,
        })
    end

    local menu = Menu:new{
        title = "Series Browser",
        item_table = items,
        width = Device.screen:getWidth() - 20,
        height = Device.screen:getHeight() - 20,
    }
    UIManager:show(menu)
end

function TomeSync:_openSeriesBooks(first_book_id)
    -- Fetch the books in this series, then drill into a per-book list so a
    -- single title can be downloaded instead of the whole series.
    local ok2, data, code2 = pcall(apiRequest, "GET", "/tome-sync/series/" .. first_book_id)
    if ok2 and type(data) == "table" and data.books then
        self:_seriesBooksMenu(data)
    else
        UIManager:show(ConfirmBox:new{
            text = "Failed to load series books.",
            ok_text = "Retry",
            cancel_text = "Close",
            ok_callback = function() self:_openSeriesBooks(first_book_id) end,
        })
    end
    local _ = code2
end

function TomeSync:_downloadCurrentBookSeries(rest_only)
    if not self.book_id then
        UIManager:show(InfoMessage:new{
            text = "No book resolved. Open a book first.",
            timeout = 3,
        })
        return
    end

    whenConnected(function() self:_downloadCurrentBookSeriesImpl(rest_only) end)
end

function TomeSync:_downloadCurrentBookSeriesImpl(rest_only)
    local ok, data, code = pcall(apiRequest, "GET",
        "/tome-sync/series/" .. self.book_id)
    if not ok or not data or not data.books then
        UIManager:show(InfoMessage:new{
            text = "Failed to load series (book may not belong to one).",
            timeout = 4,
        })
        return
    end

    local min_index = nil
    if rest_only then
        -- Find current book's series_index
        for _, b in ipairs(data.books) do
            if b.id == self.book_id then
                if type(b.series_index) == "number" then
                    min_index = b.series_index
                end
                break
            end
        end
    end

    self:_downloadSeriesBooks(data.series_name, data.books, min_index, data.book_type)
end

-- ── Device inventory (build 50) ──────────────────────────────────────────────
-- Tells Tome which of its books are on this device, so the web and the app
-- can show "On <device name>" and filter by it. The list is the book map
-- (every download, inbox delivery, resolved open and sweep match lands there)
-- minus files that no longer exist, and each upload REPLACES the device's
-- list on the server: KOReader raises no event when a file is deleted, so a
-- delete is picked up by the next push. Ids only, never paths or filenames.
-- Sent on launch, resume, WiFi up, Sync now and a few seconds after the book
-- map changes; an unchanged list is re-sent at most every INVENTORY_REFRESH
-- so the server's "last synced" stays meaningful.
local INVENTORY_REFRESH = 6 * 3600

function TomeSync:_deviceBookIds()
    local seen, ids = {}, {}
    for path, id in pairs(self.book_map) do
        if type(id) == "number" and not seen[id]
                and lfs.attributes(path, "mode") == "file" then
            seen[id] = true
            table.insert(ids, id)
        end
    end
    table.sort(ids)
    return ids
end

function TomeSync:_pushDeviceBooks(force)
    if not NetworkMgr:isConnected() then return end
    local ids = self:_deviceBookIds()
    local sig = deviceName() .. "|" .. table.concat(ids, ",")
    local ok_sha, sha2 = pcall(require, "ffi/sha2")
    if ok_sha and sha2 and sha2.md5 then sig = sha2.md5(sig) end
    local last = self.state:readSetting("tomesync_inventory_sent") or {}
    if not force and last.sig == sig and os.time() - (last.at or 0) < INVENTORY_REFRESH then
        return
    end
    local ok, _, code = pcall(apiRequest, "PUT", "/tome-sync/device-books",
        { device = deviceName(), book_ids = ids, plugin_build = BUILD })
    if ok and type(code) == "number" and code >= 200 and code < 300 then
        self.state:saveSetting("tomesync_inventory_sent", { sig = sig, at = os.time() })
        self.state:flush()
        logger.dbg("TomeSync: device inventory sent,", #ids, "book(s)")
    end
end

-- Debounced: a series download saves the map once per batch, a sweep once
-- per chunk; one push a few seconds after the last change covers them all.
function TomeSync:_scheduleDeviceBooks()
    if inventory_task then UIManager:unschedule(inventory_task) end
    inventory_task = function()
        inventory_task = nil
        pcall(function() self:_pushDeviceBooks(false) end)
    end
    UIManager:scheduleIn(5, inventory_task)
end

-- ── Library sweep: adopt closed books (build 34) ─────────────────────────────
-- Walks the device library for books TomeSync has NEVER synced (pre-Tome
-- history, other launchers, sideloads), resolves them by partial-MD5 in
-- batches, and adopts each sidecar's status/rating/progress. The server fills
-- gaps only, so re-sweeping is always safe. Resumable: every book is ack-gated
-- into a ledger keyed by its sidecar's mtime — re-runs skip everything already
-- taken unless the sidecar changed since.

function TomeSync:_sweepLibrary()
    if sweep_running then
        UIManager:show(InfoMessage:new{ text = "Library sweep already running.", timeout = 3 })
        return
    end
    whenConnected(function() self:_sweepLibraryImpl() end)
end

function TomeSync:_sweepLibraryImpl()
    local base_dir = G_reader_settings:readSetting("home_dir")
                  or G_reader_settings:readSetting("download_dir")
                  or G_reader_settings:readSetting("lastdir")
    if not base_dir then
        UIManager:show(InfoMessage:new{ text = "No home folder configured.", timeout = 4 })
        return
    end
    local DocSettings = require("docsettings")
    local sweep = self.state:readSetting("tomesync_sweep") or {}
    sweep.done = sweep.done or {}

    -- Phase 1: walk — never-synced books with a sidecar whose mtime moved past
    -- the ledger. Cheap: no file is opened or hashed here.
    local candidates = {}
    local function walk(dir, depth)
        if depth > 10 then return end
        local ok, iter, dirobj = pcall(lfs.dir, dir)
        if not ok then return end
        for entry in iter, dirobj do
            if entry ~= "." and entry ~= ".." and entry:sub(1, 1) ~= "." then
                local path = dir .. "/" .. entry
                local mode = lfs.attributes(path, "mode")
                if mode == "directory" then
                    if not entry:match("%.sdr$") then walk(path, depth + 1) end
                elseif mode == "file" then
                    local ext = entry:match("%.([^.]+)$")
                    if ext and SWEEP_EXTS[ext:lower()] and not self.book_map[path] then
                        local sidecar = DocSettings:findSidecarFile(path)
                        if sidecar then
                            local smtime = lfs.attributes(sidecar, "modification") or 0
                            if (sweep.done[path] or -1) < smtime then
                                table.insert(candidates, { path = path, smtime = smtime })
                            end
                        end
                    end
                end
            end
        end
    end
    walk(base_dir, 0)

    if #candidates == 0 then
        UIManager:show(InfoMessage:new{ text = "Library sweep: nothing new to adopt.", timeout = 4 })
        return
    end

    sweep_running = true
    UIManager:show(Notification:new{
        text = string.format("TomeSync: sweeping %d closed book(s)…", #candidates),
        timeout = 3,
    })

    local stats = { matched = 0, adopted = 0, unmatched = 0, failed = 0 }
    local idx = 1
    local CHUNK = 5

    local function finish(aborted)
        sweep_running = false
        self:_saveState("tomesync_sweep", sweep)
        UIManager:show(InfoMessage:new{
            text = string.format(
                "Library sweep %s.\n\nMatched in Tome: %d\nState adopted: %d\nNot in Tome: %d\nFailed: %d",
                aborted and "stopped (server unreachable)" or "done",
                stats.matched, stats.adopted, stats.unmatched, stats.failed),
            timeout = 8,
        })
        -- Newly matched books can now take Tome's metadata too (opt-in).
        if not aborted and stats.matched > 0 then
            UIManager:scheduleIn(1, function() pcall(function() self:_syncMetadata(false) end) end)
        end
    end

    local function step()
        if idx > #candidates then finish(false) return end
        local batch = {}
        while idx <= #candidates and #batch < CHUNK do
            table.insert(batch, candidates[idx])
            idx = idx + 1
        end
        -- Phase 2: hash + batch-resolve this chunk, then adopt each match.
        local hashes, by_hash = {}, {}
        for _, c in ipairs(batch) do
            local md5 = util.partialMD5(c.path)
            if md5 then
                table.insert(hashes, md5)
                by_hash[md5] = c
            else
                stats.failed = stats.failed + 1
            end
        end
        local ok, resp, code = pcall(apiRequest, "POST", "/tome-sync/match-hashes",
                                     { hashes = hashes })
        if not ok or type(resp) ~= "table" or type(resp.matches) ~= "table"
                or (type(code) == "number" and code >= 300) then
            -- Server gone mid-run: stop; the ledger keeps everything adopted so far.
            finish(true)
            return
        end
        for _, md5 in ipairs(hashes) do
            local c = by_hash[md5]
            local book_id = resp.matches[md5]
            if type(book_id) == "number" then
                stats.matched = stats.matched + 1
                -- A sweep match is a real resolve: everything else (positions,
                -- annotations, ratings) now works for this book too.
                self.book_map[c.path] = book_id
                if self:_adoptSidecar(book_id, c.path) then
                    stats.adopted = stats.adopted + 1
                    sweep.done[c.path] = c.smtime
                else
                    stats.failed = stats.failed + 1
                end
            else
                stats.unmatched = stats.unmatched + 1
                -- Ledger it anyway — no point re-hashing every run; a changed
                -- sidecar (mtime) will retry it naturally.
                sweep.done[c.path] = c.smtime
            end
        end
        self:_saveState("tomesync_book_map", self.book_map)
        self:_saveState("tomesync_sweep", sweep)
        UIManager:scheduleIn(0.5, step)
    end
    step()
end

function TomeSync:_adoptSidecar(book_id, path)
    -- Read the CLOSED book's sidecar (no document open) and push its state;
    -- the server fills gaps only, so nothing here can clobber live sync state.
    local DocSettings = require("docsettings")
    local okd, ds = pcall(DocSettings.open, DocSettings, path)
    if not okd or not ds then return false end
    local summary = ds:readSetting("summary") or {}
    local pct = tonumber(ds:readSetting("percent_finished"))
    local xp = ds:readSetting("last_xpointer")
    local status = nil
    if summary.status == "complete" then
        status = "read"
    elseif summary.status == "reading" then
        status = "reading"
    end
    -- "abandoned" has no Tome equivalent; the book's other state still adopts.
    local body = {
        status = status,
        rating = tonumber(summary.rating),
        review = (type(summary.note) == "string" and summary.note ~= "") and summary.note or nil,
        percentage = pct,
        progress = (type(xp) == "string") and xp or nil,
        device = deviceName(),
    }
    if body.status == nil and body.rating == nil and body.percentage == nil then
        -- Bare sidecar (layout settings only): nothing to adopt, count as done.
        return true
    end
    local ok, _resp, code = pcall(apiRequest, "POST", "/tome-sync/sweep/" .. book_id, body)
    return ok and type(code) == "number" and code < 300
end

-- ── Metadata sync (Tome → device) ────────────────────────────────────────────
-- Writes Tome's metadata into KOReader's own custom-metadata sidecar
-- (custom_metadata.lua + cover.<ext> next to the .sdr) — the same override
-- layer that Book information > Edit fills in by hand. The book file itself
-- is never touched, so its partial-MD5 identity, reading position and sidecar
-- state all stay put; deleting the sidecar file restores the file's own
-- metadata.
--
-- Safety: the server answers ONLY for (book_id, partial-MD5) pairs it has on
-- record for that exact book (hashed when Tome scanned or served the file).
-- A filename-heuristic resolve, a different edition or a sideloaded file it
-- never saw is rejected server-side, so one book's metadata can never land
-- on another file.
--
-- Per-file ledger (dedicated state file, pruned with book_map):
--   path -> { id=, md5=, size=, mtime=, fp=, keys={...} }
-- md5/size/mtime skip re-hashing files that have not changed; fp is the
-- server's content fingerprint (a match = nothing to do, nothing sent);
-- keys = which custom_props we own, so a field Tome later clears falls back
-- to the file's embedded value instead of sticking.

local META_CHUNK = 25
local META_MIN_KOREADER = "2023.10"

-- KOReader gained custom metadata in v2023.10; older builds have no
-- DocSettings:flushCustomMetadata, so there is nothing to write into.
local function metaSyncSupported()
    local ok, DocSettings = pcall(require, "docsettings")
    return ok and type(DocSettings) == "table"
        and type(DocSettings.flushCustomMetadata) == "function"
        and type(DocSettings.findCustomMetadataFile) == "function"
end
local META_PROPS = { "title", "authors", "series", "series_index", "language",
                     "keywords", "description" }

-- Tome stores every cover under a .jpg name whatever the bytes are; KOReader
-- picks the decoder by extension, so name the copy by its real format.
local function sniffImageExt(path)
    local fh = io.open(path, "rb")
    if not fh then return nil end
    local head = fh:read(12) or ""
    fh:close()
    local b1, b2 = head:byte(1, 2)
    if b1 == 255 and b2 == 216 then return "jpg" end
    if b1 == 137 and head:sub(2, 4) == "PNG" then return "png" end
    if head:sub(1, 4) == "RIFF" and head:sub(9, 12) == "WEBP" then return "webp" end
    if head:sub(1, 6) == "GIF87a" or head:sub(1, 6) == "GIF89a" then return "gif" end
    return nil
end

-- What the device currently holds for a file's custom metadata: the mtimes
-- of custom_metadata.lua and the custom cover. Stored in the ledger after a
-- write and compared before the next one, so an edit or reset made on the
-- device (KOReader rewrites those files) forces a re-apply even when Tome's
-- fingerprint is unchanged - "Tome is the source of truth" has to hold in
-- that direction too.
local function sidecarSig(path)
    local DocSettings = require("docsettings")
    local cm = DocSettings:findCustomMetadataFile(path)
    local cv = DocSettings:findCustomCoverFile(path)
    return tostring(cm and lfs.attributes(cm, "modification") or 0) .. ":"
        .. tostring(cv and lfs.attributes(cv, "modification") or 0)
end

-- CoverBrowser's BookInfoManager (BIM) caches title/author/series/cover per
-- file in bookinfo_cache.sqlite3, and other plugins (bookshelf) build their
-- shelves and series groups from those rows. Build 42/43 *deleted* the row
-- for every book it touched (InvalidateMetadataCache) and relied on the
-- cover browser re-extracting on display - but only the cover browser does
-- that, only for what it shows, so books not browsed that way dropped out
-- of bookshelf's series views. Now the row is updated in place, and a row
-- that is missing gets a background re-extraction queued (BIM's own
-- subprocess, no UI freeze; covers follow lazily on display).
local function bookInfoManager()
    local ok, bim = pcall(require, "bookinfomanager")
    if ok and type(bim) == "table" and bim.getBookInfo and bim.setBookInfoProperties then
        return bim
    end
    return nil
end

-- Update BIM's cached row with what the device now shows. `props` are the
-- effective custom_props; `cleared` lists keys that went back to the file's
-- own value (NULL them, the next extraction fills the true value in).
-- Returns "updated" | "missing" | nil (no BIM).
local function updateBookInfoRow(path, props, cleared, cover_changed)
    local bim = bookInfoManager()
    if not bim then return nil end
    local ok, row = pcall(bim.getBookInfo, bim, path, false)
    if not ok or type(row) ~= "table" then return "missing" end
    local upd = {}
    for _, k in ipairs(META_PROPS) do
        if props[k] ~= nil then
            upd[k] = props[k]
        elseif cleared and cleared[k] then
            upd[k] = false
        end
    end
    if cover_changed then
        -- Keep the old thumbnail on screen; a nil cover_fetched makes the
        -- cover browser / bookshelf re-fetch the cover at their own size.
        upd.cover_fetched = false
    end
    if next(upd) ~= nil then
        pcall(bim.setBookInfoProperties, bim, path, upd)
    end
    return "updated"
end

-- BIM's bookinfo columns, in INSERT order (stable since 2020). Used to write
-- a metadata-only row for a file whose row is missing - exactly what BIM's
-- own extraction produces without cover_specs, minus the document open.
-- Covers follow lazily: cover_fetched stays NULL, so the cover browser and
-- bookshelf fetch one at their own size when they next display the book.
local BIM_COLS = {
    "directory", "filename", "filesize", "filemtime", "in_progress",
    "unsupported", "cover_fetched", "has_meta", "has_cover", "cover_sizetag",
    "ignore_meta", "ignore_cover", "pages",
    "title", "authors", "series", "series_index", "language", "keywords", "description",
    "cover_w", "cover_h", "cover_bb_type", "cover_bb_stride", "cover_bb_data",
}

-- Insert (or replace a stale in-progress) row from the merged view KOReader
-- shows: custom props over the file's own. Returns true on success.
local function insertBookInfoRow(path, custom, orig)
    local bim = bookInfoManager()
    if not bim then return false end
    local attr = lfs.attributes(path)
    if not attr then return false end
    local dir, name = util.splitFilePathName(path)
    local row = {
        directory = dir, filename = name,
        filesize = attr.size, filemtime = attr.modification,
        in_progress = 0, pages = orig and orig.pages or nil,
    }
    local any = false
    for _, k in ipairs(META_PROPS) do
        local v = custom[k]
        if v == nil and orig then v = orig[k] end
        if v ~= nil then row[k] = v; any = true end
    end
    if any then row.has_meta = "Y" end
    local ok = pcall(function()
        bim:openDbConnection()
        for i, col in ipairs(BIM_COLS) do
            bim.set_stmt:bind1(i, row[col])
        end
        bim.set_stmt:step()
        bim.set_stmt:clearbind():reset()
    end)
    if not ok then
        pcall(function() bim.set_stmt:clearbind():reset() end)
    end
    return ok
end

-- Self-heal for a file we wrote to earlier whose row is gone (deleted by
-- build 42/43): rebuild it from the custom-metadata file we left behind.
local function healBookInfoRow(path)
    local bim = bookInfoManager()
    if not bim then return true end             -- nothing to heal without BIM
    local ok, row = pcall(bim.getBookInfo, bim, path, false)
    if not ok or row ~= nil then return true end
    local DocSettings = require("docsettings")
    local cmf = DocSettings:findCustomMetadataFile(path)
    if not cmf then return true end
    local cds = DocSettings.openSettingsFile(cmf)
    local custom = cds:readSetting("custom_props")
    if type(custom) ~= "table" then return true end
    local ok = insertBookInfoRow(path, custom, cds:readSetting("doc_props"))
    return ok, ok      -- healed?, wrote a row?
end

-- Last resort when a direct row write failed (schema drift): BIM's own
-- background extraction. Deferred a few seconds so it does not collide with
-- the cover browser's post-refresh job (starting a job kills the previous).
local function reextractMissingRows(paths)
    if #paths == 0 then return 0 end
    local bim = bookInfoManager()
    if not bim or not bim.extractInBackground then return 0 end
    local files = {}
    for _, path in ipairs(paths) do
        table.insert(files, { filepath = path })
    end
    UIManager:scheduleIn(5, function() pcall(bim.extractInBackground, bim, files) end)
    return #files
end

local function fetchToFile(api_path, dest)
    if not NetworkMgr:isConnected() then return false, "offline" end
    local fh = io.open(dest, "wb")
    if not fh then return false, "cannot open temp file" end
    local ok, code = serverRequest({
        method  = "GET",
        path    = "/api" .. api_path,
        headers = { ["Authorization"] = "Bearer " .. apiKey() },
        -- Reopened per attempt so a broken-off attempt leaves no bytes behind.
        sink    = function()
            pcall(fh.close, fh)
            fh = io.open(dest, "wb")
            return ltn12.sink.file(fh)
        end,
        block   = 10,
        total   = 60,
    })
    if fh then pcall(fh.close, fh) end
    if not ok or (type(code) == "number" and code >= 300) then
        os.remove(dest)
        return false, tostring(code or "request failed")
    end
    return true
end

-- The file's own embedded metadata, as KOReader reads it. Stored as doc_props
-- in the custom-metadata file so Book information's "reset" can restore a
-- field (KOReader indexes that table unconditionally, so it must exist).
-- Cheapest source first; opening the document is the last resort — exactly
-- what KOReader itself does when you edit a never-opened book's metadata.
function TomeSync:_originalProps(path)
    local ok, props = pcall(function()
        local cb = self.ui and self.ui.coverbrowser
        if cb and cb.getDocProps then
            local p = cb.getDocProps(path)
            if type(p) == "table" then return p end
        end
        local BookList = require("ui/widget/booklist")
        if BookList.hasBookBeenOpened(path) then
            local ds = BookList.getDocSettings(path)
            local p = ds and ds:readSetting("doc_props")
            if type(p) == "table" then
                local copy = {}
                for k, v in pairs(p) do copy[k] = v end
                copy.pages = ds:readSetting("doc_pages") or copy.pages
                return copy
            end
        end
        local DocumentRegistry = require("document/documentregistry")
        local document = DocumentRegistry:hasProvider(path)
                     and DocumentRegistry:openDocument(path)
        if not document then return nil end
        local loaded, pages = true, nil
        if document.loadDocument then           -- CreDocument: metadata only
            if not document:loadDocument(false) then loaded = false end
        else
            pages = document:getPageCount()
        end
        local p = loaded and document:getProps() or nil
        if p then p.pages = pages end
        document:close()
        return p
    end)
    if not ok then
        logger.warn("TomeSync: could not read original props for", path, props)
        return {}
    end
    props = type(props) == "table" and props or {}
    props.display_title = nil
    return props
end

-- Writes one book's props (+ cover) into its custom-metadata sidecar.
-- Returns ok, err, keys_written, cover_ok. Never opens the book file for
-- writing; a failed cover fetch leaves the props applied and is reported so
-- the caller can retry the cover next run.
function TomeSync:_applyDeviceMetadata(path, entry, prev_keys)
    local DocSettings = require("docsettings")
    local props = type(entry.props) == "table" and entry.props or {}
    local custom_file = DocSettings:findCustomMetadataFile(path)
    local cds
    if custom_file then
        cds = DocSettings.openSettingsFile(custom_file)
        if type(cds:readSetting("doc_props")) ~= "table" then
            cds:saveSetting("doc_props", self:_originalProps(path))
        end
    else
        cds = DocSettings.openSettingsFile()
        cds:saveSetting("doc_props", self:_originalProps(path))
    end
    local custom = cds:readSetting("custom_props")
    if type(custom) ~= "table" then custom = {} end
    local keys = {}
    for _, k in ipairs(META_PROPS) do
        local v = jval(props[k])
        if v ~= nil then
            custom[k] = v
            keys[k] = true
        elseif prev_keys and prev_keys[k] then
            custom[k] = nil     -- Tome cleared a field we set: back to the file's own
        end
    end
    if next(custom) == nil then
        if custom_file then os.remove(custom_file) end
    else
        cds:saveSetting("custom_props", custom)
        if not cds:flushCustomMetadata(path) then
            return false, "could not write custom metadata"
        end
        -- Read back through the same lookup KOReader uses. On read-only book
        -- storage flushCustomMetadata falls back to KOReader's internal
        -- docsettings folder, but a stale copy left in the book folder still
        -- shadows it - the device would keep showing the old values while we
        -- record the book as applied. Count that as a failure instead, so it
        -- is retried rather than forgotten.
        local eff = DocSettings:findCustomMetadataFile(path)
        local seen = eff and DocSettings.openSettingsFile(eff):readSetting("custom_props")
        if type(seen) ~= "table" then
            return false, "custom metadata not readable after write"
        end
        for k in pairs(keys) do
            if seen[k] ~= custom[k] then
                return false, "custom metadata shadowed by a stale sidecar copy"
            end
        end
    end

    local cover_ok = true
    if entry.cover == true then
        local tmp_dir = DataStorage:getDataDir() .. "/cache"
        util.makePath(tmp_dir)
        local tmp = tmp_dir .. "/tomesync_cover_" .. tostring(entry.book_id)
        local fok = fetchToFile("/books/" .. tostring(entry.book_id) .. "/cover", tmp)
        local ext = fok and sniffImageExt(tmp) or nil
        if fok and ext then
            local named = tmp .. "." .. ext     -- flushCustomCover copies as cover.<ext>
            os.rename(tmp, named)
            local old = DocSettings:findCustomCoverFile(path)
            if old then os.remove(old) end
            if not DocSettings:flushCustomCover(path, named) then cover_ok = false end
            os.remove(named)
        else
            cover_ok = false
            os.remove(tmp)
        end
    end
    -- Keep the cover browser's cached row current (it caches the merged
    -- props + cover at extraction time). Updated in place; never deleted.
    local cleared = {}
    if prev_keys then
        for k in pairs(prev_keys) do if not keys[k] then cleared[k] = true end end
    end
    local row_state = updateBookInfoRow(path, custom, cleared, entry.cover == true and cover_ok)
    if row_state == "missing" then
        row_state = insertBookInfoRow(path, custom, cds:readSetting("doc_props")) and "inserted" or "missing"
    end
    return true, nil, keys, cover_ok, sidecarSig(path), row_state
end

-- Undo what we wrote for a file Tome no longer vouches for (the file was
-- replaced under the same name, the book was deleted in Tome, visibility
-- changed): drop only the custom_props keys we own and the cover we placed,
-- so the file falls back to its own embedded metadata. Anything the user
-- customised by hand on the device is left alone.
function TomeSync:_revokeDeviceMetadata(path, prev_keys, had_cover)
    local DocSettings = require("docsettings")
    local changed = false
    local custom_file = DocSettings:findCustomMetadataFile(path)
    if custom_file and type(prev_keys) == "table" then
        local cds = DocSettings.openSettingsFile(custom_file)
        local custom = cds:readSetting("custom_props")
        if type(custom) == "table" then
            for k in pairs(prev_keys) do
                if custom[k] ~= nil then custom[k] = nil; changed = true end
            end
            if changed then
                if next(custom) == nil then
                    os.remove(custom_file)
                else
                    cds:saveSetting("custom_props", custom)
                    cds:flushCustomMetadata(path)
                end
            end
        end
    end
    if had_cover then
        local cover = DocSettings:findCustomCoverFile(path)
        if cover then os.remove(cover); changed = true end
    end
    if changed then
        -- The row still holds our values: put the file's own back (NULL where
        -- the file has none) and let the cover be re-fetched on display.
        local bim = bookInfoManager()
        if bim then
            local orig = custom_file and DocSettings.openSettingsFile(custom_file):readSetting("doc_props")
            local upd = {}
            for k in pairs(prev_keys or {}) do
                local v = type(orig) == "table" and orig[k] or nil
                upd[k] = (v == nil) and false or v
            end
            if had_cover then upd.cover_fetched = false end
            if next(upd) ~= nil then pcall(bim.setBookInfoProperties, bim, path, upd) end
        end
    end
    return changed
end

function TomeSync:_syncMetadata(interactive)
    if not interactive and not G_reader_settings:isTrue("tomesync_meta_sync") then return end
    if not metaSyncSupported() then
        if interactive then
            UIManager:show(InfoMessage:new{
                text = "Metadata sync needs KOReader " .. META_MIN_KOREADER
                       .. " or newer (custom book metadata).",
                timeout = 5,
            })
        end
        return
    end
    -- Automatic triggers (launch, WiFi up, sweep) can land within seconds of
    -- each other - KOReader raises NetworkConnected right at startup - and a
    -- second pass would only re-send fingerprints. One automatic run per two
    -- minutes is plenty; "Apply Tome metadata now" is never debounced.
    if not interactive then
        if os.time() - meta_last_auto < 120 then return end
        meta_last_auto = os.time()
    end
    if meta_sync_running then
        if interactive then
            UIManager:show(InfoMessage:new{ text = "Metadata sync already running.", timeout = 3 })
        end
        return
    end
    if interactive then
        whenConnected(function() self:_syncMetadataImpl(true) end)
    elseif NetworkMgr:isConnected() then
        self:_syncMetadataImpl(false)
    end
end

function TomeSync:_syncMetadataImpl(interactive)
    -- The book open in the reader right now is left for the next run: its
    -- in-memory doc_props would otherwise disagree with the sidecar.
    local open_file = self.ui and self.ui.document and self.ui.document.file
    local candidates = {}
    for path, id in pairs(self.book_map) do
        if type(id) == "number" and path ~= open_file then
            local attr = lfs.attributes(path)
            if attr and attr.mode == "file" then
                table.insert(candidates, { path = path, id = id,
                                           size = attr.size, mtime = attr.modification })
            end
        end
    end
    table.sort(candidates, function(a, b) return a.path < b.path end)
    if #candidates == 0 then
        if interactive then
            UIManager:show(InfoMessage:new{
                text = "No Tome books on this device yet.\nOpen or download a book first.",
                timeout = 4,
            })
        end
        return
    end

    meta_sync_running = true
    local stats = { updated = 0, unchanged = 0, rejected = 0, failed = 0,
                     covers_failed = 0, reverted = 0 }
    local total, done, changed_any = #candidates, 0, false
    local missing_rows = {}

    -- Everything below runs as a chain of small scheduled steps - one file
    -- hashed, or one book applied, per tick - so the UI repaints and taps
    -- keep working between them. The first run on a device did all of this
    -- in a few multi-second bursts on the UI thread (hash 25 files, one
    -- request, then 25 cover fetches + document opens back to back) and the
    -- home screen sat frozen for ~30 s (issue #210 follow-up).
    local TICK = 0.05

    local function progress(text)
        if interactive then
            UIManager:show(Notification:new{ text = text, timeout = 2 })
        end
    end

    local function finish(err)
        meta_sync_running = false
        self:_saveState("tomesync_meta_ledger", self.meta_ledger)
        local queued = reextractMissingRows(missing_rows)
        if queued > 0 then
            logger.info("TomeSync: queued background metadata extraction for", queued, "file(s)")
        end
        if changed_any or queued > 0 then
            UIManager:broadcastEvent(Event:new("BookMetadataChanged"))
        end
        if interactive then
            local text = (err and (err .. "\n\n") or "Metadata sync done.\n\n")
                .. string.format("Updated: %d\nUnchanged: %d\nNot matched by Tome: %d\nFailed: %d",
                                 stats.updated, stats.unchanged, stats.rejected, stats.failed)
            if stats.reverted > 0 then
                text = text .. string.format("\nReverted (no longer a Tome match): %d", stats.reverted)
            end
            if stats.covers_failed > 0 then
                text = text .. string.format("\nCovers not fetched: %d", stats.covers_failed)
            end
            UIManager:show(InfoMessage:new{ text = text, timeout = 8 })
        elseif stats.updated > 0 then
            UIManager:show(Notification:new{
                text = string.format("TomeSync: metadata updated for %d book(s)", stats.updated),
                timeout = 3,
            })
        end
    end

    -- Apply the server's answer for one batch, one book per tick.
    local function applyBatch(by_key, resp, on_done)
        local function bump(c)
            done = done + 1
            if done % 5 == 0 or done == total then
                progress(string.format("TomeSync: metadata %d of %d", done, total))
            end
        end
        local function each(list, fn)
            if type(list) ~= "table" then return end
            for _, e in ipairs(list) do
                if type(e) == "table" and e.book_id ~= nil and type(e.ko_md5) == "string" then
                    local cs = by_key[tostring(e.book_id) .. "|" .. e.ko_md5]
                    if cs then for _, c in ipairs(cs) do fn(c, e) end end
                end
            end
        end
        -- Cheap bookkeeping first (no I/O beyond the ledger).
        each(resp.unchanged, function(c)
            stats.unchanged = stats.unchanged + 1
            local led = self.meta_ledger[c.path] or {}
            led.id, led.md5, led.size, led.mtime = c.id, c.md5, c.size, c.mtime
            self.meta_ledger[c.path] = led
            -- Self-heal: a book we wrote to earlier whose cache row is gone
            -- (deleted by build 42/43) gets re-extracted in the background.
            if led.keys then
                local healed, wrote = healBookInfoRow(c.path)
                if not healed then
                    table.insert(missing_rows, c.path)
                elseif wrote then
                    -- A cache row was rebuilt: displays must be told, or
                    -- bookshelf keeps its stale series list (Kindle pass).
                    changed_any = true
                end
            end
            bump(c)
        end)
        each(resp.rejected, function(c)
            stats.rejected = stats.rejected + 1
            -- A file we wrote to before is no longer vouched for (replaced on
            -- the device under the same name, book deleted in Tome, ...):
            -- take our metadata back off it so it shows its own again.
            local prev = self.meta_ledger[c.path]
            if prev and (prev.keys or prev.cover) then
                local rok, reverted = pcall(self._revokeDeviceMetadata, self, c.path, prev.keys, prev.cover)
                if rok and reverted then
                    stats.reverted = stats.reverted + 1
                    changed_any = true
                end
            end
            -- Remember the hash (no point re-hashing) but forget any fingerprint
            -- or ownership: nothing of ours may be trusted for this file now.
            self.meta_ledger[c.path] = { id = c.id, md5 = c.md5, size = c.size, mtime = c.mtime }
            bump(c)
        end)
        -- The expensive part - cover fetch, maybe a document open, sidecar
        -- write - one book per tick.
        local work = {}
        each(resp.books, function(c, entry) table.insert(work, { c = c, entry = entry }) end)
        local i = 0
        local function step()
            i = i + 1
            local w = work[i]
            if not w then
                self:_saveState("tomesync_meta_ledger", self.meta_ledger)
                on_done()
                return
            end
            local c, entry = w.c, w.entry
            local prev = self.meta_ledger[c.path]
            local aok, res, err, keys, cover_ok, sig, row_state =
                pcall(self._applyDeviceMetadata, self, c.path, entry, prev and prev.keys)
            if aok and res then
                stats.updated = stats.updated + 1
                changed_any = true
                if row_state == "missing" then table.insert(missing_rows, c.path) end
                if not cover_ok then stats.covers_failed = stats.covers_failed + 1 end
                self.meta_ledger[c.path] = {
                    id = c.id, md5 = c.md5, size = c.size, mtime = c.mtime,
                    -- No fingerprint until the cover made it too, so a failed
                    -- cover fetch is retried next run instead of forgotten.
                    fp = cover_ok and jval(entry.fingerprint) or nil,
                    keys = keys, sig = sig,
                    cover = (entry.cover == true and cover_ok) or nil,
                }
            else
                stats.failed = stats.failed + 1
                logger.warn("TomeSync: metadata apply failed for", c.path, aok and err or res)
            end
            bump(c)
            UIManager:scheduleIn(TICK, step)
        end
        step()
    end

    -- Hash one candidate per tick; every META_CHUNK hashed (or at the end),
    -- ask the server about that batch and apply it before hashing on.
    local idx = 0
    local items, by_key, batch_n = {}, {}, 0
    local hashStep
    local function sendBatch()
        local sent_items, sent_keys = items, by_key
        items, by_key, batch_n = {}, {}, 0
        local ok, resp, code = pcall(apiRequest, "POST", "/tome-sync/metadata", { items = sent_items })
        if not ok or type(resp) ~= "table" or type(code) ~= "number" or code >= 300 then
            if code == 404 or code == 405 then
                finish("Your Tome server does not support metadata sync yet - update Tome first.")
            else
                finish("Metadata sync stopped: server unreachable.")
            end
            return
        end
        applyBatch(sent_keys, resp, function() UIManager:scheduleIn(TICK, hashStep) end)
    end
    hashStep = function()
        idx = idx + 1
        local c = candidates[idx]
        if not c then
            if batch_n > 0 then sendBatch() else finish() end
            return
        end
        local led = self.meta_ledger[c.path]
        local md5
        if led and led.md5 and led.size == c.size and led.mtime == c.mtime then
            md5 = led.md5
        else
            local hok, h = pcall(util.partialMD5, c.path)
            md5 = (hok and type(h) == "string") and h or nil
        end
        if md5 then
            c.md5 = md5
            local untouched = led and led.id == c.id and led.fp
                              and led.sig == sidecarSig(c.path)
            table.insert(items, {
                book_id = c.id, ko_md5 = md5,
                fingerprint = untouched and led.fp or nil,
            })
            local key = tostring(c.id) .. "|" .. md5
            by_key[key] = by_key[key] or {}
            table.insert(by_key[key], c)
            batch_n = batch_n + 1
        else
            stats.failed = stats.failed + 1
            done = done + 1
        end
        if batch_n >= META_CHUNK then
            sendBatch()
        else
            UIManager:scheduleIn(TICK, hashStep)
        end
    end
    progress(string.format("TomeSync: checking metadata for %d book(s)…", total))
    hashStep()
end

-- ── Self-update ──────────────────────────────────────────────────────────────

-- on_result(avail) where avail is a {build, semver} table if newer, false if
-- up to date, or nil + err message on failure.
function TomeSync:checkForUpdate(on_result)
    local ok, info, code = pcall(apiRequest, "GET", "/plugin/version")
    if not ok or not info or (type(code) == "number" and code >= 300) then
        on_result(nil, "Could not reach server.")
        return
    end
    local server_build = tonumber(info.build or info.version)
    if not server_build then
        on_result(nil, "Server did not report a build.")
        return
    end
    if server_build > BUILD then
        on_result({ build = server_build, semver = info.semver })
    else
        on_result(false)
    end
end

function TomeSync:installUpdate(new_build)
    local body, code = fetchText("/plugin/main-impl.lua")
    if not body then
        UIManager:show(InfoMessage:new{
            text = "Download failed (" .. tostring(code) .. ").\nNothing changed.",
            timeout = 5,
        })
        return
    end
    local valid, why = validateImpl(body)
    if not valid then
        UIManager:show(InfoMessage:new{
            text = "Update rejected: " .. tostring(why) .. ".\nNothing changed.",
            timeout = 6,
        })
        return
    end
    -- Back up the current (known-good) impl, then atomically swap in the new one.
    local current = readWhole(IMPL_PATH)
    if current and not writeWhole(IMPL_BAK, current) then
        UIManager:show(InfoMessage:new{ text = "Could not write backup.", timeout = 5 })
        return
    end
    if not writeWhole(IMPL_PATH .. ".new", body) then
        UIManager:show(InfoMessage:new{ text = "Could not write update.", timeout = 5 })
        return
    end
    if not os.rename(IMPL_PATH .. ".new", IMPL_PATH) then
        os.remove(IMPL_PATH .. ".new")
        UIManager:show(InfoMessage:new{ text = "Could not install update.", timeout = 5 })
        return
    end
    -- config.lua is never part of an update. An install that has none (one
    -- that came up from build 53 or older without a backup to recover the
    -- baked values from) asks the server for its own once; best effort.
    local config_dir = PluginConfig.dir()
    if not readWhole(config_dir .. "/config.lua") then
        local text = fetchText("/plugin/config.lua")
        local cfg = PluginConfig.parse(text)
        if cfg and cfg.API_KEY and cfg.API_KEY ~= "" then
            PluginConfig.write(config_dir, cfg)
        end
    end
    -- Arm the rollback state machine: unconfirmed until the new impl's init() runs.
    local cur_state = G_reader_settings:readSetting("tomesync_update") or {}
    G_reader_settings:saveSetting("tomesync_update", {
        build      = new_build,
        confirmed  = false,
        boots      = 0,
        prev_build = cur_state.build or BUILD,
    })
    G_reader_settings:flush()
    UIManager:show(InfoMessage:new{
        text = "TomeSync updated to build " .. new_build .. ".\nRestart KOReader to apply.",
        timeout = 8,
    })
end

function TomeSync:_promptUpdate(avail)
    local ConfirmBox = require("ui/widget/confirmbox")
    UIManager:show(ConfirmBox:new{
        text = string.format("TomeSync update available: %s (build %d).\nInstall now?",
            avail.semver or "?", avail.build),
        ok_text = "Install",
        ok_callback = function() self:installUpdate(avail.build) end,
    })
end

-- ── Menu ─────────────────────────────────────────────────────────────────────

-- ── Send-to-KOReader inbox (beta) ────────────────────────────────────────────

-- Poll the server inbox. Sets inbox_enabled (false on 404 = feature off),
-- inbox_items and inbox_count. Offline/transient errors keep the last state.
function TomeSync:_refreshInbox()
    local data, code = apiRequest("GET", "/tome-sync/inbox")
    if code == 404 then
        self.inbox_enabled = false
        self.inbox_count   = 0
        self.inbox_items   = {}
        return
    end
    if type(data) == "table" and data.items then
        self.inbox_enabled = true
        self.inbox_items   = data.items
        self.inbox_count   = data.count or #data.items
    end
end

-- Download the given inbox items (filing each by series/author via the shared
-- downloader) and mark each delivered on success. Shows one roll-up popup.
function TomeSync:_deliverInbox(items, auto)
    local delivered, failed = 0, 0
    for _, item in ipairs(items) do
        -- item.series may be JSON null, which rapidjson decodes to a truthy
        -- userdata sentinel (not nil) — so type-check rather than compare to nil.
        local series_name = "__unserialized__"
        if type(item.series) == "string" and item.series ~= "" then
            series_name = item.series
        end
        -- Honour the file pinned at enqueue; otherwise let the downloader choose.
        local files = item.files
        if item.pinned_file_id then
            for _, f in ipairs(item.files or {}) do
                if f.id == item.pinned_file_id then files = { f }; break end
            end
        end
        local book = {
            id = item.book_id, title = item.title,
            series_index = item.series_index, author = item.author,
            files = files,
        }
        -- guarded: the server decides this path, so the reader link's
        -- refusal rules apply (TomeSync._linkDownloadRefused).
        local res = self:_downloadSeriesBooks(series_name, { book }, nil, item.book_type, true, true)
        if res and res.failed == 0 then
            delivered = delivered + 1
            pcall(apiRequest, "POST", "/tome-sync/inbox/" .. urlEncode(tostring(item.id)) .. "/delivered")
        else
            failed = failed + 1
        end
    end
    pcall(function() self:_refreshInbox() end)
    if auto then
        -- Auto-pickup: one short line per batch, nothing when nothing arrived
        -- (a failed item is retried at the next sync moment).
        if delivered > 0 then
            UIManager:show(InfoMessage:new{
                text = string.format("Received %s from Tome Reader", plural(delivered, "book")),
                timeout = 4,
            })
        end
        if failed > 0 then logger.warn("TomeSync: inbox auto-pickup,", failed, "failed") end
        return
    end
    UIManager:show(InfoMessage:new{
        text = string.format("Inbox\n\nDelivered: %d\nFailed: %d", delivered, failed),
        timeout = 5,
    })
end

-- Inbox drill-down: a "Download all" row plus one row per queued book.
function TomeSync:_inboxMenu()
    whenConnected(function() self:_inboxMenuImpl() end)
end

function TomeSync:_inboxMenuImpl()
    if not NetworkMgr:isConnected() then
        UIManager:show(InfoMessage:new{ text = "WiFi not connected.", timeout = 3 })
        return
    end
    self:_refreshInbox()
    local items = self.inbox_items or {}
    if #items == 0 then
        UIManager:show(InfoMessage:new{ text = "Inbox is empty.", timeout = 3 })
        return
    end

    local menu_items = {}
    table.insert(menu_items, {
        text     = string.format("Download all (%d)", #items),
        callback = function()
            if self._inbox_menu then UIManager:close(self._inbox_menu) end
            self:_deliverInbox(items)
        end,
    })
    for _, item in ipairs(items) do
        local label
        if type(item.series_index) == "number" then
            local vol = item.series_index
            if vol == math.floor(vol) then vol = math.floor(vol) end
            label = "Vol. " .. tostring(vol) .. " — " .. item.title
        else
            label = item.title
        end
        table.insert(menu_items, {
            text     = label,
            callback = function()
                if self._inbox_menu then UIManager:close(self._inbox_menu) end
                self:_deliverInbox({ item })
            end,
        })
    end

    self._inbox_menu = Menu:new{
        title       = "Inbox",
        item_table  = menu_items,
        width       = Device.screen:getWidth() - 20,
        height      = Device.screen:getHeight() - 20,
    }
    UIManager:show(self._inbox_menu)
end

-- ── Reader link (build 53) ───────────────────────────────────────────────────
-- A server that looks after this device: the plugin tells it about the
-- device (battery, storage, versions) and the files under the library root,
-- and runs the file commands it queued (rename, move, delete into a trash
-- folder, new folder). Tome Reader, the iOS app, is the first such server.
-- Every route follows the inbox rule: a 404 means the server does not have
-- the feature, so the plugin stays silent and stops asking until KOReader
-- restarts. Against a Tome without these routes that costs one request per
-- process (device-info answers 404 and the rest is never asked).
-- Runs at the sync moments (launch, WiFi up, resume, book open and close,
-- Sync now) as ONE debounced pass shared by every TomeSync instance.
-- Polling: when the server's device-info / commands answer carries
-- poll_seconds > 0, a light pass (commands; device-info at most every 60 s;
-- the tree check at most every 30 s or after a command) follows every
-- poll_seconds (at least 5), 2 s timeouts; in the reader only the commands
-- GET, and no tree check on any pass while a book is open. Paths a server
-- names are checked by linkResolve/linkGuard ("Refused paths" in Tome
-- Reader's tools/reader-link-routes.md); run commands are kept across
-- restarts in settings/tomesync_link.lua. The
-- poll stops on an answer without it, no answer, suspend, WiFi down and the
-- off switch. One scheduled pass at a time (full or light).
-- One do-block: its locals stay out of the main chunk's 200-local budget.
do
local LINK_MAX_ENTRIES = 5000
local link_off     = {}     -- route -> true once the server said it has none
local link_task     = nil    -- the one pending pass
local link_reasons  = {}     -- what asked for it (log only)
local link_launched = false  -- launch pass, once per process
local link_owner    = nil    -- the TomeSync instance that init'd last (the live UI)
local link_poll_task  = nil  -- the one pending light pass
local link_poll_secs  = 0    -- what the server asked for last (0 = no poll)
local link_info_at    = 0    -- os.time() of the last device-info answer
local link_tree_at    = 0    -- os.time() of the last tree check
local link_tree_dirty = true -- a command ran (or nothing was sent yet)
local link_done       = {}   -- command id (string) -> the result posted for it
local link_done_order = {}
local LINK_INFO_EVERY = 60
local LINK_TREE_EVERY = 30
local LINK_DONE_KEEP  = 1000
local LINK_NET_LIGHT  = { block = 2, total = 2 }
local LINK_NET_TREE   = { block = 2, total = 10 }
-- The trash is bounded so unattended deliveries and deletes cannot fill the
-- disk: oldest purged first, the newest item always kept.
local LINK_TRASH_MAX_BYTES = 200 * 1024 * 1024
TomeSync.LINK_TRASH_DAYS  = 30   -- default of tomesync_link_trash_days (0 = never)
local LINK_TRASH_MAX_ITEMS = 50
-- Commands already run, kept across restarts (settings/tomesync_link.lua):
-- a result POST lost to a crash or a kill is posted again, never re-run.
local link_store = nil

local function linkRouteOff(code) return code == 404 or code == 405 end

local function linkStore()
    if not link_store then
        link_store = LuaSettings:open(DataStorage:getSettingsDir() .. "/tomesync_link.lua")
        local list = link_store:readSetting("done")
        if type(list) == "table" then
            for _, e in ipairs(list) do
                if type(e) == "table" and type(e.id) == "string" and type(e.result) == "table"
                        and link_done[e.id] == nil then
                    link_done[e.id] = e.result
                    table.insert(link_done_order, e.id)
                end
            end
            if #list > 0 then
                logger.info("TomeSync: link remembers " .. #link_done_order .. " command result(s) from before")
            end
        end
    end
    return link_store
end

local function linkNow()
    local ok, socket = pcall(require, "socket")
    if ok and socket.gettime then return socket.gettime() end
    return os.time()
end

-- Inbox auto-pickup: off unless the user (or the bake) turned it on.
local function autoDeliver()
    local v = G_reader_settings:readSetting("tomesync_auto_deliver")
    if v == nil then return DEFAULT_AUTO_DELIVER end
    return v == true
end

local function linkRealpath(p)
    local ok, ffiUtil = pcall(require, "ffi/util")
    if not ok or not ffiUtil.realpath then return nil end
    return ffiUtil.realpath(p)
end

-- The library root the link reports and acts in: home_dir, else download_dir
-- (the download code's order, without its last resort lastdir, which moves
-- with every folder the user opens and so cannot be a boundary).
local function linkRoot()
    local dir = G_reader_settings:readSetting("home_dir")
             or G_reader_settings:readSetting("download_dir")
    if type(dir) ~= "string" or dir == "" then return nil end
    local real = linkRealpath(dir)
    if not real or lfs.attributes(real, "mode") ~= "directory" then return nil end
    return real
end

local function linkInside(root, real)
    return real ~= nil and (real == root or real:sub(1, #root + 1) == root .. "/")
end

local function linkRel(root, abs)
    if abs == root then return "" end
    return abs:sub(#root + 2)
end

local function linkParent(abs) return abs:match("^(.*)/[^/]+$") or abs end

local function linkDeviceInfo(root)
    local info = {
        device       = deviceName(),
        device_id    = LinkSession.deviceID(),
        model        = (type(Device.model) == "string") and Device.model or nil,
        plugin_build = BUILD,
        home_dir     = root,
    }
    pcall(function() info.koreader_version = require("version"):getCurrentRevision() end)
    pcall(function()
        if Device.hasBattery and not Device:hasBattery() then return end
        local powerd = Device:getPowerDevice()
        info.battery_pct = powerd:getCapacity()
        info.charging    = powerd:isCharging() and true or false
    end)
    if root then
        pcall(function()
            local du = util.diskUsage(root)
            info.free_bytes  = du.available
            info.total_bytes = du.total
        end)
    end
    return info
end

-- Has this book been opened (a sidecar folder in the book or the docsettings
-- location)? The hash location is not checked: it needs an MD5 per file.
local function linkHasSidecar(abs)
    local ok, has = pcall(function()
        local DocSettings = require("docsettings")
        if lfs.attributes(DocSettings:getSidecarDir(abs, "doc"), "mode") == "directory" then
            return true
        end
        if G_reader_settings:readSetting("document_metadata_folder") == "dir" then
            return lfs.attributes(DocSettings:getSidecarDir(abs, "dir"), "mode") == "directory"
        end
        return false
    end)
    return ok and has or false
end

-- Folders and files under root, depth first, names sorted. Skips dotfiles
-- (the trash included), .sdr folders (so a sidecar and its .old backup are
-- never listed) and symbolic links; lfs only, no shell. A name that is not
-- valid UTF-8 (a Latin-1 byte from a FAT card) is skipped with its folder's
-- contents, one log line per walk: JSON cannot carry it, and one such name
-- made the whole tree PUT invalid. (Build 56.)
local function linkFileTree(root)
    local entries, truncated = {}, false
    local bad, bad_example = 0, nil
    local function utf8ok(name)
        local ok, fixed = pcall(util.fixUtf8, name, "?")
        return not ok or fixed == name
    end
    local function walk(abs, rel)
        local names = {}
        local ok = pcall(function()
            for name in lfs.dir(abs) do
                if name:sub(1, 1) ~= "." then
                    if utf8ok(name) then
                        table.insert(names, name)
                    else
                        bad = bad + 1
                        bad_example = bad_example or ((rel == "" and "" or (rel .. "/"))
                            .. name:gsub(".", function(c)
                                if c:byte() >= 128 then return string.format("\\x%02X", c:byte()) end
                            end))
                    end
                end
            end
        end)
        if not ok then return end
        table.sort(names)
        for _, name in ipairs(names) do
            if truncated then return end
            local p = abs .. "/" .. name
            local r = (rel == "") and name or (rel .. "/" .. name)
            local attr = lfs.symlinkattributes(p)
            if attr and attr.mode == "directory" and not name:match("%.sdr$") then
                if #entries >= LINK_MAX_ENTRIES then truncated = true; return end
                table.insert(entries, { path = r, type = "dir", mtime = attr.modification })
                walk(p, r)
            elseif attr and attr.mode == "file" then
                if #entries >= LINK_MAX_ENTRIES then truncated = true; return end
                table.insert(entries, {
                    path = r, type = "file", size = attr.size,
                    mtime = attr.modification, sdr = linkHasSidecar(p),
                })
            end
        end
    end
    walk(root, "")
    if bad > 0 then
        logger.info("TomeSync: link tree: skipped " .. bad .. " name(s) that are not UTF-8, e.g. " .. bad_example)
    end
    return entries, truncated
end

-- TOMESYNC_LINKGUARD_BEGIN (pure: no requires, no I/O; extracted and run
-- under luajit by tests/test_tomesync_reader_link.py)
-- What the link refuses to name, create or act on, whatever a server asks:
-- any path part starting with a dot (hidden folders such as Kobo's .adds,
-- where KOReader itself lives, and the trash, in any case), and names that
-- KOReader or the device would run (.koplugin, .lua, .sh). KOReader's own
-- folders are refused by identity in linkGuard.
local LINK_TRASH = ".tomesync-trash"
local LINK_CODE_SUFFIXES = { ".koplugin", ".lua", ".sh" }
local function linkPartRefused(part)
    if type(part) ~= "string" or part == "" then return "empty name" end
    if part == "." or part == ".." then return "path may not contain . or .." end
    local low = part:lower()
    if low == LINK_TRASH then return "path is inside the trash" end
    if part:sub(1, 1) == "." then return "names starting with a dot are refused: " .. part end
    for _, sfx in ipairs(LINK_CODE_SUFFIXES) do
        if low:sub(-#sfx) == sfx then return "refused name (" .. sfx .. "): " .. part end
    end
    return nil
end
local function linkPathRefused(rel)
    if type(rel) ~= "string" then return "missing path" end
    for part in rel:gmatch("[^/]+") do
        local why = linkPartRefused(part)
        if why then return why end
    end
    return nil
end
-- p equal to or under dir, ignoring case (FAT and exFAT, where readers keep
-- their books, ignore it).
local function linkUnderNoCase(p, dir)
    if type(p) ~= "string" or type(dir) ~= "string" or dir == "" then return false end
    local a, b = p:lower(), dir:lower()
    return a == b or b == "/" or a:sub(1, #b + 1) == b .. "/"
end
-- A rename whose new name differs from the old only in case: on FAT (and a
-- case-insensitive Mac) both names are one file, so the destination
-- "exists" and is the source itself.
local function linkCaseOnly(old_name, new_name)
    return type(old_name) == "string" and type(new_name) == "string"
        and old_name ~= new_name and old_name:lower() == new_name:lower()
end
-- The spellings KOReader stored (history, collections) that name `path`
-- ignoring case; prefix = true: entries under the folder `path` ("dir/").
-- Distinct, in the order met; empty when none.
local function linkStoredSpellings(stored, path, prefix)
    local out, seen = {}, {}
    local want = path:lower()
    for _, f in ipairs(stored) do
        if type(f) == "string" then
            local cand = prefix and f:sub(1, #path) or f
            if cand:lower() == want and not seen[cand] then
                seen[cand] = true
                table.insert(out, cand)
            end
        end
    end
    return out
end
-- A delivery's file or folder name without the leading dots and spaces that
-- would hide it (".hack__SIGN" -> "hack__SIGN"); the fallback (the book id)
-- when nothing is left. With ext (a file name) the extension is kept and
-- the stem cleaned ("...": getSafeFilename gives ".epub" -> "<id>.epub").
-- Only for deliveries: a server's rename or mkdir to a dot name is a
-- command and stays refused by linkPartRefused.
local function linkSafeName(name, fallback, ext)
    local sfx = ext and ("." .. tostring(ext)) or nil
    if sfx and #name >= #sfx and name:sub(-#sfx):lower() == sfx:lower() then
        return linkSafeName(name:sub(1, -#sfx - 1), fallback) .. name:sub(-#sfx)
    end
    local out = (name:gsub("^[%.%s]+", ""))
    if out == "" then out = tostring(fallback) end
    return out
end
-- TOMESYNC_LINKGUARD_END

-- dev:ino of a path (links followed); nil when it does not exist or the
-- filesystem gives no inode numbers.
local function linkId(p)
    local a = lfs.attributes(p)
    if type(a) ~= "table" or not a.ino or a.ino == 0 then return nil end
    return tostring(a.dev) .. ":" .. tostring(a.ino)
end
local function linkSame(a, b)
    if a == b then return true end
    local ia = linkId(a)
    return ia ~= nil and ia == linkId(b)
end
-- a is b or one of b's folders: b's parents walked up, compared with a by
-- identity (another spelling of one folder on FAT is the same folder).
local function linkContains(a, b)
    local ida = linkId(a)
    local p = b
    while type(p) == "string" and p ~= "" do
        if p == a or (ida and linkId(p) == ida) then return true end
        local up = p:match("^(.*)/[^/]*$")
        if not up or up == p then break end
        p = up
    end
    return false
end

-- KOReader's own folders: its data folder (settings, patches, plugins on
-- some devices), the folder it runs from (KOReader starts in its install
-- folder: frontend/userpatch.lua takes lfs.currentdir() as that) and the
-- plugins folder this plugin was loaded from. Each by its path and its
-- realpath, worked out once per process.
local link_protected = nil
local function linkProtected()
    if link_protected then return link_protected end
    local list, seen = {}, {}
    local function add(label, p)
        if type(p) ~= "string" or p == "" then return end
        for _, q in ipairs({ p, linkRealpath(p) or "" }) do
            if q:sub(1, 1) == "/" and not seen[q] then
                seen[q] = true
                table.insert(list, { label = label, path = q, id = linkId(q) })
            end
        end
    end
    pcall(function() add("KOReader data folder", DataStorage:getDataDir()) end)
    pcall(function() add("KOReader data folder", DataStorage:getFullDataDir()) end)
    pcall(function() add("KOReader folder", lfs.currentdir()) end)
    pcall(function() add("KOReader plugins folder", (implDir():match("^(.*)/[^/]+$"))) end)
    link_protected = list
    local names = {}
    for _, d in ipairs(list) do table.insert(names, d.label .. " " .. d.path) end
    logger.info("TomeSync: link refuses " .. table.concat(names, "; "))
    return list
end

-- nil when abs (absolute, under root; it need not exist yet) may be acted
-- on, else why. Every part below root passes linkPartRefused, and abs is
-- not one of KOReader's folders nor inside one: by name ignoring case, and
-- by identity for every existing folder on the way up to root (case,
-- symbolic links, a second mount of the same storage).
local function linkGuard(root, abs)
    if not linkInside(root, abs) then return "outside the library" end
    local why = linkPathRefused(linkRel(root, abs))
    if why then return why end
    for _, d in ipairs(linkProtected()) do
        if linkUnderNoCase(abs, d.path) then return "inside the " .. d.label end
        if d.id then
            local p = abs
            while #p >= #root do
                if linkId(p) == d.id then return "inside the " .. d.label end
                local up = linkParent(p)
                if up == p then break end
                p = up
            end
        end
    end
    return nil
end

-- A folder that holds one of KOReader's folders is never moved, renamed or
-- deleted.
local function linkHoldsProtected(src)
    for _, d in ipairs(linkProtected()) do
        if linkContains(src, d.path) then return "holds the " .. d.label end
    end
    return nil
end

-- The spelling on disk of a path under root: each part below root as its
-- folder lists it (FAT finds any case; realpath keeps the case it was given
-- on Linux, so a command's "foo.epub" would otherwise reach KOReader's
-- helpers instead of the "Foo.epub" they stored). Unchanged where nothing matches.
local function linkOnDisk(root, abs)
    if abs == root or not linkInside(root, abs) then return abs end
    local out = root
    for part in linkRel(root, abs):gmatch("[^/]+") do
        local found = nil
        if lfs.attributes(out .. "/" .. part) then
            local low = part:lower()
            pcall(function()
                for name in lfs.dir(out) do
                    if name == part then found = name; break end
                    if not found and name:lower() == low then found = name end
                end
            end)
        end
        out = out .. "/" .. (found or part)
    end
    return out
end

-- A server-given relative path, resolved inside root and checked by
-- linkGuard. must_exist: the path itself exists (checked by its realpath,
-- returned in its on-disk spelling); otherwise its parent must exist inside
-- root and the returned path is parent/name.
-- Returns abs, or nil, error, and "missing" when must_exist found nothing.
-- Never touches the disk beyond lstat/realpath/listing a folder.
local function linkResolve(root, rel, must_exist)
    if type(rel) ~= "string" or rel == "" then return nil, "missing path" end
    if rel:find("%z") or rel:sub(1, 1) == "/" or rel:find("\\", 1, true) then
        return nil, "path must be relative to the library"
    end
    rel = rel:gsub("/+", "/"):gsub("/$", "")
    if rel == "" then return nil, "missing path" end
    local why = linkPathRefused(rel)
    if why then return nil, why end
    local abs = root .. "/" .. rel
    if must_exist then
        if lfs.symlinkattributes(abs, "mode") == "link" then
            return nil, "symbolic links are not touched"
        end
        local real = linkRealpath(abs)
        if not real then return nil, "not found: " .. rel, "missing" end
        if not linkInside(root, real) or real == root then
            return nil, "outside the library: " .. rel
        end
        real = linkOnDisk(root, real)
        why = linkGuard(root, real)
        if why then return nil, why .. ": " .. rel end
        return real
    end
    local parent = linkRealpath(linkParent(abs))
    if not parent then return nil, "folder not found: " .. linkParent(rel) end
    if not linkInside(root, parent) then return nil, "outside the library: " .. rel end
    local out = linkOnDisk(root, parent) .. "/" .. abs:match("[^/]+$")
    why = linkGuard(root, out)
    if why then return nil, why .. ": " .. rel end
    return out
end

-- The book open in the reader, if any (its realpath).
local function linkOpenFile()
    local ok, ReaderUI = pcall(require, "apps/reader/readerui")
    local r = ok and ReaderUI.instance
    local f = r and r.document and r.document.file
    return f and (linkRealpath(f) or f) or nil
end

-- In the reader (a ReaderUI is up): the poll stays light there.
local function linkReading()
    local ok, ReaderUI = pcall(require, "apps/reader/readerui")
    return ok and ReaderUI.instance ~= nil
end

-- The paths ReadHistory and ReadCollection hold, as stored.
local function linkHistFiles()
    local out = {}
    pcall(function()
        for _, v in ipairs(require("readhistory").hist) do table.insert(out, v.file) end
    end)
    return out
end
local function linkCollFiles()
    local out = {}
    pcall(function()
        for _, coll in pairs(require("readcollection").coll) do
            for f in pairs(coll) do table.insert(out, f) end
        end
    end)
    return out
end

-- DocSettings.updateLocation, plus the sidecar's ".old" backups (e.g.
-- metadata.epub.lua.old): updateLocation writes a fresh sidecar at the new
-- place and purges the old folder, so the backup was lost on every move
-- (build 56). Each .old in the old sidecar folder (book-side and docsettings
-- location; the hash location does not move) is copied next to the new
-- sidecar, never over a file already there. (A field: the 200-local budget.)
function TomeSync._linkUpdateLocation(old, new)
    local DocSettings = require("docsettings")
    local keep = {}
    pcall(function()
        for _, loc in ipairs({ "doc", "dir" }) do
            local d = DocSettings:getSidecarDir(old, loc)
            if lfs.attributes(d, "mode") == "directory" then
                for name in lfs.dir(d) do
                    if name:match("%.old$") and lfs.attributes(d .. "/" .. name, "mode") == "file"
                            and (lfs.attributes(d .. "/" .. name, "size") or 0) <= 4 * 1024 * 1024 then
                        table.insert(keep, { loc = loc, name = name, data = readWhole(d .. "/" .. name) })
                    end
                end
            end
        end
    end)
    DocSettings.updateLocation(old, new)
    for _, k in ipairs(keep) do
        local d = DocSettings:getSidecarDir(new, k.loc)
        if k.data and lfs.attributes(d, "mode") == "directory" and not lfs.attributes(d .. "/" .. k.name) then
            if writeWhole(d .. "/" .. k.name, k.data) then
                logger.info("TomeSync: link kept the sidecar backup " .. k.name .. " (" .. k.loc .. " location)")
            end
        end
    end
end

-- KOReader's own bookkeeping after a move/rename, as the file manager's
-- renameFile does it (sidecar, history, collections) minus its dialogs.
-- History and collections are updated under the spelling they stored,
-- found ignoring case.
local function linkMoved(old, new, is_file)
    local DocSettings    = require("docsettings")
    local ReadHistory    = require("readhistory")
    local ReadCollection = require("readcollection")
    if is_file then
        pcall(TomeSync._linkUpdateLocation, old, new)
        for _, s in ipairs(linkStoredSpellings(linkHistFiles(), old, false)) do
            pcall(ReadHistory.updateItem, ReadHistory, s, new)
        end
        for _, s in ipairs(linkStoredSpellings(linkCollFiles(), old, false)) do
            pcall(ReadCollection.updateItem, ReadCollection, s, new)
        end
    else
        -- Trailing slashes: the helpers match by prefix, so "Foo" would also
        -- catch "Foobar/...".
        for _, s in ipairs(linkStoredSpellings(linkHistFiles(), old .. "/", true)) do
            pcall(ReadHistory.updateItemsByPath, ReadHistory, s, new .. "/")
        end
        for _, s in ipairs(linkStoredSpellings(linkCollFiles(), old .. "/", true)) do
            pcall(ReadCollection.updateItemsByPath, ReadCollection, s, new .. "/")
        end
    end
    pcall(function() require("ui/widget/booklist").resetBookInfoCache(old) end)
end

-- After a delete into the trash: the book (and its sidecar, moved along with
-- it) is kept, but history and collections treat it as deleted, like the
-- file manager's deleteFile.
local function linkTrashed(old, new, is_file)
    local DocSettings    = require("docsettings")
    local ReadHistory    = require("readhistory")
    local ReadCollection = require("readcollection")
    if is_file then
        pcall(TomeSync._linkUpdateLocation, old, new)
        for _, s in ipairs(linkStoredSpellings(linkHistFiles(), old, false)) do
            pcall(ReadHistory.fileDeleted, ReadHistory, s)
        end
        for _, s in ipairs(linkStoredSpellings(linkCollFiles(), old, false)) do
            pcall(ReadCollection.removeItem, ReadCollection, s)
        end
    else
        pcall(function()
            local prefix = (old .. "/"):lower()
            local hit = false
            for i = #ReadHistory.hist, 1, -1 do
                local f = ReadHistory.hist[i].file
                if type(f) == "string" and f:sub(1, #prefix):lower() == prefix then
                    ReadHistory:fileDeleted(i)
                    hit = true
                end
            end
            if hit then ReadHistory:_flush() end
        end)
        for _, s in ipairs(linkStoredSpellings(linkCollFiles(), old .. "/", true)) do
            pcall(ReadCollection.removeItemsByPath, ReadCollection, s)
        end
    end
    pcall(function() require("ui/widget/booklist").resetBookInfoCache(old) end)
end

-- Bytes under p (a file, or a folder walked without following links).
local function linkBytes(p)
    local a = lfs.symlinkattributes(p)
    if not a then return 0 end
    if a.mode ~= "directory" then return a.size or 0 end
    local n = 0
    pcall(function()
        for name in lfs.dir(p) do
            if name ~= "." and name ~= ".." then n = n + linkBytes(p .. "/" .. name) end
        end
    end)
    return n
end

-- TOMESYNC_LINKTRASH_BEGIN (pure: no requires, no I/O; extracted and run
-- under luajit by tests/test_tomesync_reader_link.py)
-- What the trash purge removes. items: { name, t (when the link trashed
-- it, else its mtime), bytes }, any order. First everything older than
-- `days` (0 or nil = never by age), then, oldest first, until the trash is
-- within max_items and max_bytes; `newest` (the item just trashed; else the
-- youngest) is never removed. Returns the items to remove, oldest first,
-- and how many of them went by age. (A field: the 200-local budget.)
function TomeSync._linkTrashPlan(items, newest, now, days, max_items, max_bytes)
    local list, total = {}, 0
    for _, it in ipairs(items) do
        table.insert(list, it)
        total = total + (it.bytes or 0)
    end
    if #list == 0 then return {}, 0 end
    table.sort(list, function(x, y)
        if x.t ~= y.t then return x.t < y.t end
        return x.name < y.name
    end)
    newest = newest or list[#list].name
    local cutoff = (type(days) == "number" and days > 0) and (now - days * 86400) or nil
    local out, by_age = {}, 0
    for _, it in ipairs(list) do
        local old = cutoff ~= nil and it.t < cutoff
        local over = (#list - #out > max_items) or total > max_bytes
        if not old and not over then break end
        if it.name ~= newest then
            table.insert(out, it)
            total = total - (it.bytes or 0)
            if old then by_age = by_age + 1 end
        end
    end
    return out, by_age
end
-- TOMESYNC_LINKTRASH_END

-- Keeps the trash within LINK_TRASH_MAX_ITEMS and LINK_TRASH_MAX_BYTES and,
-- since build 56, its age: items older than tomesync_link_trash_days (default
-- 30, 0 = never) go first. Age is when the link trashed them, else mtime.
-- Then the bound, oldest first. The newest item (the one just trashed, else
-- the youngest) is always kept. A book's sidecar goes with it. Runs after
-- every delete and on every full pass in the file manager; one log line per
-- run that finds anything in the trash.
local function linkTrimTrash(trash, newest)
    if lfs.attributes(trash, "mode") ~= "directory" then return end
    local DocSettings = require("docsettings")
    local st = linkStore()
    local times = st:readSetting("trash") or {}
    local items, total, present = {}, 0, {}
    local ok = pcall(function()
        for name in lfs.dir(trash) do
            if name ~= "." and name ~= ".." and not name:lower():match("%.sdr$") then
                local p = trash .. "/" .. name
                local a = lfs.symlinkattributes(p)
                if a then
                    local is_file = a.mode == "file"
                    local bytes = linkBytes(p)
                    if is_file then bytes = bytes + linkBytes(DocSettings:getSidecarDir(p, "doc")) end
                    total = total + bytes
                    present[name] = true
                    table.insert(items, { name = name, path = p, mode = a.mode, bytes = bytes,
                                          t = tonumber(times[name]) or a.modification or 0 })
                end
            end
        end
    end)
    if not ok then return end
    for name in pairs(times) do if not present[name] then times[name] = nil end end
    if #items == 0 then
        st:saveSetting("trash", times)
        st:flush()
        return
    end
    local days = G_reader_settings:readSetting("tomesync_link_trash_days")
    if type(days) ~= "number" or days < 0 then days = TomeSync.LINK_TRASH_DAYS end
    local before_n, before_b = #items, total
    local plan, by_age = TomeSync._linkTrashPlan(items, newest, os.time(), days,
                                                 LINK_TRASH_MAX_ITEMS, LINK_TRASH_MAX_BYTES)
    local purged, freed = {}, 0
    local ffiUtil = require("ffi/util")
    for _, it in ipairs(plan) do
        if it.mode == "file" then
            pcall(DocSettings.updateLocation, it.path)   -- its sidecar, every location
            os.remove(it.path)
            local sdr = DocSettings:getSidecarDir(it.path, "doc")
            if lfs.attributes(sdr, "mode") == "directory" then pcall(ffiUtil.purgeDir, sdr) end
        elseif it.mode == "directory" then
            pcall(ffiUtil.purgeDir, it.path)
        else
            os.remove(it.path)
        end
        freed = freed + it.bytes
        times[it.name] = nil
        table.insert(purged, it.name)
    end
    logger.info(string.format("TomeSync: link trash: %d items (%.1f MB), purged %d (%d by age, %s; "
        .. "%d over the bound of %d items / %d MB; %.1f MB)%s, kept %d", before_n, before_b / 1048576,
        #purged, by_age, (days > 0) and ("over " .. days .. " days") or "age off", #purged - by_age,
        LINK_TRASH_MAX_ITEMS, LINK_TRASH_MAX_BYTES / 1048576, freed / 1048576,
        (#purged > 0) and (": " .. table.concat(purged, ", ")) or "", before_n - #purged))
    st:saveSetting("trash", times)
    st:flush()
end

-- Repoint (or drop, new = nil) TomeSync's own path-keyed tables.
function TomeSync:_linkRemap(old, new, is_file)
    for _, key in ipairs({ "book_map", "meta_ledger" }) do
        local tbl = self[key]
        if type(tbl) == "table" then
            local moves = {}
            -- Ignoring case: the stored spelling may differ from the one on disk (FAT).
            local low = old:lower()
            for path, v in pairs(tbl) do
                if path:lower() == low then
                    moves[path] = { new, v }
                elseif not is_file and path:sub(1, #old + 1):lower() == low .. "/" then
                    moves[path] = { new and (new .. path:sub(#old + 1)) or nil, v }
                end
            end
            if next(moves) then
                for path, m in pairs(moves) do
                    tbl[path] = nil
                    if m[1] then tbl[m[1]] = m[2] end
                end
                self:_saveState("tomesync_" .. key, tbl)
            end
        end
    end
end

-- Refresh the file browser when it shows a folder a command touched (or the
-- folder it showed has gone, then go to the library root).
local function linkRefreshBrowser(root, touched)
    local ok, FileManager = pcall(require, "apps/filemanager/filemanager")
    local fm = ok and FileManager.instance
    local fc = fm and fm.file_chooser
    if not (fc and fc.path) then return end
    if lfs.attributes(fc.path, "mode") ~= "directory" then
        pcall(fc.changeToPath, fc, root)
        return
    end
    local shown = linkRealpath(fc.path) or fc.path
    if touched["*"] or touched[shown] then pcall(fc.refreshPath, fc) end
end

-- TOMESYNC_LINKCMD_BEGIN (pure: no requires, no I/O; extracted and run under
-- luajit by tests/test_tomesync_reader_link.py)
-- The commands a GET /tome-sync/commands reply really holds. Only a JSON
-- array (or {commands = [...]}) of {id, op, args} with a known op counts;
-- anything else is no commands at all, so it can never cause a result POST.
-- Released Tomes answer unknown GETs under /api with 200 + the web app's HTML,
-- which apiRequest hands back as {} (body not JSON): that is "nothing".
local LINK_OPS = { rename = true, move = true, delete = true, mkdir = true, refresh = true }
local function linkParseCommands(body)
    if type(body) ~= "table" then return {} end
    local list = body
    if type(body.commands) == "table" then list = body.commands end
    local out = {}
    for i, cmd in ipairs(list) do
        if i > 200 then break end
        if type(cmd) == "table"
                and (type(cmd.id) == "number" or (type(cmd.id) == "string" and cmd.id:match("^[%w%-_]+$")))
                and type(cmd.op) == "string" and LINK_OPS[cmd.op]
                and (cmd.args == nil or type(cmd.args) == "table") then
            table.insert(out, cmd)
        end
    end
    return out
end
-- TOMESYNC_LINKCMD_END

-- TOMESYNC_LINKPOLL_BEGIN (pure: no requires, no I/O; extracted and run under
-- luajit by tests/test_tomesync_reader_link.py)
-- poll_seconds from a decoded answer: a positive number, floored and kept in
-- 5..3600 (every request blocks KOReader's UI thread, so never more often
-- than every 5 s); anything else (absent, 0, negative, NaN, a string, JSON null,
-- a list) is 0 = do not poll.
local function linkPollSeconds(body)
    if type(body) ~= "table" then return 0 end
    local v = body.poll_seconds
    if type(v) ~= "number" or v ~= v or v <= 0 or v == math.huge then return 0 end
    v = math.floor(v)
    if v < 5 then v = 5 end
    if v > 3600 then v = 3600 end
    return v
end

-- The canonical tree listing the hash is taken of: one line per entry
-- (path, d/f, size, mtime, sidecar), sorted, so the same tree always gives
-- the same text whatever order the walk produced.
local function linkTreeCanon(entries, truncated)
    local lines = {}
    local function int(v) return type(v) == "number" and string.format("%d", math.floor(v)) or "" end
    for _, e in ipairs(entries) do
        lines[#lines + 1] = tostring(e.path) .. "\t" .. (e.type == "dir" and "d" or "f")
            .. "\t" .. int(e.size) .. "\t" .. int(e.mtime) .. "\t" .. (e.sdr and "1" or "")
    end
    table.sort(lines)
    if truncated then lines[#lines + 1] = "truncated" end
    return table.concat(lines, "\n")
end

-- Commands to run now and commands already run this session (by id, as a
-- string, so 7 and "7" are one id). A repeat is not run again; its stored
-- result is posted again. An id twice in one answer runs once.
local function linkSplitCommands(cmds, done)
    local fresh, again, seen = {}, {}, {}
    for _, c in ipairs(cmds) do
        local k = tostring(c.id)
        if done[k] ~= nil then
            if not seen[k] then table.insert(again, c) end
        elseif not seen[k] then
            table.insert(fresh, c)
        end
        seen[k] = true
    end
    return fresh, again
end
-- TOMESYNC_LINKPOLL_END

-- MD5 hex of a string with KOReader's bundled ffi/sha2; nil when missing
-- (then the tree is sent as before, on full passes only).
local function linkHash(str)
    local ok, sha2 = pcall(require, "ffi/sha2")
    if not ok or type(sha2) ~= "table" or type(sha2.md5) ~= "function" then return nil end
    local ok2, h = pcall(sha2.md5, str)
    return ok2 and type(h) == "string" and h or nil
end

local function linkRemember(key, result)
    if link_done[key] == nil then table.insert(link_done_order, key) end
    link_done[key] = result
    while #link_done_order > LINK_DONE_KEEP do
        link_done[table.remove(link_done_order, 1)] = nil
    end
    -- Written through before the result is posted: a kill between the two
    -- posts this result again after the restart instead of running it again.
    local ok, e = pcall(function()
        local list = {}
        for _, k in ipairs(link_done_order) do table.insert(list, { id = k, result = link_done[k] }) end
        local st = linkStore()
        st:saveSetting("done", list)
        st:flush()
    end)
    if not ok then logger.warn("TomeSync: link could not save the command results:", e) end
end

-- One command. Returns ok, error, new_path (relative), note. Raises nothing
-- the caller does not pcall. Every path and name goes through linkResolve /
-- linkGuard (tools/reader-link-routes.md, "Refused paths").
function TomeSync:_linkRunCommand(root, cmd, touched)
    local op   = cmd.op
    local args = type(cmd.args) == "table" and cmd.args or {}
    local function str(v) return type(v) == "string" and v or nil end

    if op == "refresh" then
        touched["*"] = true
        return true
    end

    if op == "mkdir" then
        local rel = str(args.path)
        if not rel then return false, "missing path" end
        -- The whole path by name first, so no level is made before a refusal.
        local why = linkPathRefused(rel)
        if why then return false, why end
        -- One level at a time, so every new folder's parent is checked.
        local built = ""
        for part in rel:gmatch("[^/]+") do
            built = (built == "") and part or (built .. "/" .. part)
            local abs, err = linkResolve(root, built, false)
            if not abs then return false, err end
            local mode = lfs.attributes(abs, "mode")
            if mode == nil then
                local ok, e = lfs.mkdir(abs)
                if not ok then return false, "could not create " .. built .. ": " .. tostring(e) end
                touched[linkParent(abs)] = true
            elseif mode ~= "directory" then
                return false, "a file is in the way: " .. built
            end
        end
        if built == "" then return false, "missing path" end
        return true, nil, built
    end

    if op ~= "rename" and op ~= "move" and op ~= "delete" then
        return false, "unknown op: " .. tostring(op)
    end

    local new_name
    if op == "rename" then
        new_name = str(args.new_name)
        if not new_name or new_name == "" or new_name:find("/", 1, true)
                or new_name:find("\\", 1, true) or new_name:find("%z") then
            return false, "bad new_name"
        end
        local why = linkPartRefused(new_name)
        if why then return false, "new_name: " .. why end
    end
    local to = (op == "move") and str(args.to_dir) or nil
    if op == "move" and to == nil then return false, "missing to_dir" end
    local function destDir()
        if to == "" or to == "/" then return root end
        local dir, e = linkResolve(root, to, true)
        if not dir then return nil, e end
        if lfs.attributes(dir, "mode") ~= "directory" then return nil, "not a folder: " .. to end
        return dir
    end

    local src, err, missing = linkResolve(root, str(args.path), true)
    if not src then
        -- Ran already (its result was lost, or it was done by hand): the
        -- source is gone and the destination is there under the expected name.
        if missing and op ~= "delete" then
            local rel = (str(args.path):gsub("/+", "/"):gsub("/$", ""))
            local name = rel:match("[^/]+$")
            local dest
            if op == "rename" then
                local parent_rel = rel:match("^(.*)/[^/]+$")
                dest = linkResolve(root, (parent_rel and (parent_rel .. "/") or "") .. new_name, true)
            else
                local dir = destDir()
                if dir and name then
                    local drel = linkRel(root, dir)
                    dest = linkResolve(root, (drel == "") and name or (drel .. "/" .. name), true)
                end
            end
            if dest then
                logger.info("TomeSync: link", op, rel, "already done:", dest)
                return true, nil, linkRel(root, dest),
                       "already done: the source is gone and the destination exists"
            end
        end
        return false, err
    end
    if linkOpenFile() and linkContains(src, linkOpenFile()) then
        return false, "open in the reader"
    end
    local mode = lfs.symlinkattributes(src, "mode")
    if mode ~= "file" and mode ~= "directory" then return false, "not a regular file or folder" end
    local is_file = (mode == "file")
    if not is_file then
        local why = linkHoldsProtected(src)
        if why then return false, why .. ": " .. linkRel(root, src) end
    end
    local trash = root .. "/" .. LINK_TRASH
    if linkContains(trash, src) then return false, "path is inside the trash" end
    local base = src:match("[^/]+$")

    local dest
    if op == "rename" then
        dest = linkParent(src) .. "/" .. new_name
    elseif op == "move" then
        local dir, e = destDir()
        if not dir then return false, e end
        if not is_file and linkContains(src, dir) then
            return false, "cannot move a folder into itself"
        end
        dest = dir .. "/" .. base
    else -- delete: into <root>/.tomesync-trash, bounded by linkTrimTrash
        if lfs.attributes(trash, "mode") ~= "directory" then
            local ok, e = lfs.mkdir(trash)
            if not ok then return false, "could not create the trash folder: " .. tostring(e) end
        end
        local stem, ext = base, ""
        if is_file then
            local s, x = base:match("^(.+)(%.[^.]+)$")
            if s then stem, ext = s, x end
        end
        dest = trash .. "/" .. base
        local n = 1
        -- Keep every deleted copy; a numbered name also gets its own sidecar.
        while lfs.attributes(dest)
                or (is_file and lfs.attributes(require("docsettings"):getSidecarDir(dest, "doc"))) do
            n = n + 1
            dest = string.format("%s/%s (%d)%s", trash, stem, n, ext)
        end
    end
    if op ~= "delete" then
        local why = linkGuard(root, dest)
        if why then return false, why .. ": " .. linkRel(root, dest) end
    end

    if dest == src then return true, nil, linkRel(root, src) end
    local case_only = false
    if lfs.attributes(dest) then
        if not linkSame(src, dest) then
            return false, "already exists: " .. linkRel(root, dest)
        elseif op == "rename" and linkCaseOnly(base, new_name) then
            case_only = true
        else
            return true, nil, linkRel(root, linkOnDisk(root, dest)), "already there"
        end
    end

    if case_only then
        -- One file under both names: through a temporary name, so the
        -- filesystem (and DocSettings.updateLocation, which would write the
        -- new sidecar into the old one and then purge it) sees two names.
        local tmp
        for n = 1, 50 do
            tmp = linkParent(src) .. "/.tomesync-case-" .. n .. "-" .. base
            if not lfs.attributes(tmp) then break end
            tmp = nil
        end
        if not tmp then return false, "no free temporary name for a case-only rename" end
        local ok, e = os.rename(src, tmp)
        if not ok then return false, "could not rename: " .. tostring(e) end
        linkMoved(src, tmp, is_file)
        ok, e = os.rename(tmp, dest)
        if not ok then
            if os.rename(tmp, src) then linkMoved(tmp, src, is_file) end
            return false, "could not rename: " .. tostring(e)
        end
        linkMoved(tmp, dest, is_file)
        self:_linkRemap(src, dest, is_file)
        logger.info("TomeSync: link rename (case only, via " .. tmp:match("[^/]+$") .. ")", src, "->", dest)
    else
        local ok, e = os.rename(src, dest)
        if not ok then return false, "could not move: " .. tostring(e) end
        if op == "delete" then
            linkTrashed(src, dest, is_file)
            self:_linkRemap(src, nil, is_file)
            pcall(function()
                local st = linkStore()
                local times = st:readSetting("trash") or {}
                times[dest:match("[^/]+$")] = os.time()
                st:saveSetting("trash", times)
            end)
            local ok2, e2 = pcall(linkTrimTrash, trash, dest:match("[^/]+$"))
            if not ok2 then logger.warn("TomeSync: link trash trim failed:", e2) end
        else
            linkMoved(src, dest, is_file)
            self:_linkRemap(src, dest, is_file)
        end
        logger.info("TomeSync: link", op, src, "->", dest)
    end
    touched[linkParent(src)] = true
    touched[linkParent(dest)] = true
    return true, nil, linkRel(root, dest)
end

-- The file tree, when the server does not already have it: hash the
-- canonical listing, ask GET /tome-sync/files/status?hash=, PUT only on
-- have ~= true. A 404/405 on status turns the check off; the tree is then
-- PUT on full passes (and after commands), unconditionally.
local function linkSendTree(root, light)
    local t0 = os.clock()
    local entries, truncated = linkFileTree(root)
    link_tree_at = os.time()
    local hash = linkHash(linkTreeCanon(entries, truncated))
    local ms = math.floor((os.clock() - t0) * 1000 + 0.5)
    local net = light and LINK_NET_LIGHT or nil
    if hash and not link_off.files_status then
        local st, code = apiRequest("GET", "/tome-sync/files/status?hash=" .. hash, nil, net)
        if linkRouteOff(code) then
            link_off.files_status = true
            logger.info("TomeSync: link files/status", code, "- tree check off")
        elseif type(code) ~= "number" then
            return       -- no answer: next pass
        elseif type(st) == "table" and st.have == true then
            link_tree_dirty = false
            logger.info("TomeSync: link files/status have=true (" .. #entries .. " entries, hash "
                        .. hash .. ", " .. ms .. " ms)")
            return
        else
            logger.info("TomeSync: link files/status have=false (hash " .. hash .. ")")
        end
    end
    if light and not link_tree_dirty and (not hash or link_off.files_status) then return end
    local _, code = apiRequest("PUT", "/tome-sync/files", {
        device    = deviceName(),
        root      = root,
        truncated = truncated,
        count     = #entries,
        hash      = hash,
        entries   = rapidjson.array and rapidjson.array(entries) or entries,
    }, light and LINK_NET_TREE or nil)
    logger.info("TomeSync: link PUT files", tostring(code), "(" .. #entries .. " entries, hash "
                .. tostring(hash) .. ", " .. ms .. " ms)")
    if linkRouteOff(code) then
        link_off.files = true
    elseif type(code) == "number" and code >= 200 and code < 300 then
        link_tree_dirty = false
    end
end

-- Link hosts (build 55): the "join the phone's hotspot and the reader finds
-- it" behaviour. config.lua's LINK_HOSTS lists the phone's addresses (the
-- one it had at install, and 172.20.10.1, the iPhone's own address on its
-- Personal Hotspot). On a full pass in the file manager, when SERVER_URL
-- did not answer, or is one of LINK_HOSTS anyway, each host is asked
-- device-info with a 2 s connect timeout and the pinned certificate; the
-- first that answers 200 is used for this session (LinkSession.url, see
-- TOMESYNC_NET). Never without a pinned fingerprint: an unpinned probe would
-- send the key to whatever answers at that address. A host that missed is
-- not asked again for LINK_HOST_BACKOFF seconds; after a pass where none
-- answered, one more pass follows after the back-off, up to
-- LINK_HOST_RETRIES times (reset when the network comes up again).
local LINK_HOST_BACKOFF = 60
local LINK_HOST_RETRIES = 10
local link_host_failed  = {}      -- host -> os.time() of its last miss
local link_host_retries = LINK_HOST_RETRIES
local link_host_missed  = false   -- the last pass probed and found none
local link_host_nopin   = false   -- "no pin" logged once

local function linkHostListed(url)
    for _, h in ipairs(PLUGIN_CONFIG.LINK_HOSTS or {}) do
        if h == url then return true end
    end
    return false
end

-- The first LINK_HOSTS entry that answers device-info 200 becomes this
-- session's address; returns that answer, or nil and why.
local function linkProbeHosts(root)
    local hosts = PLUGIN_CONFIG.LINK_HOSTS
    if not hosts then return nil, "no link hosts" end
    if not pinnedFingerprint() then
        if not link_host_nopin then
            link_host_nopin = true
            logger.info("TomeSync: link hosts set but no pinned fingerprint: not probing")
        end
        return nil, "no pinned fingerprint"
    end
    local body = rapidjson.encode(linkDeviceInfo(root))
    for _, host in ipairs(hosts) do
        local last = link_host_failed[host]
        if last and os.time() - last < LINK_HOST_BACKOFF then
            logger.info("TomeSync: link host " .. host .. " backing off ("
                        .. (LINK_HOST_BACKOFF - (os.time() - last)) .. " s left)")
        else
            local chunks = {}
            socketutil:set_timeout(2, 4)
            local ok, code = http.request({
                url     = host .. "/api/tome-sync/device-info",
                method  = "PUT",
                headers = {
                    ["Authorization"]  = "Bearer " .. apiKey(),
                    ["Content-Type"]   = "application/json",
                    ["Accept"]         = "application/json",
                    ["Content-Length"] = tostring(#body),
                },
                source  = ltn12.source.string(body),
                sink    = cappedTableSink(chunks),
                create  = tlsCreateFor(host),
            })
            socketutil:reset_timeout()
            if ok and code == 200 then
                local dok, parsed = pcall(rapidjson.decode, table.concat(chunks))
                LinkSession.url = host
                link_host_failed[host] = nil
                link_host_retries = LINK_HOST_RETRIES
                resetBackoff()
                logger.info("TomeSync: reader link via " .. host)
                return (dok and type(parsed) == "table") and parsed or {}
            end
            link_host_failed[host] = os.time()
            logger.info("TomeSync: link host " .. host
                        .. (ok and (" answered " .. tostring(code)) or (" no answer (" .. tostring(code) .. ")"))
                        .. ", not asked again for " .. LINK_HOST_BACKOFF .. " s")
        end
    end
    return nil, "no link host answered"
end

-- The pass: device info, then the command queue, then the inbox (when
-- auto-pickup is on), then the file tree, so the tree the server keeps
-- already shows what the commands and deliveries changed.
-- light (the poll): device-info only when 60 s old, no inbox, the tree only
-- after a command or every 30 s, 2 s timeouts.
-- In the reader (ReaderUI up) no tree check at all, and a light pass is the
-- commands GET only: the reader's page turns must not wait on a tree walk.
-- Each pass logs how long it took.
-- Returns the poll_seconds of the last link answer (0 = do not poll) and,
-- when 0, why (for the log).
function TomeSync:_linkPass(light)
    -- The trash's age purge: local, so before the network check; full
    -- passes in the file manager only (never during page turns).
    if not light and not linkReading() and linkRoot() then
        local ok, e = pcall(linkTrimTrash, linkRoot() .. "/" .. LINK_TRASH, nil)
        if not ok then logger.warn("TomeSync: link trash purge failed:", e) end
    end
    if not NetworkMgr:isConnected() then return 0, "offline" end
    local t0 = linkNow()
    local reading = linkReading()
    local root = linkRoot()
    linkStore()   -- the results kept from before a restart, before any command
    local link_on = not link_off.all
    local net = light and LINK_NET_LIGHT or nil
    local poll = 0
    local ran = 0
    local nowhy = nil
    -- Link hosts: full passes in the file manager, while no host is in use.
    local probe = not light and not reading and PLUGIN_CONFIG.LINK_HOSTS ~= nil
                  and LinkSession.url == nil and not link_off.all
    local probed, missed = nil, false
    link_host_missed = false
    if probe and linkHostListed(primaryURL()) then
        -- SERVER_URL is one of them: ask them all, it included, and not
        -- the configured server a second time.
        probe = false
        probed, nowhy = linkProbeHosts(root)
        if not probed then link_on, missed = false, true end
    end
    if link_on and (not light or (not reading and os.time() - link_info_at >= LINK_INFO_EVERY)) then
        local body, code
        if probed then
            body, code = probed, 200
        else
            body, code = apiRequest("PUT", "/tome-sync/device-info", linkDeviceInfo(root), net)
            if probe and type(code) ~= "number" then
                -- The configured server did not answer: try the phone.
                probed, nowhy = linkProbeHosts(root)
                if probed then body, code = probed, 200 else missed = true end
            end
        end
        if linkRouteOff(code) then
            link_off.all = true
            link_on = false
            logger.info("TomeSync: server has no reader link (device-info", code, ")")
        elseif type(code) ~= "number" then
            link_on = false   -- offline / unreachable / backing off: next moment
            nowhy = "no answer (" .. tostring(code) .. ")"
        else
            link_info_at = os.time()
            poll = linkPollSeconds(body)
        end
    end

    if link_on and root and not link_off.commands then
        local list, code = apiRequest("GET", "/tome-sync/commands", nil, net)
        if linkRouteOff(code) then
            link_off.commands = true
        elseif type(code) ~= "number" then
            link_on, poll = false, 0
            nowhy = "no answer (" .. tostring(code) .. ")"
        elseif code >= 200 and code < 300 then
            poll = linkPollSeconds(list)
            local fresh, again = linkSplitCommands(linkParseCommands(list), link_done)
            for _, cmd in ipairs(again) do
                local key = tostring(cmd.id)
                logger.info("TomeSync: link command", key, tostring(cmd.op),
                            "already run, posting its result again")
                pcall(apiRequest, "POST", "/tome-sync/commands/" .. urlEncode(key) .. "/result",
                      link_done[key], net)
            end
            local touched = {}
            for _, cmd in ipairs(fresh) do
                if not link_off.all then
                    local key = tostring(cmd.id)
                    local ok, res, err, new_path, note = pcall(self._linkRunCommand, self, root, cmd, touched)
                    if not ok then res, err, new_path, note = false, tostring(res), nil, nil end
                    ran = ran + 1
                    logger.info("TomeSync: link command", key, tostring(cmd.op),
                                res and ("ok" .. (note and (" (" .. note .. ")") or ""))
                                or ("failed: " .. tostring(err)))
                    local result = {
                        ok = res and true or false,
                        error = (not res) and (err or "failed") or nil,
                        new_path = res and new_path or nil,
                        note = note,
                    }
                    linkRemember(key, result)
                    pcall(apiRequest, "POST", "/tome-sync/commands/" .. urlEncode(key) .. "/result",
                          result, net)
                end
            end
            if ran > 0 then
                link_tree_dirty = true
                linkRefreshBrowser(root, touched)
            end
        else
            poll = 0
        end
    end

    link_host_missed = missed
    if autoDeliver() and not light and not missed then
        pcall(function() self:_refreshInbox() end)
        local items = self.inbox_items
        if self.inbox_enabled and type(items) == "table" and #items > 0 then
            self:_deliverInbox(items, true)
        end
    end

    if link_on and root and not reading and not link_off.files and not link_off.all
            and (not light or link_tree_dirty or os.time() - link_tree_at >= LINK_TREE_EVERY) then
        linkSendTree(root, light)
    end
    local ms = math.floor((linkNow() - t0) * 1000 + 0.5)
    if light then
        logger.info("TomeSync: link poll pass (" .. ran .. " command(s), next in " .. poll .. " s"
                    .. ((poll == 0 and nowhy) and (", " .. nowhy) or "") .. ", "
                    .. (reading and "reader" or "file manager") .. ", " .. ms .. " ms)")
    else
        logger.info("TomeSync: link pass took " .. ms .. " ms (" .. ran .. " command(s), "
                    .. (reading and "reader" or "file manager") .. ")")
    end
    if link_off.all then return 0, "server has no link" end
    return poll, (poll == 0) and (nowhy or "server did not ask") or nil
end

local function linkStopPoll(why)
    if link_poll_task then
        UIManager:unschedule(link_poll_task)
        link_poll_task = nil
    end
    if link_poll_secs > 0 then
        logger.info("TomeSync: link poll stopped (" .. why .. ")")
    end
    link_poll_secs = 0
end

-- (Re)arm the light pass secs from now, or stop polling (secs <= 0). Not
-- while a full pass is pending: that pass arms it when it has run.
function TomeSync:_linkArmPoll(secs, why)
    if link_poll_task then
        UIManager:unschedule(link_poll_task)
        link_poll_task = nil
    end
    if type(secs) ~= "number" or secs <= 0 or link_off.all or not NetworkMgr:isConnected() then
        linkStopPoll((link_off.all and "server has no link")
                     or ((not NetworkMgr:isConnected()) and "offline")
                     or why or "server did not ask")
        return
    end
    if link_task then return end
    if link_poll_secs ~= secs then
        logger.info("TomeSync: link poll every " .. secs .. " s")
    end
    link_poll_secs = secs
    link_poll_task = function()
        link_poll_task = nil
        local me = link_owner or self
        local ok, next_secs, why = pcall(me._linkPass, me, true)
        if not ok then
            logger.warn("TomeSync: link poll pass failed:", next_secs)
            next_secs, why = 0, "error"
        end
        me:_linkArmPoll(next_secs, why)
    end
    UIManager:scheduleIn(secs, link_poll_task)
end

-- Ask for a pass `delay` seconds from now (debounced: several moments close
-- together make one pass). Deferred like every background call here, so the
-- moment that asked never waits on the network. A pending light pass is
-- dropped: the full pass re-arms the poll from its own answer.
function TomeSync:_scheduleLinkSync(reason, delay)
    if link_off.all and not autoDeliver() then return end
    link_reasons[reason] = true
    if link_task then UIManager:unschedule(link_task) end
    if link_poll_task then
        UIManager:unschedule(link_poll_task)
        link_poll_task = nil
    end
    link_task = function()
        link_task = nil
        local why = {}
        for k in pairs(link_reasons) do table.insert(why, k) end
        link_reasons = {}
        local me = link_owner or self
        logger.info("TomeSync: link pass (" .. table.concat(why, ", ") .. ")")
        local ok, secs, why = pcall(me._linkPass, me)
        if not ok then
            logger.warn("TomeSync: link pass failed:", secs)
            secs, why = 0, "error"
        end
        if type(secs) ~= "number" or secs <= 0 then
            logger.info("TomeSync: link pass done, no poll (" .. tostring(why) .. ")")
        end
        me:_linkArmPoll(secs, why)
        -- No link host answered: one more pass once their back-off is over.
        if link_host_missed and not link_task then
            if link_host_retries > 0 then
                link_host_retries = link_host_retries - 1
                logger.info("TomeSync: no link host answered; trying again in "
                            .. (LINK_HOST_BACKOFF + 1) .. " s (" .. link_host_retries .. " retries left)")
                me:_scheduleLinkSync("link-retry", LINK_HOST_BACKOFF + 1)
            else
                logger.info("TomeSync: no link host answered; no retries left until the network comes up again")
            end
        end
    end
    UIManager:scheduleIn(delay or 3, link_task)
end

-- The network came up (again): maybe on the phone's hotspot now. The host in
-- use and the per-host back-off are forgotten, and the retries start over.
function TomeSync:_linkNetworkUp()
    if LinkSession.url then
        logger.info("TomeSync: network up: forgetting reader link via " .. LinkSession.url)
    end
    LinkSession.url = nil
    link_host_failed = {}
    link_host_retries = LINK_HOST_RETRIES
end

-- WiFi gone: no poll until a pass sees the server again (WiFi up runs one).
-- KOReader has no standby event; standby is only entered with WiFi off,
-- so this also covers it.
function TomeSync:onNetworkDisconnected()
    linkStopPoll("network down")
    LinkSession.url = nil
end

-- Called from init: the newest instance runs the passes (a closing reader's
-- pass runs on the file manager that replaces it), plus the launch pass.
function TomeSync:_linkInit()
    link_owner = self
    if not link_launched then
        link_launched = true
        self:_scheduleLinkSync("launch", 16)
    end
end

-- A download's final path, checked before any folder is made for it.
-- Always: no "." / ".." part, a plain extension, no runnable name
-- (.koplugin/.lua/.sh). guarded (inbox deliveries, which a server decides,
-- auto-pickup included): the link's whole rule, linkGuard, under base_dir.
-- Returns nil or why.
local function linkDownloadRefused(base_dir, dest, ext, guarded)
    if type(ext) ~= "string" or #ext > 16 or not ext:match("^%w[%w%.]*$") or ext:find("..", 1, true) then
        return "bad format: " .. tostring(ext)
    end
    if type(base_dir) ~= "string" or type(dest) ~= "string"
            or dest:sub(1, #base_dir + 1) ~= base_dir .. "/" then
        return "outside the download folder"
    end
    local rel = dest:sub(#base_dir + 2)
    for part in rel:gmatch("[^/]+") do
        if part == "." or part == ".." then return "path may not contain . or .." end
        local low = part:lower()
        for _, sfx in ipairs(LINK_CODE_SUFFIXES) do
            if low:sub(-#sfx) == sfx then return "refused name (" .. sfx .. "): " .. part end
        end
    end
    if not guarded then return nil end
    local base_real = linkRealpath(base_dir) or base_dir
    return linkGuard(base_real, base_real .. "/" .. rel)
end

-- A delivery's path with every part below base_dir cleaned of leading dots
-- (linkSafeName). Returns the path and true when something changed, logged
-- once per book per process. (Fields, not locals: the 200-local budget.)
TomeSync._link_named = {}
function TomeSync._linkSafeDest(base_dir, dest, ext, book_id)
    if type(base_dir) ~= "string" or type(dest) ~= "string"
            or dest:sub(1, #base_dir + 1) ~= base_dir .. "/" then
        return dest, false
    end
    local parts = {}
    for part in dest:sub(#base_dir + 2):gmatch("[^/]+") do table.insert(parts, part) end
    local fallback = tostring(book_id or "book")
    local changed = false
    for i, part in ipairs(parts) do
        local safe = linkSafeName(part, fallback, (i == #parts) and ext or nil)
        if safe ~= part then parts[i], changed = safe, true end
    end
    if not changed then return dest, false end
    local out = base_dir .. "/" .. table.concat(parts, "/")
    if not TomeSync._link_named[fallback] then
        TomeSync._link_named[fallback] = true
        logger.info("TomeSync: delivery of book " .. fallback .. ": a name started with a dot, saved as "
                    .. out:sub(#base_dir + 2) .. " (was " .. dest:sub(#base_dir + 2) .. ")")
    end
    return out, true
end
TomeSync._linkAutoDeliver = autoDeliver
TomeSync._linkStopPoll = linkStopPoll
TomeSync._linkDownloadRefused = linkDownloadRefused
end
-- Edit the custom download-path template in an input dialog. Validates by
-- rendering a sample book before saving, so an unknown token is rejected here
-- rather than silently falling back to the default layout per download.
function TomeSync:_editDownloadTemplate()
    local current = G_reader_settings:readSetting("tomesync_download_template") or ""
    local dialog
    dialog = InputDialog:new{
        title       = "Download path template",
        input       = current ~= "" and current
                      or "{book_type}/{series}/{volume:00} - {title}",
        description = "Tokens: {book_type} {series} {volume} {volume:00} "
                      .. "{title} {author} \u{2014} wrap in {Lower(...)} or "
                      .. "{Upper(...)} to force case.\n"
                      .. "\"/\" starts a new folder. The file extension is "
                      .. "appended automatically.\n"
                      .. "Leave empty to restore the default layout.",
        buttons     = {{
            {
                text     = "Cancel",
                id       = "close",
                callback = function() UIManager:close(dialog) end,
            },
            {
                text             = "Save",
                is_enter_default = true,
                callback         = function()
                    local tpl = dialog:getInputText()
                    if tpl == "" then
                        G_reader_settings:delSetting("tomesync_download_template")
                        UIManager:close(dialog)
                        return
                    end
                    local sample = renderDownloadPath(tpl, {
                        book_type = "novels", series = "Sample Series",
                        volume = 3, title = "Sample Title", author = "Sample Author",
                    })
                    if not sample then
                        UIManager:show(InfoMessage:new{
                            text = "Template is invalid (unknown token or "
                                   .. "renders empty) \u{2014} not saved.",
                            timeout = 4,
                        })
                        return
                    end
                    G_reader_settings:saveSetting("tomesync_download_template", tpl)
                    UIManager:close(dialog)
                    UIManager:show(InfoMessage:new{
                        text    = "Downloads will be saved as:\n" .. sample .. ".epub",
                        timeout = 5,
                    })
                end,
            },
        }},
    }
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

function TomeSync:_menuItems()
    local in_book = self.ui and self.ui.document
    -- The menu opening is a user-visible moment: a certificate a background
    -- request found not trusted yet is asked about (once per certificate per
    -- process, no network here), after the menu is drawn.
    UIManager:nextTick(function() TLS.trustThen(nil, { no_network = true, once = true }) end)

    -- Settings submenu: persistent toggles and diagnostics, set once and
    -- forgotten — kept out of the top level so frequent actions stay reachable.
    local settings_items = {}
    table.insert(settings_items, {
        text         = "Auto-connect WiFi when needed",
        help_text    = "When a TomeSync action needs the server and WiFi is down, "
                       .. "let KOReader re-establish the connection instead of "
                       .. "failing with \"offline\". Helps devices that "
                       .. "aggressively sleep WiFi (e.g. PocketBook).",
        checked_func = function() return G_reader_settings:isTrue("tomesync_auto_connect") end,
        callback     = function()
            G_reader_settings:saveSetting("tomesync_auto_connect",
                not G_reader_settings:isTrue("tomesync_auto_connect"))
        end,
    })
    -- Position pull strategy (like stock kosync): what to do when the server
    -- position differs from this device's on book open.
    local function pullModeItems(key, default)
        local function current()
            return G_reader_settings:readSetting(key) or default
        end
        local items = {}
        for _, m in ipairs({
            { "prompt", "Ask before jumping" },
            { "silent", "Jump automatically" },
            { "never",  "Do nothing" },
        }) do
            local value, label = m[1], m[2]
            table.insert(items, {
                text         = label,
                checked_func = function() return current() == value end,
                callback     = function()
                    if value == default then
                        G_reader_settings:delSetting(key)
                    else
                        G_reader_settings:saveSetting(key, value)
                    end
                end,
            })
        end
        return items
    end
    table.insert(settings_items, {
        text           = "Server position is ahead",
        help_text      = "What to do on book open when the server position is "
                       .. "further along than this device (you read elsewhere).",
        sub_item_table = pullModeItems("tomesync_pull_forward", "silent"),
    })
    table.insert(settings_items, {
        text           = "Server position is behind",
        help_text      = "What to do on book open when the server position is "
                       .. "earlier than this device (e.g. re-reading a section "
                       .. "on another device).",
        sub_item_table = pullModeItems("tomesync_pull_backward", "never"),
    })
    local function currentTemplate()
        return G_reader_settings:readSetting("tomesync_download_template") or ""
    end
    table.insert(settings_items, {
        text           = "Download location & naming",
        separator      = true,
        sub_item_table = {
            {
                text         = "Default (type and series folders)",
                help_text    = "Downloads land under the home folder as "
                               .. "book-type/series/Vol. N \u{2014} Title; "
                               .. "standalones go under their author instead "
                               .. "of a series folder.",
                checked_func = function() return currentTemplate() == "" end,
                callback     = function()
                    G_reader_settings:delSetting("tomesync_download_template")
                end,
            },
            {
                text         = "Flat in home folder",
                help_text    = "Every download lands directly in the home "
                               .. "folder, named \"Series - NN - Title\". "
                               .. "Series and volume are dropped for books "
                               .. "that have none.",
                checked_func = function() return currentTemplate() == FLAT_TEMPLATE end,
                callback     = function()
                    G_reader_settings:saveSetting("tomesync_download_template", FLAT_TEMPLATE)
                end,
            },
            {
                text         = "Custom template",
                help_text    = "Build the path from tokens: {book_type} "
                               .. "{series} {volume} {volume:00} {title} "
                               .. "{author}, wrapped in {Lower(...)} or "
                               .. "{Upper(...)} to force case. \"/\" starts a "
                               .. "new folder; the file extension is appended "
                               .. "automatically.",
                checked_func = function()
                    local t = currentTemplate()
                    return t ~= "" and t ~= FLAT_TEMPLATE
                end,
                callback     = function()
                    self:_editDownloadTemplate()
                end,
            },
        },
    })

    local sub_items = {}

    -- Always-visible items
    table.insert(sub_items, {
        text     = "Browse series",
        callback = function() self:_browseSeriesMenu() end,
    })
    table.insert(sub_items, {
        text     = "Sync reading history",
        callback = function()
            whenConnected(function() self:_syncReadingStats(true) end)
        end,
    })
    table.insert(sub_items, {
        text      = "Sync closed books",
        help_text = "Adopt status, rating and progress from books on this "
                  .. "device that TomeSync has never synced (read before Tome, "
                  .. "sideloaded, or opened under another launcher). Only fills "
                  .. "what Tome doesn't already have.",
        callback  = function() self:_sweepLibrary() end,
    })
    table.insert(sub_items, {
        text      = "Apply Tome metadata now",
        help_text = "Write Tome's title, author, series, tags, description and "
                  .. "cover for the books on this device into KOReader's custom "
                  .. "metadata. Only books Tome can verify by file hash are "
                  .. "touched; the book files themselves are never modified. "
                  .. "Runs regardless of the automatic setting. Needs "
                  .. "KOReader " .. META_MIN_KOREADER .. " or newer.",
        enabled_func = metaSyncSupported,
        callback  = function() self:_syncMetadata(true) end,
    })
    -- Inbox: only shown when the server has Send-to-KOReader enabled (set by the
    -- launch poll). Badge shows the pending count.
    if self.inbox_enabled then
        table.insert(sub_items, {
            text_func = function() return string.format("Inbox (%d)", self.inbox_count or 0) end,
            callback  = function() self:_inboxMenu() end,
        })
    end
    table.insert(settings_items, {
        text     = "Test connection",
        callback = function()
            whenConnected(function()
                local ok, result, code = pcall(apiRequest, "GET", "/health")
                if ok and type(code) == "number" and code >= 200 and code < 300 then
                    UIManager:show(InfoMessage:new{
                        text = "Connected to " .. serverURL()
                               .. "\nUser: " .. username(),
                        timeout = 4,
                    })
                else
                    local err = tostring(result or "unknown error")
                    UIManager:show(InfoMessage:new{
                        text = "Connection failed!\n" .. serverURL()
                               .. "\nError: " .. err,
                        timeout = 6,
                    })
                end
            end)
        end,
    })
    table.insert(settings_items, {
        text     = "Re-resolve all books",
        separator = true,
        callback = function()
            self.book_map = {}
            self.book_id = nil
            self:_saveState("tomesync_book_map", {})
            UIManager:show(InfoMessage:new{
                text = "All book mappings cleared.\nRe-open a book to re-resolve.",
                timeout = 3,
            })
        end,
    })
    table.insert(settings_items, {
        text     = "Check for updates",
        callback = function()
            whenConnected(function()
                self:checkForUpdate(function(avail, err)
                    if avail then
                        self:_promptUpdate(avail)
                    elseif avail == false then
                        UIManager:show(InfoMessage:new{
                            text = "TomeSync is up to date (build " .. BUILD .. ").",
                            timeout = 4,
                        })
                    else
                        UIManager:show(InfoMessage:new{
                            text = err or "Update check failed.",
                            timeout = 5,
                        })
                    end
                end)
            end)
        end,
    })
    table.insert(settings_items, {
        text         = "Download inbox automatically",
        help_text    = "Books sent to this device are downloaded at the next sync "
                       .. "(launch, WiFi on, wake, opening or closing a book, Sync now) "
                       .. "without opening the Inbox.",
        checked_func = function() return TomeSync._linkAutoDeliver() end,
        callback     = function()
            G_reader_settings:saveSetting("tomesync_auto_deliver",
                not TomeSync._linkAutoDeliver())
        end,
    })
    -- The reader link's trash (files the phone deleted): how long they stay.
    local function trashDaysItems()
        local function current()
            local d = G_reader_settings:readSetting("tomesync_link_trash_days")
            if type(d) ~= "number" or d < 0 then d = TomeSync.LINK_TRASH_DAYS end
            return d
        end
        local items = {}
        for _, entry in ipairs({
            { 7,  "7 days" },
            { 30, "30 days (default)" },
            { 90, "90 days" },
            { 0,  "Never by age (size bound only)" },
        }) do
            local value, label = entry[1], entry[2]
            table.insert(items, {
                text         = label,
                checked_func = function() return current() == value end,
                callback     = function()
                    if value == TomeSync.LINK_TRASH_DAYS then
                        G_reader_settings:delSetting("tomesync_link_trash_days")
                    else
                        G_reader_settings:saveSetting("tomesync_link_trash_days", value)
                    end
                end,
            })
        end
        return items
    end
    table.insert(settings_items, {
        text           = "Keep deleted files",
        help_text      = "Files Tome Reader deletes on this device go to a hidden "
                       .. "trash folder (.tomesync-trash in the library) first. "
                       .. "They are removed for good after this many days, or "
                       .. "sooner when the trash passes 50 items or 200 MB. The "
                       .. "newest item is always kept.",
        sub_item_table = trashDaysItems(),
    })
    table.insert(settings_items, {
        text         = "Auto-check for updates on launch",
        checked_func = function() return G_reader_settings:isTrue("tomesync_auto_check") end,
        callback     = function()
            G_reader_settings:saveSetting("tomesync_auto_check",
                not G_reader_settings:isTrue("tomesync_auto_check"))
        end,
    })
    table.insert(settings_items, {
        text         = "Auto-sync reading history on launch",
        help_text    = "Pushes KOReader's own page-level reading history to Tome so "
                       .. "your stats include reading from before TomeSync. The first "
                       .. "sync backfills everything (chunked and resumable); later "
                       .. "syncs send only new reading. Reading time and pages only - "
                       .. "never your read/unread status.",
        checked_func = function() return G_reader_settings:isTrue("tomesync_auto_sync_stats") end,
        callback     = function()
            G_reader_settings:saveSetting("tomesync_auto_sync_stats",
                not G_reader_settings:isTrue("tomesync_auto_sync_stats"))
        end,
    })
    table.insert(settings_items, {
        text         = "Sync on suspend",
        help_text    = "When the device goes to sleep, catch up anything still "
                       .. "pending - sessions, ratings and (if enabled above) "
                       .. "reading history - so your stats are current in the "
                       .. "morning without waking the device. Only syncs when "
                       .. "WiFi is already connected; enable aggressive sync "
                       .. "below to turn WiFi on first.",
        checked_func = function() return G_reader_settings:isTrue("tomesync_sync_on_suspend") end,
        callback     = function()
            G_reader_settings:saveSetting("tomesync_sync_on_suspend",
                not G_reader_settings:isTrue("tomesync_sync_on_suspend"))
        end,
    })
    table.insert(settings_items, {
        text         = "Aggressive sync (turn WiFi on at suspend)",
        help_text    = "If WiFi is off when the device goes to sleep, turn it on, "
                       .. "sync, and let the device power it back down as it "
                       .. "suspends. Uses more battery than plain sync on "
                       .. "suspend. If the device sleeps before the sync "
                       .. "finishes, it completes on the next connection.",
        enabled_func = function() return G_reader_settings:isTrue("tomesync_sync_on_suspend") end,
        checked_func = function() return G_reader_settings:isTrue("tomesync_suspend_wifi") end,
        callback     = function()
            G_reader_settings:saveSetting("tomesync_suspend_wifi",
                not G_reader_settings:isTrue("tomesync_suspend_wifi"))
        end,
    })
    table.insert(settings_items, {
        text         = "Apply Tome metadata to this device",
        help_text    = "Keep title, author, series, tags, description and cover "
                       .. "of the books that came from Tome in step with the "
                       .. "Tome library, using KOReader's own custom metadata "
                       .. "(the book files are never modified). Tome is the "
                       .. "source of truth: metadata edited on this device is "
                       .. "overridden. Runs shortly after launch and when WiFi "
                       .. "connects; \"Apply Tome metadata now\" runs it on demand. "
                       .. "Needs KOReader " .. META_MIN_KOREADER .. " or newer.",
        enabled_func = metaSyncSupported,
        checked_func = function() return G_reader_settings:isTrue("tomesync_meta_sync") end,
        callback     = function()
            local on = not G_reader_settings:isTrue("tomesync_meta_sync")
            G_reader_settings:saveSetting("tomesync_meta_sync", on)
            if on then self:_syncMetadata(true) end
        end,
    })
    -- Idle cap: how long a single page may count before the rest of the gap is
    -- treated as the device sitting awake unread (fell asleep, cover open).
    local function idleCapItems()
        local function current()
            local m = G_reader_settings:readSetting("tomesync_idle_cap_minutes")
            if m == nil then m = IDLE_CAP_DEFAULT_MINUTES end
            return m
        end
        local items = {}
        for _, entry in ipairs({
            { 5,  "5 minutes" },
            { 10, "10 minutes (default)" },
            { 15, "15 minutes" },
            { 30, "30 minutes" },
            { 0,  "Off - count wall-clock time" },
        }) do
            local value, label = entry[1], entry[2]
            table.insert(items, {
                text         = label,
                checked_func = function() return current() == value end,
                callback     = function()
                    if value == IDLE_CAP_DEFAULT_MINUTES then
                        G_reader_settings:delSetting("tomesync_idle_cap_minutes")
                    else
                        G_reader_settings:saveSetting("tomesync_idle_cap_minutes", value)
                    end
                end,
            })
        end
        return items
    end
    table.insert(settings_items, {
        text           = "Idle time cap",
        separator      = true,
        help_text      = "Longest gap between page turns that still counts as "
                       .. "reading. If the device is left awake without turning "
                       .. "pages - you fell asleep, or the cover did not put it "
                       .. "to sleep - time beyond the cap is not booked to the "
                       .. "reading session.",
        sub_item_table = idleCapItems(),
    })
    table.insert(settings_items, {
        text_func      = function() return "Device name: " .. deviceName() end,
        separator      = true,
        help_text      = "The label Tome shows for this device (reading log, "
                       .. "\"Where you read\"). Defaults to the device model; "
                       .. "set a name if you use several devices of the same "
                       .. "kind.",
        keep_menu_open = true,
        callback       = function() self:_editDeviceName() end,
    })
    table.insert(settings_items, {
        text_func      = function()
            local custom = G_reader_settings:readSetting("tomesync_server_url")
            return "Server URL: " .. primaryURL() .. (custom and " (custom)" or "")
        end,
        help_text      = "The address of your Tome server, e.g. "
                       .. "http://192.168.1.10:8080 or "
                       .. "https://tome.example.com. The plugin download bakes "
                       .. "this in for you; set it here if the server has "
                       .. "moved. Clear it to restore the baked-in default.",
        keep_menu_open = true,
        callback       = function()
            self:_editStringSetting("tomesync_server_url", "Server URL",
                DEFAULT_SERVER_URL, "Your Tome instance address.")
        end,
    })
    table.insert(settings_items, {
        text_func      = function()
            local fb = fallbackURL()
            if not fb then return "Fallback server URL: none" end
            local active = serverURL() == fb
            return "Fallback server URL: " .. fb .. (active and " (in use)" or "")
        end,
        help_text      = "A second address for the same Tome server, tried "
                       .. "when the server URL does not answer - for example "
                       .. "the LAN address at home when the main URL only "
                       .. "works from outside (a router without hairpin NAT, "
                       .. "a VPN address). Whichever address answered last "
                       .. "is tried first, so after a network change the "
                       .. "plugin switches once and sticks with it. Leave "
                       .. "empty if you only use one address.",
        keep_menu_open = true,
        callback       = function()
            self:_editStringSetting("tomesync_fallback_url", "Fallback server URL",
                "e.g. http://192.168.1.10:8080",
                "Optional. Tried when the server URL does not answer.", "none")
        end,
    })
    table.insert(settings_items, {
        text_func      = function() return "Account: " .. username() end,
        help_text      = "The Tome account this device syncs as. It is decided "
                       .. "by the API key, not editable here - use \"Sign in "
                       .. "with code\" below to switch accounts.",
        keep_menu_open = true,
        callback       = function()
            local paired = G_reader_settings:readSetting("tomesync_api_key") ~= nil
            UIManager:show(InfoMessage:new{
                text = "Account: " .. username()
                       .. "\nServer: " .. serverURL()
                       .. "\nAPI key: " .. (paired and "paired on this device"
                                                    or "baked in at download"),
                timeout = 5,
            })
        end,
    })
    table.insert(settings_items, {
        text           = "Sign in with code",
        help_text      = "Connect this device to a Tome account without typing "
                       .. "any credentials: the plugin shows a short code, you "
                       .. "enter it in the Tome web UI (Settings > Security > "
                       .. "Quick Connect), and the device receives its own API "
                       .. "key. Use this after changing the server URL, or to "
                       .. "switch accounts.",
        keep_menu_open = true,
        callback       = function() self:_signInWithCode() end,
    })
    table.insert(settings_items, {
        text           = "Reset connection to baked-in defaults",
        help_text      = "Forget the server URLs, API key and account set on "
                       .. "this device and go back to what was baked in when "
                       .. "the plugin was downloaded.",
        enabled_func   = function() return hasOverrides() end,
        keep_menu_open = true,
        callback       = function()
            UIManager:show(ConfirmBox:new{
                text = "Reset server URLs, API key and account to the baked-in "
                       .. "defaults?",
                ok_text = "Reset",
                ok_callback = function() self:_resetConnection() end,
            })
        end,
    })

    -- In-book items
    if in_book then
        table.insert(sub_items, {
            text     = "Download full series",
            callback = function() self:_downloadCurrentBookSeries(false) end,
        })
        table.insert(sub_items, {
            text     = "Download rest of series",
            callback = function() self:_downloadCurrentBookSeries(true) end,
        })
        table.insert(sub_items, {
            text         = "Sync now",
            callback     = function()
                whenConnected(function()
                    if self.book_id then
                        self:_syncPositionNow()
                        self:_syncAnnotations()
                        self:_pushRatingOnLeave()
                    end
                    self:_flushPendingSessions()
                    self:_flushPendingRatings()
                    self:_pushDeviceBooks(true)
                    local pending = #self.pending_sessions
                    local msg
                    if self.book_id then
                        local pct = self:_getCurrentPercentage()
                        msg = string.format("Synced: %.1f%%", pct * 100)
                    else
                        msg = "Book not resolved (position not synced)"
                    end
                    if pending > 0 then
                        msg = msg .. string.format("\n%d session(s) still pending", pending)
                    end
                    UIManager:show(InfoMessage:new{
                        text = msg,
                        timeout = 4,
                    })
                    self:_scheduleLinkSync("sync-now", 1)
                end)
            end,
        })
        table.insert(sub_items, {
            text = self.enabled and "Tracking: on (tap to pause)"
                or "Tracking: paused (tap to resume)",
            help_text = "Pauses all automatic tracking and syncing — sessions, "
                        .. "position, highlights — until resumed. Resets to on "
                        .. "when KOReader restarts.",
            callback = function()
                self.enabled = not self.enabled
                UIManager:show(InfoMessage:new{
                    text    = self.enabled and "TomeSync tracking resumed."
                        or "Tracking paused for this session.\nTurns back on at next KOReader start.",
                    timeout = 3,
                })
            end,
        })
        table.insert(sub_items, {
            separator = true,
            text_func = function()
                local n = #self.pending_sessions
                if n > 0 then
                    return string.format("Pending sessions (%d)", n)
                end
                return "Pending sessions (0)"
            end,
            callback = function()
                local n = #self.pending_sessions
                if n == 0 then
                    UIManager:show(InfoMessage:new{
                        text = "No pending sessions.",
                        timeout = 3,
                    })
                else
                    local lines = string.format("%d session(s) waiting to sync.\n", n)
                    for i, s in ipairs(self.pending_sessions) do
                        if i > 5 then lines = lines .. "\n..."; break end
                        lines = lines .. string.format("\n%s (%s)",
                            s.started_at or "?", s.device or "?")
                    end
                    UIManager:show(InfoMessage:new{
                        text = lines,
                        timeout = 8,
                    })
                end
            end,
        })
    end

    table.insert(sub_items, {
        text_func = function()
            local s, p, r = self:_syncCodeItems(false)
            local n = #s + #p + #r
            if n > 0 then return string.format("Show sync code (%d)", n) end
            return "Show sync code"
        end,
        help_text = "Hands the reading done while offline to a phone that is "
                 .. "online: sessions, positions and ratings that could not "
                 .. "be sent are shown as a QR code. Scan it with the Tome app "
                 .. "or the web UI (Settings > KOReader > Scan a sync code). "
                 .. "Nothing is removed here; the next WiFi sync still runs "
                 .. "and Tome ignores what it already has.",
        callback = function() self:_showSyncCode() end,
    })
    table.insert(sub_items, {
        text           = "Settings",
        sub_item_table = settings_items,
    })
    table.insert(sub_items, {
        text     = "About",
        callback = function()
            UIManager:show(InfoMessage:new{
                text    = "TomeSync " .. SEMVER .. " (build " .. BUILD .. ")"
                          .. "\nSyncs with your Tome library.",
                timeout = 4,
            })
        end,
    })

    return sub_items
end

function TomeSync:addToMainMenu(menu_items)
    menu_items.tomesync = {
        text = "TomeSync",
        -- Rebuilt each open so the Inbox badge/visibility reflects the latest
        -- poll rather than the state frozen at init().
        sub_item_table_func = function() return self:_menuItems() end,
    }
end

-- Show the full TomeSync menu as a standalone popup (used by the "Open menu"
-- gesture). Reuses _menuItems() so it always matches the wrench-menu contents.
-- The plain Menu widget has no checkboxes or nested tables (TouchMenu-only),
-- so toggles get their state appended to the label and submenus open as
-- another popup.
function TomeSync:_openMenu()
    self:_showPopupMenu("TomeSync", self:_menuItems())
end

function TomeSync:_showPopupMenu(title, raw)
    local items = {}
    for _, it in ipairs(raw) do
        local orig      = it.callback
        local sub       = it.sub_item_table
        local text_func = it.text_func
        if it.checked_func and not text_func then
            local base, checked_func = it.text, it.checked_func
            text_func = function()
                return base .. (checked_func() and ": on" or ": off")
            end
        elseif sub and not text_func then
            text_func = function() return it.text .. " \u{25B8}" end
        end
        table.insert(items, {
            text      = it.text,
            text_func = text_func,
            callback  = function()
                if self._gesture_menu then UIManager:close(self._gesture_menu) end
                if sub then
                    self:_showPopupMenu(it.text, sub)
                elseif orig then
                    orig()
                end
            end,
        })
    end
    self._gesture_menu = Menu:new{
        title       = title,
        item_table  = items,
        width       = Device.screen:getWidth() - 20,
        height      = Device.screen:getHeight() - 20,
    }
    UIManager:show(self._gesture_menu)
end

logger.info("TomeSync: main_impl.lua loaded successfully, returning plugin class")
return TomeSync
