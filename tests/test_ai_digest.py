"""Reading companion mechanism: chapter text (book_text), per-chapter digests
(spoiler window, citation check, fold, scope), the digest job and its
triggers. FakeProvider only; books and names are made up."""
from __future__ import annotations

import json
import re
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from ebooklib import epub
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.ai_chapter_digest import AIChapterDigest
from backend.models.ai_entity import AIEntity, AIEntityNote
from backend.models.ai_usage import AIUsage
from backend.models.book import Book, BookChapter
from backend.models.tome_sync import ApiKey, TomeSyncPosition
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services import ai, book_text
from backend.services.ai import companion, digest, digest_job, settings as ai_settings
# Imported here so previously_recap is registered before the fixtures copy the
# follow-up registry (imported first inside a test, it lands in that test's
# copy and is gone for the rest of the file).
from backend.services.ai import recap as _recap  # noqa: F401
from backend.services.ai.provider import AIProviderError, AIRefused, AIResult
from backend.services.chapters import replace_book_chapters
from backend.services.metadata import _WORD_RE, _html_to_text, epub_chapter_documents, extract_chapters_epub
from tests.ai_fake import FakeProvider

KEY = "sk-ant-test-digest-000000000000Ab12"

CHAPTERS = [
    ("The Harbour", "Mara Vell walked down to the harbour at dawn. "
                    "The fog sat on the water and the ferry bell was silent. " * 4),
    ("The Bell", "Tobin Ash rang the ferry bell twice. Mara Vell heard it from the "
                 "lighthouse and counted the strokes. " * 4),
    ("The Lighthouse", "The keeper, Oren Pike, kept a ledger of every ship. "
                       "Mara Vell read it by lamplight. " * 4),
    ("The Return", "The ferry came back without its captain. Tobin Ash tied it to the "
                   "post and said nothing at all. " * 4),
]


# ── fixtures ─────────────────────────────────────────────────────────────────

def make_epub(path: Path, chapters: list[tuple[str, str]] = CHAPTERS) -> Path:
    """An EPUB3 with a nav TOC; the nav document is in the spine, so chapter
    one carries some front matter (as real books do)."""
    book = epub.EpubBook()
    book.set_identifier(f"digest-{path.stem}")
    book.set_title(path.stem)
    book.set_language("en")
    items = []
    for i, (title, body) in enumerate(chapters):
        c = epub.EpubHtml(title=title, file_name=f"c{i}.xhtml", lang="en")
        c.content = f"<html><body><h1>{title}</h1><p>{body}</p><p>Mark{i}end.</p></body></html>"
        book.add_item(c)
        items.append(c)
    book.toc = tuple(items)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + items
    epub.write_epub(str(path), book)
    return path


def make_ncx_only_epub(path: Path, chapters: list[tuple[str, str]] = CHAPTERS) -> Path:
    """An EPUB2 whose TOC lives only in the NCX: ebooklib (ignore_ncx) finds no
    map, so the zip-level parser produces it."""
    manifest, spine, nav = [], [], []
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml",
                    '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                    '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                    "</rootfiles></container>")
        for i, (title, body) in enumerate(chapters):
            zf.writestr(f"OEBPS/text/c{i}.xhtml",
                        f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head>'
                        f"<body><h1>{title}</h1><p>{body}</p><p>Mark{i}end.</p></body></html>")
            manifest.append(f'<item id="c{i}" href="text/c{i}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{i}"/>')
            nav.append(f'<navPoint id="n{i}" playOrder="{i + 1}"><navLabel><text>{title}</text></navLabel>'
                       f'<content src="text/c{i}.xhtml"/></navPoint>')
        zf.writestr("OEBPS/content.opf",
                    '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
                    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title></metadata>'
                    f'<manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
                    f'{"".join(manifest)}</manifest><spine toc="ncx">{"".join(spine)}</spine></package>')
        zf.writestr("OEBPS/toc.ncx",
                    '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                    f'<navMap>{"".join(nav)}</navMap></ncx>')
    return path


def _make_user(db: Session, username: str, role: str, *, key: bool = True) -> User:
    user = User(username=username, email=f"{username}@example.com",
                hashed_password=hash_password("pw123456"), is_active=True,
                is_admin=(role == "admin"), role=role, must_change_password=False)
    db.add(user)
    db.flush()
    if key:
        ai_settings.set_user_key(db, user, KEY)
        db.flush()
    return user


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=user.id)}"}


def _epub_book(db: Session, make_book, tmp_path: Path, title: str, *, series: str | None = None,
               index: float | None = None, chapters=CHAPTERS, builder=make_epub) -> Book:
    path = builder(tmp_path / f"{title.replace(' ', '_')}.epub", chapters)
    book = make_book(title=title, series=series, series_index=index, file_path=str(path))
    replace_book_chapters(db, book.id, extract_chapters_epub(path))
    db.flush()
    return book


def _read(db: Session, user: User, book: Book) -> None:
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read", progress_pct=1.0))
    db.flush()


def _store_digest(db: Session, book: Book, idx: int, entries: list[dict], summary: str = "") -> AIChapterDigest:
    row = AIChapterDigest(book_id=book.id, chapter_idx=idx, model="claude-sonnet-5-5",
                          summary=summary or f"Summary {book.id}:{idx}",
                          entries_json=json.dumps([_stored(e) for e in entries]))
    db.add(row)
    db.flush()
    return row


def _stored(e: dict) -> dict:
    """An answer item as entries_json keeps it (``identity`` is stored as ``more``)."""
    out = {"name": e["name"], "kind": e["kind"], "line": e["line"],
           "more": e["more"] if "more" in e else e.get("identity", ""), "evidence": e["evidence"]}
    if "entity_id" in e:
        out["entity_id"] = e["entity_id"]
    return out


def _entry(name: str, line: str, kind: str = "person", evidence: str = "x", *, ref: int = 0,
           identity: str | None = None, aliases: list[str] | None = None,
           facts: dict | None = None) -> dict:
    """An answer item in the entity shape (``ref`` 0: new). ``identity``
    defaults to "More on {name}." (stored as the item's ``more``)."""
    return {"ref": ref, "name": name, "kind": kind,
            "identity": f"More on {name}." if identity is None else identity,
            "aliases": aliases or [], "line": line,
            "facts": {f: (facts or {}).get(f, "") for f in digest.FACT_FIELDS}, "evidence": evidence}


_HEADING_RE = re.compile(r"^=== \[c(\d+)\](?: [^\n]*)? ===\n\n", re.S)


def chapter_blocks(user_content) -> list[tuple[int, str]]:
    """(chapter number, text) of each chapter block of a multi-chapter call."""
    out = []
    for b in user_content:
        m = _HEADING_RE.match(b["text"])
        if m:
            out.append((int(m.group(1)), b["text"][m.end():]))
    return out


def _echo_one(text: str) -> dict:
    words = text.split()
    return {"summary": "Something happened.", "same_as": [],
            "entries": [_entry(words[0], "A word.", kind="term", identity="",
                               evidence=" ".join(words[:6]))]}


def echo_digest(feature, user_content, schema):
    """Answers with one entry per chapter, quoting the chapter's first six
    words: ``{summary, entries}`` for one chapter, ``{chapters: [...]}`` for
    a multi-chapter call."""
    if schema is not None and "chapters" in schema.get("properties", {}):
        return {"chapters": [{"chapter": n, **_echo_one(text)} for n, text in chapter_blocks(user_content)]}
    return _echo_one(user_content[-1]["text"])


def sent_text(call: dict) -> str:
    """Every text block of a call, joined."""
    return "\n".join(b["text"] for b in call["user_content"])


@pytest.fixture()
def one_chapter_units(monkeypatch):
    """Each unit is one chapter, for tests about per-chapter failure handling."""
    monkeypatch.setattr(digest, "CHUNK_CHAPTERS", 1)


@pytest.fixture(autouse=True)
def _jobs_inline(db: Session, monkeypatch):
    """Jobs never start a thread here; tests run them with run_pending(). The
    job gets its own session on the test connection, joined through a
    SAVEPOINT, so its commits are visible to ``db`` and a rollback after a
    failed chapter does not discard the test's fixtures."""
    @contextmanager
    def same_session():
        s = Session(bind=db.get_bind(), join_transaction_mode="create_savepoint", autoflush=False)
        try:
            yield s
        finally:
            s.close()

    digest_job.reset()
    digest.reset_triggers()
    monkeypatch.setattr(digest_job, "_spawn_worker", lambda: None)
    monkeypatch.setattr(digest_job, "_session_factory", same_session)
    # Follow-ups registered at import (recap's previously_recap) must survive
    # the tests that register their own.
    monkeypatch.setattr(digest_job, "_followups", dict(digest_job._followups))
    yield
    digest_job.reset()
    digest.reset_triggers()


# ── book_text ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("builder", [make_epub, make_ncx_only_epub])
def test_chapter_text_aligns_with_stored_fractions(db, make_book, tmp_path, builder):
    book = _epub_book(db, make_book, tmp_path, f"Align {builder.__name__}", builder=builder)
    chapters = book_text.book_chapters(book, db)
    assert len(chapters) == 4

    path = Path(book.files[0].file_path)
    htmls, file_map = epub_chapter_documents(path)
    tokens = [w for h in htmls for w in _WORD_RE.findall(_html_to_text(h))]
    total = len(tokens)
    for ch in chapters:
        text = book_text.chapter_text(book, ch.idx, db)
        start = round(ch.start_fraction * total)
        end = total if ch.end_fraction >= 1.0 else round(ch.end_fraction * total)
        # Exactly the counted words the map says belong to this chapter.
        assert _WORD_RE.findall(text) == tokens[start:end]
        assert f"Mark{ch.idx}end" in text
        for other in range(4):
            if other != ch.idx:
                assert f"Mark{other}end" not in text
    # Chapter k (k >= 1) starts at its own heading.
    assert book_text.chapter_text(book, 2, db).startswith("The Lighthouse")
    assert book_text.chapter_texts(book, db)[3] == book_text.chapter_text(book, 3, db)


def test_chapter_text_display_keeps_paragraphs_and_decodes_entities(db, make_book, tmp_path):
    book = _epub_book(db, make_book, tmp_path, "Entities", chapters=[
        ("First", "Salt &amp; iron. The <i>Wren</i> sailed at noon " * 5),
        ("Second", "Nothing else happened that day at all, not one thing, " * 3),
    ])
    text = book_text.chapter_text(book, 0, db)
    assert "Salt & iron." in text and "The Wren sailed" in text
    assert "<i>" not in text and "&amp;" not in text
    assert "\n" in text   # headings and paragraphs on their own lines


def test_chapter_text_until_cuts_at_the_position(db, make_book, tmp_path):
    book = _epub_book(db, make_book, tmp_path, "Until")
    ch = book_text.book_chapters(book, db)[1]
    mid = (ch.start_fraction + ch.end_fraction) / 2
    full = book_text.chapter_text(book, 1, db)
    cut = book_text.chapter_text(book, 1, db, until=mid)
    assert full.startswith(cut.rstrip()[:40])
    assert len(_WORD_RE.findall(cut)) < len(_WORD_RE.findall(full))
    assert "Mark1end" not in cut


def test_chapter_text_until_is_exactly_the_words_before_the_position(db, make_book, tmp_path):
    book = _epub_book(db, make_book, tmp_path, "Until Exact")
    docs = book_text.load_book_docs(book_text.epub_path(book))
    for ch in book_text.book_chapters(book, db):
        start, end = book_text._bounds(docs, ch)
        full = _WORD_RE.findall(book_text.chapter_text(book, ch.idx, db))
        assert len(full) == end - start
        for f in (0.0, 0.1, 0.37, 0.5, 0.99):
            until = ch.start_fraction + f * (ch.end_fraction - ch.start_fraction)
            cut = _WORD_RE.findall(book_text.chapter_text(book, ch.idx, db, until=until))
            # Exactly the chapter's words before the reader's word, not one more.
            assert cut == full[:max(0, min(end, round(until * docs.total)) - start)], (ch.idx, f)
        if ch.start_fraction > 0:
            assert book_text.chapter_text(book, ch.idx, db, until=ch.start_fraction - 0.01) == ""
        assert book_text.chapter_text(book, ch.idx, db, until=1.0) == book_text.chapter_text(book, ch.idx, db)


def test_no_chapter_map_and_not_epub(db, make_book, tmp_path):
    path = make_epub(tmp_path / "nomap.epub")
    no_map = make_book(title="No Map", file_path=str(path))
    assert book_text.chapter_text(no_map, 0, db) is None
    assert book_text.chapter_texts(no_map, db) is None
    assert book_text.chapter_for_fraction(no_map, 0.5, db) is None
    assert book_text.unavailable_reason(no_map, db) == "no_chapters"

    pdf = make_book(title="A Manual", file_format="pdf")
    assert book_text.unavailable_reason(pdf, db) == "not_epub"


def test_chapter_for_fraction_rounds_down(db, make_book, tmp_path):
    book = _epub_book(db, make_book, tmp_path, "Rounding")
    chapters = book_text.book_chapters(book, db)
    s1, s2 = chapters[1].start_fraction, chapters[2].start_fraction
    assert book_text.chapter_for_fraction(book, 0.0, db) == 0
    assert book_text.chapter_for_fraction(book, s1 - 1e-4, db) == 0
    assert book_text.chapter_for_fraction(book, s1, db) == 1      # on the boundary
    assert book_text.chapter_for_fraction(book, (s1 + s2) / 2, db) == 1
    assert book_text.chapter_for_fraction(book, 1.0, db) == 3
    assert book_text.chapter_for_fraction(book, None, db) is None


def test_chapter_for_fraction_none_before_chapter_zero(db, make_book):
    book = make_book(title="Late Start")
    db.add_all([
        BookChapter(book_id=book.id, idx=0, title="One", start_fraction=0.2, end_fraction=0.6),
        BookChapter(book_id=book.id, idx=1, title="Two", start_fraction=0.6, end_fraction=1.0),
    ])
    db.flush()
    assert book_text.chapter_for_fraction(book, 0.1, db) is None
    assert book_text.chapter_for_fraction(book, 0.2, db) == 0


# ── the reader's position in word units ─────────────────────────────────────

PARAS = 10           # paragraphs per chapter, "S<chapter>_<n>" marks each one
WORDS_PER_PAGE = 50  # the page model below: every document starts a new page


def illustrated_chapters() -> list[tuple[str, list[str]]]:
    return [(f"Part {i + 1}", [f"S{i}_{n:02d} the tide turned and the boats came in slow" for n in range(PARAS)])
            for i in range(4)]


