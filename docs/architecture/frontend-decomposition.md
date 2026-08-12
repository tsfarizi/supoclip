# Frontend Decomposition — A14b Structural Refactor

Status: DESAIN (read-only). Unit eksekusi: A14b. Baseline perilaku: T9 (browser regression hijau).
Dokumen ini adalah kontrak struktural: hooks, komponen, kontrak props/state, urutan refactor, dan kriteria penerimaan. Tidak berisi implementasi.

---

## 0. Ringkasan Eksekutif

Tiga surface frontend memanggul 4 tanggung jawab yang sama (auth, fetching/SSE, form/dialog, editor clip) dan mengimplementasi ulang fitur editor clip yang identik secara endpoint:

| Endpoint | tasks/[id] (detail) | tasks/[id]/edit | home (create) |
|---|---|---|---|
| PATCH /api/tasks/:id/clips/:clipId (trim) | handleTrimClip (string offset) | handleTrim (trimRange) | — |
| POST /api/tasks/:id/clips/:clipId/split | handleSplitClip | handleSplit | — |
| POST /api/tasks/:id/clips/merge | handleMergeClips (tanpa transition) | handleMerge (dengan transition) | — |
| PATCH /api/tasks/:id/clips/:clipId/captions | handleUpdateCaptions | handleUpdateCaptions | — |
| GET /api/tasks/:id/clips/:clipId/export?preset= | handleExportClip (server-side) | handleExport (client-side mediabunny) | — |
| POST /api/tasks/:id/settings vs POST /api/tasks/create | handleApplyProjectSettings | — | handleSubmit (payload serupa) |

Fetch yang diimplementasi ulang 2-3 kali: GET /api/tasks/, GET /api/tasks/:id, GET /api/tasks/:id/clips, GET /api/fonts + injeksi @font-face, GET {NEXT_PUBLIC_API_URL}/caption-templates, GET /api/tasks/billing-summary, SSE /api/tasks/:id/progress (2 mode), pola buildSupportError (3x didefinisikan ulang).

Keputusan inti: bentuk target adalah hooks kepemilikan data (pemilik fetch/state/mutasi) + komponen render murni (pemilik JSX). Batas ini mengisolasi dua sumbu perubahan: (1) logika editor clip yang berevolusi cepat di 2+ surface, (2) layout/UX per surface yang berbeda-beda. Satu implementasi per operasi; surface memilih bentuk UI-nya sendiri.
---

## 1. Inventaris

### 1.1 home-app.tsx (1710 baris, frontend/src/components/home-app.tsx)

Auth & shell: useSession (auth-client), isAdmin (cast is_admin), handleSignOut (signOut + window.location.href = /sign-in).

State (28):
- Form source: url, sourceType (youtube|upload), fileName, fileRef (ref, uncontrolled file), fileInputRef, error, isLoading
- Progress (lokal, tanpa SSE): progress, statusMessage, currentStep, sourceTitle
- Font: fontFamily, fontSize, fontColor (null = template default), availableFonts, fontSearch, fontLoadError, isUploadingFont, fontUploadInputRef, showCustomizeCaptions
- Caption template: captionTemplate, availableTemplates, outputFormat (4 nilai), addSubtitles, includeBroll, pexelsConfigured
- Cleanup: cutLongPauses, pauseThresholdMs (string), removeFillerWords, filteredWords
- Task list: latestTask, tasks, isLoadingLatest, billingSummary, mobileMenuOpen

Effects (5): broll status (GET /api/broll/status), refreshFonts + injeksi @font-face (GET /api/fonts), caption templates (GET {apiUrl}/caption-templates), latest task (GET /api/tasks/), billing summary (GET /api/tasks/billing-summary).

Fetch/mutasi: requestUploadAuthorization (POST /api/upload/authorization), uploadVideoFile (direct-to-S3) vs uploadVideoFileViaProxy (POST /api/upload), handleFontUpload (POST /api/fonts/upload), handleSubmit (POST /api/tasks/create + track(task_created) + redirect window.location.href).

Derivasi: normalizeVideoIdentity + ACTIVE_TASK_STATUSES untuk deteksi duplikat; previewFont* (fallback ke template); generationRequiresUpgrade, generationGateMessage, generationControlsDisabled, duplicateTask/duplicateMessage, canUploadCustomFonts.

JSX: Header desktop + mobile menu (~210 baris), Latest Generation Banner (~50), form create + style/captions + customize captions (~560), step-progress panel (~50), phone LivePreview frame (~220), caption info panel (~35).
### 1.2 app/tasks/[id]/page.tsx (1602 baris)

Auth: useSession — hanya sebagai guard di mutation (bukan gate render; gate via middleware).

State (33): task, clips, isLoading, error, progress, progressMessage; header: isEditing, editedTitle, showDeleteDialog; clip ops: deletingClipId, isDeleting, selectedClipIds, editingClipId, startOffset, endOffset, splitTime, captionText, captionPosition, highlightWords, exportPreset; share: shareState, isRevokingShare; project settings: projectFontFamily/Size/Color, projectCaptionTemplate, projectIncludeBroll, projectCutLongPauses, projectPauseThresholdMs, projectRemoveFillerWords, projectFilteredWords, isApplyingSettings, settingsSheetOpen, availableFonts, deletingFontName, availableTemplates; hasTriggeredAutoRefresh (ref).

Effects (3): initial fetch (fetchTaskStatus — task + clips, retry 404 x5), fonts + templates load, SSE /api/tasks/:id/progress (gated pada status queued/processing; event: status, progress, clip_ready, close, error).

