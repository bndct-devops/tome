"""Tests for KOReader annotation sync (one-directional KOReader -> Tome).

Plugin endpoints (PUT/GET /tome-sync/annotations/{book_id}) authenticate with a
tk_ API key; the web-facing read (GET /books/{book_id}/annotations) uses JWT.
"""
from backend.models.tome_sync import ApiKey, Annotation
from backend.models.user import User
from backend.core.security import hash_password


def _api_key_for(db, user_id: int) -> str:
    """Mint a plugin API key for a user and return the plaintext bearer value."""
    plaintext = ApiKey.generate()
    db.add(ApiKey(user_id=user_id, key_hash=ApiKey.hash_key(plaintext),
                  key_prefix=plaintext[:11], label="test"))
    db.flush()
    return plaintext


def _payload(*items):
    return {"annotations": list(items)}


HL1 = {"anchor": "/body/DocFragment[3]/body/p[1]/text().0", "highlighted_text": "All happy families are alike",
       "note": "great opening", "chapter": "Chapter 1", "color": "yellow", "datetime": "2026-06-03 10:00:00"}
HL2 = {"anchor": "/body/DocFragment[5]/body/p[9]/text().12", "highlighted_text": "a truth universally acknowledged",
       "note": None, "chapter": "Chapter 2", "color": "blue", "datetime": "2026-06-03 11:00:00"}


def test_put_creates_and_plugin_get_returns(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Anna Karenina")
    key = _api_key_for(db, user.id)
    hdr = {"Authorization": f"Bearer {key}"}

    r = client.put(f"/api/tome-sync/annotations/{book.id}", json=_payload(HL1, HL2), headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "created": 2, "updated": 0, "removed": 0, "total": 2}

    g = client.get(f"/api/tome-sync/annotations/{book.id}", headers=hdr)
    assert g.status_code == 200
    anns = g.json()["annotations"]
    assert [a["highlighted_text"] for a in anns] == [HL1["highlighted_text"], HL2["highlighted_text"]]
    assert anns[0]["note"] == "great opening"
    assert anns[0]["chapter"] == "Chapter 1"


def test_reput_same_set_is_noop(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}

    client.put(f"/api/tome-sync/annotations/{book.id}", json=_payload(HL1, HL2), headers=hdr)
    r = client.put(f"/api/tome-sync/annotations/{book.id}", json=_payload(HL1, HL2), headers=hdr)
    assert r.json() == {"ok": True, "created": 0, "updated": 0, "removed": 0, "total": 2}
    # still exactly two rows
    assert db.query(Annotation).filter(Annotation.book_id == book.id).count() == 2


def test_edited_note_updates(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}

    client.put(f"/api/tome-sync/annotations/{book.id}", json=_payload(HL1), headers=hdr)
    edited = {**HL1, "note": "edited note"}
    r = client.put(f"/api/tome-sync/annotations/{book.id}", json=_payload(edited), headers=hdr)
    assert r.json()["updated"] == 1
    g = client.get(f"/api/tome-sync/annotations/{book.id}", headers=hdr)
    assert g.json()["annotations"][0]["note"] == "edited note"


def test_removed_anchor_is_deleted(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}

    client.put(f"/api/tome-sync/annotations/{book.id}", json=_payload(HL1, HL2), headers=hdr)
    # second sync only has HL1 -> HL2 was deleted on the device
    r = client.put(f"/api/tome-sync/annotations/{book.id}", json=_payload(HL1), headers=hdr)
    assert r.json() == {"ok": True, "created": 0, "updated": 0, "removed": 1, "total": 1}
    rows = db.query(Annotation).filter(Annotation.book_id == book.id).all()
    assert [a.anchor for a in rows] == [HL1["anchor"]]


def test_empty_set_clears_all(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    client.put(f"/api/tome-sync/annotations/{book.id}", json=_payload(HL1, HL2), headers=hdr)
    r = client.put(f"/api/tome-sync/annotations/{book.id}", json=_payload(), headers=hdr)
    assert r.json()["removed"] == 2
    assert db.query(Annotation).filter(Annotation.book_id == book.id).count() == 0


def test_web_get_returns_current_users_annotations(client, db, admin_user, make_book):
    """The JWT-authed web endpoint used by BookDetailPage."""
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    client.put(f"/api/tome-sync/annotations/{book.id}", json=_payload(HL1, HL2), headers=hdr)

    # client's default header is the admin JWT (same user) — no override here
    r = client.get(f"/api/books/{book.id}/annotations")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 2
    assert body[0]["highlighted_text"] == HL1["highlighted_text"]


def test_isolation_between_users(client, db, admin_user, make_book):
    """A second user's key must not see the first user's annotations."""
    user, _ = admin_user
    book = make_book()
    hdr1 = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    client.put(f"/api/tome-sync/annotations/{book.id}", json=_payload(HL1), headers=hdr1)

    other = User(username="other", email="other@x.com",
                 hashed_password=hash_password("pw"), is_active=True, is_admin=False, role="member")
    db.add(other)
    db.flush()
    hdr2 = {"Authorization": f"Bearer {_api_key_for(db, other.id)}"}

    g = client.get(f"/api/tome-sync/annotations/{book.id}", headers=hdr2)
    assert g.status_code == 200
    assert g.json()["annotations"] == []


def test_auth_required(client, db, admin_user, make_book):
    book = make_book()
    # no auth header at all (clear the default admin JWT for this call)
    r = client.put(f"/api/tome-sync/annotations/{book.id}", json=_payload(HL1),
                   headers={"Authorization": "Bearer not-a-real-key"})
    assert r.status_code == 401


def test_book_not_found(client, db, admin_user):
    user, _ = admin_user
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    r = client.put("/api/tome-sync/annotations/99999", json=_payload(HL1), headers=hdr)
    assert r.status_code == 404
