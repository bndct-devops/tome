"""GET /bindery/groups — series-centric grouping with sibling-derived identity."""
import pytest


@pytest.fixture
def grouped_bindery(tmp_path, monkeypatch):
    bindery_path = tmp_path / "bindery"
    library_path = tmp_path / "library"
    bindery_path.mkdir()
    library_path.mkdir()
    monkeypatch.setattr("backend.core.config.settings.incoming_dir", bindery_path)
    monkeypatch.setattr("backend.core.config.settings.library_dir", library_path)
    # Three volumes of one series (drift spelling), one standalone
    (bindery_path / "Frieren - Beyond Journey's End v04.cbz").write_bytes(b"PK\x03\x04a")
    (bindery_path / "Frieren - Beyond Journey's End v05.cbz").write_bytes(b"PK\x03\x04b")
    (bindery_path / "Frieren - Beyond Journey's End v06.cbz").write_bytes(b"PK\x03\x04c")
    (bindery_path / "Standalone Novel - Some Author (2020).epub").write_bytes(b"PK\x03\x04d")
    return bindery_path


def test_groups_by_series_with_adopted_identity(client, grouped_bindery, make_book, db):
    v1 = make_book(title="V1", series="Frieren: Beyond Journey's End",
                   series_index=1, author="Kanehito Yamada", language="en")
    v1.is_reviewed = True
    db.commit()

    groups = client.get("/api/bindery/groups").json()
    frieren = next(g for g in groups if g["series"] and "Frieren" in g["series"])
    # Canonical spelling adopted from the library, not the drifted filename
    assert frieren["series"] == "Frieren: Beyond Journey's End"
    assert frieren["author"] == "Kanehito Yamada"
    assert frieren["library_match"]["volume_count"] == 1
    assert frieren["library_match"]["from_reviewed"] is True
    assert [f["series_index"] for f in frieren["files"]] == [4, 5, 6]
    # Destination preview files under the canonical series folder
    assert all("Frieren: Beyond Journey's End" in f["dest_preview"].replace("_", ":")
               or "Frieren" in f["dest_preview"] for f in frieren["files"])


def test_unknown_series_groups_without_match(client, grouped_bindery):
    groups = client.get("/api/bindery/groups").json()
    frieren = next(g for g in groups if g["series"] and "Frieren" in g["series"])
    assert frieren["library_match"] is None
    assert len(frieren["files"]) == 3
    # Standalone gets its own group
    single = next(g for g in groups if len(g["files"]) == 1)
    assert single["files"][0]["filename"].startswith("Standalone Novel")


def test_series_groups_sort_before_singletons(client, grouped_bindery):
    groups = client.get("/api/bindery/groups").json()
    assert len(groups[0]["files"]) >= len(groups[-1]["files"])


def test_accept_marks_book_reviewed(client, grouped_bindery, db):
    from backend.models.book import Book
    r = client.post("/api/bindery/accept", json={"files": [{
        "path": "Standalone Novel - Some Author (2020).epub",
        "title": "Standalone Novel", "author": "Some Author",
    }]})
    assert r.status_code == 200 and not r.json()["errors"]
    book_id = r.json()["accepted"][0]["book_id"]
    # Accepting IS the review — the book must count as authoritative for
    # sibling matching and must not land in the unreviewed queue.
    assert db.get(Book, book_id).is_reviewed is True
