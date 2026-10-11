"""The EPUB package walk, read straight from the zip: container.xml -> OPF ->
manifest + spine.

Shared by the identification text sample (``text_sample.py``) and the
zip-level chapter-map parser (``metadata._zip_chapter_inputs``), which the
reading companion's chapter text (``book_text.py``) reuses so its chapter
boundaries match the stored ``BookChapter`` fractions.

Reading the zip directly (rather than through ebooklib) is what lets these
paths handle technically malformed but perfectly readable EPUBs. Tag matching
is by local name, so a package with or without the OPF namespace reads the
same.
"""
from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from urllib.parse import unquote


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass(frozen=True)
class ManifestItem:
    id: str
    href: str          # OPF-relative, as written in the manifest
    media_type: str
    properties: str


@dataclass
class EpubPackage:
    opf_path: str
    opf_dir: str
    root: ET.Element
    manifest: dict[str, ManifestItem] = field(default_factory=dict)
    spine: list[str] = field(default_factory=list)   # itemref idrefs, reading order
    spine_toc: str | None = None                      # <spine toc="..."> (the NCX id)
    cover_ids: set[str] = field(default_factory=set)  # ids/hrefs the OPF marks as cover

    def member(self, href: str) -> str:
        """Zip member name for an OPF-relative href (fragment dropped, %-escapes decoded)."""
        return posixpath.normpath(posixpath.join(self.opf_dir, unquote(href.split("#")[0])))


def read_package(zf: zipfile.ZipFile) -> EpubPackage | None:
    """Parse the package document, or None when the container or OPF cannot be read."""
    try:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = next(el for el in container.iter() if local_name(el.tag) == "rootfile")
        opf_path = rootfile.attrib["full-path"]
        root = ET.fromstring(zf.read(opf_path))
    except Exception:  # noqa: BLE001 - malformed package: the caller decides the fallback
        return None

    pkg = EpubPackage(opf_path=opf_path, opf_dir=posixpath.dirname(opf_path), root=root)
    for el in root.iter():
        tag = local_name(el.tag)
        if tag == "item":
            item = ManifestItem(
                id=el.attrib.get("id", ""),
                href=el.attrib.get("href", ""),
                media_type=el.attrib.get("media-type", ""),
                properties=el.attrib.get("properties", ""),
            )
            pkg.manifest[item.id] = item
        elif tag == "spine":
            pkg.spine_toc = el.attrib.get("toc") or None
        elif tag == "itemref":
            pkg.spine.append(el.attrib.get("idref", ""))
        elif tag == "meta" and el.attrib.get("name") == "cover":
            pkg.cover_ids.add(el.attrib.get("content", ""))
        elif tag == "reference" and el.attrib.get("type", "").lower() == "cover":
            pkg.cover_ids.add(el.attrib.get("href", "").split("#")[0])
    return pkg
