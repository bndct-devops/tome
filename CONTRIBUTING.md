# Contributing to Tome

Thanks for considering a contribution. Tome is small and opinionated; the
contribution process is correspondingly light.

## Before you start

- For non-trivial changes, **open an issue first** describing what you
  want to build and why. A 5-line issue saves a lot of "I rewrote this
  whole subsystem and now we disagree on direction" pain.
- For bug fixes, an issue isn't strictly required, but mentioning the
  bug somewhere helps.

## Getting set up

Requirements: **Python 3.12+**, **Node.js 22.19+**.

```bash
git clone https://github.com/bndct-devops/tome
cd tome
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cd frontend && npm install && cd ..

# Run backend + frontend with hot reload
./dev.sh
```

- Backend: <http://localhost:8080>
- Frontend: <http://localhost:5173>
- The first time you load the frontend, you'll be sent through the
  first-run setup wizard to create an admin user.

## Project structure

```
backend/         FastAPI app
  api/           HTTP routes
  core/          config, database, security, permissions
  models/        SQLAlchemy models
  schemas/       Pydantic request/response models
  services/      business logic that doesn't fit a route
frontend/src/
  pages/         top-level routed pages
  components/    reusable UI
  contexts/      AuthContext, ToastContext
  lib/           api client, typed shared types, utilities
tests/           pytest, against an in-memory SQLite
scripts/         one-off CLIs (e.g. import_library.py)
docs/            user-facing docs (markdown)
```

## Conventions

- **Python:** 3.12 type hints everywhere, SQLAlchemy 2.0 style, Pydantic v2.
- **Frontend:** React 19 + TypeScript strict, Tailwind 4 (no PostCSS),
  Lucide for icons (no other icon libs, no emoji), React Router.
- **Icons in the DB** are stored as Lucide name strings, not assets.
- **Database:** SQLite with WAL. Use `Base.metadata.create_all()` plus
  ad-hoc startup migrations in `backend/main.py` for additive schema
  changes. Alembic is configured for larger migrations.

## Before you submit a PR

Run the test suite and the frontend build:

```bash
pytest tests/ -q             # full backend suite
cd frontend && npm run build # typechecks + production build
```

Both should be green. If you've added a feature, add tests. If you've
fixed a bug, add a regression test that fails without your fix.

For UI changes, also load the affected page in the browser and click
through it. The dev server (`./dev.sh`) hot-reloads.

## Commit & PR style

- Subject line: imperative, ≤72 chars (`Fix sync badge timezone bug`,
  not `fixed sync badge tz bug`).
- Body: explain *why*, not just *what*. The diff shows what.
- One logical change per PR. Refactors and feature work shouldn't
  share a PR.
- PR description: include the issue number if applicable, and a short
  "test plan" of what you manually verified.

## Reporting bugs

Use the GitHub issue template. Include:

- Tome version (`/api/health` returns it) or commit SHA
- How you're running it (Docker, bare metal, OS)
- Steps to reproduce
- What you expected vs. what happened
- Relevant logs (sanitise tokens / secrets first)

## Reporting security issues

**Don't open a public issue.** See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree your contributions will be licensed under
AGPL-3.0 (the project's licence).
