# Schema v2 — Audit Otoritas Skema Database SupoClip

- Tanggal audit: 2026-08-11
- Mode: READ-ONLY (tidak ada file sumber yang dimodifikasi; satu-satunya artefak yang ditulis adalah dokumen ini)
- Gradasi kepastian: **[T]** = terverifikasi langsung di file sumber (dengan referensi file:baris); **[I]** = inferensi dari bukti mekanis; **[O]** = spekulasi terbuka.

## 1. Inventaris otoritas & urutan penerapan runtime

| # | Otoritas | File | Peran |
|---|----------|------|-------|
| O1 | DDL kanonik | `init.sql` | Bootstrap manual penuh: 12 tabel, indeks, trigger. Satu-satunya sumber kolom `users.default_font_*` **[T]** init.sql:20-22 |
| O2 | ORM backend | `backend/src/models.py` | SQLAlchemy: 7 model (`User`, `AppSetting`, `RevenueCatWebhookEvent`, `Task`, `Source`, `GeneratedClip`, `ProcessingCache`) **[T]** |
| O3 | ORM frontend | `frontend/prisma/schema.prisma` | Prisma: 9 model (`User`, `AppSetting`, `Task`, `Source`, `Session`, `Account`, `Verification`, `StripeWebhookEvent`, `RevenueCatWebhookEvent`) **[T]** |
| O4 | Migrasi idempoten | `backend/src/migrations/sql/*.sql` (7 file) | Replay terurut oleh `init_db()` |

Urutan runtime `init_db()`: `Base.metadata.create_all` (hanya membuat tabel yang belum ada, TIDAK menambah kolom ke tabel yang sudah ada) → buat `schema_migrations` → replay 7 file migrasi terurut, tiap statement di-split per `;` **[T]** database.py:96-134.

**Implikasi struktural kunci:** `create_all` tidak pernah menambah kolom ke tabel eksisting dan tidak ada migrasi yang menambahkan `users.default_font_*`. Bila `init.sql` tidak dijalankan manual, ketiga kolom tersebut **permanen absen** di database apa pun yang dibangun hanya dari `create_all` + migrasi **[I]** — sementara frontend menulis kolom itu via Prisma (`frontend/src/app/api/preferences/route.ts:18,98` memakai `prisma.user`). Ini celah persistensi nyata.

## 2. Matriks divergensi

### 2.1 `users`

| Kolom | O1 init.sql | O2 models.py | O3 prisma | Status |
|---|---|---|---|---|
| id, name, email, emailVerified, image | ✓ | ✓ | ✓ | Seragam |
| createdAt, updatedAt | ✓ TIMESTAMPTZ | ✓ tz + onupdate | ✓ @updatedAt | Seragam |
| first_name, last_name, password_hash | ✓ | ✓ | ✓ | Seragam |
| **default_font_family** | ✓ DEFAULT 'TikTokSans-Regular' | **✗ TIDAK ADA** | ✓ | **models.py kekurangan** |
| **default_font_size** | ✓ DEFAULT 24 | **✗ TIDAK ADA** | ✓ | **models.py kekurangan** |
| **default_font_color** | ✓ DEFAULT '#FFFFFF' | **✗ TIDAK ADA** | ✓ | **models.py kekurangan** |
| notify_on_completion, is_admin, plan, subscription_status, subscription_provider | ✓ | ✓ | ✓ | Seragam |
| stripe_customer_id, stripe_subscription_id | ✓ UNIQUE | ✓ UNIQUE | ✓ @unique | Seragam |
| billing_period_start, billing_period_end, trial_ends_at | ✓ | ✓ | ✓ | Seragam |

- `models.py` model `User` (baris 28-88) tidak mendeklarasikan satu pun kolom `default_font_*` **[T]** models.py:28-88.
- Konsekuensi: backend SQLAlchemy tidak dapat membaca/menulis preferensi font; hanya frontend (Prisma) dan SQL langsung yang bisa.

### 2.2 `tasks`

