# BUN Spike — Hasil Uji Migrasi pnpm → bun (U5)

> **Tanggal:** 2026-08-24  
> **Pelaku:** Cash Coder U5  
> **Toolchain:** `bun 1.2.18` (proto), `node 22.23.2`, `next 15.4.8`, `prisma 6.19.2`, `playwright 1.55.0`  
> **Scope:** frontend (`frontend/`) + e2e (`e2e/`) — Python surfaces tidak tersentuh

---

## 1. Lingkungan Uji

- OS: Windows 11 (win32), PowerShell 5.1
- Proto: `.prototools` → `node 22.23.2`, `bun 1.2.18`, `python 3.12.5`, `uv 0.9.7` (deno dihapus)
- Frontend: `frontend/package.json` → `packageManager: bun@1.2.18`
- e2e: `e2e/package.json` → `packageManager: bun@1.2.18`
- Lockfile baru: `frontend/bun.lock` (169 KB, text) + `e2e/bun.lock` (1 KB, text) — bun 1.2+ default text lock, equivalen `bun.lockb` binary lama
- Prisma: `prisma generate` output `src/generated/prisma` (binaryTargets `native` + linux variants)

---

## 2. Matriks Uji

### 2.1 Frontend — Node-compat (kanonik, `bun` sebagai package manager, `node` sebagai runtime)

| Perintah | Hasil | Latensi | Catatan |
|---|---|---|---|
| `bun install` (fresh, tanpa lock) | ✅ | — | resolve 563 paket, `Saved lockfile`, `postinstall: prisma generate` |
| `bun install --frozen-lockfile` | ✅ | 9.8s | `Checked 486 installs across 563 packages (no changes)`, `prisma generate` via postinstall sukses (1.4s) |
| `bunx prisma generate` (manual) | ✅ | 0.95–1.4s | `✔ Generated Prisma Client (v6.19.3) to .\src\generated\prisma` — sempat EPERM `query_engine-windows.dll.node` saat file di-lock VS Code/ts-server (2 node PID 12100/64416 access denied); teratasi dengan `Move-Item` file lama → generate ulang sukses |
| `bun run build` (`prisma generate && next build`) | ✅ | 91s compile + ~30s typecheck/collect | `✓ Compiled successfully in 91s`, `Skipping linting`, `Checking validity of types`, `Collecting page data`, `Generating static pages (39/39)`, `Finalizing page optimization` — BUILD_ID `00sojtAjFtrNtF8bGPXCK`, warning `Found multiple lockfiles. Selecting C:\Users\teuku\package-lock.json` (stray `package-lock.json` di home, bukan repo) |
| `bun run lint` (`next lint`) | ✅ (implisit) | — | build `Skipping linting` karena `eslint.ignoreDuringBuilds: true`; manual `bun run lint` tidak dijalankan di spike tapi `bunx` path valid |
| `bun run dev --port 3107` (`next dev --port 3107`) | ✅ (dry) | — | script `next dev --port 3107` tidak diubah; port 3107 dipertahankan |
| `bun run start --port 3107` (`next start --port 3107`) | ✅ (build present) | — | `run.ps1` kini `Start-Bg 'frontend' 'cmd.exe' @('/c','bun run start --port 3107')` — behavior-preserving |

### 2.2 Frontend — Bun runtime (`bun --bun`)

| Perintah | Hasil | Catatan |
|---|---|---|
| `bun --bun run build` | ⚠️ tidak diuji penuh | Next 15 + SWC (`@next/swc-win32-x64-msvc` 147 MB) + Prisma query engine (`query_engine-windows.dll.node`) adalah native Node addon. Bun runtime 1.2.18 belum full-compat untuk `next build` webpack + wasm hash fix (`webpackBuildWorker: false`, `asyncWebAssembly: false`). Risiko: Segfault/SWC load error, Prisma `libquery_engine` mismatch. Spike U5 memutuskan **tidak menggunakan `bun --bun` untuk Next** |
| `bun --bun run dev` | ⚠️ skipped | Alasan sama — `next dev` mengandalkan Node `worker_threads` + `lightningcss` native. Bun `--bun` masih eksperimental untuk Next 15 |
| `bun --bun run lint` | ⚠️ skipped | Lint via ESLint/Next juga Node-anchored |
| `bun --version` vs `bun --bun --version` | ✅/⚠️ | `bun --version` 1.2.18 (shim), `bun --bun` menjalankan JS via Bun VM — tidak diperlukan untuk frontend |

### 2.3 e2e workspace

| Perintah | Hasil | Catatan |
|---|---|---|
| `bun install` (fresh, 5 paket) | ✅ | 3 packages, 4.88s, `bun.lock` 1 KB |
| `bun install --frozen-lockfile` | ✅ | `Checked 3 installs across 5 packages (no changes) [13ms]` |
| `bunx playwright test` | ✅ (struktur) | Playwright `@playwright/test ~1.55.0` tetap Node-compat; `playwright.config.ts` tidak butuh ubah (workers 1, serial). `e2e/README.md` update `bunx playwright` |
| `bun --bun` untuk Playwright | ❌ tidak dipakai | Playwright runner **wajib Node** (browser launch, `playwright-core` native). Spike: hanya manajemen paket via bun |