def make_illustrated_epub(path: Path, chapters=None, plates: int = 8) -> Path:
    """An EPUB2 (NCX TOC, zip-level parser) whose spine opens with ``plates``
    image-only documents (cover, colour plates) and then one document per
    chapter: <h1> then one <p> per marked sentence. Pages without words, as in
    the illustrated light novels this is for."""
    chapters = chapters or illustrated_chapters()
    manifest, spine, nav = [], [], []
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml",
                    '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                    '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                    "</rootfiles></container>")
        for i in range(plates):
            zf.writestr(f"OEBPS/text/p{i}.xhtml",
                        '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml">'
                        f'<head><title></title></head><body><div><img src="../img/p{i}.jpg" alt=""/></div>'
                        "</body></html>")
            manifest.append(f'<item id="p{i}" href="text/p{i}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="p{i}"/>')
        for i, (title, paras) in enumerate(chapters):
            body = "".join(f"<p>{t}</p>\n" for t in paras)
            zf.writestr(f"OEBPS/text/c{i}.xhtml",
                        '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml">'
                        f"<head><title></title></head><body>\n<h1>{title}</h1>\n{body}</body></html>")
            manifest.append(f'<item id="c{i}" href="text/c{i}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{i}"/>')
            nav.append(f'<navPoint id="n{i}" playOrder="{i + 1}"><navLabel><text>{title}</text></navLabel>'
                       f'<content src="text/c{i}.xhtml"/></navPoint>')
        zf.writestr("OEBPS/content.opf",
                    '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
                    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title></metadata>'
                    f'<manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
                    f'{"".join(manifest)}</manifest><spine toc="ncx">{"".join(spine)}</spine></package>')
        zf.writestr("OEBPS/toc.ncx",
                    '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                    f'<navMap>{"".join(nav)}</navMap></ncx>')
    return path


def _illustrated_book(db, make_book, tmp_path, title: str = "Illustrated", plates: int = 8) -> Book:
    path = make_illustrated_epub(tmp_path / f"{title.replace(' ', '_')}.epub", plates=plates)
    book = make_book(title=title, file_path=str(path))
    replace_book_chapters(db, book.id, extract_chapters_epub(path))
    db.flush()
    return book


def device_position(book: Book, chapter: int, para: int, plates: int = 8) -> tuple[float, str, str]:
    """What a device reports with the top of its page at paragraph ``para``
    (0-based) of ``chapter``: (page fraction, KOReader xpointer, web CFI).
    The fraction counts pages (``getLastPercent``: the current page over the
    page count, image pages and each chapter's last half-empty page
    included), so it runs ahead of the word fraction."""
    docs = book_text.load_book_docs(book_text.epub_path(book))
    pages = [max(1, -(-(docs.cum[i + 1] - docs.cum[i]) // WORDS_PER_PAGE)) for i in range(len(docs.docs))]
    d = plates + chapter
    words_into_doc = 2 + 11 * para           # the heading, then 11 words per paragraph
    page = sum(pages[:d]) + words_into_doc // WORDS_PER_PAGE + 1
    xp = f"/body/DocFragment[{d + 1}]/body/p[{para + 1}]/text().0"
    cfi = f"epubcfi(/6/{2 * (d + 1)}!/4/{2 * (para + 2)},/1:0,/1:20)"   # body/<h1>,<p>... range
    return page / sum(pages), xp, cfi


def test_parse_locator():
    loc = book_text.parse_locator("/body/DocFragment[8]/body/section/div/p[48]/text()[1].0")
    assert loc == book_text.Locator(doc=7, steps=(("body", 1), ("section", 1), ("div", 1), ("p", 48)))
    loc = book_text.parse_locator("epubcfi(/6/28!/4,/22,/44/3:194)")
    assert loc == book_text.Locator(doc=13, steps=((None, 2), (None, 11)))
    loc = book_text.parse_locator("epubcfi(/6/18!/4/2[sbo-rt-content]/2/2[what_is],/2,/12/2[x])")
    assert loc == book_text.Locator(doc=8, steps=((None, 2), (None, 1), (None, 1), (None, 1), (None, 1)))
    for junk in (None, "", "10", "cfi(b)", "comic:12", "epubcfi(/6/3!/4)"):
        assert book_text.parse_locator(junk) is None, junk


def test_page_fraction_runs_ahead_and_the_locator_places_the_reader(db, make_book, tmp_path):
    book = _illustrated_book(db, make_book, tmp_path)
    chapters = book_text.book_chapters(book, db)
    assert len(chapters) == 4
    # The top of the reader's page is the last paragraph of chapter 1.
    pct, xp, cfi = device_position(book, 1, PARAS - 1)
    # The page fraction alone would put them at least a chapter further on.
    assert book_text.chapter_for_fraction(book, pct, db) >= 2
    for locator in (xp, cfi):
        w = book_text.reading_fraction(book, pct, locator, db)
        assert book_text.chapter_for_fraction(book, w, db) == 1, locator
        window = companion.pct_window(db, book, pct, locator)
        assert window.chapter_idx == 1
        text = companion.window_text(book, window, db)
        assert f"S1_{PARAS - 2:02d}" in text                 # read
        assert f"S1_{PARAS - 1:02d}" not in text             # the top of the page: cut before it
        assert "S2_" not in text and "S3_" not in text
    # Without a locator the fraction is backed off, never used as it is.
    assert book_text.reading_fraction(book, pct, None, db) == pytest.approx(
        pct - book_text.POSITION_MARGIN, abs=0.01)
    # A finished book stays finished.
    assert book_text.reading_fraction(book, 1.0, xp, db) == 1.0


def test_locator_mid_chapter_cuts_at_the_paragraph(db, make_book, tmp_path):
    book = _illustrated_book(db, make_book, tmp_path, "Mid Chapter")
    for para in (0, 3, 7):
        pct, xp, cfi = device_position(book, 2, para)
        for locator in (xp, cfi):
            window = companion.pct_window(db, book, pct, locator)
            assert window.chapter_idx == 2
            text = companion.window_text(book, window, db)
            assert all(f"S2_{n:02d}" in text for n in range(para)), (para, locator)
            assert all(f"S2_{n:02d}" not in text for n in range(para, PARAS)), (para, locator)


def test_device_position_digests_only_finished_chapters(db, make_book, tmp_path, plugin_client):
    c, user, h = plugin_client
    book = _illustrated_book(db, make_book, tmp_path, "Device Digest")
    # The top of the page is the last paragraph of chapter 3; the page
    # fraction alone would put the reader at the end of the book.
    pct, xp, _ = device_position(book, 3, PARAS - 1)
    r = c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": pct, "progress": xp}, headers=h)
    assert r.status_code == 200, r.text
    [job] = _queued()
    assert job["upto_chapter"] == 3                 # chapters 0-2: the reader is still in 3
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    digest_job.run_pending()
    sent = json.dumps([call["user_content"] for call in fake.calls])
    assert "S0_" in sent and "S2_" in sent and "S3_" not in sent


def test_plugin_recap_and_recall_use_the_xpointer(db, make_book, tmp_path, plugin_client):
    c, user, h = plugin_client
    book = _illustrated_book(db, make_book, tmp_path, "Device Recap")
    _store_digest(db, book, 0, [])
    db.commit()
    pct, xp, _ = device_position(book, 1, 5)
    fake = FakeProvider(responses=[{"paragraphs": ["The boats came in."]},
                                   {"found": False, "name": "", "kind": "term", "line": "",
                                    "more": "", "evidence": ""}])
    ai.set_provider_override(fake)
    r = c.post(f"/api/tome-sync/ai/books/{book.id}/recap", json={"pct": pct, "progress": xp}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["chapter_idx"] == 1
    r = c.post(f"/api/tome-sync/ai/books/{book.id}/recall",
               json={"term": "tide", "pct": pct, "progress": xp}, headers=h)
    assert r.status_code == 200, r.text
    for call in fake.calls:
        sent = json.dumps(call["user_content"])
        assert "S1_04" in sent and "S1_05" not in sent and "S2_" not in sent


# ── citation check ───────────────────────────────────────────────────────────

def test_citation_check_drops_invented_and_keeps_normalised():
    text = "Mara Vell walked down to the harbour at dawn. “Wait,” said Tobin Ash."
    entries = digest.check_entries([
        _entry("Mara Vell", "A walker.", evidence="mara   VELL walked down"),          # case + spaces
        _entry("Tobin Ash", "A speaker.", evidence='"Wait," said Tobin Ash.'),          # quote style
        _entry("Oren Pike", "The keeper.", evidence="Oren Pike kept a ledger"),         # not in text
        _entry("Harbour", "A place.", kind="place", evidence=""),                       # no evidence
        _entry("mara vell", "Duplicate.", evidence="Mara Vell walked"),                  # same name again
        {"name": "Fog", "kind": "weather", "line": "Grey.", "more": "", "evidence": "at dawn"},  # bad kind
    ], text)
    assert [e["name"] for e in entries] == ["Mara Vell", "Tobin Ash"]


def test_citation_check_cuts_long_evidence_to_twenty_words():
    words = [f"w{i}" for i in range(30)]
    text = " ".join(words)
    [e] = digest.check_entries([_entry("w0", "A word.", evidence=text)], text)
    assert e["evidence"] == " ".join(words[:20])


def test_citation_check_needs_a_quote_that_names_the_entry():
    text = ("Two households, both alike in dignity, In fair Verona, where we lay our scene. "
            "Nurse: Juliet, come hither. Enter Romeo and Mercutio.")
    entries = digest.check_entries([
        # A real sentence, but about nobody of that name: an invented entry.
        _entry("Friar Laurence", "A friar who will marry Romeo and Juliet.",
               evidence="Two households, both alike in dignity"),
        # A mid-word fragment of the text is not a quote.
        _entry("Mab", "A fairy queen.", evidence="in d"),
        # Under three words, but it holds the whole name: kept (see
        # test_a_short_quote_that_is_the_whole_name_keeps_a_main_character).
        _entry("Verona", "A city.", kind="place", evidence="fair Verona"),
        # Under three words and only a part of the name: not a quote.
        _entry("Queen Mab", "A fairy queen.", evidence="our scene"),
        # One part of the name is enough.
        _entry("Juliet Capulet", "A young woman.", evidence="Juliet, come hither"),
        # Case and whitespace differ, the name is there.
        _entry("Romeo", "A young man.", evidence="ENTER   romeo and mercutio"),
        # A title alone does not tie a quote to a name.
        _entry("Lord Capulet", "Juliet's father.", evidence="Juliet, come hither"),
    ], text)
    assert [e["name"] for e in entries] == ["Verona", "Juliet Capulet", "Romeo"]
    assert digest.names_entry("Tybalt", "Tybalt's sword is out")
    assert not digest.names_entry("Mab", "Mabel came in")


# ── fold ─────────────────────────────────────────────────────────────────────

def test_fold_later_line_wins_first_seen_kept_names_merge():
    class D:
        def __init__(self, book_id, idx, entries):
            self.book_id, self.chapter_idx = book_id, idx
            self.entries_json = json.dumps([_stored(e) for e in entries])

    folded = digest.fold_entries([
        D(1, 0, [_entry("Mara Vell", "A walker."), _entry("The Harbour", "A dock.", kind="place")]),
        D(1, 1, [_entry("mara  vell", "A walker who heard the bell."), _entry("Tobin Ash", "A ringer.")]),
        D(2, 0, [_entry("MARA VELL", "The lighthouse reader.")]),
    ])
    assert [e.name for e in folded] == ["Mara Vell", "The Harbour", "Tobin Ash"]
    mara = folded[0]
    assert mara.line == "The lighthouse reader."
    assert mara.first_seen == (1, 0) and mara.last_seen == (2, 0)
    assert folded[1].kind == "place"
    assert mara.to_dict()["first_seen"] == {"book_id": 1, "chapter_idx": 0}
    # chapters: distinct chapters that listed the name (the book page sorts
    # people by it); Tobin and the Harbour were listed once.
    assert [e.chapters for e in folded] == [3, 1, 1]
    assert mara.to_dict()["chapters"] == 3


def test_series_order_puts_volume_one_first(db, make_book, tmp_path):
    user = _make_user(db, "order", "member")
    v2 = _epub_book(db, make_book, tmp_path, "Harbour Two", series="Harbour", index=2)
    v1 = _epub_book(db, make_book, tmp_path, "Harbour One", series="Harbour", index=1)
    make_book(title="Other Series", series="Elsewhere", series_index=1)
    assert [b.id for b in digest.reading_order(db, v2)] == [v1.id, v2.id]

    _read(db, user, v1)
    _store_digest(db, v2, 0, [_entry("Mara Vell", "Volume two line.")])
    _store_digest(db, v1, 0, [_entry("Mara Vell", "Volume one line."), _entry("Oren Pike", "Keeper.")])
    folded = digest.glossary_state(db, user, v2, upto=1)
    assert [e.name for e in folded] == ["Mara Vell", "Oren Pike"]
    assert folded[0].first_seen == (v1.id, 0)
    assert folded[0].line == "Volume two line."


# ── scope ────────────────────────────────────────────────────────────────────

def test_scope_excludes_unread_includes_read(db, make_book, tmp_path):
    user = _make_user(db, "scoper", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Tide One", series="Tide", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Tide Two", series="Tide", index=2)
    v3 = _epub_book(db, make_book, tmp_path, "Tide Three", series="Tide", index=3)

    scope = digest.user_scope(db, user, v3)
    assert scope.included == []
    assert [(b.id, r) for b, r in scope.excluded] == [(v1.id, "not_read"), (v2.id, "not_read")]
    assert [b.id for b in scope.volumes()] == [v3.id]

    _read(db, user, v1)
    scope = digest.user_scope(db, user, v3)
    assert [b.id for b in scope.included] == [v1.id]
    assert [b.id for b, _ in scope.excluded] == [v2.id]
    d = scope.to_dict()
    assert d["excluded_volumes"] == [{"book_id": v2.id, "title": "Tide Two", "series_index": 2.0,
                                      "reason": "not_read"}]
    assert d["included_volumes"][0]["book_id"] == v1.id
    assert d["scope_hash"] != digest.user_scope(db, _make_user(db, "other", "member"), v3).scope_hash


def test_scope_read_volume_without_chapters_is_excluded(db, make_book, tmp_path):
    user = _make_user(db, "scoper2", "member")
    v1 = make_book(title="Gale One", series="Gale", series_index=1)  # no chapter map
    v2 = _epub_book(db, make_book, tmp_path, "Gale Two", series="Gale", index=2)
    _read(db, user, v1)
    scope = digest.user_scope(db, user, v2)
    assert scope.included == []
    assert scope.excluded[0][1] in ("no_chapters", "not_epub")


# ── digest_chapter: the spoiler window ───────────────────────────────────────

def test_digest_chapter_sends_exactly_chapter_k(db, make_book, tmp_path):
    user = _make_user(db, "reader", "member")
    book = _epub_book(db, make_book, tmp_path, "Window")
    # Digests of earlier AND later chapters exist; only earlier ones may show.
    _store_digest(db, book, 0, [_entry("Mara Vell", "Walked to the harbour.")])
    _store_digest(db, book, 3, [_entry("Captainless Ferry", "LATER-SPOILER line.")])

    fake = FakeProvider(responses=[{
        "summary": "Tobin rang the bell.",
        "entries": [_entry("Tobin Ash", "Rings the ferry bell.", evidence="Tobin Ash rang the ferry bell twice")],
    }])
    ai.set_provider_override(fake)
    row = digest.digest_chapter(db, user, book, 1)

    [call] = fake.calls
    assert call["feature"] == "reading_digest"
    assert call["model"] == "claude-sonnet-5-5"
    assert call["effort"] == "low" and call["max_tokens"] == digest.MAX_TOKENS == 6000
    blocks = call["user_content"]
    assert blocks[-1]["text"] == book_text.chapter_text(book, 1, db)
    sent = json.dumps(blocks) + call["system"]
    for later in (2, 3):
        assert f"Mark{later}end" not in sent
    assert "Oren Pike" not in sent            # first named in chapter 2
    assert "LATER-SPOILER" not in sent        # a later digest never feeds an earlier one
    # The state block comes first: names and kinds only, no descriptions.
    assert blocks[0]["text"].startswith(digest.STATE_HEADER)
    assert blocks[0]["text"].split("\n")[1:] == ['{"id": 0, "name": "Mara Vell", "kind": "person", "identity": ""}']
    assert "Walked to the harbour." not in sent
    context = json.loads(blocks[1]["text"].split("\n\n", 1)[1].rsplit("\n\nThe chapter text", 1)[0])
    assert context["chapter"] == {"number": 2, "of": 4, "title": "The Bell"}

    assert row.chapter_idx == 1 and row.summary == "Tobin rang the bell."
    assert [e["name"] for e in json.loads(row.entries_json)] == ["Tobin Ash"]
    assert db.query(AIUsage).filter_by(user_id=user.id, feature="reading_digest").count() == 1

    # Stored once: a second call is free.
    assert digest.digest_chapter(db, user, book, 1).id == row.id
    assert len(fake.calls) == 1


def test_digest_chapter_drops_uncited_entries(db, make_book, tmp_path):
    user = _make_user(db, "cite", "member")
    book = _epub_book(db, make_book, tmp_path, "Cite")
    ai.set_provider_override(FakeProvider(responses=[{
        "summary": "Mara went to the harbour.",
        "entries": [
            _entry("Mara Vell", "Walks at dawn.", evidence="MARA VELL walked  down to the harbour"),
            _entry("Oren Pike", "Invented here.", evidence="Oren Pike waved from the cliff"),
        ],
    }]))
    row = digest.digest_chapter(db, user, book, 0)
    assert [e["name"] for e in json.loads(row.entries_json)] == ["Mara Vell"]


# ── job ──────────────────────────────────────────────────────────────────────

def test_job_digests_missing_chapters_only_one_usage_row_each(db, make_book, tmp_path):
    user = _make_user(db, "jobber", "member")
    book = _epub_book(db, make_book, tmp_path, "Jobbed")
    _store_digest(db, book, 1, [])
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)

    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4)
    assert snap["status"] == "queued"
    digest_job.run_pending()
    job = digest_job.get(snap["id"])
    assert job["status"] == "done"
    assert job["total"] == 3 and job["done"] == 3 and job["failed"] == 0
    # Chapter 1 is stored, so the gap splits the work into two units: [0] and [2, 3].
    assert len(fake.calls) == 2
    assert [len(chapter_blocks(c["user_content"])) for c in fake.calls] == [0, 2]
    assert {r.chapter_idx for r in db.query(AIChapterDigest).filter_by(book_id=book.id)} == {0, 1, 2, 3}
    rows = db.query(AIUsage).filter_by(user_id=user.id, feature="reading_digest").all()
    assert len(rows) == 2 and all(r.key_source == "user" for r in rows)


def test_job_scope_covers_read_earlier_volumes_first(db, make_book, tmp_path):
    user = _make_user(db, "series_jobber", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Reef One", series="Reef", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Reef Two", series="Reef", index=2)
    _read(db, user, v1)
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)

    digest_job.enqueue(user_id=user.id, book_id=v2.id, upto=1)
    digest_job.run_pending()
    order = [(r.book_id, r.chapter_idx) for r in db.query(AIChapterDigest).order_by(AIChapterDigest.id)]
    assert order == [(v1.id, 0), (v1.id, 1), (v1.id, 2), (v1.id, 3), (v2.id, 0)]


def test_job_enqueue_coalesces(db, make_book, tmp_path):
    user = _make_user(db, "coalesce", "member")
    book = _epub_book(db, make_book, tmp_path, "Coalesced")
    a = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=2)
    b = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=1)
    assert b["id"] == a["id"] and b["upto_chapter"] == 2
    c = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=3, followups=["previously_recap"])
    assert c["id"] == a["id"] and c["upto_chapter"] == 3
    assert c["followups"] == [{"name": "previously_recap", "status": "pending"}]
    other = _make_user(db, "coalesce2", "member")
    assert digest_job.enqueue(user_id=other.id, book_id=book.id, upto=1)["id"] != a["id"]


def test_job_continues_past_a_failing_chapter(db, make_book, tmp_path, one_chapter_units):
    user = _make_user(db, "flaky", "member")
    book = _epub_book(db, make_book, tmp_path, "Flaky")

    def responder(feature, user_content, schema):
        if "Mark1end" in user_content[-1]["text"]:
            raise AIProviderError("Anthropic returned an error: overloaded")
        return echo_digest(feature, user_content, schema)

    ai.set_provider_override(FakeProvider(responses=responder))
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4)
    digest_job.run_pending()
    job = digest_job.get(snap["id"])
    assert job["status"] == "done"
    assert job["done"] == 4 and job["failed"] == 1
    assert job["errors"] == [{"book_id": book.id, "chapter_idx": 1,
                              "message": "Anthropic returned an error: overloaded"}]
    assert {r.chapter_idx for r in db.query(AIChapterDigest).filter_by(book_id=book.id)} == {0, 2, 3}


def test_job_stops_on_not_configured(db, make_book, tmp_path):
    user = _make_user(db, "keyless", "member", key=False)
    book = _epub_book(db, make_book, tmp_path, "Keyless")
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4)
    digest_job.run_pending()
    job = digest_job.get(snap["id"])
    assert job["status"] == "stopped"
    assert "No AI key" in job["stop_reason"]
    assert job["done"] == 0 and fake.calls == []


