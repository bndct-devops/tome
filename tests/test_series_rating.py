"""Per-user series ratings + volume inheritance.

A volume's effective rating is its own rating, else the inherited series rating.
The series display rating is the explicit value if set, else the average of the
user's volume ratings. The "No Series" bucket can never be rated.
"""


def test_set_and_get_series_rating(client, make_book):
    make_book(title="V1", series="Saga", series_index=1)
    r = client.put("/api/series/Saga/rating", json={"rating": 4})
    assert r.status_code == 200
    body = r.json()
    assert body["rating"] == 4
    assert body["display"] == 4
    assert client.get("/api/series/Saga/rating").json()["rating"] == 4


def test_no_series_cannot_be_rated(client):
    assert client.put("/api/series/__unserialized__/rating", json={"rating": 5}).status_code == 400


def test_series_rating_out_of_range(client, make_book):
    make_book(title="V1", series="Saga", series_index=1)
    assert client.put("/api/series/Saga/rating", json={"rating": 0}).status_code == 400
    assert client.put("/api/series/Saga/rating", json={"rating": 6}).status_code == 400


def test_display_falls_back_to_volume_average(client, make_book):
    a = make_book(title="V1", series="Avg", series_index=1)
    b = make_book(title="V2", series="Avg", series_index=2)
    client.put(f"/api/books/{a.id}/rating", json={"rating": 3})
    client.put(f"/api/books/{b.id}/rating", json={"rating": 5})
    body = client.get("/api/series/Avg/rating").json()
    assert body["rating"] is None          # no explicit series rating
    assert body["volume_average"] == 4.0
    assert body["rated_volumes"] == 2
    assert body["display"] == 4             # rounded average


def test_explicit_series_rating_overrides_average(client, make_book):
    a = make_book(title="V1", series="Over", series_index=1)
    client.put(f"/api/books/{a.id}/rating", json={"rating": 2})
    client.put("/api/series/Over/rating", json={"rating": 5})
    body = client.get("/api/series/Over/rating").json()
    assert body["display"] == 5             # explicit wins over the 2-star avg


def test_volume_inherits_series_rating_in_batch(client, make_book):
    a = make_book(title="V1", series="Inherit", series_index=1)
    b = make_book(title="V2", series="Inherit", series_index=2)
    client.put("/api/series/Inherit/rating", json={"rating": 4})
    # Neither volume rated individually → both inherit 4 in the batch endpoint
    m = client.post("/api/books/statuses", json={"book_ids": [a.id, b.id]}).json()
    assert m[str(a.id)]["rating"] == 4
    assert m[str(b.id)]["rating"] == 4


def test_own_volume_rating_beats_inherited(client, make_book):
    a = make_book(title="V1", series="Beats", series_index=1)
    b = make_book(title="V2", series="Beats", series_index=2)
    client.put("/api/series/Beats/rating", json={"rating": 4})
    client.put(f"/api/books/{a.id}/rating", json={"rating": 1})
    m = client.post("/api/books/statuses", json={"book_ids": [a.id, b.id]}).json()
    assert m[str(a.id)]["rating"] == 1     # own wins
    assert m[str(b.id)]["rating"] == 4     # inherited


def test_min_rating_filter_uses_inheritance(client, make_book):
    a = make_book(title="V1", series="Filt", series_index=1)  # unrated volume...
    client.put("/api/series/Filt/rating", json={"rating": 5})  # ...but series rated 5
    ids = {b["id"] for b in client.get("/api/books?min_rating=5").json()}
    assert a.id in ids                     # inherited rating clears the filter


def test_series_list_includes_display_rating(client, make_book):
    a = make_book(title="V1", series="Listed", series_index=1)
    make_book(title="V2", series="Listed", series_index=2)
    # explicit series rating shows as the list display rating
    client.put("/api/series/Listed/rating", json={"rating": 3})
    row = next(s for s in client.get("/api/books/series").json() if s["name"] == "Listed")
    assert row["rating"] == 3

    # without an explicit series rating it falls back to the volume average
    client.put("/api/series/Listed/rating", json={"rating": None})
    client.put(f"/api/books/{a.id}/rating", json={"rating": 5})
    row = next(s for s in client.get("/api/books/series").json() if s["name"] == "Listed")
    assert row["rating"] == 5               # avg of the single rated volume
