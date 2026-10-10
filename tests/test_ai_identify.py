"""Bindery identify (POST /api/ai/identify): evidence builder, batching,
proposal sanitising, path safety, permissions, and the ai_assisted flag on
the accept audit entry. The provider is always the FakeProvider and the
metadata sources are mocked; fixtures are tiny files built here with neutral
made-up titles."""
import json
from pathlib import Path

import fitz  # PyMuPDF
import pytest
from ebooklib import epub
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.ai_usage import AIUsage
from backend.models.audit_log import AuditLog
from backend.models.library import BookType
from backend.models.user import User
from backend.services import ai
from backend.services.ai import identify, settings as ai_settings
from backend.services.metadata_fetch import FetchResult, MetadataCandidate
from backend.services.text_sample import first_pages_text
from tests.ai_fake import FakeProvider

KEY = "sk-ant-test-identify-0000000000Ab12"

CHAPTER_TEXT = (
    "The fog came in off the water before dawn, and by the time the lamps were lit "
    "along the quay the whole of the lower town had gone grey and quiet. "
) * 6


# ── fixture builders ─────────────────────────────────────────────────────────

def make_epub(path: Path, *, title: str = "The Quiet Harbour", author: str = "M. Vale",
              isbn: str | None = None) -> Path:
    book = epub.EpubBook()
    book.set_identifier("quiet-harbour-test")
    book.set_title(title)
    book.set_language("en")
    book.add_author(author)
    if isbn:
        book.add_metadata("DC", "identifier", isbn, {"id": "isbn"})

    cover = epub.EpubHtml(title="Cover", file_name="cover.xhtml", lang="en", uid="cover")
    cover.content = "<html><body><p>COVERMARK</p></body></html>"
    titlepage = epub.EpubHtml(title="Title", file_name="titlepage.xhtml", lang="en", uid="titlepage")
    titlepage.content = f"<html><body><h1>{title}</h1><p>{author}</p></body></html>"
    ch1 = epub.EpubHtml(title="One", file_name="ch1.xhtml", lang="en", uid="ch1")
    ch1.content = f"<html><body><h2>Chapter One</h2><p>{CHAPTER_TEXT}</p></body></html>"
    ch2 = epub.EpubHtml(title="Two", file_name="ch2.xhtml", lang="en", uid="ch2")
    ch2.content = f"<html><body><h2>Chapter Two</h2><p>{CHAPTER_TEXT}</p></body></html>"
    ch3 = epub.EpubHtml(title="Three", file_name="ch3.xhtml", lang="en", uid="ch3")
    ch3.content = "<html><body><h2>Chapter Three</h2><p>LATEMARK</p></body></html>"
    for item in (cover, titlepage, ch1, ch2, ch3):
        book.add_item(item)
    book.toc = (ch1, ch2, ch3)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = [cover, titlepage, ch1, ch2, ch3]
    path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(path), book)
    return path


def make_pdf(path: Path, pages: list[str]) -> Path:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


def _make_user(db: Session, username: str, role: str) -> tuple[User, dict]:
    user = User(username=username, email=f"{username}@example.com",
                hashed_password=hash_password("pw123456"), is_active=True,
                is_admin=(role == "admin"), role=role, must_change_password=False)
    db.add(user)
    db.flush()
    return user, {"Authorization": f"Bearer {create_access_token(subject=user.id)}"}


# ── shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def bindery(tmp_path: Path, monkeypatch) -> Path:
    incoming = tmp_path / "bindery"
    incoming.mkdir()
    (tmp_path / "library").mkdir()
    (tmp_path / "data" / "covers").mkdir(parents=True)
    monkeypatch.setattr("backend.core.config.settings.incoming_dir", incoming)
    monkeypatch.setattr("backend.core.config.settings.library_dir", tmp_path / "library")
    monkeypatch.setattr("backend.core.config.settings.data_dir", tmp_path / "data")
    return incoming


CANDIDATE = MetadataCandidate(
    source="open_library", source_id="OL123W", title="The Quiet Harbour",
    author="M. Vale", description="A town, a fog, a missing ferry.",
    cover_url="https://covers.example.org/1.jpg", publisher="Lantern Press",
    year=2019, isbn="9780000000002", language="en",
    tags=["Mystery", "Coastal Fiction"], series="Harbour Tales", series_index=2.0,
)


@pytest.fixture()
def sources(monkeypatch) -> list[dict]:
    """Mock the metadata sources; records each fetch's kwargs."""
    seen: list[dict] = []

    async def fake_fetch(**kwargs):
        seen.append(kwargs)
        return FetchResult(candidates=[CANDIDATE], query_used=kwargs.get("title", ""))

    monkeypatch.setattr("backend.services.ai.identify.fetch_candidates", fake_fetch)
    return seen


