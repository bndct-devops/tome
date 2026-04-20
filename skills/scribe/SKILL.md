---
name: scribe
description: Batch-import ebooks into a Tome library via its HTTP API. Triggers on /scribe, "import books into tome", "import this folder", "batch-ingest".
trigger: /scribe
---

# Scribe — Tome Batch Import Skill

Import a folder of ebooks into a running Tome instance.  Claude reads this
file and executes the workflow step-by-step.  The user never has to write
curl commands or parse JSON manually.

## Output discipline

Default mode is TERSE.  Never echo full metadata in the main flow.  Never
print per-file decision reasoning.  Print counts and ambiguous cases only.
Verbose output only when the user explicitly asks ("show details", "verbose",
"what did you pick for #3").  Judgment decisions are written to
`.scribe-state.json` in the target directory, not to chat.

Reason: output tokens cost ~5x input tokens.  A 200-book import should fit
in a screen.

---

## Profiles

A **profile** is a named connection to a Tome instance — its URL and API token.
All profiles live in `~/.config/tome/scribe.json` under the `profiles` key.

**Config shape:**
```json
{
  "profiles": {
    "dev":  {"url": "http://localhost:8080",    "token": "tome_..."},
    "prod": {"url": "https://tome.example.com", "token": "tome_..."}
  }
}
```

**How to add a profile:** say "add a profile", "add prod", or "register another instance".
Scribe will prompt for the name, URL, and token, validate connectivity, then merge
the new entry into `profiles` without touching existing ones.

**How to select a profile in a command:**
- Explicit: `"scribe on prod"`, `"use dev"`, `"/scribe --profile prod <path>"`,
  `"on prod, audit the manga library"` — Claude extracts the name and uses it.
- Implicit (single profile): if `profiles` has exactly one entry, use it silently.
- Unspecified (multiple profiles): Claude **always asks** — no default, no guessing.
  Example: `"Which instance? You have: dev, prod."` Wait for the answer before continuing.

**Config file location:** `~/.config/tome/scribe.json` (permissions: 600).

For full setup details and URL normalization rules, see Step 0 below.

---

## Step 0 — Config / first-run setup

Read `~/.config/tome/scribe.json`.

### 0a — Legacy migration (flat config)

If the file exists and has the old flat shape `{"url": "...", "token": "..."}` (no
`profiles` key), prompt the user once:

> "Found a single-profile config. What should I name it (e.g. 'dev', 'prod')?"

After they reply, rewrite the file as:
```json
{"profiles": {"<name>": {"url": "<existing url>", "token": "<existing token>"}}}
```
Preserve the existing `url` and `token` values exactly — do NOT re-validate or
re-prompt for them.  Then proceed normally with the named profile.

### 0b — Empty / missing file (first run)

If the file does not exist or is empty:

1. Tell the user: "Scribe needs a Tome instance name, URL, and an API token.
   Go to Tome → Settings → API Tokens, create one named e.g. `scribe-laptop`,
   and copy the token."
2. Ask in a single message: "Instance name (e.g. 'dev', 'prod')?" and
   "Tome URL (e.g. http://localhost:8080)?" and "API token?"
