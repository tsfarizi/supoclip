"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  AudioLines,
  Check,
  Clapperboard,
  Download,
  Gauge,
  Layers,
  Loader2,
  Palette,
  Play,
  RotateCcw,
  Scissors,
  Sparkles,
  SplitSquareVertical,
  Subtitles,
  Volume2,
  VolumeX,
} from "lucide-react";
import { useSession } from "@/lib/auth-client";
import { buildSupportError } from "@/lib/api-client";
import { buildClipDownloadFilename } from "@/lib/clip-download";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Slider } from "@/components/ui/slider";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { getHookTypeLabel, type MergeClipsPayload } from "@/lib/task-types";
import type { Composition, CompositionResponse, ReframeSpec, SpeedSpec, AudioSpec, SoundFxSpec, BrollInsertSpec } from "@/lib/composition-types";
import { FramingControl } from "@/components/editor/framing-control";
import { TimelineControls } from "@/components/editor/timeline-controls";


interface TaskDetails {
  id: string;
  source_title: string;
  source_type: string;
  status: string;
  clips_count: number;
}

interface Clip {
  id: string;
  filename: string;
  clip_order: number;
  duration: number;
  start_time: string;
  end_time: string;
  text: string;
  video_url: string;
  hook_title?: string | null;
  hook_type?: string | null;
}

interface VideoFx {
  brightness: number;
  contrast: number;
  saturation: number;
  blur: number;
  hue: number;
  zoom: number;
}

interface BrowserVideoSample {
  displayWidth: number;
  displayHeight: number;
  timestamp: number;
  draw: (ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D, x: number, y: number) => void;
}

interface BrowserAudioSample {
  timestamp: number;
  numberOfChannels: number;
  sampleRate: number;
  allocationSize: (options: { planeIndex: number; format: "f32" }) => number;
  copyTo: (target: Float32Array, options: { planeIndex: number; format: "f32" }) => void;
}

const MIN_GAP_SECONDS = 0.25;

const DEFAULT_VIDEO_FX: VideoFx = {
  brightness: 100,
  contrast: 100,
  saturation: 100,
  blur: 0,
  hue: 0,
  zoom: 1,
};

const EXPORT_DIMENSIONS = {
  tiktok: { width: 1080, height: 1920 },
  reels: { width: 1080, height: 1920 },
  shorts: { width: 1080, height: 1920 },
} as const;

