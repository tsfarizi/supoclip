# Troubleshooting

This guide collects the most common operational issues with SupoClip and how to diagnose them. It covers the native (no-container) Windows stack that `run.ps1` launches.

## Where Things Live

The launcher writes one log pair per process under `.local\logs\`:

| Process | Command in `run.ps1` | Logs | Port |
|---|---|---|---|
| Worker (arq) | `python -m arq src.workers.tasks.WorkerSettings` | `.local\logs\worker.out.log`, `worker.err.log` | — |
| Backend (FastAPI) | `python -m uvicorn src.app:app --host 127.0.0.1 --port 8000` (shim `src.main_refactored:app` compat) | `.local\logs\backend.out.log`, `backend.err.log` | 8000 |
| Frontend (Next.js production) | `bun run start --port 3107` | `.local\logs\frontend.out.log`, `frontend.err.log` | 3107 |
| MCP (optional, `-IncludeMcp`) | `supoclip-mcp` | `.local\logs\mcp.*.log` | 9100 |
| ASR (optional, native) | `python -m uvicorn src.main:app --port 8765` | `.local\logs\asr.*.log` | 8765 |

Infrastructure is expected to be live on the machine, not managed by the app:

- PostgreSQL on port **5433** (database `supoclip`)
- Redis on port **6379**

`stop.ps1` stops the app processes only; it deliberately leaves PostgreSQL and Redis running.

## Start Here

When the app is misbehaving, check these first:

```powershell
# Are the app ports listening?
Get-NetTCPConnection -State Listen -LocalPort 8000,3107 -ErrorAction SilentlyContinue
Test-NetConnection -ComputerName localhost -Port 8000
Test-NetConnection -ComputerName localhost -Port 3107
```

```powershell
# Tail the logs for each process
Get-Content .local\logs\backend.err.log -Tail 50
Get-Content .local\logs\worker.err.log -Tail 50
Get-Content .local\logs\frontend.err.log -Tail 50
```

Also verify:

- `http://localhost:3107` loads
- `http://localhost:8000/health` responds
- `http://localhost:8000/docs` opens
- `http://localhost:8000/health/db` and `http://localhost:8000/health/redis` both report OK

If nothing is running, start the stack with `.\run.ps1` from the repo root and watch the output; each process prints its log path when it starts.

## Services Fail to Start

### Symptom

One or more processes exit immediately, or `run.ps1` reports `did not come up` for a port.

### Checks

```powershell
# Worker, backend, frontend stderr/stdout in order
Get-Content .local\logs\worker.err.log -Tail 100
Get-Content .local\logs\backend.err.log -Tail 100
Get-Content .local\logs\frontend.err.log -Tail 100
```

- Redis on 6379: `Test-NetConnection -ComputerName localhost -Port 6379`
- PostgreSQL on 5433: `Test-NetConnection -ComputerName localhost -Port 5433`

### Common causes

- PostgreSQL is not running (port 5433 closed)
- Redis is not reachable (port 6379 closed)
- `.env` is missing or incomplete (`run.ps1` warns `copy .env.example to .env`)
- frontend build-time variables are invalid
- backend startup is failing because a required key or URL is malformed
- `backend\.venv` is missing because the first run was done with `-SkipDeps` (the script exits: `backend/.venv missing - run without -SkipDeps first`)

### Verify local infrastructure (PostgreSQL and Redis)

PostgreSQL must be installed and running as a service (this machine: PG18 on port 5433). Check it with:

```powershell
Test-NetConnection -ComputerName localhost -Port 5433

# Confirm the app role and schema exist (PGPASSWORD is the app role password)
$env:PGPASSWORD = 'supoclip_password'
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -U supoclip -h localhost -p 5433 -d supoclip -c "\dt"
Remove-Item Env:PGPASSWORD
```

Redis can be auto-started by `run.ps1` if a portable build exists under `%LOCALAPPDATA%\Programs\redis\redis-server.exe`. If that file is missing, install a portable Redis there (or start any Redis on 6379), then re-run `.\run.ps1`. Check it with:

```powershell
& "$env:LOCALAPPDATA\Programs\redis\redis-cli.exe" ping   # expects PONG
```

