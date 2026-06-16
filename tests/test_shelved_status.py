"""Tests for the 'shelved' reading status.

Shelved = a 4th reading state that sets a book aside: it drops off the
Continue Reading / Series Progress rails and the status-based stats, but
(unlike 'unread') keeps the reading progress + CFI so you can resume exactly
where you left off.
"""


def _set_status(client, book_id, status, progress=None, cfi=None):
    body = {"status": status}
    if progress is not None:
        body["progress_pct"] = progress
    if cfi is not None:
        body["cfi"] = cfi
    r = client.put(f"/api/books/{book_id}/status", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_shelved_is_accepted(client, make_book):
    book = make_book(title="Shelf Me")
    out = _set_status(client, book.id, "shelved")
    assert out["status"] == "shelved"


def test_shelved_preserves_progress_and_cfi(client, make_book):
    book = make_book(title="Keep My Place")
    _set_status(client, book.id, "reading", progress=0.72, cfi="epubcfi(/6/14!/4/2/2)")
    out = _set_status(client, book.id, "shelved")
    # Unlike 'unread', shelving must NOT wipe the position.
    assert out["status"] == "shelved"
    assert out["progress_pct"] == 0.72
    assert out["cfi"] == "epubcfi(/6/14!/4/2/2)"


def test_unread_still_clears_progress(client, make_book):
    """Contrast: 'unread' remains the destructive reset."""
    book = make_book(title="Reset Me")
    _set_status(client, book.id, "reading", progress=0.5, cfi="epubcfi(/6/2)")
    out = _set_status(client, book.id, "unread")
    assert out["progress_pct"] is None
    assert out["cfi"] is None


def test_shelved_filter_finds_only_shelved(client, make_book):
    reading = make_book(title="Active Read")
    shelved = make_book(title="Set Aside")
    _set_status(client, reading.id, "reading", progress=0.3)
    _set_status(client, shelved.id, "shelved", progress=0.3)

    def ids(status):
        r = client.get(f"/api/books?reading_status={status}")
        assert r.status_code == 200, r.text
        return {b["id"] for b in r.json()}

    assert shelved.id in ids("shelved")
    assert reading.id not in ids("shelved")
    # And a shelved book must not leak into the other status views.
    assert shelved.id not in ids("reading")
    assert shelved.id not in ids("read")
    assert shelved.id not in ids("unread")


def test_shelved_excluded_from_continue_reading(client, make_book):
    book = make_book(title="Was Reading")
    _set_status(client, book.id, "reading", progress=0.4)
    _set_status(client, book.id, "shelved")
    r = client.get("/api/books?reading_status=reading")
    assert book.id not in {b["id"] for b in r.json()}


def test_shelved_excluded_from_completion_stats(client, make_book):
    finished = make_book(title="Done")
    shelved = make_book(title="Parked")
    _set_status(client, finished.id, "read", progress=1.0)
    _set_status(client, shelved.id, "shelved", progress=0.6)

    r = client.get("/api/stats?range_days=365")
    assert r.status_code == 200, r.text
    cr = r.json()["completion_rate"]
    # 'started' counts only reading/read — the shelved book is not counted as
    # a started-but-unfinished book dragging the completion rate down.
    assert cr["started"] == 1
    assert cr["finished"] == 1