| Kolom | O1 init.sql | O2 models.py | O3 prisma | Status |
|---|---|---|---|---|
| id, user_id (FK CASCADE), source_id (FK SET NULL) | ✓ | ✓ | ✓ | Seragam |
| generated_clips_ids | ✓ VARCHAR(36)[] | ✓ ARRAY(String(36)) | ✓ String[] | Seragam |
| status | ✓ NOT NULL DEFAULT 'pending' | ✓ | ✓ | Seragam |
| progress (CHECK 0-100), progress_message | ✓ | ✓ | ✓ | Seragam |
| font_family, font_size, font_color | ✓ | ✓ | ✓ | Seragam |
| caption_template, include_broll | ✓ | ✓ | ✓ | Seragam |
| completion_notification_sent_at | ✓ | ✓ | ✓ | Seragam |
| share_token (unique partial), share_enabled | ✓ partial index | ✓ | ✓ @unique (non-partial) | Minor: bentuk indeks berbeda (O1 partial, O3 unique penuh) **[T]** init.sql:198 vs prisma:82 |
| created_at, updated_at | ✓ | ✓ | ✓ | Seragam |
| **processing_mode** | ✓ NOT NULL DEFAULT 'fast' | ✓ | **✗ TIDAK ADA** | **prisma kekurangan** |
| **started_at** | ✓ | ✓ | **✗ TIDAK ADA** | **prisma kekurangan** |
| **completed_at** | ✓ | ✓ | **✗ TIDAK ADA** | **prisma kekurangan** |
| **cache_hit** | ✓ NOT NULL DEFAULT false | ✓ | **✗ TIDAK ADA** | **prisma kekurangan** |
| **error_code** | ✓ VARCHAR(80) | ✓ | **✗ TIDAK ADA** | **prisma kekurangan** |
| **stage_timings_json** | ✓ TEXT | ✓ | **✗ TIDAK ADA** | **prisma kekurangan** |
| **output_format** | **✗ TIDAK ADA** | **✗ TIDAK ADA** | **✗ TIDAK ADA** | **Semua otoritas kurang — hidup di Redis** |
| **add_subtitles** | **✗ TIDAK ADA** | **✗ TIDAK ADA** | **✗ TIDAK ADA** | **Semua otoritas kurang — hidup di Redis** |
| **cleanup_settings_json** | **✗ TIDAK ADA** | **✗ TIDAK ADA** | **✗ TIDAK ADA** | **Semua otoritas kurang — hidup di Redis** |

- Model Prisma `Task` (prisma:73-104) tidak memuat 6 kolom pipeline (`processing_mode`, `started_at`, `completed_at`, `cache_hit`, `error_code`, `stage_timings_json`) **[T]**.
- `output_format`, `add_subtitles`, dan setting cleanup **tidak dipersist di PostgreSQL oleh otoritas mana pun**. Bukti mekanis: `TaskService._load_task_source_settings` membaca key Redis `task_source:{task_id}` dan mengembalikan default `output_format="vertical"`, `add_subtitles=True`, `**normalize_clip_cleanup_settings()` bila Redis kosong **[T]** task_service.py:1138-1190; sisi tulis `_save_task_source_metadata` di `api/routes/tasks.py:91`. Frontend mengirim `output_format`/`add_subtitles` saat create task **[T]** frontend/src/components/home-app.tsx:585-586,608-609. Artinya konfigurasi task **volatile** — hilang saat Redis restart/evict, dan `regenerate_all_clips_for_task` (task_service.py:655-794) jatuh ke default bila metadata tidak ditemukan.
- Model Prisma `Task` tidak memiliki relasi ke `GeneratedClip`; Prisma tidak mendeklarasikan model `GeneratedClip` sama sekali **[T]** prisma:1-180.

### 2.3 `sources`