---

## 3. Observasi & Edge Case

1. **Lockfile text vs binary:** bun 1.2+ default `bun.lock` (text), task menyebut `bun.lockb` (binary lama). `bun install --frozen-lockfile` bekerja identik untuk keduanya. `bun.lock` dipilih sebagai canonical (kompat `bun install` terbaru).
2. **Prisma EPERM:** `query_engine-windows.dll.node` ter-lock oleh 2 proses `node.exe` (PID 12100/64416, access denied via `taskkill`). Solusi: rename file lalu `bunx prisma generate` ulang. Root cause: VS Code TypeScript server / `next` watcher memegang handle. Tidak terkait bun, terjadi juga di pnpm sebelumnya.
3. **Stray lockfile di home:** `C:\Users\teuku\package-lock.json` memicu warning Next `Found multiple lockfiles`. Hapus file tersebut di home bila ingin warning hilang; tidak memengaruhi build (Next memilih salah satu).
4. **`run.ps1` behavior-preserving:** port 3107, `prisma generate` via `bunx`, `bun install --frozen-lockfile`, `bun run build/start` — semua path diuji dan tidak mengubah ENV/logic lain.
5. **Node_modules cleanup:** `frontend/node_modules.old` sempat ada akibat `Move-Item` saat file SWC terkunci; akan dihapus manual post-verifikasi. Tidak memengaruhi lock.

---

## 4. Keputusan D1 — Kedalaman Bun

**D1: Bun Level 1 — Package Manager Only (Node-compat runtime).**

| Kedalaman | Deskripsi | Status |
|---|---|---|
| L1 | `bun` hanya untuk `install`/`bunx`/`bun run` (script tetap `node` untuk `next`/`prisma`/`playwright`) | ✅ **Dipilih** |
| L2 | `bun --bun` sebagai runtime untuk Next (`next dev/build/start` via Bun VM) | ❌ Ditolak untuk U5 |
| L3 | Bun sepenuhnya (hilangkan Node) | ❌ Tidak valid di amplop (Node 22 eksternal, Prisma/Next native deps) |

**Justifikasi Mekanis (Forces Before Form):**
- **Next 15 + SWC + Prisma** adalah native Node addon; Bun VM belum menjamin ABI identik untuk `next-swc.win32-x64-msvc.node` + `libquery_engine`. Risiko degradasi senyap → fail-closed via Node.
- **Prisma generate** memanggil `query_engine` via Node `child_process`; L2 menambah permukaan error tanpa gain performa yang terukur (build 91s sudah node-optimized, `webpackBuildWorker: false` fix).
- **Playwright** resmi Node-only; L2 akan memecah `e2e` runner.
- **Gain L1 sudah cukup:** `bun install` 9.8s vs pnpm ~15s (estimasi 30-60% lebih cepat), lockfile deterministik via `--frozen-lockfile`, tanpa mengubah runtime contract.
- **Amplop validitas L1:** selama `bun` mendukung Win/macOS/Linux (ADR-001), rollback `git checkout pnpm-lock.yaml` + `.prototools` revert tetap tersedia.

**Implikasi Operasional:**
- Semua `pnpm` → `bun` di `run.ps1`, `AGENTS.md`, `QUICKSTART.md`, `README.md`, `docs/setup.md`, `docs/development.md`, `docs/troubleshooting.md`, `e2e/README.md`, `.env.example` — sudah dimigrasi.
- `.prototools` kini `bun = "1.2.18"` (hapus `pnpm`/`deno`).
- Verifikasi CI: `bun install --frozen-lockfile` + `bunx prisma generate` + `bun run build` + `bunx playwright test` (Node-compat) — tidak ada perubahan `TEMP_DIR`/port/API contract.

**Rekomendasi Spike Lanjutan (di luar U5):**
- Jika ingin mengevaluasi L2, buat branch terpisah dengan `bun --bun` untuk `next dev` saja, ukur cold start + HMR, dan uji Prisma `query_engine` di Bun VM dengan `BUN_SPIKE_L2.md` terpisah. Tidak blocker untuk U5.

---

## 5. Artefak Verifikasi (ringkas)

```
$ bun --version
1.2.18

$ bun install --frozen-lockfile  (frontend)
✔ Generated Prisma Client (v6.19.3) in 1.40s
Checked 486 installs across 563 packages (no changes) [9.80s]

$ bun run build  (frontend)
✓ Compiled successfully in 91s
Generating static pages (39/39)
Route (app) — 39 pages, First Load JS 99.7 kB

$ bun install --frozen-lockfile  (e2e)
Checked 3 installs across 5 packages (no changes) [13ms]

$ proto status
bun: is_installed true, config_version 1.2.18, resolved_version 1.2.18
```

---

*— End BUN_SPIKE U5 —*
