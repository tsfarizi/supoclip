# Clip Metadata (Description + Hashtags)

## Kontrak
- Per-clip AI description (80-300 char, 1-2 kalimat, curiosity gap, tanpa hashtag di dalam) + hashtags (3-8, lowercase `#[a-z0-9_]{3,15}`).
- `metadata_status: pending|ready|degraded|failed`, `metadata_version: clip-metadata-v1`, `metadata_prompt_version`.
- degraded = LLM gagal, fallback deterministik (keyword dari hook_title + key_topics) tetap ready-ish.

## Batas
- `ClipMetadataService` terisolasi dari `ai.py` (prompt version terpisah). Perubahan copy tidak menyentuh pipeline video.
- Kolom di `generated_clips`: `description TEXT, hashtags TEXT(JSON), metadata_status VARCHAR(20), metadata_version, metadata_prompt_version`.

## Alur
1. `VideoService.process_video_complete` → setelah `analyze_transcript` dan sebelum B-roll, panggil `ClipMetadataService.generate_batch(segments)` inline, degraded allowed.
2. `TaskService._render_stage` simpan ke `generated_clips` bersama `description/hashtags`.
3. `GET /tasks/{id}` dan `GET /tasks/shared/{token}` serialisasi `description/hashtags` via `ClipRepository`.
4. `POST /tasks/{id}/clips/{clip_id}/metadata/regenerate {platform, language}` → Redis lock `clip_metadata_lock:{clip_id} NX 60s`, generateSingle, `update_clip_metadata`, publish SSE `metadata_ready` ke `progress:{task_id}`.
5. Frontend `tasks/[id]/page.tsx` render description/hashtags + Copy + Regenerate (platform selector), subscribe SSE `metadata_ready`.

## Cache & Prompt Version
- `CLIP_METADATA_VERSION = clip-metadata-v1`. Bump → cache miss dan regenerate.

## Fallback
- `normalize_hashtags`, `sanitize_description`, `extract_keyword_fallback_hashtags` pure, tanpa LLM.

## Migrasi
- `0005_add_clip_metadata.py` + `init.sql` + `models.py`.

## Verifikasi
- `normalize_hashtags` + `sanitize_description` + `ClipMetadata` validator lulus.
- `ClipMetadataService` degraded path lulus (mock LLM down).
- `clip_repository` create/get/update + `PUBLIC_CLIP_FIELDS` include description/hashtags.
- Browser component copy/regenerate + SSE `metadata_ready` lulus.
