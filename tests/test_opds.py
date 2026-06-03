"""Regression tests for OPDS feed serialization (GH #15).

The Atom feed must serialize with the *default* namespace
(``<feed xmlns="http://www.w3.org/2005/Atom">``) and the
``application/atom+xml`` content type. Strict OPDS clients such as KOReader
silently reject prefixed namespaces (``<ns0:feed xmlns:ns0=...>``) and show an
empty catalog.

The historical failure mode was a process-global ``register_namespace`` collision
between ``services/opds.py`` and ``services/metadata_embed.py`` — both wanted the
empty ('') prefix, and whichever was imported last won, breaking the other.
"""
import xml.etree.ElementTree as ET

from backend.core.security import get_current_user_basic
from backend.services.opds import make_feed, add_navigation_entry, feed_response


def _first_tag(body: str) -> str:
    # body is "<?xml ...?>\n<feed ...>..."
    return body.split("\n", 1)[1]


def test_feed_uses_default_atom_namespace():
    feed = make_feed("urn:tome:root", "Tome", "http://x/opds", kind="navigation")
    add_navigation_entry(feed, "urn:tome:all", "All Books", "http://x/opds/all")
    body = feed_response(feed).body.decode()

    assert '<feed xmlns="http://www.w3.org/2005/Atom">' in body
    assert "ns0:" not in body
    assert "<ns0:feed" not in body


def test_feed_content_type_is_atom_xml():
    feed = make_feed("urn:tome:root", "Tome", "http://x/opds")
    resp = feed_response(feed)
    assert "application/atom+xml" in resp.media_type
    assert "json" not in resp.media_type


def test_feed_robust_against_global_namespace_clobber():
    """Even if another module steals the '' prefix for a foreign namespace,
    the OPDS feed must still serialize Atom as the default namespace."""
    # Simulate metadata_embed (or any other module) winning the '' prefix.
    ET.register_namespace("", "http://www.idpf.org/2007/opf")
    try:
        feed = make_feed("urn:tome:root", "Tome", "http://x/opds")
        body = feed_response(feed).body.decode()
        assert '<feed xmlns="http://www.w3.org/2005/Atom">' in body
        assert "ns0:" not in body
    finally:
        # Restore the Atom default so we don't leak clobbered state to other tests.
        ET.register_namespace("", "http://www.w3.org/2005/Atom")


def test_opds_root_endpoint_serves_default_namespace(client):
    """End-to-end: the live /opds endpoint returns atom+xml with a default ns."""

    class _U:
        id = 1
        is_admin = True
        role = "admin"
        username = "t"

    client.app.dependency_overrides[get_current_user_basic] = lambda: _U()
    try:
        resp = client.get("/opds")
        assert resp.status_code == 200
        assert "application/atom+xml" in resp.headers["content-type"]
        body = _first_tag(resp.text)
        assert body.startswith('<feed xmlns="http://www.w3.org/2005/Atom">')
        assert "ns0:" not in resp.text
    finally:
        client.app.dependency_overrides.pop(get_current_user_basic, None)
