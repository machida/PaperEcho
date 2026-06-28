import { useEffect, useMemo, useRef, useState } from "react";

import { StemMixer } from "../lib/audio";
import { ScorePreview } from "../components/ScorePreview";
import { Segmented } from "../components/Segmented";
import {
  exportParts,
  onProgress,
  pickExportDir,
  previewScore,
  reveal,
  type ScoreControls,
  type TempoMode,
} from "../lib/ipc";
import {
  PART_META,
  PART_ORDER,
  type AnalysisResult,
  type ExportArtifact,
  type OutputFormat,
  type PartName,
  type ProgressPayload,
} from "../lib/types";

interface ExportProps {
  result: AnalysisResult;
  onBack: () => void;
  onHome: () => void;
}

const FORMAT_OPTIONS: { id: OutputFormat; label: string; note?: string }[] = [
  { id: "musicxml", label: "MusicXML" },
  { id: "midi", label: "MIDI" },
  { id: "wav", label: "WAV" },
  { id: "mp3", label: "MP3" },
  { id: "pdf", label: "PDF", note: "needs MuseScore" },
];

// Derived (synthetic) parts built from another part's notes — offered in Export
// only, when their source part is scoreable. Not in the Analyze mixer (no audio).
const DERIVED_SOURCE: Partial<Record<PartName, PartName>> = {
  bass_treble: "bass",
};

// Key signatures by sharp (+) / flat (−) count. A signature is shared by a major
// key and its relative minor, so we label both. `null` (Auto) = music21's guess.
const KEY_OPTIONS: { sharps: number; label: string }[] = [
  { sharps: 0, label: "C / Am" },
  { sharps: 1, label: "G / Em (1♯)" },
  { sharps: 2, label: "D / Bm (2♯)" },
  { sharps: 3, label: "A / F♯m (3♯)" },
  { sharps: 4, label: "E / C♯m (4♯)" },
  { sharps: 5, label: "B / G♯m (5♯)" },
  { sharps: 6, label: "F♯ / D♯m (6♯)" },
  { sharps: 7, label: "C♯ / A♯m (7♯)" },
  { sharps: -1, label: "F / Dm (1♭)" },
  { sharps: -2, label: "B♭ / Gm (2♭)" },
  { sharps: -3, label: "E♭ / Cm (3♭)" },
  { sharps: -4, label: "A♭ / Fm (4♭)" },
  { sharps: -5, label: "D♭ / B♭m (5♭)" },
  { sharps: -6, label: "G♭ / E♭m (6♭)" },
  { sharps: -7, label: "C♭ / A♭m (7♭)" },
];

