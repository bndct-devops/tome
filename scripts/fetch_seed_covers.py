"""Download all cover art for the showcase seed.

Three sources:
  1. Prod Tome (covers we already have) — manga, Eric Ugland, One Piece
  2. Hardcover (using TOME_HARDCOVER_TOKEN from .env) — western fiction, Vinland Saga, Frieren
  3. Wikimedia Commons (direct URLs) — comic first-issues

All output lands in docs/seed/covers/ with stable filenames like
    {slug}.jpg
so the seed script can reference them by name.

Idempotent — already-downloaded covers are skipped unless --force.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COVERS_DIR = REPO_ROOT / "docs" / "seed" / "covers"
SCRIBE_CFG = Path.home() / ".config" / "tome" / "scribe.json"
ENV_FILE = REPO_ROOT / ".env"

# Load HC token from .env
HC_TOKEN: str | None = None
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("TOME_HARDCOVER_TOKEN="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            HC_TOKEN = val.removeprefix("Bearer ").strip()
            break

# Load prod URL + token
PROD_URL: str | None = None
PROD_TOKEN: str | None = None
if SCRIBE_CFG.exists():
    cfg = json.loads(SCRIBE_CFG.read_text())
    prod = cfg.get("profiles", {}).get("prod", {})
    PROD_URL = prod.get("url", "").rstrip("/")
    PROD_TOKEN = prod.get("token")


# ── 1. Books to fetch from prod by book ID ────────────────────────────────────

PROD_COVERS = [
    # Berserk vol 1-5
    ("berserk-01", 479),
    ("berserk-02", 480),
    ("berserk-03", 481),
    ("berserk-04", 482),
    ("berserk-05", 483),
    ("berserk-06", 484),
    ("berserk-07", 485),
    ("berserk-08", 486),
    ("berserk-09", 487),
    ("berserk-10", 488),
    # The Good Guys vol 1-3
    ("good-guys-01", 14),
    ("good-guys-02", 1),
    ("good-guys-03", 2),
    # The Bad Guys vol 1-3 (includes Skull and Thrones at vol 3)
    ("bad-guys-01", 45),
    ("bad-guys-02", 44),
    ("bad-guys-03", 46),  # Skull and Thrones
    # One Piece vol 1-10
    ("one-piece-01", 210),
    ("one-piece-02", 211),
    ("one-piece-03", 212),
    ("one-piece-04", 213),
    ("one-piece-05", 214),
    ("one-piece-06", 215),
    ("one-piece-07", 216),
    ("one-piece-08", 217),
    ("one-piece-09", 218),
    ("one-piece-10", 219),
]


# ── 2. Books to fetch from Hardcover (GraphQL) ────────────────────────────────

HARDCOVER_BOOKS = [
    # slug, search query, hint to disambiguate (author/year)
    ("project-hail-mary", "Project Hail Mary", "Andy Weir"),
    ("dune", "Dune", "Frank Herbert"),
    ("ready-player-one", "Ready Player One", "Ernest Cline"),
    # Hitchhiker's moved to direct-URL fetch (Open Library) — Hardcover search returns wrong editions.
    ("vinland-saga-01", "Vinland Saga Omnibus 1", "Makoto Yukimura"),
    ("vinland-saga-02", "Vinland Saga Omnibus 2", "Makoto Yukimura"),
    ("vinland-saga-03", "Vinland Saga Omnibus 3", "Makoto Yukimura"),
    ("frieren-01", "Frieren Beyond Journey's End Vol. 1", "Kanehito Yamada"),
    ("frieren-02", "Frieren Beyond Journey's End Vol. 2", "Kanehito Yamada"),
    ("frieren-03", "Frieren Beyond Journey's End Vol. 3", "Kanehito Yamada"),
]


# ── 3. Comic first-issue covers (Wikimedia direct URLs) ───────────────────────

URL_COVERS = [
    # Wikimedia (comic first-issues)
    ("action-comics-01",   "https://upload.wikimedia.org/wikipedia/en/5/5a/Action_Comics_1.jpg"),
    ("amazing-fantasy-15", "https://upload.wikimedia.org/wikipedia/en/3/35/Amazing_Fantasy_15.jpg"),
    ("detective-comics-27","https://upload.wikimedia.org/wikipedia/en/b/bc/Detective_Comics_27_%28May_1939%29.png"),
    ("watchmen-01",        "https://upload.wikimedia.org/wikipedia/en/a/a2/Watchmen%2C_issue_1.jpg"),
    # Open Library (where Hardcover returned wrong matches)
    ("hitchhikers-guide",  "https://covers.openlibrary.org/b/isbn/9780345391803-L.jpg"),
]


# ── HTTP helpers ──────────────────────────────────────────────────────────────

UA = "tome-seed-fetcher/1.0 (https://github.com/bndct-devops/tome; petutschnig.benedict@gmail.com)"


def http_get(url: str, headers: dict | None = None, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def resize_to_jpeg(raw: bytes, max_width: int = 600, quality: int = 85) -> bytes:
    """Downscale + re-encode as JPEG. Matches backend/services/metadata.py:save_cover."""
    try:
        from PIL import Image
    except ImportError:
        return raw  # PIL not installed → keep original
    img = Image.open(io.BytesIO(raw))
    if img.mode != "RGB":
        img = img.convert("RGB")
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def http_post_json(url: str, body: dict, headers: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


# ── Fetchers ──────────────────────────────────────────────────────────────────

def fetch_from_prod(slug: str, book_id: int, out: Path) -> str:
    if not PROD_URL:
        return "SKIP (no prod URL)"
    try:
        raw = http_get(f"{PROD_URL}/api/books/{book_id}/cover")
        data = resize_to_jpeg(raw)
        out.write_bytes(data)
        return f"✓ ({len(data)//1024}KB)"
    except Exception as e:
        return f"FAIL: {e}"


def fetch_from_hardcover(slug: str, query: str, hint: str, out: Path) -> str:
    if not HC_TOKEN:
        return "SKIP (no HC token)"
    gql = """
    query Search($q: String!) {
      search(query: $q, query_type: "books", per_page: 5) {
        results
      }
    }
    """
    try:
        r = http_post_json(
            "https://api.hardcover.app/v1/graphql",
            {"query": gql, "variables": {"q": query}},
            {"Authorization": f"Bearer {HC_TOKEN}"},
        )
        hits = r.get("data", {}).get("search", {}).get("results", {}).get("hits", [])
        # Prefer hit whose author contains the hint
        chosen = None
        for h in hits:
            doc = h.get("document", {}) or {}
            authors = doc.get("author_names") or []
            if any(hint.lower() in (a or "").lower() for a in authors):
                chosen = doc
                break
        if not chosen and hits:
            chosen = hits[0].get("document", {})
        if not chosen:
            return "FAIL: no hits"
        img = chosen.get("image", {}).get("url") or chosen.get("cached_image", {}).get("url")
        if not img:
            return f"FAIL: no image url (title={chosen.get('title')!r})"
        raw = http_get(img)
        data = resize_to_jpeg(raw)
        out.write_bytes(data)
        return f"✓ ({len(data)//1024}KB)  [{chosen.get('title')!r}]"
    except Exception as e:
        return f"FAIL: {e}"


def fetch_from_url(slug: str, url: str, out: Path) -> str:
    try:
        raw = http_get(url)
        data = resize_to_jpeg(raw)
        out.write_bytes(data)
        return f"✓ ({len(data)//1024}KB)"
    except Exception as e:
        return f"FAIL: {e}"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Re-download even if file exists")
    parser.add_argument("--only", help="Comma-separated slug prefixes to limit to")
    args = parser.parse_args()

    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    only = args.only.split(",") if args.only else None

    def should(slug: str) -> bool:
        return not only or any(slug.startswith(p.strip()) for p in only)

    print(f"Saving to {COVERS_DIR}\n")
    print(f"HC token:  {'set' if HC_TOKEN else 'MISSING'}")
    print(f"Prod URL:  {PROD_URL or 'MISSING'}\n")

    def run(label: str, slug: str, fetch_fn) -> None:
        if not should(slug):
            return
        out = COVERS_DIR / f"{slug}.jpg"
        if out.exists() and not args.force:
            print(f"  [skip] {slug.ljust(22)} (exists, {out.stat().st_size//1024}KB)")
            return
        print(f"  [{label}] {slug.ljust(22)} ", end="", flush=True)
        result = fetch_fn(out)
        print(result)

    print("── From prod ──")
    for slug, book_id in PROD_COVERS:
        run("prod", slug, lambda o, _id=book_id, _s=slug: fetch_from_prod(_s, _id, o))

    print("\n── From Hardcover ──")
    for slug, query, hint in HARDCOVER_BOOKS:
        run("hc  ", slug, lambda o, _q=query, _h=hint, _s=slug: fetch_from_hardcover(_s, _q, _h, o))

    print("\n── From direct URLs (Wikimedia + Open Library) ──")
    for slug, url in URL_COVERS:
        run("url ", slug, lambda o, _u=url, _s=slug: fetch_from_url(_s, _u, o))

    print()
    have = sorted(p.name for p in COVERS_DIR.glob("*.jpg"))
    print(f"\nTotal in {COVERS_DIR}: {len(have)} covers")


if __name__ == "__main__":
    main()