| Kolom | O1 init.sql | O2 models.py | O3 prisma | Status |
|---|---|---|---|---|
| id, title | ✓ | ✓ | ✓ | Seragam |
| type | ✓ VARCHAR(20) + CHECK IN ('youtube','video_url') | ✓ CheckConstraint | ✓ @db.VarChar(20) **tanpa CHECK** | Divergensi penegakan: Prisma dapat menulis nilai di luar domain **[T]** init.sql:39, models.py:217-219, prisma:108 |
| **url** | **✓ VARCHAR(1000) NULLABLE** | **✓ String(1000) NULLABLE** | **✓ String? NULLABLE** | **Ketiga otoritas sepakat NULLABLE — kebijakan baru menuntut NOT NULL** |
| created_at, updated_at | ✓ | ✓ | ✓ | Seragam |

- Tidak ada satu pun otoritas yang mewajibkan `url` non-null **[T]** init.sql:41 (tanpa NOT NULL), models.py:208 (`nullable=True`), prisma:110 (`String?`). Kebijakan "url harus selalu ada" adalah keputusan baru, bukan penyembuhan divergensi antar-otoritas.

### 2.4 `generated_clips`

| Kolom | O1 init.sql | O2 models.py | O3 prisma | Status |
|---|---|---|---|---|
| id, task_id (FK CASCADE), filename, file_path, start_time, end_time, duration, text, relevance_score, reasoning, clip_order | ✓ | ✓ | **✗ tabel tidak ada** | **prisma kekurangan seluruh tabel** |
| virality_score, hook_score, engagement_score, value_score, shareability_score, hook_type | ✓ | ✓ | ✗ | idem |
| **hook_title** | ✓ VARCHAR(200) | **✗ TIDAK ADA** | ✗ | **models.py kekurangan** |

- `hook_title` ditambahkan oleh migrasi 20260704_0001_clip_hook_title.sql dan ada di `init.sql:101`, tetapi model `GeneratedClip` (models.py:232-285) tidak mendeklarasikannya **[T]**. Repository mengakses kolom via raw SQL dan `getattr(row, "hook_title", None)` — ORM tidak mengetahui kolom ini **[T]** clip_repository.py:35-50,155.
- `hook_title` adalah nilai runtime aktif: dihasilkan AI (ai.py:111-113), ditulis oleh `clip_repository.create_clip` dengan parameter `hook_title` **[T]** clip_repository.py:35.

### 2.5 `processing_cache`

| O1 init.sql:107-116 | O2 models.py:288-302 | O3 prisma | Status |
|---|---|---|---|
| ✓ | ✓ | **✗ tabel tidak ada** | **prisma kekurangan seluruh tabel** |

### 2.6 `api_keys`

| O1 init.sql:179-188 | O2 models.py | O3 prisma | Status |
|---|---|---|---|
| ✓ (id, user_id FK CASCADE, name, key_hash UNIQUE, key_prefix, created_at, last_used_at, revoked_at) | **✗ TIDAK ADA** | **✗ TIDAK ADA** | **Hanya ada di SQL (O1 + migrasi 20260628)** |

- Model SQLAlchemy tidak mendeklarasikan `ApiKey` **[T]** models.py:1-302; Prisma juga tidak **[T]** prisma:1-180. Tabel dibuat hanya oleh `init.sql`/migrasi 20260628_0001_api_keys.sql **[T]**.
- Akses seluruhnya raw SQL di `ApiKeyRepository` (INSERT/SELECT/UPDATE) **[T]** api_key_repository.py:23-119. Status key **diturunkan** dari `revoked_at IS NOT NULL` → `"revoked": row.revoked_at is not None` **[T]** api_key_repository.py:122-132. Tidak ada kolom status eksplisit.
- Tidak ada ORM binding = tidak ada type safety, tetapi fungsional; MCP/backend mengautentikasi via `key_hash` dengan filter `revoked_at IS NULL` **[T]** api_key_repository.py:70-99.

### 2.7 `app_settings`

| Kolom | O1 | O2 | O3 | Status |
|---|---|---|---|---|
| setting_key, encrypted_value, prefer_admin_value, updated_by, created_at, updated_at | ✓ | ✓ | ✓ | Seragam |

