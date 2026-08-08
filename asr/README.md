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
container, runs inference on CUDA with a **serial queue** (at most one
transcription at a time), and exposes three endpoints: `POST /v1/transcribe`,
`GET /health`, and `GET /` (plus the auto-generated `/docs` Swagger UI).

```
┌────────────────────┐  TRANSCRIPT_PROVIDER=local_asr   ┌───────────────────────────────┐
│ worker (arq)       │ ── POST /v1/transcribe ─────────▶ │ asr (FastAPI)                │
│ video_utils.py     │     ASR_BASE_URL=http://asr:8765  │  Qwen3-ASR-1.7B              │
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
- **Podman machine** with **≥ 10 GiB memory**, and **CDI configured**:
  `nvidia-container-toolkit` installed and the CDI spec present at
  `/etc/cdi/nvidia.yaml` inside the machine.
- Verify GPU passthrough before first start:

  ```bash
  podman run --rm --device nvidia.com/gpu=all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
  ```

- Disk headroom: the **first image build is ~8–9 GB** (PyTorch CUDA 12.4 base
  image) and the **first model download is ~6 GB**, written into the
  `asr_models` named volume (`HF_HOME=/models` inside the container).

## Run

### Combined with the rest of the stack

The `asr` service is defined **inside** the root `docker-compose.yml` (single
compose file — no overlay). From the repository root:

```bash
podman compose up -d --build
```

Appending `asr` (`... up -d --build asr`) builds and starts only the ASR
service.

### Standalone (debugging)

The `asr` service joins the default network (named `supoclip-network`), so it
can be run alone while the rest of the stack is stopped:

```bash
podman compose up -d asr
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

### Stop / teardown / logs

```bash
podman compose stop asr                   # stop only ASR
podman compose down                       # full stack teardown

podman logs -f supoclip-asr
```

`down` without `-v` keeps the `asr_models` volume, so downloaded weights
survive rebuilds.

## API contract

### `POST /v1/transcribe`

Multipart form:

| Field           | Required | Type   | Notes                                                        |
|-----------------|:--------:|--------|--------------------------------------------------------------|
| `audio`         | yes      | file   | Uploaded audio file; any container-supported format          |
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

All configuration is via environment variables (`src/config.py`). The compose
file passes `ASR_MODEL`, `ASR_ALIGNER`, `ASR_REQUEST_TIMEOUT_SECONDS`, and
`MAX_AUDIO_MB` through `${VAR:-default}` from the host environment / `.env`
(the defaults shown below are the compose defaults); `ASR_PORT` and `HF_HOME`
are fixed in the compose file.

| Variable                   | Default                             | Meaning                                                        |
|----------------------------|-------------------------------------|----------------------------------------------------------------|
| `ASR_MODEL`                | `Qwen/Qwen3-ASR-1.7B`               | HF repo id of the transcription model                          |
| `ASR_ALIGNER`              | `Qwen/Qwen3-ForcedAligner-0.6B`     | HF repo id of the forced aligner (word timestamps)             |
| `ASR_PORT`                 | `8765`                              | Port the service config reads. The container always binds 8765 — the `Dockerfile` CMD passes `--port 8765` explicitly; changing it requires editing the CMD and the compose port mapping |
| `ASR_REQUEST_TIMEOUT_SECONDS` | `600`                            | Per-request inference timeout; exceeded → `TIMEOUT` (504)      |
| `MAX_AUDIO_MB`             | `64`                                | Upload size limit; exceeded → `AUDIO_TOO_LARGE` (413)          |
| `ASR_FAKE_MODEL`           | `0`                                 | **Dev-only** — `1`/`true`/`yes` loads a deterministic fake model, no GPU/torch. Never enable in production. Not set by compose |
| `HF_HOME`                  | `/models`                           | HuggingFace cache root; mounted as the `asr_models` volume     |

## Integration with SupoClip

The worker (not the backend API) is the ASR client. Its root-compose
environment declares:

```yaml
- TRANSCRIPT_PROVIDER=${TRANSCRIPT_PROVIDER:-assemblyai}
- ASR_BASE_URL=${ASR_BASE_URL:-http://asr:8765}
```

- **The local provider is opt-in.** `TRANSCRIPT_PROVIDER` defaults to
  `assemblyai`; set `TRANSCRIPT_PROVIDER=local_asr` in your environment /
  `.env` to route the worker's `generate_transcript` through
  `get_video_transcript_local` instead of the AssemblyAI client.
  `backend/src/config.py` also falls back to `http://localhost:8765` for
  `ASR_BASE_URL` outside compose.
- **Service discovery**: the `asr` service joins the compose default network
  (named `supoclip-network`), so the worker reaches it at `http://asr:8765`
  (mapped to `127.0.0.1:8765` on the host for local smoke tests).
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
  (fail-fast)")`; the lifespan aborts and the container exits. Check
  `podman logs supoclip-asr` and fix the CDI/device passthrough before
  restarting (`restart: unless-stopped` will otherwise retry in a loop).
- **OOM on 8 GB VRAM.** During inference a GPU OOM maps to `INFERENCE_FAILED`
  (500) with guidance in the detail: set `ASR_MODEL=Qwen/Qwen3-ASR-0.6B` or
  move the aligner off-GPU (the aligner also loads onto `cuda:0` by default).
- **Long first startup is expected.** First boot downloads ~6 GB of weights
  and loads both models; the healthcheck's `start_period: 300s` absorbs this,
  so the container may report `unhealthy` for several minutes before settling.
  Subsequent boots are much faster because the `asr_models` volume persists.
- **Timestamps missing for non-11 aligner languages.** The forced aligner
  supports a limited language set (~11 languages); for other languages it
  returns no word timing, so `timestamps` is `false` and subtitles degrade to
  the hook title only. The transcript cache still stores the raw text.
- **Worker may start before ASR is ready.** The root compose declares no
  `depends_on` between `worker` and `asr`. The worker's 3-attempt retry
  absorbs transient connection errors, but a transcription attempt that lands
  in the model-load window (503, retryable) can still exhaust its retries —
  prefer starting `asr` first (or the combined command above).
- **podman-compose device passthrough fallback.** If your podman-compose
  setup does not forward the `devices: ["nvidia.com/gpu=all"]` entry, run the
  container directly:

  ```bash
  podman run -d --name supoclip-asr \
    --device nvidia.com/gpu=all \
    --network supoclip-network \
    -v asr_models:/models \
    -p 127.0.0.1:8765:8765 \
    -e HF_HOME=/models \
    supoclip-asr
  ```

- **Slow/stalled image pulls from docker.io inside the podman machine.**
  On WSL-backed machines, large registry transfers can stall while small ones
  succeed. Lowering the machine's eth0 MTU to 1400 unblocks them:

  ```bash
  podman machine ssh -- sudo ip link set dev eth0 mtu 1400
  ```

  The change is lost on machine restart; re-apply after
  `podman machine stop/start` (or add it to the machine's startup config).

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