@pytest.fixture()
def book_types(db: Session) -> dict[str, BookType]:
    out = {}
    for i, (slug, label) in enumerate((("aitest-novel", "Novel"), ("aitest-comic", "Comic"))):
        bt = BookType(slug=slug, label=label, icon="BookOpen", color="blue", sort_order=100 + i)
        db.add(bt)
        out[slug] = bt
    db.flush()
    return out


@pytest.fixture()
def admin_with_key(db: Session, admin_user) -> User:
    user, _ = admin_user
    ai_settings.set_user_key(db, user, KEY)
    db.flush()
    return user


def echo_responder(**overrides):
    """A FakeProvider responder that answers every file_id in the request."""
    def respond(feature, user_content, schema):
        text = user_content[0]["text"]
        payload = json.loads(text[text.index("{"):])
        files = []
        for f in payload["files"]:
            files.append({
                "file_id": f["file_id"],
                "title": "The Quiet Harbour",
                "author": "M. Vale",
                "series": "Harbour Tales",
                "series_index": 2,
                "content_type": "volume",
                "book_type_slug": "aitest-novel",
                "language": "en",
                "year": 2019,
                "tags": ["mystery", "Invented Tag"],
                "confidence": 0.93,
                "evidence": "Title page and the Open Library record agree.",
                "candidate_source_id": "OL123W",
                **overrides,
            })
        return {"files": files}
    return respond


# ── text sample / evidence builder ───────────────────────────────────────────

def test_epub_text_sample_skips_cover_and_stops_after_two_documents(tmp_path: Path):
    path = make_epub(tmp_path / "harbour.epub")
    text = first_pages_text(path)
    assert text is not None
    assert "COVERMARK" not in text
    assert "The Quiet Harbour" in text and "Chapter One" in text and "Chapter Two" in text
    assert "LATEMARK" not in text
    assert len(text) <= 3000


def test_epub_text_sample_respects_max_chars(tmp_path: Path):
    path = make_epub(tmp_path / "harbour.epub")
    assert len(first_pages_text(path, max_chars=120) or "") == 120


def test_pdf_text_sample_reads_first_three_pages(tmp_path: Path):
    path = make_pdf(tmp_path / "guide.pdf", ["Page one text", "Page two text", "Page three text", "PAGEFOUR"])
    text = first_pages_text(path)
    assert text is not None
    assert "Page one text" in text and "Page three text" in text
    assert "PAGEFOUR" not in text


def test_comic_has_no_text_sample(tmp_path: Path):
    path = tmp_path / "comic.cbz"
    path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    assert first_pages_text(path) is None


def test_build_evidence_epub(db: Session, bindery: Path, make_book):
    make_book(title="Harbour Tales 1", author="M. Vale", series="Harbour Tales", series_index=1)
    path = make_epub(bindery / "Harbour Tales" / "Harbour Tales v02 - The Quiet Harbour.epub",
                     isbn="9780000000002")
    rel = str(path.relative_to(bindery))
    ev = identify.build_evidence(db, rel, path)
    assert ev.filename == path.name
    assert ev.folder == "Harbour Tales"
    assert ev.format == "epub"
    assert ev.embedded["title"] == "The Quiet Harbour"
    assert ev.embedded["author"] == "M. Vale"
    assert "cover_path" not in ev.embedded
    assert ev.filename_guess["series_index"] == 2
    assert ev.text_sample and "Chapter One" in ev.text_sample
    assert ev.library_series is not None
    assert ev.library_series["series"] == "Harbour Tales"
    assert ev.library_series["author"] == "M. Vale"
    model_view = ev.for_model("f1")
    assert model_view["file_id"] == "f1" and model_view["first_pages_text"] == ev.text_sample


def test_build_evidence_carries_the_series_title_pattern(db: Session, bindery: Path, make_book,
                                                        admin_user):
    admin, _ = admin_user
    for i in (1, 2, 3):
        make_book(title=f"Harbour Tales, Vol. {i}: Part {i}", author="M. Vale",
                  series="Harbour Tales", series_index=float(i))
    path = make_epub(bindery / "Harbour Tales" / "Harbour Tales v04 - The Quiet Harbour.epub")
    ev = identify.build_evidence(db, str(path.relative_to(bindery)), path, admin)
    assert ev.library_series is not None
    assert ev.library_series["sample_titles"] == [
        {"title": f"Harbour Tales, Vol. {i}: Part {i}", "series_index": float(i)} for i in (1, 2, 3)
    ]