- Catatan O4: migrasi 20260503_0001 membuat tabel `app_settings` **sudah menyertakan** `prefer_admin_value`, lalu 20260503_0002 menambah kolom yang sama lagi (idempoten, redundan) **[T]**. Tabel yang sama punya dua sumber kebenaran DDL (init.sql dan migrasi).

### 2.8 Webhook idempotency

| Tabel | O1 | O2 | O3 | Status |
|---|---|---|---|---|
| stripe_webhook_events | ✓ init.sql:156-160 | **✗ TIDAK ADA** | ✓ prisma:166-172 | **models.py kekurangan** |
| revenuecat_webhook_events | ✓ | ✓ | ✓ | Seragam |

- Frontend memakai `prisma.stripeWebhookEvent.create` di route billing webhook **[T]** frontend/src/app/api/billing/webhook/route.ts:221. Backend tidak memakai model ini; ketiadaan di models.py tidak mematahkan runtime, tapi menandakan O2 tidak lengkap sebagai cermin skema.

### 2.9 Better Auth (`session`, `account`, `verification`)

| Tabel | O1 | O2 | O3 | Status |
|---|---|---|---|---|
| session, account, verification | ✓ init.sql:119-153 | **✗ TIDAK ADA** | ✓ prisma:122-164 | models.py absen — **disengaja** (dikelola Better Auth di luar SQLAlchemy) |

- Dicatat untuk kelengkapan; bukan divergensi bermasalah **[I]**.

### 2.10 Ringkasan angka divergensi

- **14 item kolom** hilang dari otoritas: 3 (`users.default_font_*` dari models.py) + 6 (`tasks` pipeline dari prisma) + 1 (`generated_clips.hook_title` dari models.py) + 3 (`tasks.output_format`, `tasks.add_subtitles`, `tasks.cleanup_settings_json` dari **semua** otoritas, Redis-only) + 1 (`sources.type` CHECK tidak ditegakkan prisma).
- **4 tabel** tidak terpetakan di ORM: `api_keys` (models.py + prisma), `processing_cache` (prisma), `generated_clips` (prisma), `stripe_webhook_events` (models.py). +1 set tabel Better Auth absen di models.py (disengaja).
- **2 keputusan kebijakan** menunggu: `sources.url` NOT NULL (semua otoritas kini nullable); status `api_keys` (kini derived dari `revoked_at`).
- **Kelompok migrasi redundan** vs init.sql: 20260302 (tasks + processing_cache), 20260503_0001/0002 (app_settings), 20260507 (tasks + sources.url) — duplikasi DDL idempoten, dua sumber kebenaran.

## 3. Temuan struktural tambahan

1. **Runner `init_db` tidak bisa memperbaiki drift**: `create_all` hanya menambah tabel baru; migrasi hanya `ADD COLUMN IF NOT EXISTS`. Tidak ada mekanisme penegakan kesesuaian ORM ↔ DB, tidak ada migrasi destruktif. Drift hanya bertambah **[T]** database.py:96-134.
2. **Split SQL per `;`** di runner: statement yang mengandung titik koma di dalam literal string akan rusak (risiko untuk migrasi masa depan dengan nilai berisi `;`) **[T]** database.py:126-130.
3. **Tiga mekanisme `updated_at` paralel**: trigger `update_updated_at_column()` (init.sql:213-238), `onupdate=func.now()` di SQLAlchemy (models.py:41-46, dll), `@updatedAt` Prisma (prisma:26,93,112). Tidak destruktif (trigger menimpa), tetapi tiga jalur untuk satu fungsi.
4. **Indeks**: Prisma mendeklarasikan `@@index` pada `user_id`, `source_id`, `created_at`, `status` untuk tasks dan `created_at` untuk sources **[T]** prisma:99-102,117. `init.sql` menambah `idx_tasks_processing_mode`, `idx_tasks_completed_at`, indeks `generated_clips`, `processing_cache`, `session`, `account`, `verification`, `api_keys` yang tidak ada di Prisma — konsisten karena Prisma tidak meng-query kolom itu.

## 4. Rekomendasi DDL Schema v2

