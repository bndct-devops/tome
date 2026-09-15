"""Tests for the cross-library Highlights endpoint `GET /api/annotations`.

The per-book view (`/api/books/{id}/annotations`) is covered elsewhere; this
exercises the aggregate: search, grouping context, on-this-day, and that it only
returns the caller's own highlights on books they can see.
"""
from backend.core.security import create_access_token, hash_password
from backend.models.tome_sync import Annotation
from backend.models.user import User


def _hdr(user):
    return {"Authorization": f"Bearer {create_access_token(subject=user.id)}"}


def _anno(db, user, book, text, anchor, note=None, chapter=None, when="2024-03-10 09:00:00"):
    db.add(Annotation(
        user_id=user.id, book_id=book.id, anchor=anchor,
        highlighted_text=text, note=note, chapter=chapter, koreader_datetime=when,
    ))
    db.flush()


def test_aggregates_across_books_with_context(client, db, admin_user, make_book):
    user, _ = admin_user
    b1 = make_book(title="Dune")
    b2 = make_book(title="Hyperion")
    _anno(db, user, b1, "Fear is the mind-killer", "a1", chapter="Book One")
    _anno(db, user, b1, "The spice must flow", "a2")
    _anno(db, user, b2, "The Time Tombs", "a3", note="eerie")
    db.commit()

    r = client.get("/api/annotations", headers=_hdr(user))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 3
    assert data["books"] == 2
    # Context travels with each item so the page can group + link.
    item = next(i for i in data["items"] if i["highlighted_text"] == "The Time Tombs")
    assert item["book_id"] == b2.id and item["book_title"] == "Hyperion"
    assert item["note"] == "eerie"


def test_search_matches_text_note_and_title(client, db, admin_user, make_book):
    user, _ = admin_user
    b1 = make_book(title="Dune")
    b2 = make_book(title="Hyperion")
    _anno(db, user, b1, "Fear is the mind-killer", "a1")
    _anno(db, user, b2, "The Time Tombs", "a3", note="eerie cathedral")
    db.commit()

    assert client.get("/api/annotations?q=mind-killer", headers=_hdr(user)).json()["total"] == 1
    assert client.get("/api/annotations?q=cathedral", headers=_hdr(user)).json()["total"] == 1  # note
    assert client.get("/api/annotations?q=hyperion", headers=_hdr(user)).json()["total"] == 1   # title
    assert client.get("/api/annotations?q=nothinghere", headers=_hdr(user)).json()["total"] == 0


def test_on_this_day_filters_by_month_day(client, db, admin_user, make_book, monkeypatch):
    user, _ = admin_user
    b1 = make_book(title="Dune")
    _anno(db, user, b1, "kept — same day, prior year", "a1", when="2022-03-10 08:00:00")
    _anno(db, user, b1, "dropped — different day", "a2", when="2022-07-01 08:00:00")
    db.commit()

    import backend.api.annotations as mod
    monkeypatch.setattr(mod, "func_now_str", lambda: "2024-03-10 12:00:00")

    data = client.get("/api/annotations?on_this_day=1", headers=_hdr(user)).json()
    assert data["total"] == 1
    assert data["items"][0]["highlighted_text"].startswith("kept")


def test_spotlight_prefers_on_this_day(client, db, admin_user, make_book, monkeypatch):
    user, _ = admin_user
    book = make_book(title="Dune")
    _anno(db, user, book, "today, a past year", "a1", when="2021-03-10 08:00:00")
    _anno(db, user, book, "some other day", "a2", when="2021-09-01 08:00:00")
    db.commit()

    import backend.api.annotations as mod
    monkeypatch.setattr(mod, "func_now_str", lambda: "2024-03-10 12:00:00")

    data = client.get("/api/annotations/spotlight", headers=_hdr(user)).json()
    assert data["on_this_day"] is True
    assert data["highlight"]["highlighted_text"] == "today, a past year"


def test_spotlight_falls_back_to_random(client, db, admin_user, make_book, monkeypatch):
    user, _ = admin_user
    book = make_book(title="Dune")
    _anno(db, user, book, "only highlight", "a1", when="2021-09-01 08:00:00")
    db.commit()

    import backend.api.annotations as mod
    monkeypatch.setattr(mod, "func_now_str", lambda: "2024-03-10 12:00:00")  # nothing on 03-10

    data = client.get("/api/annotations/spotlight", headers=_hdr(user)).json()
    assert data["on_this_day"] is False
    assert data["highlight"]["highlighted_text"] == "only highlight"


def test_spotlight_null_when_no_highlights(client, db, admin_user, make_book):
    user, _ = admin_user
    make_book(title="Dune")  # book but no highlights
    data = client.get("/api/annotations/spotlight", headers=_hdr(user)).json()
    assert data == {"highlight": None, "on_this_day": False}