3. Once they reply, **normalize the URL before saving**:
   - Strip any path segments after the host:port — e.g.
     `http://localhost:8080/settings` → `http://localhost:8080`
   - If the port is `5173` (the Vite frontend dev server), swap it to `8080`
     (where Tome's API runs) and warn the user:
     "I'll use :8080 since that's where Tome's API runs."
   - Examples:
     - `http://localhost:5173/settings` → `http://localhost:8080`
     - `http://192.168.1.10:8080/books` → `http://192.168.1.10:8080`
     - `https://tome.example.com/settings` → `https://tome.example.com`
4. Validate connectivity **before writing the config**:

```bash
curl -sf -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer <TOKEN>" "<NORMALIZED_URL>/api/health"
```

   - If the response is 200, write the file:

```bash
mkdir -p ~/.config/tome
cat > ~/.config/tome/scribe.json <<'EOF'
{"profiles": {"<NAME>": {"url": "<NORMALIZED_URL>", "token": "<TOKEN>"}}}
EOF
chmod 600 ~/.config/tome/scribe.json
```

   - If not 200, tell the user: "Could not reach Tome at <URL> — got HTTP
     <status>.  Please check the URL and token, then try again."  Do not save
     the config.  Ask the user to fix and retry.

### 0c — Adding a profile

If the user says "add a profile", "add prod", "register another instance", or similar:

1. Prompt: "Instance name?" and "Tome URL?" and "API token?"
2. Normalize the URL using the same rules as 0b.
3. Validate connectivity via `GET /api/health` as above.
4. On success, read the current `~/.config/tome/scribe.json`, merge the new profile
   into the `profiles` object, and write the file back.  Do not touch existing profiles.

### 0d — Profile selection

Before ANY action that hits Tome, resolve which profile to use:

- **Explicit in the command:** `"scribe on prod"`, `"use dev"`,
  `"/scribe --profile prod <path>"`, `"on prod, audit the manga library"` —
  extract the profile name and use it.  If that name does not exist in `profiles`,
  tell the user and list available names.
- **Single profile exists:** use it silently.  No prompt needed.
- **Multiple profiles, none specified:** **always ask. Never default. Never guess.**
  Prompt: `"Which instance? You have: dev, prod."` Wait for the user's reply
  before proceeding with any API call.

Once resolved, bind shell variables for the rest of the run:
```bash
PROFILE="<name>"
URL="<profiles[PROFILE].url>"
TOKEN="<profiles[PROFILE].token>"
```

All subsequent steps use `$URL` and `$TOKEN` sourced from the selected profile.

---

## Step 1 — Discover

Run the extract script against the target path.  The script is at
`skills/scribe/scripts/extract.py` relative to the Tome repo root.  Resolve
the repo root as the directory containing this SKILL.md's parent `skills/`
folder.

### Calling extract.py — argument rules

**Never call extract.py with a single file path when the user intended a
batch.**  The script recurses directories; calling it on a single file gives
"Found 1 files" and loses the rest of the batch.

- **User passes a directory** → call `extract.py <dir>` — it recurses.
- **User passes individual files or multiple files from the same parent** →
  call `extract.py <common_parent_dir>` once, then filter the resulting JSON
  array to only the entries whose `path` appears in the user's list.
- **Mixed paths from different parents** → find the deepest common ancestor
  directory, call `extract.py <common_ancestor>`, then filter to the user's
  list.

```bash
EXTRACT="<abs-path-to-repo>/skills/scribe/scripts/extract.py"
python3 "$EXTRACT" "<target_path>" > /tmp/scribe_files.json
```

Parse the JSON array.  Each item has: `path`, `format`, `size_bytes`,
`content_hash`, `embedded` (dict of title/author/series/series_index/year/
isbn/language), `filename_hints` (same fields parsed from filename).

After filtering to the user's intended files (if applicable), write the
filtered list back to `/tmp/scribe_files.json`.

Print: `"Found N files."` — nothing more.

---

## Step 2 — Resume check

If `<target_path>/.scribe-state.json` exists, read it.

Check that the `profile` field inside the state JSON matches the currently
active `$PROFILE`.  If it does not match, warn:

> "This state file was created against profile '<old_profile>', but you are
> currently using '<current_profile>'.  Resuming may apply changes to the
> wrong Tome instance.  Continue anyway? (yes / start fresh)"

If the profile matches (or the user confirms), ask:
`"Found a previous run with X files processed. Resume? (yes / start fresh)"`

If resuming: load state and skip to Step 5 (upload), processing only entries
whose `status` is not `"uploaded"`.

If starting fresh or no state file: proceed.

---

## Step 3 — Deduplicate

Extract all `content_hash` values from the discovered files and POST to
check-hashes:

```bash
HASHES=$(python3 -c "
import json, sys
files = json.load(open('/tmp/scribe_files.json'))
print(json.dumps({'hashes': [f['content_hash'] for f in files]}))
")
curl -sf -X POST "$URL/api/books/check-hashes" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$HASHES" > /tmp/scribe_existing.json
```

Remove files whose `content_hash` appears in `existing`.  Print:
`"Skipped N duplicates."` (or omit the line if 0).

---

## Step 4 — Fetch libraries and book types

```bash
curl -sf -H "Authorization: Bearer $TOKEN" "$URL/api/libraries" \
  > /tmp/scribe_libraries.json
curl -sf -H "Authorization: Bearer $TOKEN" "$URL/api/book-types" \
  > /tmp/scribe_book_types.json
```

Parse both.  You will use them for library assignment in Step 4c.

---

## Step 4a — Ingest with embedded metadata (batch)

For each non-duplicate file, upload it immediately using the `embedded`
metadata from the extract step.  Do NOT wait for metadata fetching first —
ingest creates the book record so we can call `fetch-metadata` against its id.

Build the metadata JSON from `embedded` fields.  Apply filename_hints as
fallback for missing fields.  Always include `title` (required).

```bash
# For each file:
METADATA=$(python3 -c "
import json
embedded = <embedded dict>
hints = <filename_hints dict>
# Merge: embedded wins over hints
merged = {**hints, **{k: v for k, v in embedded.items() if v is not None}}
# Keep only ingest-schema fields
fields = ['title','subtitle','author','series','series_index','isbn',
          'publisher','description','language','year','tags',
          'library_ids','book_type_id']
out = {k: merged[k] for k in fields if k in merged}
print(json.dumps(out))
")
curl -sf -X POST "$URL/api/books/ingest" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@<abs_path>" \
  -F "metadata=$METADATA" \
  -w "\n%{http_code}" \
  > /tmp/scribe_upload_<i>.json
```

On HTTP 201: record `book_id` from response.
On HTTP 409: the file is a duplicate (race condition with Step 3). Note as
  skipped.
On other errors: log the error, mark file as failed, continue.

Save all results to `.scribe-state.json` in the target dir after each file
(so a crash is resumable).

State file schema:
```json
{
  "profile": "<active profile name>",
  "target_path": "/abs/path",
  "run_at": "ISO timestamp",
  "files": [
    {
      "path": "/abs/path/to/file.epub",
      "content_hash": "abc...",
      "status": "pending|uploaded|skipped|error",
      "book_id": 42,
      "error": null,
      "confidence": "auto|ambiguous|no_match|null",
      "chosen_candidate_index": null,
      "metadata_applied": false,
      "pinned_fields": null
    }
  ]
}
```

Note: the `profile` field at the top level is used by Step 2's resume check
to detect cross-profile resume attempts.

After the full batch, print one terse line:
`"Uploaded N. Skipped K (duplicates/errors). Errors: [list if any]"`

---

## Step 4b — Context enrichment — check Tome for siblings

**Run this before the pre-classification.**  For each uploaded file in the
batch, look up existing books in Tome that share the same series or author.
Record any matches as `pinned_fields` in the state file.

Sibling-lookup is context enrichment only.  Finding siblings does NOT skip
fetch-metadata.  It only pins canonical fields (author, series spelling,
book_type, library assignment, title_template) so the candidate scoring and
apply step can use them to improve accuracy and prevent series drift.

No chat output unless the user asks.

For each file:

1. Derive a `series` guess from `embedded.series` or `filename_hints.series`.
2. If `series` is non-empty, query Tome:

```bash
curl -sf -H "Authorization: Bearer $TOKEN" \
  "$URL/api/books?series=<url-encoded-series-name>" \
  > /tmp/scribe_siblings_<i>.json
```

   The `series` query param is an exact-match filter (confirmed against
   `backend/api/books.py`).  URL-encode the series name.

3. If results exist (at least one book), extract `pinned_fields` from the
   first (lowest `series_index`) sibling:
   - `author` — exact string from the sibling book
   - `series` — exact canonical spelling/casing
   - `book_type_id` — from the sibling book
   - `library_ids` — from the sibling book
   - `title_template` — inspect the sibling titles and detect the pattern:
     - If siblings are titled "Sherlock Holmes, Vol. 1", "Sherlock Holmes, Vol. 2" →
       template is `"Sherlock Holmes, Vol. <index>"`
     - If siblings use inconsistent formats (i.e. would be Case B under the
       drift detection rule in Step A2), omit `title_template` — do not guess
       a canonical form here.  The audit mode's drift detection step will surface
       the inconsistency properly.
     - Only set `title_template` if at least 2 siblings exist and they agree

4. If no series, fall back to `author` lookup:

```bash
curl -sf -H "Authorization: Bearer $TOKEN" \
  "$URL/api/books?author=<url-encoded-author>" \
  > /tmp/scribe_siblings_<i>.json
```

   Extract `author`, `book_type_id`, `library_ids` from the first result.
   Do **not** set `title_template` for author-only lookups.

5. If no siblings at all, `pinned_fields` is `null` — normal flow continues.

Write `pinned_fields` into each file's state entry.  Do not print anything.

---

## Step 4c — Pre-classify metadata quality (silent)

Before calling any external API, inspect each ingested book's metadata
(already captured from the extract step) and classify it silently as
`confidence: "auto"` or `confidence: "ambiguous"`.  Persist the classification
to `.scribe-state.json`.  Print nothing.

This classification only decides **how candidates are presented to the user
after fetch**.  It does NOT skip fetching.

**Auto — top candidate is applied silently after fetch:**
- Title is present, non-empty, and does not look like a filename artifact
  (e.g. "chapter-03", "vol_12", a bare number, "Book", "Untitled").
- Author is present and non-empty.
- At least one of `{year, isbn, publisher}` is present.
- Filename hints do not contradict embedded metadata egregiously (e.g.
  filename says "Sherlock Holmes vol 3" but embedded title is "Around the World in Eighty Days" →
  not auto).

**Ambiguous — surface candidates to the user after fetch:**
- Title or author missing, empty, or obviously garbage.
- All publication fields (`year`, `isbn`, `publisher`) are absent.
- Embedded metadata vs. filename hints disagree on title or series.
- Title looks auto-generated ("Book", "Untitled", a bare number, or a
  slug-style string like "chapter-03").

Add `confidence` and `metadata_applied` fields to each file's state entry.
Do NOT print per-file decisions.

---

## Step 4d — Fetch metadata candidates (ALL books)

**Fetch-metadata runs for every ingested book without exception.**  Even if
sibling-lookup found matches, or the pre-classification is `auto`, every book
still needs a candidate fetch — because description, cover, year, publisher,
tags, and ISBN are per-book fields that siblings cannot supply.

For each uploaded book, call:

```bash
curl -sf -H "Authorization: Bearer $TOKEN" \
  "$URL/api/books/<book_id>/fetch-metadata" \
  > /tmp/scribe_candidates_<book_id>.json
```

**Important:** `GET /api/books/{book_id}/fetch-metadata` requires an existing
book_id. This is why ingest comes before metadata fetch — the book must exist
first.  You can optionally pass `?q=<title+author>` to override the search
query if embedded metadata is weak.

Run all curl calls concurrently (via `&` + `wait` in bash, or sequentially if
count <= 10 to avoid hammering external APIs).

Do NOT print anything per-book.

After receiving candidates, score and classify each result using the
**candidate scoring heuristic** below.  Pick the highest-scoring candidate as
the top-ranked choice; if multiple candidates tie or none pass the threshold,
keep the `ambiguous` classification from Step 4c.

Classification rules (applied after scoring, may override Step 4c):
- `auto`: top candidate scores >= 5 AND no other candidate is within 2 points
  of it — apply silently without user interaction.
- `ambiguous`: candidates exist but none pass the `auto` threshold, OR multiple
  pass with very similar scores — surface to user for manual resolution.
- `no_match`: zero candidates returned — keep embedded/pinned fields as-is.

Write classification + chosen candidate index (best score for `auto`;
`null` for `ambiguous` and `no_match`) to the state file.

Do NOT print per-file decisions.

---

## Candidate scoring heuristic

Apply this to every candidate for a given book.  Sum the points; higher is
better.  Use this consistently — do not invent a different scorer at runtime.

| Condition | Points |
|-----------|--------|
| `candidate.author` matches `pinned_fields.author` (case-insensitive) | +5 |
| `candidate.author` matches filename-hint author (case-insensitive) | +5 |
| `candidate.series_index` equals parsed filename/embedded volume number | +4 |
| `candidate.title` contains the series name (case-insensitive) | +3 |
| `candidate.year` is within ±1 of embedded/filename year | +2 |
| `candidate.author` disagrees with any pinned field | -3 |
| `candidate.series` disagrees with `pinned_fields.series` (both present, clearly different) | -3 |

**Tie-break:** prefer source order **Hardcover > Google Books > Open Library**.
This matches the backend's actual fetch order in `backend/services/metadata_fetch.py`
(Hardcover results are prepended first, then Google Books, then Open Library).

---

## Web fallback for missing descriptions

This subsection applies whenever a book's best metadata candidate has an
empty, null, or short description.  It is used in ingest (after Step 4d),
update (after Step U4), and audit (after Step A4) — wherever descriptions are
being enriched.

**Trigger conditions (check both):**

1. The top-ranked candidate's `description` field is null, empty string, or
   shorter than 200 characters.
2. The field being filled is `description` only.  Do NOT use web search to
   override author, series, year, cover, or other fields — those come from
   fetch-metadata candidates, sibling pinning, or user input.

**Procedure:**

1. Build a search query: `"<title>" <author> book plot summary`
   - Quote the title (wrap in `"`) if it has more than two words.
   - Include the author if known.
   - Examples:
     - `"Barsoom" Edgar Rice Burroughs book plot summary`
     - `"Around the World in Eighty Days" Jules Verne book plot summary`

2. Run `WebSearch` with that query.

3. Pick the **top trustworthy result** in priority order:
   - Goodreads
   - Wikipedia
   - Official publisher page (VIZ, Dark Horse, Yen Press, Tor, Del Rey, etc.)
   - Author's official site
   - Skip: blogs, fan wikis, obvious SEO spam, "top 10 books" listicles,
     retailer product pages (Amazon, Barnes & Noble) — these often have
     truncated or marketing-copy descriptions.

4. Run `WebFetch` on the chosen URL.  Extract only the synopsis /
   description / plot-summary section.  Apply these constraints:
   - Minimum useful length: 100 characters (below this, discard and fall back).
   - Maximum to apply: 2000 characters.  Trim at the nearest sentence boundary.
   - Strip: star ratings, review quotes, "also by the author" sections,
     metadata sidebars, "buy now" CTAs, chapter lists, spoiler sections.

5. Verify the fetched text is for the **correct book** — check that title and
   author are mentioned or clearly implied.  If the page is for a different
   edition, different volume, or a different book entirely, discard it.

6. If the web result is still empty, too short, or clearly wrong after steps
   3-5, fall back to the original candidate's description even if it is short,
   or leave the field empty.  Log a note in the state file (`"web_fallback":
   "failed — no good result"`).  Do not keep retrying with more URLs.

**Source attribution in reports:**

When a description is sourced from a web fallback, note the source in the
user-facing diff / apply report.  Examples:

```
#1 A Princess of Mars  [id=10]
  description:  (empty) → 489c  [from Wikipedia]
```

Do **not** embed attribution text inside the description field itself — keep
the stored description clean.

**State file field:**

Add `"web_fallback_source": "<url or null>"` to the per-book state entry when
a web fallback is used.  This allows the run to be audited later.

---

## Step 4e — Library assignment

Default rule: assign each book to the library associated with its book_type
(the `library_id` field on the BookType).  If book_type_id was not set (no
embedded metadata for it), assign to no library for now.

If `pinned_fields.library_ids` exists for a book, use those library IDs
instead of the default rule.

If 5 or more books share the same series or author and have no book_type_id,
ask once: `"N books look like <series/author> — which book type? (options:
<list slugs from /api/book-types>)"`.  Apply that type to all of them via
`PUT /api/books/<id>` with `{"book_type_id": <id>}`.

Never ask per-book questions about library assignment.

---

## Step 5 — Report ambiguous cases

After fetch and scoring, print a terse summary:

```
Uploaded 42  |  auto-applied 38  |  needs review 3  |  no candidates 1  |  skipped 2 dups
```

Then render a compact table of ambiguous books only:

```
#  Title (embedded)            Candidates
1  Some Book Name              [A] "Some Book" 2019 Penguin  [B] "Some Book Name" 2021 Tor  [C] "Some Book" 2018 (no publisher)
2  Another Title               [A] "Another Title" by J. Smith 2020  [B] "Another" by J. Smith 2019
3  Unknown Title               (no candidates)
```

Then prompt: `"How should I handle these? Options: 'accept all A', '#2 use B',
'#3 skip metadata', 'skip all', or 'show me #1's full candidates'."`

---

## Step 6 — User resolves ambiguous

Parse the user's reply.  Examples:

- `"accept all A"` → use candidate index 0 for all ambiguous books
- `"accept all"` → use best-scored candidate for each
- `"#2 is B"` → use candidate index 1 for book #2
- `"#3 skip"` or `"skip #3"` → leave book #3 with embedded metadata only
- `"show #1"` → print all candidate fields for book #1 (verbose drill-down)
- `"skip all"` → leave all ambiguous with embedded metadata

After resolution, apply chosen candidates via:

```bash
curl -sf -X POST "$URL/api/books/<book_id>/apply-metadata" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '<ApplyMetadataRequest JSON>'
```

### Respecting pinned fields at apply time

`POST /api/books/{id}/apply-metadata` supports partial payloads — only
provided fields are updated.  Use this to protect pinned values:

1. Start from the chosen candidate's fields.
2. **Overwrite** any key that exists in `pinned_fields` back to the pinned
   value — never let a candidate's value replace a pinned field.
3. If `pinned_fields.title_template` is set, regenerate the title:
   - Find the volume/series_index for this book (from candidate, embedded, or
     filename hint — in that priority order).
   - Substitute into the template, e.g. template `"Sherlock Holmes, Vol. <index>"` +
     index `3` → title `"Sherlock Holmes, Vol. 3"`.
4. Build the final payload from the modified fields and POST it.

Rationale: this prevents series drift.  If `fetch-metadata` returns
`"Doyle Arthur Conan"` but all existing siblings use `"Arthur Conan Doyle"`, we keep
the consistent spelling.

For `auto` books, apply the chosen candidate automatically (no user
interaction), respecting pinned fields as above.

For `no_match`, apply nothing (embedded stays).

---

## Step 7 — Final summary

Print one terse line:

```
Done. Uploaded 42 books, applied metadata on 38, skipped 3 (1 duplicate, 2 errors).
See $URL/books for review.
```

Offer: `"Type 'list errors' for details or 'show skipped' to see what was
left out."`

Clean up `/tmp/scribe_*.json` temp files.

---

## Notes on API shape (verified against Tome source)

- `POST /api/books/check-hashes` — body `{"hashes": [...]}`, response
  `{"existing": {"<hash>": <book_id>}}`.  Auth: Bearer token.

- `POST /api/books/ingest` — multipart form: `file` (binary) + `metadata`
  (JSON string).  Returns 201 + BookDetailOut on success, 409 +
  `{"detail": {"detail": "duplicate", "existing_id": N}}` on dup.
  Sets `is_reviewed=true` automatically.

- `GET /api/books?series=<exact-name>` — exact-match series filter.  Returns
  paginated book list.  Also supports `?author=<name>` for author filtering.
  Both are simple string equality checks in the backend (not substring/fuzzy).

- `GET /api/books/{book_id}/fetch-metadata?q=<optional override>` — returns
  `list[MetadataCandidateOut]`.  Each candidate: `source`, `source_id`,
  `title`, `author`, `description`, `cover_url`, `publisher`, `year`,
  `page_count`, `isbn`, `language`, `tags`, `series`, `series_index`.

- `POST /api/books/{book_id}/apply-metadata` — body: any subset of
  `title`, `author`, `description`, `publisher`, `year`, `language`, `isbn`,
  `tags`, `series`, `series_index`, `cover_url`.  Only provided fields are
  updated (partial payload supported).

- `GET /api/libraries` — returns list of `{id, name, icon, is_public, ...}`.

- `GET /api/book-types` — returns list of `{id, slug, label, library_id, ...}`.

- `PUT /api/books/{book_id}` — body: any BookUpdate fields.  Use for
  post-upload corrections.

- Auth header for all requests: `Authorization: Bearer <token>` where token
  was created in Tome → Settings → API Tokens.

**Mismatch with original spec:** The spec described `POST /api/books/fetch-metadata`
(standalone, no book_id).  That endpoint does not exist.  The actual endpoint
is `GET /api/books/{book_id}/fetch-metadata`, so books must be ingested before
metadata can be fetched.  Scribe's workflow reflects this: ingest first, then
fetch metadata against the created book_id.

---

## Update mode — `/scribe update <query>`

Refresh metadata on books that already exist in Tome.  Works on any subset of
the library: a single book, a whole series, all books of a given type, etc.

### Trigger forms

- "check metadata for tarzan"
- "check metadata for all burroughs books"
- "can you quickly check all novel volumes"
- "update descriptions for books in my Classics library"
- "refresh metadata for book 271"

The free-text query is natural language.  Parse the intent, do not require the
user to know API param names.

Profile selection (Step 0d) applies before any API call is made.

---

### Step U1 — Interpret the query

Map natural language to `GET /api/books` filter params.  Verified param names
(from `backend/api/books.py`):

| Phrase | Param used |
|--------|-----------|
| "X series" / "all X books" | `series=X` (exact match) |
| "books by X" / "X books" (no series context) | `author=X` |
| "book 271" / "book id 271" | skip GET — fetch `GET /api/books/271` directly |
| free text (title keyword) | `q=<text>` |
| "books in library Y" | resolve `library_id` via `GET /api/libraries`, then `library_id=<id>` |
| "manga" / "comics" / "novels" | **no `book_type_id` filter exists on `GET /api/books`** — instead use `q=<type label>` or look up the book type's associated `library_id` via `GET /api/book-types` and filter by that |
| "added by X" | `added_by=<user_id>` (admin only) |

All `GET /api/books` calls use `$URL` and `$TOKEN` from the selected profile.

**Fallback chain for ambiguous series/author queries:**

1. Try `series=<query>` — if results come back, use them.
2. If no results, try `author=<query>`.
3. If still nothing, try `q=<query>` (full-text).
4. If still nothing, ask the user once for clarification.

**Type-label resolution:** `GET /api/book-types` returns `{id, slug, label, library_id}`.
If the user says "manga volumes" or "light novels", find the matching book type
by `slug` or `label`, then use its `library_id` as the `library_id` filter param.
This is the correct workaround since `book_type_id` is not a filter param on
`GET /api/books`.

If the query is genuinely ambiguous after exhausting the above (e.g. "burroughs"
could be a series name or an author name), ask once before querying.

---

### Step U2 — Confirm scope

After the GET, print a terse summary and wait for confirmation if more than
3 books matched:

```
Found 24 books in series 'Tarzan' by Edgar Rice Burroughs. Fetch candidates and review updates?
```

If 1-3 books matched, skip the confirmation and proceed directly.

---

### Step U3 — Sibling context (series queries only)

If the query targeted a specific series (i.e. `series=<name>` was used), apply
the same sibling-pinning logic as Step 4b of the ingest workflow:

- Extract `author`, `series`, `book_type_id`, `library_ids`, and
  `title_template` from the existing books in the result set (lowest
  `series_index` first).
- Use these as `pinned_fields` when scoring and applying candidates.

For heterogeneous queries (full-text, author-only, library-wide), skip sibling
pinning — books are processed individually without cross-book constraints.

---

### Step U4 — Per-book fetch and score

After fetching candidates, apply the **web fallback for missing descriptions**
(defined above in the ingest section) for any book whose top candidate has a
null, empty, or sub-200-character description.

For each matched book, call:

```bash
curl -sf -H "Authorization: Bearer $TOKEN" \
  "$URL/api/books/<book_id>/fetch-metadata" \
  > /tmp/scribe_update_<book_id>.json
```

Score candidates using the **candidate scoring heuristic** (defined above in
the ingest workflow — do not redefine it here, use exactly the same table).
Apply pinned fields from Step U3 where available.

Write a state file at `~/.cache/tome-scribe/.scribe-update-<profile>-<timestamp>.json`
(create `~/.cache/tome-scribe/` if missing) after each book fetch so the run
is resumable on Ctrl-C.  Including `<profile>` in the filename ensures that a
resume attempt only picks up state files for the currently-active profile —
files for other profiles are ignored.

State file schema (update variant):

```json
{
  "mode": "update",
  "profile": "<active profile name>",
  "query": "<original user query>",
  "run_at": "ISO timestamp",
  "books": [
    {
      "book_id": 42,
      "title": "Tarzan, Vol. 1",
      "status": "pending|fetched|applied|skipped|error",
      "chosen_candidate_index": null,
      "pinned_fields": null,
      "diff": null,
      "error": null
    }
  ]
}
```

**Resume:** on start, check `~/.cache/tome-scribe/` for any
`.scribe-update-<profile>-*.json` file (matching the current active profile)
whose `status` contains at least one `"pending"` or `"fetched"` entry.
If found, ask:
`"Found an in-progress update run from <timestamp>. Resume? (yes / start fresh)"`
Ignore state files belonging to other profiles.

---

### Step U5 — Diff preview

For each book, compute a field-level diff between the **current book state**
and the top-ranked candidate (with pinned overrides applied).  Omit fields
that are unchanged.

Present diffs in a compact block — all books together, one entry per book:

```
#1 Tarzan, Vol. 3  [id=42]
  description:  (empty) → 512c
  year:         null → 1914
  cover:        none → openlibrary.org

#2 Tarzan, Vol. 7  [id=55]
  title:        "Tarzan v7" → "Tarzan, Vol. 7"   [pinned template]
  description:  (empty) → 488c
  tags:          [] → ["adventure", "classic"]
```

Rules for compact display:
- `description`: show char count of new value (e.g. `488c`), not the full text.
- `cover`: show only the domain of the URL (e.g. `openlibrary.org`, `books.google.com`), not the full URL.
- `title`: if the change is driven by a pinned template, append `[pinned template]`.
- If a book has no changes (candidate matches current state), show
  `#N <Title>  [id=X]  — no changes` and exclude it from the apply step.

Then prompt:

```
42 books — 38 with changes, 4 unchanged. Apply? Options:
  "accept all" — apply every diff
  "skip #3" — exclude that book, apply the rest
  "show #5" — verbose drill-down on book #5's full candidate list
  "cancel" — discard all without applying
```

---

### Step U6 — Apply

Parse the user's response:

- `"accept all"` → apply every diff that has changes
- `"skip #N"` → exclude book N, apply the rest
- `"show #N"` → print all candidate fields for book N (title, author, year,
  publisher, description snippet, source, cover URL); do not apply yet; re-prompt
- `"cancel"` → discard without applying; delete the state file

For each book to apply, use `POST /api/books/{id}/apply-metadata` (with
`$URL` and `$TOKEN` from the selected profile) with the merged payload
(candidate fields overwritten by pinned fields, same logic as Step 6 /
"Respecting pinned fields at apply time" in the ingest workflow).

Print terse progress while applying: `"Applied 1/38... 2/38..."`.

Final report: `"Updated 38. Skipped 4. Errors 0."`

Delete the state file on clean completion.

---

## Audit mode — `/scribe audit [scope]`

Scan the library for books with weak metadata, fetch candidates for the weak
ones, and apply fixes with user approval.

### Trigger forms

- `/scribe audit` — all books (null/missing fields + series drift)
- `/scribe audit years [scope]` — publication-year drift check (see "Year-drift audit" subsection below)
- "audit metadata" / "find books with missing metadata"
- "audit the manga library" / "audit books in Light Novels"
- "audit books by X"
- "audit years", "check publication years", "verify years for tarzan"

Narrow scope is parsed with the same natural-language interpretation as
Update mode Step U1.

Profile selection (Step 0d) applies before any API call is made.

---

### Weak-metadata criteria

A book is **weak** if any of the following are true:

| Field | Weak condition |
|-------|----------------|
| `description` | null or empty string |
| `year` | null |
| `cover_path` | null or empty string |
| Series title drift | within a series, titles are inconsistent across books (see drift detection rule below) |
| Year drift | stored `year` differs from Open Library's `first_publish_year` by more than 3 years (requires fetch — see Year-drift audit below) |

Do **not** flag missing `isbn`, `publisher`, or `subtitle` — these are
legitimately absent on many books.

---

### Year-drift audit (trigger: `/scribe audit years [scope]`)

**Motivation:** embedded/ingested metadata often has the *edition* or *reprint*
year, not the *first publication* year.  For a classic like Tarzan, stored
year might be `2008` (Penguin reprint) when the book was actually first
published in `1914`.  This matters if we later embed Tome metadata into the
files on disk — we want the real year baked in.

**Trigger forms:**
- `/scribe audit years` — check every book
- `/scribe audit years <scope>` — narrow scope, same natural-language parsing
  as Update mode Step U1 (series, author, library, free-text)
- "audit years", "check years for tarzan", "verify publication years in
  Classics library"

**How year trust works per source:**

| Source | Year field meaning | Trust for first-pub? |
|--------|--------------------|----------------------|
| Open Library | `first_publish_year` (already parsed as `year` in the candidate dataclass) | **Authoritative** — use for drift detection |
| Hardcover | `release_year` (edition, not first-pub) | Not reliable — ignore for drift |
| Google Books | edition year parsed from `publishedDate` | Not reliable — ignore for drift |

**Detection is fetch-based, not scan-based.**  Unlike null/missing fields,
year drift cannot be detected from the DB alone — it requires comparing
stored values against external sources.  So year-drift audit always runs
`fetch-metadata` on every book in scope, regardless of whether other weak
criteria apply.

**Procedure:**

1. Resolve scope via the same natural-language parsing as Update mode Step U1.
   Confirm with the user before fetching if scope has more than ~20 books
   (time estimate: `ceil(count / 10)` minutes).

2. For each book in scope, call `GET /api/books/<book_id>/fetch-metadata`
   concurrently (batches of 10).

3. For each response, find the **first Open Library candidate** in the list
   (identified by `source == "open_library"`).  If no OL candidate exists,
   mark the book `no_ol_candidate` and move on — do not use Hardcover or
   Google year for drift.

4. Compare `stored_year` against `ol_candidate.year`:
   - Both present and `abs(stored - ol) > 3` → **drift**
   - Stored missing and OL present → **missing year** (normal weak case, not
     drift — handled by the regular audit flow)
   - OL missing → `no_signal`, skip

5. Write a state file at `~/.cache/tome-scribe/.scribe-audit-years-<profile>-<timestamp>.json`
   (same directory convention as other audit/update state files).  Persist
   after each fetch so the run is resumable.  Schema:

```json
{
  "mode": "audit-years",
  "profile": "<active profile name>",
  "scope": "<description>",
  "run_at": "ISO timestamp",
  "books": [
    {
      "book_id": 42,
      "title": "Tarzan, Vol. 1",
      "stored_year": 2008,
      "ol_year": 1912,
      "delta": 96,
      "status": "pending|drift|match|no_ol_candidate|no_signal|applied|skipped"
    }
  ]
}
```

6. Present drift cases in a compact block, grouped by magnitude:

```
34 books scanned · 8 drift · 22 match · 3 no OL candidate · 1 no signal

Year drift (OL first_publish_year vs. stored):
  #id=42  "Tarzan, Vol. 1"       2008 → 1912   (Δ 96y)
  #id=55  "A Princess of Mars"   2011 → 1912   (Δ 99y)
  #id=71  "The Gods of Mars"     2010 → 1913   (Δ 97y)
  #id=88  "Sherlock Holmes v3"   1995 → 1892   (Δ 103y)
  ...

Apply? Options:
  "accept all"       — update all drift cases
  "skip #N"          — exclude a specific book
  "show #N"          — verbose: print all OL candidate fields
  "threshold 10"     — re-filter to only drift > 10 years (drop noise)
  "cancel"           — discard without applying
```

7. On `accept all` or selective apply, use `POST /api/books/{id}/apply-metadata`
   with `{"year": <ol_year>}` only — **never** pull other fields from the OL
   candidate here.  This step corrects year only; other fields stay as-is.

8. Final summary:
   ```
   Year audit complete. 34 scanned · 8 drift · 7 corrected · 1 skipped · 0 errors
   ```
   Delete the state file on clean completion.

**Edge case — ranges and approximate dates:** OL occasionally returns wide
ranges or centuries for old works (e.g. `first_publish_year: 1605` for a
modern edition of Don Quixote).  Trust OL in these cases — ancient/classic
first-pub years are usually correct in OL even if pre-modern.

**Never** auto-apply year corrections without surfacing them.  Even perfect
confidence requires explicit user approval — same rule as series title
drift.

---

### Step A1 — Scope

Default: all books via paginated `GET /api/books`.  Narrow scope using the
same filter-param mapping as Update mode Step U1.

Before proceeding, confirm scope with the user:

```
Auditing 347 books in Classics library. This will take ~5 min (one fetch per
weak book). Continue?
```

Estimate time as `ceil(weak_book_count / 10)` minutes (assuming ~6 s per
fetch, 10 concurrent).  You don't know the weak count yet at this stage, so
use total book count as an upper bound.

---

### Step A2 — Scan pass (cheap — no API fetches)

Paginate through all books in scope using `GET /api/books?skip=<N>&limit=200`
(using `$URL` and `$TOKEN` from the selected profile).
For each book, classify as weak or strong based on the criteria above.  No
`fetch-metadata` calls in this pass.

**Series title-drift detection:** as you paginate, build a per-series map:
```
series_name → {title_pattern: count}
```
Where `title_pattern` is the book title with the series index replaced by `N`
(e.g. `"Tarzan, Vol. 3"` → `"Tarzan, Vol. N"`).  After all pages are
read, apply the **drift detection rule** (defined once here; referenced from
Step A5 and sibling-lookup Step 4b / Step U3):

**Case A — clear majority (≥80%):** if the top pattern covers ≥80% of books
in the series and ≥1 book deviates, mark the outliers as weak with reason
`"series title drift"`.  The majority pattern is the suggested canonical form.

**Case B — no majority, but inconsistent:** if no single pattern reaches 80%
(e.g. all three books use different separators or number formats), yet the
titles are clearly inconsistent (differing separators like `/`, `,`, ` `;
differing number tokens like `v`, `Vol.`, `Volume`, `#`; or mixed
zero-padding), mark **all** books in the series as weak with reason
`"series title drift — no majority"` and supply a suggested canonical format
based on `book_type`:
- `book_type` comics or graphic_novel → suggest `"<Series>, Vol. <N>"`
- `book_type` book with series_index → suggest `"<Series>, Vol. <N>"`
- `book_type` book (novels, non-series standalones) → do not auto-suggest;
  ask the user what pattern to use
- Unknown / mixed → ask the user

**Case C — all consistent:** all books share the same pattern; no action.

The rule applies to series with ≥3 books.  For series with 2 books, only
flag if the two titles use clearly incompatible formats (e.g. one uses `Vol.`
and the other uses `/`).

Exploit the existing `missing` filter param to accelerate the scan:

```bash
# Books missing cover
curl -sf -H "Authorization: Bearer $TOKEN" \
  "$URL/api/books?missing=cover&limit=200&skip=0"

# Books missing description
curl -sf -H "Authorization: Bearer $TOKEN" \
  "$URL/api/books?missing=description&limit=200&skip=0"
```

Run these concurrently to build the weak set faster.  For year (null), there
is no `missing=year` param — paginate normally and filter client-side.

---

### Step A3 — Report before fetching

Print a summary of what the scan found, then ask for confirmation before any
`fetch-metadata` calls:

```
347 books scanned
  · 42 missing description
  · 18 missing year
  · 31 no cover
  · 6 series title drift
  68 unique weak books (some weak in multiple fields)

Proceed with candidate fetch on 68 weak books?
```

A book is counted once in the "unique weak books" total even if weak in
multiple fields.

Series title-drift cases are listed separately after the main counts.  Show
which drift case applies (A = clear majority, B = no majority):

```
Series title drift detected:
  "Tarzan"           [Case A] — 10 books use "Tarzan, Vol. N"; 1 uses "Tarzan vN"
  "Sherlock Holmes"  [Case A] — 8 books use "Sherlock Holmes Vol. N"; 2 use "Sherlock Holmes v N"
  "Barsoom"          [Case B] — 3 inconsistent patterns; suggested canonical: "Barsoom, Vol. N"
```

---

### Step A4 — Fetch, diff, apply

For each unique weak book, run the same fetch + score + diff loop as Update
mode Steps U4 and U5.  For books flagged as weak due to a missing or empty
description, apply the **web fallback for missing descriptions** (defined in
the ingest section) if the top fetch-metadata candidate's description is also
null, empty, or shorter than 200 characters.

Write a state file at `~/.cache/tome-scribe/.scribe-audit-<profile>-<timestamp>.json`
(same directory as update state files; create if missing) and persist after
each fetch so the run is resumable.  Including `<profile>` in the filename
ensures that resume checks only surface files for the currently-active profile.

State file schema (audit variant):

```json
{
  "mode": "audit",
  "profile": "<active profile name>",
  "scope": "<description of scope>",
  "run_at": "ISO timestamp",
  "total_scanned": 347,
  "weak_book_ids": [42, 55, 71],
  "books": [
    {
      "book_id": 42,
      "title": "Tarzan Vol. 1",
      "weak_reasons": ["no description", "no year"],
      "status": "pending|fetched|applied|skipped|error",
      "chosen_candidate_index": null,
      "diff": null,
      "error": null
    }
  ]
}
```

**Resume:** on start, check `~/.cache/tome-scribe/` for any
`.scribe-audit-<profile>-*.json` file (matching the current active profile)
with pending/fetched entries.  If found, offer to resume before re-scanning.
Ignore state files belonging to other profiles.

Stream progress while fetching:

```
Processing 1/68... 2/68... 3/68...
```

After all fetches complete, show the same compact diff format as Update mode
Step U5.  Then prompt for bulk resolution with the same option syntax
(`"accept all"`, `"skip #N"`, `"show #N"`, `"cancel"`).

---

### Step A5 — Series title drift (always surface for review, never auto-apply)

Title drift is handled separately from the main apply step, regardless of
candidate confidence scores.  Even a perfect confidence score does not
auto-apply a title change.

After the main apply step, present drift cases one at a time.  For Case A
(clear majority), show only the outliers.  For Case B (no majority), show
every book in the series alongside the suggested canonical form; let the user
accept, edit, or skip:

Case A example:
```
Series 'Sherlock Holmes' [Case A] — 8 books use "Sherlock Holmes Vol. N"; 2 use "Sherlock Holmes v N"
Outliers:
  #id=71  "Sherlock Holmes v 2"  → normalize to "Sherlock Holmes Vol. 2"?
  #id=83  "Sherlock Holmes v 5"  → normalize to "Sherlock Holmes Vol. 5"?
Normalize these outliers? [y/n/show]
```

Case B example:
```
Series 'Barsoom' [Case B] — 3 inconsistent patterns (no majority)
Suggested canonical: "Barsoom, Vol. N"  (series convention)
All books:
  #id=10  "Barsoom /1"    → "Barsoom, Vol. 1"
  #id=11  "Barsoom/2"     → "Barsoom, Vol. 2"
  #id=12  "Barsoom/3"     → "Barsoom, Vol. 3"
Accept suggested pattern, edit it, or skip? [accept/edit <new pattern>/skip]
```

If the user chooses `edit <new pattern>`, substitute that pattern for all
books in the series (replacing `N` with each book's `series_index`).

Never auto-apply in either case — always wait for explicit user confirmation.

- `y` → apply via `PUT /api/books/{id}` with `{"title": "<normalized>"}` for
  each outlier (using `$URL` and `$TOKEN` from the selected profile).
- `n` → skip this series, move to next.
- `show` → print all book titles in the series for context, then re-prompt.

Use `PUT /api/books/{id}` (not `apply-metadata`) for title-only corrections,
since `apply-metadata` triggers a full metadata merge and may overwrite other
fields.

---

### Step A6 — Final summary

```
Audit complete. 347 scanned · 68 weak · 51 updated · 12 skipped · 5 errors
Series drift: 3 series normalized, 1 skipped.
```

Delete the state file on clean completion.

---

## Shared conventions for update and audit modes

- Both modes use the **candidate scoring heuristic** table defined above (in
  the ingest section) without modification.  Do not redefine or adjust the
  scoring at runtime.
- Both modes use the **pinned-fields logic** from Step 4b and "Respecting
  pinned fields at apply time" (Step 6) in the ingest workflow.
- Both modes use the same **output discipline**: terse by default; verbose
  drill-down only on explicit user request ("show #N", "verbose").
- State files for both modes live in `~/.cache/tome-scribe/` (create if
  missing).  They are **not** placed in any book directory or the Tome data
  dir.
- `book_type_id` is **not** a filter param on `GET /api/books`.  To narrow by
  book type, resolve the type's `library_id` from `GET /api/book-types` and
  use `library_id=<id>` instead.
- Title-only corrections (drift normalization) use `PUT /api/books/{id}` with
  a body containing just `{"title": "<new>"}`.  Field-level metadata updates
  (description, year, cover, tags, etc.) use
  `POST /api/books/{id}/apply-metadata`.

---

## Error handling reminders

- If `extract.py` exits non-zero, show stderr and stop.
- If Tome returns 401 on any call, tell the user their token may be expired
  or wrong, and offer to re-run Step 0 for the active profile.
- If a single ingest call fails with a non-409 error, log it and continue —
  never abort the batch.
- If `/tmp` fills up, warn and stop before corrupting state.