Mutations (13): edit title (PATCH), delete task (DELETE), delete clip (DELETE), trim (PATCH), split (POST), merge (POST), update captions (PATCH), apply settings (POST /settings), delete font (DELETE /api/fonts/:name), export clip (GET export?preset=, blob download), download clip (preset original = link langsung, else export), copy share (POST /share + clipboard fallback), revoke share (DELETE /share).

JSX: header (edit title, badges, share, cancel/resume, Open Editor), progress dots + live clips grid (duplikat render ClipCard mode live), Project Settings Sheet, ClipCard completed mode (player, virality breakdown 4 sub-skor, transcript, download+preset, Edit panel trim/split/caption), 2 AlertDialog.

### 1.3 app/tasks/[id]/edit/page.tsx (1069 baris)

Auth: useSession — guard mutation.

State (30): task, clips, selectedClipId, mergeSelection, isLoading, isSaving, error, transitionSpec, availableTransitions; editor: trimRange ([start,end] dtk), splitTime, captionText, captionPosition, highlightWords (array), subtitleSize, subtitleY, captionSaved, clipRenderProgress; playback: volume, isMuted, playbackRate, videoFx (6 bidang), currentTime, isPlaying; export: exportPreset (tiktok default), exportProgress; videoRef.

Effects (4): fetchEditorData (task + clips + selectedClipId preservasi), transitions (GET /api/transitions), sync state saat selectedClip berubah (trimRange/splitTime/caption/clear), sinkronisasi video volume/mute/rate, SSE /api/tasks/:id/progress?mode=edit (hanya event clip_render).

Mutations (5, semua via withSaving = set isSaving + action + refetch): trim, split, merge (+transition), update captions (+captionSaved flash), export (client-side mediabunny: OffscreenCanvas burn subtitle + gain audio, download _browser.mp4).

Helper murni di luar komponen: MIN_GAP_SECONDS, DEFAULT_VIDEO_FX, EXPORT_DIMENSIONS, clamp, getSubtitleWordsAtTime (kemunculan kata per waktu).

JSX: header + ExportBar (preset + tombol export), VideoCanvas card (video + playhead slider + trim slider), Fine Controls (split/audio/FX/reset), CaptionEditor (script + posisi + ukuran + offset + highlight words), ClipTimeline (grid clip + merge bar + transition select).

### 1.4 app/list/page.tsx (749 baris)

Auth: useSession (gate render). State: tasks, selectedTaskIds, isLoading, error, batchNotice, activeBatchAction, showDeleteDialog.
Fetch: fetchTasksList (GET /api/tasks/) — duplikat dari home-app latest task fetch (beda: di sini full list + selection prune).
Mutasi: runBatchAction (generic Promise.allSettled) untuk cancel/resume/delete massal.
JSX: header + counts, batch command bar, delete dialog. Batas bawah prioritas — file tidak raksasa; hook useTaskList hanya diekstrak bila list surface mendapat fitur baru.
### 1.5 Lib yang sudah ada (tidak boleh diduplikasi ulang)

| Lib | Isi | Dipakai di |
|---|---|---|
| lib/video-identity.ts | normalizeVideoIdentity | home |
| lib/font-options.ts | FontOptionsPayload, FONT_TEMPLATE_DEFAULT_VALUE, FONT_SIZE_OPTIONS, buildFontOptionsPayload | home, tasks/[id] |
| lib/api-error.ts | parseApiError, formatSupportMessage, ApiErrorInfo | home, tasks/[id], edit, list |
| lib/billing-plans.ts | formatBillingPlanName, isPaidBillingPlan, getPublicBillingPlans, PAID_PLAN_IDS | home, settings |

### 1.6 Komponen yang sudah ada