def test_only_own_highlights(client, db, admin_user, make_book):
    user, _ = admin_user
    other = User(username="other", email="other@x.io",
                 hashed_password=hash_password("x"), is_admin=True, role="admin")
    db.add(other)
    db.flush()
    book = make_book(title="Shared")
    _anno(db, user, book, "mine", "a1")
    _anno(db, other, book, "theirs", "a2")
    db.commit()

    data = client.get("/api/annotations", headers=_hdr(user)).json()
    assert data["total"] == 1
    assert data["items"][0]["highlighted_text"] == "mine"


def test_only_notes_filter(client, db, admin_user, make_book):
    user, _ = admin_user
    b = make_book(title="Noted")
    _anno(db, user, b, "plain highlight", "n1")
    _anno(db, user, b, "annotated highlight", "n2", note="my thought")
    _anno(db, user, b, "empty-string note", "n3", note="")
    db.commit()

    r = client.get("/api/annotations", params={"only_notes": "1"}, headers=_hdr(user)).json()
    assert r["total"] == 1
    assert r["items"][0]["note"] == "my thought"
    # composes with search
    r = client.get("/api/annotations", params={"only_notes": "1", "q": "thought"},
                   headers=_hdr(user)).json()
    assert r["total"] == 1
    r = client.get("/api/annotations", params={"only_notes": "1", "q": "plain"},
                   headers=_hdr(user)).json()
    assert r["total"] == 0


def test_spotlight_exclude_rerolls_to_a_different_highlight(client, db, admin_user, make_book):
    user, _ = admin_user
    b = make_book(title="Spot")
    _anno(db, user, b, "first", "s1")
    _anno(db, user, b, "second", "s2")
    db.commit()

    first = client.get("/api/annotations/spotlight", headers=_hdr(user)).json()["highlight"]
    for _ in range(5):
        other = client.get("/api/annotations/spotlight",
                           params={"exclude": first["id"]}, headers=_hdr(user)).json()["highlight"]
        assert other["id"] != first["id"]


def test_spotlight_exclude_with_single_highlight_returns_it_again(client, db, admin_user, make_book):
    user, _ = admin_user
    b = make_book(title="Solo")
    _anno(db, user, b, "the only one", "s1")
    db.commit()

    got = client.get("/api/annotations/spotlight", headers=_hdr(user)).json()["highlight"]
    again = client.get("/api/annotations/spotlight",
                       params={"exclude": got["id"]}, headers=_hdr(user)).json()["highlight"]
    # Better the same highlight than an empty card.
    assert again is not None and again["id"] == got["id"]


def test_book_id_filter(client, db, admin_user, make_book):
    user, _ = admin_user
    b1 = make_book(title="Dune")
    b2 = make_book(title="Hyperion")
    _anno(db, user, b1, "Fear is the mind-killer", "a1", note="n")
    _anno(db, user, b1, "The spice must flow", "a2")
    _anno(db, user, b2, "The Time Tombs", "a3", note="n")
    db.commit()

    r = client.get("/api/annotations", params={"book_id": b1.id}, headers=_hdr(user)).json()
    assert r["total"] == 2 and r["books"] == 1
    assert {i["book_id"] for i in r["items"]} == {b1.id}
    # composes with the other filters
    r = client.get("/api/annotations", params={"book_id": b1.id, "only_notes": "1"}, headers=_hdr(user)).json()
    assert r["total"] == 1 and r["items"][0]["highlighted_text"] == "Fear is the mind-killer"
    # no param: unchanged
    r = client.get("/api/annotations", headers=_hdr(user)).json()
    assert r["total"] == 3 and r["books"] == 2
    # a book without highlights
    assert client.get("/api/annotations", params={"book_id": 999999}, headers=_hdr(user)).json() == \
        {"total": 0, "books": 0, "items": []}


def test_book_id_filter_respects_visibility(client, db, admin_user, make_book):
    from backend.models.library import Library

    admin, _ = admin_user
    member = User(username="reader", email="reader@x.io", hashed_password=hash_password("x"),
                  is_admin=False, role="member", is_active=True)
    db.add(member)
    db.flush()
    hidden = make_book(title="Private")
    lib = Library(name="Admin only", is_public=False, owner_id=admin.id)
    db.add(lib)
    db.flush()
    hidden.libraries.append(lib)
    # a stale highlight of the member's on a book they can no longer see
    _anno(db, member, hidden, "should not leak", "v1")
    _anno(db, admin, hidden, "admin's own", "v2")
    db.commit()

    r = client.get("/api/annotations", params={"book_id": hidden.id}, headers=_hdr(member)).json()
    assert r == {"total": 0, "books": 0, "items": []}
    r = client.get("/api/annotations", params={"book_id": hidden.id}, headers=_hdr(admin)).json()
    assert r["total"] == 1 and r["items"][0]["highlighted_text"] == "admin's own"
