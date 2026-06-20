"""Tests for the TomeSync rating endpoints (`/tome-sync/rating/{id}`).

The KOReader plugin syncs its native star rating + review through these. They
mirror the web `/books/{id}/rating` + `/status` endpoints but authenticate via
the plugin's tk_ API key (`_get_api_key_user`) — the web ones use
get_current_user, which rejects that key (401). Both write the same
UserBookStatus row, so a rating set on either side is the same per-user value.
"""
from backend.models.tome_sync import ApiKey


def _api_key_for(db, user_id: int) -> str:
    plaintext = ApiKey.generate()
    db.add(ApiKey(user_id=user_id, key_hash=ApiKey.hash_key(plaintext),
                  key_prefix=plaintext[:11], label="test"))
    db.flush()
    return plaintext


def test_put_then_get_roundtrips(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}

    r = client.put(f"/api/tome-sync/rating/{book.id}", headers=hdr,
                   json={"rating": 4, "review": "solid"})
    assert r.status_code == 200, r.text

    g = client.get(f"/api/tome-sync/rating/{book.id}", headers=hdr).json()
    assert g["rating"] == 4
    assert g["review"] == "solid"


def test_no_row_returns_nulls(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    g = client.get(f"/api/tome-sync/rating/{book.id}", headers=hdr).json()
    assert g == {"book_id": book.id, "rating": None, "review": None}


def test_null_clears_rating_and_review(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    client.put(f"/api/tome-sync/rating/{book.id}", headers=hdr,
               json={"rating": 5, "review": "great"})
    # Explicit nulls clear both (the plugin always sends both fields).
    r = client.put(f"/api/tome-sync/rating/{book.id}", headers=hdr,
                   json={"rating": None, "review": None})
    assert r.status_code == 200
    g = client.get(f"/api/tome-sync/rating/{book.id}", headers=hdr).json()
    assert g["rating"] is None and g["review"] is None


def test_out_of_range_rating_rejected(client, db, admin_user, make_book):
    user, _ = admin_user
    book = make_book()
    hdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    r = client.put(f"/api/tome-sync/rating/{book.id}", headers=hdr,
                   json={"rating": 6})
    assert r.status_code == 400


def test_shares_row_with_web_status(client, db, admin_user, make_book):
    # A rating set via the plugin endpoint is visible on the web status endpoint
    # (same UserBookStatus row), and reading status is left untouched.
    user, _ = admin_user
    book = make_book()
    keyhdr = {"Authorization": f"Bearer {_api_key_for(db, user.id)}"}
    from backend.core.security import create_access_token
    jwthdr = {"Authorization": f"Bearer {create_access_token(subject=user.id)}"}

    client.put(f"/api/tome-sync/rating/{book.id}", headers=keyhdr,
               json={"rating": 3, "review": "ok"})
    s = client.get(f"/api/books/{book.id}/status", headers=jwthdr).json()
    assert s["rating"] == 3
    assert s["review"] == "ok"
    assert s["status"] == "unread"  # rating never touches reading status


def test_web_key_rejected_by_get_current_user(client, db, admin_user, make_book):
    # Guards the reason these endpoints exist: the plugin's tk_ key does NOT
    # authenticate against the web rating endpoint.
    user, _ = admin_user
    book = make_book()
    key = _api_key_for(db, user.id)
    r = client.get(f"/api/books/{book.id}/status",
                   headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 401