def test_job_stops_after_three_consecutive_failures(db, make_book, tmp_path, one_chapter_units):
    user = _make_user(db, "refused", "member")
    book = _epub_book(db, make_book, tmp_path, "Refused")
    fake = FakeProvider(refuse=True)
    ai.set_provider_override(fake)
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4)
    digest_job.run_pending()
    job = digest_job.get(snap["id"])
    assert job["status"] == "stopped" and "repeated failures" in job["stop_reason"]
    assert len(fake.calls) == 3 and job["failed"] == 3 and job["done"] == 3
    # Each billed refusal is kept as a placeholder, never a digest with content.
    rows = db.query(AIChapterDigest).filter_by(book_id=book.id).all()
    assert sorted(r.chapter_idx for r in rows) == [0, 1, 2]
    assert all(r.model == digest.FAILED_MODEL and r.summary == "" and r.entries_json == "[]" for r in rows)


def test_job_stops_when_reading_digest_is_switched_off(db, make_book, tmp_path):
    user = _make_user(db, "switched", "member")
    book = _epub_book(db, make_book, tmp_path, "Switched Off")
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4)
    ai_settings.set_feature_enabled(db, "reading_digest", False)
    db.commit()
    digest_job.run_pending()
    job = digest_job.get(snap["id"])
    assert job["status"] == "stopped" and job["done"] == 0 and fake.calls == []
    ai_settings.set_feature_enabled(db, "reading_digest", True)
    db.commit()


def test_a_refused_chapter_is_billed_once_and_never_resent(client, db, make_book, tmp_path,
                                                          one_chapter_units):
    user = _make_user(db, "grim", "member")
    book = _epub_book(db, make_book, tmp_path, "Grim")

    def responder(feature, user_content, schema):
        if feature == "reading_digest" and "Mark1end" in user_content[-1]["text"]:
            raise AIRefused(category="general_harms", result=AIResult(
                text=None, parsed=None, model="claude-sonnet-5-5", input_tokens=900, output_tokens=5,
                cache_read_tokens=0, cache_write_tokens=0))
        if feature == "recap":
            return {"paragraphs": ["Mara walked to the harbour."]}
        return echo_digest(feature, user_content, schema)

    fake = FakeProvider(responses=responder)
    ai.set_provider_override(fake)
    digest_job.enqueue(user_id=user.id, book_id=book.id, upto=2)
    digest_job.run_pending()
    failed = digest.get_digest(db, book.id, 1)
    assert failed is not None and failed.model == digest.FAILED_MODEL and failed.input_tokens == 900
    usage = db.query(AIUsage).filter_by(user_id=user.id, feature="reading_digest").count()
    assert usage == 2                                   # the refusal was billed, once
    sent_chapter_1 = lambda: sum(1 for c in fake.calls if "Mark1end" in json.dumps(c["user_content"]))  # noqa: E731
    assert sent_chapter_1() == 1

    # The reader moves on, opens the glossary, asks for a recap: chapter 1 is not sent again.
    db.add(TomeSyncPosition(user_id=user.id, book_id=book.id, device="Kindle",
                            percentage=book_text.book_chapters(book, db)[3].start_fraction + 0.1))
    db.commit()
    digest.on_position_advanced(db, user, book, 1.0 - 1e-6)
    assert _queued() == []                              # one chapter missing: below PENDING_MIN
    r = client.post(f"/api/ai/books/{book.id}/digest", json={}, headers=_headers(user))
    assert r.status_code == 202, r.text
    digest_job.run_pending()
    g = client.get(f"/api/ai/books/{book.id}/glossary", headers=_headers(user)).json()
    assert g["coverage"]["failed"] == 1 and g["coverage"]["digested"] == g["coverage"]["needed"]
    assert g["job"] is None
    r = client.post(f"/api/ai/books/{book.id}/recap", json={}, headers=_headers(user))
    assert r.status_code == 200, r.text
    assert r.json()["partial"] is True                  # written without chapter 1
    assert sent_chapter_1() == 1
    assert db.query(AIUsage).filter_by(user_id=user.id, feature="reading_digest").count() == usage + 1

    # "Ask again" is the explicit retry: the placeholder goes, chapter 1 is tried once more
    # when the reader starts the digest (the recap only says it is needed).
    assert client.delete(f"/api/ai/books/{book.id}/recap", headers=_headers(user)).status_code == 200
    assert digest.get_digest(db, book.id, 1) is None
    r = client.post(f"/api/ai/books/{book.id}/recap", json={}, headers=_headers(user))
    assert r.status_code == 202 and r.json()["needs_digest"] is True
    assert _queued() == []
    r = client.post(f"/api/ai/books/{book.id}/digest", json={}, headers=_headers(user))
    assert r.status_code == 202, r.text
    digest_job.run_pending()
    assert sent_chapter_1() == 2


def test_outage_is_not_stored_and_stays_retryable(db, make_book, tmp_path):
    user = _make_user(db, "outage", "member")
    book = _epub_book(db, make_book, tmp_path, "Outage")
    ai.set_provider_override(FakeProvider(error=AIProviderError("Anthropic returned an error: overloaded")))
    with pytest.raises(AIProviderError):
        digest.digest_chapter(db, user, book, 0)
    assert digest.get_digest(db, book.id, 0) is None


def test_prior_glossary_is_the_series_order_not_the_users_scope(db, make_book, tmp_path):
    """A digest is shared by everyone, so the glossary it is built on must not
    depend on whether the user who triggered it marked volume 1 read."""
    newcomer = _make_user(db, "newcomer", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Strait One", series="Strait", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Strait Two", series="Strait", index=2)
    _store_digest(db, v1, 0, [_entry("Mara Vell", "A ferry pilot from volume one.")])
    assert digest.user_scope(db, newcomer, v2).included == []      # volume 1 not read
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    digest.digest_chapter(db, newcomer, v2, 0)
    assert '{"id": 0, "name": "Mara Vell", "kind": "person", "identity": ""}' in fake.calls[0]["user_content"][0]["text"]


def test_job_cancel_and_followups(db, make_book, tmp_path):
    user = _make_user(db, "canceller", "member")
    book = _epub_book(db, make_book, tmp_path, "Cancelled")
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4)
    assert digest_job.cancel(snap["id"])["status"] == "cancelled"
    digest_job.run_pending()
    assert db.query(AIChapterDigest).filter_by(book_id=book.id).count() == 0

    ai.set_provider_override(FakeProvider(responses=echo_digest))
    seen: list[tuple[int, int]] = []
    digest_job.register_followup("previously_recap", lambda db_, u, b: seen.append((u.id, b.id)))
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4,
                              followups=["previously_recap", "something_else"])
    digest_job.run_pending()
    job = digest_job.get(snap["id"])
    assert seen == [(user.id, book.id)]
    assert job["followups"] == [
        {"name": "previously_recap", "status": "done"},
        {"name": "something_else", "status": "skipped", "message": "Not available yet."},
    ]


# ── triggers ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def plugin_client(db: Session):
    from backend.core.database import get_db
    from backend.main import create_app

    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    user = _make_user(db, "kindle", "member")
    plaintext = ApiKey.generate()
    db.add(ApiKey(user_id=user.id, key_hash=ApiKey.hash_key(plaintext),
                  key_prefix=plaintext[:11], label="test"))
    db.flush()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, user, {"Authorization": f"Bearer {plaintext}"}
    app.dependency_overrides.clear()


def _queued() -> list[dict]:
    return [digest_job.get(j.id) for j in list(digest_job._queue)]


def test_position_put_crossing_a_boundary_enqueues_once(db, make_book, tmp_path, plugin_client):
    c, user, h = plugin_client
    book = _epub_book(db, make_book, tmp_path, "Synced")
    chapters = book_text.book_chapters(book, db)
    inside_0 = chapters[1].start_fraction / 2
    inside_2 = (chapters[2].start_fraction + chapters[3].start_fraction) / 2
    inside_3 = (chapters[3].start_fraction + 1.0) / 2

    r = c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": inside_0}, headers=h)
    assert r.status_code == 200, r.text
    assert _queued() == []                      # still in chapter 0: nothing finished

    r = c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": inside_2}, headers=h)
    assert r.status_code == 200
    assert _queued() == []                      # two finished chapters: fewer than PENDING_MIN

    r = c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": inside_3}, headers=h)
    assert r.status_code == 200
    [job] = _queued()
    assert job["book_id"] == book.id and job["user_id"] == user.id and job["scope"] == "book"
    assert job["upto_chapter"] == 3             # chapters 0-2 only, never the current one

    c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": inside_3 + 0.001}, headers=h)
    assert len(_queued()) == 1                  # same chapter again: no second job

    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    digest_job.run_pending()
    assert len(fake.calls) == 1                 # the three chapters in one call
    assert {r.chapter_idx for r in db.query(AIChapterDigest).filter_by(book_id=book.id)} == {0, 1, 2}


