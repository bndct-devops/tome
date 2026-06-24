"""Word-count parsing: the CJK-aware tokenizer + EPUB counting + ingest hook."""
from pathlib import Path

from ebooklib import epub

from backend.services.metadata import (
    count_words_text,
    count_words_epub,
    extract_metadata,
)


def _make_epub(path: Path, chapters: list[str]) -> None:
    book = epub.EpubBook()
    book.set_identifier("wc-test")
    book.set_title("Word Count Test")
    book.set_language("en")
    items = []
    for i, txt in enumerate(chapters):
        c = epub.EpubHtml(title=f"Chapter {i}", file_name=f"c{i}.xhtml", lang="en")
        c.content = f"<html><body><p>{txt}</p></body></html>"
        book.add_item(c)
        items.append(c)
    book.toc = tuple(items)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + items
    epub.write_epub(str(path), book)


class TestTokenizer:
    def test_plain_english(self):
        assert count_words_text("The quick brown fox jumps") == 5

    def test_contractions_and_hyphens_are_one_word(self):
        # "don't" and "well-known" each count once
        assert count_words_text("don't stop the well-known fox") == 5

    def test_punctuation_is_not_words(self):
        assert count_words_text("Hello, world!  —  ok.") == 3

    def test_cjk_counts_per_character(self):
        # No spaces between CJK chars; each ideograph is one "word".
        assert count_words_text("你好世界") == 4

    def test_mixed_latin_and_cjk(self):
        assert count_words_text("Hello 你好") == 3  # Hello + 你 + 好

    def test_empty(self):
        assert count_words_text("") == 0
        assert count_words_text("   \n\t  ") == 0


class TestEpubWordCount:
    def test_counts_body_text(self, tmp_path):
        body = " ".join(f"word{i}" for i in range(50))  # 50 words
        path = tmp_path / "book.epub"
        _make_epub(path, [body])
        count = count_words_epub(path)
        assert count is not None
        # Nav/TOC adds a little, body is the floor.
        assert count >= 50

    def test_sums_across_chapters(self, tmp_path):
        path = tmp_path / "multi.epub"
        _make_epub(path, ["alpha beta gamma", "delta epsilon"])  # 3 + 2
        count = count_words_epub(path)
        assert count is not None
        assert count >= 5

    def test_html_tags_not_counted(self, tmp_path):
        path = tmp_path / "tags.epub"
        # Markup tokens must not inflate the count.
        _make_epub(path, ['<strong>one</strong> <em>two</em> three'])
        count = count_words_epub(path)
        assert count is not None
        assert count >= 3 and count < 20

    def test_corrupt_file_returns_none(self, tmp_path):
        bad = tmp_path / "bad.epub"
        bad.write_bytes(b"not really an epub")
        assert count_words_epub(bad) is None

    def test_falls_back_to_zip_when_ebooklib_cannot_parse(self, tmp_path):
        # No META-INF/OPF → ebooklib raises, but the XHTML is readable, so the
        # zip fallback still counts it (mirrors the broken-TOC/nav real files).
        import zipfile

        p = tmp_path / "broken.epub"
        body = " ".join(f"w{i}" for i in range(40))
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr("OEBPS/ch1.xhtml", f"<html><body><p>{body}</p></body></html>")
            zf.writestr("OEBPS/ch2.xhtml", "<html><body><p>alpha beta gamma</p></body></html>")
        count = count_words_epub(p)
        assert count is not None
        assert count >= 43


class TestIngestHook:
    def test_extract_metadata_sets_word_count(self, tmp_path):
        body = " ".join(f"w{i}" for i in range(120))
        path = tmp_path / "ingest.epub"
        _make_epub(path, [body])
        meta = extract_metadata(path, tmp_path / "covers")
        assert "word_count" in meta
        assert meta["word_count"] >= 120
