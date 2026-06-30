export type PartName =
  | "drums"
  | "bass"
  | "bass_treble"
  | "other"
  | "vocals"
  | "guitar"
  | "piano"
  | "click";

export type OutputFormat = "musicxml" | "midi" | "wav" | "mp3" | "pdf";

export interface Rhythm {
  tempo: number;
  beats: number[];
  time_signature: string;
}

export interface AnalysisResult {
  event: "done";
  stage: "analyze";
  job_id: string;
  job_dir: string;
  input: string;
  source_wav: string;
  parts: PartName[];
  pitched_parts: PartName[];
  stems: Record<string, string>;
  /** Compressed per-part previews for in-app playback (streamed, not decoded).
   * Absent on jobs analysed before previews existed — fall back to `stems`. */
  previews?: Record<string, string>;
  rhythm: Rhythm;
}

export interface ProgressPayload {
  event: "progress";
  stage: string;
  pct: number;
  msg: string;
}

export interface ExportArtifact {
  part: string;
  format: string;
  path?: string;
  skipped?: string;
}

export interface ExportResult {
  event: "done";
  stage: "export";
  job_dir: string;
  artifacts: ExportArtifact[];
}

/** Per-part swatch colour. Display names are localized via i18n (`part.<name>`),
 * not stored here. */
export const PART_META: Record<PartName, { color: string }> = {
  bass: { color: "#7c5cff" },
  bass_treble: { color: "#9d86ff" },
  vocals: { color: "#ff5c8a" },
  guitar: { color: "#ffb15c" },
  piano: { color: "#41d6a0" },
  drums: { color: "#8a93a6" },
  other: { color: "#5c9dff" },
  click: { color: "#d2d6df" },
};

export const PART_ORDER: PartName[] = [
  "bass",
  "bass_treble",
  "vocals",
  "guitar",
  "piano",
  "drums",
  "other",
  "click",
];
