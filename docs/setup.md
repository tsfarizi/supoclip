# Setup

This guide covers the native (no-container) setup, the checks to perform after first boot, and the pieces the one-command launcher manages for you.

## Requirements

### Required software

- [proto](https://moonrepo.dev/docs/proto/install) — toolchain version manager
- Git
- PostgreSQL installed and running as a service (this machine: PG18 on port **5433**)
- FFmpeg available on PATH (or a portable build under `%LOCALAPPDATA%\Programs\ffmpeg`)

### Required credentials

- `ASSEMBLY_AI_API_KEY` (or a native ASR service with `TRANSCRIPT_PROVIDER=local_asr`)
- One LLM provider configuration:
  - `OPENAI_API_KEY` with `LLM=openai:...`
  - `GOOGLE_API_KEY` with `LLM=google-gla:...`
  - `ANTHROPIC_API_KEY` with `LLM=anthropic:...`
  - `LLM=ollama:...` with an available Ollama server, optionally `OLLAMA_BASE_URL`

### Optional credentials

- `PEXELS_API_KEY` for AI B-roll sourcing
- `NEXT_PUBLIC_DATAFAST_WEBSITE_ID` and `NEXT_PUBLIC_DATAFAST_DOMAIN` for DataFast analytics
- `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `SES_FROM_EMAIL` for hosted billing emails
- Stripe keys if you are running with monetization enabled
- Discord webhook URLs for feedback forwarding

## Recommended Setup: Native Stack

The native path starts the frontend, backend, worker, PostgreSQL, and Redis together with the expected wiring.

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd supoclip
```

### 2. Install the pinned toolchain

All runtime versions (node, bun, python, uv) are pinned in `.prototools`:

```bash
proto install
```

### 3. Create a local environment file

```bash
cp .env.example .env
```

Then edit `.env` and set at least:

```env
ASSEMBLY_AI_API_KEY=your_assemblyai_key
LLM=google-gla:gemini-3-flash-preview
GOOGLE_API_KEY=your_google_key
BETTER_AUTH_SECRET=replace_this_for_real_use
BACKEND_AUTH_SECRET=replace_this_if_using_hosted_mode

# Optional: DataFast analytics
NEXT_PUBLIC_DATAFAST_WEBSITE_ID=dfid_xxxxx
NEXT_PUBLIC_DATAFAST_DOMAIN=your-domain.com
NEXT_PUBLIC_DATAFAST_ALLOW_LOCALHOST=false
```

### 4. Start the stack

```powershell
.\run.ps1
```

The launcher:
- installs anything missing from `.prototools`
- starts a portable Redis when none is listening on 6379
- bootstraps the `supoclip` role/database and applies `init.sql` when the schema is missing
- installs backend and frontend dependencies on first run
- starts worker, backend API, and frontend with logs in `.local/logs/`

### 5. Verify services

Logs live in `.local/logs/` (`backend.*.log`, `worker.*.log`, `frontend.*.log`). The launcher prints a health summary.

### 6. Open the application

- Frontend: `http://localhost:3107`
- Backend API: `http://localhost:8000`
- FastAPI docs: `http://localhost:8000/docs`

## What the Native Stack Starts

Four processes/infra:

- `frontend`
  - Next.js application on port `3107`
  - Proxies authenticated requests to the backend
- `backend`
  - FastAPI API on port `8000`
  - Provides task, media, billing, admin, and feedback endpoints
- `worker`
  - ARQ background worker
  - Processes long-running video jobs from Redis
- `postgres` / `redis`
  - Stores users, sessions, tasks, sources, clips, billing metadata (PG on 5433)
  - Backs the job queue and progress event flow (Redis on 6379)

## First-Run Checklist

After the stack is up:

1. Load the homepage at `http://localhost:3107`.
2. Create an account or sign in.
3. Submit a YouTube URL or upload a video file.
4. Open the task page and confirm progress updates appear.
5. Wait for clip generation to finish.
6. Open the clips list and verify playback and download work.
7. If DataFast is enabled, open browser devtools and confirm `/js/script.js` and `/api/events` load from your own domain.
8. Trigger one successful action such as sign-up, sign-in, task creation, feedback submission, or waitlist submission and verify the goal arrives in DataFast.

## Running Apps Individually

You still need PostgreSQL and Redis running.

### Backend

```bash
cd backend
uv sync
uv run uvicorn src.app:app --reload --host 0.0.0.0 --port 8000  # shim src.main_refactored:app compat
```

In a second terminal:

```bash
cd backend
uv run arq src.workers.tasks.WorkerSettings
```

### Frontend

```bash
cd frontend
bun install
bun run dev
```

### MCP server (optional)

```bash
cd mcp
uv sync
uv run supoclip-mcp
```

### Required local dependencies

- Python 3.11+ (proto-pinned)
- Node.js compatible with Next.js 15 (proto-pinned)
- PostgreSQL
- Redis (portable build auto-started by `run.ps1`)
- FFmpeg available to the backend environment

## Data and Storage

Natively, the stack shares one filesystem tree (`TEMP_DIR`, default `backend/data`):

- `backend/data/uploads`
- `backend/data/clips`
- `backend/data/broll`

Persistent data lives in the PostgreSQL data directory and Redis dump file. These local directories are read directly from the repository:

- `backend/fonts`
- `backend/transitions`

## Hosted Mode Versus Self-Hosted Mode

SupoClip defaults to self-host mode:

```env
SELF_HOST=true
```

When `SELF_HOST=false`, monetization and hosted billing flows become active. That mode requires additional Stripe and backend auth configuration. See [Configuration](./configuration.md).

## Production Setup Notes

For anything beyond local experimentation:

- Change `BETTER_AUTH_SECRET`
- Set a strong `BACKEND_AUTH_SECRET`
- Put the app behind HTTPS
- Set `NEXT_PUBLIC_APP_URL` to your deployed frontend origin
- Use persistent storage and backups for PostgreSQL
- Keep API keys outside version control
- Decide whether you want self-host mode or monetized hosted mode before launch
- Verify all callback URLs and origins match your deployed domain
- If using DataFast, set `NEXT_PUBLIC_DATAFAST_DOMAIN` to the deployed root domain you want tracked
- For hosted billing, create and verify both Stripe monthly prices before deploy: Pro at `$10/month` and Scale at `$50/month`

## Useful Commands

### Start the stack

```powershell
.\run.ps1
```

### Stream logs

```powershell
Get-Content .local\logs\backend.out.log -Wait
Get-Content .local\logs\worker.out.log -Wait
Get-Content .local\logs\frontend.out.log -Wait
```

### Stop services

```powershell
.\stop.ps1
```

### Reset the database

```powershell
# WARNING: This deletes all data!
$env:PGPASSWORD='postgres'
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -U postgres -h localhost -p 5433 -d postgres -c "DROP DATABASE supoclip;"
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -U postgres -h localhost -p 5433 -d postgres -c "CREATE DATABASE supoclip OWNER supoclip;"
.\run.ps1   # re-applies init.sql
```

## Next Steps

- Review [Configuration](./configuration.md) before changing defaults
- Review [App Guide](./app-guide.md) to understand the UI and workflows
- Review [Troubleshooting](./troubleshooting.md) if tasks do not process correctly