| Komponen | Status | Catatan |
|---|---|---|
| components/dynamic-video-player.tsx | Siap | dipakai tasks/[id] (2 mode). Props: src, poster, autoPlay, muted, loop, className |
| components/transcript-preview.tsx | Siap | dipakai tasks/[id] (2 mode). Props: text, clipTitle |
| components/font-select-option.tsx | Siap | dipakai tasks/[id] settings sheet. Props: font, isDeleting, onDelete |
| components/ui/* | Siap | shadcn-style primitives |

### 1.7 Duplikasi logika editor antar surface (dipetakan)

| Operasi | Detail surface | Edit surface | Titik ekstraksi |
|---|---|---|---|
| Trim | PATCH dengan Number(startOffset), Number(endOffset) string; error -> alert() | PATCH dengan offset dari trimRange (float 2 desimal); error -> throw | useClipEditor.trimClip(clipId, startOffset, endOffset) — normalisasi ke number, throw Error |
| Split | POST split_time: Number(splitTime); error -> alert | POST split_time: value.toFixed(2); error -> throw | useClipEditor.splitClip(clipId, splitTime) |
| Merge | POST tanpa transition; guard selectedClipIds.length < 2 | POST dengan transition; guard sama | useClipEditor.mergeClips(clipIds, transition?) |
| Caption | PATCH caption_text/position/highlight_words (split koma) | PATCH payload sama (array sudah bersih) | useClipEditor.updateCaptions(clipId, payload) |
| Export | Server-side GET blob; preset original = link langsung | Client-side mediabunny (canvas + gain) | TIDAK dibagi — dua mekanisme berbeda; hanya konstanta preset yang dibagi |
| Settings | POST /settings dengan buildFontOptionsPayload + normalisasi pause/filtered | — (create form POST /create dengan payload serupa) | useTaskSettings — state + normalisasi + build payload per endpoint |
| Refetch setelah mutasi | fetchTaskStatus() | withSaving -> fetchEditorData() | callbacks onAfterMutation |

### 1.8 Duplikasi fetch lintas surface

| Fetch | home-app | tasks/[id] | edit | settings | list |
|---|---|---|---|---|---|
| GET /api/tasks/ | latestTask | — | — | — | fetchTasksList |
| GET /api/tasks/:id | — | fetchTaskStatus | fetchEditorData | — | — |
| GET /api/tasks/:id/clips | — | fetchTaskStatus | fetchEditorData | — | — |
| GET /api/fonts + inject @font-face | refreshFonts (otf->opentype) | loadFonts (tanpa inject) | — | loadFonts (selalu truetype) | — |
| GET {apiUrl}/caption-templates | loadTemplates | loadTemplates | — | — | — |
| GET /api/tasks/billing-summary | fetchBillingSummary | — | — | fetchBillingSummary | — |
| SSE /api/tasks/:id/progress | — | mode default (5 event) | mode=edit (clip_render) | — | — |
| buildSupportError (parseApiError+format) | inline | useCallback | useCallback | — | module-level fn |
---

## 2. Boundary Target

Lokasi baru: frontend/src/hooks/ (baru) dan frontend/src/components/features/ (baru). Tipe bersama: frontend/src/lib/task-types.ts (baru).

### 2.1 Tipe bersama (lib/task-types.ts) — DIBAGI

Semua interface Clip/TaskDetails/Task/BillingSummary/FontOption/CaptionTemplate dipindah ke satu modul. Tiga file saat ini mendefinisikan varian berbeda dari Clip dan TaskDetails; tipe bersama adalah union dari bidang yang dipakai, dengan bidang opsional:

```ts
export interface Clip {
  id: string;
  filename: string;
  file_path?: string;
  video_url: string;
  start_time: string;
  end_time: string;
  duration: number;
  text: string;
  relevance_score: number;
  reasoning?: string;
  clip_order: number;
  created_at: string;
  virality_score?: number;
  hook_score?: number;
  engagement_score?: number;
  value_score?: number;
  shareability_score?: number;
  hook_type?: string | null;
  hook_title?: string | null;
}

export interface TaskDetails {
  id: string;
  user_id?: string;
  source_id?: string;
  source_title: string;
  source_type: string;
  status: string;
  progress?: number;
  progress_message?: string;
  clips_count: number;
  created_at: string;
  updated_at?: string;
  font_family?: string | null;
  font_size?: number | null;
  font_color?: string | null;
  caption_template?: string;
  cut_long_pauses?: boolean;
  pause_threshold_ms?: number;
  remove_filler_words?: boolean;
  filtered_words?: string[];
  include_broll?: boolean;
  share_enabled?: boolean;
}

export type OutputFormat = "vertical" | "vertical_pan" | "vertical_split" | "original";
export type ExportPreset = "original" | "tiktok" | "reels" | "shorts";
export type CaptionPosition = "top" | "middle" | "bottom";

export interface CaptionTemplate {
  id: string;
  name: string;
  description: string;
  animation?: string;
  font_family?: string;
  font_size?: number;
  font_color?: string;
}

export interface BillingSummary {
  monetization_enabled: boolean;
  plan: string;
  subscription_status: string;
  subscription_provider?: string | null;
  usage_count: number;
  usage_limit: number | null;
  remaining: number | null;
  can_create_task?: boolean;
  upgrade_required: boolean;
  reason?: string | null;
}

export interface FontOption {
  name: string;
  display_name: string;
  format?: string;
  scope?: "system" | "user";
}
```

Utilitas bersama (pindah dari file raksasa): getClipUrl (normalisasi /api prefix), formatDuration, clamp, dan helper skor warna (getScoreColor, getViralityColor, getViralityBgColor, getHookTypeLabel) — dipakai tasks/[id] dan edit. buildSupportError menjadi helper di lib/api-error.ts (sudah ada primitifnya).
### 2.2 useTaskPolling (hooks/use-task-polling.ts) — DIBAGI (detail + edit)

Membungkus EventSource /api/tasks/:id/progress untuk dua mode. Satu implementasi; mode mengubah event yang dilanggan dan callback yang dipanggil.

```ts
interface UseTaskPollingOptions {
  taskId: string | undefined;
  mode?: "default" | "edit";        // default: task detail; edit: ?mode=edit
  enabled?: boolean;                  // caller gate: hanya true saat status aktif (queued/processing) untuk mode default
  onProgress?: (data: { progress: number; message?: string }) => void;
  onStatus?: (status: string) => void;              // mode default: update status task
  onClipReady?: (clip: Clip) => void;               // mode default: merge incremental clips
  onCompleted?: () => void;                         // mode default: fetchTaskStatus + auto-refresh
  onError?: (message: string) => void;              // mode default: setError
  onClipRender?: (clipId: string, progress: number) => void; // mode edit: clip_render
}

interface UseTaskPollingResult {
  connected: boolean;
}

function useTaskPolling(options: UseTaskPollingOptions): UseTaskPollingResult;
```

Kontrak perilaku yang dipertahankan:
- Mode default: connect hanya bila enabled=true (caller menghitung dari task.status); langganan event status, progress, clip_ready, close, error; tutup pada unmount/status change; auto-refresh setelah completed (delay 700ms via triggerAutoRefresh — dipindah ke hook sebagai opsi autoRefreshMs).
- Mode edit: connect selalu (task selesai tetap dibuka); hanya event clip_render; error diabaikan (EventSource reconnect otomatis).
- Pembersihan eventSource pada cleanup; re-connect saat taskId/status berubah.

### 2.3 useClipEditor (hooks/use-clip-editor.ts) — DIBAGI (detail + edit)

Pemilik mutasi trim/split/merge/caption. Normalisasi input ke number; error selalu throw Error (caller memilih alert vs inline).

```ts
interface UseClipEditorOptions {
  taskId: string | undefined;
  userId: string | undefined;
  onAfterMutation?: () => Promise<void> | void;  // refetch setelah mutasi sukses
}

interface ClipEditorApi {
  trimClip(clipId: string, startOffset: number, endOffset: number): Promise<void>;
  splitClip(clipId: string, splitTime: number): Promise<void>;
  mergeClips(clipIds: string[], transition?: string): Promise<void>;
  updateCaptions(clipId: string, payload: {
    caption_text: string;
    position: CaptionPosition;
    highlight_words: string[];
  }): Promise<void>;
  isMutating: boolean;  // satu flag untuk semua operasi (detail: isDeleting terpisah; edit: isSaving)
}

function useClipEditor(options: UseClipEditorOptions): ClipEditorApi;
```

Kontrak endpoint (tidak berubah):
- trim: PATCH /api/tasks/:id/clips/:clipId body { start_offset, end_offset } (number)
- split: POST /api/tasks/:id/clips/:clipId/split body { split_time } (number)
- merge: POST /api/tasks/:id/clips/merge body { clip_ids, transition? }
- captions: PATCH /api/tasks/:id/clips/:clipId/captions body { caption_text, position, highlight_words }

Pembulatan: detail memakai Number() tanpa pembulatan; edit memakai toFixed(2). Hook memakai aturan edit (toFixed(2)) untuk konsistensi — beda 0.001 dtk tidak mengubah output render; dicatat sebagai keputusan normalisasi.
### 2.4 useTaskQuery (hooks/use-task-query.ts) — DIBAGI (detail + edit)

Pemilik fetch task + clips. Menghilangkan duplikasi fetchTaskStatus (detail) vs fetchEditorData (edit). Perbedaan perilaku lama dipertahankan via opsi:

```ts
interface UseTaskQueryOptions {
  taskId: string | undefined;
  retryOn404?: boolean;      // detail: true (retry 5x); edit: false
  mergeIncremental?: boolean; // detail: merge clips saat processing; edit: replace
}

interface TaskQueryResult {
  task: TaskDetails | null;
  clips: Clip[];
  isLoading: boolean;
  error: string | null;
  refetch(): Promise<void>;  // tanpa set isLoading (dipakai setelah mutasi)
}

function useTaskQuery(options: UseTaskQueryOptions): TaskQueryResult;
```

Perilaku yang dipertahankan: hidrasi state settings dari task (font/size/color/template/broll/cleanup) dilakukan oleh pemanggil via callback onTaskLoaded — hook tidak tahu soal ProjectSettingsSheet; detail dan edit masing-masing meneruskan onTaskLoaded untuk mengisi state mereka.

### 2.5 useTaskSettings (hooks/use-task-settings.ts) — DIBAGI (home create + detail sheet)

Pemilik state font/caption/cleanup yang sama di dua surface, plus normalisasi payload per endpoint. State null = template default (kontrak sama dengan lib/font-options).

```ts
interface TaskSettingsState {
  fontFamily: string | null;
  fontSize: number | null;
  fontColor: string | null;
  captionTemplate: string;
  includeBroll: boolean;
  cutLongPauses: boolean;
  pauseThresholdMs: string;
  removeFillerWords: boolean;
  filteredWords: string;
}

interface TaskSettingsApi {
  state: TaskSettingsState;
  setters: Record<keyof TaskSettingsState, (v: never) => void>; // setFontFamily, setFontSize, ...
  resetFromTask(task: TaskDetails): void;        // hidrasi dari task (detail sheet)
  resetToDefaults(): void;                       // nilai default create form
  normalizePauseThreshold(): number;             // clamp 250..3000, fallback 900
  normalizeFilteredWords(): string[];            // split koma, trim, lowercase, filter kosong
  buildFontOptions(): FontOptionsPayload;        // via lib/font-options
  buildCreatePayload(): CreateTaskPayload;       // untuk POST /api/tasks/create
  buildProjectSettingsPayload(): ProjectSettingsPayload; // untuk POST /api/tasks/:id/settings
}

function useTaskSettings(initial?: Partial<TaskSettingsState>): TaskSettingsApi;
```

Kontrak payload:
- create: { source, font_options, caption_template, processing_mode: "fast", output_format, add_subtitles, include_broll, cut_long_pauses, pause_threshold_ms, remove_filler_words, filtered_words } — field output_format/add_subtitles/source tetap di CreateTaskForm (bukan settings).
- settings: { ...fontOptions, caption_template, include_broll, cut_long_pauses, pause_threshold_ms, remove_filler_words, filtered_words, apply_to_existing: true }.

### 2.6 useBillingSummary (hooks/use-billing-summary.ts) — DIBAGI (home + settings)

```ts
function useBillingSummary(userId: string | undefined): {
  summary: BillingSummary | null;
  isLoading: boolean;
  refresh(): Promise<void>;
}
```

GET /api/tasks/billing-summary, cache no-store, silent catch (kegagalan -> summary tetap null, tidak set error). Dipakai home (Header + CreateTaskForm) dan settings (billing section). Settings menambah subscription_provider — tipe BillingSummary bersama sudah memuatnya sebagai opsional.
### 2.7 useApiKeys (hooks/use-api-keys.ts) — surface-specific (settings/api-keys)

Hanya satu surface; diekstrak untuk mengecilkan api-keys/page.tsx dan menguji pola hook. Kalau preferensi tim adalah meminimalkan abstraksi, hook ini opsional — file api-keys (254 baris) tidak melanggar ambang.

```ts
function useApiKeys(): {
  keys: ApiKey[];
  isFetching: boolean;
  error: string | null;
  createKey(name: string): Promise<string | null>; // mengembalikan key sekali-lihat, null saat gagal
  revokeKey(id: string): Promise<void>;
  refresh(): Promise<void>;
}
```

Kontrak: GET/POST /api/api-keys, DELETE /api/api-keys/:id; error disimpan di state (bukan throw).

### 2.8 useFonts (hooks/use-fonts.ts) — DIBAGI (home + tasks/[id] + settings)

Menghilangkan 3 implementasi load/inject font. Perbedaan perilaku lama dinormalisasi:
- home: inject @font-face dengan format otf->opentype, else truetype; upload + search + delete.
- tasks/[id]: load saja (tanpa inject), delete font dari settings sheet.
- settings: load + inject (selalu truetype) + tidak ada upload/delete.

```ts
function useFonts(): {
  fonts: FontOption[];
  loadError: string | null;
  isUploading: boolean;
  refresh(): Promise<void>;                    // GET /api/fonts + inject @font-face (opsi inject=true default)
  uploadFont(file: File): Promise<FontOption | null>; // POST /api/fonts/upload; validasi .ttf/.otf; refresh setelah sukses
  deleteFont(name: string): Promise<void>;     // DELETE /api/fonts/:name; filter state lokal
}
```

Keputusan normalisasi eksplisit: aturan format font (otf->opentype, else truetype) dipakai untuk semua surface. Settings lama selalu menulis truetype; perubahan ini tidak terlihat di browser baseline (format hanya memengaruhi parsing browser saat render font yang valid) dan diseragamkan ke perilaku yang lebih benar.

### 2.9 Matriks DIBAGI vs surface-specific

| Unit | DIBAGI | Surface pemakai |
|---|---|---|
| lib/task-types.ts (tipe + util) | Ya | home, detail, edit, list, settings |
| useTaskQuery | Ya | detail, edit |
| useTaskPolling | Ya | detail (mode default), edit (mode edit) |
| useClipEditor | Ya | detail, edit |
| useTaskSettings | Ya | home create, detail sheet |
| useBillingSummary | Ya | home, settings |
| useFonts | Ya | home, detail, settings |
| useApiKeys | Tidak (opsional) | settings/api-keys |
| useTaskList | Tidak (opsional) | list |
| Header (home) | Tidak | home |
| CreateTaskForm | Tidak | home |
| TaskHistoryCard | Tidak | home |
| LivePreview | Tidak | home |
| TaskDetailHeader | Tidak | detail |
| ClipCard (mode live + editable) | Tidak (satu file, dua varian) | detail |
| ProjectSettingsSheet | Tidak | detail |
| VideoCanvas | Tidak | edit |
| ClipTimeline | Tidak | edit |
| CaptionEditor | Tidak | edit |
| ExportBar | Tidak | edit |
| FineControls | Tidak | edit |

Keputusan: komponen UI tidak dibagi lintas surface untuk A14b. Alasan: layout tiga surface berbeda nyata (form inline vs sheet vs canvas editor); memaksa satu komponen akan menambah permukaan kontrak tanpa menghapus duplikasi nyata. Duplikasi yang bernilai dibagi adalah logika (hooks), bukan JSX.
---

## 3. Kontrak Props/State Antar Komponen

Kontrak ini cukup untuk implementasi A14b tanpa menebak. Semua props eksplisit (tanpa context). State editor inline di detail page TETAP di page (bukan per-clip) karena perilaku baseline: startOffset/endOffset/splitTime/captionText adalah state global page, bukan per-clip. Mengubahnya menjadi per-clip adalah perubahan perilaku — dilarang.

### 3.1 home-app decomposition

home-app.tsx menjadi komposisi: Header + TaskHistoryCard + CreateTaskForm + LivePreview, plus state shell (session, mobileMenuOpen ditarik ke Header internal).

Header (components/features/home/header.tsx):
```ts
interface HeaderProps {
  user: { name?: string | null; email?: string | null; image?: string | null };
  isAdmin: boolean;
  billingSummary: BillingSummary | null;
  onSignOut(): void;
}
// state internal: mobileMenuOpen. Render: nav desktop, usage badge/bar, mobile menu.
```

TaskHistoryCard (components/features/home/task-history-card.tsx):
```ts
interface TaskHistoryCardProps {
  latestTask: LatestTask | null;
  isLoading: boolean;
}
// Render: banner klik ke /tasks/:id; skeleton saat isLoading.
```

CreateTaskForm (components/features/home/create-task-form.tsx):
```ts
interface CreateTaskFormProps {
  userId: string;
  billingSummary: BillingSummary | null;
  onTaskCreated(taskId: string): void;  // navigasi window.location.href
}
// Internal: useTaskSettings + useFonts + useBillingSummary? TIDAK — billingSummary dari props (dipakai juga Header).
// Internal: state source (url, sourceType, fileName, fileRef), progress/statusMessage/currentStep/sourceTitle,
//   outputFormat, addSubtitles, pexelsConfigured, duplicate detection (via useTaskList ringan atau fetch latest),
//   upload pipeline, handleSubmit.
```

LivePreview (components/features/home/live-preview.tsx):
```ts
interface LivePreviewProps {
  sourceType: "youtube" | "upload";
  youtubeThumbnailUrl: string | null;
  previewFontFamily: string;
  previewFontSize: number;
  previewFontColor: string;
  fontFamily: string | null;       // info eksplisit (display + template default)
  fontSize: number | null;
  fontColor: string | null;
  templateName: string;
}
// Render murni: phone frame + caption info panel. Tanpa state.
```

Catatan duplikasi yang tersisa di home: deteksi duplikat memakai tasks (fetch GET /api/tasks/) — ini dipakai juga untuk latestTask. A14b mempertahankan satu fetch di CreateTaskForm internal; ekstraksi useTaskList opsional.
### 3.2 tasks/[id] decomposition

TaskDetailHeader (components/features/task-detail/task-detail-header.tsx):
```ts
interface TaskDetailHeaderProps {
  task: TaskDetails;
  clipsCount: number;
  isEditing: boolean;
  editedTitle: string;
  shareState: "idle" | "copying" | "copied";
  isRevokingShare: boolean;
  onEditedTitleChange(v: string): void;
  onStartEdit(): void;
  onCancelEdit(): void;
  onSaveTitle(): void;
  onCopyShareLink(): void;
  onRevokeShareLink(): void;
  onRequestDelete(): void;
  onCancelTask(): void;   // POST /api/tasks/:id/cancel + refetch
  onResumeTask(): void;   // POST /api/tasks/:id/resume + refetch
}
// Render: back, title+edit, badges (status, source, date, clip count), share/cancel/resume buttons.
```

ClipCard (components/features/task-detail/clip-card.tsx) — DUA VARIAN dalam satu komponen, dipilih via prop mode:
```ts
interface ClipCardProps {
  clip: Clip;
  mode: "live" | "editable";      // live: progress render; editable: kontrol penuh
  isSelectedForMerge?: boolean;
  isEditing?: boolean;
  exportPreset: ExportPreset;
  editorState?: {
    startOffset: string;
    endOffset: string;
    splitTime: string;
    captionText: string;
    captionPosition: CaptionPosition;
    highlightWords: string;
  };
  onToggleMerge?(): void;
  onToggleEdit?(): void;
  onDelete?(): void;
  onDownload?(): void;
  onPresetChange?(preset: ExportPreset): void;
  onTrim?(): void;
  onSplit?(): void;
  onUpdateCaptions?(): void;
  onEditorStateChange?(partial: Partial<ClipCardProps["editorState"]>): void;
}
// Render bersama: DynamicVideoPlayer, judul, meta waktu, badge skor, TranscriptPreview.
// Mode live menghilangkan: merge checkbox, edit panel, delete, export preset.
// Catatan: live grid saat ini adalah duplikat render; memakai ClipCard mode live menghapus duplikasi ini.
```

ProjectSettingsSheet (components/features/task-detail/project-settings-sheet.tsx):
```ts
interface ProjectSettingsSheetProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  settings: TaskSettingsState;          // dari useTaskSettings di page
  fonts: FontOption[];
  templates: CaptionTemplate[];
  isApplying: boolean;
  deletingFontName: string | null;
  onSettingsChange(partial: Partial<TaskSettingsState>): void;
  onApply(): void;                       // handleApplyProjectSettings
  onDeleteFont(font: FontOption): void;
}
// Render: Sheet + form font/size/color/template/broll/cleanup + tombol Apply to All Clips.
```

Dialog delete task + delete clip tetap di page (2 AlertDialog) — kecil, tidak diekstrak.
### 3.3 tasks/[id]/edit decomposition

VideoCanvas (components/features/editor/video-canvas.tsx):
```ts
interface VideoCanvasProps {
  clip: Clip | null;
  videoStyle: React.CSSProperties;
  currentTime: number;
  isPlaying: boolean;
  trimRange: [number, number];
  renderProgress: number | null;         // progress re-render caption untuk clip terpilih
  videoRef: React.RefObject<HTMLVideoElement | null>;
  onTimeUpdate(t: number): void;
  onPlayChange(playing: boolean): void;
  onSeek(t: number): void;
  onTrimChange(range: [number, number]): void;
  onSetTrimIn(): void;
  onSetTrimOut(): void;
  onApplyTrim(): void;
}
// Render: <video> + style FX, baris playhead (slider + -1s/+1s + Set In/Out), panel trim range (slider + Apply/Jump).
```

FineControls (components/features/editor/fine-controls.tsx):
```ts
interface FineControlsProps {
  clip: Clip | null;
  splitTime: number;
  volume: number;
  isMuted: boolean;
  playbackRate: number;
  videoFx: VideoFx;
  isSaving: boolean;
  currentTime: number;
  onSplitTimeChange(v: number): void;
  onSplitToPlayhead(): void;
  onSplit(): void;
  onVolumeChange(v: number): void;
  onMuteToggle(): void;
  onPlaybackRateChange(v: number): void;
  onVideoFxChange(fx: Partial<VideoFx>): void;
  onResetAdjustments(): void;
}
// Render: card Split + Audio + Video FX + Reset.
```

CaptionEditor (components/features/editor/caption-editor.tsx):
```ts
interface CaptionEditorProps {
  captionText: string;
  captionPosition: CaptionPosition;
  subtitleSize: number;
  subtitleY: number;
  highlightWords: string[];
  isSaving: boolean;
  captionSaved: boolean;
  clip: Clip | null;
  onCaptionTextChange(v: string): void;
  onPositionChange(pos: CaptionPosition): void;
  onSubtitleSizeChange(v: number): void;
  onSubtitleYChange(v: number): void;
  onToggleHighlightWord(word: string): void;
  onClearHighlights(): void;
  onResetTranscript(): void;
  onSave(): void;
}
// Render: textarea script, posisi, reset transcript, slider size/offset, chip highlight words, tombol save.
// Kata highlight dihitung di komponen (subtitleWords dari captionText) — murni render.
```

ClipTimeline (components/features/editor/clip-timeline.tsx):
```ts
interface ClipTimelineProps {
  clips: Clip[];
  selectedClipId: string | null;
  mergeSelection: string[];
  renderProgress: Record<string, number | null>;
  transitionSpec: string;
  availableTransitions: { name: string; display_name: string; kind: string }[];
  isSaving: boolean;
  onSelect(clipId: string): void;
  onToggleMerge(clipId: string): void;
  onTransitionChange(v: string): void;
  onMerge(): void;
}
// Render: merge bar (muncul saat >= 2 terpilih), grid clip cards, progress per clip.
```

ExportBar (components/features/editor/export-bar.tsx):
```ts
interface ExportBarProps {
  exportPreset: ExportPreset;            // tiktok/reels/shorts
  exportProgress: number | null;
  isSaving: boolean;
  disabled: boolean;                     // !selectedClip
  onPresetChange(preset: ExportPreset): void;
  onExport(): void;
}
// Render: Select preset + tombol Export (label berubah saat progress).
```

Header edit page tetap di page (kecil: back, judul, ExportBar di dalam header).

### 3.4 Aturan state ownership (ringkas)

- Page/detail: pemilik state task, clips, seleksi, share, dialog. Hook useTaskQuery + useTaskPolling + useClipEditor + useTaskSettings.
- Page/edit: pemilik state playback, editor, export. Hook useTaskQuery + useTaskPolling(mode edit) + useClipEditor.
- Home: pemilik state source/upload/progress. Hook useTaskSettings + useFonts + useBillingSummary.
- Komponen fitur: TIDAK memiliki state data; hanya state UI lokal (menu terbuka, focus, animasi). Semua state data naik ke page/hook.
---

## 4. Urutan Refactor (langkah aman, preservasi perilaku)

Prinsip tiap langkah: (1) buat unit baru, (2) ganti satu surface, (3) jalankan verifikasi penuh, (4) lanjut. Tidak ada langkah yang mengubah lebih dari satu surface sekaligus. Setelah tiap langkah: pnpm run lint + smoke test manual surface yang disentuh. T10 browser regression dijalankan penuh setelah langkah yang menyentuh detail/home/list/settings.

### Langkah 1 — Tipe bersama + util murni (nol JSX, nol fetch)
- Buat lib/task-types.ts: semua interface + getClipUrl, formatDuration, clamp, skor warna, getHookTypeLabel, ACTIVE_TASK_STATUSES, MIN_GAP_SECONDS, DEFAULT_VIDEO_FX, EXPORT_DIMENSIONS.
- Pindah buildSupportError ke lib/api-error.ts (helper async).
- Verifikasi: lint. TS compile memastikan tidak ada referensi rusak.
- Risiko: rendah. Tidak ada perilaku runtime berubah.

### Langkah 2 — useBillingSummary + useFonts + useApiKeys (hook tanpa editor)
- Buat hooks; ganti pemakaian di home (billing + fonts), settings (billing + fonts), tasks/[id] (fonts).
- Hapus duplikasi refreshFonts/loadFonts/fetchBillingSummary dari tiga surface.
- Verifikasi: lint + T10 (home auth, settings, task detail).
- Risiko: sedang. Injeksi @font-face pindah ke satu tempat; T10 tidak menguji font secara visual — smoke manual cek preview font di home.

### Langkah 3 — useTaskQuery (fetch task + clips)
- Buat hook dengan opsi retryOn404/mergeIncremental; ganti fetchTaskStatus (detail) dan fetchEditorData (edit).
- Callback onTaskLoaded untuk hidrasi settings state di detail (hanya detail; edit tidak butuh).
- Verifikasi: lint + T10 task detail (seeded task + progress render).
- Risiko: sedang — ini yang paling banyak menyentuh logika fetch; pertahankan retry 404 dan merge incremental clip persis.

### Langkah 4 — useTaskPolling (SSE)
- Buat hook dua mode; ganti efek SSE di detail (mode default) dan edit (mode edit).
- triggerAutoRefresh pindah ke hook (opsi autoRefreshMs=700, guard sekali).
- Verifikasi: lint + T10 task detail; smoke manual: jalankan task kecil dan amati progress live.
- Risiko: sedang-tinggi. SSE adalah perilaku live; perubahan callback wiring mudah bocor. Urutkan SETELAH useTaskQuery agar onCompleted memakai refetch yang sudah ada.

### Langkah 5 — useClipEditor + useTaskSettings
- Buat useClipEditor; ganti 4 mutasi di detail dan 3 di edit (trim/split/merge/caption). onAfterMutation = refetch dari useTaskQuery.
- Buat useTaskSettings; ganti state settings di detail sheet dan bagian style create form. handleApplyProjectSettings dan payload create memakai builder dari hook.
- Verifikasi: lint + T10 + smoke: trim/split/merge/caption di detail dan edit, create task di home.
- Risiko: tinggi untuk detail (13 mutasi pindah sebagian). Kerjakan per-operasi, commit per operasi (contoh: trim dulu, split, merge, caption).

### Langkah 6 — Pecah home-app
- Header, TaskHistoryCard, CreateTaskForm, LivePreview (kontrak 3.1). home-app tersisa ~150-250 baris komposisi.
- Verifikasi: lint + T10 home auth + create flow smoke.
- Risiko: sedang. JSX dipindah verbatim; risiko tinggi hanya di props wiring.

### Langkah 7 — Pecah tasks/[id]
- TaskDetailHeader, ClipCard (mode live + editable), ProjectSettingsSheet (kontrak 3.2). Memakai useClipEditor/useTaskSettings/useTaskQuery/useTaskPolling dari langkah 3-5.
- Gabungkan live grid + completed grid via ClipCard mode. Hapus duplikat render.
- Verifikasi: lint + T10 task detail penuh + smoke manual: share link, delete clip, trim.
- Risiko: tinggi (file terbesar, 13 mutasi). Ini langkah paling berisiko — kerjakan setelah hook stabil.

### Langkah 8 — Pecah tasks/[id]/edit
- VideoCanvas, FineControls, CaptionEditor, ClipTimeline, ExportBar (kontrak 3.3).
- Verifikasi: lint + T10 + smoke: export (mediabunny) di edit page.
- Risiko: sedang. Export client-side adalah satu-satunya jalur yang tidak bisa diuji T10 penuh (butuh video); pertahankan kode mediabunny verbatim di ExportBar/handler page.

### Langkah 9 (opsional, di luar ambang) — list + api-keys
- useTaskList (list) + useApiKeys (api-keys) bila diperlukan. Tidak wajib untuk A14b.

Urutan aman karena: tiap langkah memindahkan SATU tanggung jawab, hooks dibangun sebelum komponen yang memakainya, dan langkah berisiko (SSE, editor mutasi) diurutkan setelah fondasi fetch stabil.
---

## 5. Kriteria Penerimaan A14b

Ambang ukuran (setelah A14b):
- home-app.tsx < 300 baris (komposisi + shell).
- app/tasks/[id]/page.tsx < 400 baris (state + handlers + komposisi; dialog kecil tetap di page).
- app/tasks/[id]/edit/page.tsx < 400 baris.
- Semua komponen fitur baru < 300 baris per file.
- Hooks < 150 baris per file (kecuali useTaskQuery/useTaskPolling bila logika SSE memerlukan; tetap < 250).

Verifikasi wajib (semua hijau):
1. Unit test frontend dihapus (Vitest); verifikasi via `pnpm run lint` + `cd e2e && pnpm exec playwright test`.
2. pnpm run lint — nol error, nol warning baru.
3. cd e2e && pnpm exec playwright test (T10 browser regression) — seluruh spek e2e hijau: home auth, task detail (seeded clip visible), list, settings save, admin gate.
4. TIDAK ada perubahan perilaku baseline: URL route tidak berubah, kontrak API tidak berubah (body/endpoint sama), teks UI tidak berubah, alur user tidak berubah (kecuali refactor murni internal).
5. Tidak ada kode mati: pindahkan (bukan salin) logika; verifikasi dengan grep bahwa handler lama tidak tersisa.
6. Bundel build: pnpm run build sukses (TS strict + Next build) — menjamin tidak ada import yang hilang.

Definisi selesai per langkah: unit baru ada + test hijau + surface yang disentuh di-refactor + verifikasi 1-6 lulus.

## 6. Audit Simetris — Sambungan Terlemah

Titik patah pertama yang diidentifikasi pada desain ini:

1. SSE callback wiring (Langkah 4) — paling mudah bocor: event close/status di mode default memanggil onCompleted yang memicu auto-reload 700ms. Jika onCompleted ganda dipanggil (status event + close event), reload terjadi dua kali dengan guard hasTriggeredAutoRefresh — guard ini WAJIB dipertahankan di hook. Risiko perilaku: reload tak terduga.

2. ClipCard dua varian (Langkah 7) — live grid dan completed grid saat ini TIDAK identik (live lebih sederhana, tanpa merge/edit). Menggabungkan via prop mode menambah permukaan kontrak; jika mode wiring salah, tombol Edit muncul di grid live atau sebaliknya. Alternatif aman: ekstrak hanya bagian duplikat (header/badge/transcript) sebagai sub-komponen, biarkan dua varian ClipCard terpisah. Direkomendasikan: mulai dengan sub-komponen, gabung penuh hanya bila diff bersih.

3. Export dua mekanisme (detail server-side vs edit client-side) — sengaja TIDAK dibagi. Godaan menggabungkan akan menciptakan hook abstrak yang membayar dua perilaku berbeda; tolak. Batasnya: konstanta preset dibagi, pipeline tidak.

4. useTaskSettings state bersama home vs detail — hidrasi dari task (detail) vs default (home) berbeda. resetFromTask harus tidak menimpa null font dengan string kosong; ikuti persis hidrasi lama (font_family ?? null, typeof font_size === number ? font_size : null).

5. Pembulatan trim (toFixed(2) vs Number) — normalisasi ke toFixed(2) mengubah nilai yang dikirim untuk input desimal panjang di detail page. Dampak render diabaikan; dicatat agar tidak mengejutkan.

6. Batch action list page — sengaja tidak disentuh A14b (logika Promise.allSettled berbeda domain); jika useTaskList diekstrak, jangan menyeret runBatchAction ke hook editor.

Amplop validitas desain ini: berlaku selama tiga surface tetap memakai endpoint /api/tasks yang sama dan baseline T9 tetap menjadi jaring pengaman. Jika backend mengganti kontrak (mis. export di-merge ke satu endpoint), useClipEditor/export boundary menyerap perubahan — komponen tidak berubah.

## 7. Jalur Migrasi saat Amplop Dilampaui

- Bila detail page tumbuh fitur baru (mis. drag-reorder clip): pindah state editor inline ke hook useClipSelection (baru), bukan memperbesar ClipCard props.
- Bila edit page menambah efek video baru: perluas VideoFx di lib/task-types.ts + satu slot slider di FineControls; tidak menyentuh hook lain.
- Bila home create dan detail settings divergen (payload beda): pecah useTaskSettings menjadi dua hook dengan kontrak payload masing-masing; state bersama tetap di lib/task-types.
- Bila muncul surface keempat yang memakai editor clip: reuse useClipEditor/useTaskQuery/useTaskPolling; hanya komponen baru.