Dasar: setiap perubahan membayar kekuatan nyata (lihat kolom "Kekuatan yang dinetralkan").

### 4.1 `tasks` — persistensi konfigurasi (menutup celah Redis volatile)

```sql
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS output_format      VARCHAR(20) NOT NULL DEFAULT 'vertical',
    ADD COLUMN IF NOT EXISTS add_subtitles      BOOLEAN     NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS cleanup_settings_json JSONB;

ALTER TABLE tasks
    ADD CONSTRAINT chk_tasks_output_format
    CHECK (output_format IN ('vertical', 'vertical_pan', 'vertical_split', 'original'));

ALTER TABLE tasks
    ADD CONSTRAINT chk_tasks_cleanup_settings_json
    CHECK (jsonb_typeof(cleanup_settings_json) = 'object');
```

- Kekuatan yang dinetralkan: konfigurasi task saat ini volatile (Redis `task_source:{task_id}`) — restart/evict Redis mengembalikan semua task ke default, dan regenerasi klip diam-diam memakai default alih-alih setting pengguna **[T]** task_service.py:1138-1190. Persistensi di PostgreSQL membuat konfigurasi tahan restart dan dapat di-query.
- Domain `output_format` diambil dari `VALID_OUTPUT_FORMATS = {"vertical","vertical_pan","vertical_split","original"}` yang dipakai runtime **[T]** video_utils.py:38.
- CHECK JSONB `'object'` menjaga kolom dari skalar; normalizer `normalize_clip_cleanup_settings(cut_long_pauses, pause_threshold_ms, remove_filler_words, filtered_words)` tetap sumber kebenaran semantik **[T]** clip_cleanup.py:54.
- Pajak: dua CHECK constraint baru pada tabel panas; validasi domain ganda (app + DB). Amplop validitas: selama set format tidak berevolusi cepat; bila format baru muncul, ALTER CHECK.

### 4.2 `users` — menutup celah `create_all`

```sql
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS default_font_family VARCHAR(100) NOT NULL DEFAULT 'TikTokSans-Regular',
    ADD COLUMN IF NOT EXISTS default_font_size   INTEGER     NOT NULL DEFAULT 24,
    ADD COLUMN IF NOT EXISTS default_font_color  VARCHAR(7)  NOT NULL DEFAULT '#FFFFFF';
```

- Kekuatan yang dinetralkan: tanpa migrasi ini, DB yang dibangun tanpa `init.sql` selamanya kekurangan kolom yang ditulis frontend via Prisma (preferences route) **[T]** frontend/src/app/api/preferences/route.ts:18,98.
- Catatan: migrasi ini menuntut sinkronisasi `models.py` (tambah 3 kolom ke `User`) agar ORM backend tidak menulis/overwrite tanpa sadar — di luar scope DDL, diserahkan ke implementasi.

### 4.3 `sources.url` — kebijakan wajib ada

```sql
-- 1) Backfill data lama terlebih dahulu (nilai apa pun yang bukan NULL)
UPDATE sources SET url = 'unknown://' WHERE url IS NULL OR url = '';
-- 2) Enforce
ALTER TABLE sources ALTER COLUMN url SET NOT NULL;
```

- Kebijakan "url harus selalu ada": ketiga otoritas kini nullable **[T]**; keputusan ini menyelaraskan kebijakan bisnis (setiap source berasal dari URL) dengan skema.
- Pajak: backfill placeholder `unknown://` untuk data lama yang tidak punya URL; write path (create source / create task) wajib menyediakan url. Amplop: bila sumber non-URL muncul di masa depan (mis. upload lokal), kebijakan ini harus dibuka kembali.
- Opsi alternatif yang ditolak: membiarkan nullable dan menegakkan di aplikasi — tidak membayar karena write path (tasks API) sudah dapat memvalidasi; namun kolom tetap menerima NULL dari jalur lain. Rekomendasi tetap NOT NULL di DB karena biaya backfill rendah dan data nyata selalu ber-URL **[I]**.

### 4.4 `api_keys` — status

