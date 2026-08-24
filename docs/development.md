# Development

This guide is for contributors working on SupoClip locally.

## Repository Layout

Current repository structure:

- `backend/`
  - FastAPI app
  - ARQ worker
  - services, repositories, route modules, and media-processing code
- `frontend/`
  - Next.js app
  - App Router pages, API routes, auth, Prisma schema, UI components
- Root files
  - `.prototools`
  - `init.sql`
  - `.env.example`
  - `run.ps1` / `stop.ps1`

Note: older repo guidance references a `waitlist/` app, but it is not present in this checkout.

## Main Commands

## Full stack natively (no containers)

```powershell
proto install   # one-time: pinned toolchain
.\run.ps1       # start worker, API, frontend (+ auto-bootstrap Postgres/Redis)
.\stop.ps1      # stop everything
```

## Frontend

```bash
cd frontend
bun install
bun run dev
bun run build
bun run start
bun run lint
```

## Backend

```bash
cd backend
uv sync
uv run uvicorn src.app:app --reload --host 0.0.0.0 --port 8000  # shim src.main_refactored:app compat
```

Run the worker separately:

```bash
cd backend
uv run arq src.workers.tasks.WorkerSettings  # shim src.worker.WorkerSettings compat
```

> Toolchain pins: `.prototools` → `node 22.23.2`, `bun 1.2.18`, `python 3.12.5`, `uv 0.9.7` (tanpa deno). Frontend lock kanonik `bun.lock` (text, `bun install --frozen-lockfile`).

## Frontend Development Notes

Important locations:

- `frontend/src/app`
  - App pages and API routes
- `frontend/src/components`
  - Reusable UI and product components
- `frontend/src/lib`
  - Pure client-safe helpers (`lib/api-client.ts` → `fetchJson`/`apiFetch`, `lib/api-error.ts`, `lib/task-types.ts`); re-export tipis dari `server/` bila perlu
- `frontend/src/server`
  - Server-only boundary (`server/db.ts` singleton Prisma, `server/backend-auth.ts`, `server/stripe.ts`, `server/session.ts` — guard `import "server-only"`)
- `frontend/src/hooks`
  - Data-fetch hooks (`use-task-query`, `use-task-polling`, `use-clip-editor` … — lihat `docs/architecture/frontend-decomposition.md`)
- `frontend/prisma`
  - Prisma schema and migrations, if present in your branch
- `frontend/src/generated/prisma`
  - Generated Prisma client output

### Build behavior

The frontend build runs:

```bash
prisma generate && next build
```

`postinstall` also runs Prisma generation.

### Frontend patterns

- App Router
- Mostly client-side product pages
- Better Auth sessions
- No dedicated global state library

## Backend Development Notes

Important locations:

- `backend/src/app.py (shim backend/src/main_refactored.py)`
  - Active entry point
- `backend/src/api/routes`
  - Route modules
- `backend/src/services`
  - Business logic
- `backend/src/repositories`
  - Data access
- `backend/src/workers`
  - Queue processing
- `backend/src/video_utils.py`
  - Clip rendering pipeline
- `backend/src/ai.py`
  - LLM prompt and validation logic

### Layering guideline

When possible:

- keep HTTP concerns in route modules
- keep orchestration in services
- keep SQL and persistence in repositories

## Database Notes

The primary database bootstrap file is:

- `init.sql`

It defines:

- users
- sessions
- auth support tables
- tasks
- sources
- generated clips
- processing cache
- Stripe webhook tracking

The frontend also uses Prisma for auth and admin-related access patterns.

## Common Development Workflows

### Modify clip selection behavior

Primary files:

- `backend/src/ai.py`
- `backend/src/services/video_service.py`

Use this area when changing:

- segment selection rules
- LLM prompts
- output validation
- clip count heuristics

### Modify rendering or subtitle behavior

Primary files:

- `backend/src/video_utils.py`
- `backend/src/caption_templates.py`
- `backend/src/clip_editor.py`

Use this area when changing:

- subtitle layout
- font rendering
- cropping
- export presets
- clip edits after generation

### Modify task orchestration

Primary files:

