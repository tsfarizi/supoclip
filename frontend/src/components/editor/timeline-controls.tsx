"use client";

import { useState } from "react";
import { Gauge, Plus, Trash2, Volume2, Film } from "lucide-react";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import type { AudioSpec, BrollInsertSpec, SoundFxSpec, SpeedSpec } from "@/lib/composition-types";

interface TimelineControlsProps {
  speed: SpeedSpec;
  audio: AudioSpec;
  sfxList: SoundFxSpec[];
  brollList: BrollInsertSpec[];
  onSpeedChange: (speed: SpeedSpec) => void;
  onAudioChange: (audio: AudioSpec) => void;
  onSfxChange: (sfx: SoundFxSpec[]) => void;
  onBrollChange: (broll: BrollInsertSpec[]) => void;
}

export function TimelineControls({
  speed,
  audio,
  sfxList,
  brollList,
  onSpeedChange,
  onAudioChange,
  onSfxChange,
  onBrollChange,
}: TimelineControlsProps) {
  // Speed rate (0.5x to 2.0x)
  const currentSpeed = speed.rate || 1.0;
  // Audio gain (-12dB to +6dB)
  const currentGain = audio.gain_db || 0.0;

  const [newSfxQuery, setNewSfxQuery] = useState("");
  const [newSfxTime, setNewSfxTime] = useState("0.0");

  const [newBrollTerm, setNewBrollTerm] = useState("");
  const [newBrollTime, setNewBrollTime] = useState("0.0");
  const [newBrollDur, setNewBrollDur] = useState("3.0");

  const handleAddSfx = () => {
    if (!newSfxQuery.trim()) return;
    const newSfx: SoundFxSpec = {
      id: `sfx_${Math.random().toString(36).substring(2, 9)}`,
      at_time: Math.max(0, parseFloat(newSfxTime) || 0),
      duration: 1.5,
      query: newSfxQuery.trim(),
      intensity: "moderate",
      gain_db: 0.0,
      placement: "main",
    };
    onSfxChange([...sfxList, newSfx]);
    setNewSfxQuery("");
  };

  const handleRemoveSfx = (id: string) => {
    onSfxChange(sfxList.filter((item) => item.id !== id));
  };

  const handleAddBroll = () => {
    if (!newBrollTerm.trim()) return;
    const newBroll: BrollInsertSpec = {
      id: `broll_${Math.random().toString(36).substring(2, 9)}`,
      at_time: Math.max(0, parseFloat(newBrollTime) || 0),
      duration: Math.max(0.5, parseFloat(newBrollDur) || 3.0),
      search_term: newBrollTerm.trim(),
    };
    onBrollChange([...brollList, newBroll]);
    setNewBrollTerm("");
  };

  const handleRemoveBroll = (id: string) => {
    onBrollChange(brollList.filter((item) => item.id !== id));
  };

  return (
    <div className="space-y-6">
      {/* Playback Speed Control */}
      <div className="space-y-2 p-3 bg-gray-50 border rounded-lg">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1.5 text-gray-700">
            <Gauge className="w-4 h-4 text-blue-600" />
            Playback Speed
          </span>
          <span className="text-xs font-mono font-medium px-2 py-0.5 bg-white border rounded">
            {currentSpeed.toFixed(2)}x
          </span>
        </div>
        <Slider
          value={[currentSpeed]}
          min={0.5}
          max={2.0}
          step={0.05}
          onValueChange={([val]) => onSpeedChange({ ...speed, rate: Number(val.toFixed(2)) })}
        />
        <div className="flex justify-between text-[10px] text-gray-400">
          <span>0.5x</span>
          <span>1.0x (Normal)</span>
          <span>2.0x</span>
        </div>
      </div>

      {/* Audio Gain Control */}
      <div className="space-y-2 p-3 bg-gray-50 border rounded-lg">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1.5 text-gray-700">
            <Volume2 className="w-4 h-4 text-emerald-600" />
            Audio Gain / Volume
          </span>
          <span className="text-xs font-mono font-medium px-2 py-0.5 bg-white border rounded">
            {currentGain > 0 ? `+${currentGain.toFixed(1)}` : currentGain.toFixed(1)} dB
          </span>
        </div>
        <Slider
          value={[currentGain]}
          min={-12.0}
          max={6.0}
          step={0.5}
          onValueChange={([val]) => onAudioChange({ ...audio, gain_db: Number(val.toFixed(1)) })}
        />
        <div className="flex justify-between text-[10px] text-gray-400">
          <span>-12 dB</span>
          <span>0 dB</span>
          <span>+6 dB</span>
        </div>
      </div>

      {/* Sound Effects (SFX) List */}
      <div className="space-y-3 p-3 bg-gray-50 border rounded-lg">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1.5 text-gray-700">
            <Volume2 className="w-4 h-4 text-purple-600" />
            Sound Effects (SFX)
          </span>
          <span className="text-[11px] text-gray-500">{sfxList.length} items</span>
        </div>

        <div className="space-y-2">
          {sfxList.map((item) => (
            <div key={item.id} className="flex items-center justify-between p-2 bg-white border rounded text-xs">
              <div>
                <span className="font-medium text-gray-800">{item.query}</span>
                <span className="text-gray-400 ml-2 text-[11px]">at {item.at_time.toFixed(1)}s ({item.duration}s)</span>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6 text-red-500 hover:text-red-700 hover:bg-red-50"
                onClick={() => handleRemoveSfx(item.id)}
              >
                <Trash2 className="w-3.5 h-3.5" />
              </Button>
            </div>
          ))}
          {sfxList.length === 0 && (
            <p className="text-[11px] text-gray-400 italic">No sound effects added.</p>
          )}
        </div>

        <div className="flex gap-2 pt-1">
          <Input
            placeholder="SFX search (e.g. whoosh, pop)"
            value={newSfxQuery}
            onChange={(e) => setNewSfxQuery(e.target.value)}
            className="h-8 text-xs flex-1"
          />
          <Input
            type="number"
            placeholder="At (s)"
            value={newSfxTime}
            onChange={(e) => setNewSfxTime(e.target.value)}
            className="h-8 w-16 text-xs"
            min={0}
            step={0.5}
          />
          <Button size="sm" className="h-8 text-xs" onClick={handleAddSfx}>
            <Plus className="w-3.5 h-3.5 mr-1" /> Add
          </Button>
        </div>
      </div>

      {/* B-Roll Inserts List */}
      <div className="space-y-3 p-3 bg-gray-50 border rounded-lg">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold flex items-center gap-1.5 text-gray-700">
            <Film className="w-4 h-4 text-amber-600" />
            B-Roll Overlays
          </span>
          <span className="text-[11px] text-gray-500">{brollList.length} items</span>
        </div>

        <div className="space-y-2">
          {brollList.map((item) => (
            <div key={item.id} className="flex items-center justify-between p-2 bg-white border rounded text-xs">
              <div>
                <span className="font-medium text-gray-800">{item.search_term}</span>
                <span className="text-gray-400 ml-2 text-[11px]">
                  at {item.at_time.toFixed(1)}s (dur: {item.duration.toFixed(1)}s)
                </span>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6 text-red-500 hover:text-red-700 hover:bg-red-50"
                onClick={() => handleRemoveBroll(item.id)}
              >
                <Trash2 className="w-3.5 h-3.5" />
              </Button>
            </div>
          ))}
          {brollList.length === 0 && (
            <p className="text-[11px] text-gray-400 italic">No B-Roll overlays added.</p>
          )}
        </div>

        <div className="flex gap-2 pt-1">
          <Input
            placeholder="B-roll keyword (e.g. city traffic)"
            value={newBrollTerm}
            onChange={(e) => setNewBrollTerm(e.target.value)}
            className="h-8 text-xs flex-1"
          />
          <Input
            type="number"
            placeholder="At (s)"
            value={newBrollTime}
            onChange={(e) => setNewBrollTime(e.target.value)}
            className="h-8 w-16 text-xs"
            min={0}
            step={0.5}
          />
          <Input
            type="number"
            placeholder="Dur"
            value={newBrollDur}
            onChange={(e) => setNewBrollDur(e.target.value)}
            className="h-8 w-16 text-xs"
            min={0.5}
            step={0.5}
          />
          <Button size="sm" className="h-8 text-xs" onClick={handleAddBroll}>
            <Plus className="w-3.5 h-3.5 mr-1" /> Add
          </Button>
        </div>
      </div>
    </div>
  );
}
