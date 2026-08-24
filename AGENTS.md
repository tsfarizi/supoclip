# Repository Guidelines

## Project Structure & Module Organization
This repository is a monorepo with two apps:
- `backend/`: FastAPI + async worker code (`src/api`, `src/services`, `src/repositories`, `src/workers`).
- `frontend/`: main Next.js app (`src/app`, `src/components`, `src/lib`, `prisma/`).
- `mcp/`: standalone MCP server for programmatic access (`src/supoclip_mcp`).
- `asr/`: optional self-hosted transcription service (Qwen3-ASR, GPU).

Infra and bootstrap files live at the root: `.prototools`, `init.sql`, `.env.example`, `run.ps1`, and `stop.ps1`.

## Toolchain (proto)
All runtime toolchain versions are pinned in `.prototools` and managed by
[proto](https://moonrepo.dev/docs/proto): `node`, `bun`, `python`, `uv`.
Bootstrap once with `proto install`, then run the full stack with `.\run.ps1`.

## Build, Test, and Development Commands
The stack is fully native (no containers). `run.ps1` starts everything:
- `.\run.ps1`: start worker, backend API, and frontend (auto-bootstraps Redis,
  PostgreSQL schema, and dependency installs).
- `.\stop.ps1`: stop all SupoClip processes.
- `.\run.ps1 -SkipDeps`: skip `uv sync` / `bun install` on re-runs.
- `.\run.ps1 -IncludeMcp` / `-IncludeAsr`: also start the optional MCP/ASR services.

Logs land in `.local/logs/`.

Local app commands (manual):
- `cd backend && uv sync && uv run uvicorn src.app:app (shim src.main_refactored:app compat) --reload --host 0.0.0.0 --port 8000`: run API locally.
- `cd backend && uv run arq src.workers.tasks.WorkerSettings`: run the worker.
- `cd frontend && bun install && bun run dev`: run Next.js in dev mode (port 3107).
- `cd frontend && bun run build && bun run start`: production build + serve.
- `cd frontend && bun run lint`: run ESLint.
- `cd mcp && uv run supoclip-mcp`: run the MCP server.

## Coding Style & Naming Conventions
- Python: 4-space indentation, type hints where practical, `snake_case` for functions/modules.
- TypeScript/React: 2-space indentation, `PascalCase` for component names, `camelCase` for variables/functions, route files in Next.js App Router conventions (`app/.../page.tsx`, `route.ts`).
- Linting: Next.js ESLint configs in `frontend/eslint.config.mjs` and `waitlist/eslint.config.mjs`.
- Imports: use the `@/*` alias in Next.js apps when possible.

## Testing Guidelines
Backend `pytest` gate narrow-scope `auth+billing ≥65%` (`--cov=src.auth_headers --cov=src.services.billing_service`; ekspansi `--cov=src` ditunda — lihat `backend/pyproject.toml` addopts & `CONVENTIONS.md` §9 / `docs/architecture/TARGET_ARCHITECTURE.md` §7.4):

```bash
cd backend  && uv run pytest
cd frontend && bun run lint
cd frontend && bunx tsc --noEmit
cd e2e      && bunx playwright test
```

Smoke test core flows with the native stack (create task, process clips, view task page). When adding tests, place them near code or under `tests/` with clear names (`test_*.py`, `*.test.ts[x]`). Pins `.prototools` → `node 22.23.2`/`bun 1.2.18`/`python 3.12.5`/`uv 0.9.7` tanpa deno; lock `frontend/bun.lock` text; entry `src.app:app` shim `src.main_refactored:app` compat; `run.ps1` kanonik.

## Commit & Pull Request Guidelines
Recent history favors short imperative commit subjects (`Add list endpoint`, `Fix typo`, `improve UX`). Prefer:
- `type(scope): concise summary` (example: `feat(backend): add task list pagination`).
- One logical change per commit.

PRs should include:
- What changed and why.
- Any env/config or migration impact.
- Screenshots/GIFs for UI changes.
- Linked issue(s) and manual verification steps.

## Security & Configuration Tips
- Never commit real secrets; use `.env.example` as the template.
- Required runtime keys include `ASSEMBLY_AI_API_KEY` and either one hosted LLM provider key (`OPENAI_API_KEY`, `GOOGLE_API_KEY`, or `ANTHROPIC_API_KEY`) or an Ollama model configuration (`LLM=ollama:*`, optional `OLLAMA_BASE_URL`).