If `run.ps1` cannot reach Redis on 6379 it exits with `Redis is not reachable...` before starting any app process.

### Fixes

- Start PostgreSQL service, confirm Redis, then re-run `.\run.ps1`.
- Create `.env` from `.env.example` if it is missing.
- After config changes, restart the whole stack:
  - `.\stop.ps1` (stops worker/backend/frontend)
  - `.\run.ps1` (rebuilds the frontend if needed; use `-SkipBuild` to reuse the existing build, `-Rebuild` to force one)
- If the environment is badly out of sync, delete stale PID files under `.local\pids\`, stop everything, and start fresh.

## Tasks Stay Queued Forever

### Symptom

Task creation succeeds, but progress never moves beyond `queued`.

### Most likely causes

- Worker process is not running
- Redis is unavailable
- Worker cannot reach backend dependencies
- Task timed out in queue handling

### Checks

```powershell
Get-Content .local\logs\worker.err.log -Tail 100
Get-Content .local\logs\worker.out.log -Tail 50
& "$env:LOCALAPPDATA\Programs\redis\redis-cli.exe" ping
```

Also inspect:

- task status in the UI
- backend progress endpoint behavior (`GET /tasks/{task_id}/progress`)
- `QUEUED_TASK_TIMEOUT_SECONDS` (default `180`; stale queued tasks are marked `error` after this)

### Fixes

- Restart the worker. Either stop the whole stack and start it again, or run the worker in a foreground terminal so its output is live:
  ```powershell
  cd backend
  uv run arq src.workers.tasks.WorkerSettings
  ```
- Confirm Redis is healthy (`redis-cli ping` returns `PONG`).
- Confirm the task was actually enqueued (worker log should show a dequeue line shortly after creation).
- Review worker exceptions around download, transcription, or rendering in `worker.err.log`.

## Backend Starts But Clip Generation Fails

### Symptom

Tasks begin processing and then move to `error`.

### Common causes

- Invalid API key
- LLM/provider mismatch
- YouTube download issue
- AssemblyAI failure
- FFmpeg or media-processing dependency issue
- Rendering failure caused by fonts or clip options

### Checks

```powershell
Get-Content .local\logs\worker.err.log -Tail 100
Get-Content .local\logs\backend.err.log -Tail 100
```

Verify:

- `ASSEMBLY_AI_API_KEY` is set (required for `TRANSCRIPT_PROVIDER=assemblyai`, the default)
- `LLM` matches the provider key you supplied
- The provider account is active and has quota
- `ffprobe`/`ffmpeg` is on PATH (or a portable build under `%LOCALAPPDATA%\Programs\ffmpeg`); `run.ps1` warns if it is missing, and rendering will fail at clip time

### Provider mismatch examples

- `LLM=openai:...` requires `OPENAI_API_KEY`
- `LLM=google-gla:...` requires `GOOGLE_API_KEY`
- `LLM=anthropic:...` requires `ANTHROPIC_API_KEY`
- `LLM=ollama:...` requires a reachable Ollama endpoint (default `http://localhost:11434/v1`, override with `OLLAMA_BASE_URL`)

If `LLM` is unset, the backend infers a default from whichever provider key is present (Google first, then OpenAI, then Anthropic), so a missing key can silently pick a provider you did not intend.

## YouTube Downloads Fail

### Symptom

YouTube tasks error early or cannot fetch source media.

### Common causes

- Expired or invalid cookies
- YouTube anti-bot restrictions
- Network restrictions
- Apify actor failures or missing `APIFY_API_TOKEN`
- `yt-dlp` fallback edge-case changes

### Checks

- Review `backend.err.log` and `worker.err.log`
- Confirm `APIFY_API_TOKEN` is set if you expect the primary download path to use Apify (`YOUTUBE_DOWNLOAD_PROVIDER=apify`)
- Confirm `APIFY_YOUTUBE_DEFAULT_QUALITY` is one of `360`, `480`, `720`, `1080`, `1440`, or `2160` (default `1080`; any other value falls back to `1080`)

### Fixes

