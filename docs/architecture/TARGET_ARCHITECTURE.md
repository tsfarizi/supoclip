# TARGET ARCHITECTURE — SupoClip V2 Gate

> **Status:** DRAFT — Gate Ratifikasi V2 (U2)  
> **Penulis:** Cash Architect (Boundary Architect persona)  
> **Tanggal:** 2026-08-24  
> **Konteks:** Audit U1 selesai. Dokumen ini adalah **spesifikasi target & konvensi** yang mengikat. Tidak mengubah kode production — hanya kontrak struktural yang akan diratifikasi user sebelum eksekusi U3/U4/U5.  
> **Prinsip turunan bentuk:** *Forces Before Form* — setiap batas membayar kekuatan aktif atau dihapus.

---

## Daftar Isi
0. [Peta Topologi Target](#0-peta-topologi-target)  
1. [Struktur Paket Domain Backend Target](#1-struktur-paket-domain-backend-target)  
2. [Matriks Legalitas Impor Antar-Lapisan](#2-matriks-legalitas-impor-antar-lapisan)  
3. [Aturan Boundary Server-Only Frontend](#3-aturan-boundary-server-only-frontend)  
4. [Toolchain & Perintah (konsumsi parsial U5)](#4-toolchain--perintah-konsumsi-parsial-u5)  
5. [Taksonomi Error Backend & Frontend](#5-taksonomi-error-backend--frontend)  
6. [ADR — Keputusan Struktural Besar](#6-adr--keputusan-struktural-besar)  
7. [Invarian Behavior-Preserving & Titik Masuk Beku](#7-invarian-behavior-preserving--titik-masuk-beku)  
8. [Gate Ratifikasi V2 Checklist](#8-gate-ratifikasi-v2-checklist)

---

## 0. Peta Topologi Target

### 0.1 Graf Makro

```text
Browser -> Frontend (Next.js) -> server (server-only) -> Backend FastAPI (app.py) -> Redis/Postgres
api/routes -> services -> repositories -> infra/db & cache
workers -> services
```

Leaf shared/config, shared/errors, shared/observability tidak impor infra/domain.
Dinding penahan: config<->runtime_settings cycle, database+redis, models, api->services->repositories, TEMP_DIR.

---

## 1. Struktur Paket Domain Backend Target

### 1.1 Forces

- Volatilitas caption/AI tinggi -> domain/caption+ingestion terisolasi
- B-roll/SFX eksternal -> broll/sfx sub-paket behind interface
- Skala render paralel (max_jobs=4) -> domain/media hot path terpisah
- Konsistensi billing atomik -> domain/billing owns BillingService
- Blast radius video_utils 5482 baris -> pecah bertahap

### 1.2 Tabel 31 Modul

| # | Modul lama | Paket baru | Domain | Strategi |
|---|---|---|---|---|
| 1 | config.py | shared/config/__init__.py | shared | leaf-first |
| 2 | runtime_settings.py | shared/config/runtime_settings.py | shared | leaf-first lazy |
| 3 | database.py | infra/db/__init__.py | infra | leaf-first |
| 4 | infra/redis_client.py | infra/cache/redis_client.py | infra | leaf-first |
| 5 | errors.py | shared/errors/__init__.py | shared | leaf-first |
| 6 | observability.py | shared/observability/__init__.py | shared | leaf-first |
| 7 | models.py | infra/db/models/__init__.py + split | infra | god split |
| 8 | media_tools.py | infra/media_tools.py | infra | leaf-first |
| 9 | model_assets.py | infra/assets/model_assets.py | infra | leaf-first |
| 10 | task_validation.py | domain/task/validation.py | task | leaf-first |
| 11 | clip_metadata.py | domain/media/clip_metadata.py | media | leaf-first |
| 12 | clip_source_map.py | domain/media/source_map.py | media | leaf-first |
| 13 | clip_cleanup.py | domain/media/cleanup.py | media | leaf-first |
| 14 | ai.py | domain/caption/ai/__init__.py | caption | god split |
| 15 | caption_templates.py | domain/caption/templates.py | caption | leaf-first |
| 16 | emoji_captions.py | domain/caption/emoji.py | caption | leaf-first |
| 17 | font_registry.py | domain/caption/fonts/registry.py | caption | leaf-first |
| 18 | video_utils.py | domain/media/video/__init__.py | media | god prio #1 (5482 baris) |
| 19 | clip_editor.py | domain/media/editing/__init__.py | media | god prio #2 (479 baris) |
| 20 | face_tracking.py | domain/media/video/face_tracking.py | media | leaf-first |
| 21 | visual_signals.py | domain/media/video/visual_signals.py | media | leaf-first |
| 22 | transition_spec.py | domain/media/video/transitions/spec.py | media | leaf-first |
| 23 | transition_engine.py | domain/media/video/transitions/engine.py | media | leaf-first |
| 24 | youtube_utils.py | domain/ingestion/youtube/service.py | ingestion | god split |
| 25 | apify_youtube_downloader.py | domain/ingestion/youtube/apify.py | ingestion | leaf-first |
| 26 | video_cache.py | domain/ingestion/cache/video_cache.py | ingestion | leaf-first |
| 27 | broll.py | domain/ingestion/broll/service.py | ingestion | leaf-first |
| 28 | freesound.py | domain/ingestion/sfx/freesound.py | ingestion | leaf-first |
| 29 | sound_effect_cache.py | domain/ingestion/sfx/cache.py | ingestion | leaf-first |
| 30 | auth_headers.py | domain/auth/headers.py | auth | leaf-first |
| 31 | admin_auth.py | domain/auth/admin.py | auth | leaf-first |

Tambahan paket: services/* -> domain/*/service.py, repositories/* -> infra/db/repositories/*, workers/* tetap, api/routes/* tetap, migrations/* -> infra/db/migrations/*, main_refactored.py -> app.py + shim, worker_main.py -> worker.py.

### 1.3 Leaf-first vs God

Leaf-first (1 commit): errors, observability, task_validation, clip_metadata/source_map, caption_templates/emoji, font_registry, media_tools, model_assets, auth_headers, admin_auth, apify, video_cache, broll, freesound, sound_effect_cache, transition_spec/engine, face_tracking, visual_signals, database, redis_client, config+runtime_settings (pair lazy).

God strangler: video_utils (transcript/cache, subtitle/ass, render/pipeline, crop/face), clip_editor (ops/trim/split/merge/captions/export), ai (llm), youtube_utils, models. Shim re-export 1 rilis.

---

## 2. Matriks Legalitas Impor

### 2.1 Arah

api -> services -> repositories -> infra/db & cache
shared/config leaf tidak impor infra/domain
Infra impor shared saja
Repositories impor infra/db + shared
Domain boleh impor repositories, infra, shared
API impor domain/service + shared + infra DI, dilarang repositories langsung
Workers impor domain/service + infra + shared, tidak api
Migrations hanya infra/db + models

### 2.2 Matriks

| Pengimpor \ Diimpor | shared/config | shared/errors | infra/db | infra/cache | repositories | domain service | api | workers |
|---|---|---|---|---|---|---|---|---|
| shared/config | — | OK | lazy | lazy | NG | NG | NG | NG |
| infra/db | OK | OK | — | NG | NG | NG | NG | NG |
| infra/cache | lazy | OK | NG | — | NG | NG | NG | NG |
| repositories | OK | OK | OK | NG | — | NG | NG | NG |
| domain service | OK | OK | via repo | via infra | OK | OK | NG | NG |
| api/routes | OK | OK | OK DI | OK DI | NG | OK | — | NG |
| workers | OK | OK | OK | OK | NG via service | OK | NG | — |
| migrations | NG | NG | OK | NG | NG | NG | NG | — |

OK=legal NG=ilegal lazy=lazy di dalam fungsi.

Guards: import-linter, ruff TID, circular detector, rg migrations isolation.

### 2.4 Anti-cycle

config tidak top-level import runtime_settings (lazy di _get_runtime_setting).
runtime_settings tidak top-level import infra (lazy di publish_settings_changed).
infra/cache lazy get_config di _connection_kwargs.

---

## 3. Boundary Server-Only Frontend

### 3.1 Satu Rumah

| Concern | Rumah kanonik | Hapus/shim |
|---|---|---|
| Prisma singleton | server/db.ts (server-only) | lib/prisma.ts hapus; lib/auth.ts tidak new PrismaClient |
| Stripe | server/stripe.ts | lib/stripe.ts shim |
| Session | server/session.ts | — |
| Billing plans | server/billing-plans.ts + lib/billing-plans.ts public | lib pure, server env-aware |
| Backend auth HMAC | server/backend-auth.ts (crypto Node) | lib/backend-auth.ts hapus |

server/db.ts:
```ts
import "server-only";
import { PrismaClient } from "../generated/prisma";
const g = globalThis as unknown as { prisma?: PrismaClient };
export const prisma = g.prisma ?? new PrismaClient();
if (process.env.NODE_ENV !== "production") g.prisma = prisma;
export default prisma;
```

### 3.2 lib vs server vs hooks

lib pure, boleh client. server server-only, prisma/stripe. hooks client use-task-query etc. components/features. app/api handler tipis <=40 baris.

Guard ESLint no-restricted-imports @/server/* + import server-only.

### 3.3 Handler Tipis

```ts
import { NextResponse } from "next/server";
import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";
export async function GET() {
  const session = await getServerSession();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const upstream = await fetchBackend("/tasks/", { method: "GET", userId: session.user.id, cache: "no-store" });
  return createProxyResponse(upstream);
}
```

### 3.4 Error Tunggal

lib/api-error.ts parseApiError, formatSupportMessage, ApiErrorInfo. Semua hook wajib lewat itu. Backend envelope { detail, trace_id } + x-trace-id.

### 3.5 Before->After

2 PrismaClient -> server/db.ts; stripe re-export -> server/stripe; billing split -> lib pure + server env; backend-auth crypto -> server-only; fetch duplikasi -> hooks; god home-app -> features/* <300.

---

## 4. Toolchain

### 4.1 .prototools

Before: node 22.23.2, pnpm 10.27.0, python 3.12.5, deno 2.9.5 drift, uv 0.9.7
After: node 22.23.2, bun 1.2.18, python 3.12.5, uv 0.9.7 (deno hapus)

### 4.2 Lockfile

pnpm + pnpm-lock.yaml -> bun + bun.lock (text, bun 1.2+ canonical; equivalen bun.lockb). bun install --frozen-lockfile, bunx prisma generate. Migrasi bun import -> bun install -> build.

### 4.3 Perintah Kanonik

Frontend: bun install && bun run dev (3107), bun run build/start, bun run lint
Backend API: uv run uvicorn src.app:app --reload --port 8000
Worker: uv run arq src.workers.tasks.WorkerSettings
Full: run.ps1 / scripts/run.sh atau bun run dev:all

### 4.4 run.ps1 Migration

run.ps1 tetap kanonik Windows-native 343L (behavior-preserving). Evaluasi shim `scripts/run.mjs` cross-platform ditunda ke iterasi berikutnya — spec vs implementasi disinkronkan: run.ps1 shim tidak wajib di V2, migrasi toolchain fokus pada bun (`bun install --frozen-lockfile`, `bunx prisma generate`, `bun run build/start`).

### 4.5 TEMP_DIR

Single absolute tree Path(os.getenv("TEMP_DIR","backend/data")).resolve(), sub-tree uploads/clips/broll/exports/fonts. Hapus temp/graphify-out + .gitignore.

---

## 5. Taksonomi Error

### 5.1 Backend shared/errors

TaskProcessingError -> Download, Transcription, Analysis, Render, Cancelled; InvalidSource 400; Duplicate 409; Billing 402; Auth 401/403; NotFound 404.

### 5.2 HTTP Mapping

InvalidSource 400, NotFound 404, Auth 401, Forbidden 403, Duplicate 409, Billing 402 {code:SUBSCRIPTION_REQUIRED}, Download etc 500 + error_code, Validation 422, Unhandled 500, semua + trace_id header.

### 5.3 Frontend lib/api-error.ts

ApiErrorInfo {message, traceId}, parseApiError, formatSupportMessage, buildSupportError. Forward x-trace-id via createProxyResponse.

---

## 6. ADR

ADR-001 pnpm->bun: install 30-60% cepat, pajak bun.lockb binary. Amplop valid selama bun support Win/macOS/Linux. Rollback git checkout pnpm-lock.

ADR-002 main_refactored->app.py: nama sementara bocor 3 tempat, pajak 2 shim 1 rilis, kanonik uvicorn src.app:app.

ADR-003 TEMP_DIR single tree: drift relatif vs absolute, pajak Path.resolve, amplop local FS, evolusi StorageProvider S3.

ADR-004 Prisma singleton server/db.ts: 2 client pool tidak sinkron + crypto bocor, pajak server-only fail-closed.

ADR-005 Lazy cycle break: cycle top-level ImportError, pajak 1 baris lazy per call, evolusi Config DI di create_app.

---

## 7. Invarian & Titik Masuk Beku

### 7.1 Invarian 11

I-01 API route kontrak tidak berubah, I-02 Frontend URL tetap, I-03 lifecycle queued->processing->completed via CAS, I-04 billing atomik SELECT FOR UPDATE + duplicate index, I-05 envelope trace_id, I-06 SSE events, I-07 file_path absolut TEMP_DIR, I-08 HMAC SHA256 TTL300, I-09 render settings Schema v2 single authority, I-10 Prisma schema tetap, I-11 upload direct S3 vs proxy.

### 7.2 Frozen Entrypoints

Backend API src.app:app shim main_refactored, Worker WorkerSettings, Frontend layout.tsx, DB init_db shim, Redis get_redis_client shim, Auth getSession via server/session.ts. Shim sampai V2+1.

### 7.3 Amplop Validitas

media vs caption <100 job/jam else pecah queue; config lazy <1ms else DI; bun Node22 else revert; TEMP_DIR local <20GB else S3; server/db RSC else Accelerate; thin handler <30 endpoint else validation; god <6000 baris else iris lanjutan.

### 7.4 Pytest Gate — Narrow Scope (U8 → U9 preservasi)

Gate coverage backend `≥65%` preservasi asli repo **narrow-scope** `auth+billing` saja:
`--cov=src.auth_headers --cov=src.services.billing_service` (`backend/pyproject.toml` `addopts`). Ekspansi ke `--cov=src` **ditunda** hingga god modules `video_utils` (5482 baris) / `ai` ter-strangle dan test seam tersedia. Keputusan dicatat sebagai amplop validitas U9; tidak mengubah perilaku — hanya menunda perluasan threshold untuk menghindari false-fail pada modul yang belum terdekomposisi.

---

## 8. Gate Checklist

- [ ] 1 Paket domain 31 modul disetujui
- [ ] 2 Matriks legalitas disetujui
- [ ] 3 Boundary server-only disetujui
- [ ] 4 Toolchain pnpm->bun disetujui
- [ ] 5 Taksonomi error disetujui
- [ ] 6 ADR 5 disetujui
- [ ] 7 Invarian & frozen disetujui
- [ ] 8 Urutan U3 leaf-first, U4 frontend, U5 toolchain+strangler

Lampiran A Audit Simetris: video_utils strangler shared leaf dulu, config lazy check_lazy.py, bun.lockb conflict frozen, server-only dual guard, run.mjs single source, shim hit CI.

Lampiran B U1->V2 mapping tabel lengkap di dokumen penuh.

*— End V2 Gate —*
