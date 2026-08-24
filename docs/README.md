# SupoClip Documentation

This directory is the canonical documentation hub for SupoClip.

If you are new to the project, start here:

1. Read [Setup](./setup.md) to get the app running.
2. Review [Configuration](./configuration.md) to understand environment variables and operating modes.
3. Use [App Guide](./app-guide.md) to learn the user-facing parts of the product.
4. Use [Architecture](./architecture.md) to understand how the system works end to end.
5. Use [Troubleshooting](./troubleshooting.md) when something goes wrong.

## Documentation Map

- [Setup](./setup.md)
  - Native one-command installation
  - Local development commands
  - First-run checklist
  - Production-minded setup notes
- [Configuration](./configuration.md)
  - Required API keys
  - DataFast analytics settings
  - Processing modes
  - Auth and monetization settings
  - YouTube auth rotation settings
  - Feedback and email configuration
- [App Guide](./app-guide.md)
  - Main screens and routes
  - Core user workflows
  - Admin features
  - Hosted versus self-host differences
- [Architecture](./architecture.md)
  - Frontend, backend, worker, Redis, PostgreSQL
  - Queue and SSE progress flow
  - Video processing pipeline
  - Database model overview
- [API Reference](./api-reference.md)
  - Frontend proxy routes
  - Backend endpoints
  - Admin and billing endpoints
  - Notes on auth and streaming
- [Development](./development.md)
  - Repository layout
  - Commands for each app
  - Common workflows
  - Where to modify major features
- [Troubleshooting](./troubleshooting.md)
  - Startup failures
  - Stuck tasks
  - Auth, fonts, billing, and YouTube issues
  - Performance and recovery guidance

## What SupoClip Is

SupoClip is an open-source AI video clipping application. It takes long-form videos, transcribes them, uses an LLM to select the most promising short segments, and renders vertical or source-aspect clips with subtitles and optional effects.

The current repository snapshot includes:

- `frontend/`: the main Next.js application (`src/server/db.ts` singleton Prisma, `src/lib/api-client.ts` `fetchJson`/`apiFetch`, `src/hooks/`, `src/components/features/`)
- `backend/`: the FastAPI API (`src/app.py` kanonik, shim `src/main_refactored.py` compat) and ARQ worker code
- Root-level infrastructure files such as `.prototools` (`node 22.23.2`/`bun 1.2.18`/`python 3.12.5`/`uv 0.9.7` tanpa deno, lock `frontend/bun.lock` text), `init.sql`, `.env.example`, `run.ps1` (kanonik 343L behavior-preserving), and `stop.ps1`

Boundary kanonik diringkas di [`CONVENTIONS.md`](../CONVENTIONS.md) dan `docs/architecture/TARGET_ARCHITECTURE.md` (gate V2) — termasuk matriks impor backend, `server/` vs `lib/` vs `hooks/`, dan pytest narrow-scope `auth+billing ≥65%` (ekspansi `--cov=src` ditunda).

Repository guidance still mentions a separate `waitlist/` app, but that directory is not present in this checkout. The documentation in this folder reflects the repository as it exists now (as-built U9, post-remediasi U8).

## Recommended Reading Paths

For operators:

1. [Setup](./setup.md)
2. [Configuration](./configuration.md)
3. [Troubleshooting](./troubleshooting.md)

For developers:

1. [Development](./development.md)
2. [Architecture](./architecture.md)
3. [API Reference](./api-reference.md)

For product and support:

1. [App Guide](./app-guide.md)
2. [Troubleshooting](./troubleshooting.md)

## Existing Documentation Outside `docs/`

This new docs tree replaces the need to hunt across several markdown files, but these older documents still contain useful context:

- [`README.md`](../README.md)
- [`QUICKSTART.md`](../QUICKSTART.md)
- [`CLAUDE.md`](../CLAUDE.md)
- [`REFACTORING_COMPLETE.md`](../REFACTORING_COMPLETE.md)
- [`backend/REFACTORING_GUIDE.md`](../backend/REFACTORING_GUIDE.md)
