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
[proto](https://moonrepo.dev/docs/proto): `node`, `pnpm`, `python`, `deno`, `uv`.
Bootstrap once with `proto install`, then run the full stack with `.\run.ps1`.

## Build, Test, and Development Commands
The stack is fully native (no containers). `run.ps1` starts everything:
- `.\run.ps1`: start worker, backend API, and frontend (auto-bootstraps Redis,
  PostgreSQL schema, and dependency installs).
- `.\stop.ps1`: stop all SupoClip processes.
- `.\run.ps1 -SkipDeps`: skip `uv sync` / `pnpm install` on re-runs.
- `.\run.ps1 -IncludeMcp` / `-IncludeAsr`: also start the optional MCP/ASR services.

Logs land in `.local/logs/`.

Local app commands (manual):
- `cd backend && uv sync && uv run uvicorn src.main_refactored:app --reload --host 0.0.0.0 --port 8000`: run API locally.
- `cd backend && uv run arq src.workers.tasks.WorkerSettings`: run the worker.
- `cd frontend && pnpm install && pnpm run dev`: run Next.js in dev mode (port 3107).
- `cd frontend && pnpm run build && pnpm run start`: production build + serve.
- `cd frontend && pnpm run lint`: run ESLint.
- `cd mcp && uv run supoclip-mcp`: run the MCP server.

## Coding Style & Naming Conventions
- Python: 4-space indentation, type hints where practical, `snake_case` for functions/modules.
- TypeScript/React: 2-space indentation, `PascalCase` for component names, `camelCase` for variables/functions, route files in Next.js App Router conventions (`app/.../page.tsx`, `route.ts`).
- Linting: Next.js ESLint configs in `frontend/eslint.config.mjs` and `waitlist/eslint.config.mjs`.
- Imports: use the `@/*` alias in Next.js apps when possible.

## Testing Guidelines
There is no mature automated test suite yet. Treat linting plus manual verification as the current baseline:
- Run `pnpm run lint` in the Next.js app.
- Smoke test core flows with the native stack (create task, process clips, view task page).

When adding tests, place them near code or under `tests/` with clear names (`test_*.py`, `*.test.ts[x]`).

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
