"""KOSync bridge tests (issue #156).

The KOSync-compat API serves clients that don't run the Tome plugin (stock
KOReader kosync, Readest). Covered here:

- deterministic auto-linking of KOSync documents via ko_hashes (the KOSync
  ``document`` field IS KOReader's partial-MD5, which Tome records for every
  artifact it scans or serves), with the legacy OPDS heuristic as fallback
- the read-only GET bridge: a newer web/plugin position is served to KOSync
  clients; KOSync pushes still win when they are newer
- the hard guarantee that KOSync pushes never write plugin-visible position
  state (TomeSyncPosition / PositionHistory)
- KOSync status writes route through the shared sticky-completion rule
"""
import time
from datetime import datetime, timedelta

from backend.models.book import Book
from backend.models.kosync import (
    KOSyncDocumentMap,
    KOSyncProgress,
    KOSyncUser,
    OPDSPendingLink,
)
from backend.models.ko_stats import KoHash
from backend.models.library import Library
from backend.models.tome_sync import PositionHistory, TomeSyncPosition
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.core.security import hash_password


MD5 = "d41d8cd98f00b204e9800998ecf8427e"  # any 32-hex string


def _kosync_account(db, tome_user, username=None, key="k" * 32):
    acc = KOSyncUser(username=username or tome_user.username, userkey=key,
                     user_id=tome_user.id)
    db.add(acc)
    db.flush()
    return acc


def _headers(acc):
    return {"x-auth-user": acc.username, "x-auth-key": acc.userkey}


def _put_progress(client, acc, document, percentage, progress="/body/x[1]", device="Readest"):
    return client.put("/api/v1/syncs/progress", headers=_headers(acc), json={
        "document": document,
        "percentage": percentage,
        "progress": progress,
        "device": device,
    })


# ── Auto-link via ko_hashes ───────────────────────────────────────────────────