def test_prompt_defines_an_exact_match_and_the_title_pattern():
    prompt = identify.SYSTEM_PROMPT
    assert "this exact book" in prompt
    assert "same language and same format" in prompt
    assert "light novel is not its manga adaptation" in prompt
    assert "ISBN, cover, description and publisher are copied" in prompt
    assert "sample_titles" in prompt
    assert "Use null when none of them gives it" in prompt


def test_build_evidence_pdf(db: Session, bindery: Path):
    path = make_pdf(bindery / "Field Notes.pdf", ["Field Notes on Tides", "by A. Example", "Contents"])
    ev = identify.build_evidence(db, "Field Notes.pdf", path)
    assert ev.folder is None
    assert ev.format == "pdf"
    assert ev.text_sample and "Field Notes on Tides" in ev.text_sample
    assert ev.embedded.get("page_count") == 3
    assert ev.library_series is None


# ── endpoint: proposals, batching ────────────────────────────────────────────

def test_identify_returns_sanitised_proposals(client: TestClient, db: Session, bindery: Path,
                                              sources, book_types, admin_with_key):
    path = make_epub(bindery / "Harbour Tales v02.epub")
    fake = FakeProvider(responses=echo_responder())
    ai.set_provider_override(fake)

    r = client.post("/api/ai/identify", json={"paths": [path.name]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["threshold"] == pytest.approx(0.85)
    [p] = body["proposals"]
    assert p["path"] == path.name
    assert p["confidence"] == pytest.approx(0.93)
    assert p["evidence"] == "Title page and the Open Library record agree."
    prop = p["proposal"]
    assert prop["title"] == "The Quiet Harbour"
    assert prop["series"] == "Harbour Tales" and prop["series_index"] == 2.0
    assert prop["book_type_slug"] == "aitest-novel"
    assert prop["book_type_id"] == book_types["aitest-novel"].id
    # Only candidate tags survive, in the candidate's spelling.
    assert prop["tags"] == ["Mystery"]
    assert p["candidate"]["source_id"] == "OL123W"
    assert p["candidate"]["cover_url"] == "https://covers.example.org/1.jpg"

    [call] = fake.calls
    assert call["feature"] == "bindery_identify"
    assert call["effort"] == "high"
    assert call["schema"]["properties"]["files"]["items"]["additionalProperties"] is False
    sent = call["user_content"][0]["text"]
    assert "Chapter One" in sent and "aitest-comic" in sent and "OL123W" in sent
    assert db.query(AIUsage).filter(AIUsage.feature == "bindery_identify").count() == 1


def test_identify_coerces_bad_model_values(client: TestClient, bindery: Path, sources,
                                           book_types, admin_with_key):
    path = make_epub(bindery / "harbour.epub")
    ai.set_provider_override(FakeProvider(responses=echo_responder(
        confidence=1.7, book_type_slug="not-a-type", candidate_source_id="nope",
        year=99999, content_type="omnibus", title="  ")))
    r = client.post("/api/ai/identify", json={"paths": [path.name]})
    assert r.status_code == 200, r.text
    [p] = r.json()["proposals"]
    assert p["confidence"] == 1.0
    assert p["candidate"] is None
    prop = p["proposal"]
    assert prop["book_type_slug"] is None and prop["book_type_id"] is None
    assert prop["year"] is None
    assert prop["content_type"] == "volume"
    assert prop["title"]  # fell back to the filename guess


def test_identify_batches_ten_files_per_call(client: TestClient, db: Session, bindery: Path,
                                             sources, book_types, admin_with_key):
    paths = []
    for i in range(12):
        p = bindery / f"Harbour Tales v{i + 1:02d}.pdf"
        make_pdf(p, [f"Volume {i + 1}"])
        paths.append(p.name)
    fake = FakeProvider(responses=echo_responder())
    ai.set_provider_override(fake)

    r = client.post("/api/ai/identify", json={"paths": paths})
    assert r.status_code == 200, r.text
    proposals = r.json()["proposals"]
    assert [p["path"] for p in proposals] == paths
    assert len(fake.calls) == 2
    sizes = []
    for call in fake.calls:
        text = call["user_content"][0]["text"]
        sizes.append(len(json.loads(text[text.index("{"):])["files"]))
    assert sizes == [10, 2]
    # The output budget grows with the batch (Opus thinks inside it).
    assert [c["max_tokens"] for c in fake.calls] == [14096, 6096]
    assert len(sources) == 12
    assert db.query(AIUsage).filter(AIUsage.feature == "bindery_identify").count() == 2


def test_identify_marks_files_the_model_skipped(client: TestClient, bindery: Path, sources,
                                                book_types, admin_with_key):
    a = make_pdf(bindery / "a.pdf", ["A"])
    b = make_pdf(bindery / "b.pdf", ["B"])

    def only_first(feature, user_content, schema):
        return {"files": [echo_responder()(feature, user_content, schema)["files"][0]]}

    ai.set_provider_override(FakeProvider(responses=only_first))
    r = client.post("/api/ai/identify", json={"paths": [a.name, b.name]})
    assert r.status_code == 200
    first, second = r.json()["proposals"]
    assert first["proposal"] is not None
    assert second["proposal"] is None and second["confidence"] == 0.0


def test_identify_limits_paths_per_call(client: TestClient, admin_with_key, bindery: Path):
    ai.set_provider_override(FakeProvider())
    r = client.post("/api/ai/identify", json={"paths": [f"f{i}.epub" for i in range(51)]})
    assert r.status_code == 422


# ── path safety and permissions ──────────────────────────────────────────────

def test_identify_rejects_path_outside_bindery(client: TestClient, tmp_path: Path, bindery: Path,
                                               sources, admin_with_key):
    make_epub(tmp_path / "outside.epub")
    fake = FakeProvider(responses=echo_responder())
    ai.set_provider_override(fake)
    r = client.post("/api/ai/identify", json={"paths": ["../outside.epub"]})
    assert r.status_code == 400
    assert fake.calls == [] and sources == []


def test_identify_missing_file_is_404(client: TestClient, bindery: Path, admin_with_key):
    fake = FakeProvider()
    ai.set_provider_override(fake)
    r = client.post("/api/ai/identify", json={"paths": ["nope.epub"]})
    assert r.status_code == 404
    assert fake.calls == []


def test_identify_guest_forbidden(client: TestClient, db: Session, bindery: Path):
    make_epub(bindery / "harbour.epub")
    _, headers = _make_user(db, "idguest", "guest")
    ai.set_provider_override(FakeProvider())
    r = client.post("/api/ai/identify", json={"paths": ["harbour.epub"]}, headers=headers)
    assert r.status_code == 403


def test_identify_member_with_own_key(client: TestClient, db: Session, bindery: Path,
                                      sources, book_types):
    make_epub(bindery / "harbour.epub")
    member, headers = _make_user(db, "idmember", "member")
    ai_settings.set_user_key(db, member, KEY)
    db.flush()
    ai.set_provider_override(FakeProvider(responses=echo_responder()))
    r = client.post("/api/ai/identify", json={"paths": ["harbour.epub"]}, headers=headers)
    assert r.status_code == 200, r.text
    assert db.query(AIUsage).filter(AIUsage.user_id == member.id).count() == 1


def test_identify_without_key_is_409_and_skips_evidence(client: TestClient, bindery: Path, sources):
    make_epub(bindery / "harbour.epub")
    fake = FakeProvider()
    ai.set_provider_override(fake)
    r = client.post("/api/ai/identify", json={"paths": ["harbour.epub"]})
    assert r.status_code == 409
    assert fake.calls == [] and sources == []


def test_identify_feature_off_is_404(client: TestClient, db: Session, bindery: Path, sources,
                                     admin_with_key):
    make_epub(bindery / "harbour.epub")
    ai_settings.set_feature_enabled(db, "bindery_identify", False)
    db.flush()
    fake = FakeProvider()
    ai.set_provider_override(fake)
    r = client.post("/api/ai/identify", json={"paths": ["harbour.epub"]})
    assert r.status_code == 404
    assert fake.calls == []


# ── accept audit ─────────────────────────────────────────────────────────────

def _accept_audit(db: Session) -> list[dict]:
    rows = db.query(AuditLog).filter(AuditLog.action == "bindery.accepted").all()
    return [json.loads(r.details) for r in rows]


def test_accept_audit_records_ai_assisted(client: TestClient, db: Session, bindery: Path):
    make_epub(bindery / "harbour.epub")
    make_epub(bindery / "other.epub", title="A Second Tide")
    r = client.post("/api/bindery/accept", json={"files": [
        {"path": "harbour.epub", "title": "The Quiet Harbour", "author": "M. Vale",
         "series": "Harbour Tales", "series_index": 2, "ai_assisted": True},
        {"path": "other.epub", "title": "A Second Tide"},
    ]})
    assert r.status_code == 200, r.text
    assert len(r.json()["accepted"]) == 2
    details = _accept_audit(db)
    assert len(details) == 2
    by_path = {d["source_path"]: d for d in details}
    assert by_path["harbour.epub"]["ai_assisted"] is True
    assert "ai_assisted" not in by_path["other.epub"]
