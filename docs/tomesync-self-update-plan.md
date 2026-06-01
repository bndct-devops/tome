# TomeSync Self-Update — Implementation Plan

> Status: **CODE COMPLETE — pending on-device testing + first manual deploy.**
> In-app "Check for updates" + self-update for the TomeSync KOReader plugin,
> replacing the SSH-into-every-device workflow. Built so a bad update **cannot
> brick** — it self-heals on the next launch. Backend + shim + impl all
> implemented on branch `tomesync-self-update`; §8 device tests and the §7
> one-time manual bootstrap remain.

Decisions locked with the user:
- **Anti-brick:** stable shim + auto-rollback (not just validate+backup).
- **Compat:** protect existing installs **and** keep `/plugin/version` back-compatible.
- **Versioning:** independent plugin **semver** for humans + hidden monotonic
  **build integer** for the actual comparison.
- **Trigger:** a manual "Check for updates" item **plus** an opt-in auto-check on launch.

---

## 1. The brick problem, stated precisely

A KOReader plugin can only "brick" by failing at **load/init** time. KOReader
itself is resilient — `PluginLoader` `pcall`s both the module load and the
instance creation, so a broken plugin is *skipped*, not fatal. So:

- **KOReader never bricks.** Worst realistic case = "TomeSync is missing this session."
- The job is to make even *that* self-recover, automatically, without SSH.

Failure modes to defend against:

| # | Failure | When | Defense |
|---|---|---|---|
| 1 | Truncated / corrupt download; server returns an HTML error page or empty body, written over the plugin | install | **Validate before swap** (status 200, size floor, `load()` compiles, sentinel strings) |
| 2 | New code has a **syntax error** | next load | Shim `pcall(dofile)` fails → **same-boot rollback** |
| 3 | New code loads but **throws at `init()`** | next instance creation | **Confirm-counter**: unproven build that doesn't confirm within one boot → **next-boot rollback** |
| 4 | Even the backup is broken | rollback | Shim returns a **stub plugin** (valid, inert) so KOReader still gets a module |
| 5 | Logic bug in an event handler (e.g. `onPageUpdate`) | runtime | Out of scope for bricking — KOReader `pcall`s event dispatch; it's a normal bug fixed by a later update |

---

## 2. Architecture — frozen shim + replaceable impl

Split the single `main.lua` into two files:

```
tomesync.koplugin/
  _meta.lua              static
  main.lua               STABLE SHIM — deployed once, never touched by self-update
  main_impl.lua          the real plugin (code + baked config) — what updates replace
  main_impl.lua.bak      last confirmed-good impl
```

- **`main.lua` (shim):** ~50 lines, defensively `pcall`-wrapped end-to-end, contains
  **no config**. Its only jobs: locate its own dir, run the rollback state-machine,
  `dofile` the impl, return the plugin class. It is generated once and **frozen** —
  the updater never overwrites it. (If it ever must change: rare, manual redeploy.)
- **`main_impl.lua` (impl):** everything else — the full plugin code **and** the
  baked `SERVER_URL` / `API_KEY` / `USERNAME`. This is the only file the updater swaps.
- **State** (`book_map`, `pending_sessions`, settings) lives in `G_reader_settings`,
  *not* in the files — so updates never touch reading progress / mappings.

The shim finds its own directory with `debug.getinfo(1,"S").source` (strip `@` +
filename) — self-contained, no dependency on how KOReader loaded it.

---

## 3. Anti-brick state machine (the centerpiece)

State (in `G_reader_settings`, key `tomesync_update`):
`{ build = <installed build>, confirmed = <bool>, boots = <int>, prev_build = <int> }`

### Updater (in impl) — install build N
1. Fetch new impl text; **validate** (see §5). Abort untouched on any failure.
2. `cp main_impl.lua → main_impl.lua.bak` (current is known-good).
3. Write text → `main_impl.lua.new`; `os.rename` → `main_impl.lua` (atomic; no
   partial file ever in place).
4. Set state `{ build=N, confirmed=false, boots=0, prev_build=<old build> }`; **flush**.
5. Notify: "Update vX installed — restart KOReader."

### Shim — every load, before `dofile`
```
state = read()
if state and not state.confirmed then
    state.boots = (state.boots or 0) + 1
    if state.boots >= 2 then
        -- a prior boot installed this build but it never confirmed -> it crashed at init
        restore main_impl.lua.bak -> main_impl.lua
        state = { build = state.prev_build, confirmed = true }   -- backup is known-good
        notify("TomeSync update failed — rolled back")
    end
    write(state); flush()
end

ok, plugin = pcall(dofile, main_impl.lua)
if not ok then
    -- syntax / load-time throw: roll back immediately, same boot
    restore main_impl.lua.bak -> main_impl.lua
    set state confirmed=true, build=prev_build; flush()
    ok, plugin = pcall(dofile, main_impl.lua)
end
if not ok then return stub_plugin() end   -- last resort: valid inert module
return plugin
```

### Impl — on successful `init()`
```
if state.build == THIS_BUILD and not state.confirmed then
    state.confirmed = true
    G_reader_settings:flush()    -- force the write now, not just on exit
end
```

### Walkthrough
- **Good update:** install N (confirmed=false). Boot1: shim boots=1, `dofile` OK,
  `init()` reaches the end → confirmed=true (+flush). Boot2+: confirmed → normal.
- **Syntax-broken update:** Boot1: `pcall(dofile)` fails → restore `.bak`, reload →
  working version **this same launch**. One notification, zero downtime.
- **Init-crashing update:** Boot1: `dofile` OK, `init()` throws (KOReader skips the
  instance, TomeSync absent this session), confirmed stays false. Boot2: shim sees
  unconfirmed + boots≥2 → restore `.bak` → working version. Recovers within one relaunch.
