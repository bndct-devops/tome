"""TomeSync API — custom KOReader plugin endpoints.

Auth: Bearer API key (not JWT) for all /api/tome-sync/ endpoints.
Plugin download: Bearer JWT for /api/plugin/koreader.
"""
import io
import logging
import zipfile
from datetime import datetime
from typing import Optional

from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel as PydanticBaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from backend.core.database import get_db
from backend.core.security import get_current_user
from backend.models.user import User
from backend.models.book import Book, BookFile
from backend.models.user_book_status import UserBookStatus
from backend.models.tome_sync import ApiKey, ReadingSession, TomeSyncPosition

router = APIRouter(tags=["tome-sync"])
logger = logging.getLogger(__name__)

TOMESYNC_PLUGIN_VERSION = "4"  # bump when plugin code changes


# ── API key auth ──────────────────────────────────────────────────────────────

def _get_api_key_user(
    authorization: str = Header(..., description="Bearer <api_key>"),
    db: Session = Depends(get_db),
) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    key = authorization.removeprefix("Bearer ").strip()
    api_key = db.query(ApiKey).filter(ApiKey.key == key).first()
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    user = db.get(User, api_key.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    # Update last_used_at
    api_key.last_used_at = datetime.utcnow()
    db.commit()
    return user


def _get_position(db: Session, user_id: int, book_id: int) -> Optional[TomeSyncPosition]:
    return (
        db.query(TomeSyncPosition)
        .filter(TomeSyncPosition.user_id == user_id, TomeSyncPosition.book_id == book_id)
        .first()
    )


# ── Resolve endpoint ─────────────────────────────────────────────────────────

@router.get("/tome-sync/resolve")
def resolve_book(
    filename: str,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Match a filename to a Tome book ID.

    KOReader OPDS downloads save files as 'Author - Vol. X — Title.ext'.
    We try multiple strategies, including volume number extraction.
    """
    import re

    stem = filename.rsplit(".", 1)[0] if "." in filename else filename

    # Extract volume number from filename (e.g. "Vol. 1", "Vol. 12", "v01")
    vol_match = re.search(r'[Vv]ol\.?\s*(\d+)', stem)
    vol_num = float(vol_match.group(1)) if vol_match else None

    # 1. Exact file path match in book_files
    book_file = (
        db.query(BookFile)
        .filter(BookFile.file_path.endswith("/" + filename) | (BookFile.file_path == filename))
        .first()
    )
    if book_file:
        book = db.get(Book, book_file.book_id)
        if book and book.status == "active":
            return {"book_id": book.id}

    # 2. Extract title part and match with volume
    title_part = None
    if "\u2014" in stem:  # em dash: 'Author - Vol. X — Title'
        title_part = stem.split("\u2014")[-1].strip()
    elif " - " in stem:  # regular dash fallback
        parts = stem.split(" - ", 1)
        title_part = parts[-1].strip()
        # Remove "Vol. X" prefix from title_part if present
        title_part = re.sub(r'^[Vv]ol\.?\s*\d+\s*[-—]?\s*', '', title_part).strip()

    if title_part:
        query = db.query(Book).filter(
            Book.title.ilike(f"%{title_part}%"), Book.status == "active"
        )
        if vol_num is not None:
            # Prefer exact volume match
            book = query.filter(Book.series_index == vol_num).first()
            if book:
                return {"book_id": book.id}
        # Fall back to first match if no volume info
        book = query.first()
        if book:
            return {"book_id": book.id}

    # 3. Reverse match: book title contained in filename, with volume
    books = db.query(Book).filter(Book.status == "active").all()
    for book in books:
        if book.title and book.title.lower() in stem.lower():
            if vol_num is not None and book.series_index is not None:
                if book.series_index == vol_num:
                    return {"book_id": book.id}
            else:
                return {"book_id": book.id}

    # 4. Reverse match without volume constraint (last resort)
    for book in books:
        if book.title and book.title.lower() in stem.lower():
            return {"book_id": book.id}

    raise HTTPException(status_code=404, detail="No matching book found")


# ── Position endpoints ────────────────────────────────────────────────────────

@router.get("/tome-sync/position/{book_id}")
def get_position(
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    book = db.get(Book, book_id)
    if not book or book.status != "active":
        raise HTTPException(status_code=404, detail="Book not found")

    pos = _get_position(db, user.id, book_id)
    if not pos:
        raise HTTPException(status_code=404, detail="No position stored")

    return {
        "book_id": book_id,
        "progress": pos.progress,
        "percentage": pos.percentage,
        "device": pos.device,
        "updated_at": pos.updated_at.isoformat() + "Z",
    }


class PutPositionRequest(PydanticBaseModel):
    progress: Optional[str] = None
    percentage: float
    device: Optional[str] = None


@router.put("/tome-sync/position/{book_id}")
def put_position(
    book_id: int,
    body: PutPositionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    book = db.get(Book, book_id)
    if not book or book.status != "active":
        raise HTTPException(status_code=404, detail="Book not found")

    # Clamp percentage to 0-1 range
    pct = max(0.0, min(1.0, body.percentage))

    pos = _get_position(db, user.id, book_id)
    if pos:
        pos.progress = body.progress
        pos.percentage = pct
        pos.device = body.device
        pos.updated_at = datetime.utcnow()
    else:
        pos = TomeSyncPosition(
            user_id=user.id,
            book_id=book_id,
            progress=body.progress,
            percentage=pct,
            device=body.device,
        )
        db.add(pos)

    # Keep UserBookStatus in sync
    status_row = (
        db.query(UserBookStatus)
        .filter(UserBookStatus.user_id == user.id, UserBookStatus.book_id == book_id)
        .first()
    )
    if status_row:
        status_row.progress_pct = pct
        if body.progress:
            status_row.cfi = body.progress
        if status_row.status == "unread" and pct > 0:
            status_row.status = "reading"
        elif pct >= 0.99:
            status_row.status = "read"
    else:
        new_status = "read" if pct >= 0.99 else ("reading" if pct > 0 else "unread")
        db.add(UserBookStatus(
            user_id=user.id,
            book_id=book_id,
            status=new_status,
            progress_pct=pct,
            cfi=body.progress,
        ))

    db.commit()
    return {"ok": True, "timestamp": datetime.utcnow().isoformat() + "Z"}


# ── Session endpoint ──────────────────────────────────────────────────────────

class PostSessionRequest(PydanticBaseModel):
    book_id: int
    started_at: str  # ISO 8601
    ended_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    progress_start: Optional[float] = None
    progress_end: Optional[float] = None
    pages_turned: Optional[int] = None
    device: Optional[str] = None
    session_uuid: Optional[str] = None  # client dedup key


@router.post("/tome-sync/session", status_code=201)
def post_session(
    body: PostSessionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    book = db.get(Book, body.book_id)
    if not book or book.status != "active":
        raise HTTPException(status_code=404, detail="Book not found")

    # Dedup: if same session_uuid already stored, return it
    if body.session_uuid:
        existing = (
            db.query(ReadingSession)
            .filter(ReadingSession.session_uuid == body.session_uuid)
            .first()
        )
        if existing:
            return {"session_id": existing.id}

    try:
        started = datetime.fromisoformat(body.started_at.replace("Z", "+00:00"))
        ended = datetime.fromisoformat(body.ended_at.replace("Z", "+00:00")) if body.ended_at else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime: {exc}")

    session = ReadingSession(
        user_id=user.id,
        book_id=body.book_id,
        started_at=started,
        ended_at=ended,
        duration_seconds=body.duration_seconds,
        progress_start=body.progress_start,
        progress_end=body.progress_end,
        pages_turned=body.pages_turned,
        device=body.device,
        session_uuid=body.session_uuid,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return {"session_id": session.id}


# ── Series endpoints (API-key-authed, for the plugin) ────────────────────────

@router.get("/tome-sync/series")
def list_series(
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """List all series for the series browser menu."""
    rows = (
        db.query(Book.series, func.count(Book.id).label("book_count"))
        .filter(Book.status == "active", Book.series.isnot(None))
        .group_by(Book.series)
        .order_by(Book.series)
        .all()
    )

    result = []
    for series_name, book_count in rows:
        first_book = (
            db.query(Book)
            .filter(Book.status == "active", Book.series == series_name)
            .order_by(Book.series_index.asc().nullslast(), Book.title.asc())
            .first()
        )
        result.append({
            "name": series_name,
            "book_count": book_count,
            "author": first_book.author if first_book else None,
            "first_book_id": first_book.id if first_book else None,
        })

    return result


@router.get("/tome-sync/series/{book_id}")
def get_series_books(
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Given a book_id, return all books in the same series with file info."""
    book = db.get(Book, book_id)
    if not book or book.status != "active":
        raise HTTPException(status_code=404, detail="Book not found")
    if not book.series:
        raise HTTPException(status_code=404, detail="Book has no series")

    books = (
        db.query(Book)
        .options(joinedload(Book.files))
        .filter(Book.status == "active", Book.series == book.series)
        .order_by(Book.series_index.asc().nullslast(), Book.title.asc())
        .all()
    )

    return {
        "series_name": book.series,
        "books": [
            {
                "id": b.id,
                "title": b.title,
                "series_index": b.series_index,
                "author": b.author,
                "files": [
                    {"id": f.id, "format": f.format, "file_size": f.file_size}
                    for f in b.files
                ],
            }
            for b in books
        ],
    }


@router.get("/tome-sync/download/{book_id}/{file_id}")
def download_book_via_api_key(
    book_id: int,
    file_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(_get_api_key_user),
):
    """Stream a book file using API key auth (for the plugin)."""
    book_file = (
        db.query(BookFile)
        .filter(BookFile.id == file_id, BookFile.book_id == book_id)
        .first()
    )
    if not book_file:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = Path(book_file.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File no longer on disk")

    filename = f"{book_file.book.title}.{book_file.format}"
    return FileResponse(
        str(file_path),
        media_type="application/octet-stream",
        filename=filename,
    )


# ── API key management (JWT-authed, for the web UI) ───────────────────────────

@router.get("/plugin/api-keys")
def list_api_keys(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    keys = db.query(ApiKey).filter(ApiKey.user_id == current_user.id).all()
    return [
        {
            "id": k.id,
            "label": k.label,
            "key_preview": k.key[:8] + "…",
            "created_at": k.created_at.isoformat(),
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        }
        for k in keys
    ]


class CreateKeyRequest(PydanticBaseModel):
    label: str = "KOReader Plugin"


@router.post("/plugin/api-keys", status_code=201)
def create_api_key(
    body: CreateKeyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    key_value = ApiKey.generate()
    api_key = ApiKey(user_id=current_user.id, key=key_value, label=body.label)
    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    # Return the full key only once — it cannot be retrieved again
    return {
        "id": api_key.id,
        "label": api_key.label,
        "key": key_value,
        "created_at": api_key.created_at.isoformat(),
    }


@router.delete("/plugin/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    key_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    api_key = db.query(ApiKey).filter(
        ApiKey.id == key_id, ApiKey.user_id == current_user.id
    ).first()
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")
    db.delete(api_key)
    db.commit()


# ── Plugin version ────────────────────────────────────────────────────────────

@router.get("/plugin/version")
def plugin_version() -> dict:
    return {"version": TOMESYNC_PLUGIN_VERSION}


# ── Plugin download ───────────────────────────────────────────────────────────

@router.get("/plugin/koreader")
def download_plugin(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    server_url: str | None = None,
):
    """Generate and download a pre-configured tomesync.koplugin ZIP."""
    # Auto-create an API key if none exists
    existing_keys = db.query(ApiKey).filter(ApiKey.user_id == current_user.id).all()
    if existing_keys:
        api_key_value = existing_keys[0].key
    else:
        api_key_value = ApiKey.generate()
        db.add(ApiKey(user_id=current_user.id, key=api_key_value, label="KOReader Plugin"))
        db.commit()

    # Use explicit server_url if provided (frontend passes it to avoid vite proxy issues)
    if not server_url:
        server_url = str(request.base_url).rstrip("/")

    # Build the ZIP in memory — single-file plugin (meta + main only)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("tomesync.koplugin/_meta.lua", _meta_lua())
        zf.writestr("tomesync.koplugin/main.lua", _main_lua(server_url, api_key_value, current_user.username))
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=tomesync.koplugin.zip"},
    )


# ── Lua plugin source ─────────────────────────────────────────────────────────

def _meta_lua() -> str:
    return '''\
local _ = require("gettext")

return {
    name = "tomesync",
    fullname = _("TomeSync"),
    description = _([[Sync reading progress with your Tome library server.
Tracks reading sessions and syncs position across devices.]]),
}
'''


def _main_lua(server_url: str, api_key: str, username: str) -> str:
    return f'''--[[
TomeSync KOReader Plugin — single-file build
Syncs reading progress and sessions with a Tome library server.
Browse and download series. Tracks reading sessions and syncs position across devices.

Installation: copy tomesync.koplugin/ into koreader/plugins/ and restart.
]]

local logger = require("logger")
logger.info("TomeSync: main.lua loading...")

local WidgetContainer = require("ui/widget/container/widgetcontainer")
local InfoMessage      = require("ui/widget/infomessage")
local UIManager        = require("ui/uimanager")
local Device           = require("device")
local NetworkMgr       = require("ui/network/manager")
local http             = require("socket.http")
local ltn12            = require("ltn12")
local rapidjson        = require("rapidjson")
local lfs              = require("libs/libkoreader-lfs")
local util             = require("util")
local Menu             = require("ui/widget/menu")

-- ── Config (baked in at download time) ───────────────────────────────────────

local SERVER_URL = "{server_url}"
local API_KEY    = "{api_key}"
local USERNAME   = "{username}"

-- Short timeout so unreachable server doesn't freeze the UI
http.TIMEOUT = 5

-- Track consecutive failures for backoff
local consecutive_failures = 0
local MAX_BACKOFF_FAILURES = 3

-- ── HTTP client ──────────────────────────────────────────────────────────────

local HEARTBEAT_PAGES = 50
local PLUGIN_VERSION  = "{TOMESYNC_PLUGIN_VERSION}"

local function urlEncode(s)
    return s:gsub("([^%w%-%.%_%~])", function(c)
        return string.format("%%%02X", string.byte(c))
    end)
end

local function deviceName()
    local ok, name = pcall(function() return Device:getFriendlyDeviceName() end)
    return (ok and name) or "KOReader"
end

local function apiRequest(method, path, body)
    -- Skip immediately if WiFi is not connected — zero blocking
    if not NetworkMgr:isConnected() then
        return nil, "offline"
    end

    -- Skip requests if server has been unreachable repeatedly (backoff)
    if consecutive_failures >= MAX_BACKOFF_FAILURES then
        logger.warn("TomeSync: skipping request (server unreachable, backing off)")
        return nil, "backoff"
    end

    local url = SERVER_URL .. "/api" .. path
    local req_body = body and rapidjson.encode(body) or nil
    local resp_chunks = {{}}

    local headers = {{
        ["Authorization"] = "Bearer " .. API_KEY,
        ["Content-Type"]  = "application/json",
        ["Accept"]        = "application/json",
    }}
    if req_body then
        headers["Content-Length"] = tostring(#req_body)
    end

    local ok, code = http.request({{
        url     = url,
        method  = method,
        headers = headers,
        source  = req_body and ltn12.source.string(req_body) or nil,
        sink    = ltn12.sink.table(resp_chunks),
    }})

    if not ok then
        consecutive_failures = consecutive_failures + 1
        logger.warn("TomeSync: request failed:", tostring(code),
                     "(" .. consecutive_failures .. "/" .. MAX_BACKOFF_FAILURES .. ")")
        return nil, code
    end

    -- Server reachable — reset backoff counter
    consecutive_failures = 0

    local resp_body = table.concat(resp_chunks)
    if code == 404 then return nil, 404 end
    if code >= 200 and code < 300 then
        local ok2, parsed = pcall(rapidjson.decode, resp_body)
        if ok2 then return parsed, code end
        return {{}}, code
    end

    logger.warn("TomeSync: HTTP", code, resp_body)
    return nil, code
end

-- ── Format preference & download helpers ────────────────────────────────────

local FORMAT_PREFERENCE = {{"epub", "kepub.epub", "cbz", "pdf", "mobi", "azw3"}}

local function pickBestFile(files)
    if not files or #files == 0 then return nil end
    for _, fmt in ipairs(FORMAT_PREFERENCE) do
        for _, f in ipairs(files) do
            if f.format == fmt then return f end
        end
    end
    return files[1]
end

local function downloadFile(book_id, file_id, dest_path)
    if not NetworkMgr:isConnected() then
        return false, "offline"
    end

    local url = SERVER_URL .. "/api/tome-sync/download/" .. book_id .. "/" .. file_id
    local fh = io.open(dest_path, "wb")
    if not fh then
        return false, "cannot open file for writing"
    end

    local saved_timeout = http.TIMEOUT
    http.TIMEOUT = 60

    local ok, code = http.request({{
        url     = url,
        method  = "GET",
        headers = {{
            ["Authorization"] = "Bearer " .. API_KEY,
        }},
        sink = ltn12.sink.file(fh),
    }})

    http.TIMEOUT = saved_timeout

    if not ok or (type(code) == "number" and code >= 300) then
        os.remove(dest_path)
        return false, tostring(code or "request failed")
    end

    return true
end

-- ── Plugin widget ────────────────────────────────────────────────────────────

local TomeSync = WidgetContainer:extend{{
    name        = "tomesync",
    is_doc_only = false,
}}

function TomeSync:init()
    self.book_id        = nil
    self.session_start  = nil
    self.page_count     = 0
    self.progress_start = nil
    self.last_progress  = nil
    self.enabled        = true
    self.book_map       = G_reader_settings:readSetting("tomesync_book_map") or {{}}
    self.pending_sessions = G_reader_settings:readSetting("tomesync_pending_sessions") or {{}}
    self.ui.menu:registerToMainMenu(self)
    logger.info("TomeSync: init complete, menu registered,",
                #self.pending_sessions, "pending sessions")
end

function TomeSync:onReaderReady()
    if not self.enabled then return end
    local doc = self.ui and self.ui.document
    if not doc then return end

    self.book_id = self.book_map[doc.file]

    -- If no cached mapping, try to resolve by filename
    if not self.book_id then
        self:_tryResolve()
    end

    if not self.book_id then return end

    self:_initSession()
end

function TomeSync:onPageUpdate(pageno)
    if not self.enabled then return end
    if pageno == false then return end

    -- Retry resolve if book wasn't matched on open (e.g. WiFi was not ready)
    if not self.book_id then
        self:_tryResolve()
        if self.book_id then
            self:_initSession()
        end
        return
    end

    self.page_count = self.page_count + 1
    if self.page_count % HEARTBEAT_PAGES == 0 then
        local pct = self:_getCurrentPercentage()
        self.last_progress = pct
        pcall(apiRequest, "PUT", "/tome-sync/position/" .. self.book_id, {{
            progress   = self:_getCurrentProgress(),
            percentage = pct,
            device     = deviceName(),
        }})
        -- Flush any offline sessions while we know WiFi is up
        self:_flushPendingSessions()
    end
end

function TomeSync:onSuspend()
    if not self.enabled or not self.book_id then return end

    -- Record the reading session (lid close = end of session)
    local pct      = self:_getCurrentPercentage()
    local cfi      = self:_getCurrentProgress()
    local duration = self.session_start and (os.time() - self.session_start) or 0
    local dev      = deviceName()

    pcall(apiRequest, "PUT", "/tome-sync/position/" .. self.book_id, {{
        progress = cfi, percentage = pct, device = dev,
    }})

    if duration > 10 then
        local session = {{
            book_id          = self.book_id,
            started_at       = os.date("!%Y-%m-%dT%H:%M:%SZ", self.session_start),
            ended_at         = os.date("!%Y-%m-%dT%H:%M:%SZ", os.time()),
            duration_seconds = duration,
            progress_start   = self.progress_start,
            progress_end     = pct,
            pages_turned     = self.page_count,
            device           = dev,
            session_uuid     = string.format("%d-%d-%s", self.book_id, self.session_start or 0, dev),
        }}
        local sok, sresult, scode = pcall(apiRequest, "POST", "/tome-sync/session", session)
        if not sok or not sresult or (type(scode) == "number" and scode >= 300) then
            -- Failed to send — save for later
            table.insert(self.pending_sessions, session)
            -- Cap at 50 to prevent unbounded growth
            while #self.pending_sessions > 50 do
                table.remove(self.pending_sessions, 1)
            end
            G_reader_settings:saveSetting("tomesync_pending_sessions", self.pending_sessions)
            logger.info("TomeSync: session queued for retry, pending:", #self.pending_sessions)
        end
    end
end

function TomeSync:onResume()
    if not self.enabled or not self.book_id then return end

    -- Start a fresh session (lid open = new session)
    self.session_start  = os.time()
    self.page_count     = 0
    self.progress_start = self:_getCurrentPercentage()
    self.last_progress  = self.progress_start

    -- Push position on wake — catches up after offline periods
    self:_pushPosition()

    -- Flush any pending sessions from offline periods
    self:_flushPendingSessions()
end

function TomeSync:_flushPendingSessions()
    if #self.pending_sessions == 0 then return end
    if not NetworkMgr:isConnected() then return end

    local remaining = {{}}
    for _, session in ipairs(self.pending_sessions) do
        local ok, result, code = pcall(apiRequest, "POST", "/tome-sync/session", session)
        if not ok or not result or (type(code) == "number" and code >= 300) then
            table.insert(remaining, session)
        end
    end

    self.pending_sessions = remaining
    G_reader_settings:saveSetting("tomesync_pending_sessions", remaining)
    if #remaining == 0 then
        logger.info("TomeSync: all pending sessions flushed")
    else
        logger.info("TomeSync:", #remaining, "sessions still pending")
    end
end

function TomeSync:onCloseDocument()
    if not self.enabled or not self.book_id then return end

    local pct      = self:_getCurrentPercentage()
    local cfi      = self:_getCurrentProgress()
    local duration = self.session_start and (os.time() - self.session_start) or 0
    local dev      = deviceName()

    pcall(apiRequest, "PUT", "/tome-sync/position/" .. self.book_id, {{
        progress = cfi, percentage = pct, device = dev,
    }})

    if duration > 10 then
        local uuid = string.format("%d-%d-%s", self.book_id, self.session_start or 0, dev)
        pcall(apiRequest, "POST", "/tome-sync/session", {{
            book_id          = self.book_id,
            started_at       = os.date("!%Y-%m-%dT%H:%M:%SZ", self.session_start),
            ended_at         = os.date("!%Y-%m-%dT%H:%M:%SZ", os.time()),
            duration_seconds = duration,
            progress_start   = self.progress_start,
            progress_end     = pct,
            pages_turned     = self.page_count,
            device           = dev,
            session_uuid     = uuid,
        }})
    end

    self.book_id        = nil
    self.session_start  = nil
    self.page_count     = 0
    self.progress_start = nil
    self.last_progress  = nil
end

-- ── Helpers ──────────────────────────────────────────────────────────────────

function TomeSync:_tryResolve()
    local doc = self.ui and self.ui.document
    if not doc then return end
    local filename = doc.file:match("([^/]+)$") or doc.file
    logger.info("TomeSync: resolving filename:", filename)
    local rok, result, rcode = pcall(apiRequest, "GET",
        "/tome-sync/resolve?filename=" .. urlEncode(filename))
    if rok and result and type(rcode) == "number" and rcode == 200 and result.book_id then
        self.book_id = result.book_id
        self.book_map[doc.file] = self.book_id
        G_reader_settings:saveSetting("tomesync_book_map", self.book_map)
        logger.info("TomeSync: resolved to book_id", self.book_id)
    else
        logger.dbg("TomeSync: could not resolve", filename)
    end
end

function TomeSync:_initSession()
    logger.dbg("TomeSync: book opened, id =", self.book_id)
    self.session_start = os.time()
    self.page_count    = 0

    local ok, pos, code = pcall(apiRequest, "GET", "/tome-sync/position/" .. self.book_id)
    if ok and pos and code == 200 then
        local server_pct = pos.percentage or 0
        local local_pct  = self:_getCurrentPercentage()
        if server_pct > (local_pct + 0.01) and server_pct < 0.99 then
            self.progress_start = server_pct
            UIManager:show(InfoMessage:new{{
                text = string.format(
                    "TomeSync: Server at %.0f%% (device: %.0f%%).",
                    server_pct * 100, local_pct * 100
                ),
                timeout = 3,
            }})
            if pos.progress and self.ui and self.ui.rolling then
                pcall(function()
                    self.ui.rolling:onGotoXPointer(pos.progress, pos.progress)
                end)
            end
        else
            self.progress_start = local_pct
        end
    else
        self.progress_start = self:_getCurrentPercentage()
    end
    self.last_progress = self.progress_start
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
    pcall(apiRequest, "PUT", "/tome-sync/position/" .. self.book_id, {{
        progress = self:_getCurrentProgress(), percentage = pct, device = deviceName(),
    }})
end

function TomeSync:registerBookId(file_path, book_id)
    self.book_map[file_path] = book_id
    G_reader_settings:saveSetting("tomesync_book_map", self.book_map)
    logger.info("TomeSync: registered book_id", book_id, "for", file_path)
end

-- ── Series download ─────────────────────────────────────────────────────────

function TomeSync:_downloadSeriesBooks(series_name, books, min_index)
    local base_dir = G_reader_settings:readSetting("download_dir")
                  or G_reader_settings:readSetting("lastdir")
    if not base_dir then
        UIManager:show(InfoMessage:new{{
            text = "No download directory configured.",
            timeout = 4,
        }})
        return
    end

    local safe_name = util.getSafeFilename(series_name)
    local series_dir = base_dir .. "/" .. safe_name
    lfs.mkdir(series_dir)

    local downloaded, skipped, failed = 0, 0, 0

    for _, book in ipairs(books) do
        -- Skip books at or before min_index (for "download rest")
        if min_index and book.series_index and book.series_index <= min_index then
            skipped = skipped + 1
        else
            local file = pickBestFile(book.files)
            if not file then
                failed = failed + 1
            else
                local ext = file.format or "epub"
                local fname = util.getSafeFilename(book.title .. "." .. ext)
                local dest = series_dir .. "/" .. fname

                -- Skip if already exists
                if lfs.attributes(dest) then
                    skipped = skipped + 1
                else
                    local ok, err = downloadFile(book.id, file.id, dest)
                    if ok then
                        downloaded = downloaded + 1
                        -- Register in book_map so sync works
                        self.book_map[dest] = book.id
                    else
                        logger.warn("TomeSync: download failed for", book.title, err)
                        failed = failed + 1
                    end
                end
            end
        end
    end

    -- Persist book_map
    G_reader_settings:saveSetting("tomesync_book_map", self.book_map)

    UIManager:show(InfoMessage:new{{
        text = string.format(
            "%s\\n\\nDownloaded: %d\\nSkipped: %d\\nFailed: %d\\n\\nSaved to: %s",
            series_name, downloaded, skipped, failed, series_dir
        ),
        timeout = 8,
    }})
end

function TomeSync:_browseSeriesMenu()
    if not NetworkMgr:isConnected() then
        UIManager:show(InfoMessage:new{{
            text = "WiFi not connected.",
            timeout = 3,
        }})
        return
    end

    local ok, series_list, code = pcall(apiRequest, "GET", "/tome-sync/series")
    if not ok or not series_list or (type(code) == "number" and code >= 300) then
        UIManager:show(InfoMessage:new{{
            text = "Failed to load series list.",
            timeout = 4,
        }})
        return
    end

    local items = {{}}
    for _, s in ipairs(series_list) do
        local text = s.name .. " (" .. s.book_count .. ")"
        if s.author then
            text = text .. " - " .. s.author
        end
        table.insert(items, {{
            text = text,
            callback = function()
                -- Fetch books in this series
                local ok2, data, code2 = pcall(apiRequest, "GET",
                    "/tome-sync/series/" .. s.first_book_id)
                if ok2 and data and data.books then
                    self:_downloadSeriesBooks(data.series_name, data.books, nil)
                else
                    UIManager:show(InfoMessage:new{{
                        text = "Failed to load series books.",
                        timeout = 4,
                    }})
                end
            end,
        }})
    end

    local menu = Menu:new{{
        title = "Series Browser",
        item_table = items,
        width = Device.screen:getWidth() - 20,
        height = Device.screen:getHeight() - 20,
        show_parent = self.ui or UIManager,
    }}
    UIManager:show(menu)
end

function TomeSync:_downloadCurrentBookSeries(rest_only)
    if not self.book_id then
        UIManager:show(InfoMessage:new{{
            text = "No book resolved. Open a book first.",
            timeout = 3,
        }})
        return
    end

    local ok, data, code = pcall(apiRequest, "GET",
        "/tome-sync/series/" .. self.book_id)
    if not ok or not data or not data.books then
        UIManager:show(InfoMessage:new{{
            text = "Failed to load series (book may not belong to one).",
            timeout = 4,
        }})
        return
    end

    local min_index = nil
    if rest_only then
        -- Find current book's series_index
        for _, b in ipairs(data.books) do
            if b.id == self.book_id then
                min_index = b.series_index
                break
            end
        end
    end

    self:_downloadSeriesBooks(data.series_name, data.books, min_index)
end

-- ── Menu ─────────────────────────────────────────────────────────────────────

function TomeSync:addToMainMenu(menu_items)
    local in_book = self.ui and self.ui.document

    local sub_items = {{}}

    -- Always-visible items
    table.insert(sub_items, {{
        text     = "Browse series",
        callback = function() self:_browseSeriesMenu() end,
    }})
    table.insert(sub_items, {{
        text     = "Test connection",
        callback = function()
            local ok, result, code = pcall(apiRequest, "GET", "/health")
            if ok and type(code) == "number" and code >= 200 and code < 300 then
                UIManager:show(InfoMessage:new{{
                    text = "Connected to " .. SERVER_URL
                           .. "\\nUser: " .. USERNAME,
                    timeout = 4,
                }})
            else
                local err = tostring(result or "unknown error")
                UIManager:show(InfoMessage:new{{
                    text = "Connection failed!\\n" .. SERVER_URL
                           .. "\\nError: " .. err,
                    timeout = 6,
                }})
            end
        end,
    }})
    table.insert(sub_items, {{
        text     = "Re-resolve all books",
        callback = function()
            self.book_map = {{}}
            self.book_id = nil
            G_reader_settings:saveSetting("tomesync_book_map", {{}})
            UIManager:show(InfoMessage:new{{
                text = "All book mappings cleared.\\nRe-open a book to re-resolve.",
                timeout = 3,
            }})
        end,
    }})
    table.insert(sub_items, {{
        text     = "About",
        separator = in_book,
        callback = function()
            UIManager:show(InfoMessage:new{{
                text    = "TomeSync v" .. PLUGIN_VERSION
                          .. "\\nSyncs with your Tome library.",
                timeout = 4,
            }})
        end,
    }})

    -- In-book items
    if in_book then
        table.insert(sub_items, {{
            text     = "Download full series",
            callback = function() self:_downloadCurrentBookSeries(false) end,
        }})
        table.insert(sub_items, {{
            text     = "Download rest of series",
            callback = function() self:_downloadCurrentBookSeries(true) end,
        }})
        table.insert(sub_items, {{
            text         = "Sync now",
            callback     = function()
                if self.book_id then
                    self:_pushPosition()
                end
                self:_flushPendingSessions()
                local pending = #self.pending_sessions
                local msg
                if self.book_id then
                    local pct = self:_getCurrentPercentage()
                    msg = string.format("Synced: %.1f%%", pct * 100)
                else
                    msg = "Book not resolved (position not synced)"
                end
                if pending > 0 then
                    msg = msg .. string.format("\\n%d session(s) still pending", pending)
                end
                UIManager:show(InfoMessage:new{{
                    text = msg,
                    timeout = 4,
                }})
            end,
        }})
        table.insert(sub_items, {{
            text = self.enabled and "Enabled (tap to disable)" or "Disabled (tap to enable)",
            callback = function()
                self.enabled = not self.enabled
                UIManager:show(InfoMessage:new{{
                    text    = "TomeSync " .. (self.enabled and "enabled" or "disabled"),
                    timeout = 2,
                }})
            end,
        }})
        table.insert(sub_items, {{
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
                    UIManager:show(InfoMessage:new{{
                        text = "No pending sessions.",
                        timeout = 3,
                    }})
                else
                    local lines = string.format("%d session(s) waiting to sync.\\n", n)
                    for i, s in ipairs(self.pending_sessions) do
                        if i > 5 then lines = lines .. "\\n..."; break end
                        lines = lines .. string.format("\\n%s (%s)",
                            s.started_at or "?", s.device or "?")
                    end
                    UIManager:show(InfoMessage:new{{
                        text = lines,
                        timeout = 8,
                    }})
                end
            end,
        }})
    end

    menu_items.tomesync = {{
        text         = "TomeSync",
        sorting_hint = "search",
        sub_item_table = sub_items,
    }}
end

logger.info("TomeSync: main.lua loaded successfully, returning plugin class")
return TomeSync
'''
