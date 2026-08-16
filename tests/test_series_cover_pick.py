"""GET /books/series must never point cover_book_id at a coverless book.

The series grid requests /api/books/{cover_book_id}/cover for every card, so a
coverless pick produced a guaranteed 404 per render (UX sweep finding). The
cover comes from the first volume (by series index) that actually has one, and
is null when no volume does.
"""


def _series(client, name):
    rows = client.get("/api/books/series").json()
    return next(r for r in rows if r["name"] == name)


def test_cover_skips_coverless_early_volumes(client, make_book):
    make_book(title="V1", series="Saga", series_index=1)  # no cover
    v2 = make_book(title="V2", series="Saga", series_index=2, cover_path="c2.jpg")
    row = _series(client, "Saga")
    assert row["cover_book_id"] == v2.id


def test_cover_null_when_no_volume_has_one(client, make_book):
    make_book(title="V1", series="Bare", series_index=1)
    make_book(title="V2", series="Bare", series_index=2)
    assert _series(client, "Bare")["cover_book_id"] is None


def test_cover_prefers_lowest_index_with_cover(client, make_book):
    v1 = make_book(title="V1", series="Both", series_index=1, cover_path="c1.jpg")
    make_book(title="V2", series="Both", series_index=2, cover_path="c2.jpg")
    assert _series(client, "Both")["cover_book_id"] == v1.id


def test_unserialized_bucket_skips_coverless(client, make_book):
    make_book(title="Standalone A")  # no cover, lower id
    b = make_book(title="Standalone B", cover_path="cb.jpg")
    row = _series(client, "__unserialized__")
    assert row["cover_book_id"] == b.id
    assert row["book_count"] == 2
