# Security Policy

Thanks for taking the time to report a vulnerability. Tome is a small
self-hosted project run by a single maintainer; responsible disclosure
keeps users safe while we work on a fix.

## Supported versions

| Version | Supported |
|---------|-----------|
| The latest release | Yes |
| Anything older     | No: update to the latest release |

We don't backport fixes to pre-`0.2` releases. Upgrade to `0.2.0` or
later if you're on something older.

## Reporting a vulnerability

**Please don't open a public GitHub issue for security reports.**

Email **security@bndct.dev** with:

- A description of the vulnerability and its impact
- Steps to reproduce (ideally with a minimal repro or proof-of-concept)
- The Tome version / commit SHA you tested against
- Your name or handle if you'd like credit in the fix's release notes

You can expect:

- An acknowledgement within 7 days
- A fix or mitigation within 30 days for confirmed issues, sooner for
  anything actively exploitable
- A public disclosure (via a GitHub Security Advisory) once a patched
  release is out

## Scope

In scope:

- Authentication and authorisation flows (JWT, API tokens, OPDS Basic,
  KOSync headers, Quick Connect codes)
- Per-user book visibility / IDOR-class bugs
- File upload, ingest, and bindery paths (path traversal, zip-slip,
  archive extraction)
- Server-side request forgery in metadata fetching (`safe_fetch`)
- Credential storage (password hashing, API key hashing, OPDS PIN hashing)
- Sensitive data in logs or error responses

Out of scope:

- Self-XSS against your own account
- Issues that require physical access to the server or its filesystem
- Vulnerabilities in third-party services Tome integrates with
  (Hardcover, Google Books, OpenLibrary) — report those upstream
- Denial-of-service via uncapped uploads on instances with no upstream
  proxy rate-limiting (Tome assumes a reverse proxy is in front)

## Hardening guidance for operators

The README and `docs/` cover deployment basics. A short security-focused
checklist:

- Run behind a reverse proxy that terminates TLS and rate-limits
  authentication endpoints
- Don't expose `/api/docs` to the public internet (the OpenAPI spec
  discloses every endpoint shape)
- Set a long, random `TOME_SECRET_KEY` rather than relying on the
  auto-generated one if you're rotating secrets externally
- Keep the Docker image up to date — `docker compose pull && docker
  compose up -d` weekly is fine
- Run the container as the non-root `tome` user (the default)
