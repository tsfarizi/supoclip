# SupoClip ASR Service

Self-hosted speech-to-text service for SupoClip. It wraps **Qwen3-ASR-1.7B**
(word transcription) and **Qwen3-ForcedAligner-0.6B** (word-level timestamps)
behind a small HTTP API, and is designed as an opt-in replacement for the
default **AssemblyAI** transcription path in the SupoClip worker.

This document describes the `asr/` service as it exists in this repository
(package `supoclip-asr` 0.1.0; the HTTP API reports version `1.0.0`). It covers
operating the service in the monorepo — not the model internals.

## Overview

The service is a FastAPI app (`src/main.py`) that owns one model pair per
process, runs inference on CUDA with a **serial queue** (at most one
transcription at a time), and exposes three endpoints: `POST /v1/transcribe`,
`GET /health`, and `GET /` (plus the auto-generated `/docs` Swagger UI).

```
┌────────────────────┐  TRANSCRIPT_PROVIDER=local_asr   ┌───────────────────────────────┐
│ worker (arq)       │ ── POST /v1/transcribe ─────────▶ │ asr (FastAPI)                │
│ video_utils.py     │     ASR_BASE_URL=http://localhost:8765  │  Qwen3-ASR-1.7B     │
│                    │                                   │  Qwen3-ForcedAligner-0.6B    │
│                    │                                   │  CUDA bf16, serial queue      │
└─────────┬──────────┘                                   └───────────────────────────────┘
          │ get_video_transcript_local()
          ▼
  <video>.transcript_cache.json   (schema v2: version / words / utterances / text)
```

The worker's `get_video_transcript_local` adapter calls the ASR HTTP contract,
shapes the response into the same transcript object the AssemblyAI path uses,
and caches it in a `<video>.transcript_cache.json` file (schema version 2) for
subtitle generation. No API key, no outbound calls — transcription stays on the
host.

## Prerequisites

- **NVIDIA GPU with 8 GB VRAM** (verified with `Qwen/Qwen3-ASR-1.7B` +
  `Qwen/Qwen3-ForcedAligner-0.6B`). The model pair runs in `bfloat16` with
  batch size 1.
- **CUDA-enabled PyTorch** available to the `asr/.venv` (the `gpu` extra
  installs the `qwen-asr` stack). On Windows, verify `nvidia-smi` works and
  the CUDA wheels can be installed.
- Disk headroom: the **first `uv sync --extra gpu` is ~8–9 GB** (PyTorch CUDA
  wheels) and the **first model download is ~6 GB**, written to `HF_HOME`
  (default `asr/models`).

## Run

### Combined with the rest of the stack

`run.ps1` starts the native ASR service automatically when
`TRANSCRIPT_PROVIDER=local_asr` (or with `.\run.ps1 -IncludeAsr`), provided
the `asr/.venv` exists:

```powershell
cd asr
uv sync --extra gpu      # one-time: installs torch + qwen-asr
cd ..
.\run.ps1                # starts asr on http://localhost:8765
```

### Standalone (debugging)

```powershell
cd asr
uv sync --extra gpu
uv run uvicorn src.main:app --host 0.0.0.0 --port 8765
```

### Smoke test

```bash
bash asr/scripts/smoke.sh                 # from the repository root
bash asr/scripts/smoke.sh /path/to/audio.mp3
```

`scripts/smoke.sh` checks `GET /health` (must report `status: ok` and
`model_ready: true`), then posts an audio file to `/v1/transcribe` and requires
a non-empty `words` array with `timestamps: true`. With no audio file
argument, it synthesizes a 2-second 440 Hz tone via `ffmpeg`; if `ffmpeg` is
unavailable it performs the health check only and exits 0. Override the target
with `ASR_URL` (default `http://127.0.0.1:8765`).

### Stop

Stop the ASR process with `.\stop.ps1` (it also stops the rest of the stack)
or kill the `uvicorn ... --port 8765` process. Model weights persist under
`asr/models` (`HF_HOME`), so restarts are fast.

## API contract

### `POST /v1/transcribe`

Multipart form:

| Field           | Required | Type   | Notes                                                        |
|-----------------|:--------:|--------|--------------------------------------------------------------|
| `audio`         | yes      | file   | Uploaded audio file; any ffmpeg/decoder-supported format        |
| `language`      | no       | string | Optional language hint passed to the model                   |
| `max_new_tokens`| no       | int    | Default `256`, clamped to `[1, 2048]`                        |