- Verify the source URL is publicly reachable by either Apify or plain `yt-dlp`
- Retry with `APIFY_API_TOKEN` configured if the direct `yt-dlp` fallback is being rate-limited
- Remember the default download provider is `yt_dlp`; set `YOUTUBE_DOWNLOAD_PROVIDER=apify` only if you intend the Apify actor to be tried first

## Frontend Loads But Shows Errors

### Symptom

The UI opens, but parts of it fail to load or authenticated actions do not work.

### Common causes

- Backend unreachable from frontend
- `NEXT_PUBLIC_API_URL` is wrong
- auth secret or origin mismatch
- database/auth tables not initialized

### Checks

- Browser network tab
- `frontend.err.log` / `frontend.out.log`
- `backend.err.log`
- Better Auth configuration in `frontend/src/lib/auth.ts`

### Fixes

- Confirm `NEXT_PUBLIC_API_URL` points to the backend (default `http://localhost:8000`; `run.ps1` injects it at startup)
- Confirm `BETTER_AUTH_SECRET` and `BETTER_AUTH_URL` are correct
- Confirm trusted origins (CORS) include your actual frontend URL — `CORS_ORIGINS` defaults to `http://localhost:3107`; if you open the UI on a different hostname, add it to `CORS_ORIGINS`

## Cannot Sign In or Sign Up

### Common causes

- Database not ready
- Better Auth misconfiguration
- `DISABLE_SIGN_UP=true`
- Cookies blocked by wrong origin or protocol setup

### Checks

- Inspect frontend auth route behavior
- Verify auth tables exist in PostgreSQL:
  ```powershell
  $env:PGPASSWORD = 'supoclip_password'
  & 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -U supoclip -h localhost -p 5433 -d supoclip -c "\dt users; \dt session; \dt account;"
  Remove-Item Env:PGPASSWORD
  ```
- Verify app URL and auth URL match your active hostname (`NEXT_PUBLIC_APP_URL` defaults to `http://localhost:3107`)

## Fonts Are Missing or Upload Fails

### Symptom

Font picker is empty, custom fonts do not appear, or font upload fails.

### Checks

- Verify `backend\fonts\` contains valid `.ttf` or `.otf` files
- Verify `GET /fonts` returns entries
- Check `backend.err.log` for font registry or upload errors

### Hosted-mode note

Some font upload behavior can differ when monetization is enabled, so verify whether you are in self-host or hosted mode (`SELF_HOST`).

### Fixes

- Add font files to `backend\fonts\`
- Restart the backend if the registry state is stale — fonts are read from the local directory, no rebuild is needed: `.\stop.ps1`, then `.\run.ps1 -SkipBuild`
- Confirm the frontend is calling `/api/fonts` successfully (proxy route under `frontend\src\app\api\fonts`)

## Caption Templates or B-roll Are Missing

### Caption templates

If templates are missing:

- check `GET /caption-templates`
- inspect `backend.err.log`
- verify template definitions are still valid

### B-roll

If B-roll is unavailable:

- confirm `PEXELS_API_KEY` is set
- check `GET /broll/status`
- confirm the provider account has not been rate-limited or disabled

## Billing or Subscription Flow Is Broken

### Symptom

Checkout, portal access, billing summary, or subscription emails do not work.

### Checks

- Confirm `SELF_HOST=false` (monetization is only enabled when `SELF_HOST=false`)
- Confirm Stripe keys are set: `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`; price IDs via `STRIPE_PRO_PRICE_ID` / `STRIPE_SCALE_PRICE_ID` (`STRIPE_PRICE_ID` remains a legacy fallback)
- Confirm `BACKEND_AUTH_SECRET` is set
- Confirm `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `SES_FROM_EMAIL` are configured

### Webhook checks

- Verify Stripe is sending events to the frontend webhook route
- Confirm webhook signature validation succeeds
- Confirm the database can persist webhook event records (`stripe_webhook_events` table)

### Email checks

- Confirm the sender domain is verified in Amazon SES
- Check `backend.err.log` for subscription email errors

## Database Problems

### Symptom

Auth, tasks, or admin pages fail with database errors.

### Checks

