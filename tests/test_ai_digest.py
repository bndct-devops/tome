"""Reading companion mechanism: chapter text (book_text), per-chapter digests
(spoiler window, citation check, fold, scope), the digest job and its
triggers. FakeProvider only; books and names are made up."""
from __future__ import annotations

import json
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from ebooklib import epub
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.ai_chapter_digest import AIChapterDigest
from backend.models.ai_usage import AIUsage
from backend.models.book import Book, BookChapter
from backend.models.tome_sync import ApiKey, TomeSyncPosition
from backend.models.user import User
from backend.models.user_book_status import UserBookStatus
from backend.services import ai, book_text
from backend.services.ai import companion, digest, digest_job, settings as ai_settings
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
                          entries_json=json.dumps(entries))
    db.add(row)
    db.flush()
    return row


def _entry(name: str, line: str, kind: str = "person", evidence: str = "x") -> dict:
    return {"name": name, "kind": kind, "line": line, "more": f"More on {name}.", "evidence": evidence}


def echo_digest(feature, user_content, schema):
    """Answers with one entry per call, quoting the chapter's first six words."""
    text = user_content[-1]["text"]
    words = text.split()
    return {"summary": "Something happened.",
            "entries": [{"name": words[0], "kind": "term", "line": "A word.", "more": "",
                         "evidence": " ".join(words[:6])}]}


@pytest.fixture(autouse=True)
def _jobs_inline(db: Session, monkeypatch):
    """Jobs never start a thread here; tests run them with run_pending(). The
    job gets its own session on the test connection, joined through a
    SAVEPOINT, so its commits are visible to ``db`` and a rollback after a
    failed chapter does not discard the test's fixtures."""
    @contextmanager
    def same_session():
        s = Session(bind=db.connection(), join_transaction_mode="create_savepoint", autoflush=False)
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
    pct, xp, _ = device_position(book, 1, PARAS - 1)
    r = c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": pct, "progress": xp}, headers=h)
    assert r.status_code == 200, r.text
    [job] = _queued()
    assert job["upto_chapter"] == 1                 # chapter 0 only: the reader is still in 1
    fake = FakeProvider(responses=echo_digest)
    ai.set_provider_override(fake)
    digest_job.run_pending()
    sent = json.dumps([call["user_content"] for call in fake.calls])
    assert "S0_" in sent and "S1_" not in sent and "S2_" not in sent


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
        # Too short to be a quote.
        _entry("Verona", "A city.", kind="place", evidence="fair Verona"),
        # One part of the name is enough.
        _entry("Juliet Capulet", "A young woman.", evidence="Juliet, come hither"),
        # Case and whitespace differ, the name is there.
        _entry("Romeo", "A young man.", evidence="ENTER   romeo and mercutio"),
        # A title alone does not tie a quote to a name.
        _entry("Lord Capulet", "Juliet's father.", evidence="Juliet, come hither"),
    ], text)
    assert [e["name"] for e in entries] == ["Juliet Capulet", "Romeo"]
    assert digest.names_entry("Tybalt", "Tybalt's sword is out")
    assert not digest.names_entry("Mab", "Mabel came in")


# ── fold ─────────────────────────────────────────────────────────────────────

def test_fold_later_line_wins_first_seen_kept_names_merge():
    class D:
        def __init__(self, book_id, idx, entries):
            self.book_id, self.chapter_idx, self.entries_json = book_id, idx, json.dumps(entries)

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
    blocks = call["user_content"]
    assert blocks[-1]["text"] == book_text.chapter_text(book, 1, db)
    sent = json.dumps(blocks) + call["system"]
    for later in (2, 3):
        assert f"Mark{later}end" not in sent
    assert "Oren Pike" not in sent            # first named in chapter 2
    assert "LATER-SPOILER" not in sent        # a later digest never feeds an earlier one
    context = json.loads(blocks[0]["text"].split("\n\n", 1)[1].rsplit("\n\nThe chapter text", 1)[0])
    assert [e["name"] for e in context["glossary_so_far"]] == ["Mara Vell"]

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
    assert len(fake.calls) == 3
    assert {r.chapter_idx for r in db.query(AIChapterDigest).filter_by(book_id=book.id)} == {0, 1, 2, 3}
    rows = db.query(AIUsage).filter_by(user_id=user.id, feature="reading_digest").all()
    assert len(rows) == 3 and all(r.key_source == "user" for r in rows)


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


def test_job_continues_past_a_failing_chapter(db, make_book, tmp_path):
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


def test_job_stops_after_three_consecutive_failures(db, make_book, tmp_path):
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