def test_position_trigger_digests_the_current_book_only(db, make_book, tmp_path, plugin_client):
    c, user, h = plugin_client
    v1 = _epub_book(db, make_book, tmp_path, "Sound One", series="Sound", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Sound Two", series="Sound", index=2)
    _read(db, user, v1)
    db.commit()
    digest_job.reset()                          # marking v1 read queued its own job
    chapters = book_text.book_chapters(v2, db)
    pct = (chapters[3].start_fraction + 1.0) / 2
    r = c.put(f"/api/tome-sync/position/{v2.id}", json={"percentage": pct}, headers=h)
    assert r.status_code == 200
    ai.set_provider_override(FakeProvider(responses=echo_digest))
    digest_job.run_pending()
    rows = {(r.book_id, r.chapter_idx) for r in db.query(AIChapterDigest)}
    assert rows == {(v2.id, 0), (v2.id, 1), (v2.id, 2)}   # volume 1 waits for an explicit start

    # Opening the glossary only reads.
    g = c.get(f"/api/tome-sync/ai/books/{v2.id}/glossary?pct={pct}", headers=h).json()
    assert g["job"] is None and _queued() == []
    assert g["coverage"]["digested"] < g["coverage"]["needed"]

    # The explicit series start widens it to the read earlier volume.
    r = c.post(f"/api/tome-sync/ai/books/{v2.id}/digest", json={"pct": pct, "scope": "series"}, headers=h)
    assert r.status_code == 202, r.text
    assert r.json()["job"]["scope"] == "series" and r.json()["scope"] == "series"
    digest_job.run_pending()
    assert {(r.book_id, r.chapter_idx) for r in db.query(AIChapterDigest)} >= {(v1.id, i) for i in range(4)}


def test_web_status_put_with_a_cfi_places_the_reader(db, make_book, tmp_path):
    from backend.core.database import get_db
    from backend.main import create_app

    user = _make_user(db, "webcfi", "member")
    book = _illustrated_book(db, make_book, tmp_path, "Web Cfi")
    pct, _, cfi = device_position(book, 3, PARAS - 1)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(app) as c:
        r = c.put(f"/api/books/{book.id}/status",
                  json={"status": "reading", "progress_pct": pct, "cfi": cfi}, headers=_headers(user))
        assert r.status_code == 200, r.text
    [job] = _queued()
    assert job["upto_chapter"] == 3


def test_position_trigger_needs_a_key_and_the_feature(db, make_book, tmp_path, plugin_client):
    c, user, h = plugin_client
    book = _epub_book(db, make_book, tmp_path, "Gated")
    late = book_text.book_chapters(book, db)[3].start_fraction + 0.01

    ai_settings.set_feature_enabled(db, "reading_digest", False)
    db.commit()
    c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": late}, headers=h)
    assert _queued() == []

    ai_settings.set_feature_enabled(db, "reading_digest", True)
    ai_settings.set_user_key(db, user, None)
    db.commit()
    c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": late}, headers=h)
    assert _queued() == []


def test_web_status_put_triggers_digest(db, make_book, tmp_path):
    from backend.core.database import get_db
    from backend.main import create_app

    user = _make_user(db, "webreader", "member")
    book = _epub_book(db, make_book, tmp_path, "Web Read")
    chapters = book_text.book_chapters(book, db)
    pct = (chapters[3].start_fraction + 1.0) / 2
    app = create_app()
    app.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(app) as c:
        r = c.put(f"/api/books/{book.id}/status", json={"status": "reading", "progress_pct": pct},
                  headers=_headers(user))
        assert r.status_code == 200, r.text
    [job] = _queued()
    assert job["upto_chapter"] == 3 and job["followups"] == []


def test_marking_a_series_book_read_enqueues_previously_on(db, make_book, tmp_path):
    from backend.core.database import get_db
    from backend.main import create_app

    user = _make_user(db, "finisher", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Shoal One", series="Shoal", index=1)
    standalone = _epub_book(db, make_book, tmp_path, "Lone Book")
    app = create_app()
    app.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(app) as c:
        r = c.put(f"/api/books/{standalone.id}/status", json={"status": "read"}, headers=_headers(user))
        assert r.status_code == 200
        assert _queued() == []                  # not in a series

        r = c.put(f"/api/books/{v1.id}/status", json={"status": "read"}, headers=_headers(user))
        assert r.status_code == 200
    [job] = _queued()
    assert job["book_id"] == v1.id and job["upto_chapter"] == 4 and job["scope"] == "book"
    assert job["followups"] == [{"name": "previously_recap", "status": "pending"}]


def test_device_sync_to_the_end_enqueues_previously_on(db, make_book, tmp_path, plugin_client):
    c, user, h = plugin_client
    book = _epub_book(db, make_book, tmp_path, "Shoal Two", series="Shoal", index=2)
    r = c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": 1.0}, headers=h)
    assert r.status_code == 200
    jobs = _queued()
    assert len(jobs) == 1                       # the position job coalesced into it
    assert jobs[0]["upto_chapter"] == 4
    assert jobs[0]["followups"] == [{"name": "previously_recap", "status": "pending"}]


def test_bulk_mark_read_is_capped(client, db, make_book, tmp_path):
    """A reading-CSV import (or a sweep) of a finished series must not queue a
    whole-book digest and a recap for every volume."""
    user = _make_user(db, "importer", "member")
    books = [_epub_book(db, make_book, tmp_path, f"Drift {i}", series="Drift", index=i) for i in range(1, 6)]
    db.commit()
    r = client.post("/api/import/reading-csv/apply", headers=_headers(user),
                    json={"items": [{"book_id": b.id, "status": "read"} for b in books]})
    assert r.status_code == 200, r.text
    assert len(_queued()) == digest.MAX_FINISHED_PER_COMMIT

    # Per user per day as well: one book at a time stops at the daily cap.
    digest_job.reset()
    more = [_epub_book(db, make_book, tmp_path, f"Eddy {i}", series="Eddy", index=i) for i in range(1, 6)]
    db.commit()
    for b in more:
        client.put(f"/api/books/{b.id}/status", json={"status": "read"}, headers=_headers(user))
    assert len(_queued()) == digest.MAX_FINISHED_PER_USER_DAY - digest.MAX_FINISHED_PER_COMMIT


def test_finished_hook_ignores_rollback_and_already_read(db, make_book, tmp_path):
    user = _make_user(db, "rollback", "member")
    book = _epub_book(db, make_book, tmp_path, "Rolled", series="Rolled", index=1)
    db.commit()
    nested = db.begin_nested()
    db.add(UserBookStatus(user_id=user.id, book_id=book.id, status="read"))
    db.flush()
    nested.rollback()
    db.commit()
    assert _queued() == []

    row = UserBookStatus(user_id=user.id, book_id=book.id, status="read")
    db.add(row)
    db.commit()
    assert len(_queued()) == 1
    digest_job.reset()
    row.status = "read"
    row.progress_pct = 1.0
    db.commit()
    assert _queued() == []


# ── job endpoints ────────────────────────────────────────────────────────────

def test_job_endpoints_permissions_and_cancel(client: TestClient, db: Session, make_book, tmp_path):
    owner = _make_user(db, "owner", "member")
    stranger = _make_user(db, "stranger", "member")
    guest = _make_user(db, "visitor", "guest", key=False)
    book = _epub_book(db, make_book, tmp_path, "Endpoints")
    snap = digest_job.enqueue(user_id=owner.id, book_id=book.id, upto=2)

    r = client.get(f"/api/ai/digest-jobs/{snap['id']}", headers=_headers(owner))
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert client.get(f"/api/ai/digest-jobs/{snap['id']}").status_code == 200   # admin
    assert client.get(f"/api/ai/digest-jobs/{snap['id']}", headers=_headers(stranger)).status_code == 404
    assert client.get(f"/api/ai/digest-jobs/{snap['id']}", headers=_headers(guest)).status_code == 403
    assert client.get("/api/ai/digest-jobs/nope", headers=_headers(owner)).status_code == 404
    assert client.post(f"/api/ai/digest-jobs/{snap['id']}/cancel",
                       headers=_headers(stranger)).status_code == 404

    r = client.post(f"/api/ai/digest-jobs/{snap['id']}/cancel", headers=_headers(owner))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


# ── run 2c: cost per call ────────────────────────────────────────────────────

class _D:
    def __init__(self, book_id, idx, entries):
        self.book_id, self.chapter_idx = book_id, idx
        self.entries_json = json.dumps([_stored(e) for e in entries])


def _names(state) -> list[str]:
    return [s["name"] for s in state]


def test_model_state_is_names_only_capped_and_append_ordered(monkeypatch):
    monkeypatch.setattr(digest, "GLOSSARY_STATE_MAX", 3)
    earlier = [
        _D(1, 0, [_entry("Ada", "One."), _entry("Bram", "Two.")]),
        _D(1, 1, [_entry("Cora", "Three.", kind="place"), _entry("Dov", "Four.")]),
        _D(1, 2, [_entry("ada", "One, later.")]),
    ]
    current = [
        _D(2, 0, [_entry("Eli", "Five."), _entry("Bram", "Back again.")]),
        _D(2, 1, [_entry("Fen", "Six.", kind="term"), _entry("Ada", "Seen here too.")]),
    ]
    after_0 = digest.model_state(digest.seen_entities(earlier + current[:1], 2))
    after_1 = digest.model_state(digest.seen_entities(earlier + current, 2))
    # The 3 entries seen most recently in earlier volumes (Bram, seen only in
    # chapter 0, is out), in first-seen order; then this volume's names in
    # order of first appearance here (Bram comes back, Ada was listed).
    assert _names(after_0) == ["Ada", "Cora", "Dov", "Eli", "Bram"]
    assert _names(after_1) == ["Ada", "Cora", "Dov", "Eli", "Bram", "Fen"]
    assert after_1[:len(after_0)] == after_0                   # append-only
    assert all(set(s) == {"id", "name", "kind", "identity"} for s in after_1)    # no lines
    assert after_1[1] == {"id": 0, "name": "Cora", "kind": "place", "identity": ""}
    assert digest.GLOSSARY_STATE_MAX == 3 and digest.model_state(digest.seen_entities([], 2)) == []


def test_state_blocks_come_first_and_cache_the_stable_prefix(monkeypatch):
    monkeypatch.setattr(digest, "STATE_CHUNK", 2)
    five = [{"name": f"N{i}", "kind": "person"} for i in range(5)]
    six = five + [{"name": "N5", "kind": "term"}]
    b5, b6 = digest.state_blocks(five), digest.state_blocks(six)
    assert b5[0]["text"].startswith(digest.STATE_HEADER)
    assert [len(b["text"].split("\n")) for b in b5] == [3, 2, 1]
    # The last FULL block is marked; full blocks are byte-identical as the list grows.
    assert [("cache_control" in b) for b in b5] == [False, True, False]
    assert [("cache_control" in b) for b in b6] == [False, False, True]
    assert b5[0] == b6[0] and b5[1]["text"] == b6[1]["text"]
    assert b6[2]["cache_control"] == {"type": "ephemeral"}
    assert digest.state_blocks([]) == [{"type": "text", "text": digest.STATE_HEADER + "\n(none yet)"}]


def test_digest_call_sends_the_cached_state_first_and_the_text_uncached(db, make_book, tmp_path, monkeypatch):
    monkeypatch.setattr(digest, "STATE_CHUNK", 1)
    user = _make_user(db, "cachedstate", "member")
    book = _epub_book(db, make_book, tmp_path, "Cached State")
    _store_digest(db, book, 0, [_entry("Mara Vell", "A walker."), _entry("The Harbour", "A dock.", kind="place")])
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    digest.digest_chapter(db, user, book, 1)
    blocks = fake.calls[0]["user_content"]
    assert blocks[0]["text"] == digest.STATE_HEADER + '\n{"id": 0, "name": "Mara Vell", "kind": "person", "identity": ""}'
    assert blocks[1] == {"type": "text", "text": '{"id": 0, "name": "The Harbour", "kind": "place", "identity": ""}',
                         "cache_control": {"type": "ephemeral"}}
    assert all("cache_control" not in b for b in blocks[2:])
    assert blocks[-1]["text"] == book_text.chapter_text(book, 1, db)


def test_known_names_keep_their_more_and_unchanged_lines_are_dropped(db, make_book, tmp_path):
    user = _make_user(db, "updates", "member")
    book = _epub_book(db, make_book, tmp_path, "Updates")
    _store_digest(db, book, 0, [_entry("Mara Vell", "Walked to the harbour.")])
    ai.set_provider_override(FakeProvider(responses=[
        {"summary": "Tobin rang the bell.", "entries": [
            _entry("Tobin Ash", "Rings the ferry bell.", evidence="Tobin Ash rang the ferry bell twice"),
            {**_entry("MARA VELL", "Heard the bell from the lighthouse.",
                      evidence="Mara Vell heard it from the lighthouse"), "identity": "Later context."},
        ]},
        {"summary": "Oren kept a ledger.", "entries": [
            # Same line as now: not an update, dropped.
            _entry("Mara Vell", "Heard the bell from the lighthouse.", evidence="Mara Vell read it by lamplight"),
            _entry("Oren Pike", "The keeper.", evidence="The keeper, Oren Pike, kept a ledger"),
        ]},
    ]))
    row1 = digest.digest_chapter(db, user, book, 1)
    e1 = {e["name"]: e for e in json.loads(row1.entries_json)}
    assert e1["Tobin Ash"]["more"] == "More on Tobin Ash."       # new: keeps its more
    assert e1["MARA VELL"]["more"] == "Later context."           # stored as given
    row2 = digest.digest_chapter(db, user, book, 2)
    assert [e["name"] for e in json.loads(row2.entries_json)] == ["Oren Pike"]
    mara = digest.glossary_state(db, user, book, upto=3)[0]
    assert mara.name == "Mara Vell" and mara.line == "Heard the bell from the lighthouse."
    assert mara.more == "More on Mara Vell."                     # the fold keeps the first more


def test_check_entries_caps_entries_per_chapter():
    text = " ".join(f"Name{i} walked down the road." for i in range(40))
    raw = [_entry(f"Name{i}", "Walks.", evidence=f"Name{i} walked down the road") for i in range(40)]
    assert len(digest.check_entries(raw, text)) == digest.MAX_ENTRIES_PER_CHAPTER == 25


def test_names_keep_their_brackets():
    assert digest.clean_name("Vane Estate (Hedge Maze)") == "Vane Estate (Hedge Maze)"
    assert digest.clean_name(" Vane Estate (Hedge Maze).  ") == "Vane Estate (Hedge Maze)"
    assert digest.clean_name("Vane Estate)") == "Vane Estate"
    assert digest.clean_name("(Hedge Maze") == "Hedge Maze"
    assert digest.clean_name("Sir Ash [the Elder]") == "Sir Ash [the Elder]"
    assert digest.clean_name('"The Hound"') == '"The Hound"'
    assert digest.clean_name("Mara Vell!?") == "Mara Vell"
    text = "They walked into the Vane Estate (Hedge Maze) at dusk and got lost."
    [e] = digest.check_entries([_entry("Vane Estate (Hedge Maze)", "A maze.", kind="place",
                                       evidence="into the Vane Estate (Hedge Maze) at dusk")], text)
    assert e["name"] == "Vane Estate (Hedge Maze)"


# ── run 2c: multi-chapter calls ──────────────────────────────────────────────

def test_digest_chapters_one_call_checks_each_chapter_and_splits_tokens(db, make_book, tmp_path):
    user = _make_user(db, "multi", "member")
    book = _epub_book(db, make_book, tmp_path, "Multi")

    def responder(feature, user_content, schema):
        assert "chapters" in schema["properties"]
        return {"chapters": [
            {"chapter": 1, "summary": "Mara walked.", "entries": [
                _entry("Mara Vell", "Walks at dawn.", evidence="Mara Vell walked down to the harbour"),
                # A sentence of chapter 2 attributed to chapter 1: dropped.
                _entry("Tobin Ash", "Rings the bell.", evidence="Tobin Ash rang the ferry bell twice"),
            ]},
            {"chapter": 2, "summary": "Tobin rang.", "entries": [
                _entry("Tobin Ash", "Rings the bell.", evidence="Tobin Ash rang the ferry bell twice"),
                _entry("Mara Vell", "Heard the bell.", evidence="Mara Vell heard it from the lighthouse"),
            ]},
            # chapter 3 is left out
        ]}

    fake = FakeProvider(responses=responder, input_tokens=1001, cache_read_tokens=500, output_tokens=333)
    ai.set_provider_override(fake)
    rows = digest.digest_chapters(db, user, book, [2, 0, 1])

    [call] = fake.calls
    assert call["system"] == digest.SYSTEM_PROMPT_MANY
    assert call["max_tokens"] == digest.multi_max_tokens(3) == 9000
    assert digest.multi_max_tokens(100) == 32_000
    # A full unit (CHUNK_CHAPTERS) gets about 2500 tokens a chapter: a 3-act
    # unit of Romeo and Juliet needed 3600 on Haiku and was cut off at 700 a
    # chapter (stored as failed, billed).
    assert digest.multi_max_tokens(digest.CHUNK_CHAPTERS) >= 2500 * digest.CHUNK_CHAPTERS
    blocks = chapter_blocks(call["user_content"])
    assert [n for n, _ in blocks] == [1, 2, 3]
    assert call["user_content"][-3]["text"].startswith("=== [c1] ")
    assert blocks[1][1] == book_text.chapter_text(book, 1, db)   # the heading is outside the text
    assert "Mark3end" not in sent_text(call)

    assert [r.chapter_idx for r in rows] == [0, 1]
    assert digest.get_digest(db, book.id, 2) is None             # left out: still undigested
    r0, r1 = rows
    assert [e["name"] for e in json.loads(r0.entries_json)] == ["Mara Vell"]
    e1 = {e["name"]: e for e in json.loads(r1.entries_json)}
    assert set(e1) == {"Tobin Ash", "Mara Vell"} and r1.summary == "Tobin rang."
    assert e1["Tobin Ash"]["more"] == "More on Tobin Ash."
    assert e1["Mara Vell"]["more"] == "More on Mara Vell."       # kept; the fold keeps the first
    # Both rows were written by a call that also saw chapter 3 (idx 2).
    assert r0.context_end_idx == 2 and r1.context_end_idx == 2
    # The call's tokens, split over the stored chapters by words sent.
    assert r0.input_tokens + r1.input_tokens == 1501
    assert r0.output_tokens + r1.output_tokens == 333
    assert db.query(AIUsage).filter_by(user_id=user.id, feature="reading_digest").count() == 1


def test_apportion_sums_to_the_total():
    assert digest.apportion(10, [1, 1, 1]) == [4, 3, 3]
    assert sum(digest.apportion(333, [5000, 120, 7])) == 333
    assert digest.apportion(5, []) == []
    assert digest.apportion(0, [3, 4]) == [0, 0]


def test_multi_chapter_refusal_stores_nothing_and_splits(db, make_book, tmp_path):
    user = _make_user(db, "multirefuse", "member")
    book = _epub_book(db, make_book, tmp_path, "Multi Refused")
    ai.set_provider_override(FakeProvider(refuse=True, input_tokens=1000, output_tokens=10))
    with pytest.raises(digest.UnitSplit) as exc:
        digest.digest_chapters(db, user, book, [0, 1, 2])
    assert [u.idxs for u in exc.value.units] == [(0,), (1,), (2,)]
    assert db.query(AIChapterDigest).filter_by(book_id=book.id).count() == 0
    # The billed answer is still on the ledger, once.
    assert db.query(AIUsage).filter_by(user_id=user.id, feature="reading_digest").count() == 1
    # One chapter refused on its own: stored as a placeholder (never re-sent).
    with pytest.raises(AIRefused):
        digest.digest_chapter(db, user, book, 0)
    [row] = db.query(AIChapterDigest).filter_by(book_id=book.id).all()
    assert row.model == digest.FAILED_MODEL and row.input_tokens == 1000 and row.output_tokens == 10


def test_job_records_a_left_out_chapter_and_reads_it_next_time(db, make_book, tmp_path):
    user = _make_user(db, "leftout", "member")
    book = _epub_book(db, make_book, tmp_path, "Left Out")

    def responder(feature, user_content, schema):
        out = echo_digest(feature, user_content, schema)
        if "chapters" in out:
            out["chapters"] = [c for c in out["chapters"] if c["chapter"] != 2]
        return out

    fake = FakeProvider(responses=responder)
    ai.set_provider_override(fake)
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4)
    digest_job.run_pending()
    job = digest_job.get(snap["id"])
    assert job["status"] == "done" and job["total"] == 4 and job["done"] == 4 and job["failed"] == 1
    assert [(e["chapter_idx"], "left this chapter out" in e["message"]) for e in job["errors"]] == [(1, True)]
    assert {r.chapter_idx for r in db.query(AIChapterDigest).filter_by(book_id=book.id)} == {0, 2, 3}

    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4)
    digest_job.run_pending()
    assert digest_job.get(snap["id"])["total"] == 1 and len(fake.calls) == 2
    assert digest.get_digest(db, book.id, 1) is not None


def test_plan_units_boundaries(db, make_book, tmp_path, monkeypatch):
    user = _make_user(db, "planner", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Plan One", series="Plan", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Plan Two", series="Plan", index=2)
    _read(db, user, v1)
    _store_digest(db, v2, 1, [])                                   # a gap in volume 2
    counts = {0: 100, 1: 100, 2: 100, 3: 500}
    monkeypatch.setattr(book_text, "chapter_word_counts", lambda b, db=None: dict(counts))
    monkeypatch.setattr(digest, "CHUNK_WORDS", 250)
    monkeypatch.setattr(digest, "MAX_CHAPTER_WORDS", 400)
    scope = digest.user_scope(db, user, v2)

    units = digest.plan_units(db, scope, 4)
    assert [(u.book_id, u.idxs, u.words) for u in units] == [
        (v1.id, (0, 1), 200),          # a third chapter would pass CHUNK_WORDS
        (v1.id, (2,), 100),
        (v1.id, (3,), 400),            # over CHUNK_WORDS: on its own, truncated when sent
        (v2.id, (0,), 100),            # chapter 1 is stored: the gap ends the unit
        (v2.id, (2,), 100),
        (v2.id, (3,), 400),
    ]
    monkeypatch.setattr(digest, "CHUNK_WORDS", 10_000)
    monkeypatch.setattr(digest, "CHUNK_CHAPTERS", 3)
    units = digest.plan_units(db, scope, 4)
    assert [(u.book_id, u.idxs) for u in units] == [
        (v1.id, (0, 1, 2)), (v1.id, (3,)), (v2.id, (0,)), (v2.id, (2, 3))]
    # The current book only, up to the reader's chapter.
    assert [(u.book_id, u.idxs) for u in digest.plan_units(db, digest.Scope(book=v2), 3)] == [
        (v2.id, (0,)), (v2.id, (2,))]


def test_chapter_word_counts(db, make_book, tmp_path):
    book = _epub_book(db, make_book, tmp_path, "Counted")
    counts = book_text.chapter_word_counts(book, db)
    docs = book_text.load_book_docs(book_text.epub_path(book))
    assert sorted(counts) == [0, 1, 2, 3] and sum(counts.values()) == docs.total
    for i in range(4):
        assert counts[i] == book_text.count_words(book_text.chapter_text(book, i, db))
    book.word_count = 1000
    chapters = book_text.book_chapters(book, db)
    assert book_text.chapter_word_counts(book, db)[2] == round(
        (chapters[2].end_fraction - chapters[2].start_fraction) * 1000)


# ── run 2c: triggers ─────────────────────────────────────────────────────────

EIGHT = [(f"Part {i + 1}", body) for i, (_t, body) in enumerate(CHAPTERS + CHAPTERS)]


def _mid_of(book: Book, db: Session, idx: int) -> float:
    ch = book_text.book_chapters(book, db)[idx]
    return (ch.start_fraction + ch.end_fraction) / 2


def test_position_trigger_waits_for_pending_min_past_an_active_job(db, make_book, tmp_path):
    user = _make_user(db, "pending", "member")
    book = _epub_book(db, make_book, tmp_path, "Pending", chapters=EIGHT)
    assert digest.PENDING_MIN == 3
    assert digest.on_position_advanced(db, user, book, _mid_of(book, db, 2)) is None   # 2 finished
    job = digest.on_position_advanced(db, user, book, _mid_of(book, db, 3))
    assert job is not None and job["upto_chapter"] == 3
    # Chapters 0-2 are covered by the queued job: one more is not enough.
    assert digest.on_position_advanced(db, user, book, _mid_of(book, db, 4)) is None
    assert digest_job.get(job["id"])["upto_chapter"] == 3
    # Three past it: the queued job grows instead of a new one.
    again = digest.on_position_advanced(db, user, book, _mid_of(book, db, 6))
    assert again["id"] == job["id"] and again["upto_chapter"] == 6
    assert len(_queued()) == 1


def test_no_automatic_enqueue_after_a_cancel_until_the_user_starts_one(db, make_book, tmp_path, plugin_client):
    c, user, h = plugin_client
    book = _epub_book(db, make_book, tmp_path, "Stopped", chapters=EIGHT, series="Stopped", index=1)
    db.commit()
    r = c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": _mid_of(book, db, 3)}, headers=h)
    assert r.status_code == 200
    [job] = _queued()
    r = c.post(f"/api/ai/digest-jobs/{job['id']}/cancel", headers=_headers(user))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert digest_job.last_cancelled(db, user.id, book.id)

    # Position syncs and finishing the book do not start it again.
    c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": _mid_of(book, db, 6)}, headers=h)
    assert _queued() == []
    r = c.put(f"/api/books/{book.id}/status", json={"status": "read"}, headers=_headers(user))
    assert r.status_code == 200 and _queued() == []
    # Neither does opening the glossary.
    g = c.get(f"/api/tome-sync/ai/books/{book.id}/glossary", headers=h).json()
    assert g["job"] is None and _queued() == []

    # The user starts it: that job is the latest, so the triggers act again.
    r = c.post(f"/api/ai/books/{book.id}/digest", json={"scope": "book"}, headers=_headers(user))
    assert r.status_code == 202, r.text
    # Marked read above: an explicit start reads the whole book, not up to
    # the stored position (companion.digest_upto).
    assert r.json()["job"]["upto_chapter"] == 8
    assert not digest_job.last_cancelled(db, user.id, book.id)


def test_last_cancelled_sees_a_running_job_being_cancelled(db, make_book, tmp_path):
    user = _make_user(db, "runningcancel", "member")
    book = _epub_book(db, make_book, tmp_path, "Running Cancel")
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4)
    assert not digest_job.last_cancelled(db, user.id, book.id)
    job = digest_job._jobs[snap["id"]]
    job.status = "running"
    digest_job.cancel(snap["id"])
    assert digest_job.last_cancelled(db, user.id, book.id)
    assert not digest_job.last_cancelled(db, user.id, book.id + 1)


# ── run 2c: the worker pool, Stop, persistence ──────────────────────────────

