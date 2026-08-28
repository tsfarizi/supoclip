"use client";

import { useEffect, useState } from "react";
import { Crop, Move, Sparkles } from "lucide-react";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ReframeMode, ReframeSpec } from "@/lib/composition-types";

interface FramingControlProps {
  reframe: ReframeSpec;
  onChange: (updated: ReframeSpec) => void;
}

export function FramingControl({ reframe, onChange }: FramingControlProps) {
  const mode = reframe.mode || "track";
  const box = reframe.box || { x: 0.25, y: 0.1, w: 0.5, h: 0.8 };

  const handleModeChange = (newMode: ReframeMode) => {
    if (newMode === "crop" && !reframe.box) {
      onChange({
        ...reframe,
        mode: newMode,
        box: { x: 0.25, y: 0.1, w: 0.5, h: 0.8 },
      });
    } else {
      onChange({
        ...reframe,
        mode: newMode,
      });
    }
  };

  const handleBoxChange = (field: "x" | "y" | "w" | "h", value: number) => {
    onChange({
      ...reframe,
      mode: "crop",
      box: {
        ...box,
        [field]: Number(value.toFixed(2)),
      },
    });
  };

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <Label className="text-xs font-semibold text-gray-700">Framing Mode</Label>
        <Select value={mode} onValueChange={(v) => handleModeChange(v as ReframeMode)}>
          <SelectTrigger className="w-full">
            <SelectValue placeholder="Select mode" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="track">
              <span className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-purple-600" />
                <span>Face Track (Auto Center)</span>
              </span>
            </SelectItem>
            <SelectItem value="crop">
              <span className="flex items-center gap-2">
                <Crop className="w-4 h-4 text-blue-600" />
                <span>Manual Crop</span>
              </span>
            </SelectItem>
            <SelectItem value="zoompan">
              <span className="flex items-center gap-2">
                <Move className="w-4 h-4 text-emerald-600" />
                <span>Zoom &amp; Pan (Dynamic)</span>
              </span>
            </SelectItem>
          </SelectContent>
        </Select>
      </div>

      {mode === "crop" && (
        <div className="space-y-3 p-3 bg-gray-50 border rounded-lg">
          <div className="flex items-center justify-between text-xs text-gray-600">
            <span>Crop Framing Box</span>
            <span className="font-mono text-[11px]">
              X:{Math.round(box.x * 100)}% Y:{Math.round(box.y * 100)}% W:{Math.round(box.w * 100)}% H:{Math.round(box.h * 100)}%
            </span>
          </div>

          {/* Visual Crop Feedback Box */}
          <div className="relative w-full aspect-[16/9] bg-gray-200 rounded border overflow-hidden flex items-center justify-center">
            <div className="absolute inset-0 bg-black/40" />
            <div
              className="absolute border-2 border-primary bg-primary/20 rounded shadow-sm flex items-center justify-center pointer-events-none"
              style={{
                left: `${box.x * 100}%`,
                top: `${box.y * 100}%`,
                width: `${box.w * 100}%`,
                height: `${box.h * 100}%`,
              }}
            >
              <span className="text-[10px] font-semibold text-white drop-shadow bg-black/50 px-1 rounded">
                Crop Area
              </span>
            </div>
          </div>

          <div className="space-y-2 pt-1">
            <div className="space-y-1">
              <div className="flex justify-between text-xs">
                <span>Horizontal Offset (X)</span>
                <span>{Math.round(box.x * 100)}%</span>
              </div>
              <Slider
                value={[box.x * 100]}
                min={0}
                max={100 - box.w * 100}
                step={1}
                onValueChange={([val]) => handleBoxChange("x", val / 100)}
              />
            </div>

            <div className="space-y-1">
              <div className="flex justify-between text-xs">
                <span>Vertical Offset (Y)</span>
                <span>{Math.round(box.y * 100)}%</span>
              </div>
              <Slider
                value={[box.y * 100]}
                min={0}
                max={100 - box.h * 100}
                step={1}
                onValueChange={([val]) => handleBoxChange("y", val / 100)}
              />
            </div>

            <div className="space-y-1">
              <div className="flex justify-between text-xs">
                <span>Crop Width</span>
                <span>{Math.round(box.w * 100)}%</span>
              </div>
              <Slider
                value={[box.w * 100]}
                min={10}
                max={100}
                step={1}
                onValueChange={([val]) => {
                  const newW = val / 100;
                  const newX = Math.min(box.x, 1.0 - newW);
                  onChange({
                    ...reframe,
                    mode: "crop",
                    box: { ...box, w: newW, x: newX },
                  });
                }}
              />
            </div>

            <div className="space-y-1">
              <div className="flex justify-between text-xs">
                <span>Crop Height</span>
                <span>{Math.round(box.h * 100)}%</span>
              </div>
              <Slider
                value={[box.h * 100]}
                min={10}
                max={100}
                step={1}
                onValueChange={([val]) => {
                  const newH = val / 100;
                  const newY = Math.min(box.y, 1.0 - newH);
                  onChange({
                    ...reframe,
                    mode: "crop",
                    box: { ...box, h: newH, y: newY },
                  });
                }}
              />
            </div>
          </div>
        </div>
      )}

      {mode === "track" && (
        <p className="text-xs text-gray-500 italic">
          Automatic vertical framing detects primary speakers and keeps faces centered.
        </p>
      )}

      {mode === "zoompan" && (
        <p className="text-xs text-gray-500 italic">
          Subtle dynamic zoom and slow pan effect applied automatically across segment duration.
        </p>
      )}
    </div>
  );
}
