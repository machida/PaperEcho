import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { open, save } from "@tauri-apps/plugin-dialog";

import type {
  AnalysisResult,
  ExportResult,
  OutputFormat,
  ProgressPayload,
} from "./types";

const PROGRESS_EVENT = "pipeline://progress";
const AUDIO_EXTENSIONS = ["mp3", "wav", "m4a", "aiff", "aif"];

/** Subscribe to pipeline progress. Returns an unlisten function. */
export function onProgress(
  cb: (p: ProgressPayload) => void,
): Promise<UnlistenFn> {
  return listen<ProgressPayload>(PROGRESS_EVENT, (e) => cb(e.payload));
}

export function analyze(
  filePath: string,
  transcribeParts: string[],
): Promise<AnalysisResult> {
  return invoke<AnalysisResult>("analyze", { filePath, transcribeParts });
}

const RUNTIME_PROGRESS_EVENT = "runtime://progress";

/** Whether the Python ML runtime is present. On first launch of a packaged build
 * it isn't — the app must download it before anything else works. */
export interface RuntimeStatus {
  ready: boolean;
  version: string;
  arch: string;
  url: string;
}

export interface RuntimeProgress {
  phase: "download" | "verify" | "extract" | "done";
  downloaded: number;
  total: number;
}

export function runtimeStatus(): Promise<RuntimeStatus> {
  return invoke<RuntimeStatus>("runtime_status");
}

/** Fetch + verify + extract the Python runtime. Resolves when ready. */
export function downloadRuntime(): Promise<void> {
  return invoke("download_runtime");
}

/** Subscribe to runtime-download progress. Returns an unlisten function. */
export function onRuntimeProgress(
  cb: (p: RuntimeProgress) => void,
): Promise<UnlistenFn> {
  return listen<RuntimeProgress>(RUNTIME_PROGRESS_EVENT, (e) => cb(e.payload));
}

/** "fixed" snaps the score to a metronomic grid; "variable" keeps the detected
 * per-beat timing (faithful to live/rubato recordings). */
export type TempoMode = "fixed" | "variable";

/** Manual reading aids applied when rendering a score (export & preview). */
export interface ScoreControls {
  tempoMult: number;
  beatOffset: number;
  keySharps: number | null;
  tempoMode: TempoMode;
  octaveShift: number;
}

export function exportParts(
  jobDir: string,
  parts: string[],
  formats: OutputFormat[],
  destDir: string,
  controls: ScoreControls,
): Promise<ExportResult> {
  return invoke<ExportResult>("export", {
    jobDir,
    parts,
    formats,
    destDir,
    ...controls,
  });
}

export interface PreviewResult {
  part: string;
  musicxml: string;
}

/** Render one part's score (with tempo/nudge/key) to a MusicXML string for preview. */
export function previewScore(
  jobDir: string,
  part: string,
  controls: ScoreControls,
): Promise<PreviewResult> {
  return invoke<PreviewResult>("preview", {
    jobDir,
    part,
    ...controls,
  });
}

/** Ask the user where to save the exported files. Returns null if cancelled. */
export async function pickExportDir(): Promise<string | null> {
  const selected = await open({ directory: true, multiple: false });
  return typeof selected === "string" ? selected : null;
}

export function reveal(path: string): Promise<void> {
  return invoke("reveal", { path });
}

/** Mix the stems at the given per-part gains into an mp3 at `dest`. */
export function mixdown(
  jobDir: string,
  gains: Record<string, number>,
  dest: string,
): Promise<{ path: string }> {
  return invoke<{ path: string }>("mixdown", { jobDir, gains, dest });
}

/** Native save dialog for an mp3. Returns the chosen path or null. */
export async function pickSaveMp3(defaultName: string): Promise<string | null> {
  const selected = await save({
    defaultPath: defaultName,
    filters: [{ name: "MP3", extensions: ["mp3"] }],
  });
  return selected ?? null;
}

/** Open the native file picker filtered to supported audio formats. */
export async function pickAudioFile(): Promise<string | null> {
  const selected = await open({
    multiple: false,
    directory: false,
    filters: [{ name: "Audio", extensions: AUDIO_EXTENSIONS }],
  });
  return typeof selected === "string" ? selected : null;
}

export function isSupportedAudio(path: string): boolean {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return AUDIO_EXTENSIONS.includes(ext);
}
