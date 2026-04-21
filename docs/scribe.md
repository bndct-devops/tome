# Scribe

Scribe is Tome's Claude Code Skill for batch-ingesting and maintaining metadata on your library. It is meant for admins who already use [Claude Code](https://claude.ai/code) locally — Scribe turns Claude into a conversational ingest/curation tool that talks to a running Tome instance via its HTTP API.

If you are looking for the older filename-based one-shot bulk importer, see [`docs/import.md`](import.md). Scribe is the newer, more capable path: it uses embedded file metadata, dedupes by content hash, fetches external candidates (Hardcover / Google Books / OpenLibrary), falls back to web search for missing descriptions, and unifies titles across a series.

---

## What Scribe does

Four modes, all invoked conversationally inside Claude Code:

| Mode | Trigger | Purpose |
|------|---------|---------|
| Ingest | `/scribe <path>` | Upload a folder of new books, dedupe, fetch metadata, apply |
| Update | `/scribe update <query>` | Refresh metadata on books already in Tome |
| Audit | `/scribe audit [scope]` | Find weak metadata and unify series titles |
| Series | `/scribe series <name>` | Fill series-level metadata (story arcs, publication status) from Claude's knowledge |

Scribe never reads or writes your database directly. Everything goes through Tome's HTTP API, authenticated with a user-level API token.

---

## Prerequisites

