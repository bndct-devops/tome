# Wishlist — Implementation Plan

> Status: **PLANNED, not started.** Sibling-level detail to the send-to-device
> feature (PR #1). Scope for this plan is **wishlist + admin-fulfill loop only**.
> Smart release detection is a *separate* future plan — but the schema and
> service seams below are deliberately designed so detection bolts on with **no
> refactor** (see [§11 Forward-compatibility](#11-forward-compatibility-for-detection)).

Branch: `feat/wishlist`

---

## 1. Summary

Members can add books or series they *want* to a personal **wishlist**. Each
wish is captured as a **structured reference** (via the existing Hardcover /
Google Books / OpenLibrary metadata search) so it carries title, author, cover,
and an external source id — with a free-text fallback for anything the search
can't find.

Admins get a **Wishlist tab** in the admin area showing every member's open
wishes with requester names. When a matching book lands in the library (manual
upload, ingest, Bindery, or scan), Tome **surfaces matching open wishes to the
admin and auto-suggests fulfilment** — one click marks the wish fulfilled and
notifies the requester **in-app and by email** (email only when SMTP is
configured, reusing the send-to-device service).

Design intent:
- **Valuable to a single user on day one** (a reading TODO list), so it does not
  depend on having many users.
- **Fulfilment is opt-in generosity**, not an SLA — no queue guilt.
- **The wish and the upload close the loop automatically** via a matcher, so the
  list never rots.

Roles: **members and admins** can create wishes (guests cannot, consistent with
guests being browse/download/read/OPDS only). Members see **only their own**
wishes; admins see **all**.

---

## 2. Data model

One primitive — a **watch entry** — modelled now as a wishlist, shaped so a
later `kind = "follow"` + detection columns require only additive migrations.

### `backend/models/wish.py` → `Wish`

```
id              int PK
user_id         FK users.id ON DELETE CASCADE, indexed   # the requester
kind            String(16) default "wish"   # "wish" today; "follow" reserved for detection
status          String(16) default "open"   # open | fulfilled | dismissed

# Structured reference (from metadata search) — all nullable for free-text wishes
title           String(512) NOT NULL        # always present (search pick OR typed)
author          String(255) nullable
series          String(255) nullable        # set when the wish is a whole series
cover_url       String(1024) nullable        # remote cover from the search result
source          String(32) nullable          # "hardcover" | "google_books" | "open_library" | "manual"
source_id       String(128) nullable         # external id from that source
isbn            String(20) nullable

# Free-text
note            Text nullable                # member's optional note ("the new one")

# Fulfilment linkage
fulfilled_book_id   FK books.id ON DELETE SET NULL, nullable
fulfilled_by        FK users.id ON DELETE SET NULL, nullable   # the admin who fulfilled
fulfilled_at        DateTime nullable

created_at      DateTime default utcnow
updated_at      DateTime default utcnow onupdate utcnow

# ── reserved for the detection plan (added now, unused, so no later refactor) ──
external_series_id  String(128) nullable     # canonical series id on the tracker
last_checked_at     DateTime nullable        # detection poll bookkeeping
latest_known_index  Float nullable           # highest volume the tracker reports

__table_args__ = (
    UniqueConstraint("user_id", "source", "source_id", name="uq_wish_user_source"),
    Index("ix_wish_status", "status"),
)

→ user:           relationship User (requester)
→ fulfilled_book: relationship Book
```

Notes / nitpicks baked in:
- `title` is **NOT NULL** even for structured wishes — denormalised so the list
  renders without a join and survives the external record changing.
- The `UniqueConstraint(user_id, source, source_id)` stops a member adding the
  same Hardcover book twice. Free-text wishes have `source=NULL` so they don't
  collide (SQLite treats NULLs as distinct in unique indexes — intended).
- `kind`, `external_series_id`, `last_checked_at`, `latest_known_index` are
  **dead columns today**. They exist so the detection plan is *additive only*.

### Cross-member dedup (the "two people want the same book" case)

We do **not** collapse rows across members (keeps per-requester provenance and
the email-on-fulfil simple). Instead the **matcher** ([§6](#6-the-matcher))
finds *all* open wishes matching a new book — across every member — and the
admin fulfils them together. One upload → N wishes closed → N notifications.

### Registration / migration

- Add `from backend.models.wish import Wish` to `backend/models/__init__.py`.
- Add `import backend.models.wish` to `tests/conftest.py::_init_test_db`.
- Tables auto-create on startup via `Base.metadata.create_all()` — in **dev and
  prod**. `Wish` and `Notification` are brand-new tables, so `create_all()`
  (run in `backend/main.py`'s lifespan on every startup) provisions them
  (including the reserved detection columns) on both, exactly as every prior
  table-adding feature since `content_type` shipped (devices, series_meta,
  audit_log, quick_connect, api_tokens, …). **No Alembic migration is written for
  this feature** — Alembic *is* set up (`backend/alembic/`, 6 historical
  migrations) but has been effectively abandoned: the runtime path is
  `create_all()` plus a few manual `ALTER TABLE … ADD COLUMN` blocks in the
  lifespan for columns on *existing* tables. A plain `docker pull` + restart
  creates the new tables — verified empirically against a 1.0.0-seeded DB.
- **Future caveat:** the *detection* plan will add columns to the now-existing
  `Wish` table — `create_all()` does **not** alter live tables, so those columns
  need a manual `ALTER TABLE` block in the lifespan (the established pattern for
  `content_type` / `is_reviewed` / `role`), not an Alembic migration. The reserved
  columns here exist precisely to avoid even that.
- Versioning: this is an additive feature → **minor bump, 1.0.0 → 1.1.0** (SemVer).

---

## 3. Config / secrets

No new secrets. Reuses:
- **`hardcover_token`** (already in `config.py`) for structured search.
- **`TOME_SMTP_*`** (already present) for fulfilment emails. If SMTP is not
  configured, email is silently skipped and only the in-app notice fires.

New config in `backend/core/config.py`:
- `wishlist_enabled: bool = True` (env `TOME_WISHLIST_ENABLED`) — kill switch for
  the whole feature; defaults on. Not an alpha flag — the wishlist is a shipped
  feature. (The *detection* alpha toggle belongs to the later plan; see §11.)
- `wishlist_max_open_per_user: int = 100` (env `TOME_WISHLIST_MAX`) — soft cap to
  prevent runaway lists; 409 when exceeded. Mirrors the send-to-device limit
  pattern.

`.env.example` / docs/configuration updated with both vars.

---

## 4. Backend API — `backend/api/wishlist.py`

Mounted at `/api` in `main.py`, `tags=["wishlist"]`. All routes accept JWT or
`tome_*` token (universal auth). Role checks via `backend.core.permissions`.

### Member endpoints (member+)

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/wishlist` | Current user's wishes (filter `?status=open`). |
| `POST` | `/wishlist` | Create a wish. Body below. Enforces `wishlist_max_open_per_user` (409) and the unique constraint (409 on dup). |
| `DELETE` | `/wishlist/{id}` | Remove **own** wish (404 if not owner). |
| `GET` | `/wishlist/search?q=` | Proxy to existing `metadata_fetch.fetch_candidates` so the member picks a structured result. Returns the candidate list (title/author/cover/source/source_id). |

`POST /wishlist` body:
```json
{
  "title": "string (required)",
  "author": "string | null",
  "series": "string | null",
  "cover_url": "string | null",
  "source": "hardcover | google_books | open_library | manual | null",
  "source_id": "string | null",
  "isbn": "string | null",
  "note": "string | null"
}
```
Free-text path: client sends just `title` (+ optional `note`), `source` omitted
→ stored as `source=null` ("manual").

### Admin endpoints (admin only — `require_role(user, "admin")`)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/admin/wishlist` | All wishes, joined with requester username; filters `?status=`, `?user_id=`. Includes a `match_count`/`suggested_book_ids` hint when an open wish matches existing library books. |
| `POST` | `/admin/wishlist/{id}/fulfill` | Body `{ "book_id": int }`. Sets status=fulfilled, links `fulfilled_book_id`, `fulfilled_by`, `fulfilled_at`; triggers notification ([§7](#7-notifications)). 409 if already fulfilled. |
| `POST` | `/admin/wishlist/{id}/dismiss` | Admin closes a wish without fulfilling (status=dismissed). Notifies requester in-app (no email). |
| `GET` | `/admin/wishlist/matches?book_id=` | Given a book, return open wishes (any member) that match it — used by the "you just added X, it satisfies these wishes" prompt. |

Audit: every fulfil/dismiss writes `audit(db, action="wishlist.fulfilled" / "wishlist.dismissed", resource_type="wish", resource_id=..., details={requester, book_id})`.

### Schemas — `backend/schemas/wish.py`

`WishCreate`, `WishOut` (member view), `WishAdminOut` (adds `requester_username`,
`suggested_book_ids`), `WishSearchResult`, `FulfillRequest`. Pydantic v2,
`model_config = {"from_attributes": True}`. `cover_url` validated as http(s).

---

## 5. Services

### `backend/services/wishlist.py`
- `create_wish(db, user, payload) -> Wish` — cap + unique enforcement, normalises
  series names with the existing sanitiser used on ingest.
- `fulfill_wish(db, wish, book, admin) -> Wish` — state transition + notify.
- `search_candidates(q) -> list` — thin wrapper over `metadata_fetch.fetch_candidates`.

### `backend/services/wish_matcher.py` (the seam — see §6)
- `find_matching_wishes(db, book) -> list[Wish]`
- `match_on_book_created(db, book)` — called from every book-creation site.

---

## 6. The matcher

A new book can be born at **four** sites (confirmed in code):
- `backend/api/books.py:1843` (upload) and `:2050` (ingest)
- `backend/services/scanner.py:253` (folder scan)
- `backend/api/bindery.py:339` (Bindery accept / auto-import)

To avoid four divergent implementations, **all four call one function**:

```python
from backend.services.wish_matcher import match_on_book_created
match_on_book_created(db, book)   # after db.add(book); db.flush()
```

Matching strategy (cheap, deterministic, admin-confirmed — never silent):
1. **ISBN exact** (book.isbn == wish.isbn) → strong.
2. **Series + index** (book.series == wish.series, same volume) → strong.
3. **Title + author fuzzy** (`SequenceMatcher > 0.85`, reusing the threshold from
   `admin_duplicates.py`) → weak, suggestion only.

The matcher **never auto-fulfils**. On a manual upload/ingest by an admin it
returns the matches so the API can surface "this satisfies Maya's wish → Fulfill?".
For Bindery/scan (no admin in the loop) it leaves the wish open and flags it so
it appears under `GET /admin/wishlist` with `suggested_book_ids` populated. This
keeps the human-confirm rule intact (mirrors the duplicate-dismissal philosophy).

---

## 7. Notifications

User chose **both in-app + email**. There is no in-app notification system today,
so we add the minimum viable one (reusable later for detection).

### In-app — `backend/models/notification.py` → `Notification`
```
id, user_id (FK CASCADE, indexed), kind String(32), title String(255),
body Text nullable, link String(512) nullable, read bool default False,
created_at DateTime
```
Endpoints (`backend/api/notifications.py`):
- `GET /notifications?unread=true` — current user's notices.
- `POST /notifications/{id}/read`, `POST /notifications/read-all`.

Frontend: a small bell in the top bar with an unread count; clicking a
"wish fulfilled" notice links to the book detail page. Kept generic so detection
("vol 12 is out") reuses it verbatim.

### Email — reuse send-to-device SMTP
Add `send_wish_fulfilled_email(to_email, wish, book)` to
`backend/services/email.py`. Fires only when `settings.smtp_configured`; failures
are swallowed and logged (never block fulfilment). Plain template: "Your wish
*{title}* is now in the library — read it here {link}."

Fulfilment flow: `fulfill_wish` → write `Notification` (always) → attempt email
(if SMTP configured) → audit.

---

## 8. Frontend

### Member surfaces
- **`WishlistModal.tsx`** — "Add to wishlist" with a search box (debounced →
  `GET /wishlist/search`) rendering candidate cards (cover/title/author/source
  badge); pick one → structured wish. A "Can't find it? Add manually" toggle
  reveals title/author/note fields → free-text wish.
- **`pages/WishlistPage.tsx`** (route `/wishlist`) — the member's own list:
  open wishes (cover, title, author, note, "remove"), and a collapsed
  "Fulfilled" section linking to the now-available books. Sidebar nav entry with
  a Lucide icon (`Sparkles` or `Heart` — strings, per icon rule).
- Entry points: a "Wishlist" item in `Sidebar.tsx`; an "Add to wishlist" action
  on `BookDetailPage` is **not** applicable (book already exists) — instead a
  global "+ Wish" button in the wishlist page header and an empty-state CTA.

### Admin surfaces
- **`AdminPage.tsx` → new "Wishlist" tab** (mirrors the Email tab structure):
  table of all open wishes — requester, title/author/cover, age, and a
  **Fulfill** button. Fulfill opens a small picker: if `suggested_book_ids` is
  non-empty, preselect the match; otherwise let the admin search existing books
  or note that they need to upload it first. A "Dismiss" action with confirm.
  Tabs for Open / Fulfilled / Dismissed.
- **Post-upload prompt**: after an admin upload/ingest, if the response carries
  matched wishes, show a toast/modal "This satisfies N wish(es) — Fulfill all?".

### Plumbing
- `frontend/src/lib/wishlist.ts` — typed API client (list/create/delete/search,
  admin list/fulfill/dismiss).
- Bell + `lib/notifications.ts` for the notification primitive.
- "Learn more →" links via `lib/docs.ts` (`DOCS.wishlist`).

---

## 9. Tests (DoD)

Backend (`tests/test_wishlist.py`, `tests/test_wish_matcher.py`,
`tests/test_notifications.py`):
- Member creates structured wish; appears in `GET /wishlist`.
- Free-text wish (no source) persists; two free-text wishes don't collide.
- Duplicate structured wish (same source+source_id) → 409.
- Cap exceeded → 409.
- Guest cannot create (403); member cannot see another member's wish (own-only).
- Admin sees all wishes with requester names; non-admin gets 403 on `/admin/wishlist`.
- **Matcher**: ISBN match, series+index match, fuzzy title+author match each
  surface the right wish; non-match returns none.
- Fulfil: status→fulfilled, links set, `Notification` row created, audit written.
  Email attempted only when SMTP configured (monkeypatch `send_wish_fulfilled_email`,
  assert called / not-called).
- Fulfil already-fulfilled → 409. Dismiss → status + in-app notice, no email.
- Matcher fires from **all four** creation paths (upload, ingest, scan, bindery)
  — parametrised test asserting an open matching wish gets `suggested_book_ids`.
- Notification endpoints: list unread, mark read, mark-all, ownership enforced.

Frontend: `npm run build` (per CLAUDE.md — `tsc -b` catches unused imports that
root `tsc --noEmit` misses) must pass clean.

Run `pytest` after backend changes (per memory).

---

## 10. Docs & screenshots (DoD)

- **`website/src/pages/docs/wishlist.astro`** — feature page (member how-to +
  admin fulfil flow + email/in-app notice explanation). Add to `docs-nav.ts`
  under "Features" after Send to device.
- **`frontend/src/lib/docs.ts`** — add `wishlist: docsUrl('/docs/wishlist')`.
- **`docs/screenshots/`** + `website/public/shots/{light,dark,amber}/` — add to
  `frontend/scripts/screenshots.mjs` and regenerate in all three themes
  (generous padding, per memory): wishlist page (empty + populated), add-wish
  search modal, free-text fallback, admin wishlist tab, fulfil picker,
  post-upload "satisfies N wishes" prompt, notification bell.
- **`docs/configuration.astro`** — `TOME_WISHLIST_ENABLED`, `TOME_WISHLIST_MAX`.
- **`docs/features.md`** + root **`CLAUDE.md` "What's Built"** — add Wishlist entry.
- **`CHANGELOG.md`** — `[Unreleased] / Added` entry.

---

## 11. Forward-compatibility for detection

The user's explicit constraint: *wishlist-first must not force a refactor when
detection arrives.* How this plan guarantees that:

| Detection will need | Provided now |
|---|---|
| A "follow this series" entity | `Wish.kind` column (`"wish"` today, `"follow"` later) — same table, same API shape. |
| Canonical external series id | `Wish.external_series_id` column (dead now). |
| Poll bookkeeping | `Wish.last_checked_at`, `Wish.latest_known_index` columns (dead now). |
| "New volume!" alerts | The generic `Notification` model + bell (built now for fulfilment, reused verbatim). |
| Series ↔ external match UI | Lives on the **series detail page** in the detection plan; stores into `external_series_id`. Not built now, but the column it writes to exists. |
| Admin alpha gate | The detection plan adds an **`AppSetting` key/value store** (does not exist today — all current toggles are env vars) for the in-app alpha switch. Out of scope here; flagged so the detection plan owns it. |
| Release source | Hardcover (already integrated) + MangaUpdates + Royal Road RSS — detection plan. |

Net: detection is **additive migrations + new service + reused notification/UI
primitives**. No table rename, no API breaking change, no data backfill.

---

## 12. Open questions / assumptions

Resolved with you:
- Scope = wishlist + admin-fulfil; schema forward-compatible (above). ✔
- Wish capture = structured search + free-text fallback. ✔
- Notify = both in-app + email (email only when SMTP configured). ✔
- Visibility = own + admin-sees-all. ✔

Still assumed (defaults chosen; flag if wrong):
1. **Guests cannot wish** (members+ only). 
2. **No cross-member row collapse** — N wishes, N notifications, fulfilled together
   by the matcher rather than merged into one row.
3. **Matcher confirm-only on every path** — Bindery/scan never auto-fulfil; they
   flag the wish for admin review (consistent with duplicate-dismissal ethos).
4. **Soft cap 100 open wishes/user** (`TOME_WISHLIST_MAX`).
5. **Series-wish granularity** — a member can wish for a whole series (`series`
   set, `series_index` absent); the matcher treats any new volume of that series
   as a candidate. **Decided:** a series-wish **stays open** when a volume lands —
   it's a standing want for the whole series, not satisfied by one book. A single
   volume's arrival surfaces a suggestion to the admin but never auto-closes the
   wish; the admin dismisses it manually when the series is complete. (A
   single-volume wish, `series_index` present, closes on that volume as normal.)
   This is the natural bridge to a future `follow`.

---

## 13. Definition of Done checklist

- [ ] `Wish` + `Notification` models, registered in `__init__.py` and test conftest
- [ ] (No Alembic migration — new tables auto-create via `create_all()` in dev+prod)
- [ ] Config vars + `.env.example` + configuration docs
- [ ] `api/wishlist.py`, `api/notifications.py` mounted; schemas
- [ ] `services/wishlist.py`, `services/wish_matcher.py`, email helper
- [ ] Matcher wired into all 4 book-creation sites
- [ ] Frontend: wishlist page, add modal, admin tab, fulfil picker, bell, api libs
- [ ] Audit entries on fulfil/dismiss
- [ ] Backend tests green (`pytest`); frontend `npm run build` clean
- [ ] Docs page + nav + docs.ts link; screenshots in 3 themes
- [ ] CHANGELOG + features.md + CLAUDE.md "What's Built" updated
- [ ] Branch `feat/wishlist`, working code only, committed locally (never pushed)
