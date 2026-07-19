"""Goodreads / StoryGraph CSV import — parse, match, fill-gaps apply."""
import io

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.models.user_book_status import UserBookStatus
from backend.services.reading_import import parse_csv

GOODREADS = '''Book Id,Title,Author,ISBN,ISBN13,My Rating,Date Read,Exclusive Shelf,My Review
1,"Mistborn: The Final Empire","Brandon Sanderson","=""0765311780""","=""9780765311788""",5,2023/07/18,read,"Loved it"
2,"The Way of Kings","Brandon Sanderson",,,0,,currently-reading,
3,"Some TBR Book","Nobody",,,0,,to-read,
'''

STORYGRAPH = """Title,Authors,ISBN/UID,Format,Read Status,Last Date Read,Star Rating,Review
"Mistborn: The Final Empire","Brandon Sanderson",9780765311788,paperback,read,2023-07-18,4.5,
"Unknown Thing","Ghost Writer",,,read,2022-01-01,3.0,
"""


def test_parse_goodreads_dialect():
    dialect, rows, skipped = parse_csv(GOODREADS.encode())
    assert dialect == "goodreads"
    assert skipped == 1  # to-read dropped
    assert rows[0]["isbn"] == "9780765311788"  # ="..." unwrapped
    assert rows[0]["rating"] == 5.0
    assert rows[0]["finished_on"] == "2023-07-18"
    assert rows[1]["status"] == "reading"
    assert rows[1]["rating"] is None


def test_parse_storygraph_dialect():
    dialect, rows, skipped = parse_csv(STORYGRAPH.encode())
    assert dialect == "storygraph"
    assert rows[0]["rating"] == 4.5


def test_preview_matches_and_flags_gaps(client: TestClient, db: Session, admin_user, make_book):
    user, _ = admin_user
    b1 = make_book(title="Mistborn: The Final Empire", author="Brandon Sanderson")
    b1.isbn = "9780765311788"
    b2 = make_book(title="The Way of Kings", author="Brandon Sanderson")
    # b1 already has a rating -> preview must mark rating as no-op
    db.add(UserBookStatus(user_id=user.id, book_id=b1.id, rating=3.0))
    db.flush()

    r = client.post("/api/import/reading-csv",
                    files={"file": ("goodreads.csv", io.BytesIO(GOODREADS.encode()), "text/csv")})
    assert r.status_code == 200
    data = r.json()
    assert data["dialect"] == "goodreads"
    assert data["skipped_unread"] == 1
    by_title = {m["title"]: m for m in data["matched"]}
    m1 = by_title["Mistborn: The Final Empire"]
    assert m1["book_id"] == b1.id
    assert m1["match_via"] == "isbn"
    assert m1["will_apply"]["rating"] is False   # existing rating survives
    assert m1["will_apply"]["status"] is True
    m2 = by_title["The Way of Kings"]
    assert m2["match_via"] == "title"
    assert data["unmatched"] == []


def test_apply_fills_gaps_only(client: TestClient, db: Session, admin_user, make_book):
    user, _ = admin_user
    book = make_book(title="Gapped Book", author="A. Author")
    db.add(UserBookStatus(user_id=user.id, book_id=book.id,
                          status="reading", rating=None))
    db.flush()

    r = client.post("/api/import/reading-csv/apply", json={"items": [{
        "book_id": book.id, "status": "read", "rating": 4.0,
        "finished_on": "2023-05-01", "review": "imported review",
    }]})
    assert r.status_code == 200
    applied = r.json()["applied"]
    # status NOT applied (already 'reading'); rating/finish/review filled.
    assert applied["status"] == 0
    assert applied["rating"] == 1
    assert applied["finished_on"] == 1
    assert applied["review"] == 1

    row = db.query(UserBookStatus).filter_by(user_id=user.id, book_id=book.id).one()
    db.refresh(row)
    assert row.status == "reading"      # untouched
    assert row.rating == 4.0
    assert row.finished_at.date().isoformat() == "2023-05-01"


def test_bad_csv_rejected(client: TestClient):
    r = client.post("/api/import/reading-csv",
                    files={"file": ("x.csv", io.BytesIO(b"foo,bar\n1,2\n"), "text/csv")})
    assert r.status_code == 422
