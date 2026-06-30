import { useEffect, useMemo, useRef, useState } from "react";

import { StemMixer } from "../lib/audio";
import { formatTime } from "../lib/format";
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
import { useI18n } from "../lib/i18n";
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

const FORMAT_OPTIONS: { id: OutputFormat; label: string; noteKey?: string }[] = [
  { id: "musicxml", label: "MusicXML" },
  { id: "midi", label: "MIDI" },
  { id: "wav", label: "WAV" },
  { id: "mp3", label: "MP3" },
  { id: "pdf", label: "PDF", noteKey: "format.needsMuseScore" },
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
  const { t } = useI18n();
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
    // `controls` is rebuilt every render from these primitives; listing the
    // fields (not the object) keeps the effect from re-firing on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result.job_dir, previewPart, tempoMult, tempoMode, beatOffset, keySharps, octaveShift]);

  const togglePart = (p: string) =>
    setParts((prev) => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });

  const toggleFormat = (f: OutputFormat) =>
    setFormats((prev) => {
      const next = new Set(prev);
      if (next.has(f)) next.delete(f);
      else next.add(f);
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
      <h2>{t("export.title")}</h2>

      <div className="export-grid">
        <fieldset>
          <legend>{t("export.parts")}</legend>
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
                {t(`part.${part}`)}
                {isDerived && <span className="badge">{t("export.notationOnly")}</span>}
                {!isScoreable && <span className="badge">{t("export.audioOnly")}</span>}
              </label>
            );
          })}
        </fieldset>

        <fieldset>
          <legend>{t("export.formats")}</legend>
          {FORMAT_OPTIONS.map((f) => (
            <label key={f.id} className="check">
              <input
                type="checkbox"
                checked={formats.has(f.id)}
                onChange={() => toggleFormat(f.id)}
              />
              {f.label}
              {f.noteKey && <span className="badge">{t(f.noteKey)}</span>}
            </label>
          ))}
        </fieldset>
      </div>

      <div className="tempo-row">
        <span className="tempo-label">{t("export.tempoGrid")}</span>
        <Segmented
          options={["fixed", "variable"] as TempoMode[]}
          value={tempoMode}
          onChange={setTempoMode}
          label={(m) => (m === "fixed" ? t("seg.fixed") : t("seg.variable"))}
        />
        <span className="tempo-value muted">
          {tempoMode === "fixed" ? t("export.gridFixed") : t("export.gridVariable")}
        </span>
      </div>
      <p className="hint tempo-hint">{t("export.gridHint")}</p>

      <div className="tempo-row">
        <span className="tempo-label">{t("export.tempo")}</span>
        <Segmented
          options={[0.5, 1, 2]}
          value={tempoMult}
          onChange={setTempoMult}
          label={(m) => (m === 1 ? "1×" : m === 0.5 ? "½×" : "2×")}
        />
        <span className="tempo-value">
          {effectiveTempo} BPM
          {tempoMult !== 1 && (
            <span className="muted"> {t("export.detected", { n: detectedTempo })}</span>
          )}
        </span>
      </div>
      <p className="hint tempo-hint">{t("export.tempoHint")}</p>

      <div className="tempo-row">
        <span className="tempo-label">{t("export.beatNudge")}</span>
        <Segmented
          options={[-3, -2, -1, -0.5, -0.25, 0, 0.25, 0.5, 1, 2, 3]}
          value={beatOffset}
          onChange={setBeatOffset}
          label={(off) => (off === 0 ? "0" : off > 0 ? `+${off}` : String(off))}
        />
        <span className="tempo-value muted">{t("export.beats")}</span>
      </div>
      <p className="hint tempo-hint">{t("export.beatHint")}</p>

      <div className="tempo-row">
        <span className="tempo-label">{t("export.key")}</span>
        <select
          className="key-select"
          value={keySharps === null ? "auto" : String(keySharps)}
          onChange={(e) => {
            const v = e.currentTarget.value;
            setKeySharps(v === "auto" ? null : Number(v));
          }}
        >
          <option value="auto">{t("export.keyAuto")}</option>
          {KEY_OPTIONS.map((k) => (
            <option key={k.sharps} value={k.sharps}>
              {k.label}
            </option>
          ))}
        </select>
        <span className="tempo-value muted">
          {keySharps === null ? t("export.keyEstimated") : t("export.keyFixed")}
        </span>
      </div>
      <p className="hint tempo-hint">{t("export.keyHint")}</p>

      <div className="tempo-row">
        <span className="tempo-label">{t("export.octave")}</span>
        <Segmented
          options={[-2, -1, 0, 1, 2]}
          value={octaveShift}
          onChange={setOctaveShift}
          label={(oct) => (oct === 0 ? "0" : oct > 0 ? `+${oct}` : String(oct))}
        />
        <span className="tempo-value muted">{t("export.octaves")}</span>
      </div>
      <p className="hint tempo-hint">{t("export.octaveHint")}</p>

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
                    {t(`part.${p}`)}
                  </button>
                ))}
              </div>
            ) : (
              <span>{t("export.previewSingle", { label: t(`part.${previewPart}`) })}</span>
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
          {t("common.back")}
        </button>
        <button className="btn primary" onClick={run} disabled={!canRun}>
          {running ? t("export.exporting") : t("export.chooseFolder")}
        </button>
      </div>

      {running && progress && (
        <div className="progress-track slim">
          <div className="progress-fill" style={{ width: `${progress.pct}%` }} />
        </div>
      )}

      {error && <p className="error">{t("export.failed", { error })}</p>}

      {artifacts && (
        <div className="results">
          <h3>{t("export.savedCount", { n: written.length })}</h3>
          {destDir && (
            <div className="dest-row">
              <span className="mono dest">{destDir}</span>
              {written.length > 0 && (
                <button
                  className="link"
                  onClick={() => written[0].path && reveal(written[0].path)}
                >
                  {t("export.openFolder")}
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
                  {t("common.reveal")}
                </button>
              </li>
            ))}
          </ul>
          {skipped.length > 0 && (
            <details className="skipped">
              <summary>{t("export.skippedCount", { n: skipped.length })}</summary>
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
            {t("common.done")}
          </button>
        </div>
      )}
    </section>
  );
}

// Single-stem player for the Export preview: lets you hear the source audio of
// the previewed part while reading its notation, to check the transcription.
// Reuses the (confirmed-working) StemMixer with a one-entry stem map.
function StemPlayer({ part, stemPath }: { part: string; stemPath: string }) {
  const { t } = useI18n();
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

  if (err)
    return (
      <span className="hint preview-audio-err">{t("mixer.audioError", { error: err })}</span>
    );

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
        {formatTime(pos)} / {formatTime(dur)}
      </span>
    </span>
  );
}