- **Backup also broken** (shouldn't happen — backup was confirmed-good): stub plugin
  returned; KOReader fine; menu says "TomeSync failed to load — reinstall."

False rollbacks (e.g. battery died mid-first-boot) only ever land you on the
previous **known-good** version — harmless; just re-run the update.

---

## 4. Versioning

- New constants (replace `TOMESYNC_PLUGIN_VERSION`):
  - `TOMESYNC_PLUGIN_BUILD: int` — monotonic, the **only** thing compared. Current
    "7" → continue at **8**.
  - `TOMESYNC_PLUGIN_SEMVER: str` — independent plugin track, human-facing. Start **"1.0.0"**.
- Comparison stays a trivial `tonumber(server_build) > local_build` — no semver
  parsing in Lua (zero bug surface, which also means the *version logic* can't cause
  a bad install; and even if it did, §3 catches it).
- Semver is display-only ("TomeSync 1.0.0 (build 8)").

---

## 5. Server changes (`backend/api/tome_sync.py`)

- `GET /plugin/version` — **back-compatible**: keep `version` = build int as a string
  (unchanged for any existing reader, incl. the web UI), **add** `build` (int) and
  `semver` (str):
  `{"version": "8", "build": 8, "semver": "1.0.0"}`
- New `GET /plugin/main-impl.lua` — **authenticated** (`_get_api_key_user`), returns
  `_main_impl_lua(server_url, caller_api_key, caller_username)` as `text/plain`. Bakes
  the caller's config, so config survives every update. *(Verify nothing else needs the
  shim text; the shim is config-free and frozen, so it isn't served for updates.)*
- Refactor `_main_lua` → `_main_shim_lua()` (static) + `_main_impl_lua(...)` (config).
  The plugin-zip download endpoint now writes `_meta.lua`, `main.lua` (shim),
  `main_impl.lua` (impl).
- Confirm the web UI's reading of `/plugin/version` still works (it reads `version`
  → same format); optionally switch its display to `semver`.

---

## 6. Plugin (Lua) changes

**Shim (`main.lua`)** — frozen, ~50 lines: self-dir, the §3 state machine, `pcall`
loader with same-boot rollback, `stub_plugin()` fallback. No config, no network.

**Impl (`main_impl.lua`)** — current code plus:
- The gesture action already drafted (`tome_browse_series` → `_browseSeriesMenu`).
- `BUILD` / `SEMVER` constants.
- `checkForUpdate()` — GET `/plugin/version`, compare `build`, return availability.
- Menu: **"Check for updates"** (manual) and a **"Auto-check on launch"** toggle.
- `installUpdate(build)` — fetch + validate + backup + atomic swap + arm state (§3).
- Validation helper: HTTP 200, body non-empty, `#body > 15000`, `load(body)` compiles,
  body contains `"function TomeSync:init"` **and** ends near `"return TomeSync"`.
- In `init()`: the confirm step (§3); if auto-check enabled, a **deferred**
  (`UIManager:scheduleIn`) non-blocking check that only notifies when newer.

---

## 7. Bootstrapping (the last manual deploy)

Chicken-and-egg: the first version carrying the shim+updater is installed **by hand**
(the final SSH push). It places `main.lua` (shim) **and** `main_impl.lua`, replacing
the old single-file plugin. Every update after that is "Check for updates → restart"
and only ever rewrites `main_impl.lua`.

This same first deploy also ships the `tome_browse_series` gesture action.

---

## 8. Testing (must pass before we trust it on the real device)

On a throwaway KOReader (or the showcase device) drive each §3 branch:
1. **Happy path:** bump build on server → Check for updates → install → restart →
   new version loads, confirms, `.bak` retained.
2. **Syntax-broken impl** (inject a deliberate error into the served impl) → install →
   restart → shim rolls back same boot; TomeSync still present; notification shown.
3. **Init-crashing impl** (`error()` early in `init`) → install → restart (TomeSync
   absent) → restart again → auto-rolled back to working version.
4. **Garbage download** (server returns HTML/empty) → install **aborts**, files untouched.
5. **Backup-also-broken** (manually corrupt `.bak` + impl) → stub plugin; KOReader fine.
6. Config + state survive an update (token, `book_map`, `pending_sessions` intact).

Keep the manual SSH restore (`mv .bak`) documented as the ultimate fallback.

---

## 9. Definition of Done
- [x] `_main_lua` split into `_main_shim_lua()` + `_main_impl_lua()`; zip writes all three files
- [x] `/plugin/version` returns `version`(compat) + `build` + `semver`
- [x] `GET /plugin/main-impl.lua` (authenticated, config-baked)
- [x] `TOMESYNC_PLUGIN_BUILD=8`, `TOMESYNC_PLUGIN_SEMVER="1.0.0"`
- [x] Shim: state machine, pcall loader, same-boot + next-boot rollback, stub fallback
- [x] Impl: check/install/validate, "Check for updates" + "Auto-check" menu, confirm-on-init, gesture action
- [ ] All §8 test branches pass on a test device
- [ ] First version deployed manually (shim + impl); `.bak` retained
- [x] Web UI `/plugin/version` consumer verified (reads `version`; now displays `semver` + build); `TOMESYNC_PLUGIN_VERSION` kept as compat alias
- [x] CHANGELOG + CLAUDE.md note the TomeSync versioning change

---

## 10. Open considerations
- **`_meta.lua` changes:** rare; if ever needed, the updater would refresh it too.
  For v1, impl-only updates (the shim and `_meta` are static).
- **Shim immutability:** keep the shim deliberately minimal so it never needs an
  update; if it ever does, that's a manual redeploy (documented).
- **Multi-device:** each device self-updates independently against the same server;
  no coordination needed.
