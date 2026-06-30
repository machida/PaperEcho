import { useEffect, useRef, useState } from "react";

import { StemMixer, type LoadResult, type TrackState } from "../lib/audio";
import { formatTime } from "../lib/format";
import { anySoloed, effectiveGain } from "../lib/mixer-gain";
import { analyze, mixdown, onProgress, pickSaveMp3, reveal } from "../lib/ipc";
import { useI18n, type TFunc } from "../lib/i18n";
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

const STAGES = new Set(["decode", "separate", "rhythm", "transcribe", "preview"]);

export function Analyze({ filePath, transcribeParts, result, onDone, onExport, onBack }: AnalyzeProps) {
  const { t } = useI18n();
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
        <p className="error">{t("analyze.failed", { error })}</p>
        <button className="btn" onClick={onBack}>
          {t("common.back")}
        </button>
      </section>
    );
  }

  if (!result) {
    const label = progress
      ? STAGES.has(progress.stage)
        ? t(`stage.${progress.stage}`)
        : progress.stage
      : t("analyze.starting");
    return (
      <section className="analyze analyzing">
        <h2>{t("analyze.analyzing")}</h2>
        <p className="filename">{filePath.split("/").pop()}</p>
        <div className="progress-track">
          <div className="progress-fill" style={{ width: `${progress?.pct ?? 4}%` }} />
        </div>
        <p className="stage-label">
          {label}
          {progress?.msg ? ` — ${progress.msg}` : ""}
        </p>
        <p className="hint">{t("analyze.firstRunHint")}</p>
      </section>
    );
  }

  return (
    <section className="analyze">
      <div className="analyze-head">
        <div>
          <h2>{t("analyze.detectedParts")}</h2>
          <p className="meta-line">
            {t("analyze.meta", {
              n: result.parts.length,
              bpm: Math.round(result.rhythm.tempo),
              ts: result.rhythm.time_signature,
            })}
          </p>
        </div>
        <button className="btn primary" onClick={onExport}>
          {t("analyze.export")}
        </button>
      </div>
      <PartsMixer result={result} t={t} />
      <button className="btn ghost start-over" onClick={onBack}>
        {t("analyze.startOver")}
      </button>
    </section>
  );
}

function PartsMixer({ result, t }: { result: AnalysisResult; t: TFunc }) {
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
    const anySolo = anySoloed(Object.values(tracks));
    const gains: Record<string, number> = {};
    for (const part of result.parts) {
      const s = tracks[part] ?? { volume: 0.9, muted: false, soloed: false };
      gains[part] = effectiveGain(s, anySolo);
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
          t("mixer.playing", {
            n: m.loadedCount(),
            state: m.contextState(),
            rate: m.contextRate(),
          }),
        );
      }
      setPos(m.getPosition());
    } catch (e) {
      setAudioDiag(t("mixer.audioError", { error: String(e) }));
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
              <span className="part-name">{t(`part.${part}`)}</span>
              {!result.pitched_parts.includes(part as PartName) && (
                <span className="badge">{t("mixer.audioOnly")}</span>
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
                  {t("mixer.solo")}
                </button>
                <button
                  className={`tag ${st.muted ? "on" : ""}`}
                  onClick={() => {
                    mixer.current?.toggleMute(part);
                    if (mixer.current) snapshot(mixer.current);
                  }}
                  disabled={!ready}
                >
                  {t("mixer.mute")}
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
      {!ready && !loadInfo && <p className="hint">{t("mixer.loadingStems")}</p>}
      {loadInfo && (
        <p
          className={loadInfo.failed.length > 0 ? "error" : "hint"}
          style={{ textAlign: "left", whiteSpace: "pre-wrap" }}
        >
          {t("mixer.audioLoaded", {
            loaded: loadInfo.loaded.length,
            total: loadInfo.loaded.length + loadInfo.failed.length,
          })}
          {loadInfo.failed.length > 0 &&
            t("mixer.audioFailed", {
              list: loadInfo.failed.map((f) => `${f.name} (${f.error})`).join(", "),
            })}
          {audioDiag ? ` · ${audioDiag}` : t("mixer.pressPlay")}
        </p>
      )}
      <div className="mixer-foot">
        <button className="btn" onClick={exportMix} disabled={!ready || mixing}>
          {mixing ? t("mixer.exporting") : t("mixer.exportMix")}
        </button>
        {mixPath && (
          <button className="link" onClick={() => reveal(mixPath)}>
            {t("common.reveal")}
          </button>
        )}
        {mixError && <span className="error">{t("mixer.mixFailed", { error: mixError })}</span>}
      </div>
    </div>
  );
}
