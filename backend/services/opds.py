"""OPDS 1.2 Atom XML feed builder helpers."""
import xml.etree.ElementTree as ET
from datetime import datetime

from fastapi.responses import Response

ATOM_NS = "http://www.w3.org/2005/Atom"
DC_NS = "http://purl.org/dc/terms/"
OPDS_NS = "http://opds-spec.org/2010/catalog"
OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"
TOME_NS = "https://tome.app/ns/1.0"

ET.register_namespace("", ATOM_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("opds", OPDS_NS)
ET.register_namespace("opensearch", OPENSEARCH_NS)
ET.register_namespace("tome", TOME_NS)

ACQUISITION_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"
NAVIGATION_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"

FORMAT_MIME: dict[str, str] = {
    "epub": "application/epub+zip",
    "pdf": "application/pdf",
    "cbz": "application/x-cbz",
    "cbr": "application/x-cbr",
    "mobi": "application/x-mobipocket-ebook",
    "azw3": "application/x-mobi8-ebook",
}


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def make_feed(
    id: str,
    title: str,
    self_url: str,
    kind: str = "navigation",
    up_url: str | None = None,
    search_url: str | None = "/opds/search",
) -> ET.Element:
    feed = ET.Element(f"{{{ATOM_NS}}}feed")
    ET.SubElement(feed, f"{{{ATOM_NS}}}id").text = id
    ET.SubElement(feed, f"{{{ATOM_NS}}}title").text = title
    ET.SubElement(feed, f"{{{ATOM_NS}}}updated").text = _now()

    author_el = ET.SubElement(feed, f"{{{ATOM_NS}}}author")
    ET.SubElement(author_el, f"{{{ATOM_NS}}}name").text = "Tome"

    ET.SubElement(feed, f"{{{ATOM_NS}}}link", attrib={
        "rel": "self", "href": self_url,
        "type": f"application/atom+xml;profile=opds-catalog;kind={kind}",
    })
    ET.SubElement(feed, f"{{{ATOM_NS}}}link", attrib={
        "rel": "start", "href": "/opds", "type": NAVIGATION_TYPE,
    })
    if up_url:
        ET.SubElement(feed, f"{{{ATOM_NS}}}link", attrib={
            "rel": "up", "href": up_url, "type": NAVIGATION_TYPE,
        })
    if search_url:
        ET.SubElement(feed, f"{{{ATOM_NS}}}link", attrib={
            "rel": "search", "href": search_url,
            "type": "application/opensearchdescription+xml",
        })
    return feed


def add_navigation_entry(
    feed: ET.Element, id: str, title: str, href: str, content: str = ""
) -> None:
    entry = ET.SubElement(feed, f"{{{ATOM_NS}}}entry")
    ET.SubElement(entry, f"{{{ATOM_NS}}}id").text = id
    ET.SubElement(entry, f"{{{ATOM_NS}}}title").text = title
    ET.SubElement(entry, f"{{{ATOM_NS}}}updated").text = _now()
    if content:
        ET.SubElement(entry, f"{{{ATOM_NS}}}content", attrib={"type": "text"}).text = content
    ET.SubElement(entry, f"{{{ATOM_NS}}}link", attrib={
        "rel": "subsection", "href": href, "type": ACQUISITION_TYPE,
    })


def add_book_entry(feed: ET.Element, book, base_url: str, display_title: str | None = None) -> None:
    entry = ET.SubElement(feed, f"{{{ATOM_NS}}}entry")
    ET.SubElement(entry, f"{{{ATOM_NS}}}id").text = f"urn:tome:book:{book.id}"
    ET.SubElement(entry, f"{{{TOME_NS}}}book-id").text = str(book.id)
    ET.SubElement(entry, f"{{{ATOM_NS}}}title").text = display_title or book.title or "Untitled"
    ET.SubElement(entry, f"{{{ATOM_NS}}}updated").text = (
        book.updated_at.isoformat() + "Z" if getattr(book, "updated_at", None) else _now()
    )

    if book.author:
        author_el = ET.SubElement(entry, f"{{{ATOM_NS}}}author")
        ET.SubElement(author_el, f"{{{ATOM_NS}}}name").text = book.author

    if getattr(book, "language", None):
        ET.SubElement(entry, f"{{{DC_NS}}}language").text = book.language
    if getattr(book, "publisher", None):
        ET.SubElement(entry, f"{{{DC_NS}}}publisher").text = book.publisher
    if getattr(book, "isbn", None):
        ET.SubElement(entry, f"{{{DC_NS}}}identifier").text = f"urn:isbn:{book.isbn}"
    if getattr(book, "year", None):
        ET.SubElement(entry, f"{{{DC_NS}}}issued").text = str(book.year)

    if getattr(book, "description", None):
        ET.SubElement(entry, f"{{{ATOM_NS}}}content", attrib={"type": "text"}).text = book.description

    for tag in getattr(book, "tags", []):
        ET.SubElement(entry, f"{{{ATOM_NS}}}category", attrib={
            "term": tag.tag, "label": tag.tag,
        })

    if getattr(book, "cover_path", None):
        cover_href = f"{base_url}/api/books/{book.id}/cover"
        ET.SubElement(entry, f"{{{ATOM_NS}}}link", attrib={
            "rel": "http://opds-spec.org/image",
            "href": cover_href, "type": "image/jpeg",
        })
        ET.SubElement(entry, f"{{{ATOM_NS}}}link", attrib={
            "rel": "http://opds-spec.org/image/thumbnail",
            "href": cover_href, "type": "image/jpeg",
        })

    for f in getattr(book, "files", []):
        mime = FORMAT_MIME.get(f.format, "application/octet-stream")
        attrib: dict[str, str] = {
            "rel": "http://opds-spec.org/acquisition/open-access",
            "href": f"/opds/download/{book.id}/{f.id}",
            "type": mime,
            "title": f.format.upper(),
        }
        if f.file_size:
            attrib["length"] = str(f.file_size)
        ET.SubElement(entry, f"{{{ATOM_NS}}}link", attrib=attrib)


def add_pagination(
    feed: ET.Element, self_url: str, page: int, per_page: int, total: int
) -> None:
    ET.SubElement(feed, f"{{{OPENSEARCH_NS}}}totalResults").text = str(total)
    ET.SubElement(feed, f"{{{OPENSEARCH_NS}}}itemsPerPage").text = str(per_page)
    ET.SubElement(feed, f"{{{OPENSEARCH_NS}}}startIndex").text = str((page - 1) * per_page)

    sep = "&" if "?" in self_url else "?"
    if page * per_page < total:
        ET.SubElement(feed, f"{{{ATOM_NS}}}link", attrib={
            "rel": "next",
            "href": f"{self_url}{sep}page={page + 1}",
            "type": ACQUISITION_TYPE,
        })
    if page > 1:
        ET.SubElement(feed, f"{{{ATOM_NS}}}link", attrib={
            "rel": "previous",
            "href": f"{self_url}{sep}page={page - 1}",
            "type": ACQUISITION_TYPE,
        })


def feed_response(feed: ET.Element) -> Response:
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(feed, encoding="unicode")
    return Response(content=xml, media_type="application/atom+xml;charset=utf-8")