- A running Tome instance (local, LAN, or public) you can reach over HTTP
- [Claude Code](https://claude.ai/code) installed locally
- An account on the Tome instance with the roles you want Scribe to act on (admin recommended for full access)

---

## Install

From the root of the Tome repo:

```bash
./skills/scribe/install.sh
```

This symlinks `skills/scribe/` into `~/.claude/skills/scribe`, which is where Claude Code discovers skills. The script is idempotent — safe to re-run after pulling updates.

---

## Create an API token

Scribe authenticates with a Tome API token instead of a username and password. Tokens inherit the role and visibility of the user that created them.

1. Open Tome and go to **Settings → API Tokens**.
2. Click **Create Token**, give it a descriptive name (e.g. `scribe-laptop`), and copy the token. The full token is only shown **once** — save it somewhere safe.
3. The token looks like `tome_<32 random chars>` and can be revoked at any time from the same page.

Admins can also view all users' tokens via the same page for auditing.

---

## Configure a profile

The first time you use Scribe, it will prompt you for:

- A profile name (e.g. `dev`, `prod`, `home-server`)
- The Tome URL (the API server, typically port `8080`)
- The API token you just created

Scribe validates connectivity with `GET /api/health` before writing the config. The config lives at `~/.config/tome/scribe.json` with `0600` permissions:

```json
{
  "profiles": {
    "dev":  {"url": "http://localhost:8080",      "token": "tome_..."},
    "prod": {"url": "https://tome.example.com",   "token": "tome_..."}
  }
}
```

You can register additional profiles any time by saying "add a profile" or "register another instance" to Claude Code.

**Picking a profile per run:** say `"scribe on prod <path>"` or pass `--profile prod`. If you have multiple profiles and do not specify one, Scribe always asks — it never defaults.

---

## Ingest mode

Drop a folder of new books on Scribe:

```
/scribe /Volumes/NAS/ebooks/incoming
```

Or conversationally:

> scribe on prod, import /Volumes/NAS/ebooks/incoming

What happens:

1. **Extract** — `extract.py` recursively scans the folder and pulls embedded metadata from EPUB/CBZ/PDF files, plus filename hints as fallback.
2. **Dedupe** — `POST /api/books/check-hashes` skips files already in Tome by SHA-256 content hash.
3. **Sibling lookup** — for each new book, Scribe queries Tome for existing books in the same series to pin canonical fields (author spelling, series name, book type, library, title template) so imports stay consistent.
4. **Ingest** — `POST /api/books/ingest` uploads each file with its embedded metadata, sets `is_reviewed=true`, and returns the new book id.
5. **Fetch candidates** — `GET /api/books/{id}/fetch-metadata` pulls candidates from Hardcover / Google Books / OpenLibrary for every book. High-confidence picks are applied silently; ambiguous ones are surfaced in a compact review table.
6. **Apply** — chosen candidates are merged via `POST /api/books/{id}/apply-metadata`, with pinned fields preserved so series drift can't sneak in.

Scribe keeps a `.scribe-state.json` in the target folder so a crashed run resumes where it left off.

Terse output by default. Say "show details" or "show #3" for a verbose drill-down on any book.

---

## Update mode

Refresh metadata on books you have already imported. Free-text natural-language queries:

```
/scribe update all tarzan volumes
/scribe update books by edgar rice burroughs
/scribe update book 271
/scribe update descriptions for books in my Classics library
```

Scribe maps the query to `GET /api/books` filters (`series`, `author`, `library_id`, `q`), fetches candidates for each matched book, and shows a field-level diff before asking for approval:

```
#1 Tarzan, Vol. 3  [id=42]
  description:  (empty) → 512c
  year:         null → 1914
  cover:        none → openlibrary.org

42 books — 38 with changes, 4 unchanged. Apply?
```

Options: `accept all`, `skip #N`, `show #N`, `cancel`.

---

## Audit mode

Scan for books with weak metadata or inconsistent series titles:

```
/scribe audit
/scribe audit the Classics library
/scribe audit books by dickens
```

A book is **weak** if it's missing a description, year, or cover, or if its title drifts from the rest of its series. Scribe reports the scan before spending any external API calls:

```
347 books scanned
  · 42 missing description
  · 18 missing year
  · 31 no cover
  · 6 series title drift
  68 unique weak books (some weak in multiple fields)

Series title drift detected:
  "Tarzan"          [Case A] — 10 books use "Tarzan, Vol. N"; 1 uses "Tarzan vN"
  "Sherlock Holmes" [Case A] — 8 books use "Sherlock Holmes Vol. N"; 2 use "Sherlock Holmes v N"
  "Barsoom"         [Case B] — 3 inconsistent patterns; suggested canonical: "Barsoom, Vol. N"

Proceed with candidate fetch on 68 weak books?
```

For missing descriptions that the external metadata sources also don't have, Scribe falls back to a `WebSearch` + `WebFetch` against trusted sources (Goodreads, Wikipedia, publisher pages) and proposes the extracted synopsis.

Title drift is **never** auto-applied — Scribe always surfaces outliers for explicit confirmation, because titles are the most user-visible field.

---

## Series mode

Fill series-level metadata — publication status and story arcs — from Claude's own knowledge of the work:

```
/scribe series Berserk
/scribe series "A Certain Magical Index"
```

Scribe reads the current series state, asks Claude for a proposal, and renders a diff you can accept or edit row-by-row before anything is written:

```
Series: Berserk
Status:   hiatus → ongoing

Arcs (new):
  #1  Black Swordsman                  Vol. 1–2    Guts, marked by a demonic brand, hunts apostles of the Godhand.
  #2  Golden Age                       Vol. 3–13   Flashback to Guts' time in the Band of the Hawk.
  #3  Lost Children                    Vol. 14     A haunted forest, elfin creatures, and a twisted faerie threat.
  ...

Apply? ("yes"/"apply", row-level correction, or "no"/"abort")
```

Row-level corrections are natural language — `change arc 3 end to 20`, `drop arc 5`, `rename #2 to Lost Children Arc`. Chain them until the proposal looks right, then apply.

If Claude doesn't have confident knowledge of the series, Scribe refuses rather than guessing. Writes require an admin token; arcs can also be edited manually via the Manage modal on the series detail page.

---

## Output discipline

Scribe is tuned for terse, high-signal output. A 200-book import fits on one screen. If you want detail on a specific book or decision, ask:

- `show #3` — print the full candidate list for book 3
- `verbose` — enable verbose mode for the rest of the run
- `show skipped` / `list errors` — dig into what was left out at the end

---

## Standalone fallback

If you cannot use Claude Code and still want a batch ingest tool, the original [`tome-scribe` CLI](https://github.com/bndct-devops/tome-scribe) repository exists as a local-LLM fallback. It is no longer actively developed — the Claude Code Skill is the recommended path.
