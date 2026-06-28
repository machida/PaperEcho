import { useEffect, useRef, useState } from "react";

import { StemMixer, type LoadResult, type TrackState } from "../lib/audio";
import { analyze, mixdown, onProgress, pickSaveMp3, reveal } from "../lib/ipc";
import {
  PART_META,
  PART_ORDER,
  type AnalysisResult,
  type PartName,
  type ProgressPayload,
} from "../lib/types";

interface AnalyzeProps {
  filePath: string;
  transcribeParts: string[];
  result: AnalysisResult | null;
  onDone: (r: AnalysisResult) => void;
  onExport: () => void;
  onBack: () => void;
}

const STAGE_LABELS: Record<string, string> = {
  decode: "Decoding audio",
  separate: "Separating parts",
  rhythm: "Estimating tempo",
  transcribe: "Transcribing notes",
  preview: "Encoding previews",
};

export function Analyze({ filePath, transcribeParts, result, onDone, onExport, onBack }: AnalyzeProps) {
  const [progress, setProgress] = useState<ProgressPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (result || started.current) return;
    started.current = true;
    let unlisten: (() => void) | undefined;
    onProgress(setProgress).then((fn) => (unlisten = fn));
    analyze(filePath, transcribeParts)
      .then(onDone)
      .catch((e) => setError(String(e)));
    return () => unlisten?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filePath]);

  if (error) {
    return (
      <section className="analyze">
        <p className="error">Analysis failed: {error}</p>
        <button className="btn" onClick={onBack}>
          ← Back
        </button>
      </section>
    );
  }

  if (!result) {
    const label = progress ? STAGE_LABELS[progress.stage] ?? progress.stage : "Starting…";
    return (
      <section className="analyze analyzing">
        <h2>Analyzing</h2>
        <p className="filename">{filePath.split("/").pop()}</p>
        <div className="progress-track">
          <div className="progress-fill" style={{ width: `${progress?.pct ?? 4}%` }} />
        </div>
        <p className="stage-label">
          {label}
          {progress?.msg ? ` — ${progress.msg}` : ""}
        </p>
        <p className="hint">
          First run downloads the AI models (separation, pitch, piano, beats);
          later runs use the cache and are faster.
        </p>
      </section>
    );
  }

  return (
    <section className="analyze">
      <div className="analyze-head">
        <div>
          <h2>Detected parts</h2>
          <p className="meta-line">
            {result.parts.length} parts · ~{Math.round(result.rhythm.tempo)} BPM ·{" "}
            {result.rhythm.time_signature}
          </p>
        </div>
        <button className="btn primary" onClick={onExport}>
          Export →
        </button>
      </div>
      <PartsMixer result={result} />
      <button className="btn ghost start-over" onClick={onBack}>
        ← Start over
      </button>
    </section>
  );
}

