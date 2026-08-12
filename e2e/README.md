# SupoClip E2E (Playwright)

Suite E2E Playwright untuk memverifikasi fungsionalitas SupoClip terhadap
**stack native yang sedang berjalan** (dikelola `run.ps1`) — suite ini
**tidak** menyalakan stack sendiri dan **tidak** punya `webServer`.

## Prasyarat

1. Stack native berjalan:
   - Backend API: `http://localhost:8000` (`/health` → healthy)
   - Frontend (production build): `http://localhost:3107`
   - Redis: `:6379`
   - Jalankan: `.\run.ps1` dari root repo (sekali). Jangan menyalakan stack
     melalui suite ini.
2. Node/pnpm dari toolchain proto (lihat `.prototools`): `proto install`.
3. Browser Playwright terpasang. Folder `e2e/` memakai `@playwright/test
   ~1.55.0` (versi sama dengan frontend) sehingga browser yang sudah ada di
   `%LOCALAPPDATA%\ms-playwright` ter-reuse. Bila terjadi mismatch browser:
   ```
   pnpm exec playwright install chromium
   ```

## Menjalankan

Dari folder `e2e/`:

```
pnpm install
pnpm exec playwright test          # semua spec, headless
pnpm exec playwright test --headed # tampilkan browser
pnpm exec playwright test --reporter=list --project=chromium specs/tasks.spec.ts
pnpm run test:report               # buka HTML report (hasil run terakhir)
```

`global-setup.ts` berjalan otomatis sebelum suite dan:
- mengecek health backend + frontend (gagal → pesan "jalankan .\\run.ps1 dulu"),
- melakukan provision user login via better-auth REST (idempotent): memakai
  user fixture `e2e-user@supoclip.test` bila ada, atau membuat user baru
  `e2e-suite-<timestamp>@supoclip.test` bila tidak ada,
- menulis `.credentials.json` (kredensial untuk spec), `.fixtures.json`
  (ketersediaan task completed fixture; spec detail/share skip bila tidak ada),
  dan `storageState.json` (sesi login hasil provisioning). Spec yang butuh auth
  memakai `test.use({ storageState })` sehingga HANYA 1-2 request sign-in per
  run — penting karena better-auth menerapkan rate limit sign-in (429 "Too
  many requests") bila dipukul berulang dalam jendela pendek.

> File `.credentials.json`, `.fixtures.json`, dan `storageState.json` dibuat
> oleh global-setup dan di-ignore oleh git. Jangan di-commit.

## Struktur

```
e2e/
├── package.json            # name: supoclip-e2e, scripts test/test:headed/test:report
├── playwright.config.ts    # testDir ./specs, baseURL :3107, workers 1, serial
├── global-setup.ts         # health check + provision user (REST, idempotent)
├── README.md
├── fixtures/
│   └── dummy.mp4           # file teks 1KB bernama .mp4 untuk alur upload
└── specs/
    ├── helpers.ts          # signIn, findCompletedTaskHref, baca kredensial/fixtures
    ├── public.spec.ts      # landing, /sign-in, /sign-up, /blog, /ai-video-clipper, share invalid
    ├── auth.spec.ts        # sign-in salah/benar, sign-out
    ├── home.spec.ts        # header, form create, validasi submit, kartu task
    ├── tasks.spec.ts       # /list, detail task completed (clips, skor, transkrip, aksi)
    ├── share.spec.ts       # buat share link → publik → disable → 404
    ├── settings.spec.ts    # preferensi + lifecycle API key
    ├── admin.spec.ts       # gate admin untuk user non-admin
    └── upload.spec.ts      # upload dummy → task queued → cleanup
```

## Batas (known limitations)

- **Worker tidak berjalan & tanpa ASSEMBLY_AI_API_KEY** → pipeline proses video
  penuh (download → transkripsi → analisis → render) **tidak diuji**. Task baru
  akan tetap `queued`. Suite fokus pada fungsionalitas UI/alur memakai fixture
  task completed yang sudah ada, dan `upload.spec.ts` hanya membuktikan upload
  + pembuatan task (lalu membersihkannya).
- **JANGAN hapus/ubah data user fixture** `e2e-user@supoclip.test`. Test hanya
  membuat state sementara (API key direvoke, task upload dihapus, share
  di-disable) dan meninggalkan data user dalam kondisi semula.
- **Skor virality = 0 di fixture**: klip fixture menyimpan `virality_score = 0`,
  dan UI hanya merender badge/breakdown virality bila skor `> 0`. Spec detail
  memverifikasi skor relevansi (`%`) yang memang tampil; UI virality tidak
  diharapkan tampil untuk fixture ini (lihat `tasks.spec.ts`).
- **`/placeholder-video.jpg` 404** (isu diketahui, sudah dilacak): poster video
  di halaman detail/share tidak ada. Test **tidak** digagalkan karenanya; bila
  404 muncul di console, dicatat di output spec.
- **Kebocoran sesi DB saat worker memproses upload dummy**: `upload.spec.ts`
  sengaja mengupload file teks bernama `.mp4`; bila worker aktif, worker akan
  mencoba memprosesnya dan gagal. Jalur error worker ini meninggalkan sesi
  Postgres `idle in transaction` (query `generated_clips`) yang tidak
  ditutup — setelah beberapa run, pool DB penuh dan endpoint backend yang
  menyentuh DB (mis. `/tasks/`) hang padahal `/health` tetap healthy. Ini
  **bug ketahanan aplikasi (bukan bug suite)**. Pemulihan:
  ```
  psql -h localhost -p 5433 -U supoclip -d supoclip -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle in transaction';"
  ```
  atau restart backend + worker (mis. lewat `stop.ps1` + `run.ps1`). Suite
  tetap deterministik; `upload.spec` menghapus task yang dibuatnya sendiri.
- Concurrency dimatikan (`workers: 1`, `fullyParallel: false`) karena spec
  berbagi DB user yang sama dan urutan state (share/API key/task) harus serial.

## Menambah spec

1. Buat `specs/<area>.spec.ts` dengan assertion **observable** (teks/URL/role),
   satu klaim per test, tanpa membaca internal komponen.
2. Bila butuh auth: panggil `signIn(page, credentials.email, credentials.password)`
   dari `helpers.ts` (kredensial dari `.credentials.json`).
3. Bila butuh task completed: `findCompletedTaskHref(page)`; bila ketersediaan
   fixture belum tentu ada, baca `.fixtures.json` dan `test.skip(...)` dengan
   reason (lihat `tasks.spec.ts`).
4. Jangan menyalakan stack dan jangan menyentuh backend langsung tanpa
   signature HMAC (backend menolak request tanpa header `x-supoclip-*`).
5. Jalankan `pnpm exec playwright test specs/<area>.spec.ts` dan pastikan
   deterministik (lolos berulang kali dalam urutan apa pun).