**Rekomendasi: pertahankan `revoked_at` sebagai status kanonik; JANGAN tambah kolom status redundan.**

- Alasan mekanis: repository sudah menurunkan `revoked` dari `revoked_at IS NOT NULL` di satu titik serialisasi **[T]** api_key_repository.py:122-132; semua query aktif memfilter `revoked_at IS NULL` **[T]** api_key_repository.py:70-99,102-119. Kolom `status VARCHAR` duplikat akan menuntut sinkronisasi dua sumber (trigger atau logika aplikasi) tanpa kekuatan yang membayarnya — redundansi state murni.
- Opsional bila pola query aktif sering (bukan sekarang): partial index
  ```sql
  CREATE INDEX IF NOT EXISTS idx_api_keys_active_user ON api_keys(user_id) WHERE revoked_at IS NULL;
  ```
  Hanya tambahkan saat metrik menunjukkan scan; saat ini `idx_api_keys_user_id` cukup **[I]**.

### 4.5 Indeks lain yang terbukti divergen

- Tidak ada indeks baru yang dibayar kekuatan. `idx_tasks_processing_mode` / `idx_tasks_completed_at` (init.sql:196-197) tidak di-query Prisma; `generated_clips`, `processing_cache`, `session`, `account`, `verification` indeks sudah ada di DB dan tidak perlu direplikasi di Prisma karena Prisma tidak memakai tabel itu. Menambah indeks tanpa query = beban tulis tanpa imbalan (Nol Spekulasi Skala).
- `tasks.share_token`: seragamkan bentuk — rekomendasi partial unique `WHERE share_token IS NOT NULL` (O1) sebagai kanonik; Prisma `@unique` penuh (prisma:82) menghasilkan indeks lebih besar untuk hasil yang sama secara semantik (NULL ganda diizinkan oleh keduanya) **[I]**.

## 5. DP-3 — Nasib model `Task` di `schema.prisma`

**Opsi A: sinkronisasi lanjut (rekomendasi). Opsi B: hapus.**

Bukti mekanis yang membatalkan premis "frontend tidak pernah akses tasks via Prisma":
- `frontend/src/app/admin/page.tsx` memakai `prisma.task.count` (93-95), `prisma.task.findMany` (108,130,170), `prisma.task.groupBy` (152) — dashboard admin nyata **[T]**.
- `frontend/e2e/global-setup.ts` memakai `prisma.task.create` (82,93), `prisma.task.update` (118), `prisma.task.deleteMany` (24) — setup test end-to-end **[T]**.
- Kolom pipeline (`processing_mode`, `started_at`, `completed_at`, `cache_hit`, `error_code`, `stage_timings_json`) tidak pernah dibaca via Prisma — konsumen admin hanya memakai `id`, `user_id`, `status`, `created_at`, `share_token` **[I]** dari grep di atas.

**Rekomendasi: Sinkronisasi lanjut (Opsi A), dengan batas yang diperkecil.** Alasan:
1. Menghapus model = error kompilasi di 2 konsumen langsung (admin dashboard + e2e setup) dan memaksa penulisan ulang keduanya — biaya pembalikan tinggi tanpa kekuatan yang membayarnya.
2. Sinkronisasi lanjut murah: tambah kolom baru Schema v2 (`output_format`, `add_subtitles`, `cleanup_settings_json`) ke model Task hanya bila admin perlu membacanya; kolom pipeline lama cukup diabaikan karena tidak dibaca.
3. Biaya sinkronisasi lanjut: 2 titik drift (models.py + prisma) harus dijaga sejajar — pajak yang sudah dibayar oleh arsitektur monorepo ini.

**Jalur evolusi (kapan Opsi B valid):** saat admin metrics pindah ke endpoint backend (mis. `get_performance_metrics` task_service.py:1115 atau API tasks) dan e2e setup memakai seeding API, `prisma.task` kehilangan konsumen → hapus model, hapus `Task[]` dari relasi `User` (prisma:51), hapus `@@index` Prisma. Amplop: bertahan selama admin dashboard & e2e setup memakai `prisma.task` **[I]**.

