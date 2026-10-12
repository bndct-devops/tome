import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from backend.core.database import engine, Base, init_fts, backfill_fts
from backend.core.config import settings

logger = logging.getLogger(__name__)

from backend.api import health, auth, books, libraries, book_types
from backend.api import devices as devices_api
from backend.api import telemetry as telemetry_api
from backend.api import users  # noqa: F401
from backend.api import downloads
from backend.api import opds
from backend.api import opds_pins
from backend.api import kosync
from backend.api import tome_sync
from backend.api import stats
from backend.api import reading_calendar as reading_calendar_api
from backend.api import meta as meta_api
from backend.api import admin_covers
from backend.api import reading_import
from backend.api import admin_backup
from backend.api import share as share_api
from backend.services import outbound_notifications  # noqa: F401  (registers ORM fanout listeners)
from backend.api import quick_connect
from backend.api import admin_duplicates
from backend.api import word_count as word_count_api
from backend.api import home
from backend.api import bindery
from backend.api import api_tokens
from backend.api import series as series_api
from backend.api import send_to_device
from backend.api import wishlist as wishlist_api
from backend.api import notifications as notifications_api
from backend.api import oidc as oidc_api
from backend.api import goals as goals_api
from backend.api import annotations as annotations_api
from backend.api import sync_code as sync_code_api
from backend.api import hardcover as hardcover_api
from backend.api import ai as ai_api
from backend.models.kosync import KOSyncUser, KOSyncProgress, OPDSPendingLink, ReadingHistory  # noqa: F401
from backend.models.opds_pin import OpdsPin  # noqa: F401
from backend.models.tome_sync import ApiKey, ReadingSession, TomeSyncPosition  # noqa: F401
from backend.models.user_book_status import UserBookStatus  # noqa: F401
from backend.models.audit_log import AuditLog  # noqa: F401
from backend.models.quick_connect import QuickConnectCode  # noqa: F401
from backend.models.duplicate_dismissal import DuplicateDismissal  # noqa: F401
from backend.models.api_token import ApiToken  # noqa: F401
from backend.models.series_meta import Arc, SeriesMeta  # noqa: F401
from backend.models.user_device import UserDevice  # noqa: F401
from backend.models.wish import Wish  # noqa: F401
from backend.models.notification import Notification  # noqa: F401
from backend.models.reading_goal import ReadingGoal  # noqa: F401
from backend.models.book import BookChapter  # noqa: F401
from backend.models.ai_usage import AIUsage  # noqa: F401
from backend.models.ai_chapter_digest import AIChapterDigest  # noqa: F401
from backend.models.ai_entity import AIEntity, AIEntityNote  # noqa: F401
from backend.models.ai_recap import AIRecap  # noqa: F401
from backend.models.ai_job import AIJob  # noqa: F401
from backend.models.ai_reconcile_verdict import AIReconcileVerdict  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    # A staged restore was applied before the engine existed (see
    # instance_backup.apply_staged_restore_if_present) — it left a marker
    # because it could not write the audit entry itself. Log it now, into the
    # RESTORED database, so the restore shows up in the audit trail.
    _restore_marker = settings.data_dir / "restore_applied.json"
    if _restore_marker.is_file():
        try:
            import json as _json
            from backend.core.database import SessionLocal as _SL
            from backend.services.audit import audit as _audit
            _info = _json.loads(_restore_marker.read_text())
            _sdb = _SL()
            try:
                _audit(_sdb, "backup.restore_applied", username="system", details=_info)
            finally:
                _sdb.close()
        except Exception:  # noqa: BLE001 — marker cleanup must never block startup
            pass
        _restore_marker.unlink(missing_ok=True)
    # Add columns that create_all can't add to existing tables
    from sqlalchemy import text, inspect
    with engine.connect() as conn:
        # share_links grew series/book targets while unreleased. The original
        # shape had saved_filter_id NOT NULL, which SQLite cannot relax via
        # ALTER — rebuild the table (feature unreleased: only dev DBs can have
        # the old shape, and links are cheap to re-mint).
        sl_info = conn.execute(text("PRAGMA table_info(share_links)")).fetchall()
        sl_notnull = {r[1]: bool(r[3]) for r in sl_info}
        if sl_info and (sl_notnull.get("saved_filter_id") or "series_name" not in sl_notnull or "expires_at" not in sl_notnull):
            conn.execute(text("DROP TABLE share_links"))
            conn.commit()
            from backend.models.library import ShareLink as _SL
            _SL.__table__.create(bind=conn)
            conn.commit()
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(books)")).fetchall()}
        if "content_type" not in cols:
            conn.execute(text("ALTER TABLE books ADD COLUMN content_type VARCHAR(16) DEFAULT 'volume' NOT NULL"))
            conn.commit()
        # Add is_reviewed — default 1 so ALL existing books are considered already reviewed
        if "is_reviewed" not in cols:
            conn.execute(text("ALTER TABLE books ADD COLUMN is_reviewed BOOLEAN NOT NULL DEFAULT 1"))
            conn.commit()
        # Add word_count — NULL for existing books until backfilled (Admin → Word Counts)
        if "word_count" not in cols:
            conn.execute(text("ALTER TABLE books ADD COLUMN word_count INTEGER"))
            conn.commit()
        # Intrinsic PDF/CBZ page count (EPUB stays NULL by design) — filled at
        # ingest, backfilled by the same admin job as word counts.
        if "page_count" not in cols:
            conn.execute(text("ALTER TABLE books ADD COLUMN page_count INTEGER"))
            conn.commit()
        # Chapter-extraction attempt marker (see Book.chapters_extracted_at).
        # Distinguishes "never tried" from "tried, no usable TOC" so TOC-less
        # EPUBs stop re-queuing in the backfill. Books that already have
        # chapter rows are marked done, so upgraded installs don't re-extract
        # their whole library once.
        if "chapters_extracted_at" not in cols:
            conn.execute(text("ALTER TABLE books ADD COLUMN chapters_extracted_at DATETIME"))
            conn.execute(text(
                "UPDATE books SET chapters_extracted_at = CURRENT_TIMESTAMP "
                "WHERE id IN (SELECT DISTINCT book_id FROM book_chapters)"
            ))
            conn.commit()
        user_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
        if "role" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'guest'"))
            conn.execute(text("UPDATE users SET role = 'admin' WHERE is_admin = 1"))
            conn.execute(text(
                "UPDATE users SET role = 'member' "
                "WHERE is_admin = 0 AND id IN ("
                "  SELECT user_id FROM user_permissions WHERE can_upload = 1"
                ")"
            ))
            conn.commit()
        # OIDC/SSO provenance columns. auth_source defaults 'local' so every
        # pre-existing account stays a password login; oidc_sub/oidc_issuer are
        # NULL until an account is linked or provisioned via SSO.
        if "auth_source" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN auth_source VARCHAR(16) NOT NULL DEFAULT 'local'"))
            conn.commit()
        if "oidc_sub" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN oidc_sub VARCHAR(255)"))
            conn.commit()
        if "oidc_issuer" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN oidc_issuer VARCHAR(255)"))
            conn.commit()
        # Content restrictions (issue #190). Both nullable, so every existing
        # account stays unrestricted and unlimited. download_events itself is
        # created by create_all.
        if "excluded_tags" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN excluded_tags TEXT"))
            conn.commit()
        if "download_limit" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN download_limit INTEGER"))
            conn.commit()
        # Migrate api_keys.key (plaintext) → key_hash (sha256) + key_prefix (display)
        # so a DB leak doesn't compromise any KOReader plugin install. One-way: existing
        # installed plugins keep working because they send the plaintext, we hash on lookup.
        #
        # Idempotent: handles partial-prior-attempt states (e.g. container restart mid
        # migration) by re-backfilling any rows that are missing key_hash.
        import hashlib as _hashlib
        ak_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(api_keys)")).fetchall()}
        if "key_hash" not in ak_cols:
            conn.execute(text("ALTER TABLE api_keys ADD COLUMN key_hash VARCHAR(64)"))
            ak_cols.add("key_hash")
        if "key_prefix" not in ak_cols:
            conn.execute(text("ALTER TABLE api_keys ADD COLUMN key_prefix VARCHAR(16)"))
            ak_cols.add("key_prefix")
        # If the old plaintext column still exists, backfill any rows missing key_hash.
        if "key" in ak_cols:
            rows = conn.execute(
                text("SELECT id, key FROM api_keys WHERE key_hash IS NULL OR key_hash = ''")
            ).fetchall()
            for row in rows:
                key_id, plaintext = row[0], row[1]
                if not plaintext:
                    continue
                conn.execute(
                    text("UPDATE api_keys SET key_hash = :h, key_prefix = :p WHERE id = :i"),
                    {
                        "h": _hashlib.sha256(plaintext.encode()).hexdigest(),
                        "p": plaintext[:11],
                        "i": key_id,
                    },
                )
            # Drop the old plaintext column. The legacy model had index=True on
            # `key`, leaving behind ix_api_keys_key that blocks DROP COLUMN unless
            # we kill it first. Same for the UNIQUE constraint's implicit index.
            conn.execute(text("DROP INDEX IF EXISTS ix_api_keys_key"))
            conn.execute(text("DROP INDEX IF EXISTS sqlite_autoindex_api_keys_1"))
            conn.execute(text("ALTER TABLE api_keys DROP COLUMN key"))
        # Safety: any rows still missing key_hash after this point are leftovers from a
        # failed migration attempt (e.g. when the original `key` column was already dropped
        # but the backfill didn't run). They can never authenticate — drop them so users
        # can regenerate fresh keys via Settings → Download Plugin.
        conn.execute(text("DELETE FROM api_keys WHERE key_hash IS NULL OR key_hash = ''"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_api_keys_key_hash ON api_keys (key_hash)"))
        conn.commit()
        # Add scope column to api_tokens
        at_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(api_tokens)")).fetchall()}
        if "scope" not in at_cols:
            conn.execute(text("ALTER TABLE api_tokens ADD COLUMN scope VARCHAR(16) NOT NULL DEFAULT 'full'"))
            conn.commit()
        # Quick Connect poll capability. Pre-existing rows keep NULL — they can
        # never be polled again, which is fine: codes live for five minutes.
        qc_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(quick_connect_codes)")).fetchall()}
        if qc_cols and "poll_token" not in qc_cols:
            conn.execute(text("ALTER TABLE quick_connect_codes ADD COLUMN poll_token VARCHAR(64)"))
            conn.commit()
        # Enforce one reading-position row per (user, book). Concurrent device
        # and web first-writes could previously both INSERT, leaving duplicate
        # rows that made position reads flap and double-counted stats joins.
        # Dedupe (keep the freshest) before adding the constraint so it can't
        # fail on legacy data.
        tsp_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(tome_sync_positions)")).fetchall()}
        if tsp_cols:
            conn.execute(text("""
                DELETE FROM tome_sync_positions
                WHERE id NOT IN (
                    SELECT keep_id FROM (
                        SELECT id AS keep_id, ROW_NUMBER() OVER (
                            PARTITION BY user_id, book_id
                            ORDER BY updated_at DESC, id DESC
                        ) AS rn
                        FROM tome_sync_positions
                    ) WHERE rn = 1
                )
            """))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_tspos_user_book "
                "ON tome_sync_positions (user_id, book_id)"
            ))
            conn.commit()
        # Per-user ratings/reviews on user_book_status (nullable — existing rows stay unrated).
        ubs_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(user_book_status)")).fetchall()}
        if ubs_cols and "rating" not in ubs_cols:
            conn.execute(text("ALTER TABLE user_book_status ADD COLUMN rating INTEGER"))
            conn.execute(text("ALTER TABLE user_book_status ADD COLUMN review TEXT"))
            conn.execute(text("ALTER TABLE user_book_status ADD COLUMN rated_at DATETIME"))
            conn.commit()
        # Explicit finish date — updated_at also moves on rating/review/CFI writes,
        # so it can't be shown as "Finished". Backfill from updated_at (the best
        # available guess) for rows already marked read.
        if ubs_cols and "finished_at" not in ubs_cols:
            conn.execute(text("ALTER TABLE user_book_status ADD COLUMN finished_at DATETIME"))
            conn.execute(text(
                "UPDATE user_book_status SET finished_at = updated_at WHERE status = 'read'"
            ))
            conn.commit()
        # reading_goals from the parked feat/reading-goals WIP (never shipped) lacks
        # book_type_id and carries a stale (user_id, kind) unique constraint that
        # SQLite can't alter away — recreate the table on the current schema.
        rg_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(reading_goals)")).fetchall()}
        if rg_cols and "book_type_id" not in rg_cols:
            conn.execute(text("DROP TABLE reading_goals"))
            conn.commit()
        # Composite index for the re-reads group-by over (user, book, page) —
        # create_all only builds indexes for brand-new tables.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_ko_page_stats_user_book_page "
            "ON ko_page_stats (user_id, book_id, page)"
        ))
        # Range seek for the per-sitting overlap probe in reconciled_reading.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_ko_page_stats_user_book_start "
            "ON ko_page_stats (user_id, book_id, start_time)"
        ))
        conn.commit()
        # Web-created annotations carry an EPUB CFI so the web reader can
        # re-paint them without a text search (annotations table pre-exists).
        an_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(annotations)")).fetchall()}
        if an_cols and "cfi" not in an_cols:
            conn.execute(text("ALTER TABLE annotations ADD COLUMN cfi TEXT"))
            conn.commit()
        # Clock-offset guard: marks LWW stamps minted by the server's clock (web
        # create/edit/delete) so plugin syncs can shift them into the device frame.
        # Pre-existing rows default 0 (device-minted) — the safe assumption.
        if an_cols and "server_minted" not in an_cols:
            conn.execute(text("ALTER TABLE annotations ADD COLUMN server_minted BOOLEAN NOT NULL DEFAULT 0"))
            conn.commit()
        at_tomb_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(annotation_tombstones)")).fetchall()}
        if at_tomb_cols and "server_minted" not in at_tomb_cols:
            conn.execute(text("ALTER TABLE annotation_tombstones ADD COLUMN server_minted BOOLEAN NOT NULL DEFAULT 0"))
            conn.commit()
        # Release detection stores the tracker's latest volume title + date on
        # follows (wishes table pre-exists on upgraded installs).
        wi_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(wishes)")).fetchall()}
        if wi_cols and "latest_known_title" not in wi_cols:
            conn.execute(text("ALTER TABLE wishes ADD COLUMN latest_known_title VARCHAR(512)"))
            conn.execute(text("ALTER TABLE wishes ADD COLUMN latest_release_date VARCHAR(10)"))
            conn.commit()
        # Hardcover sync — per-user credentials (users), book-level match
        # identity (books), and per-user-per-book push state (user_book_status).
        # All nullable, so existing rows are untouched. Note ratings themselves
        # need no migration: the rating columns keep their INTEGER declaration
        # and SQLite's NUMERIC affinity stores new half-star values as REAL.
        if "hardcover_token" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN hardcover_token TEXT"))
            conn.execute(text("ALTER TABLE users ADD COLUMN hardcover_user_id INTEGER"))
            conn.execute(text("ALTER TABLE users ADD COLUMN hardcover_username VARCHAR(255)"))
            conn.execute(text("ALTER TABLE users ADD COLUMN hardcover_token_status VARCHAR(16)"))
            conn.execute(text("ALTER TABLE users ADD COLUMN hardcover_linked_at DATETIME"))
            conn.execute(text("ALTER TABLE users ADD COLUMN hardcover_sync_enabled BOOLEAN NOT NULL DEFAULT 0"))
            conn.commit()
        # AI features: the user's own provider key (encrypted) and when it was set.
        if "ai_api_key" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN ai_api_key TEXT"))
            conn.execute(text("ALTER TABLE users ADD COLUMN ai_key_set_at DATETIME"))
            conn.commit()
        # AI spend limit per user (run 2c). Nullable: no limit.
        if "ai_monthly_limit_usd" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN ai_monthly_limit_usd FLOAT"))
            conn.commit()
        # An admin's cap on a user, apart from the user's own limit.
        if "ai_admin_limit_usd" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN ai_admin_limit_usd FLOAT"))
            conn.commit()
        # AI usage rows name their job and book, and how long the call took
        # (run 2c: jobs view, per-book spend, the digest time estimate).
        au_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(ai_usage)")).fetchall()}
        if au_cols and "job_id" not in au_cols:
            conn.execute(text("ALTER TABLE ai_usage ADD COLUMN job_id VARCHAR(12)"))
            conn.commit()
        if au_cols and "book_id" not in au_cols:
            conn.execute(text(
                "ALTER TABLE ai_usage ADD COLUMN book_id INTEGER REFERENCES books(id) ON DELETE SET NULL"))
            conn.commit()
        if au_cols and "duration_ms" not in au_cols:
            conn.execute(text("ALTER TABLE ai_usage ADD COLUMN duration_ms INTEGER"))
            conn.commit()
        if au_cols and "words_sent" not in au_cols:
            conn.execute(text("ALTER TABLE ai_usage ADD COLUMN words_sent INTEGER"))
            conn.execute(text("ALTER TABLE ai_usage ADD COLUMN chapters INTEGER"))
            conn.commit()
        if au_cols:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ai_usage_job_id ON ai_usage (job_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ai_usage_book_id ON ai_usage (book_id)"))
            conn.commit()
        # AI jobs report the step they are on (run 2e: reading, merging, writing).
        aj_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(ai_jobs)")).fetchall()}
        if aj_cols and "step" not in aj_cols:
            conn.execute(text("ALTER TABLE ai_jobs ADD COLUMN step VARCHAR(16)"))
            conn.execute(text("ALTER TABLE ai_jobs ADD COLUMN step_done INTEGER NOT NULL DEFAULT 0"))
            conn.execute(text("ALTER TABLE ai_jobs ADD COLUMN step_total INTEGER NOT NULL DEFAULT 0"))
            conn.commit()
        # Who merged an entity (run 2e): "reconcile" (the reconcile pass), else null.
        ae_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(ai_entities)")).fetchall()}
        if ae_cols and "merged_by" not in ae_cols:
            conn.execute(text("ALTER TABLE ai_entities ADD COLUMN merged_by VARCHAR(16)"))
            conn.commit()
        # A multi-chapter digest records the last chapter its call saw.
        acd_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(ai_chapter_digests)")).fetchall()}
        if acd_cols and "context_end_idx" not in acd_cols:
            conn.execute(text("ALTER TABLE ai_chapter_digests ADD COLUMN context_end_idx INTEGER"))
            conn.commit()
        # A recap records which volumes it was written without digests for.
        ar_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(ai_recaps)")).fetchall()}
        if ar_cols and "missing_json" not in ar_cols:
            conn.execute(text("ALTER TABLE ai_recaps ADD COLUMN missing_json TEXT"))
            conn.commit()
        if "hardcover_book_id" not in cols:
            conn.execute(text("ALTER TABLE books ADD COLUMN hardcover_book_id INTEGER"))
            conn.execute(text("ALTER TABLE books ADD COLUMN hardcover_edition_id INTEGER"))
            conn.execute(text("ALTER TABLE books ADD COLUMN hardcover_pages INTEGER"))
            conn.execute(text("ALTER TABLE books ADD COLUMN hardcover_match_method VARCHAR(16)"))
            conn.execute(text("ALTER TABLE books ADD COLUMN hardcover_matched_at DATETIME"))
            conn.commit()
        if "hardcover_slug" not in cols:
            conn.execute(text("ALTER TABLE books ADD COLUMN hardcover_slug VARCHAR(255)"))
            conn.commit()
        ubs_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(user_book_status)")).fetchall()}
        if ubs_cols and "hardcover_user_book_id" not in ubs_cols:
            conn.execute(text("ALTER TABLE user_book_status ADD COLUMN hardcover_user_book_id INTEGER"))
            conn.execute(text("ALTER TABLE user_book_status ADD COLUMN hardcover_read_id INTEGER"))
            conn.execute(text("ALTER TABLE user_book_status ADD COLUMN hardcover_synced_rating FLOAT"))
            conn.execute(text("ALTER TABLE user_book_status ADD COLUMN hardcover_synced_pct FLOAT"))
            conn.execute(text("ALTER TABLE user_book_status ADD COLUMN hardcover_synced_status VARCHAR(16)"))
            conn.execute(text("ALTER TABLE user_book_status ADD COLUMN hardcover_synced_at DATETIME"))
            conn.execute(text("ALTER TABLE user_book_status ADD COLUMN hardcover_error VARCHAR(255)"))
            conn.execute(text("ALTER TABLE user_book_status ADD COLUMN hardcover_fail_count INTEGER NOT NULL DEFAULT 0"))
            conn.commit()
        # Ownership of the Hardcover entry (#227). Existing rows default to 0 =
        # "not ours": their origin cannot be recovered, and treating an unknown
        # entry as the user's own is the safe direction — Tome never deletes it.
        if ubs_cols and "hardcover_created" not in ubs_cols:
            conn.execute(text(
                "ALTER TABLE user_book_status ADD COLUMN hardcover_created BOOLEAN NOT NULL DEFAULT 0"))
            conn.commit()
        # Hardcover Want-to-Read pull resolves shelf entries by matched book id
        # every cycle — create_all only builds indexes for brand-new tables, and
        # this must run after the hardcover_book_id column-add above.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_books_hardcover_book_id "
            "ON books (hardcover_book_id)"
        ))
        conn.commit()
    ReadingGoal.__table__.create(bind=engine, checkfirst=True)
    init_fts(engine)
    backfill_fts(engine)
    settings.ensure_dirs()
    settings.resolve_secret_key()
    # Seed default book types (no-op if already seeded)
    from backend.core.database import SessionLocal
    from backend.services.book_types import seed_book_types
    with SessionLocal() as db:
        seed_book_types(db)

    # AI digest jobs live in memory: any left queued/running/stopping by the
    # last process are over. Mark them interrupted (the UI offers Continue;
    # nothing resumes on its own).
    from backend.services.ai import digest_job as _digest_job
    try:
        with SessionLocal() as db:
            _n = _digest_job.mark_interrupted(db)
        if _n:
            logger.info("Marked %d AI job(s) interrupted by the restart", _n)
    except Exception:
        logger.exception("Marking interrupted AI jobs failed (continuing startup)")

    # Repair collateral from the first 2.2.0 build's Hardcover shelf mirror
    # (wrongly-dismissed follows/wishes; see repair_shelf_sync_collateral).
    # Idempotent, no-op on clean installs.
    from backend.services.hardcover_sync import repair_shelf_sync_collateral
    try:
        with SessionLocal() as db:
            repair_shelf_sync_collateral(db)
    except Exception:
        logger.exception("Hardcover shelf-sync repair failed (continuing startup)")

    # Start background auto-import task if enabled
    auto_import_task: asyncio.Task | None = None
    if settings.auto_import:
        logger.info(
            "Auto-import enabled — scanning bindery every %d seconds",
            settings.auto_import_interval,
        )
        auto_import_task = asyncio.create_task(_auto_import_loop())
    else:
        logger.info("Auto-import disabled (set TOME_AUTO_IMPORT=true to enable)")

    # Opt-in telemetry: a daily check that sends the monthly report when it is
    # due and consent is current. Does nothing at all otherwise.
    telemetry_task = asyncio.create_task(_telemetry_loop())

    # One-shot backfill of KOReader partial-MD5s for pre-existing library
    # files (new files are hashed at ingest/serve time). No-op once done.
    import threading
    from backend.services.ko_hash import backfill_missing_raw_hashes
    threading.Thread(target=backfill_missing_raw_hashes, daemon=True,
                     name="ko-hash-backfill").start()

    # One-shot repair of KOReader-history matches the pre-volume-guard fuzzy
    # matcher attributed to the wrong sibling volume (issue #152). Idempotent:
    # corrected rows no longer qualify, so later startups are no-ops.
    from backend.services.ko_stats_import import repair_fuzzy_matches_startup
    threading.Thread(target=repair_fuzzy_matches_startup, daemon=True,
                     name="ko-stats-repair").start()

    release_task: asyncio.Task | None = None
    if settings.release_detection:
        logger.info("Release detection enabled — checking follows every %d seconds",
                    settings.release_check_interval)
        release_task = asyncio.create_task(_release_check_loop())

    # Hardcover sync worker (inert until a user links a personal token).
    hardcover_task: asyncio.Task | None = None
    if settings.hardcover_sync_enabled:
        from backend.services.hardcover_sync import hardcover_sync_loop
        hardcover_task = asyncio.create_task(hardcover_sync_loop())

    yield

    # Shutdown: cancel the background tasks cleanly
    for task in (auto_import_task, release_task, hardcover_task, telemetry_task):
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def _auto_import_loop() -> None:
    """Background task: periodically import files from the bindery into the library."""
    while True:
        try:
            await asyncio.sleep(settings.auto_import_interval)
            await _run_auto_import()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unhandled error in auto-import loop")