def test_a_refused_chapter_is_billed_once_and_never_resent(client, db, make_book, tmp_path):
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
    digest_job.run_pending()
    g = client.get(f"/api/ai/books/{book.id}/glossary", headers=_headers(user)).json()
    assert g["coverage"]["failed"] == 1 and g["coverage"]["digested"] == g["coverage"]["needed"]
    assert g["job"] is None
    r = client.post(f"/api/ai/books/{book.id}/recap", json={}, headers=_headers(user))
    assert r.status_code == 200, r.text
    assert r.json()["partial"] is True                  # written without chapter 1
    assert sent_chapter_1() == 1
    assert db.query(AIUsage).filter_by(user_id=user.id, feature="reading_digest").count() == usage + 1

    # "Ask again" is the explicit retry: the placeholder goes, chapter 1 is tried once more.
    assert client.delete(f"/api/ai/books/{book.id}/recap", headers=_headers(user)).status_code == 200
    assert digest.get_digest(db, book.id, 1) is None
    r = client.post(f"/api/ai/books/{book.id}/recap", json={}, headers=_headers(user))
    assert r.status_code == 202
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
    assert "A ferry pilot from volume one." in fake.calls[0]["user_content"][0]["text"]


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

    r = c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": inside_0}, headers=h)
    assert r.status_code == 200, r.text
    assert _queued() == []                      # still in chapter 0: nothing finished

    r = c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": inside_2}, headers=h)
    assert r.status_code == 200
    [job] = _queued()
    assert job["book_id"] == book.id and job["user_id"] == user.id
    assert job["upto_chapter"] == 2             # chapters 0 and 1 only, never the current one

    c.put(f"/api/tome-sync/position/{book.id}", json={"percentage": inside_2 + 0.001}, headers=h)
    assert len(_queued()) == 1                  # same chapter again: no second job

    ai.set_provider_override(FakeProvider(responses=echo_digest))
    digest_job.run_pending()
    assert {r.chapter_idx for r in db.query(AIChapterDigest).filter_by(book_id=book.id)} == {0, 1}


def test_position_trigger_digests_the_current_book_only(db, make_book, tmp_path, plugin_client):
    c, user, h = plugin_client
    v1 = _epub_book(db, make_book, tmp_path, "Sound One", series="Sound", index=1)
    v2 = _epub_book(db, make_book, tmp_path, "Sound Two", series="Sound", index=2)
    _read(db, user, v1)
    db.commit()
    digest_job.reset()                          # marking v1 read queued its own job
    chapters = book_text.book_chapters(v2, db)
    pct = (chapters[2].start_fraction + chapters[3].start_fraction) / 2
    r = c.put(f"/api/tome-sync/position/{v2.id}", json={"percentage": pct}, headers=h)
    assert r.status_code == 200
    ai.set_provider_override(FakeProvider(responses=echo_digest))
    digest_job.run_pending()
    rows = {(r.book_id, r.chapter_idx) for r in db.query(AIChapterDigest)}
    assert rows == {(v2.id, 0), (v2.id, 1)}     # volume 1 waits for an on-demand recap or glossary

    # The on-demand glossary widens it to the read earlier volume.
    g = c.get(f"/api/tome-sync/ai/books/{v2.id}/glossary?pct={pct}", headers=h).json()
    assert g["job"] is not None
    digest_job.run_pending()
    assert {(r.book_id, r.chapter_idx) for r in db.query(AIChapterDigest)} >= {(v1.id, i) for i in range(4)}


def test_web_status_put_with_a_cfi_places_the_reader(db, make_book, tmp_path):
    from backend.core.database import get_db
    from backend.main import create_app

    user = _make_user(db, "webcfi", "member")
    book = _illustrated_book(db, make_book, tmp_path, "Web Cfi")
    pct, _, cfi = device_position(book, 1, PARAS - 1)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(app) as c:
        r = c.put(f"/api/books/{book.id}/status",
                  json={"status": "reading", "progress_pct": pct, "cfi": cfi}, headers=_headers(user))
        assert r.status_code == 200, r.text
    [job] = _queued()
    assert job["upto_chapter"] == 1


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
    pct = (chapters[2].start_fraction + chapters[3].start_fraction) / 2
    app = create_app()
    app.dependency_overrides[get_db] = lambda: (yield db)
    with TestClient(app) as c:
        r = c.put(f"/api/books/{book.id}/status", json={"status": "reading", "progress_pct": pct},
                  headers=_headers(user))
        assert r.status_code == 200, r.text
    [job] = _queued()
    assert job["upto_chapter"] == 2 and job["followups"] == []


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
    assert job["book_id"] == v1.id and job["upto_chapter"] == 4
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