- `backend/src/api/routes/tasks.py`
- `backend/src/services/task_service.py`
- `backend/src/workers/tasks.py`
- `backend/src/workers/job_queue.py`

Use this area when changing:

- status transitions
- background job behavior
- cancellation and resume logic
- progress reporting

### Modify uploads, fonts, transitions, or media listings

Primary files:

- `backend/src/api/routes/media.py`
- `backend/src/font_registry.py`
- `backend/fonts/`
- `backend/transitions/`

### Modify auth or user roles

Primary files:

- `frontend/src/lib/auth.ts`
- `frontend/src/app/api/auth/[...all]/route.ts`
- `init.sql`

### Modify billing behavior

Primary files:

- `frontend/src/app/api/billing/*`
- `frontend/src/lib/stripe.ts`
- `backend/src/api/routes/billing.py`
- `backend/src/services/billing_service.py`
- `backend/src/services/subscription_email_service.py`

### Modify the admin dashboard

Primary files:

- `frontend/src/app/admin/page.tsx`
- `backend/src/api/routes/admin.py`

## Testing and Verification

The repository now uses a two-layer automated test setup:

- backend `pytest` for unit and integration coverage
- frontend `Playwright` for browser e2e tests in `e2e/` against real frontend and backend processes

Direct app-level commands:

```bash
cd backend && uv run pytest
cd e2e && bunx playwright test
```

### Local Test Environment

- Start PostgreSQL and Redis locally before running integration or e2e flows.
- `.\run.ps1` starts both (plus the full stack) when you want manual smoke testing.

Useful backend test env vars:

```bash
DATABASE_URL=postgresql+asyncpg://supoclip:supoclip_password@localhost:5433/supoclip
TEST_DATABASE_URL=postgresql+asyncpg://supoclip:supoclip_password@localhost:5433/supoclip
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
BACKEND_AUTH_SECRET=supoclip_test_secret
BETTER_AUTH_SECRET=supoclip_better_auth_test_secret
```

### Coverage and CI

- Backend coverage gate narrow-scope `auth+billing` `≥65%` (`--cov=src.auth_headers --cov=src.services.billing_service` — preservasi gate asli; ekspansi ke `--cov=src` ditunda hingga god modules `video_utils`/`ai` ter-strangle, lihat `docs/architecture/TARGET_ARCHITECTURE.md` §7 / `CONVENTIONS.md` §9).
- Frontend unit tests were removed with Vitest; frontend verification is `bun run lint` + `bunx tsc --noEmit` plus the Playwright e2e suite in `e2e/`.
- Verifikasi copy-paste kanonik:
  ```bash
  cd frontend && bun run lint
  cd frontend && bunx tsc --noEmit
  cd backend  && uv run pytest
  cd e2e      && bunx playwright test
  ```
- GitHub Actions runs separate `backend`, `frontend`, and `e2e` jobs with Postgres and Redis service containers.
- Playwright failures retain traces, screenshots, and videos for debugging.

### Recommended Manual Smoke Test

Automated tests cover the main seams, but manual smoke testing is still useful for high-risk media flows:

1. Start the stack.
2. Sign in.
3. Create a task from a YouTube URL.
4. Confirm progress updates arrive.
5. Confirm clips are generated.
6. Confirm clip editing and export actions still work.

## Helpful Logs

Logs for every process live in `.local/logs/`:

```text
.local/logs/backend.*.log
.local/logs/worker.*.log
.local/logs/frontend.*.log
```

## Codebase Conventions

### Backend

- Python 3.11+
- 4-space indentation
- Prefer type hints where practical
- `snake_case` naming

### Frontend

- TypeScript and React
- 2-space indentation
- `PascalCase` components
- `camelCase` variables and functions
- Use `@/*` imports where practical

## Safe Defaults for New Work

- Prefer `backend/src/app.py (shim backend/src/main_refactored.py)` over `main.py`
- Keep auth-sensitive browser requests behind frontend API routes
- Preserve async behavior by keeping blocking work out of FastAPI request handlers
- Use the worker for long-running media processing

## Related Reading

- [Architecture](./architecture.md)
- [API Reference](./api-reference.md)
- [Troubleshooting](./troubleshooting.md)
