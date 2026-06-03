"""Tests for KOReader annotation sync — bidirectional across devices via Tome.

Identity is the anchor (xPointer). Edit conflicts resolve last-write-wins by the
KOReader modification time; deletes write tombstones so stale devices can't
resurrect them. Plugin endpoints use a tk_ API key; the web read uses JWT.
"""
from backend.models.tome_sync import ApiKey, Annotation, AnnotationTombstone
from backend.models.user import User
from backend.core.security import hash_password


def _api_key_for(db, user_id: int) -> str:
    plaintext = ApiKey.generate()
    db.add(ApiKey(user_id=user_id, key_hash=ApiKey.hash_key(plaintext),
                  key_prefix=plaintext[:11], label="test"))
    db.flush()
    return plaintext


def _hl(anchor, text="t", note=None, chapter="C1", color="yellow",
        dt="2026-06-03 10:00:00", dtu=None):
    return {"anchor": anchor, "highlighted_text": text, "note": note, "chapter": chapter,
            "color": color, "datetime": dt, "datetime_updated": dtu}


def _sync(client, hdr, book_id, upserts=(), deletes=()):
    return client.post(f"/api/tome-sync/annotations/{book_id}/sync", headers=hdr,
                       json={"upserts": list(upserts), "deletes": list(deletes)})


A1 = "/body/DocFragment[2]/p[1]/text().0"
A2 = "/body/DocFragment[4]/p[9]/text().12"


def test_sync_creates_and_get_returns(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}

    r = _sync(client, hdr, book.id, upserts=[_hl(A1, "hello"), _hl(A2, "world")])
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == {"created": 2, "updated": 0, "deleted": 0, "skipped": 0}

    g = client.get(f"/api/tome-sync/annotations/{book.id}", headers=hdr).json()
    assert {a["highlighted_text"] for a in g["annotations"]} == {"hello", "world"}
    assert g["tombstones"] == []


def test_union_across_devices(client, db, admin_user, make_book):
    """Two devices, different anchors -> union, neither clobbers the other."""
    user, _ = admin_user
    book = make_book()
    devA = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    devB = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}

    _sync(client, devA, book.id, upserts=[_hl(A1, "from A")])
    _sync(client, devB, book.id, upserts=[_hl(A2, "from B")])   # B doesn't send A1
    g = client.get(f"/api/tome-sync/annotations/{book.id}", headers=devA).json()
    assert {a["highlighted_text"] for a in g["annotations"]} == {"from A", "from B"}


def test_edit_lww_newer_wins_older_skipped(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}

    _sync(client, hdr, book.id, upserts=[_hl(A1, "v1", note="old", dtu="2026-06-03 10:00:00")])
    # newer edit wins
    r = _sync(client, hdr, book.id, upserts=[_hl(A1, "v1", note="new", dtu="2026-06-03 12:00:00")])
    assert r.json()["applied"]["updated"] == 1
    # older edit is ignored
    r = _sync(client, hdr, book.id, upserts=[_hl(A1, "v1", note="stale", dtu="2026-06-03 09:00:00")])
    assert r.json()["applied"]["skipped"] == 1
    g = client.get(f"/api/tome-sync/annotations/{book.id}", headers=hdr).json()
    assert g["annotations"][0]["note"] == "new"


def test_delete_creates_tombstone_and_removes(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}

    _sync(client, hdr, book.id, upserts=[_hl(A1, dtu="2026-06-03 10:00:00")])
    r = _sync(client, hdr, book.id, deletes=[{"anchor": A1, "datetime": "2026-06-03 11:00:00"}])
    assert r.json()["applied"]["deleted"] == 1
    g = r.json()
    assert g["annotations"] == []
    assert [t["anchor"] for t in g["tombstones"]] == [A1]
    assert db.query(Annotation).filter(Annotation.book_id == book.id).count() == 0