@pytest.fixture()
def pool(tmp_path, monkeypatch):
    """Real worker threads on a file-backed SQLite database: each worker
    session gets its own connection, as in production (the in-memory test
    connection cannot be shared between threads)."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    from backend.core.database import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'pool.db'}",
                           connect_args={"check_same_thread": False}, poolclass=NullPool)

    def _pragmas(conn, _record):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    event.listen(engine, "connect", _pragmas)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(digest_job, "_session_factory", factory)
    monkeypatch.setattr(digest_job, "_spawn_worker", digest_job._start_thread)
    yield factory
    for t in list(digest_job._threads):
        t.join(timeout=15)
    engine.dispose()


def _pool_series(factory, tmp_path: Path, volumes: int, chapters=CHAPTERS) -> tuple[int, list[int]]:
    """A member reading the last of ``volumes`` volumes, the earlier ones
    marked read (before the key is set, so the finished hook stays quiet)."""
    from backend.models.book import BookFile

    with factory() as s:
        user = User(username="pooler", email="pooler@example.com", hashed_password="x",
                    is_active=True, is_admin=False, role="member", must_change_password=False)
        s.add(user)
        s.flush()
        ids = []
        for i in range(1, volumes + 1):
            path = make_epub(tmp_path / f"Pool_{i}.epub", chapters)
            b = Book(title=f"Pool {i}", series="Pool", series_index=i, status="active")
            s.add(b)
            s.flush()
            s.add(BookFile(book_id=b.id, file_path=str(path), format="epub",
                           file_size=path.stat().st_size))
            replace_book_chapters(s, b.id, extract_chapters_epub(path))
            ids.append(b.id)
        for bid in ids[:-1]:
            s.add(UserBookStatus(user_id=user.id, book_id=bid, status="read", progress_pct=1.0))
        s.commit()
        ai_settings.set_user_key(s, user, KEY)
        s.commit()
        return user.id, ids


def _wait_finished(job_id: str, timeout: float = 30.0) -> dict:
    import time as _time
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        snap = digest_job.get(job_id)
        if snap is not None and snap["status"] not in digest_job.ACTIVE:
            return snap
        _time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish: {digest_job.get(job_id)}")


_BOOK_RE = re.compile(r'"book": "([^"]+)"')


def test_four_workers_run_units_of_different_volumes_concurrently(pool, tmp_path, monkeypatch):
    import itertools
    import threading
    import time as _time

    from backend.models.ai_job import AIJob

    monkeypatch.setattr(digest, "CHUNK_CHAPTERS", 2)          # two units per volume
    user_id, ids = _pool_series(pool, tmp_path, 5)
    barrier = threading.Barrier(digest_job.WORKERS, timeout=15)
    order = itertools.count()
    spans: list[tuple[str, float, float]] = []
    lock = threading.Lock()

    def on_call(call):
        title = _BOOK_RE.search(sent_text(call)).group(1)
        start = _time.monotonic()
        if next(order) < digest_job.WORKERS:
            barrier.wait()          # passes only when four calls are in flight at once
        else:
            _time.sleep(0.02)
        with lock:
            spans.append((title, start, _time.monotonic()))

    fake = FakeProvider(responses=echo_digest, on_call=on_call)
    ai.set_provider_override(fake)
    snap = digest_job.enqueue(user_id=user_id, book_id=ids[-1], upto=0)   # vol 5 at its start
    job = _wait_finished(snap["id"])

    assert not barrier.broken
    assert job["status"] == "done", job
    assert job["total"] == 16 and job["done"] == 16 and job["failed"] == 0
    assert len(fake.calls) == 8 and job["calls"] == 8
    # The first four calls were one unit of each of the four read volumes.
    first = sorted(spans, key=lambda s: s[1])[: digest_job.WORKERS]
    assert sorted(t for t, _, _ in first) == ["Pool 1", "Pool 2", "Pool 3", "Pool 4"]
    # The units of one volume never overlapped (each builds on the one before).
    for title in {t for t, _, _ in spans}:
        mine = sorted((s for s in spans if s[0] == title), key=lambda s: s[1])
        assert all(b[1] >= a[2] for a, b in zip(mine, mine[1:])), title
    with pool() as s:
        assert s.query(AIChapterDigest).count() == 16
        rows = s.query(AIUsage).all()
        assert len(rows) == 8 and {r.job_id for r in rows} == {snap["id"]}
        assert {r.book_id for r in rows} == set(ids[:4])
        row = s.get(AIJob, snap["id"])
        assert row.status == "done" and row.done == 16 and row.calls == 8
        assert row.cost_usd == pytest.approx(sum(r.cost_usd for r in rows))


def test_stop_is_stopping_at_once_then_cancelled_after_the_unit_in_flight(pool, tmp_path, monkeypatch):
    import threading

    from backend.models.ai_job import AIJob

    monkeypatch.setattr(digest, "CHUNK_CHAPTERS", 2)
    user_id, [book_id] = _pool_series(pool, tmp_path, 1)
    started, release = threading.Event(), threading.Event()

    def on_call(call):
        started.set()
        assert release.wait(15)

    fake = FakeProvider(responses=echo_digest, on_call=on_call)
    ai.set_provider_override(fake)
    snap = digest_job.enqueue(user_id=user_id, book_id=book_id, upto=4, current_book_only=True)
    assert started.wait(15)

    stopping = digest_job.cancel(snap["id"])
    assert stopping["status"] == "stopping"
    assert digest_job.get(snap["id"])["status"] == "stopping"
    with pool() as s:
        assert s.get(AIJob, snap["id"]).status == "stopping"
        assert digest_job.last_cancelled(s, user_id, book_id)
    release.set()
    job = _wait_finished(snap["id"])
    assert job["status"] == "cancelled"
    assert len(fake.calls) == 1                     # the second unit was dropped
    with pool() as s:
        # The unit in flight finished and stored its chapters.
        assert sorted(r.chapter_idx for r in s.query(AIChapterDigest)) == [0, 1]
        row = s.get(AIJob, snap["id"])
        assert row.status == "cancelled" and row.done == 2 and row.calls == 1
        assert digest_job.last_cancelled(s, user_id, book_id)


def test_job_row_mirrors_the_snapshot_and_usage_rows_carry_job_and_book(db, make_book, tmp_path):
    from backend.models.ai_job import AIJob

    user = _make_user(db, "mirrored", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Mirror One", series="Mirror", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Mirror Two", series="Mirror", index=2)
    _read(db, user, v1)
    ai.set_provider_override(FakeProvider(responses=echo_digest))

    snap = digest_job.enqueue(user_id=user.id, book_id=v2.id, upto=2)
    row = db.get(AIJob, snap["id"])
    assert row is not None and row.status == "queued" and row.scope == "series"
    assert row.user_id == user.id and row.book_id == v2.id and row.upto_chapter == 2

    digest_job.run_pending()
    job = digest_job.get(snap["id"])
    db.expire_all()
    row = db.get(AIJob, snap["id"])
    for name in ("status", "scope", "total", "done", "failed", "calls", "est_seconds",
                 "est_cost_usd", "est_chapters", "stop_reason"):
        assert getattr(row, name) == job[name], name
    assert row.cost_usd == pytest.approx(job["cost_usd"])
    assert job["status"] == "done" and job["total"] == 6 and job["calls"] == 2
    assert job["est_chapters"] == 6 and job["est_cost_usd"] > 0 and job["est_seconds"] > 0
    assert row.started_at is not None and row.finished_at is not None
    usage_rows = db.query(AIUsage).filter_by(user_id=user.id).all()
    assert {(r.job_id, r.book_id) for r in usage_rows} == {(snap["id"], v1.id), (snap["id"], v2.id)}
    assert all(r.duration_ms is not None for r in usage_rows)
    assert job["cost_usd"] == pytest.approx(sum(r.cost_usd for r in usage_rows))


def test_followup_calls_are_billed_to_the_job(db, make_book, tmp_path):
    from backend.services.ai import recap  # noqa: F401 - registers previously_recap

    user = _make_user(db, "followbill", "member")
    book = _epub_book(db, make_book, tmp_path, "Follow Bill", series="Follow Bill", index=1)

    def responder(feature, user_content, schema):
        if feature == "recap":
            return {"paragraphs": ["Mara walked to the harbour."]}
        return echo_digest(feature, user_content, schema)

    ai.set_provider_override(FakeProvider(responses=responder))
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4,
                              followups=[digest_job.FOLLOWUP_PREVIOUSLY])
    digest_job.run_pending()
    job = digest_job.get(snap["id"])
    assert job["followups"] == [{"name": "previously_recap", "status": "done"}]
    rows = db.query(AIUsage).filter_by(job_id=snap["id"]).all()
    assert sorted(r.feature for r in rows) == ["reading_digest", "recap"]
    assert all(r.book_id == book.id for r in rows) and job["calls"] == 2


def test_cancel_of_a_running_job_between_units_is_cancelled_at_once(db, make_book, tmp_path):
    user = _make_user(db, "between", "member")
    book = _epub_book(db, make_book, tmp_path, "Between")
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4)
    job = digest_job._jobs[snap["id"]]
    with digest_job._lock:                          # started, nothing in flight
        digest_job._queue.remove(job)
        job.status = "running"
    assert digest_job.cancel(snap["id"])["status"] == "cancelled"
    assert digest_job.active_for(user.id, book.id) is None


def test_job_snapshot_falls_back_to_the_row(client, db, make_book, tmp_path):
    user = _make_user(db, "fallback", "member")
    book = _epub_book(db, make_book, tmp_path, "Fallback")
    ai.set_provider_override(FakeProvider(responses=echo_digest))
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4)
    digest_job.run_pending()
    digest_job.reset()                              # as after a restart
    assert digest_job.get(snap["id"]) is None
    r = client.get(f"/api/ai/digest-jobs/{snap['id']}", headers=_headers(user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "done" and body["done"] == 4 and body["calls"] == 1
    assert body["current"] is None and body["errors"] == []
    r = client.post(f"/api/ai/digest-jobs/{snap['id']}/cancel", headers=_headers(user))
    assert r.status_code == 200 and r.json()["status"] == "done"


def test_prompts_ask_for_evidence_that_names_the_entry():
    """The citation check drops evidence that does not name its entry. On the
    first real run of the run 2c prompt ("a quote ... that mentions the
    entry") the model quoted a line each character speaks, so 14 of 19 Act I
    entries of Romeo and Juliet (Romeo, Juliet, Benvolio...) were dropped.
    Both prompts must ask for a quote that contains the name."""
    for prompt in (digest.SYSTEM_PROMPT, digest.SYSTEM_PROMPT_MANY):
        flat = " ".join(prompt.split())
        assert "contains the entry's name" in flat
        assert "does not count unless the name is in it" in flat
        # The check needs at least MIN_EVIDENCE_WORDS words ("Enter ROMEO" is two).
        assert f"quote of {digest.MIN_EVIDENCE_WORDS} to {digest.MAX_EVIDENCE_WORDS} words" in flat
    # The rule the prompt describes is the one check_entries applies: a quote
    # that names nobody keeps the entry only because the name is in the
    # chapter, and the stored quote is then re-anchored on the name; a name
    # the chapter never writes is dropped.
    text = ("Enter ROMEO and BENVOLIO. The street is wide and grey and the morning is cold "
            "and the bells ring out over the old town square. Is she a Capulet? O dear account!")
    raw = [{"name": "Romeo", "kind": "person", "line": "A Montague.", "more": "",
            "evidence": "Is she a Capulet?"},
           {"name": "Benvolio", "kind": "person", "line": "Romeo's cousin.", "more": "",
            "evidence": "Enter ROMEO and BENVOLIO"},
           {"name": "Tybalt", "kind": "person", "line": "A Capulet.", "more": "",
            "evidence": "Is she a Capulet?"}]
    kept = {e["name"]: e["evidence"] for e in digest.check_entries(raw, text)}
    assert set(kept) == {"Romeo", "Benvolio"}
    assert "ROMEO" in kept["Romeo"] and "Capulet" not in kept["Romeo"]


def test_a_short_quote_that_is_the_whole_name_keeps_a_main_character():
    """On the smoke, Sonnet cited "Enter ROMEO" (two words) for Romeo and
    the three-word minimum dropped the protagonist. A quote under the
    minimum passes when it holds the entry's whole name as whole words and
    occurs in the chapter; a fragment or a part of a name still does not."""
    text = "Enter ROMEO and BENVOLIO. Is she a Capulet? Enter the Nurse. Lady Capulet calls."
    def kept(name, evidence):
        raw = [{"name": name, "kind": "person", "line": "Someone.", "more": "", "evidence": evidence}]
        return bool(digest.check_entries(raw, text))
    assert kept("Romeo", "Enter ROMEO")
    assert kept("Romeo", "ROMEO")
    assert kept("Lady Capulet", "Lady Capulet calls")
    assert not kept("Lady Capulet", "Lady")             # part of a name, not the name
    assert not kept("Romeo", "Enter JULIET")            # names someone else
    assert not kept("Romeo", "Romeo Montague")          # not in the chapter
    assert not kept("Nurse", "the nurse of")            # fragment the chapter does not hold
    assert not kept("Al", "Al")                         # too short to mean anything


# ── review fixes: chapter numbers, split units, context end, more, evidence ──

def _billed_refusal() -> AIRefused:
    return AIRefused(category="general_harms", result=AIResult(
        text=None, parsed=None, model="claude-sonnet-5-5", input_tokens=100, output_tokens=5,
        cache_read_tokens=0, cache_write_tokens=0))


def _numbered_by_position(feature, user_content, schema):
    """A model numbering the chapters by their place in the request (1..n)."""
    if "chapters" not in schema["properties"]:
        return echo_digest(feature, user_content, schema)
    return {"chapters": [{"chapter": i + 1, "summary": f"Heading {n}", "entries": []}
                         for i, (n, _t) in enumerate(chapter_blocks(user_content))]}


def test_heading_numbers_are_bracketed_and_the_schema_says_so(db, make_book, tmp_path):
    book = _epub_book(db, make_book, tmp_path, "Headings")
    ch = book_text.book_chapters(book, db)[2]
    assert digest.chapter_heading(ch) == "=== [c3] The Lighthouse ==="
    item = digest.build_multi_schema()["properties"]["chapters"]["items"]["properties"]["chapter"]
    assert "number in brackets" in item["description"]
    assert "number in brackets" in digest.SYSTEM_PROMPT_MANY


@pytest.mark.parametrize("bad", ["shifted", "duplicate", "unknown", "not_an_object"])
def test_unplaceable_chapter_numbers_reject_the_whole_answer(db, make_book, tmp_path, bad):
    user = _make_user(db, f"numbers{bad}", "member")
    book = _epub_book(db, make_book, tmp_path, f"Numbers {bad}")

    def answer(feature, user_content, schema):
        if bad == "shifted":                  # chapters 2..4 sent, answered as 1..3
            return _numbered_by_position(feature, user_content, schema)
        items = [{"chapter": n, "summary": f"Heading {n}", "entries": []}
                 for n, _t in chapter_blocks(user_content)]
        if bad == "duplicate":
            items.append({**items[0], "summary": "again"})
        elif bad == "unknown":
            items.append({"chapter": 40, "summary": "not sent", "entries": []})
        else:
            items.append("chapter 2")
        return {"chapters": items}

    ai.set_provider_override(FakeProvider(responses=answer))
    with pytest.raises(digest.UnitSplit) as exc:
        digest.digest_chapters(db, user, book, [1, 2, 3])
    assert [u.idxs for u in exc.value.units] == [(1,), (2,), (3,)]
    assert db.query(AIChapterDigest).filter_by(book_id=book.id).count() == 0


def test_job_reads_a_rejected_unit_again_one_chapter_at_a_time(db, make_book, tmp_path):
    user = _make_user(db, "rejected", "member")
    book = _epub_book(db, make_book, tmp_path, "Rejected")
    _store_digest(db, book, 0, [])
    fake = FakeProvider(responses=_numbered_by_position)
    ai.set_provider_override(fake)
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4, current_book_only=True)
    digest_job.run_pending()
    job = digest_job.get(snap["id"])
    assert job["status"] == "done" and job["total"] == 3 and job["done"] == 3 and job["failed"] == 0
    assert job["calls"] == 4                                     # the rejected call + three singles
    assert [len(chapter_blocks(c["user_content"])) for c in fake.calls] == [3, 0, 0, 0]
    # The singles ran in order, each with only its own chapter.
    assert [i for c in fake.calls[1:] for i in range(4) if f"Mark{i}end" in sent_text(c)] == [1, 2, 3]
    rows = {r.chapter_idx: r for r in db.query(AIChapterDigest).filter_by(book_id=book.id)}
    assert set(rows) == {0, 1, 2, 3}
    assert all(r.context_end_idx is None and r.model != digest.FAILED_MODEL for r in rows.values())


def test_job_splits_a_refused_unit_and_a_split_is_not_a_failure(db, make_book, tmp_path):
    user = _make_user(db, "splitter", "member")
    book = _epub_book(db, make_book, tmp_path, "Splitter")

    def refuse_multi(feature, user_content, schema):
        if "chapters" in schema["properties"]:
            raise _billed_refusal()
        return echo_digest(feature, user_content, schema)

    fake = FakeProvider(responses=refuse_multi)
    ai.set_provider_override(fake)
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=4)
    digest_job.run_pending()
    job = digest_job.get(snap["id"])
    assert job["status"] == "done" and job["total"] == 4 and job["done"] == 4 and job["failed"] == 0
    assert len(fake.calls) == 5
    rows = db.query(AIChapterDigest).filter_by(book_id=book.id).all()
    assert len(rows) == 4 and all(r.model != digest.FAILED_MODEL for r in rows)

    # Everything refused: the split itself is not a failure, so the job stops
    # after three single-chapter refusals (three placeholders), not two.
    book2 = _epub_book(db, make_book, tmp_path, "Splitter Two")
    fake = FakeProvider(refuse=True)
    ai.set_provider_override(fake)
    snap = digest_job.enqueue(user_id=user.id, book_id=book2.id, upto=4)
    digest_job.run_pending()
    job = digest_job.get(snap["id"])
    assert job["status"] == "stopped" and job["stop_reason"] == digest_job.REPEATED_FAILURES
    assert len(fake.calls) == 4 and job["failed"] == 3
    failed = db.query(AIChapterDigest).filter_by(book_id=book2.id).all()
    assert sorted(r.chapter_idx for r in failed) == [0, 1, 2]
    assert all(r.model == digest.FAILED_MODEL for r in failed)


def test_a_reader_behind_the_context_end_does_not_get_that_digest(db, make_book, tmp_path):
    user = _make_user(db, "behind", "member")
    book = _epub_book(db, make_book, tmp_path, "Behind")
    ai.set_provider_override(FakeProvider(responses=echo_digest))
    digest.digest_chapters(db, user, book, [0, 1, 2])            # one call that saw chapters 0..2
    old = {r.chapter_idx: r.id for r in db.query(AIChapterDigest).filter_by(book_id=book.id)}
    assert all(r.context_end_idx == 2 for r in db.query(AIChapterDigest).filter_by(book_id=book.id))
    scope = digest.Scope(book=book)

    # A reader in chapter 2 (idx 2) must not get rows 0 and 1: their call read idx 2.
    assert [c.idx for _, c in digest.missing_chapters(db, scope, 2)] == [0, 1]
    assert digest.scoped_digests(db, scope, 2) == []
    assert digest.coverage(db, scope, 2)["digested"] == 0
    assert digest.glossary_state(db, user, book, 2) == []
    assert [(u.idxs, u.replace) for u in digest.plan_units(db, scope, 2)] == [((0,), True), ((1,), True)]
    # The reader who triggered it (past chapter idx 2) and anyone further on use it.
    assert digest.missing_chapters(db, scope, 3) == []
    for upto in (3, 4):
        assert len(digest.scoped_digests(db, scope, upto)) == 3
        assert digest.glossary_state(db, user, book, upto) != []
    assert [c.idx for _, c in digest.missing_chapters(db, scope, 4)] == [3]

    # The reader behind gets them re-read singly; the rows are overwritten in place.
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    snap = digest_job.enqueue(user_id=user.id, book_id=book.id, upto=2, current_book_only=True)
    digest_job.run_pending()
    assert digest_job.get(snap["id"])["status"] == "done"
    assert len(fake.calls) == 2 and all(chapter_blocks(c["user_content"]) == [] for c in fake.calls)
    rows = {r.chapter_idx: r for r in db.query(AIChapterDigest).filter_by(book_id=book.id)}
    assert rows[0].id == old[0] and rows[1].id == old[1]
    assert rows[0].context_end_idx is None and rows[1].context_end_idx is None
    assert rows[2].context_end_idx == 2
    assert len(digest.scoped_digests(db, scope, 2)) == 2 and digest.missing_chapters(db, scope, 2) == []
    # A single-chapter digest is valid for every reader.
    assert len(digest.scoped_digests(db, scope, 3)) == 3


def test_more_survives_for_a_reader_without_the_earlier_volume(db, make_book, tmp_path):
    user = _make_user(db, "volumes", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Tide One", series="Tide", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Tide Two", series="Tide", index=2)
    _store_digest(db, v1, 0, [{**_entry("Mara Vell", "A walker.",
                                        evidence="Mara Vell walked down to the harbour"),
                               "more": "Introduced in volume one."}])   # a row before entities
    # Volume 2 lists her with the same line: kept (known only from another volume), more kept.
    ai.set_provider_override(FakeProvider(responses=[{"summary": "Back.", "entries": [
        {**_entry("Mara Vell", "A walker.", evidence="Mara Vell walked down to the harbour"),
         "identity": "Back in volume two."}]}]))
    row = digest.digest_chapter(db, user, v2, 0)
    [e] = json.loads(row.entries_json)
    assert e["more"] == "Back in volume two."

    newcomer = _make_user(db, "newcomer2", "member")             # never read volume 1
    [mara] = digest.glossary_state(db, newcomer, v2, 1)
    assert mara.more == "Back in volume two."
    _read(db, user, v1)                                          # read it: the first more wins
    [mara] = digest.glossary_state(db, user, v2, 1)
    assert mara.more == "Introduced in volume one."


def test_check_entries_widens_a_bare_name_quote():
    play = ("Enter ROMEO. BENVOLIO. Good morrow, cousin. ROMEO. Is the day so young? "
            "BENVOLIO. But new struck nine. Exeunt all but ROMEO")
    [e] = digest.check_entries([_entry("Romeo", "A young man.", evidence="Enter ROMEO")], play)
    assert e["evidence"] == "Enter ROMEO. BENVOLIO"
    assert digest.evidence_in_text(e["evidence"], digest.normalize_text(play))
    [e] = digest.check_entries([_entry("Romeo", "A young man.", evidence="romeo")], play)
    assert e["evidence"] == "ROMEO. BENVOLIO. Good morrow, cousin"   # first occurrence, verbatim case
    # At the chapter's end the preceding words fill it up.
    assert digest.widen_evidence("Romeo", "but ROMEO", "Exeunt all but ROMEO") == "all but ROMEO"
    # Whitespace and typographic quotes in the text are tolerated; the slice is verbatim.
    text = "They sat down.\n  Tom’s   boat drifted out past the reef that night."
    [e] = digest.check_entries([_entry("Tom", "A sailor.", kind="person", evidence="Tom's")], text)
    assert e["evidence"] == "Tom’s boat drifted out past the reef that night"
    # A two-letter Latin name never carries an entry on its own; a two-character CJK name does.
    assert digest.check_entries([_entry("Al", "A man.", evidence="Al")], "Al went home early today.") == []
    cjk = "王林走进了房间。他坐下了。"
    [e] = digest.check_entries([_entry("王林", "一个人。", evidence="王林")], cjk)
    assert e["evidence"] == "王林走进了房间"


def test_finishing_a_volume_digests_only_that_volume(db, make_book, tmp_path):
    user = _make_user(db, "onlythis", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Reef One", series="Reef", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Reef Two", series="Reef", index=2)
    _read(db, user, v1)                                          # read earlier, never digested
    db.commit()
    digest_job.reset()
    digest.reset_triggers()

    def answer(feature, user_content, schema):
        out = echo_digest(feature, user_content, schema)
        if "chapters" in out:                                    # leave chapter 2 out
            out["chapters"] = [c for c in out["chapters"] if c["chapter"] != 2]
        return out

    fake = FakeProvider(responses=answer)
    ai.set_provider_override(fake)
    db.add(UserBookStatus(user_id=user.id, book_id=v2.id, status="read", progress_pct=1.0))
    db.commit()
    [job] = _queued()
    assert job["book_id"] == v2.id and job["scope"] == "book"
    digest_job.run_pending()
    assert db.query(AIChapterDigest).filter_by(book_id=v1.id).count() == 0
    assert {u.book_id for u in db.query(AIUsage).filter_by(feature="reading_digest")} == {v2.id}
    # Its own digests are incomplete: "Previously on" is skipped, never billed.
    done = digest_job.get(job["id"])
    assert done["followups"] == [{"name": "previously_recap", "status": "skipped",
                                  "message": digest_job.BOOK_INCOMPLETE}]
    assert [c["feature"] for c in fake.calls] == ["reading_digest"]


def test_position_trigger_leaves_a_large_backlog_to_the_explicit_start(db, make_book, tmp_path, monkeypatch):
    assert digest.CONFIRM_THRESHOLD == 20
    monkeypatch.setattr(digest, "CONFIRM_THRESHOLD", 4)
    user = _make_user(db, "backlog", "member")
    book = _epub_book(db, make_book, tmp_path, "Backlog", chapters=EIGHT)
    assert digest.on_position_advanced(db, user, book, _mid_of(book, db, 5)) is None   # 5 > 4
    assert _queued() == []
    other = _make_user(db, "backlog2", "member")
    job = digest.on_position_advanced(db, other, book, _mid_of(book, db, 4))           # 4 <= 4
    assert job is not None and job["upto_chapter"] == 4 and job["scope"] == "book"


# ── entities (2d step 3) ─────────────────────────────────────────────────────

GUILD = [
    ("Arrival", "Neil Davone healed the wound while Jason Asano watched from the cart. " * 4),
    ("Market", "Neil Brown, the smith, sold Jason a blade. Neil Davone frowned at the price. " * 4),
    ("Stranger", "The Hooded Man stalked the market. Jason Asano saw the stranger at the gate. " * 4),
    ("Reveal", "The Hooded Man lowered his hood, and Jason Asano saw his own face. Old Neil laughed. " * 4),
]


def _answer(*entries, same_as=None, summary="Something happened.") -> dict:
    return {"summary": summary, "entries": list(entries), "same_as": same_as or []}


def _dig(db, user, book, idx, *entries, same_as=None):
    """Digest chapter ``idx`` with this answer; (row, the call sent)."""
    fake = FakeProvider(responses=[_answer(*entries, same_as=same_as)])
    ai.set_provider_override(fake)
    row = digest.digest_chapter(db, user, book, idx)
    return row, fake.calls[0]


def _entities(db) -> dict[str, AIEntity]:
    return {e.name: e for e in db.query(AIEntity).order_by(AIEntity.id)}


def _notes(db, entity: AIEntity) -> list[AIEntityNote]:
    return db.query(AIEntityNote).filter_by(entity_id=entity.id).order_by(AIEntityNote.chapter_idx).all()


def _state_lines(call) -> list[dict]:
    lines = []
    for b in call["user_content"]:
        if b["text"].startswith("Digest "):
            break
        lines += [json.loads(x) for x in b["text"].split("\n") if x.startswith("{")]
    return lines


@pytest.fixture()
def guild(db, make_book, tmp_path):
    user = _make_user(db, "guild", "member")
    book = _epub_book(db, make_book, tmp_path, "Guild One", series="The  Guild", index=1, chapters=GUILD)
    return user, book


def _jason_and_neil(db, user, book):
    _dig(db, user, book, 0,
         _entry("Neil Davone", "Healed a wound.", evidence="Neil Davone healed the wound",
                identity="Jason's teammate, a healer."),
         _entry("Jason Asano", "Watched from the cart.", evidence="Jason Asano watched from the cart",
                identity="A newcomer to the world.", facts={"status": "alive", "location": "the cart"}))
    ents = _entities(db)
    return ents["Jason Asano"], ents["Neil Davone"]


def test_new_entries_create_entities_and_the_state_carries_identity(db, guild):
    user, book = guild
    jason, neil = _jason_and_neil(db, user, book)
    assert jason.series_key == "the guild" and digest.series_key(book) == "the guild"
    assert (jason.first_book_id, jason.first_chapter_idx) == (book.id, 0)
    assert neil.identity == "Jason's teammate, a healer."
    row0 = digest.get_digest(db, book.id, 0)
    items = json.loads(row0.entries_json)
    assert [(e["name"], e["entity_id"], e["more"]) for e in items] == [
        ("Neil Davone", neil.id, "Jason's teammate, a healer."),
        ("Jason Asano", jason.id, "A newcomer to the world.")]
    [note] = _notes(db, jason)
    assert note.line == "Watched from the cart." and json.loads(note.facts_json) == {
        "status": "alive", "location": "the cart"}

    _, call = _dig(db, user, book, 1, _entry("Neil Davone", "Frowned at a price.", ref=neil.id,
                                             identity="", evidence="Neil Davone frowned at the price"))
    assert _state_lines(call) == [
        {"id": neil.id, "name": "Neil Davone", "kind": "person", "identity": "Jason's teammate, a healer."},
        {"id": jason.id, "name": "Jason Asano", "kind": "person", "identity": "A newcomer to the world."}]
    # Append-only: the next chapter's state starts with this one.
    _, call2 = _dig(db, user, book, 2, _entry("The Hooded Man", "Stalked the market.",
                                              evidence="The Hooded Man stalked the market"))
    assert _state_lines(call2)[:2] == _state_lines(call)
    assert "Frowned at a price." not in sent_text(call2)        # identity only, no situational line


def test_an_accepted_ref_attaches_and_a_ref_not_named_in_the_quote_is_new(db, guild):
    user, book = guild
    jason, neil = _jason_and_neil(db, user, book)
    row, _ = _dig(
        db, user, book, 1,
        # Named by a part of the name: accepted, even with another spelling.
        _entry("Jason", "Bought a blade.", ref=jason.id, identity="",
               evidence="the smith, sold Jason a blade"),
        # A ref whose entity the quote does not name: a new entity instead.
        # (A quote naming any part of the entity's name passes: "Neil" alone
        # would carry a ref to Neil Davone, so the ref is the model's call.)
        _entry("Neil Brown", "Sold a blade.", ref=jason.id, identity="A smith.",
               evidence="Neil Brown, the smith, sold"),
    )
    ents = _entities(db)
    assert set(ents) == {"Neil Davone", "Jason Asano", "Neil Brown"}
    items = {e["name"]: e for e in json.loads(row.entries_json)}
    assert items["Jason"]["entity_id"] == jason.id and items["Jason"]["more"] == ""
    assert items["Neil Brown"]["entity_id"] == ents["Neil Brown"].id
    assert items["Neil Brown"]["more"] == "A smith."
    assert [n.chapter_idx for n in _notes(db, jason)] == [0, 1]
    assert [n.chapter_idx for n in _notes(db, neil)] == [0]
    # The spelling used is learned as an alias (it is in the text).
    assert [a["name"] for a in digest.entity_aliases(jason)] == ["Jason"]


def test_a_ref_to_another_kind_or_an_unknown_id_is_new(db, guild):
    user, book = guild
    jason, _ = _jason_and_neil(db, user, book)
    _dig(db, user, book, 1, _entry("Jason", "A place now?", kind="place", ref=jason.id,
                                   evidence="the smith, sold Jason a blade"))
    _dig(db, user, book, 2, _entry("The Hooded Man", "Stalked.", ref=99999,
                                   evidence="The Hooded Man stalked the market"))
    ents = _entities(db)
    assert ents["Jason"].kind == "place" and ents["Jason"].id != jason.id
    assert "The Hooded Man" in ents


def test_two_people_called_neil_stay_apart(db, guild):
    user, book = guild
    _, davone = _jason_and_neil(db, user, book)
    _dig(db, user, book, 1, _entry("Neil Brown", "Sold a blade.", identity="A smith.",
                                   evidence="Neil Brown, the smith, sold"))
    brown = _entities(db)["Neil Brown"]
    # Chapter 3 says only "Neil": the ref decides who it is.
    _dig(db, user, book, 3, _entry("Neil", "Laughed.", ref=brown.id, identity="",
                                   evidence="his own face. Old Neil laughed"))
    assert [n.chapter_idx for n in _notes(db, brown)] == [1, 3]
    assert [n.chapter_idx for n in _notes(db, davone)] == [0]
    assert davone.identity != brown.identity
    assert "Neil" not in _entities(db)
    # And the same short name pointed at the other one in another book of the series.
    db.query(AIChapterDigest).filter_by(book_id=book.id, chapter_idx=3).delete()
    db.query(AIEntityNote).filter_by(book_id=book.id, chapter_idx=3).delete()
    _dig(db, user, book, 3, _entry("Neil", "Laughed.", ref=davone.id, identity="",
                                   evidence="his own face. Old Neil laughed"))
    assert [n.chapter_idx for n in _notes(db, davone)] == [0, 3]
    assert [n.chapter_idx for n in _notes(db, brown)] == [1]


def test_a_new_entry_with_a_known_name_attaches(db, guild):
    user, book = guild
    jason, _ = _jason_and_neil(db, user, book)
    _dig(db, user, book, 2, _entry("JASON  ASANO", "Saw a stranger.", identity="Someone.",
                                   evidence="Jason Asano saw the stranger"))
    assert len(_entities(db)) == 2
    assert [n.chapter_idx for n in _notes(db, jason)] == [0, 2]
    assert jason.identity == "A newcomer to the world."           # stable


def test_aliases_are_learned_only_from_the_text(db, guild):
    user, book = guild
    jason, _ = _jason_and_neil(db, user, book)
    _dig(db, user, book, 2, _entry("Jason Asano", "Saw a stranger.", ref=jason.id, identity="",
                                   aliases=["Jason", "The Outworlder", "jason"],
                                   evidence="Jason Asano saw the stranger"))
    assert digest.entity_aliases(jason) == [
        {"name": "Jason", "book_id": book.id, "chapter_idx": 2, "context_end": 2}]


def test_one_note_per_entity_and_chapter(db, guild):
    user, book = guild
    jason, _ = _jason_and_neil(db, user, book)
    _dig(db, user, book, 3,
         _entry("Jason Asano", "Saw his own face.", ref=jason.id, identity="",
                facts={"status": "shaken"}, evidence="Jason Asano saw his own face"),
         _entry("Jason", "Looked again.", ref=jason.id, identity="",
                facts={"status": "calm", "location": "the gate"},
                evidence="his hood, and Jason Asano saw"))
    notes = _notes(db, jason)
    assert [n.chapter_idx for n in notes] == [0, 3]
    assert notes[1].line == "Saw his own face."
    assert json.loads(notes[1].facts_json) == {"status": "shaken", "location": "the gate"}


def test_same_as_merges_from_that_chapter_on(db, guild):
    user, book = guild
    jason, neil = _jason_and_neil(db, user, book)
    _dig(db, user, book, 2, _entry("The Hooded Man", "Stalked the market.", identity="A stranger.",
                                   evidence="The Hooded Man stalked the market"),
         # Neil Davone is not in chapter 2: refused.
         same_as=[[jason.id, neil.id]])
    hooded = _entities(db)["The Hooded Man"]
    assert neil.merged_into is None and jason.merged_into is None
    _dig(db, user, book, 3, _entry("The Hooded Man", "Lowered his hood.", ref=hooded.id, identity="",
                                   evidence="The Hooded Man lowered his hood"),
         same_as=[[hooded.id, jason.id]])
    assert hooded.merged_into == jason.id
    assert (hooded.merged_book_id, hooded.merged_chapter_idx) == (book.id, 3)
    assert "The Hooded Man" in [a["name"] for a in digest.entity_aliases(jason)]
    notes = _notes(db, jason)
    assert [(n.chapter_idx, n.origin_entity_id) for n in notes] == [(0, None), (2, hooded.id), (3, hooded.id)]
    assert _notes(db, hooded) == []
    # A merged entity is not referable; its name still finds it.
    db.query(AIChapterDigest).filter_by(book_id=book.id, chapter_idx=1).delete()
    _dig(db, user, book, 1, _entry("Jason", "Bought a blade.", ref=hooded.id, identity="",
                                   evidence="the smith, sold Jason a blade"))
    assert "Jason" in _entities(db)                               # treated as new

    # Profiles: before the reveal they are two; from it on, one.
    scope = digest.Scope(book=book)
    before = {p.name: p for p in digest.profiles(db, scope, 3)}
    assert before["The Hooded Man"].entity_id == hooded.id
    assert [t["chapter_idx"] for t in before["The Hooded Man"].timeline] == [2]
    assert "The Hooded Man" not in before["Jason Asano"].aliases
    after = {p.name: p for p in digest.profiles(db, scope, 4)}
    assert "The Hooded Man" not in after
    assert [t["chapter_idx"] for t in after["Jason Asano"].timeline] == [0, 2, 3]
    assert "The Hooded Man" in after["Jason Asano"].aliases


def test_same_as_refused_when_a_name_is_not_in_the_text_or_kinds_differ(db, guild):
    user, book = guild
    jason, neil = _jason_and_neil(db, user, book)
    _dig(db, user, book, 2, _entry("The Hooded Man", "Stalked.", kind="term",
                                   evidence="The Hooded Man stalked the market"))
    hooded = _entities(db)["The Hooded Man"]
    _dig(db, user, book, 3, _entry("Jason Asano", "Saw his face.", ref=jason.id, identity="",
                                   evidence="Jason Asano saw his own face"),
         same_as=[[jason.id, neil.id], [hooded.id, jason.id], [jason.id, jason.id], [1], "x"])
    assert all(e.merged_into is None for e in _entities(db).values())


def test_a_multi_chapter_call_creates_then_refers(db, guild):
    user, book = guild

    def responder(feature, user_content, schema):
        return {"chapters": [
            {"chapter": 1, **_answer(_entry("Neil Davone", "Healed.", identity="A healer.",
                                            evidence="Neil Davone healed the wound"))},
            {"chapter": 2, **_answer(_entry("Neil Davone", "Frowned.", identity="",
                                            evidence="Neil Davone frowned at the price"))},
        ]}

    ai.set_provider_override(FakeProvider(responses=responder))
    rows = digest.digest_chapters(db, user, book, [0, 1])
    [neil] = _entities(db).values()
    assert [n.chapter_idx for n in _notes(db, neil)] == [0, 1]
    assert [json.loads(r.entries_json)[0]["entity_id"] for r in rows] == [neil.id, neil.id]
    assert neil.identity == "A healer."


def test_a_split_unit_stores_no_entities_or_notes(db, guild):
    user, book = guild
    ai.set_provider_override(FakeProvider(refuse=True))
    with pytest.raises(digest.UnitSplit):
        digest.digest_chapters(db, user, book, [0, 1])
    assert db.query(AIEntity).count() == 0 and db.query(AIEntityNote).count() == 0


def test_a_failed_entity_write_stores_neither_row_nor_notes(db, guild, monkeypatch):
    user, book = guild
    db.flush()
    # A session joined through a SAVEPOINT (as the job's), so its rollback
    # does not discard the fixtures.
    s = Session(bind=db.get_bind(), join_transaction_mode="create_savepoint", autoflush=False)

    def boom(self, *a, **k):
        s.add(AIEntity(series_key="x", kind="person", name="Half"))
        s.flush()
        raise RuntimeError("bug")

    monkeypatch.setattr(digest._EntityWriter, "write_chapter", boom)
    ai.set_provider_override(FakeProvider(responses=[_answer(
        _entry("Neil Davone", "Healed.", evidence="Neil Davone healed the wound"))]))
    try:
        with pytest.raises(RuntimeError):
            digest.digest_chapter(s, s.get(User, user.id), s.get(Book, book.id), 0)
    finally:
        s.close()
    assert digest.get_digest(db, book.id, 0) is None
    assert db.query(AIEntity).count() == 0


def test_profiles_fold_facts_latest_inside_the_window(db, guild):
    user, book = guild
    jason, _ = _jason_and_neil(db, user, book)
    _dig(db, user, book, 1, _entry("Jason", "Bought a blade.", ref=jason.id, identity="",
                                   facts={"status": "wounded"}, evidence="the smith, sold Jason a blade"))
    _dig(db, user, book, 2, _entry("Jason Asano", "Saw a stranger.", ref=jason.id, identity="",
                                   facts={"status": "dead", "location": "the gate"},
                                   evidence="Jason Asano saw the stranger"))
    scope = digest.Scope(book=book)
    [_, p] = digest.profiles(db, scope, 2)                       # Neil first, then Jason
    d = p.to_dict()
    assert d["entity_id"] == jason.id and d["identity"] == "A newcomer to the world."
    assert d["status"] == "wounded"
    assert d["facts"] == {
        "status": {"value": "wounded", "book_id": book.id, "chapter_idx": 1},
        "location": {"value": "the cart", "book_id": book.id, "chapter_idx": 0}}
    assert d["timeline"] == [{"book_id": book.id, "chapter_idx": 0, "line": "Watched from the cart."},
                             {"book_id": book.id, "chapter_idx": 1, "line": "Bought a blade."}]
    assert d["first_seen"] == {"book_id": book.id, "chapter_idx": 0}
    assert d["last_seen"] == {"book_id": book.id, "chapter_idx": 1}
    assert d["chapters"] == 2 and d["aliases"] == ["Jason"]
    assert set(d) == {"entity_id", "name", "kind", "aliases", "identity", "facts", "status",
                      "timeline", "first_seen", "last_seen", "chapters"}
    later = {p.name: p.to_dict() for p in digest.profiles(db, scope, 3)}
    assert later["Jason Asano"]["status"] == "dead" and later["Jason Asano"]["chapters"] == 3
    assert digest.profiles(db, scope, 0) == []


def test_profiles_synthesise_rows_written_before_entities(db, guild):
    user, book = guild
    _store_digest(db, book, 0, [_entry("Mara Vell", "Walked."), _entry("Neil Davone", "Healed.")])
    _store_digest(db, book, 1, [_entry("mara vell", "Rang a bell.")])
    # An entity of the same name and kind exists (a later chapter re-read).
    _dig(db, user, book, 2, _entry("Neil Davone", "Back.", identity="A healer.",
                                   evidence="Jason Asano saw the stranger") )   # quote fails: no entity
    assert db.query(AIEntity).count() == 0
    db.query(AIChapterDigest).filter_by(book_id=book.id, chapter_idx=2).delete()
    db.add(AIEntity(series_key=digest.series_key(book), kind="person", name="Neil Davone",
                    identity="A healer.", first_book_id=book.id, first_chapter_idx=0))
    db.flush()
    out = [p.to_dict() for p in digest.profiles(db, digest.Scope(book=book), 2)]
    assert [(p["name"], p["entity_id"] is None) for p in out] == [("Mara Vell", True), ("Neil Davone", False)]
    mara, neil = out
    assert mara["identity"] == "Walked." and mara["facts"] == {} and mara["status"] is None
    assert [t["line"] for t in mara["timeline"]] == ["Walked.", "Rang a bell."]
    assert mara["chapters"] == 2 and mara["first_seen"]["chapter_idx"] == 0
    assert neil["identity"] == "A healer." and [t["line"] for t in neil["timeline"]] == ["Healed."]


def test_entity_state_follows_merges_only_inside_prior(db, guild):
    """The state sent for a chapter before a merge lists both entities."""
    user, book = guild
    jason, _ = _jason_and_neil(db, user, book)
    _dig(db, user, book, 2, _entry("The Hooded Man", "Stalked.", identity="A stranger.",
                                   evidence="The Hooded Man stalked the market"))
    hooded = _entities(db)["The Hooded Man"]
    _dig(db, user, book, 3, _entry("Jason Asano", "Saw his face.", ref=jason.id, identity="",
                                   evidence="Jason Asano saw his own face"),
         same_as=[[jason.id, hooded.id]])
    assert hooded.merged_into == jason.id
    prior = digest._prior_digests(db, book, 3)                   # chapters 0..2: before the merge
    names = [s["name"] for s in digest.model_state(
        digest.seen_entities(prior, book.id, digest._series_entities(db, book)))]
    assert names == ["Neil Davone", "Jason Asano", "The Hooded Man"]
    prior4 = digest._prior_digests(db, book, 4)
    names4 = [s["name"] for s in digest.model_state(
        digest.seen_entities(prior4, book.id, digest._series_entities(db, book)))]
    assert names4 == ["Neil Davone", "Jason Asano"]


def test_prompts_carry_the_entity_rules():
    for prompt in (digest.SYSTEM_PROMPT, digest.SYSTEM_PROMPT_MANY):
        flat = " ".join(prompt.split())
        assert "Judge by context, not by spelling" in flat
        assert "Never set ref to an entity of a different kind" in flat
        assert "identity is who or what they are, never what they did" in flat
        assert "Facts only when this chapter establishes or changes them" in flat
        assert "viewpoint character and main characters are entries" in flat
        assert "same_as" in flat and "at most 25" in flat and "at most 3 sentences" in flat
        assert "Front or back matter" in flat and "never use what you know about it" in flat
    assert "refer to it with ref 0 and the same name" in " ".join(digest.SYSTEM_PROMPT_MANY.split())


def _strict(schema: dict) -> None:
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        for v in schema["properties"].values():
            _strict(v)
    if schema.get("type") == "array":
        _strict(schema["items"])


def test_schemas_are_strict_and_carry_the_entity_fields():
    for schema in (digest.build_schema(), digest.build_multi_schema()):
        _strict(schema)
    item = digest.build_schema()["properties"]["entries"]["items"]
    assert item["required"] == ["ref", "name", "kind", "identity", "aliases", "line", "facts", "evidence"]
    assert set(item["properties"]["facts"]["properties"]) == set(digest.FACT_FIELDS)
    multi = digest.build_multi_schema()["properties"]["chapters"]["items"]
    assert "same_as" in multi["required"]


# ── review fixes (2d step 3): the window rule for entity-level writes ──────

HOOD = [
    ("One", "The Hooded Man watched Jason Asano from the roof of the inn. " * 4),
    ("Two", "The Hooded Man and Jason Asano argued at the gate of the town. " * 4),
    ("Three", "Rain fell over the town and the market was quiet all day. " * 4),
    ("Four", "The Hooded Man lowered his hood and was Jason Asano from the future. " * 4),
]


def _hood_start(db, user, book):
    _dig(db, user, book, 0,
         _entry("The Hooded Man", "Watched.", identity="A watcher on the roofs.",
                evidence="The Hooded Man watched Jason Asano"),
         _entry("Jason Asano", "Was watched.", identity="A traveller.",
                evidence="The Hooded Man watched Jason Asano"))
    ents = _entities(db)
    return ents["The Hooded Man"], ents["Jason Asano"]


def test_a_multi_chapter_call_decides_nothing_for_a_reader_before_its_end(db, make_book, tmp_path):
    """A call over chapters 1..3 that proves a merge, learns an alias and
    meets a new place in chapter 1 read chapter 3 to do it: a reader in
    chapter 2 gets none of it (profiles and the state sent for chapter 2),
    a reader past chapter 3 gets all of it, and the single-chapter re-read of
    chapter 1 for the reader behind re-derives it from its own answer."""
    user = _make_user(db, "multi", "member")
    book = _epub_book(db, make_book, tmp_path, "Hood", series="Hood", index=1, chapters=HOOD)
    hooded, jason = _hood_start(db, user, book)

    def responder(feature, user_content, schema):
        return {"chapters": [
            {"chapter": 2, **_answer(
                _entry("Jason Asano", "Argued.", ref=jason.id, identity="A traveller.", aliases=["Asano"],
                       evidence="The Hooded Man and Jason Asano argued"),
                _entry("The Town", "Was argued in.", kind="place", identity="SECRET: the future's town.",
                       evidence="argued at the gate of the town"),
                same_as=[[jason.id, hooded.id]])},
            {"chapter": 3, **_answer(_entry("The Town", "Rained.", kind="place", identity="",
                                            evidence="Rain fell over the town"))},
            {"chapter": 4, **_answer(_entry("The Hooded Man", "Lowered his hood.", ref=hooded.id,
                                            identity="", evidence="The Hooded Man lowered his hood"))},
        ]}

    ai.set_provider_override(FakeProvider(responses=responder))
    digest.digest_chapters(db, user, book, [1, 2, 3])
    town = _entities(db)["The Town"]
    assert jason.merged_into == hooded.id and jason.merged_context_end == 3
    assert town.identity_context_end == 3
    assert {a["name"]: a["context_end"] for a in digest.entity_aliases(hooded)} == {
        "Jason Asano": 3, "Asano": 3}
    scope = digest.Scope(book=book)

    # Reader in chapter 2 (idx 2): row 1 is not usable, and nothing it decided is either.
    before = {p.name: p for p in digest.profiles(db, scope, 2)}
    assert set(before) == {"The Hooded Man", "Jason Asano"}
    assert before["The Hooded Man"].aliases == [] and before["Jason Asano"].aliases == []
    assert before["Jason Asano"].identity == "A traveller."
    state = digest.seen_entities(digest._prior_digests(db, book, 2), book.id,
                                 digest._series_entities(db, book), upto=2)
    # Two entities, not merged; the town's name is in chapter 1's text, its identity is not sent.
    assert {s.name: s.identity for s in state} == {"The Hooded Man": "A watcher on the roofs.",
                                                   "Jason Asano": "A traveller.", "The Town": ""}
    # Reader past the call: one entity, its aliases, the town.
    after = {p.name: p for p in digest.profiles(db, scope, 4)}
    assert "Jason Asano" not in after and set(after["The Hooded Man"].aliases) == {"Jason Asano", "Asano"}
    assert after["The Town"].identity == "SECRET: the future's town."

    # The reader behind gets chapter 1 re-read on its own (the job's replace
    # unit): no merge in that answer.
    assert [(u.idxs, u.replace) for u in digest.plan_units(db, scope, 2)] == [((1,), True)]
    fake = FakeProvider(responses=[_answer(
        _entry("Jason Asano", "Argued at the gate.", ref=jason.id, identity="A traveller.",
               evidence="The Hooded Man and Jason Asano argued"),
        _entry("The Town", "A gate.", kind="place", identity="A walled town.",
               evidence="argued at the gate of the town"))])
    ai.set_provider_override(fake)
    digest.digest_chapter(db, user, book, 1, replace_partial=True)
    assert "SECRET" not in sent_text(fake.calls[0])
    row1 = digest.get_digest(db, book.id, 1)
    assert row1.context_end_idx is None
    assert jason.merged_into is None and jason.merged_context_end is None
    assert digest.entity_aliases(hooded) == []
    assert town.identity == "A walled town." and town.identity_context_end == 1
    assert [(n.chapter_idx, n.origin_entity_id) for n in _notes(db, jason)] == [(0, None), (1, None)]
    now = {p.name: p for p in digest.profiles(db, scope, 2)}
    assert set(now) == {"The Hooded Man", "Jason Asano", "The Town"}
    assert now["The Town"].identity == "A walled town."


def test_an_earlier_first_sight_takes_that_chapters_identity(db, make_book, tmp_path):
    """Volume 3 read first gave an identity; volume 1 read later is the first
    sight and gives none: the volume 3 text never stays as the identity of a
    volume 1 sighting."""
    user = _make_user(db, "firstsight", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Hood1", series="Hood", index=1, chapters=HOOD)
    v3 = _epub_book(db, make_book, tmp_path, "Hood3", series="Hood", index=3, chapters=HOOD)
    _dig(db, user, v3, 0, _entry("Jason Asano", "x.", identity="SECRET: the future self of the Hooded Man.",
                                 evidence="The Hooded Man watched Jason Asano"))
    _dig(db, user, v1, 0, _entry("Jason Asano", "Was watched.", identity="",
                                 evidence="The Hooded Man watched Jason Asano"))
    j = _entities(db)["Jason Asano"]
    assert (j.first_book_id, j.first_chapter_idx) == (v1.id, 0)
    assert j.identity == "" and j.identity_context_end == 0
    profs = {p.name: p for p in digest.profiles(db, digest.Scope(book=v1), 2)}
    assert "SECRET" not in profs["Jason Asano"].identity
    # Given one there, the earlier chapter's identity is the identity.
    db.query(AIChapterDigest).filter_by(book_id=v1.id, chapter_idx=0).delete()
    _dig(db, user, v1, 0, _entry("Jason Asano", "Was watched.", identity="A traveller.",
                                 evidence="The Hooded Man watched Jason Asano"))
    assert j.identity == "A traveller."


def test_a_note_before_a_later_merge_stays_on_its_own_entity(db, make_book, tmp_path):
    """Two entities merged in volume 3; a volume 1 chapter digested afterwards
    names both: each keeps its own note (line and facts), and a volume 1
    reader sees two; a reader past the merge sees one with both lines."""
    user = _make_user(db, "mg", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Mask 1", series="Mask", index=1)
    v3 = _epub_book(db, make_book, tmp_path, "Mask 3", series="Mask", index=3)
    key = digest.series_key(v1)
    a = AIEntity(series_key=key, kind="person", name="Prince Aldo", aliases_json="[]", identity="A prince.",
                 first_book_id=v1.id, first_chapter_idx=0)
    b = AIEntity(series_key=key, kind="person", name="Masked Knight", aliases_json="[]",
                 identity="A knight in a mask.", first_book_id=v1.id, first_chapter_idx=0)
    db.add_all([a, b])
    db.flush()
    b.merged_into, b.merged_book_id, b.merged_chapter_idx = a.id, v3.id, 1
    db.flush()
    text = "Prince Aldo rode north. The Masked Knight wore a silver mask and fought well."
    checked = [
        ({"name": "Prince Aldo", "kind": "person", "line": "Rode north.", "more": "",
          "evidence": "Prince Aldo rode north"}, {"ref": a.id, "facts": {"location": "the north"}}),
        ({"name": "Masked Knight", "kind": "person", "line": "Fought well in a silver mask.", "more": "",
          "evidence": "The Masked Knight wore a silver mask"},
         {"ref": b.id, "facts": {"appearance": "silver mask"}}),
    ]
    out = digest._EntityWriter(db, v1).write_chapter(1, text, checked, [])
    assert [e["entity_id"] for e in out] == [a.id, b.id]          # the ref to b is accepted in volume 1
    db.add(AIChapterDigest(book_id=v1.id, chapter_idx=1, model="m", summary="s", entries_json=json.dumps(out)))
    db.add(AIChapterDigest(book_id=v1.id, chapter_idx=0, model="m", summary="s", entries_json="[]"))
    db.flush()
    notes = {n.entity_id: n for n in db.query(AIEntityNote).filter_by(book_id=v1.id)}
    assert json.loads(notes[a.id].facts_json) == {"location": "the north"}
    assert json.loads(notes[b.id].facts_json) == {"appearance": "silver mask"}
    assert notes[b.id].origin_entity_id is None
    early = {p.name: p.to_dict() for p in digest.profiles(db, digest.Scope(book=v1), 2)}
    assert set(early["Prince Aldo"]["facts"]) == {"location"}
    assert [t["line"] for t in early["Masked Knight"]["timeline"]] == ["Fought well in a silver mask."]
    _read(db, user, v1)
    late = {p.name: p.to_dict()
            for p in digest.profiles(db, digest.user_scope(db, user, v3), 2)}
    assert "Masked Knight" not in late
    assert set(late["Prince Aldo"]["facts"]) == {"location", "appearance"}
    assert late["Prince Aldo"]["timeline"][0]["line"] == "Rode north. Fought well in a silver mask."


def test_a_later_merge_does_not_conflate_an_earlier_volumes_notes(db, make_book, tmp_path):
    user = _make_user(db, "conflate", "member")
    v2 = _epub_book(db, make_book, tmp_path, "HoodA", series="HoodX", index=2, chapters=HOOD)
    v3 = _epub_book(db, make_book, tmp_path, "HoodB", series="HoodX", index=3, chapters=HOOD)
    _dig(db, user, v3, 0,
         _entry("The Hooded Man", "Watched.", evidence="The Hooded Man watched Jason Asano"),
         _entry("Jason Asano", "Was watched.", evidence="The Hooded Man watched Jason Asano"))
    e = _entities(db)
    h, j = e["The Hooded Man"], e["Jason Asano"]
    _dig(db, user, v3, 3, _entry("Jason Asano", "Revealed.", ref=j.id, identity="",
                                 evidence="The Hooded Man lowered his hood and was Jason Asano"),
         same_as=[[h.id, j.id]])
    assert j.merged_into == h.id
    _dig(db, user, v2, 1,
         _entry("The Hooded Man", "Argued.", ref=h.id, identity="", facts={"status": "masked"},
                evidence="The Hooded Man and Jason Asano argued"),
         _entry("Jason Asano", "Argued back.", ref=j.id, identity="",
                facts={"location": "the gate", "status": "angry"},
                evidence="The Hooded Man and Jason Asano argued"))
    profs = {p.name: p.to_dict() for p in digest.profiles(db, digest.Scope(book=v2), 2)}
    assert {k: v["value"] for k, v in profs["The Hooded Man"]["facts"].items()} == {"status": "masked"}
    assert {k: v["value"] for k, v in profs["Jason Asano"]["facts"].items()} == {
        "location": "the gate", "status": "angry"}
    assert [t["line"] for t in profs["Jason Asano"]["timeline"]] == ["Argued back."]


def test_the_losers_note_in_the_merge_chapter_reaches_the_survivor(db, guild):
    user, book = guild
    jason, _ = _jason_and_neil(db, user, book)
    _dig(db, user, book, 2, _entry("The Hooded Man", "Stalked.", identity="A stranger.",
                                   evidence="The Hooded Man stalked the market"))
    hooded = _entities(db)["The Hooded Man"]
    _dig(db, user, book, 3,
         _entry("Jason Asano", "Saw his own face.", ref=jason.id, identity="",
                evidence="Jason Asano saw his own face"),
         _entry("The Hooded Man", "Lowered his hood.", ref=hooded.id, identity="",
                facts={"appearance": "Jason's face"}, evidence="The Hooded Man lowered his hood"),
         same_as=[[hooded.id, jason.id]])
    assert hooded.merged_into == jason.id
    [kept] = [n for n in _notes(db, hooded)]                     # both named in chapter 3: stays
    assert kept.chapter_idx == 3
    scope = digest.Scope(book=book)
    after = {p.name: p.to_dict() for p in digest.profiles(db, scope, 4)}
    assert "The Hooded Man" not in after
    t3 = [t for t in after["Jason Asano"]["timeline"] if t["chapter_idx"] == 3]
    assert t3 and "Lowered his hood." in t3[0]["line"] and "Saw his own face." in t3[0]["line"]
    assert after["Jason Asano"]["facts"]["appearance"]["value"] == "Jason's face"
    assert after["Jason Asano"]["identity"] == "A newcomer to the world."


SHADOW = [
    ("Docks", "Kell Varn met Mira Holt at the docks. The water was black and still all night. " * 4),
    ("Night", "The Grey Wolf prowled the docks with Mira Holt and nobody saw his face. " * 4),
    ("Truth", "Kell Varn laughed. Kell Varn, the Grey Wolf, had been hiding all along. " * 4),
]


def test_a_ref_or_merge_never_uses_an_alias_learned_later(db, make_book, tmp_path):
    user = _make_user(db, "shadow", "member")
    book = _epub_book(db, make_book, tmp_path, "Shadow", chapters=SHADOW)
    _dig(db, user, book, 0,
         _entry("Kell Varn", "Met Mira.", evidence="Kell Varn met Mira Holt"),
         _entry("Mira Holt", "Met Kell.", evidence="Kell Varn met Mira Holt"))
    kell, mira = _entities(db)["Kell Varn"], _entities(db)["Mira Holt"]
    _dig(db, user, book, 2, _entry("Kell Varn", "Was the Grey Wolf.", ref=kell.id, identity="",
                                   aliases=["Grey Wolf"], evidence="Kell Varn, the Grey Wolf, had"))
    assert [a["name"] for a in digest.entity_aliases(kell)] == ["Grey Wolf"]
    # Chapter 1 read afterwards: the alias from chapter 2 neither carries the ref nor the merge.
    row, _ = _dig(db, user, book, 1,
                  _entry("Grey Wolf", "Prowled.", ref=kell.id, identity="A prowler.",
                         evidence="The Grey Wolf prowled the docks"),
                  same_as=[[kell.id, mira.id]])
    [item] = json.loads(row.entries_json)
    assert item["entity_id"] != kell.id and _entities(db)["Grey Wolf"].id == item["entity_id"]
    assert mira.merged_into is None and kell.merged_into is None
    # From chapter 2 on the alias counts.
    db.query(AIChapterDigest).filter_by(book_id=book.id, chapter_idx=2).delete()
    row, _ = _dig(db, user, book, 2, _entry("Grey Wolf", "Laughed.", ref=kell.id, identity="",
                                            aliases=["Grey Wolf"], evidence="Kell Varn, the Grey Wolf, had"))
    assert json.loads(row.entries_json)[0]["entity_id"] == kell.id


def test_the_state_sends_no_identity_from_a_row_the_chapter_cannot_use(db, make_book, tmp_path):
    user = _make_user(db, "stateid", "member")
    book = _epub_book(db, make_book, tmp_path, "StateId", chapters=SHADOW)

    def responder(feature, user_content, schema):
        return {"chapters": [{"chapter": 1, **_answer(_entry("Kell Varn", "Met Mira.", identity="Secretly the Grey Wolf.",
                                                             evidence="Kell Varn met Mira Holt"))},
                             {"chapter": 2, **_answer()}, {"chapter": 3, **_answer()}]}

    ai.set_provider_override(FakeProvider(responses=responder))
    digest.digest_chapters(db, user, book, [0, 1, 2])
    ents = digest._series_entities(db, book)
    prior = digest._prior_digests(db, book, 1)
    [s] = digest.seen_entities(prior, book.id, ents, upto=1)
    assert s.name == "Kell Varn" and s.identity == ""             # its call read chapter 2
    [s] = digest.seen_entities(digest._prior_digests(db, book, 3), book.id, ents, upto=3)
    assert s.identity == "Secretly the Grey Wolf."
    # The single re-read of chapter 1 (for a reader in chapter 1) sends no identity either.
    fake = FakeProvider(responses=[_answer()])
    ai.set_provider_override(fake)
    digest.digest_chapter(db, user, book, 1, replace_partial=True)
    assert "Secretly" not in sent_text(fake.calls[0]) and "Kell Varn" in sent_text(fake.calls[0])


PLAY = ("ACT I. SCENE V. A hall in Capulet's house. Enter the Maskers. "
        "ROMEO. What lady is that, which doth enrich the hand of yonder knight? "
        "SERVANT. I know not, sir. "
        "TYBALT. This, by his voice, should be a Montague. Fetch me my rapier, boy.")


def test_a_speakers_own_line_keeps_the_speaker_with_a_widened_quote():
    raw = [_entry("Romeo", "Sees Juliet.", evidence="What lady is that, which doth enrich"),
           _entry("Tybalt", "Hears a Montague.", evidence="Fetch me my rapier, boy")]
    got = {e["name"]: e["evidence"] for e in digest.check_entries(raw, PLAY)}
    assert got["Romeo"] == "ROMEO. What lady is that, which doth enrich"
    assert "TYBALT" in got["Tybalt"] and "Fetch me my rapier, boy" in got["Tybalt"]
    for ev in got.values():
        assert digest.evidence_in_text(ev, digest.normalize_text(PLAY))
        assert 3 <= digest.book_text.count_words(ev) <= digest.MAX_EVIDENCE_WORDS
    # The name after the line ("...," said Mara) works too.
    text = "\"The tide is turning against us tonight,\" said Mara, and left the room."
    [e] = digest.check_entries([_entry("Mara", "Warns.", evidence="The tide is turning against us")], text)
    assert "Mara" in e["evidence"] and "The tide is turning against us" in e["evidence"]
    # A name 20 words away is past the speaker rule; the entry is then kept
    # on the name itself (it is in the chapter), re-anchored on it.
    far = ("ROMEO. " + " ".join(f"word{i}" for i in range(20))
           + " What lady is that, which doth enrich the hand.")
    [e] = digest.check_entries([_entry("Romeo", "Sees.", evidence="What lady is that, which doth")], far)
    assert e["evidence"].startswith("ROMEO.") and "What lady" not in e["evidence"]
    # A quote that is not in the chapter stays dropped.
    assert digest.check_entries([_entry("Romeo", "Sees.", evidence="What lord is that")], PLAY) == []


def test_prompts_ask_for_speaker_tags_returning_names_and_stable_identities():
    for prompt in (digest.SYSTEM_PROMPT, digest.SYSTEM_PROMPT_MANY):
        flat = " ".join(prompt.split())
        assert "in a play or dialogue, quote the speaker tag with the line" in flat
        assert "List a glossary entity again whenever this chapter changes any of its facts" in flat
        assert "Main and viewpoint characters are listed in every chapter they appear in" in flat
        assert "for every entry, new or ref" in flat
        assert '"City where Romeo is exiled" is wrong, "A city in Lombardy" is right' in flat
        assert "at most 25" in flat
    many = " ".join(digest.SYSTEM_PROMPT_MANY.split())
    assert "not only the names new in it" in many


def test_a_ref_note_carries_its_identity_and_a_reader_without_the_first_volume_gets_it(
        db, make_book, tmp_path):
    user = _make_user(db, "noteid", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Guild V1", series="Guild", index=1, chapters=GUILD)
    v2 = _epub_book(db, make_book, tmp_path, "Guild V2", series="Guild", index=2, chapters=GUILD)
    _dig(db, user, v1, 0, _entry("Neil Davone", "Healed a wound.", identity="Jason's teammate, a healer.",
                                 evidence="Neil Davone healed the wound"))
    neil = _entities(db)["Neil Davone"]
    row, _ = _dig(db, user, v2, 1, _entry("Neil Davone", "Frowned at a price.", ref=neil.id,
                                          identity="A guild healer.", evidence="Neil Davone frowned at the price"))
    assert json.loads(row.entries_json)[0]["more"] == ""          # entries_json keeps its shape
    [note] = [n for n in _notes(db, neil) if n.book_id == v2.id]
    assert note.identity == "A guild healer."
    newcomer = _make_user(db, "noteid2", "member")                # never read volume 1
    [p] = digest.profiles(db, digest.user_scope(db, newcomer, v2), 2)
    assert p.identity == "A guild healer." and p.identity != "Frowned at a price."
    _read(db, user, v1)
    [p] = digest.profiles(db, digest.user_scope(db, user, v2), 2)
    assert p.identity == "Jason's teammate, a healer."


def test_budget_word_count_counts_cjk_characters():
    text = "王林走进了房间。他坐下了。"
    assert ai._content_words(text) == book_text.count_words(text) > len(text.split())
    assert ai._content_words([{"type": "text", "text": text}, {"type": "text", "text": "two words"}]) == \
        book_text.count_words(text) + 2


def test_renaming_a_series_keeps_its_glossary(db, guild):
    user, book = guild
    jason, neil = _jason_and_neil(db, user, book)
    book.series = "The Adventurers' Guild"
    db.flush()
    assert digest._series_entities(db, book) == {}
    profs = {p.name: p for p in digest.profiles(db, digest.Scope(book=book), 1)}
    assert set(profs) == {"Jason Asano", "Neil Davone"}
    assert profs["Neil Davone"].entity_id == neil.id and profs["Neil Davone"].identity == "Jason's teammate, a healer."


LONG_SPEECH = ("ROMEO. " + " ".join(f"line{i} of the speech" for i in range(12))
               + " Thus with a kiss I die. JULIET. What's here? a cup, closed in my true love's hand?")


def test_a_quote_far_from_its_speaker_tag_keeps_the_entry_when_the_name_is_in_the_chapter():
    """The second smoke: Romeo's "Thus with a kiss I die" sits 36 words from
    the ROMEO tag, so the speaker rule could not tie it; the name is in the
    chapter, so the entry stays with a verbatim slice naming it."""
    [e] = digest.check_entries([_entry("Romeo", "Dies.", evidence="Thus with a kiss I die")], LONG_SPEECH)
    assert "ROMEO" in e["evidence"]
    assert digest.evidence_in_text(e["evidence"], digest.normalize_text(LONG_SPEECH))
    assert 3 <= digest.book_text.count_words(e["evidence"]) <= digest.WIDE_EVIDENCE_WORDS
    # A name the chapter never writes, or a quote not in the chapter: still dropped.
    assert digest.check_entries([_entry("Tybalt", "Dies.", evidence="Thus with a kiss I die")], LONG_SPEECH) == []
    assert digest.check_entries([_entry("Romeo", "Dies.", evidence="Thus with a sigh I die")], LONG_SPEECH) == []


def test_a_ref_identity_that_echoes_the_glossary_line_is_not_this_chapters_note(db, make_book, tmp_path):
    """The second smoke: digesting volume 2 with volume 1's entities in the
    state, the model copied their identities ("City to which Romeo is sent in
    exile") into the volume 2 notes, and a reader without volume 1 got them.
    A ref's identity that repeats the glossary line is stored as no identity."""
    user = _make_user(db, "echo", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Guild E1", series="GuildE", index=1, chapters=GUILD)
    v2 = _epub_book(db, make_book, tmp_path, "Guild E2", series="GuildE", index=2, chapters=GUILD)
    _dig(db, user, v1, 0, _entry("Neil Davone", "Healed a wound.",
                                 identity="Jason's teammate, a healer who was later exiled to Mantua.",
                                 evidence="Neil Davone healed the wound"))
    neil = _entities(db)["Neil Davone"]
    for ident in ("Jason's teammate, a healer who was later exiled to Mantua.",
                  "A healer, Jason's teammate, later exiled to Mantua"):
        db.query(AIChapterDigest).filter_by(book_id=v2.id, chapter_idx=1).delete()
        db.query(AIEntityNote).filter_by(book_id=v2.id, chapter_idx=1).delete()
        _dig(db, user, v2, 1, _entry("Neil Davone", "Frowned at a price.", ref=neil.id, identity=ident,
                                     evidence="Neil Davone frowned at the price"))
        [note] = [n for n in _notes(db, neil) if n.book_id == v2.id]
        assert note.identity == ""
    assert digest.echoes("A healer.", "Jason's teammate, a healer who was later exiled to Mantua.") is False
    db.query(AIChapterDigest).filter_by(book_id=v2.id, chapter_idx=1).delete()
    db.query(AIEntityNote).filter_by(book_id=v2.id, chapter_idx=1).delete()
    _dig(db, user, v2, 1, _entry("Neil Davone", "Frowned at a price.", ref=neil.id,
                                 identity="A guild healer haggling at the market.",
                                 evidence="Neil Davone frowned at the price"))
    [note] = [n for n in _notes(db, neil) if n.book_id == v2.id]
    assert note.identity == "A guild healer haggling at the market."
    # A reader without volume 1 never sees the volume 1 line.
    newcomer = _make_user(db, "echo2", "member")
    [p] = digest.profiles(db, digest.user_scope(db, newcomer, v2), 2)
    assert "Mantua" not in p.identity and "exiled" not in p.identity