function formatTime(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

function PartsMixer({ result }: { result: AnalysisResult }) {
  const mixer = useRef<StemMixer | null>(null);
  const [ready, setReady] = useState(false);
  const [tracks, setTracks] = useState<Record<string, TrackState>>({});
  const [playing, setPlaying] = useState(false);
  const [pos, setPos] = useState(0);
  const [dur, setDur] = useState(0);
  const [mixing, setMixing] = useState(false);
  const [mixPath, setMixPath] = useState<string | null>(null);
  const [mixError, setMixError] = useState<string | null>(null);
  const [loadInfo, setLoadInfo] = useState<LoadResult | null>(null);
  const [audioDiag, setAudioDiag] = useState<string | null>(null);

  const snapshot = (m: StemMixer) => {
    const next: Record<string, TrackState> = {};
    for (const part of result.parts) next[part] = m.getState(part);
    setTracks(next);
  };

  // Effective per-stem gain = what the mixer is currently playing (solo wins).
  const effectiveGains = (): Record<string, number> => {
    const anySolo = Object.values(tracks).some((t) => t.soloed);
    const gains: Record<string, number> = {};
    for (const part of result.parts) {
      const s = tracks[part] ?? { volume: 0.9, muted: false, soloed: false };
      const audible = anySolo ? s.soloed : !s.muted;
      gains[part] = audible ? s.volume : 0;
    }
    return gains;
  };

  const exportMix = async () => {
    const baseName =
      (result.input.split("/").pop() || "mix").replace(/\.[^.]+$/, "") + "_mix.mp3";
    const dest = await pickSaveMp3(baseName);
    if (!dest) return;
    setMixing(true);
    setMixError(null);
    setMixPath(null);
    try {
      const res = await mixdown(result.job_dir, effectiveGains(), dest);
      setMixPath(res.path);
    } catch (e) {
      setMixError(String(e));
    } finally {
      setMixing(false);
    }
  };

  useEffect(() => {
    const m = new StemMixer();
    mixer.current = m;
    m.load(result.previews ?? result.stems)
      .then((info) => {
        setDur(m.getDuration());
        snapshot(m);
        setLoadInfo(info);
        setReady(m.loadedCount() > 0); // ready if at least one stem decoded
      })
      .catch((e) => {
        setLoadInfo({ loaded: [], failed: [{ name: "all", error: String(e) }] });
        setReady(false);
      });
    return () => m.dispose();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result.job_id]);

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

  const togglePlay = async () => {
    const m = mixer.current;
    if (!m) return;
    try {
      if (m.isPlaying()) {
        m.pause();
        setPlaying(false);
      } else {
        await m.play();
        setPlaying(true);
        setAudioDiag(
          `playing ${m.loadedCount()} stems, context ${m.contextState()} @ ${m.contextRate()}Hz`,
        );
      }
      setPos(m.getPosition());
    } catch (e) {
      setAudioDiag(`audio error: ${String(e)}`);
    }
  };

  const orderedParts = PART_ORDER.filter((p) => result.parts.includes(p));
  const anySolo = Object.values(tracks).some((t) => t.soloed);

  return (
    <div className="mixer">
      <div className="transport">
        <button className="play-btn" onClick={togglePlay} disabled={!ready}>
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
      </div>

      <ul className="parts">
        {orderedParts.map((part) => {
          const meta = PART_META[part as PartName];
          const st = tracks[part] ?? { volume: 0.9, muted: false, soloed: false };
          const dimmed = anySolo && !st.soloed;
          return (
            <li key={part} className={`part ${dimmed ? "dimmed" : ""}`}>
              <span className="swatch" style={{ background: meta.color }} />
              <span className="part-name">{meta.label}</span>
              {!result.pitched_parts.includes(part as PartName) && (
                <span className="badge">audio only</span>
              )}
              <div className="part-controls">
                <button
                  className={`tag ${st.soloed ? "on" : ""}`}
                  onClick={() => {
                    mixer.current?.toggleSolo(part);
                    if (mixer.current) snapshot(mixer.current);
                  }}
                  disabled={!ready}
                >
                  Solo
                </button>
                <button
                  className={`tag ${st.muted ? "on" : ""}`}
                  onClick={() => {
                    mixer.current?.toggleMute(part);
                    if (mixer.current) snapshot(mixer.current);
                  }}
                  disabled={!ready}
                >
                  Mute
                </button>
                <input
                  className="vol"
                  type="range"
                  min={0}
                  max={1}
                  step={0.01}
                  value={st.volume}
                  disabled={!ready}
                  onChange={(e) => {
                    const v = Number(e.currentTarget.value);
                    mixer.current?.setVolume(part, v);
                    if (mixer.current) snapshot(mixer.current);
                  }}
                />
              </div>
            </li>
          );
        })}
      </ul>
      {!ready && !loadInfo && <p className="hint">Loading stem audio…</p>}
      {loadInfo && (
        <p
          className={loadInfo.failed.length > 0 ? "error" : "hint"}
          style={{ textAlign: "left", whiteSpace: "pre-wrap" }}
        >
          {`audio: ${loadInfo.loaded.length}/${
            loadInfo.loaded.length + loadInfo.failed.length
          } stems loaded`}
          {loadInfo.failed.length > 0 &&
            ` — failed: ${loadInfo.failed
              .map((f) => `${f.name} (${f.error})`)
              .join(", ")}`}
          {audioDiag ? ` · ${audioDiag}` : " · press ▶ for playback state"}
        </p>
      )}
      <div className="mixer-foot">
        <button className="btn" onClick={exportMix} disabled={!ready || mixing}>
          {mixing ? "Exporting…" : "Export mix (mp3)"}
        </button>
        {mixPath && (
          <button className="link" onClick={() => reveal(mixPath)}>
            Reveal
          </button>
        )}
        {mixError && <span className="error">Mix failed: {mixError}</span>}
      </div>
    </div>
  );
}
