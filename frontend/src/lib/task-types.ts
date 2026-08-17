// Shared domain types + pure helpers for task/clip surfaces (home, detail, edit, list, settings).
// Union dari definisi aktual di home-app.tsx, tasks/[id]/page.tsx, tasks/[id]/edit/page.tsx,
// list/page.tsx, settings/page.tsx; bidang yang tidak dipakai semua varian dibuat opsional.

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

// Existing merge endpoint contract. Hook metadata is returned by the backend;
// clients do not provide hook-generation options.
export interface MergeClipsPayload {
  clip_ids: string[];
  transition?: string | null;
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
  sound_effects_count?: number;
  sfx_attribution?: Array<{ sound_id: string; title: string; creator: string; license: string; source_url: string }>;
  sfx_degraded?: boolean;
  share_enabled?: boolean;
}

export interface Task {
  id: string;
  user_id: string;
  source_id: string;
  source_title: string;
  source_type: string;
  status: string;
  clips_count: number;
  created_at: string;
  updated_at: string;
}

export interface LatestTask {
  id: string;
  source_title: string;
  source_type: string;
  status: string;
  clips_count: number;
  created_at: string;
  source_url?: string;
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

export interface VideoFx {
  brightness: number;
  contrast: number;
  saturation: number;
  blur: number;
  hue: number;
  zoom: number;
}

// Task statuses that mean the video is still being processed.
export const ACTIVE_TASK_STATUSES: string[] = [
  "queued",
  "pending",
  "processing",
  "downloading",
  "transcribing",
  "analyzing",
  "generating_clips",
];

export const MIN_GAP_SECONDS = 0.25;

export const DEFAULT_VIDEO_FX: VideoFx = {
  brightness: 100,
  contrast: 100,
  saturation: 100,
  blur: 0,
  hue: 0,
  zoom: 1,
};

export const EXPORT_DIMENSIONS = {
  tiktok: { width: 1080, height: 1920 },
  reels: { width: 1080, height: 1920 },
  shorts: { width: 1080, height: 1920 },
} as const;

// Normalisasi path video dari backend: sudah ber-prefix /api/ dibiarkan, selain itu ditambah prefix.
// Amplop: hanya valid untuk path relatif; URL absolut yang tidak berawalan /api/ tetap diberi prefix
// sesuai perilaku baseline lama (bukan kasus nyata di data backend).
export function getClipUrl(videoUrl: string): string {
  return videoUrl.startsWith("/api/") ? videoUrl : `/api${videoUrl}`;
}

export function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

export function getScoreColor(score: number): string {
  if (score >= 0.8) return "bg-green-100 text-green-800";
  if (score >= 0.6) return "bg-yellow-100 text-yellow-800";
  return "bg-red-100 text-red-800";
}

export function getViralityColor(score: number): string {
  if (score >= 80) return "text-green-600";
  if (score >= 60) return "text-yellow-600";
  if (score >= 40) return "text-orange-600";
  return "text-red-600";
}

export function getViralityBgColor(score: number): string {
  if (score >= 80) return "bg-green-500";
  if (score >= 60) return "bg-yellow-500";
  if (score >= 40) return "bg-orange-500";
  return "bg-red-500";
}

// hookType menerima undefined karena Clip.hook_type bersifat opsional pada tipe bersama.
export function getHookTypeLabel(hookType: string | null | undefined): string {
  const labels: Record<string, string> = {
    question: "Question Hook",
    statement: "Bold Statement",
    statistic: "Data/Stats",
    story: "Story Hook",
    contrast: "Contrast Hook",
    none: "No Hook",
  };
  return labels[hookType || "none"] || hookType || "None";
}