def test_stale_device_cannot_resurrect_deleted(client, db, admin_user, make_book):
    """Device B, unaware of the delete, re-uploads the old highlight -> stays gone."""
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}

    _sync(client, hdr, book.id, upserts=[_hl(A1, dtu="2026-06-03 10:00:00")])
    _sync(client, hdr, book.id, deletes=[{"anchor": A1, "datetime": "2026-06-03 11:00:00"}])
    # stale re-add with an OLD mtime (predates the delete)
    r = _sync(client, hdr, book.id, upserts=[_hl(A1, dtu="2026-06-03 10:00:00")])
    assert r.json()["applied"]["skipped"] == 1
    assert r.json()["annotations"] == []                       # still gone
    assert [t["anchor"] for t in r.json()["tombstones"]] == [A1]


def test_rehighlight_after_delete_wins(client, db, admin_user, make_book):
    """Re-highlighting the same passage AFTER a delete (newer mtime) brings it back."""
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}

    _sync(client, hdr, book.id, upserts=[_hl(A1, dtu="2026-06-03 10:00:00")])
    _sync(client, hdr, book.id, deletes=[{"anchor": A1, "datetime": "2026-06-03 11:00:00"}])
    r = _sync(client, hdr, book.id, upserts=[_hl(A1, "re-made", dtu="2026-06-03 12:00:00")])
    assert r.json()["applied"]["created"] == 1
    assert [a["highlighted_text"] for a in r.json()["annotations"]] == ["re-made"]
    assert r.json()["tombstones"] == []                        # tombstone cleared


def test_edit_newer_than_delete_wins(client, db, admin_user, make_book):
    """Concurrent: an edit newer than a delete keeps the highlight alive."""
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    _sync(client, hdr, book.id, upserts=[_hl(A1, "edited", dtu="2026-06-03 12:00:00")])
    # a delete that is OLDER than the edit must not win
    r = _sync(client, hdr, book.id, deletes=[{"anchor": A1, "datetime": "2026-06-03 11:00:00"}])
    assert r.json()["applied"]["skipped"] == 1
    assert [a["highlighted_text"] for a in r.json()["annotations"]] == ["edited"]


def test_web_get_shows_alive_only(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    _sync(client, hdr, book.id, upserts=[_hl(A1, "keep", dtu="2026-06-03 10:00:00"),
                                          _hl(A2, "drop", dtu="2026-06-03 10:00:00")])
    _sync(client, hdr, book.id, deletes=[{"anchor": A2, "datetime": "2026-06-03 11:00:00"}])
    # client default header is the admin JWT (same user)
    body = client.get(f"/api/books/{book.id}/annotations").json()
    assert [a["highlighted_text"] for a in body] == ["keep"]


def test_isolation_between_users(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    hdr1 = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    _sync(client, hdr1, book.id, upserts=[_hl(A1)])
    other = User(username="other", email="o@x.com", hashed_password=hash_password("pw"),
                 is_active=True, is_admin=False, role="member")
    db.add(other); db.flush()
    hdr2 = {"Authorization": f"Bearer {_api_key_for(db, other.id)}"}
    g = client.get(f"/api/tome-sync/annotations/{book.id}", headers=hdr2).json()
    assert g["annotations"] == [] and g["tombstones"] == []


def test_empty_object_payload_coerced(client, db, admin_user, make_book):
    """KOReader's rapidjson sends empty tables as {} (object), not [] — must not 422."""
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    r = client.post(f"/api/tome-sync/annotations/{book.id}/sync", headers=hdr,
                    json={"upserts": {}, "deletes": {}})
    assert r.status_code == 200, r.text
    assert r.json()["annotations"] == []


def test_auth_and_404(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    assert _sync(client, {"Authorization": "Bearer nope"}, book.id, upserts=[_hl(A1)]).status_code == 401
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    assert _sync(client, hdr, 99999, upserts=[_hl(A1)]).status_code == 404