Uploads are rejected with `AUDIO_INVALID` (HTTP 400) when empty and
`AUDIO_TOO_LARGE` (HTTP 413) when larger than `MAX_AUDIO_MB`. Response shape
(`TranscribeResponse`):

```json
{
  "language": "en",
  "text": "Hello world from fake ASR.",
  "words": [
    { "text": "Hello", "start_ms": 0, "end_ms": 300 },
    { "text": "world", "start_ms": 300, "end_ms": 600 }
  ],
  "timestamps": true,
  "duration_ms": 1500,
  "elapsed_ms": 0,
  "model": "Qwen/Qwen3-ASR-1.7B",
  "aligner": "Qwen/Qwen3-ForcedAligner-0.6B"
}
```

- `timestamps` is `true` only when the forced aligner produced word timing;
  otherwise `words` is empty and `duration_ms` is `0`.
- `elapsed_ms` measures the HTTP handler wall time (queue wait + inference).
- `model` / `aligner` echo the configured HF repo ids.
- `x-trace-id`: the request header (or a generated 12-hex id) is echoed on the
  response; error bodies carry the same value in `trace_id`.

### `GET /health`

```json
{ "status": "ok", "model_ready": true, "gpu": "NVIDIA GeForce RTX 4090", "vram_free_mb": 17203, "queue_depth": 0 }
```

- `status` is `ok` when `model_ready` is true, else `loading`.
- `gpu` / `vram_free_mb` are runtime values, `null` before the model loads
  (and in fake-model mode).
- `queue_depth` = waiting requests + in-flight request (the serial queue).

### `GET /`

```json
{ "name": "SupoClip ASR Service", "version": "1.0.0", "docs": "/docs", "api": "/v1/transcribe" }
```

### Errors

Every error response uses the same body shape (`ErrorResponse`):

```json
{
  "detail": "Audio exceeds 64 MB limit",
  "error_code": "AUDIO_TOO_LARGE",
  "retryable": false,
  "trace_id": "a1b2c3d4e5f6"
}
```

| HTTP | `error_code`      | Retryable | When                                                              |
|-----:|-------------------|:---------:|-------------------------------------------------------------------|
| 400  | `AUDIO_INVALID`   | no        | Uploaded audio is empty                                           |
| 413  | `AUDIO_TOO_LARGE` | no        | Upload exceeds `MAX_AUDIO_MB`                                     |
| 422  | `VALIDATION`      | no        | Malformed request (missing `audio`, bad form fields)              |
| 500  | `INFERENCE_FAILED`| yes       | Inference failure, GPU OOM, or any unhandled server error         |
| 503  | `MODEL_NOT_READY` | yes       | Model not loaded (defensive; lifespan loads it at boot)           |
| 504  | `TIMEOUT`         | yes       | Transcription exceeded `ASR_REQUEST_TIMEOUT_SECONDS`              |

Unhandled FastAPI `HTTPException`s are mapped to `VALIDATION` (status < 500)
or `INFERENCE_FAILED` (status ≥ 500) with `retryable` mirroring status ≥ 500.

## Configuration

All configuration is via environment variables (`src/config.py`). `run.ps1`
passes `ASR_MODEL`, `ASR_ALIGNER`, `ASR_REQUEST_TIMEOUT_SECONDS`,
`MAX_AUDIO_MB`, and `HF_HOME` through from the environment / `.env` when set;
`ASR_PORT` is read by the service config (default `8765`).

| Variable                   | Default                             | Meaning                                                        |
|----------------------------|-------------------------------------|----------------------------------------------------------------|
| `ASR_MODEL`                | `Qwen/Qwen3-ASR-1.7B`               | HF repo id of the transcription model                          |
| `ASR_ALIGNER`              | `Qwen/Qwen3-ForcedAligner-0.6B`     | HF repo id of the forced aligner (word timestamps)             |
| `ASR_PORT`                 | `8765`                              | Port the service binds                                         |
| `ASR_REQUEST_TIMEOUT_SECONDS` | `600`                            | Per-request inference timeout; exceeded → `TIMEOUT` (504)      |
| `MAX_AUDIO_MB`             | `64`                                | Upload size limit; exceeded → `AUDIO_TOO_LARGE` (413)          |
| `ASR_FAKE_MODEL`           | `0`                                 | **Dev-only** — `1`/`true`/`yes` loads a deterministic fake model, no GPU/torch. Never enable in production. |
| `HF_HOME`                  | `asr/models`                        | HuggingFace cache root for downloaded weights                  |

## Integration with SupoClip