- Confirm PostgreSQL is listening on 5433: `Test-NetConnection -ComputerName localhost -Port 5433`
- Confirm the schema was applied (`run.ps1` runs `init.sql` once when the `users` table is missing):
  ```powershell
  $env:PGPASSWORD = 'supoclip_password'
  & 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -U supoclip -h localhost -p 5433 -d supoclip -c "\dt"
  Remove-Item Env:PGPASSWORD
  ```
- Confirm the app is using the same connection string you expect: `DATABASE_URL` defaults to `postgresql+asyncpg://supoclip:supoclip_password@localhost:5433/supoclip`
- If `run.ps1` exits during the schema step with `init.sql failed`, the PostgreSQL admin credentials are wrong — fix `POSTGRES_ADMIN_PASSWORD` (default `postgres`) or the admin password on the service

### Fixes

If the database is disposable and you want a clean reset:

```powershell
$env:PGPASSWORD = 'postgres'   # your PostgreSQL admin password
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -U postgres -h localhost -p 5433 -d postgres -c "DROP DATABASE supoclip WITH (FORCE);"
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -U postgres -h localhost -p 5433 -d postgres -c "CREATE DATABASE supoclip OWNER supoclip;"
Remove-Item Env:PGPASSWORD
.\run.ps1 -SkipBuild
```

`run.ps1` detects the missing schema and re-applies `init.sql` with the correct ownership. Warning: this deletes persisted data.

## Redis Problems

### Symptom

Queueing, progress updates, or worker behavior break.

### Checks

```powershell
& "$env:LOCALAPPDATA\Programs\redis\redis-cli.exe" ping
Test-NetConnection -ComputerName localhost -Port 6379
```

If Redis is unavailable, task creation may still appear to work while background processing does not. `run.ps1` refuses to start when port 6379 is closed, so an app that started normally had Redis available at boot.

## Performance Is Poor

### Common causes

- Large or long source videos
- Slow external providers
- Resource constraints on your machine
- Too aggressive clip generation settings

### Practical mitigations

- Keep `DEFAULT_PROCESSING_MODE=fast`
- Lower `FAST_MODE_MAX_CLIPS`
- Use a lighter model where acceptable
- Avoid enabling B-roll unless needed
- Watch `GET /tasks/metrics/performance` for aggregate timing

## Task Detail Page Never Finishes Refreshing

### Symptom

The task page shows progress, but completed clips do not appear.

### Checks

- Verify `GET /tasks/{task_id}` returns `completed`
- Verify `GET /tasks/{task_id}/clips` returns clip data
- Check browser network logs for failed proxy requests
- Check whether the SSE stream (`GET /tasks/{task_id}/progress`) ended normally

## Admin Features Do Not Work

### Common causes

- User is not marked `is_admin`
- frontend auth state is stale
- backend admin routes are blocked by auth or proxy configuration

### Checks

- Confirm the admin page is loading with an admin session
- Confirm user records in PostgreSQL have `is_admin=true`:
  ```powershell
  $env:PGPASSWORD = 'supoclip_password'
  & 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -U supoclip -h localhost -p 5433 -d supoclip -c "SELECT id, email, is_admin FROM users;"
  Remove-Item Env:PGPASSWORD
  ```
- Confirm frontend admin proxy routes respond

## Recovery Playbook

If you just need to get back to a known good local state:

1. Stop the app processes (PostgreSQL and Redis keep running; that is expected).
   ```powershell
   .\stop.ps1
   ```
2. Review `.env` (secrets redacted) and confirm `backend\.venv` and `frontend\node_modules` exist.
3. Restart, reusing the existing frontend build for speed.
   ```powershell
   .\run.ps1 -SkipBuild
   ```
   Use a plain `.\run.ps1` if you changed frontend code or env and want a fresh `next build`; use `.\run.ps1 -Rebuild` to force a rebuild.
4. If needed, reset the database (see [Database Problems](#database-problems)) and start again.

## What to Collect Before Filing an Issue

- Exact command you ran
- `.env` values involved, with secrets redacted
- Browser error message
- Relevant `backend.err.log` and `worker.err.log` excerpts
- Whether the problem happens for YouTube, uploads, or both
- Whether `SELF_HOST` is `true` or `false`

## Related Reading

- [Setup](./setup.md)
- [Configuration](./configuration.md)
- [Architecture](./architecture.md)
