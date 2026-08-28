export type ReframeMode = "track" | "crop" | "zoompan";

export interface ReframeBox {
  x: number; // 0.0 - 1.0
  y: number; // 0.0 - 1.0
  w: number; // 0.0 - 1.0
  h: number; // 0.0 - 1.0
}

export interface ReframeSpec {
  mode: ReframeMode;
  box?: ReframeBox | null;
  track_target?: string | null;
}

export interface SpeedSpec {
  rate: number;
  ramp?: any[] | null;
}

export interface AudioSpec {
  take_source: boolean;
  gain_db: number;
  ducking?: Record<string, any> | null;
}

export interface CaptionSpec {
  text_override?: string | null;
  font_family?: string | null;
  font_size?: number | null;
  font_color?: string | null;
  template: string;
  position: string;
  highlight_words: string[];
}

export interface SegmentSpec {
  id: string;
  source_start: number;
  source_end: number;
  reframe: ReframeSpec;
  speed: SpeedSpec;
  audio: AudioSpec;
  caption: CaptionSpec;
}

export interface BrollInsertSpec {
  id: string;
  at_time: number;
  duration: number;
  search_term: string;
  asset_path?: string | null;
  reframe?: ReframeSpec | null;
}

export interface SoundFxSpec {
  id: string;
  at_time: number;
  duration: number;
  query: string;
  intensity: string;
  gain_db: number;
  placement: string;
  asset_path?: string | null;
}

export interface SoundtrackSpec {
  asset_path?: string | null;
  volume: number;
}

export interface TransitionSpec {
  between: [string, string];
  type: string;
  duration: number;
}

export interface SourceAssetRef {
  source_id?: string | null;
  source_type?: string | null;
  source_url?: string | null;
  source_identity?: string | null;
}

export interface OutputSpec {
  format: string;
  preset: string;
}

export interface Composition {
  schema_version: number;
  source_asset_ref: SourceAssetRef;
  output: OutputSpec;
  segments: SegmentSpec[];
  broll_inserts: BrollInsertSpec[];
  sfx: SoundFxSpec[];
  soundtrack?: SoundtrackSpec | null;
  transitions: TransitionSpec[];
}

export interface CompositionResponse {
  composition: Composition;
  composition_version: number;
}
