"""The reading companion's switch: nothing is read on its own where it is off;
switching it on starts at the reader's chapter and never goes back on its own."""
from __future__ import annotations

import pytest

from backend.models.user_book_status import UserBookStatus
from backend.services.ai import digest, digest_job, optin
from tests.test_ai_digest import (  # noqa: F401 - fixtures and helpers
    CHAPTERS, EIGHT, _epub_book, _headers, _jobs_inline, _make_user, _mid_of, _queued, _read, _store_digest,
)


@pytest.fixture(autouse=True)
def _production_default(monkeypatch):
    monkeypatch.setattr(optin, "_TEST_DEFAULT", None)


def test_off_by_default_nothing_runs_on_its_own(db, make_book, tmp_path):
    user = _make_user(db, "offuser", "member")
    book = _epub_book(db, make_book, tmp_path, "Off Book", chapters=EIGHT, series="Off", index=1)
    assert optin.enabled(db, user, book) is False
    assert digest.on_position_advanced(db, user, book, _mid_of(book, db, 6)) is None
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read", progress_pct=1.0))
    db.commit()
    assert _queued() == []


def test_switching_on_starts_at_the_reader_s_chapter(db, make_book, tmp_path):
    user = _make_user(db, "midway", "member")
    book = _epub_book(db, make_book, tmp_path, "Midway", chapters=EIGHT, series="Midway", index=1)
    row = optin.set_enabled(db, user, book, True, position_chapter=4)
    assert row.enabled and row.start_book_id == book.id and row.start_chapter_idx == 4
    assert optin.floor(db, user, book) == 4
    # Reading on from chapter 4: only chapters at or past the start are queued.
    job = digest.on_position_advanced(db, user, book, _mid_of(book, db, 7))
    assert job is not None
    units = digest.plan_units(db, digest.Scope(book=book), 7, floor=job["floor"])
    assert [i for u in units for i in u.idxs] == [4, 5, 6]
    # Marked read: the whole-book digest also starts at the switch's start.
    digest_job.reset(); digest.reset_triggers()
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read", progress_pct=1.0))
    db.commit()
    [finished] = _queued()
    assert finished["floor"] == 4 and finished["upto_chapter"] == 8


def test_a_book_with_digests_continues_where_it_stopped(db, make_book, tmp_path):
    user = _make_user(db, "resumer", "member")
    book = _epub_book(db, make_book, tmp_path, "Resumer", chapters=EIGHT, series="Resumer", index=1)
    for i in range(3):
        _store_digest(db, book, i, [])
    row = optin.set_enabled(db, user, book, True, position_chapter=6)
    assert row.start_chapter_idx == 0
    job = digest.on_position_advanced(db, user, book, _mid_of(book, db, 7))
    assert job is not None and job["floor"] == 0
    units = digest.plan_units(db, digest.Scope(book=book), 7, floor=0)
    assert [i for u in units for i in u.idxs] == [3, 4, 5, 6]


def test_the_switch_carries_to_later_volumes_from_chapter_zero(db, make_book, tmp_path):
    user = _make_user(db, "carrier", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Carry 1", chapters=EIGHT, series="Carry", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Carry 2", chapters=EIGHT, series="Carry", index=2)
    optin.set_enabled(db, user, v1, True, position_chapter=5)
    assert optin.enabled(db, user, v2) is True
    assert optin.floor(db, user, v2) == 0
    assert optin.scope_key(v1) == optin.scope_key(v2)


def test_explicit_start_ignores_the_switch_and_its_start(db, make_book, tmp_path, client):
    user = _make_user(db, "explicit", "member")
    book = _epub_book(db, make_book, tmp_path, "Explicit", chapters=EIGHT, series="Explicit", index=1)
    optin.set_enabled(db, user, book, True, position_chapter=5)
    db.commit()
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=8, current_book_only=True)
    units = digest.plan_units(db, digest.Scope(book=book), 8, floor=snap["floor"])
    assert [i for u in units for i in u.idxs] == list(range(8))


def test_endpoint_round_trip(client, db, make_book, tmp_path):
    user = _make_user(db, "switcher", "member")
    book = _epub_book(db, make_book, tmp_path, "Switcher", chapters=EIGHT, series="Switch", index=1)
    db.commit()
    r = client.get(f"/api/ai/books/{book.id}/companion", headers=_headers(user))
    assert r.status_code == 200
    assert r.json()["optin"] == {"enabled": False, "scope": "series", "start": None}
    r = client.put(f"/api/ai/books/{book.id}/companion/optin", json={"enabled": True}, headers=_headers(user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True and body["scope"] == "series"
    assert body["start"] == {"book_id": book.id, "chapter_idx": 0}   # no position yet
    r = client.put(f"/api/ai/books/{book.id}/companion/optin", json={"enabled": False}, headers=_headers(user))
    assert r.json()["enabled"] is False
    assert client.get(f"/api/ai/books/{book.id}/companion", headers=_headers(user)).json()["optin"]["enabled"] is False


def test_the_switch_alone_reads_the_same_row(client, db, make_book, tmp_path):
    user = _make_user(db, "lone", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Lone 1", chapters=EIGHT, series="Lone", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Lone 2", chapters=EIGHT, series="Lone", index=2)
    db.commit()
    r = client.get(f"/api/ai/books/{v2.id}/companion/optin", headers=_headers(user))
    assert r.status_code == 200
    assert r.json() == {"enabled": False, "scope": "series", "start": None}
    client.put(f"/api/ai/books/{v1.id}/companion/optin", json={"enabled": True}, headers=_headers(user))
    # One row per series: switched on through volume 1, volume 2 sees it on.
    assert client.get(f"/api/ai/books/{v2.id}/companion/optin", headers=_headers(user)).json()["enabled"] is True
