# CONVENTIONS — SupoClip

> Ringkasan operasional dari TARGET_ARCHITECTURE.md (V2 Gate, 2026-08-24)

## 1. Penamaan
- Backend entry: `src/app.py:app` (kanonik), shim `src/main_refactored.py` untuk kompatibilitas 1 sprint
- Worker: `src/worker.py:WorkerSettings` (shim `src/worker_main.py`)
- Paket: `shared/` (config, errors, observability), `infra/` (db, cache, media_tools), `domain/` (auth, media, caption, ingestion, task)
- Frontend: `server/` = server-only (guard `import "server-only"`), `lib/` = murni client-safe, `hooks/` = data-fetch, `components/` = UI

## 2. Matriks Impor Backend
| Dari → Ke | shared | infra | domain | services | api | workers | migrations |
|---|---|---|---|---|---|---|---|
| shared | OK | NG | NG | NG | NG | NG | NG |
| infra | OK lazy | OK | NG | NG | NG | NG | NG |
| domain | OK | OK | OK | NG | NG | NG | NG |
| services | OK | OK | OK | OK* | NG | NG | NG |
| api | OK | OK | OK | OK | OK | NG (kecuali job_queue/progress) | NG |
| workers | OK | OK | OK | OK | NG | OK | NG |
| migrations | NG | OK (db) | NG | NG | NG | NG | OK |

*services tidak saling impor siklis; `task_service ↔ clip_render` via konstruktor, bukan import siklis.
Enforcement: `backend/scripts/boundary_guard.py` (regex, `python backend/scripts/boundary_guard.py`)

## 3. Frontend Boundary
- `server/db.ts` = satu-satunya PrismaClient singleton (`server-only` + globalThis)
- `server/auth.ts` = betterAuth (pakai `server/db`), `server/stripe.ts`, `server/backend-auth.ts` (crypto), `server/session.ts`
- `lib/` shim re-export dari `server/` — jangan impor `server/*` dari `components/` atau `hooks/` (eslint `no-restricted-imports`)
- Handler `app/api/**/route.ts` tipis: validasi → delegasi `fetch` backend → `createProxyResponse`
- Error: satu `lib/api-error.ts` (`parseApiError`/`buildSupportError` + `trace_id`), dipakai via `lib/api-client.ts` (`fetchJson`/`apiFetch`)

## 4. Toolchain
- Pinned: `node 22.23.2`, `bun 1.2.18`, `python 3.12.5`, `uv 0.9.7` (`.prototools` — tanpa deno)
- Lock kanonik: `frontend/bun.lock` + `e2e/bun.lock` **text** (bun 1.2+ canonical, equivalen `bun.lockb` binary; `bun install --frozen-lockfile` valid untuk keduanya) — lihat `docs/architecture/TARGET_ARCHITECTURE.md` §4.2 & `docs/architecture/BUN_SPIKE.md`
- Frontend/e2e: `bun install --frozen-lockfile`, `bunx prisma generate`, `bun run dev/build/start`, `bun run lint`, `bunx tsc --noEmit`
- Backend: `uv sync`, `uv run uvicorn src.app:app` (shim `src.main_refactored:app` compat, ADR-002), `uv run arq src.workers.tasks.WorkerSettings` (shim `src.worker.WorkerSettings` compat)
- MCP: `uv run --directory mcp ...`, port 9100 frozen
- Run: `.\run.ps1` tetap kanonik Windows-native 343L behavior-preserving (bun `install --frozen-lockfile` + `bunx prisma generate` + `bun run build/start`, `uv sync` + `alembic upgrade head`); `scripts/run.mjs` cross-platform ditunda — lihat TARGET §4.4, logs `.local/logs/`
- Port & env var tetap: frontend `3107`, backend `8000`, `DATABASE_URL` `postgresql+asyncpg://…@5433/supoclip`, `REDIS` `6379`, `TEMP_DIR` absolute `backend/data`

## 5. Taksonomi Error
Backend `shared/errors`: `TaskProcessingError` → Validation/NotFound/Duplicate/Billing/Auth/Server → HTTP 400/404/409/402/401/500 + `trace_id`. Frontend `lib/api-error.ts` envelope `ApiErrorInfo { status, detail, traceId }`.

## 6. ADR Ringkas
- ADR-001 pnpm→bun (L1: package manager only, Node compat untuk Next/Prisma/Playwright)
- ADR-002 main_refactored→app.py (shim 1 sprint)
- ADR-003 TEMP_DIR single absolute tree (`backend/data`, env override)
- ADR-004 Prisma singleton (server/db.ts)
- ADR-005 cycle break via lazy import (config↔runtime_settings↔infra)

## 7. Invarian
Behavior-preserving, titik masuk beku, 11 invarian (API route, URL, CAS lifecycle, billing atomik, trace_id, SSE, file_path, HMAC, Schema v2, Prisma, upload). Detail: `docs/architecture/TARGET_ARCHITECTURE.md` §7.

## 8. Referensi
`docs/architecture/TARGET_ARCHITECTURE.md` (gate), `docs/architecture/BUN_SPIKE.md` (spike L1), `backend/scripts/boundary_guard.py`

## 9. Pytest Gate — Narrow Scope (U8 → U9 preservasi)

Backend `pytest` gate `≥65%` **narrow-scope** preservasi asli: `--cov=src.auth_headers --cov=src.services.billing_service` (`backend/pyproject.toml` addopts). Ekspansi ke `--cov=src` **ditunda** hingga god modules `video_utils`/`ai` ter-strangle dan test seam tersedia — amplop validitas U9, bukan perubahan perilaku. Perintah copy-paste kanonik tetap:

```bash
cd backend && uv run pytest
cd frontend && bun run lint
cd frontend && bunx tsc --noEmit
cd e2e && bunx playwright test
```

Copy-paste gate tidak berubah; hanya cakupan threshold yang dicatat sebagai preservasi.