def test_put_auto_links_via_ko_hash(db, client, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Bridge Book")
    db.add(KoHash(book_id=book.id, ko_partial_md5=MD5, kind="baked"))
    acc = _kosync_account(db, user)
    db.flush()

    r = _put_progress(client, acc, MD5, 0.42)
    assert r.status_code == 200

    m = db.query(KOSyncDocumentMap).filter_by(tome_user_id=user.id, document=MD5).one()
    assert m.book_id == book.id
    ubs = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    assert ubs.status == "reading" and ubs.progress_pct == 0.42


def test_put_never_writes_plugin_position_state(db, client, admin_user, make_book):
    # The no-interference guarantee: a KOSync push must not create or touch
    # TomeSyncPosition / PositionHistory, however it resolves.
    user, _ = admin_user
    book = make_book(title="Bridge Book")
    db.add(KoHash(book_id=book.id, ko_partial_md5=MD5, kind="baked"))
    acc = _kosync_account(db, user)
    db.flush()

    _put_progress(client, acc, MD5, 0.42)

    assert db.query(TomeSyncPosition).count() == 0
    assert db.query(PositionHistory).count() == 0


def test_put_opds_fallback_still_works(db, client, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="OPDS Book")
    db.add(OPDSPendingLink(user_id=user.id, book_id=book.id))
    acc = _kosync_account(db, user)
    db.flush()

    r = _put_progress(client, acc, MD5, 0.10)
    assert r.status_code == 200
    m = db.query(KOSyncDocumentMap).filter_by(tome_user_id=user.id, document=MD5).one()
    assert m.book_id == book.id
    assert db.query(OPDSPendingLink).count() == 0  # consumed


def test_put_hash_link_beats_opds_guess(db, client, admin_user, make_book):
    # Hash identity is exact; the pending-download guess must not override it
    # (and stays pending for a genuinely unknown document).
    user, _ = admin_user
    hashed = make_book(title="Hashed Book")
    pending = make_book(title="Pending Book")
    db.add(KoHash(book_id=hashed.id, ko_partial_md5=MD5, kind="raw"))
    db.add(OPDSPendingLink(user_id=user.id, book_id=pending.id))
    acc = _kosync_account(db, user)
    db.flush()

    _put_progress(client, acc, MD5, 0.20)

    m = db.query(KOSyncDocumentMap).filter_by(tome_user_id=user.id, document=MD5).one()
    assert m.book_id == hashed.id
    assert db.query(OPDSPendingLink).count() == 1  # not consumed by a hash hit


def test_no_auto_link_for_invisible_book(db, client, admin_user, make_book):
    # A member must not get auto-linked to a book they cannot see, even when
    # the hash matches (private library owned by someone else).
    user, _ = admin_user
    book = make_book(title="Private Book")
    lib = Library(name="Vault", is_public=False, owner_id=user.id)
    db.add(lib)
    db.flush()
    book.libraries.append(lib)
    member = User(username="kmember", email="km@example.com",
                  hashed_password=hash_password("pw12345678"),
                  is_active=True, role="member")
    db.add(member)
    db.flush()
    db.add(KoHash(book_id=book.id, ko_partial_md5=MD5, kind="raw"))
    acc = _kosync_account(db, member)
    db.flush()

    r = _put_progress(client, acc, MD5, 0.30)
    assert r.status_code == 200  # push itself succeeds (stored as raw KOSync progress)
    assert db.query(KOSyncDocumentMap).filter_by(tome_user_id=member.id).count() == 0


# ── GET bridge ────────────────────────────────────────────────────────────────

def test_get_serves_newer_tome_position(db, client, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Bridge Book")
    db.add(KoHash(book_id=book.id, ko_partial_md5=MD5, kind="baked"))
    acc = _kosync_account(db, user)
    db.flush()
    # Old KOSync push, then newer web-reader progress.
    db.add(KOSyncProgress(user_id=acc.id, document=MD5, progress="/body/x[1]",
                          percentage=0.10, device="Readest",
                          timestamp=int(time.time()) - 3600))
    db.add(TomeSyncPosition(user_id=user.id, book_id=book.id, percentage=0.55,
                            progress="epubcfi(/6/10!/4/2)", device="web",
                            updated_at=datetime.utcnow()))
    db.commit()

    r = client.get(f"/api/v1/syncs/progress/{MD5}", headers=_headers(acc))
    assert r.status_code == 200
    body = r.json()
    assert body["percentage"] == 0.55
    assert body["device"] == "web"
    assert body["document"] == MD5
    assert body["progress"]  # locator always present for client compatibility


def test_get_serves_kosync_row_when_newer(db, client, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Bridge Book")
    db.add(KoHash(book_id=book.id, ko_partial_md5=MD5, kind="baked"))
    acc = _kosync_account(db, user)
    db.flush()
    db.add(TomeSyncPosition(user_id=user.id, book_id=book.id, percentage=0.55,
                            progress="epubcfi(/6/10!/4/2)", device="web",
                            updated_at=datetime.utcnow() - timedelta(hours=2)))
    db.add(KOSyncProgress(user_id=acc.id, document=MD5, progress="/body/y[2]",
                          percentage=0.70, device="Readest",
                          timestamp=int(time.time())))
    db.commit()

    r = client.get(f"/api/v1/syncs/progress/{MD5}", headers=_headers(acc))
    body = r.json()
    assert body["percentage"] == 0.70
    assert body["device"] == "Readest"
    assert body["progress"] == "/body/y[2]"


def test_get_bridges_on_first_pull_without_any_push(db, client, admin_user, make_book):
    # The reporter's exact flow: connect Readest to a fresh KOSync account and
    # pull. No KOSync progress exists at all — the hash auto-link plus the
    # bridge must serve the web position on the very first GET.
    user, _ = admin_user
    book = make_book(title="Bridge Book")
    db.add(KoHash(book_id=book.id, ko_partial_md5=MD5, kind="baked"))
    acc = _kosync_account(db, user)
    db.flush()
    db.add(TomeSyncPosition(user_id=user.id, book_id=book.id, percentage=0.33,
                            progress="epubcfi(/6/8!/4/2)", device="web",
                            updated_at=datetime.utcnow()))
    db.commit()

    r = client.get(f"/api/v1/syncs/progress/{MD5}", headers=_headers(acc))
    body = r.json()
    assert body["percentage"] == 0.33
    # ... and the pull created the persistent document map.
    m = db.query(KOSyncDocumentMap).filter_by(tome_user_id=user.id, document=MD5).one()
    assert m.book_id == book.id


def test_get_unknown_document_empty(db, client, admin_user):
    user, _ = admin_user
    acc = _kosync_account(db, user)
    db.flush()
    r = client.get(f"/api/v1/syncs/progress/{'0' * 32}", headers=_headers(acc))
    assert r.status_code == 200
    assert r.json() == {}


def test_get_unlinked_account_unchanged(db, client, admin_user, make_book):
    # A KOSync account not linked to any Tome user keeps the pre-bridge
    # behaviour: it only ever sees its own pushes.
    user, _ = admin_user
    book = make_book(title="Bridge Book")
    db.add(KoHash(book_id=book.id, ko_partial_md5=MD5, kind="baked"))
    acc = KOSyncUser(username="standalone", userkey="s" * 32, user_id=None)
    db.add(acc)
    db.flush()
    db.add(TomeSyncPosition(user_id=user.id, book_id=book.id, percentage=0.9,
                            progress="x", device="web", updated_at=datetime.utcnow()))
    db.commit()

    r = client.get(f"/api/v1/syncs/progress/{MD5}", headers=_headers(acc))
    assert r.json() == {}
    assert db.query(KOSyncDocumentMap).count() == 0


# ── Shared status rule ────────────────────────────────────────────────────────

def test_push_finishes_at_099_not_095(db, client, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Bridge Book")
    db.add(KoHash(book_id=book.id, ko_partial_md5=MD5, kind="baked"))
    acc = _kosync_account(db, user)
    db.flush()

    _put_progress(client, acc, MD5, 0.96)
    ubs = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    assert ubs.status == "reading"  # 0.95 no longer finishes a book

    _put_progress(client, acc, MD5, 0.995)
    db.refresh(ubs)
    assert ubs.status == "read" and ubs.progress_pct == 1.0
    assert ubs.finished_at is not None


def test_push_never_unfinishes_a_read_book(db, client, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Bridge Book")
    db.add(KoHash(book_id=book.id, ko_partial_md5=MD5, kind="baked"))
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read",
                          progress_pct=1.0, finished_at=datetime.utcnow()))
    acc = _kosync_account(db, user)
    db.flush()

    _put_progress(client, acc, MD5, 0.20)  # re-read from the start

    ubs = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    assert ubs.status == "read" and ubs.progress_pct == 1.0  # sticky


def test_manual_link_endpoint_applies_shared_rule(db, client, admin_user, make_book):
    # link-kosync used to carry its own 0.95 non-sticky copy of the rule.
    user, _ = admin_user
    book = make_book(title="Linked Book")
    acc = _kosync_account(db, user)
    db.flush()
    db.add(KOSyncProgress(user_id=acc.id, document=MD5, progress="p",
                          percentage=0.96, device="Readest", timestamp=int(time.time())))
    db.commit()

    r = client.post(f"/api/books/{book.id}/link-kosync", json={"document": MD5})
    assert r.status_code == 200
    ubs = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    assert ubs.status == "reading" and ubs.progress_pct == 0.96
    m = db.query(KOSyncDocumentMap).filter_by(tome_user_id=user.id, document=MD5).one()
    assert m.book_id == book.id
