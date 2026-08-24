# SupoClip Quick Start Guide

Run SupoClip natively on Windows with a single command. No Docker, no containers.

## Prerequisites

1. **proto** installed — [moonrepo.dev/docs/proto/install](https://moonrepo.dev/docs/proto/install)
2. **PostgreSQL** installed and running as a service (this machine: PG18 on port **5433**)
3. **API Keys** (get these from the providers):
   - [AssemblyAI API Key](https://www.assemblyai.com/) (required for transcription)
   - At least one AI provider:
      - [OpenAI API Key](https://platform.openai.com/api-keys) (recommended)
      - [Google AI API Key](https://makersuite.google.com/app/apikey)
      - [Anthropic API Key](https://console.anthropic.com/)
      - [Ollama](https://ollama.com/) (local/self-hosted, no API key required for local)

## Quick Start (Single Command)

```powershell
.\run.ps1
```

That's it! The script will:
- Install the pinned toolchain (node/bun/python/uv) via proto
- Start Redis and ffmpeg from portable user-scope installs when missing
- Bootstrap the `supoclip` role, database, and schema on PostgreSQL
- Install Python and frontend dependencies
- Start the worker, backend API, and frontend (logs in `.local/logs/`)
- Print the URLs to access everything

Stop everything with `.\stop.ps1`.

## First Time Setup

### 1. Configure Environment Variables

Edit the `.env` file in the project root and add your API keys:

```bash
# Required for video transcription (or use TRANSCRIPT_PROVIDER=local_asr with the native ASR service)
ASSEMBLY_AI_API_KEY=your_assemblyai_key_here

# Choose one AI provider for clip selection
GOOGLE_API_KEY=your_google_key_here

# Configure which AI model to use
LLM=google-gla:gemini-3-flash-preview

# OR use Ollama locally
# LLM=ollama:gpt-oss:20b
# OLLAMA_BASE_URL=http://localhost:11434/v1

# Optional: Amazon SES for waitlist + subscription lifecycle emails
# AWS_REGION=us-east-1
# AWS_ACCESS_KEY_ID=your_aws_access_key_id
# AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key
```

### 2. Start SupoClip

```powershell
.\run.ps1
```

### 3. Access the Application

- **Frontend**: http://localhost:3107
- **Backend API**: http://localhost:8000
- **API Documentation**: http://localhost:8000/docs

## Manual Commands

If you prefer to run each piece yourself:

```powershell
# One-time toolchain
proto install

# Backend API + worker (two terminals)
cd backend
uv sync
uv run uvicorn src.app:app --host 0.0.0.0 --port 8000                 # terminal 1 (shim src.main_refactored:app compat)
uv run arq src.workers.tasks.WorkerSettings                          # terminal 2 (shim src.worker.WorkerSettings compat)

# Frontend
cd frontend
bun install
bun run dev

# MCP server (optional)
cd mcp
uv sync
uv run supoclip-mcp
```

## Environment Configuration

### Required Variables

| Variable | Description | Where to Get |
|----------|-------------|--------------|
| `ASSEMBLY_AI_API_KEY` | Speech-to-text transcription | https://www.assemblyai.com/ |
| `LLM` | AI model identifier | e.g., `google-gla:gemini-3-flash-preview` or `ollama:gpt-oss:20b` |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TRANSCRIPT_PROVIDER` | `assemblyai` | `assemblyai` or `local_asr` (native ASR service on :8765) |
| `DATABASE_URL` | `postgresql+asyncpg://supoclip:supoclip_password@localhost:5433/supoclip` | Backend PostgreSQL connection |
| `REDIS_HOST` / `REDIS_PORT` | `localhost` / `6379` | Redis connection |
| `TEMP_DIR` | `backend/data` | Shared working dir (run.ps1 overrides with an absolute path) |
| `BETTER_AUTH_SECRET` | dev secret | Auth secret (change in production!) |
| `GOOGLE_API_KEY` | - | For Google Gemini models |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | For local/self-hosted Ollama endpoint |

### Local Transcription (ASR)

The worker calls the native ASR service at `http://localhost:8765` when
`TRANSCRIPT_PROVIDER=local_asr`. Set it up once (GPU recommended):

```powershell
cd asr
uv sync --extra gpu
# then run.ps1 starts it automatically when TRANSCRIPT_PROVIDER=local_asr
```

For a GPU-free smoke test, set `ASR_FAKE_MODEL=1` in the environment when starting the ASR service.

## Architecture

SupoClip runs 4 native processes (managed by `run.ps1`):

1. **Frontend** (Next.js 15) - Port 3107
2. **Backend** (FastAPI + Python) - Port 8000
3. **Worker** (arq, async Redis queue) - processes video in the background
4. **Infra**: PostgreSQL (5433) + Redis (6379)

All processes share PostgreSQL, Redis, and the `TEMP_DIR` filesystem tree.

## Troubleshooting

### Services not starting?

1. **Logs**: every process writes to `.local/logs/` (`backend.*.log`, `worker.*.log`, `frontend.*.log`).
2. **PostgreSQL**: verify the service is running (`Get-Service postgresql-x64-18`) and listening on 5433.
3. **Redis**: `run.ps1` starts a portable redis-server under `%LOCALAPPDATA%\Programs\redis` when needed.

### API Keys not working?

1. Verify keys are set in `.env` file
2. Ensure no extra spaces around the `=` sign
3. Restart the stack: `.\stop.ps1; .\run.ps1`

### Database issues?

Reset the database schema:

```powershell
# WARNING: This deletes all data!
$env:PGPASSWORD='postgres'; & 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -U postgres -h localhost -p 5433 -d postgres -c "DROP DATABASE supoclip;"
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -U postgres -h localhost -p 5433 -d postgres -c "CREATE DATABASE supoclip OWNER supoclip;"
.\run.ps1   # re-applies init.sql
```

## Next Steps

- Read the full documentation in `docs/`
- Check out the API docs at http://localhost:8000/docs
- View example clips in the frontend
- Customize fonts by adding TTF files to `backend/fonts/`
- Add transition effects by adding MP4 files to `backend/transitions/`

## Getting Help

- Check logs in `.local/logs/`
- View API documentation: http://localhost:8000/docs
- Report issues: Create a GitHub issue with logs and error messages