export function Export({ result, onBack, onHome }: ExportProps) {
  const derivedParts = useMemo(
    () =>
      (Object.keys(DERIVED_SOURCE) as PartName[]).filter((d) =>
        result.pitched_parts.includes(DERIVED_SOURCE[d]!),
      ),
    [result.pitched_parts],
  );

  // A part can produce notation if it's a transcribed part or a derived one.
  const scoreable = useMemo(
    () => new Set<string>([...result.pitched_parts, ...derivedParts]),
    [result.pitched_parts, derivedParts],
  );

  const orderedParts = useMemo(
    () =>
      PART_ORDER.filter(
        (p) => result.parts.includes(p) || derivedParts.includes(p),
      ),
    [result.parts, derivedParts],
  );

  const [parts, setParts] = useState<Set<string>>(new Set(result.pitched_parts));
  const [formats, setFormats] = useState<Set<OutputFormat>>(
    new Set<OutputFormat>(["musicxml", "midi"]),
  );
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<ProgressPayload | null>(null);
  const [artifacts, setArtifacts] = useState<ExportArtifact[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [destDir, setDestDir] = useState<string | null>(null);
  const [tempoMult, setTempoMult] = useState(1);
  const [tempoMode, setTempoMode] = useState<TempoMode>("fixed");
  const [beatOffset, setBeatOffset] = useState(0);
  const [octaveShift, setOctaveShift] = useState(0);
  // null = auto-detect the key; a number pins the key signature (sharps +/flats −).
  const [keySharps, setKeySharps] = useState<number | null>(null);
  const controls: ScoreControls = {
    tempoMult,
    beatOffset,
    keySharps,
    tempoMode,
    octaveShift,
  };

  const detectedTempo = result.rhythm.tempo;
  const effectiveTempo = Math.round(detectedTempo * tempoMult * 10) / 10;

  // Parts the user can preview: selected AND able to produce notation.
  const previewableParts = useMemo(
    () => orderedParts.filter((p) => parts.has(p) && scoreable.has(p)),
    [orderedParts, parts, scoreable],
  );
  // Which part's notation is shown. Defaults to the first previewable part, but
  // the user can pick any other (the tabs in the preview header).
  const [previewSel, setPreviewSel] = useState<PartName | null>(null);
  const previewPart = useMemo(
    () =>
      (previewSel && previewableParts.includes(previewSel)
        ? previewSel
        : previewableParts[0]) ?? null,
    [previewSel, previewableParts],
  );
  // Stem to play alongside the preview. Derived parts (e.g. bass_treble) have no
  // stem of their own, so play their source part's audio (same line, concert
  // pitch). Null if no stem exists for this part.
  const previewStemPart = useMemo(() => {
    if (!previewPart) return null;
    const src = DERIVED_SOURCE[previewPart] ?? previewPart;
    return result.stems[src] ? src : null;
  }, [previewPart, result.stems]);
  const [previewXml, setPreviewXml] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  // Cache rendered MusicXML by part+tempo+nudge so revisiting a setting is instant
  // (each fresh render spawns Python + imports music21, ~7s).
  const previewCache = useRef<Map<string, string>>(new Map());

  useEffect(() => {
    if (!previewPart) {
      setPreviewXml(null);
      return;
    }
    const key = `${previewPart}|${tempoMult}|${tempoMode}|${beatOffset}|${keySharps}|${octaveShift}`;
    const cached = previewCache.current.get(key);
    if (cached) {
      setPreviewXml(cached);
      setPreviewLoading(false);
      return;
    }
    let cancelled = false;
    setPreviewLoading(true);
    const timer = window.setTimeout(() => {
      previewScore(result.job_dir, previewPart, controls)
        .then((res) => {
          if (cancelled) return;
          previewCache.current.set(key, res.musicxml);
          setPreviewXml(res.musicxml);
        })
        .catch(() => {
          if (!cancelled) setPreviewXml(null);
        })
        .finally(() => {
          if (!cancelled) setPreviewLoading(false);
        });
    }, 350); // debounce rapid tempo/nudge changes
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [result.job_dir, previewPart, tempoMult, tempoMode, beatOffset, keySharps, octaveShift]);

  const togglePart = (p: string) =>
    setParts((prev) => {
      const next = new Set(prev);
      next.has(p) ? next.delete(p) : next.add(p);
      return next;
    });

  const toggleFormat = (f: OutputFormat) =>
    setFormats((prev) => {
      const next = new Set(prev);
      next.has(f) ? next.delete(f) : next.add(f);
      return next;
    });

  const canRun = parts.size > 0 && formats.size > 0 && !running;

  const run = async () => {
    const dest = await pickExportDir();
    if (!dest) return; // user cancelled the folder picker
    setDestDir(dest);
    setRunning(true);
    setArtifacts(null);
    setError(null);
    let unlisten: (() => void) | undefined;
    try {
      unlisten = await onProgress(setProgress);
      const res = await exportParts(
        result.job_dir,
        [...parts],
        [...formats] as OutputFormat[],
        dest,
        controls,
      );
      setArtifacts(res.artifacts);
    } catch (e) {
      setError(String(e));
    } finally {
      unlisten?.();
      setRunning(false);
    }
  };

  const written = artifacts?.filter((a) => a.path) ?? [];
  const skipped = artifacts?.filter((a) => a.skipped) ?? [];

  return (
    <section className="export">
      <h2>Export</h2>

      <div className="export-grid">
        <fieldset>
          <legend>Parts</legend>
          {orderedParts.map((part) => {
            const meta = PART_META[part as PartName];
            const isScoreable = scoreable.has(part);
            const isDerived = part in DERIVED_SOURCE;
            return (
              <label key={part} className="check">
                <input
                  type="checkbox"
                  checked={parts.has(part)}
                  onChange={() => togglePart(part)}
                />
                <span className="swatch" style={{ background: meta.color }} />
                {meta.label}
                {isDerived && <span className="badge">notation only</span>}
                {!isScoreable && <span className="badge">audio only</span>}
              </label>
            );
          })}
        </fieldset>

        <fieldset>
          <legend>Formats</legend>
          {FORMAT_OPTIONS.map((f) => (
            <label key={f.id} className="check">
              <input
                type="checkbox"
                checked={formats.has(f.id)}
                onChange={() => toggleFormat(f.id)}
              />
              {f.label}
              {f.note && <span className="badge">{f.note}</span>}
            </label>
          ))}
        </fieldset>
      </div>

      <div className="tempo-row">
        <span className="tempo-label">Tempo grid</span>
        <Segmented
          options={["fixed", "variable"] as TempoMode[]}
          value={tempoMode}
          onChange={setTempoMode}
          label={(m) => (m === "fixed" ? "Fixed" : "Variable")}
        />
        <span className="tempo-value muted">
          {tempoMode === "fixed" ? "steady metronomic grid" : "follow live tempo"}
        </span>
      </div>
      <p className="hint tempo-hint">
        Fixed snaps every bar to a steady tempo — cleaner for studio takes and
        immune to beat-tracking wobble. Use Variable for live/rubato recordings
        where the tempo genuinely drifts.
      </p>

      <div className="tempo-row">
        <span className="tempo-label">Tempo</span>
        <Segmented
          options={[0.5, 1, 2]}
          value={tempoMult}
          onChange={setTempoMult}
          label={(m) => (m === 1 ? "1×" : m === 0.5 ? "½×" : "2×")}
        />
        <span className="tempo-value">
          {effectiveTempo} BPM
          {tempoMult !== 1 && <span className="muted"> (detected {detectedTempo})</span>}
        </span>
      </div>
      <p className="hint tempo-hint">
        Tempo octave (e.g. 80 vs 160) is ambiguous — if the note values look
        doubled or halved, switch ½×/2×.
      </p>

      <div className="tempo-row">
        <span className="tempo-label">Beat nudge</span>
        <Segmented
          options={[-3, -2, -1, -0.5, -0.25, 0, 0.25, 0.5, 1, 2, 3]}
          value={beatOffset}
          onChange={setBeatOffset}
          label={(off) => (off === 0 ? "0" : off > 0 ? `+${off}` : String(off))}
        />
        <span className="tempo-value muted">beats</span>
      </div>
      <p className="hint tempo-hint">
        If everything sits a beat off (e.g. notes land on the off-beat / measure
        heads are rests), nudge the grid by ±¼ or ±½ beat to line it up.
      </p>

      <div className="tempo-row">
        <span className="tempo-label">Key</span>
        <select
          className="key-select"
          value={keySharps === null ? "auto" : String(keySharps)}
          onChange={(e) => {
            const v = e.currentTarget.value;
            setKeySharps(v === "auto" ? null : Number(v));
          }}
        >
          <option value="auto">Auto (detect)</option>
          {KEY_OPTIONS.map((k) => (
            <option key={k.sharps} value={k.sharps}>
              {k.label}
            </option>
          ))}
        </select>
        <span className="tempo-value muted">
          {keySharps === null ? "estimated from the notes" : "fixed"}
        </span>
      </div>
      <p className="hint tempo-hint">
        Auto key detection often misses — if the key signature is wrong, pick the
        real key here.
      </p>

      <div className="tempo-row">
        <span className="tempo-label">Octave</span>
        <Segmented
          options={[-2, -1, 0, 1, 2]}
          value={octaveShift}
          onChange={setOctaveShift}
          label={(oct) => (oct === 0 ? "0" : oct > 0 ? `+${oct}` : String(oct))}
        />
        <span className="tempo-value muted">octaves</span>
      </div>
      <p className="hint tempo-hint">
        Shifts the written notes up/down by octaves to read them in a comfortable
        register (e.g. a low vocal that sits under the staff). Applies to the
        parts you export — set 0 when exporting several parts together.
      </p>

      {previewPart && (
        <div className="preview-block">
          <div className="preview-head">
            {previewableParts.length > 1 ? (
              <div className="preview-tabs">
                {previewableParts.map((p) => (
                  <button
                    key={p}
                    className={`seg ${p === previewPart ? "on" : ""}`}
                    onClick={() => setPreviewSel(p)}
                  >
                    {PART_META[p].label}
                  </button>
                ))}
              </div>
            ) : (
              <span>Preview · {PART_META[previewPart].label}</span>
            )}
            {previewStemPart && (
              <StemPlayer
                key={previewStemPart}
                part={previewStemPart}
                stemPath={result.previews?.[previewStemPart] ?? result.stems[previewStemPart]}
              />
            )}
          </div>
          <ScorePreview musicxml={previewXml} loading={previewLoading} />
        </div>
      )}

      <div className="export-actions">
        <button className="btn ghost" onClick={onBack}>
          ← Back
        </button>
        <button className="btn primary" onClick={run} disabled={!canRun}>
          {running ? "Exporting…" : "Choose folder & Export…"}
        </button>
      </div>

      {running && progress && (
        <div className="progress-track slim">
          <div className="progress-fill" style={{ width: `${progress.pct}%` }} />
        </div>
      )}

      {error && <p className="error">Export failed: {error}</p>}

      {artifacts && (
        <div className="results">
          <h3>{written.length} file(s) saved</h3>
          {destDir && (
            <div className="dest-row">
              <span className="mono dest">{destDir}</span>
              {written.length > 0 && (
                <button
                  className="link"
                  onClick={() => written[0].path && reveal(written[0].path)}
                >
                  Open folder
                </button>
              )}
            </div>
          )}
          <ul className="artifact-list">
            {written.map((a) => (
              <li key={`${a.part}-${a.format}`}>
                <span className="mono">
                  {a.part}.{a.format === "midi" ? "mid" : a.format}
                </span>
                <button className="link" onClick={() => a.path && reveal(a.path)}>
                  Reveal
                </button>
              </li>
            ))}
          </ul>
          {skipped.length > 0 && (
            <details className="skipped">
              <summary>{skipped.length} skipped</summary>
              <ul>
                {skipped.map((a) => (
                  <li key={`${a.part}-${a.format}`}>
                    {a.part} · {a.format} — {a.skipped}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <button className="btn ghost" onClick={onHome}>
            Done
          </button>
        </div>
      )}
    </section>
  );
}

function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

// Single-stem player for the Export preview: lets you hear the source audio of
// the previewed part while reading its notation, to check the transcription.
// Reuses the (confirmed-working) StemMixer with a one-entry stem map.
function StemPlayer({ part, stemPath }: { part: string; stemPath: string }) {
  const mixer = useRef<StemMixer | null>(null);
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [pos, setPos] = useState(0);
  const [dur, setDur] = useState(0);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const m = new StemMixer();
    mixer.current = m;
    m.load({ [part]: stemPath })
      .then((info) => {
        setDur(m.getDuration());
        if (info.failed.length > 0) setErr(info.failed[0].error);
        setReady(m.loadedCount() > 0);
      })
      .catch((e) => setErr(String(e)));
    return () => m.dispose();
  }, [part, stemPath]);

  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => {
      const m = mixer.current;
      if (!m) return;
      setPos(m.getPosition());
      if (!m.isPlaying()) setPlaying(false);
    }, 150);
    return () => window.clearInterval(id);
  }, [playing]);

  const toggle = async () => {
    const m = mixer.current;
    if (!m) return;
    try {
      if (m.isPlaying()) {
        m.pause();
        setPlaying(false);
      } else {
        await m.play();
        setPlaying(true);
      }
      setPos(m.getPosition());
    } catch (e) {
      setErr(String(e));
    }
  };

  if (err) return <span className="hint preview-audio-err">audio: {err}</span>;

  return (
    <span className="preview-audio">
      <button className="play-btn small" onClick={toggle} disabled={!ready}>
        {playing ? "❚❚" : "▶"}
      </button>
      <input
        className="seek"
        type="range"
        min={0}
        max={dur || 1}
        step={0.05}
        value={pos}
        disabled={!ready}
        onChange={(e) => {
          const m = mixer.current;
          if (!m) return;
          const v = Number(e.currentTarget.value);
          m.seek(v);
          setPos(v);
          setPlaying(m.isPlaying());
        }}
      />
      <span className="time">
        {fmtTime(pos)} / {fmtTime(dur)}
      </span>
    </span>
  );
}
