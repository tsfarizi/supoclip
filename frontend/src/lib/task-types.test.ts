import { describe, expect, it } from "vitest";
import {
  ACTIVE_TASK_STATUSES,
  DEFAULT_VIDEO_FX,
  EXPORT_DIMENSIONS,
  MIN_GAP_SECONDS,
  clamp,
  formatDuration,
  getClipUrl,
  getHookTypeLabel,
  getScoreColor,
  getViralityBgColor,
  getViralityColor,
} from "./task-types";

describe("getClipUrl", () => {
  it("leaves already-prefixed /api/ paths untouched", () => {
    expect(getClipUrl("/api/clips/123.mp4")).toBe("/api/clips/123.mp4");
  });

  it("prepends /api to unprefixed relative paths", () => {
    expect(getClipUrl("/clips/123.mp4")).toBe("/api/clips/123.mp4");
    expect(getClipUrl("/tasks/1/clips/2.mp4")).toBe("/api/tasks/1/clips/2.mp4");
  });

  it("prepends /api to any string that does not start with /api/ (documented envelope)", () => {
    expect(getClipUrl("https://cdn.example.com/v.mp4")).toBe("/apihttps://cdn.example.com/v.mp4");
  });
});

describe("formatDuration", () => {
  it("formats zero and sub-minute durations", () => {
    expect(formatDuration(0)).toBe("0:00");
    expect(formatDuration(59)).toBe("0:59");
  });

  it("rolls over at 60 seconds", () => {
    expect(formatDuration(60)).toBe("1:00");
    expect(formatDuration(65.7)).toBe("1:05");
  });

  it("formats multi-minute durations", () => {
    expect(formatDuration(600)).toBe("10:00");
    expect(formatDuration(3599)).toBe("59:59");
  });
});

describe("clamp", () => {
  it("returns the value when inside bounds", () => {
    expect(clamp(5, 0, 10)).toBe(5);
    expect(clamp(0, 0, 10)).toBe(0);
    expect(clamp(10, 0, 10)).toBe(10);
  });

  it("clamps below the minimum", () => {
    expect(clamp(-1, 0, 10)).toBe(0);
  });

  it("clamps above the maximum", () => {
    expect(clamp(11, 0, 10)).toBe(10);
  });
});

describe("getScoreColor", () => {
  it("returns green for scores at or above 0.8", () => {
    expect(getScoreColor(0.8)).toBe("bg-green-100 text-green-800");
    expect(getScoreColor(1)).toBe("bg-green-100 text-green-800");
  });

  it("returns yellow for scores in [0.6, 0.8)", () => {
    expect(getScoreColor(0.79)).toBe("bg-yellow-100 text-yellow-800");
    expect(getScoreColor(0.6)).toBe("bg-yellow-100 text-yellow-800");
  });

  it("returns red for scores below 0.6", () => {
    expect(getScoreColor(0.59)).toBe("bg-red-100 text-red-800");
    expect(getScoreColor(0)).toBe("bg-red-100 text-red-800");
  });
});

describe("getViralityColor", () => {
  it("returns green at or above 80", () => {
    expect(getViralityColor(80)).toBe("text-green-600");
    expect(getViralityColor(100)).toBe("text-green-600");
  });

  it("returns yellow in [60, 80)", () => {
    expect(getViralityColor(79)).toBe("text-yellow-600");
    expect(getViralityColor(60)).toBe("text-yellow-600");
  });

  it("returns orange in [40, 60)", () => {
    expect(getViralityColor(59)).toBe("text-orange-600");
    expect(getViralityColor(40)).toBe("text-orange-600");
  });

  it("returns red below 40", () => {
    expect(getViralityColor(39)).toBe("text-red-600");
    expect(getViralityColor(0)).toBe("text-red-600");
  });
});

describe("getViralityBgColor", () => {
  it("returns the matching bg class at each threshold", () => {
    expect(getViralityBgColor(80)).toBe("bg-green-500");
    expect(getViralityBgColor(60)).toBe("bg-yellow-500");
    expect(getViralityBgColor(40)).toBe("bg-orange-500");
    expect(getViralityBgColor(0)).toBe("bg-red-500");
  });
});

describe("getHookTypeLabel", () => {
  it("maps known hook types to display labels", () => {
    expect(getHookTypeLabel("question")).toBe("Question Hook");
    expect(getHookTypeLabel("statement")).toBe("Bold Statement");
    expect(getHookTypeLabel("statistic")).toBe("Data/Stats");
    expect(getHookTypeLabel("story")).toBe("Story Hook");
    expect(getHookTypeLabel("contrast")).toBe("Contrast Hook");
    expect(getHookTypeLabel("none")).toBe("No Hook");
  });

  it("falls back for null, undefined, and empty string", () => {
    // Perilaku baseline: hookType || "none" memicu lookup labels["none"] = "No Hook".
    expect(getHookTypeLabel(null)).toBe("No Hook");
    expect(getHookTypeLabel(undefined)).toBe("No Hook");
    expect(getHookTypeLabel("")).toBe("No Hook");
  });

  it("returns unknown hook types verbatim", () => {
    expect(getHookTypeLabel("custom_hook")).toBe("custom_hook");
  });
});

describe("shared constants", () => {
  it("ACTIVE_TASK_STATUSES covers every in-progress status used by home duplicate detection", () => {
    expect(ACTIVE_TASK_STATUSES).toEqual([
      "queued",
      "pending",
      "processing",
      "downloading",
      "transcribing",
      "analyzing",
      "generating_clips",
    ]);
    expect(ACTIVE_TASK_STATUSES).toContain("processing");
    expect(ACTIVE_TASK_STATUSES).not.toContain("completed");
  });

  it("MIN_GAP_SECONDS is the editor minimum gap", () => {
    expect(MIN_GAP_SECONDS).toBe(0.25);
  });

  it("DEFAULT_VIDEO_FX pins the editor default effects", () => {
    expect(DEFAULT_VIDEO_FX).toEqual({
      brightness: 100,
      contrast: 100,
      saturation: 100,
      blur: 0,
      hue: 0,
      zoom: 1,
    });
  });

  it("EXPORT_DIMENSIONS maps every preset to 1080x1920", () => {
    expect(EXPORT_DIMENSIONS.tiktok).toEqual({ width: 1080, height: 1920 });
    expect(EXPORT_DIMENSIONS.reels).toEqual({ width: 1080, height: 1920 });
    expect(EXPORT_DIMENSIONS.shorts).toEqual({ width: 1080, height: 1920 });
  });
});