async def _telemetry_loop() -> None:
    """Background task: once a day, send the telemetry report if it is due."""
    from backend.core.database import SessionLocal
    from backend.services.telemetry import send_if_due

    def _tick() -> str:
        with SessionLocal() as db:
            return send_if_due(db)

    await asyncio.sleep(120)  # let startup settle
    while True:
        try:
            result = await asyncio.to_thread(_tick)
            if result == "sent":
                logger.info("Telemetry report sent")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unhandled error in telemetry loop")
        await asyncio.sleep(24 * 3600)


async def _release_check_loop() -> None:
    """Background task: poll Hardcover for new volumes of followed series.

    The per-follow due check lives in check_follows (last_checked_at vs the
    configured interval); this loop just wakes hourly to see if anything is due,
    so a long interval doesn't need a long sleep to survive restarts.
    """
    from backend.core.database import SessionLocal
    from backend.services.release_detection import check_follows
    while True:
        try:
            await asyncio.sleep(min(3600, settings.release_check_interval))
            with SessionLocal() as db:
                result = await check_follows(db)
            if result.get("notified"):
                logger.info("Release detection: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unhandled error in release-check loop")


async def _run_auto_import() -> None:
    """One auto-import tick; the work lives in backend/services/auto_import.py."""
    from backend.services.auto_import import run_auto_import
    await run_auto_import()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Tome",
        description="Self-hosted ebook library",
        version="1.7.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],  # Vite dev server
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # SessionMiddleware backs the OIDC handshake (transient state/nonce/PKCE).
    # Cookie carries only short-lived OAuth state; cleared after the callback.
    # Mark it Secure when the public origin is https so it survives a proxied
    # TLS deployment without breaking plain-http local dev.
    from starlette.middleware.sessions import SessionMiddleware
    settings.ensure_dirs()
    _session_secure = (
        (settings.oidc_redirect_url or settings.public_url or "").lower().startswith("https")
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.resolve_secret_key(),
        same_site="lax",
        https_only=_session_secure,
        max_age=600,
    )

    # API routes
    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(devices_api.router, prefix="/api")
    app.include_router(telemetry_api.router, prefix="/api")
    app.include_router(home.router, prefix="/api")
    app.include_router(books.router, prefix="/api")
    app.include_router(libraries.router, prefix="/api")
    app.include_router(book_types.router, prefix="/api")
    app.include_router(users.router, prefix="/api")
    app.include_router(downloads.router, prefix="/api")
    app.include_router(opds.router)  # mounted at /opds, not /api
    app.include_router(opds_pins.router, prefix="/api")
    app.include_router(kosync.router, prefix="/api")  # mounted at /api/v1/
    app.include_router(tome_sync.router, prefix="/api")
    app.include_router(reading_calendar_api.router, prefix="/api")
    app.include_router(stats.router, prefix="/api")
    app.include_router(quick_connect.router, prefix="/api")
    app.include_router(admin_duplicates.router, prefix="/api")
    app.include_router(word_count_api.router, prefix="/api")
    app.include_router(bindery.router, prefix="/api/bindery", tags=["bindery"])
    app.include_router(api_tokens.router, prefix="/api")
    app.include_router(series_api.router, prefix="/api")
    app.include_router(hardcover_api.router, prefix="/api")
    app.include_router(send_to_device.router, prefix="/api")
    # Wishlist + notifications — static paths registered before /{id} routes
    app.include_router(wishlist_api.router, prefix="/api")
    app.include_router(notifications_api.router, prefix="/api")
    app.include_router(oidc_api.router, prefix="/api")
    app.include_router(goals_api.router, prefix="/api")
    app.include_router(meta_api.router, prefix="/api")
    app.include_router(admin_covers.router, prefix="/api")
    app.include_router(reading_import.router, prefix="/api")
    app.include_router(admin_backup.router, prefix="/api")
    app.include_router(share_api.router, prefix="/api")
    app.include_router(annotations_api.router, prefix="/api")
    app.include_router(sync_code_api.router, prefix="/api")
    app.include_router(ai_api.router, prefix="/api")
    # Every AI failure (refusal, provider down, no key, feature off) is a typed
    # exception from backend/services/ai; one handler maps them to status codes
    # so feature endpoints never repeat the mapping.
    ai_api.register_exception_handlers(app)

    # Serve frontend static files in production (SPA fallback)
    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        from starlette.responses import FileResponse as _FileResponse

        index_html = frontend_dist / "index.html"

        app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="static-assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            # Serve the actual file if it exists, otherwise index.html
            file = frontend_dist / full_path
            if full_path and file.is_file():
                return _FileResponse(str(file))
            return _FileResponse(str(index_html))

    return app


app = create_app()