## 6. DP-4 — Penyimpanan `cleanup_settings`

**Opsi A: satu kolom `JSONB` (rekomendasi). Opsi B: kolom per-field (`cut_long_pauses BOOLEAN`, `pause_threshold_ms INTEGER`, `remove_filler_words BOOLEAN`, `filtered_words TEXT[]`).**

Bukti bentuk data: `normalize_clip_cleanup_settings(cut_long_pauses, pause_threshold_ms, remove_filler_words, filtered_words)` — tepat 4 field, satu di antaranya array **[T]** clip_cleanup.py:54; payload dibangun sebagai dict di task_service.py:675-680 dan dibaca sebagai dict di task_service.py:1181-1190.

Rekomendasi: **JSONB tunggal (Opsi A)**. Alasan mekanis:
1. **Sambungan natural dict↔JSONB**: service menerima/menghasilkan `Dict[str, Any]`; serialisasi ke JSONB = satu hop. Opsi B menuntut translate dict↔row di setiap sambungan repository/service — kopling tambahan yang tidak dibayar kekuatan apa pun.
2. **Sumbu perubahan tinggi pada field cleanup**: fitur muda, argumen normalizer dan perilaku terus berubah (test suite menambah kasus, `DEFAULT_FILTERED_WORDS` di clip_cleanup.py). JSONB menyerap penambahan field tanpa migrasi DDL; Opsi B = 1 migrasi per field baru.
3. **`filtered_words` adalah array**: JSONB menyimpannya natural; Opsi B butuh `TEXT[]` + konversi tipe di tiap sambungan.
4. **Tidak ada query per-field**: tidak ditemukan `WHERE cut_long_pauses` atau agregasi per-field di seluruh backend (grep `cleanup` hanya menemukan akses sebagai dict utuh) **[T]**.

Pajak Opsi A: tidak ada validasi tipe di level DB (normalizer tetap sumber kebenaran), tidak bisa indeks per-field. Pajak ini diterima karena belum ada query yang membutuhkannya.

Amplop validitas: selama tidak ada kebutuhan agregasi/analitik per-field ("berapa task memakai cut_long_pauses") dan tidak ada query filter per-field, JSONB cukup. Bila analitik per-field muncul → materialisasi kolom terpilih dengan backfill dari JSONB (jalur migrasi bernama, tanpa kehilangan data) **[I]**.

## 7. Lampiran bukti mekanis (file:baris)

| Klaim | Bukti |
|---|---|
| `users.default_font_*` hanya di init.sql + prisma, absen di models.py | init.sql:20-22; prisma:34-36; models.py:28-88 (tidak memuat) |
| 6 kolom pipeline tasks absen di prisma | prisma:73-104; models.py:161-174; init.sql:66-71 |
| `output_format`/`add_subtitles`/cleanup Redis-only | task_service.py:1138-1190; api/routes/tasks.py:91; home-app.tsx:585-586 |
| `hook_title` absen di models.py, dipakai raw SQL | init.sql:101; migrasi 20260704; models.py:259-275; clip_repository.py:35-50,155 |
| `api_keys` tanpa ORM, status derived | init.sql:179-188; migrasi 20260628; api_key_repository.py:122-132 |
| prisma.task dipakai frontend | admin/page.tsx:93-95,108,130,152,170; e2e/global-setup.ts:24-27,82,93,118 |
| `stripe_webhook_events` dipakai frontend | billing/webhook/route.ts:221; prisma:166-172; models.py absen |
| Runner create_all + migrasi, split per `;` | database.py:96-134 |
| `VALID_OUTPUT_FORMATS` | video_utils.py:38 |
| Normalizer 4 field | clip_cleanup.py:54 |

---

*Dokumen ini dihasilkan dari audit read-only. Semua rekomendasi DDL belum dieksekusi; eksekusi dan sinkronisasi ORM (models.py / schema.prisma) adalah pekerjaan implementasi di luar ruang lingkup audit.*