export default function TaskEditPage() {
  const params = useParams();
  const { data: session } = useSession();
  const taskApiUrl = "/api/tasks";
  const getClipUrl = (videoUrl: string) =>
    videoUrl.startsWith("/api/") ? videoUrl : `/api${videoUrl}`;

  const [task, setTask] = useState<TaskDetails | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [mergeSelection, setMergeSelection] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [transitionSpec, setTransitionSpec] = useState("none");
  const [availableTransitions, setAvailableTransitions] = useState<{ name: string; display_name: string; kind: string }[]>([]);

  const [trimRange, setTrimRange] = useState<[number, number]>([0, 1]);
  const [splitTime, setSplitTime] = useState(1);
  const [captionText, setCaptionText] = useState("");
  const [captionPosition, setCaptionPosition] = useState("bottom");
  const [highlightWords, setHighlightWords] = useState<string[]>([]);
  const [subtitleSize, setSubtitleSize] = useState(52);
  const [subtitleY, setSubtitleY] = useState(78);
  const [captionSaved, setCaptionSaved] = useState(false);
  const [clipRenderProgress, setClipRenderProgress] = useState<Record<string, number | null>>({});

  const [volume, setVolume] = useState(100);
  const [isMuted, setIsMuted] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [videoFx, setVideoFx] = useState<VideoFx>(DEFAULT_VIDEO_FX);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);

  // Editable Composition State
  const [composition, setComposition] = useState<Composition | null>(null);
  const [compositionVersion, setCompositionVersion] = useState<number>(1);
  const [isCompLoading, setIsCompLoading] = useState(false);
  const [isCompSaving, setIsCompSaving] = useState(false);
  const [activeEditorTab, setActiveEditorTab] = useState<"trim" | "framing" | "timeline" | "subtitles" | "effects">("framing");

  // Preview & Server Render State
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isPreviewRendering, setIsPreviewRendering] = useState(false);
  const [isExportingFull, setIsExportingFull] = useState(false);
  const [saveSuccessMsg, setSaveSuccessMsg] = useState<string | null>(null);
  const [conflictError, setConflictError] = useState<string | null>(null);

  const [exportPreset, setExportPreset] = useState("tiktok");
  const [exportProgress, setExportProgress] = useState<number | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);

  const selectedClip = useMemo(
    () => clips.find((clip) => clip.id === selectedClipId) ?? null,
    [clips, selectedClipId]
  );

  const videoStyle = useMemo(
    () => ({
      filter: `brightness(${videoFx.brightness}%) contrast(${videoFx.contrast}%) saturate(${videoFx.saturation}%) blur(${videoFx.blur}px) hue-rotate(${videoFx.hue}deg)`,
      transform: `scale(${videoFx.zoom})`,
      transformOrigin: "center center",
    }),
    [videoFx]
  );

  const subtitleWords = useMemo(
    () => captionText.split(/\s+/).map((word) => word.trim()).filter(Boolean),
    [captionText]
  );

  const getSubtitleWordsAtTime = useCallback(
    (timeSeconds: number, durationSeconds: number) => {
      if (subtitleWords.length === 0) return [] as string[];
      const safeDuration = Math.max(durationSeconds, 0.01);
      const progress = clamp(timeSeconds / safeDuration, 0, 0.9999);
      const wordIndex = Math.floor(progress * subtitleWords.length);
      const startIndex = Math.max(0, wordIndex - 1);
      return subtitleWords.slice(startIndex, startIndex + 6);
    },
    [subtitleWords]
  );

  const formatDuration = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);

  const fetchEditorData = useCallback(async () => {
    if (!params.id) return;
    setError(null);

    try {
      const taskResponse = await fetch(`${taskApiUrl}/${params.id}`, { cache: "no-store" });
      if (!taskResponse.ok) {
        throw new Error(await buildSupportError(taskResponse, `Failed to fetch task: ${taskResponse.status}`));
      }

      const taskData = (await taskResponse.json()) as TaskDetails;
      setTask(taskData);

      if (taskData.status !== "completed") {
        setClips([]);
        return;
      }

      const clipsResponse = await fetch(`${taskApiUrl}/${params.id}/clips`, { cache: "no-store" });
      if (!clipsResponse.ok) {
        throw new Error(await buildSupportError(clipsResponse, `Failed to fetch clips: ${clipsResponse.status}`));
      }

      const clipsData = await clipsResponse.json();
      const nextClips = (clipsData.clips || []) as Clip[];
      setClips(nextClips);

      setSelectedClipId((current) => {
        if (current && nextClips.some((clip) => clip.id === current)) return current;
        return nextClips[0]?.id ?? null;
      });

      setMergeSelection((current) => current.filter((id) => nextClips.some((clip) => clip.id === id)));
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "Failed to load editor");
    }
  }, [params.id, taskApiUrl]);

  useEffect(() => {
    const run = async () => {
      setIsLoading(true);
      try {
        await fetchEditorData();
      } finally {
        setIsLoading(false);
      }
    };
    void run();
  }, [fetchEditorData]);

  useEffect(() => {
    let cancelled = false;
    const loadTransitions = async () => {
      try {
        const response = await fetch("/api/transitions", { cache: "no-store" });
        if (!response.ok) return;
        const data = (await response.json()) as {
          transitions?: { name: string; display_name: string; kind: string }[];
        };
        if (!cancelled) setAvailableTransitions(data.transitions || []);
      } catch {
        // Leave the list empty; merging without a transition stays valid.
      }
    };
    void loadTransitions();
    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch composition when clip is selected
  const fetchComposition = useCallback(async (clipId: string) => {
    if (!params.id) return;
    setIsCompLoading(true);
    setConflictError(null);
    try {
      const resp = await fetch(`${taskApiUrl}/${params.id}/clips/${clipId}/composition`, {
        cache: "no-store",
      });
      if (resp.ok) {
        const data = (await resp.json()) as CompositionResponse;
        setComposition(data.composition);
        setCompositionVersion(data.composition_version);
      }
    } catch (err) {
      console.error("Failed to load composition", err);
    } finally {
      setIsCompLoading(false);
    }
  }, [params.id, taskApiUrl]);

  useEffect(() => {
    if (selectedClipId) {
      setPreviewUrl(null);
      void fetchComposition(selectedClipId);
    }
  }, [selectedClipId, fetchComposition]);

  const handleSaveComposition = async () => {
    if (!selectedClip || !params.id || !composition) return;
    setIsCompSaving(true);
    setConflictError(null);
    setSaveSuccessMsg(null);
    try {
      const resp = await fetch(`${taskApiUrl}/${params.id}/clips/${selectedClip.id}/composition`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          composition,
          base_version: compositionVersion,
        }),
      });

      if (resp.status === 409) {
        const errPayload = await resp.json().catch(() => ({}));
        setConflictError(errPayload.detail || "Composition was modified by another request. Please reload.");
        return;
      }

      if (!resp.ok) {
        throw new Error(await buildSupportError(resp, "Failed to save composition"));
      }

      const data = (await resp.json()) as CompositionResponse;
      setComposition(data.composition);
      setCompositionVersion(data.composition_version);
      setSaveSuccessMsg("Composition saved!");
      setTimeout(() => setSaveSuccessMsg(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save composition");
    } finally {
      setIsCompSaving(false);
    }
  };

  const handleRenderPreview = async () => {
    if (!selectedClip || !params.id) return;
    setIsPreviewRendering(true);
    setError(null);
    try {
      // First save current changes
      if (composition) {
        await handleSaveComposition();
      }

      const resp = await fetch(`${taskApiUrl}/${params.id}/clips/${selectedClip.id}/composition/render`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ intent: "preview" }),
      });

      if (!resp.ok) {
        throw new Error(await buildSupportError(resp, "Failed to request preview render"));
      }

      const data = await resp.json();
      if (data.status === "ready" && data.preview_url) {
        setPreviewUrl(data.preview_url);
      } else if (data.status === "queued") {
        setSaveSuccessMsg("Render queued waiting for capacity...");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to render preview");
    } finally {
      setIsPreviewRendering(false);
    }
  };

  const handleExportFullComposition = async () => {
    if (!selectedClip || !params.id) return;
    setIsExportingFull(true);
    setError(null);
    try {
      if (composition) {
        await handleSaveComposition();
      }

      const resp = await fetch(`${taskApiUrl}/${params.id}/clips/${selectedClip.id}/composition/render`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ intent: "export" }),
      });

      if (!resp.ok) {
        throw new Error(await buildSupportError(resp, "Failed to start full export"));
      }

      const data = await resp.json();
      setSaveSuccessMsg("Export job enqueued! Video will update when render finishes.");
      setTimeout(() => setSaveSuccessMsg(null), 5000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to export composition");
    } finally {
      setIsExportingFull(false);
    }
  };

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.volume = clamp(volume / 100, 0, 1);
    video.muted = isMuted;
    video.playbackRate = playbackRate;
  }, [volume, isMuted, playbackRate]);

  // Live progress for clip re-renders (caption edits). mode=edit keeps the
  // stream open for completed tasks so clip_render events arrive here.
  useEffect(() => {
    if (!params.id) return;
    const eventSource = new EventSource(`${taskApiUrl}/${params.id}/progress?mode=edit`);

    eventSource.addEventListener("clip_render", (e) => {
      const data = JSON.parse(e.data) as {
        clip_id?: string;
        progress?: number;
      };
      const clipId = data.clip_id;
      if (!clipId) return;
      const progress = Number(data.progress ?? 0);
      setClipRenderProgress((current) => ({ ...current, [clipId]: progress }));
      if (progress >= 100) {
        window.setTimeout(() => {
          setClipRenderProgress((current) => {
            const next = { ...current };
            delete next[clipId];
            return next;
          });
        }, 1500);
      }
    });

    eventSource.addEventListener("error", () => {
      // EventSource reconnects automatically; transient errors are harmless.
    });

    return () => eventSource.close();
  }, [params.id, taskApiUrl]);

  const withSaving = async (action: () => Promise<void>) => {
    setIsSaving(true);
    try {
      await action();
      await fetchEditorData();
    } finally {
      setIsSaving(false);
    }
  };

  const handleTrim = async () => {
    if (!selectedClip || !session?.user?.id || !task?.id) return;
    const startOffset = Number(trimRange[0].toFixed(2));
    const endOffset = Number((selectedClip.duration - trimRange[1]).toFixed(2));

    await withSaving(async () => {
      const response = await fetch(`${taskApiUrl}/${task.id}/clips/${selectedClip.id}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ start_offset: startOffset, end_offset: endOffset }),
      });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to trim clip"));
    });
  };

  const handleSplit = async (splitAt?: number) => {
    if (!selectedClip || !session?.user?.id || !task?.id) return;
    const value = splitAt ?? splitTime;
    await withSaving(async () => {
      const response = await fetch(`${taskApiUrl}/${task.id}/clips/${selectedClip.id}/split`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ split_time: Number(value.toFixed(2)) }),
      });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to split clip"));
    });
  };

  const handleUpdateCaptions = async () => {
    if (!selectedClip || !session?.user?.id || !task?.id) return;

    await withSaving(async () => {
      const response = await fetch(`${taskApiUrl}/${task.id}/clips/${selectedClip.id}/captions`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          caption_text: captionText,
          position: captionPosition,
          highlight_words: highlightWords,
        }),
      });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to update captions"));
    });
    setCaptionSaved(true);
    window.setTimeout(() => setCaptionSaved(false), 2500);
  };

  const handleMerge = async () => {
    if (!session?.user?.id || !task?.id || mergeSelection.length < 2) return;
    await withSaving(async () => {
      const response = await fetch(`${taskApiUrl}/${task.id}/clips/merge`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          clip_ids: mergeSelection,
          transition: transitionSpec,
        } satisfies MergeClipsPayload),
      });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to merge selected clips"));
    });
    setMergeSelection([]);
  };

  const handleExport = async () => {
    if (!selectedClip || !session?.user?.id || !task?.id) return;
    setIsSaving(true);
    setExportProgress(0);

    try {
      const sourceResponse = await fetch(getClipUrl(selectedClip.video_url));
      if (!sourceResponse.ok) {
        throw new Error(`Failed to fetch source clip: ${sourceResponse.status}`);
      }

      const sourceBlob = await sourceResponse.blob();

      const {
        Input,
        Output,
        Conversion,
        ALL_FORMATS,
        BlobSource,
        BufferTarget,
        Mp4OutputFormat,
        AudioSample,
      } = await import("mediabunny");

      const outputSize = EXPORT_DIMENSIONS[exportPreset as keyof typeof EXPORT_DIMENSIONS] || EXPORT_DIMENSIONS.tiktok;
      const trimStart = trimRange[0];
      const trimEnd = trimRange[1];
      const targetDuration = Math.max(trimEnd - trimStart, 0.1);
      const highlightSet = new Set(highlightWords);

      const input = new Input({
        source: new BlobSource(sourceBlob),
        formats: ALL_FORMATS,
      });

      const output = new Output({
        format: new Mp4OutputFormat(),
        target: new BufferTarget(),
      });

      let canvas: OffscreenCanvas | HTMLCanvasElement | null = null;
      let ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null = null;

      const conversion = await Conversion.init({
        input,
        output,
        trim: {
          start: trimStart,
          end: trimEnd,
        },
        video: {
          forceTranscode: true,
          process: (sample) => {
            const browserSample = sample as unknown as BrowserVideoSample;
            if (!canvas || !ctx) {
              if (typeof OffscreenCanvas !== "undefined") {
                canvas = new OffscreenCanvas(outputSize.width, outputSize.height);
                ctx = canvas.getContext("2d");
              } else {
                const fallbackCanvas = document.createElement("canvas");
                fallbackCanvas.width = outputSize.width;
                fallbackCanvas.height = outputSize.height;
                canvas = fallbackCanvas;
                ctx = fallbackCanvas.getContext("2d");
              }
            }

            if (!ctx || !canvas) {
              return sample;
            }

            const scale = Math.min(outputSize.width / browserSample.displayWidth, outputSize.height / browserSample.displayHeight);
            const drawWidth = browserSample.displayWidth * scale;
            const drawHeight = browserSample.displayHeight * scale;
            const drawX = (outputSize.width - drawWidth) / 2;
            const drawY = (outputSize.height - drawHeight) / 2;

            ctx.clearRect(0, 0, outputSize.width, outputSize.height);
            ctx.fillStyle = "black";
            ctx.fillRect(0, 0, outputSize.width, outputSize.height);

            ctx.save();
            ctx.filter = `brightness(${videoFx.brightness}%) contrast(${videoFx.contrast}%) saturate(${videoFx.saturation}%) blur(${videoFx.blur}px) hue-rotate(${videoFx.hue}deg)`;

            const centerX = drawX + drawWidth / 2;
            const centerY = drawY + drawHeight / 2;
            ctx.translate(centerX, centerY);
            ctx.scale(videoFx.zoom, videoFx.zoom);
            ctx.translate(-centerX, -centerY);

            browserSample.draw(ctx, drawX, drawY);
            ctx.restore();

            const subtitleAtTime = getSubtitleWordsAtTime(browserSample.timestamp, targetDuration);
            if (subtitleAtTime.length > 0) {
              const fontSize = Math.max(24, Math.round(subtitleSize));
              ctx.font = `700 ${fontSize}px ui-sans-serif, system-ui, sans-serif`;
              ctx.textAlign = "center";
              ctx.textBaseline = "middle";

              const text = subtitleAtTime.join(" ");
              const metrics = ctx.measureText(text);
              const textWidth = Math.max(metrics.width, 32);
              const y = (subtitleY / 100) * outputSize.height;
              const boxPaddingX = 22;
              const boxPaddingY = 14;

              ctx.fillStyle = "rgba(0,0,0,0.7)";
              const left = outputSize.width / 2 - textWidth / 2 - boxPaddingX;
              const top = y - fontSize / 2 - boxPaddingY;
              const width = textWidth + boxPaddingX * 2;
              const height = fontSize + boxPaddingY * 2;
              ctx.beginPath();
              ctx.roundRect(left, top, width, height, 16);
              ctx.fill();

              let cursorX = outputSize.width / 2 - textWidth / 2;
              for (const word of subtitleAtTime) {
                const cleanedWord = word.toLowerCase().replace(/[^a-z0-9']/g, "");
                ctx.fillStyle = highlightSet.has(cleanedWord) ? "#fde047" : "#ffffff";
                ctx.fillText(word, cursorX + ctx.measureText(word).width / 2, y);
                cursorX += ctx.measureText(`${word} `).width;
              }
            }

            return canvas;
          },
        },
        audio: {
          forceTranscode: true,
          process: (sample) => {
            const browserSample = sample as unknown as BrowserAudioSample;
            if (isMuted || volume !== 100) {
              const gain = isMuted ? 0 : volume / 100;
              const bytes = browserSample.allocationSize({ planeIndex: 0, format: "f32" });
              const data = new Float32Array(bytes / 4);
              browserSample.copyTo(data, { planeIndex: 0, format: "f32" });

              for (let i = 0; i < data.length; i += 1) {
                data[i] *= gain;
              }

              return new AudioSample({
                data,
                format: "f32",
                numberOfChannels: browserSample.numberOfChannels,
                sampleRate: browserSample.sampleRate,
                timestamp: browserSample.timestamp,
              });
            }

            return sample;
          },
        },
      });

      conversion.onProgress = (progress: number) => {
        setExportProgress(Math.round(progress * 100));
      };

      await conversion.execute();

      const targetBuffer = output.target.buffer as ArrayBuffer;
      const blob = new Blob([targetBuffer], { type: "video/mp4" });
      const blobUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = blobUrl;
      link.download = buildClipDownloadFilename(
        selectedClip.hook_title,
        selectedClip.clip_order,
        `_${exportPreset}_browser`,
      );
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(blobUrl);
      setExportProgress(100);
    } finally {
      setIsSaving(false);
      setTimeout(() => setExportProgress(null), 800);
    }
  };

  const toggleMergeSelection = (clipId: string) => {
    setMergeSelection((current) => (current.includes(clipId) ? current.filter((id) => id !== clipId) : [...current, clipId]));
  };

  const toggleHighlightedWord = (word: string) => {
    const cleaned = word.toLowerCase().replace(/[^a-z0-9']/g, "").trim();
    if (!cleaned) return;
    setHighlightWords((current) => (current.includes(cleaned) ? current.filter((value) => value !== cleaned) : [...current, cleaned]));
  };

  const handleTrimRangeChange = (value: number[]) => {
    if (!selectedClip || value.length !== 2) return;
    const min = 0;
    const max = selectedClip.duration;
    let nextStart = clamp(value[0], min, max);
    let nextEnd = clamp(value[1], min, max);

    if (nextEnd - nextStart < MIN_GAP_SECONDS) {
      if (nextStart + MIN_GAP_SECONDS <= max) nextEnd = nextStart + MIN_GAP_SECONDS;
      else {
        nextStart = max - MIN_GAP_SECONDS;
        nextEnd = max;
      }
    }
    setTrimRange([nextStart, nextEnd]);
  };

  const seekTo = (seconds: number) => {
    if (!videoRef.current || !selectedClip) return;
    const target = clamp(seconds, 0, selectedClip.duration);
    videoRef.current.currentTime = target;
    setCurrentTime(target);
  };

  const handleTimeUpdate = () => {
    if (!videoRef.current) return;
    setCurrentTime(videoRef.current.currentTime || 0);
  };

  const setTrimInToPlayhead = () => {
    setTrimRange(([, end]) => {
      const nextStart = Math.min(currentTime, end - MIN_GAP_SECONDS);
      return [clamp(nextStart, 0, Math.max(end - MIN_GAP_SECONDS, 0)), end];
    });
  };

  const setTrimOutToPlayhead = () => {
    if (!selectedClip) return;
    setTrimRange(([start]) => {
      const nextEnd = Math.max(currentTime, start + MIN_GAP_SECONDS);
      return [start, clamp(nextEnd, start + MIN_GAP_SECONDS, selectedClip.duration)];
    });
  };

  const resetPreviewAdjustments = () => {
    setVideoFx(DEFAULT_VIDEO_FX);
    setVolume(100);
    setIsMuted(false);
    setPlaybackRate(1);
    setSubtitleSize(52);
    setSubtitleY(78);
  };

  if (isLoading) {
    return (
      <div className="min-h-screen bg-white p-4">
        <div className="max-w-7xl mx-auto space-y-4">
          <Skeleton className="h-10 w-56" />
          <Skeleton className="h-105 w-full" />
          <div className="grid grid-cols-1 xl:grid-cols-12 gap-4">
            <Skeleton className="h-130 xl:col-span-7" />
            <Skeleton className="h-130 xl:col-span-5" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white">
      <div className="border-b bg-white">
        <div className="max-w-7xl mx-auto px-4 py-5 flex items-center justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-3">
              <Link href={`/tasks/${params.id}`}>
                <Button variant="ghost" size="sm">
                  <ArrowLeft className="w-4 h-4" />
                  Back to Task
                </Button>
              </Link>
              <Badge variant="outline">Studio Editor</Badge>
            </div>
            <h1 className="text-2xl font-bold text-black">{task?.source_title || "Clip Editor"}</h1>
          </div>
          <div className="flex items-center gap-2">
            <Select value={exportPreset} onValueChange={setExportPreset}>
              <SelectTrigger className="w-32">
                <SelectValue placeholder="Preset" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="tiktok">TikTok</SelectItem>
                <SelectItem value="reels">Reels</SelectItem>
                <SelectItem value="shorts">Shorts</SelectItem>
              </SelectContent>
            </Select>
            <Button onClick={handleExport} disabled={!selectedClip || isSaving}>
              <Download className="w-4 h-4" />
              {exportProgress !== null ? `Exporting ${exportProgress}%` : "Export Selected"}
            </Button>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {error && (
          <Alert>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {!task ? (
          <Alert>
            <AlertDescription>Task not found.</AlertDescription>
          </Alert>
        ) : task.status !== "completed" ? (
          <Card>
            <CardContent className="p-8 text-center space-y-3">
              <p className="text-lg font-semibold">This editor is available once processing completes.</p>
              <p className="text-gray-600">Current status: {task.status}</p>
              <Link href={`/tasks/${task.id}`}>
                <Button variant="outline">Return to Task</Button>
              </Link>
            </CardContent>
          </Card>
        ) : clips.length === 0 ? (
          <Card>
            <CardContent className="p-8 text-center space-y-3">
              <p className="text-lg font-semibold">No clips to edit yet.</p>
              <Link href={`/tasks/${task.id}`}>
                <Button variant="outline">Return to Task</Button>
              </Link>
            </CardContent>
          </Card>
        ) : (
          <>
            <div className="grid grid-cols-1 xl:grid-cols-12 gap-5">
              <Card className="xl:col-span-7">
                <CardContent className="p-4 lg:p-5 space-y-4">
                  {selectedClip ? (
                    <>
                      <div className="rounded-xl bg-black overflow-hidden relative flex items-center justify-center min-h-[360px]">
                        <video
                          ref={videoRef}
                          key={previewUrl || selectedClip.id}
                          src={previewUrl ? getClipUrl(previewUrl) : getClipUrl(selectedClip.video_url)}
                          controls
                          onTimeUpdate={handleTimeUpdate}
                          onPlay={() => setIsPlaying(true)}
                          onPause={() => setIsPlaying(false)}
                          className="max-h-150 max-w-full h-auto w-auto object-contain"
                          style={previewUrl ? undefined : videoStyle}
                        />
                        {previewUrl && (
                          <div className="absolute top-3 left-3 bg-black/70 backdrop-blur text-white text-xs px-2.5 py-1 rounded-full flex items-center gap-1.5 border border-white/20">
                            <Sparkles className="w-3 h-3 text-yellow-400" />
                            <span>Composition Preview Active</span>
                          </div>
                        )}
                      </div>

                      {/* Preview & Export Bar */}
                      <div className="flex flex-wrap items-center justify-between gap-2 p-3 bg-gray-50 border rounded-lg">
                        <div className="flex items-center gap-2">
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={handleRenderPreview}
                            disabled={isPreviewRendering || isCompSaving}
                            className="bg-white border hover:bg-gray-100 text-xs"
                          >
                            {isPreviewRendering ? (
                              <>
                                <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                                Rendering Preview...
                              </>
                            ) : (
                              <>
                                <Play className="w-3.5 h-3.5 mr-1.5 fill-current" />
                                Generate Preview
                              </>
                            )}
                          </Button>
                          {previewUrl && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setPreviewUrl(null)}
                              className="text-xs text-gray-500 hover:text-gray-900"
                            >
                              Show Original Clip
                            </Button>
                          )}
                        </div>

                        <div className="flex items-center gap-2">
                          <Button
                            variant="default"
                            size="sm"
                            onClick={handleSaveComposition}
                            disabled={isCompSaving || !composition}
                            className="text-xs"
                          >
                            {isCompSaving ? (
                              <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                            ) : (
                              <Check className="w-3.5 h-3.5 mr-1.5" />
                            )}
                            Save Changes
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={handleExportFullComposition}
                            disabled={isExportingFull || isCompSaving}
                            className="text-xs"
                          >
                            {isExportingFull ? (
                              <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                            ) : (
                              <Download className="w-3.5 h-3.5 mr-1.5" />
                            )}
                            Export Full Video
                          </Button>
                        </div>
                      </div>

                      {saveSuccessMsg && (
                        <p className="text-xs font-medium text-emerald-600 px-1">{saveSuccessMsg}</p>
                      )}
                      {conflictError && (
                        <Alert variant="destructive">
                          <AlertDescription className="flex items-center justify-between text-xs">
                            <span>{conflictError}</span>
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-6 text-[11px]"
                              onClick={() => void fetchComposition(selectedClip.id)}
                            >
                              Reload Composition
                            </Button>
                          </AlertDescription>
                        </Alert>
                      )}

                      {selectedClip && clipRenderProgress[selectedClip.id] != null && (
                        <div className="space-y-1.5 rounded-lg border border-blue-200 bg-blue-50 p-3">
                          <div className="flex items-center justify-between text-xs text-blue-700">
                            <span className="font-medium flex items-center gap-1.5">
                              <Loader2 className="w-3.5 h-3.5 animate-spin" />
                              Rendering captions...
                            </span>
                            <span>{clipRenderProgress[selectedClip.id]}%</span>
                          </div>
                          <Progress value={clipRenderProgress[selectedClip.id] ?? 0} className="h-2" />
                        </div>
                      )}

                      <div className="border rounded-lg p-3 space-y-3">
                        <div className="flex items-center justify-between text-sm text-gray-600">
                          <span>Playhead: {formatDuration(currentTime)} / {formatDuration(selectedClip.duration)}</span>
                          <span>{isPlaying ? "Playing" : "Paused"}</span>
                        </div>

                        <Slider
                          min={0}
                          max={selectedClip.duration}
                          value={[currentTime]}
                          step={0.01}
                          onValueChange={(value) => seekTo(value[0] || 0)}
                        />

                        <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
                          <Button variant="outline" size="sm" onClick={() => seekTo(Math.max(0, currentTime - 1))}>-1s</Button>
                          <Button variant="outline" size="sm" onClick={() => seekTo(Math.min(selectedClip.duration, currentTime + 1))}>+1s</Button>
                          <Button variant="outline" size="sm" onClick={setTrimInToPlayhead}>Set In</Button>
                          <Button variant="outline" size="sm" onClick={setTrimOutToPlayhead}>Set Out</Button>
                        </div>
                      </div>

                      <div className="border rounded-lg p-3 space-y-3">
                        <div className="flex items-center justify-between text-sm text-gray-700">
                          <span className="font-medium">Trim Range</span>
                          <span>{formatDuration(trimRange[0])} - {formatDuration(trimRange[1])}</span>
                        </div>
                        <Slider
                          min={0}
                          max={selectedClip.duration}
                          value={trimRange}
                          step={0.01}
                          onValueChange={handleTrimRangeChange}
                        />
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                          <Button onClick={handleTrim} disabled={isSaving}>
                            <Scissors className="w-4 h-4" />
                            Apply Trim
                          </Button>
                          <Button variant="outline" onClick={() => seekTo(trimRange[0])}>Jump In</Button>
                          <Button variant="outline" onClick={() => seekTo(trimRange[1])}>Jump Out</Button>
                        </div>
                      </div>
                    </>
                  ) : (
                    <p className="text-sm text-gray-600">Select a clip to start editing.</p>
                  )}
                </CardContent>
              </Card>

              <div className="xl:col-span-5 space-y-4">
                <div className="flex rounded-lg border bg-gray-100 p-1 text-xs font-medium">
                  <button
                    type="button"
                    onClick={() => setActiveEditorTab("framing")}
                    className={`flex-1 py-1.5 rounded-md transition ${activeEditorTab === "framing" ? "bg-white shadow-sm font-semibold text-black" : "text-gray-600 hover:text-black"}`}
                  >
                    Framing
                  </button>
                  <button
                    type="button"
                    onClick={() => setActiveEditorTab("timeline")}
                    className={`flex-1 py-1.5 rounded-md transition ${activeEditorTab === "timeline" ? "bg-white shadow-sm font-semibold text-black" : "text-gray-600 hover:text-black"}`}
                  >
                    Audio &amp; B-Roll
                  </button>
                  <button
                    type="button"
                    onClick={() => setActiveEditorTab("subtitles")}
                    className={`flex-1 py-1.5 rounded-md transition ${activeEditorTab === "subtitles" ? "bg-white shadow-sm font-semibold text-black" : "text-gray-600 hover:text-black"}`}
                  >
                    Subtitles
                  </button>
                  <button
                    type="button"
                    onClick={() => setActiveEditorTab("trim")}
                    className={`flex-1 py-1.5 rounded-md transition ${activeEditorTab === "trim" ? "bg-white shadow-sm font-semibold text-black" : "text-gray-600 hover:text-black"}`}
                  >
                    Split &amp; FX
                  </button>
                </div>

                {activeEditorTab === "framing" && (
                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="text-base flex items-center gap-2">
                        <Sparkles className="w-4 h-4 text-purple-600" />
                        Framing &amp; Reframe
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      {composition && composition.segments.length > 0 ? (
                        <FramingControl
                          reframe={composition.segments[0].reframe}
                          onChange={(updatedReframe) => {
                            const updatedSegments = [...composition.segments];
                            updatedSegments[0] = {
                              ...updatedSegments[0],
                              reframe: updatedReframe,
                            };
                            setComposition({
                              ...composition,
                              segments: updatedSegments,
                            });
                          }}
                        />
                      ) : (
                        <div className="py-6 text-center text-xs text-gray-500">
                          {isCompLoading ? (
                            <span className="flex items-center justify-center gap-1.5">
                              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading composition...
                            </span>
                          ) : (
                            "No composition loaded for this clip."
                          )}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                )}

                {activeEditorTab === "timeline" && (
                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="text-base flex items-center gap-2">
                        <AudioLines className="w-4 h-4 text-blue-600" />
                        Timeline, Speed, Sound &amp; B-Roll
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      {composition && composition.segments.length > 0 ? (
                        <TimelineControls
                          speed={composition.segments[0].speed}
                          audio={composition.segments[0].audio}
                          sfxList={composition.sfx}
                          brollList={composition.broll_inserts}
                          onSpeedChange={(newSpeed) => {
                            const updatedSegments = [...composition.segments];
                            updatedSegments[0] = {
                              ...updatedSegments[0],
                              speed: newSpeed,
                            };
                            setComposition({
                              ...composition,
                              segments: updatedSegments,
                            });
                          }}
                          onAudioChange={(newAudio) => {
                            const updatedSegments = [...composition.segments];
                            updatedSegments[0] = {
                              ...updatedSegments[0],
                              audio: newAudio,
                            };
                            setComposition({
                              ...composition,
                              segments: updatedSegments,
                            });
                          }}
                          onSfxChange={(newSfx) => {
                            setComposition({
                              ...composition,
                              sfx: newSfx,
                            });
                          }}
                          onBrollChange={(newBroll) => {
                            setComposition({
                              ...composition,
                              broll_inserts: newBroll,
                            });
                          }}
                        />
                      ) : (
                        <div className="py-6 text-center text-xs text-gray-500">
                          {isCompLoading ? (
                            <span className="flex items-center justify-center gap-1.5">
                              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading composition...
                            </span>
                          ) : (
                            "No composition loaded for this clip."
                          )}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                )}

                {activeEditorTab === "trim" && (
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base flex items-center gap-2">
                      <Layers className="w-4 h-4" />
                      Fine Controls
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-5">
                    <div className="space-y-3">
                      <div className="flex items-center justify-between text-sm">
                        <span className="flex items-center gap-2"><SplitSquareVertical className="w-4 h-4" />Split</span>
                        <span>{splitTime.toFixed(2)}s</span>
                      </div>
                      <Slider
                        min={MIN_GAP_SECONDS}
                        max={Math.max((selectedClip?.duration || MIN_GAP_SECONDS) - MIN_GAP_SECONDS, MIN_GAP_SECONDS)}
                        value={[splitTime]}
                        step={0.01}
                        onValueChange={(value) => setSplitTime(value[0] || MIN_GAP_SECONDS)}
                      />
                      <div className="grid grid-cols-2 gap-2">
                        <Button variant="outline" onClick={() => setSplitTime(currentTime)} disabled={!selectedClip}>Set to Playhead</Button>
                        <Button variant="outline" onClick={() => void handleSplit()} disabled={isSaving || !selectedClip}>Split Clip</Button>
                      </div>
                    </div>

                    <div className="space-y-3">
                      <div className="text-sm font-medium flex items-center gap-2"><AudioLines className="w-4 h-4" />Audio</div>
                      <div className="space-y-2">
                        <div className="flex items-center justify-between text-xs text-gray-600">
                          <span>Volume</span>
                          <span>{volume}%</span>
                        </div>
                        <Slider min={0} max={200} step={1} value={[volume]} onValueChange={(v) => setVolume(v[0] || 0)} />
                      </div>
                      <div className="space-y-2">
                        <div className="flex items-center justify-between text-xs text-gray-600">
                          <span>Playback Rate</span>
                          <span>{playbackRate.toFixed(2)}x</span>
                        </div>
                        <Slider min={0.5} max={2} step={0.05} value={[playbackRate]} onValueChange={(v) => setPlaybackRate(v[0] || 1)} />
                      </div>
                      <Button variant="outline" className="w-full" onClick={() => setIsMuted((m) => !m)}>
                        {isMuted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
                        {isMuted ? "Unmute" : "Mute"}
                      </Button>
                    </div>

                    <div className="space-y-3">
                      <div className="text-sm font-medium flex items-center gap-2"><Palette className="w-4 h-4" />Video FX</div>
                      {[
                        ["Brightness", "brightness", 40, 180, 1],
                        ["Contrast", "contrast", 40, 180, 1],
                        ["Saturation", "saturation", 0, 220, 1],
                        ["Blur", "blur", 0, 8, 0.1],
                        ["Hue", "hue", -180, 180, 1],
                        ["Zoom", "zoom", 1, 2, 0.01],
                      ].map(([label, key, min, max, step]) => {
                        const typedKey = key as keyof VideoFx;
                        const currentValue = videoFx[typedKey];
                        return (
                          <div key={key} className="space-y-1.5">
                            <div className="flex items-center justify-between text-xs text-gray-600">
                              <span>{label}</span>
                              <span>{currentValue}</span>
                            </div>
                            <Slider
                              min={Number(min)}
                              max={Number(max)}
                              step={Number(step)}
                              value={[currentValue]}
                              onValueChange={(value) => setVideoFx((current) => ({ ...current, [typedKey]: value[0] ?? currentValue }))}
                            />
                          </div>
                        );
                      })}
                    </div>

                    <Button variant="outline" className="w-full" onClick={resetPreviewAdjustments}>
                      <Gauge className="w-4 h-4" />
                      Reset Preview Adjustments
                    </Button>
                  </CardContent>
                </Card>
                )}

                {activeEditorTab === "subtitles" && (
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base flex items-center gap-2">
                      <Subtitles className="w-4 h-4" />
                      Subtitle Text
                    </CardTitle>
                    <p className="text-xs text-gray-500">
                      Edit the caption script. Words stay in sync with the clip audio.
                    </p>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between">
                        <label htmlFor="caption-text" className="text-sm font-medium text-gray-800">
                          Caption script
                        </label>
                        <span className="text-xs text-gray-500">{subtitleWords.length} words</span>
                      </div>
                      <textarea
                        id="caption-text"
                        value={captionText}
                        onChange={(e) => setCaptionText(e.target.value)}
                        placeholder="Type or paste the caption text that appears on the clip..."
                        className="w-full min-h-40 resize-y rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-sm leading-relaxed text-gray-900 shadow-sm placeholder:text-gray-400 focus:border-gray-900 focus:outline-none focus:ring-2 focus:ring-gray-900/20"
                      />
                    </div>

                    <div className="grid grid-cols-2 gap-2">
                      <Select value={captionPosition} onValueChange={setCaptionPosition}>
                        <SelectTrigger>
                          <SelectValue placeholder="Position" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="top">Top</SelectItem>
                          <SelectItem value="middle">Middle</SelectItem>
                          <SelectItem value="bottom">Bottom</SelectItem>
                        </SelectContent>
                      </Select>

                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => setCaptionText(selectedClip?.text || "")}
                        disabled={isSaving || !selectedClip}
                      >
                        <RotateCcw className="w-4 h-4" />
                        Reset to Transcript
                      </Button>
                    </div>

                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-xs text-gray-600">
                        <span>Subtitle Size</span>
                        <span>{subtitleSize}</span>
                      </div>
                      <Slider min={28} max={88} step={1} value={[subtitleSize]} onValueChange={(v) => setSubtitleSize(v[0] || 52)} />
                    </div>

                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-xs text-gray-600">
                        <span>Vertical Offset</span>
                        <span>{subtitleY}%</span>
                      </div>
                      <Slider min={10} max={85} step={1} value={[subtitleY]} onValueChange={(v) => setSubtitleY(v[0] || 78)} />
                    </div>

                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-600">Highlight words (click to toggle)</span>
                        {highlightWords.length > 0 && (
                          <button
                            type="button"
                            onClick={() => setHighlightWords([])}
                            className="text-xs text-gray-500 underline underline-offset-2 hover:text-gray-800"
                          >
                            Clear all
                          </button>
                        )}
                      </div>
                      <div className="max-h-32 overflow-y-auto rounded-lg border border-gray-200 p-2 flex flex-wrap gap-1.5">
                        {subtitleWords.length === 0 ? (
                          <span className="text-xs text-gray-500">Type text above to see words.</span>
                        ) : (
                          subtitleWords.map((word, index) => {
                            const cleaned = word.toLowerCase().replace(/[^a-z0-9']/g, "");
                            const highlighted = cleaned ? highlightWords.includes(cleaned) : false;
                            return (
                              <button
                                key={`${word}-${index}`}
                                type="button"
                                onClick={() => toggleHighlightedWord(word)}
                                className={`px-2 py-1 rounded-md text-xs font-medium border transition ${
                                  highlighted
                                    ? "bg-yellow-100 border-yellow-300 text-yellow-900"
                                    : "bg-white border-gray-200 text-gray-700 hover:border-gray-400"
                                }`}
                              >
                                {word}
                              </button>
                            );
                          })
                        )}
                      </div>
                    </div>

                    <Button onClick={handleUpdateCaptions} disabled={isSaving || !selectedClip} className="w-full">
                      {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                      {isSaving ? "Rendering..." : captionSaved ? "Saved ✓" : "Save & Re-render Captions"}
                    </Button>
                  </CardContent>
                </Card>
                )}
              </div>
            </div>

            <Card>
              <CardHeader>
                <CardTitle className="text-base flex items-center gap-2">
                  <Clapperboard className="w-4 h-4" />
                  Clips
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {mergeSelection.length >= 2 && (
                  <div className="flex items-center gap-2 justify-end">
                    <Select value={transitionSpec} onValueChange={setTransitionSpec} disabled={isSaving}>
                      <SelectTrigger className="w-56">
                        <SelectValue placeholder="Transisi" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">Tanpa transisi</SelectItem>
                        {availableTransitions
                          .filter((transition) => transition.kind === "builtin" || transition.kind === "file")
                          .map((transition) => {
                            const isBuiltin = transition.kind === "builtin";
                            return (
                              <SelectItem
                                key={`${isBuiltin ? "xfade" : "file"}:${transition.name}`}
                                value={`${isBuiltin ? "xfade" : "file"}:${transition.name}`}
                              >
                                {transition.display_name || transition.name}
                                {isBuiltin ? "" : " (file)"}
                              </SelectItem>
                            );
                          })}
                      </SelectContent>
                    </Select>
                    <Button variant="outline" onClick={handleMerge} disabled={isSaving}>
                      Merge Selected ({mergeSelection.length})
                    </Button>
                  </div>
                )}
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                  {clips.map((clip) => {
                    const isActive = clip.id === selectedClipId;
                    const isSelectedForMerge = mergeSelection.includes(clip.id);
                    return (
                      <button
                        key={clip.id}
                        type="button"
                        onClick={() => setSelectedClipId(clip.id)}
                        className={`text-left rounded-lg border p-3 transition ${
                          isActive ? "border-black bg-gray-50" : "border-gray-200 hover:border-gray-400"
                        }`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div>
                            <p className="font-medium text-sm text-black">
                              {clip.hook_title || `Clip ${clip.clip_order}`}
                            </p>
                            <p className="text-xs text-gray-500">{clip.start_time} - {clip.end_time}</p>
                            <p className="text-xs text-gray-500">{formatDuration(clip.duration)}</p>
                            {clip.hook_type && clip.hook_type !== "none" && (
                              <Badge variant="outline" className="mt-1 text-[10px]">
                                {getHookTypeLabel(clip.hook_type)}
                              </Badge>
                            )}
                          </div>
                          <label className="flex items-center gap-1 text-xs text-gray-600" onClick={(e) => e.stopPropagation()}>
                            <input type="checkbox" checked={isSelectedForMerge} onChange={() => toggleMergeSelection(clip.id)} />
                            Merge
                          </label>
                        </div>
                        {clipRenderProgress[clip.id] != null && (
                          <div className="mt-2 space-y-1">
                            <div className="flex items-center justify-between text-[10px] text-blue-600">
                              <span className="font-medium flex items-center gap-1">
                                <Loader2 className="w-3 h-3 animate-spin" />
                                Rendering captions
                              </span>
                              <span>{clipRenderProgress[clip.id]}%</span>
                            </div>
                            <Progress value={clipRenderProgress[clip.id] ?? 0} className="h-1.5" />
                          </div>
                        )}
                      </button>
                    );
                  })}
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