The worker (not the backend API) is the ASR client. It reads the environment:

```
TRANSCRIPT_PROVIDER=local_asr
ASR_BASE_URL=http://localhost:8765
```

- **The local provider is opt-in.** `TRANSCRIPT_PROVIDER` defaults to
  `assemblyai`; set `TRANSCRIPT_PROVIDER=local_asr` in `.env` to route the
  worker's `generate_transcript` through `get_video_transcript_local` instead
  of the AssemblyAI client. `backend/src/config.py` falls back to
  `http://localhost:8765` for `ASR_BASE_URL` when unset.
- **Networking**: the worker reaches the native service at
  `http://localhost:8765` on the same host. Start ASR before the worker
  attempts a transcription (or rely on the worker's retry).
- **Audio prep**: the worker extracts a mono 16 kHz 64 kbps MP3
  (`<stem>.assemblyai.mp3`, cached next to the video) via ffmpeg, then POSTs it
  as multipart `audio` with a hardcoded `audio/mpeg` content type. If ffmpeg is
  unavailable or extraction fails, the source video file is uploaded instead.
- **Retry behavior** (worker side, `get_video_transcript_local`):
  - HTTP `400` / `413` / `422` fail immediately (permanent rejection).
  - Any other HTTP error — including `500`, `503`, `504` — and transport
    errors are retried **up to 3 attempts** with backoff (`2s` after attempt 1,
    `5s` after attempt 2), then raise.
  - The request timeout is `ASSEMBLY_AI_HTTP_TIMEOUT_SECONDS` (default `900`).
- **Timestamps missing**: when the aligner cannot produce word timing, the
  response has `timestamps: false` and an empty `words` array; the worker logs
  that subtitles will degrade to hook title only. Note the actual
  implementation behavior: the analysis transcript is built only from
  timestamped words, so with no words the formatted analysis text is **empty**
  — the raw `text` is still preserved in the transcript cache file.
- **No speaker diarization**: the ASR contract has no speaker concept. The
  worker's shim leaves `speaker` null and `utterances` empty; the downstream
  pipeline tolerates this (no `Speaker N:` prefixes, cache schema v2 writes
  `utterances: []`).

## Troubleshooting

- **CUDA unavailable at boot — fail-fast.** `ModelRuntime.load()` raises
  `RuntimeError("CUDA is not available — ASR service requires a GPU
  (fail-fast)")`; the process aborts. Check `nvidia-smi` and the torch CUDA
  build before restarting.
- **OOM on 8 GB VRAM.** During inference a GPU OOM maps to `INFERENCE_FAILED`
  (500) with guidance in the detail: set `ASR_MODEL=Qwen/Qwen3-ASR-0.6B` or
  move the aligner off-GPU (the aligner also loads onto `cuda:0` by default).
- **Long first startup is expected.** First boot downloads ~6 GB of weights
  and loads both models. Subsequent boots are much faster because weights
  persist under `HF_HOME` (`asr/models`).
- **Timestamps missing for non-11 aligner languages.** The forced aligner
  supports a limited language set (~11 languages); for other languages it
  returns no word timing, so `timestamps` is `false` and subtitles degrade to
  the hook title only. The transcript cache still stores the raw text.
- **Worker may start before ASR is ready.** The worker's 3-attempt retry
  absorbs transient connection errors, but a transcription attempt that lands
  in the model-load window (503, retryable) can still exhaust its retries —
  prefer starting ASR first (or let `run.ps1` start it before submitting
  tasks).

## Development

Inside `asr/` (Python ≥ 3.12, [uv](https://docs.astral.sh/uv/)):

```bash
uv sync                 # base deps (FastAPI app runs on CPU, fake model)
uv sync --group dev     # + pytest
uv run pytest -q        # contract tests
```

The test suite needs **no GPU**: tests inject a `FakeRuntime` through the
documented DI seam (`create_app(model_runtime=...)`) and never load real
weights.

Run the service locally on CPU with the fake model (no torch, no GPU, no
downloads):

```bash
ASR_FAKE_MODEL=1 uv run uvicorn src.main:app --host 0.0.0.0 --port 8765
```

(PowerShell: `$env:ASR_FAKE_MODEL="1"; uv run uvicorn src.main:app --port 8765`)

In fake mode `GET /health` reports `model_ready: true` with `gpu: null`, and
`/v1/transcribe` returns the deterministic 5-word "Hello world from fake ASR."
payload — enough to run `bash asr/scripts/smoke.sh` end-to-end locally.
