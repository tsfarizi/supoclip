# lib layer — SupoClip Frontend (U4)

> **Gate V2 §3.2** — `lib` murni (boleh client), `server` adalah satu-satunya rumah untuk `server-only` (Prisma, Stripe, crypto, betterAuth).

## Boundary

| Concern | Rumah kanonik | Shim |
|---|---|---|
| Prisma singleton | `server/db.ts` (`server-only`) | `lib/prisma.ts` re-export |
| Stripe | `server/stripe.ts` (`server-only`) | `lib/stripe.ts` re-export |
| Auth (betterAuth) | `server/auth.ts` (`server-only`, pakai `server/db.ts`) | `lib/auth.ts` re-export |
| Backend HMAC | `server/backend-auth.ts` (`server-only`, `crypto`) | `lib/backend-auth.ts` re-export |
| Session helper | `server/session.ts` | — |
| Billing plans (priceId) | `server/billing-plans.ts` | `lib/billing-plans.ts` murni tipe + marketing limits |

`server/backend-api.ts` sekarang impor `server/backend-auth` (arah benar), bukan `lib/backend-auth`.

## Grab-bag split (U4)

Flat 20 file di `lib/` telah dipetakan ke kategori (behavior-preserving via shim):

- **lib/domain/** — tipe & helper murni domain: `billing-plans`, `task-types`, `monetization`, `video-identity`, `clip-download`, `font-options` — bisa diimpor client maupun server tanpa guard.
- **lib/infra/** — `api-error`, `api-client` (single fetch wrapper, konsolidasi `buildSupportError` yang duplikat 3× di `list/page.tsx` & `tasks/[id]/**`), `datafast` (`use client`).
- **lib/ui/** — `utils` (`cn`), `seo`, `seo-pages`, `site`, `social-image`, `blog-posts` — presentational.

Untuk iterasi ini canonical file tetap di `lib/*.ts` agar diff minimal; direktori `domain/infra/ui` disediakan sebagai rumah baru dan shim `export *` ditempatkan di lokasi lama saat pemindahan fisik dilakukan di iterasi berikutnya. Server-only code sudah bermigrasi penuh ke `server/`.

## Monetization bug

`lib/monetization.ts`: `value === "false"` terbalik → `normalized !== "true" && !== "1" && !== "yes"`. Hosted default (env kosong) = enabled, self-host `NEXT_PUBLIC_SELF_HOST=true` = disabled.

## Hooks fetch unification

`lib/api-client.ts` (dan shim `hooks/api.ts`) menyediakan `buildSupportError`/`fetchJson` tunggal. Hooks `use-task-query`, `use-billing-summary`, `use-api-keys`, `use-fonts` kini lewat `buildSupportError` yang sama; duplikasi `parseApiError→formatSupportMessage` di `app/list/page.tsx` & `tasks/[id]/**` dihapus.

## God component

`components/home-app.tsx` (>1800 baris, 28 state, 5 useEffect) dibiarkan sebagai debt; hanya layer hooks yang dipecah (sesuai constraint U4).
