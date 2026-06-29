import { useCallback, useEffect, useRef, useState } from "react";

import {
  downloadRuntime,
  onRuntimeProgress,
  type RuntimeProgress,
} from "../lib/ipc";
import { useI18n } from "../lib/i18n";

interface RuntimeSetupProps {
  /** Called once the runtime is installed and the pipeline is ready. */
  onReady: () => void;
}

const PHASE_KEY: Record<RuntimeProgress["phase"], string> = {
  download: "runtime.download",
  verify: "runtime.verify",
  extract: "runtime.extract",
  done: "runtime.done",
};

function mb(bytes: number): string {
  return (bytes / 1_000_000).toFixed(0);
}

/** First-run gate: the ~350 MB Python runtime isn't bundled, so fetch it once
 * (from GitHub Releases) before the app can analyse anything. */
export function RuntimeSetup({ onReady }: RuntimeSetupProps) {
  const { t } = useI18n();
  const [progress, setProgress] = useState<RuntimeProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  // Guard against double-invocation (React 18 StrictMode mounts twice in dev).
  const running = useRef(false);

  const retry = useCallback(() => {
    setError(null);
    setProgress(null);
    setAttempt((n) => n + 1);
  }, []);

  useEffect(() => {
    if (running.current) return;
    running.current = true;

    let unlisten: (() => void) | undefined;
    let cancelled = false;

    onRuntimeProgress((p) => {
      if (!cancelled) setProgress(p);
    }).then((fn) => (unlisten = fn));

    downloadRuntime()
      .then(() => {
        if (!cancelled) onReady();
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        running.current = false;
      });

    return () => {
      cancelled = true;
      unlisten?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  const pct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.downloaded / progress.total) * 100))
      : null;

  return (
    <section className="runtime-setup">
      <div className="dropzone-icon">♪</div>
      <h2>{t("runtime.title")}</h2>
      <p>{t("runtime.desc")}</p>

      {error ? (
        <>
          <p className="error">{t("runtime.failed", { error })}</p>
          <button className="btn primary" onClick={retry}>
            {t("runtime.retry")}
          </button>
        </>
      ) : (
        <div className="runtime-progress">
          <div className="progress-track">
            <div
              className="progress-fill"
              style={{ width: pct != null ? `${pct}%` : "100%" }}
              data-indeterminate={pct == null}
            />
          </div>
          <p className="runtime-status-line">
            {progress ? t(PHASE_KEY[progress.phase]) : t("runtime.preparing")}
            {progress && progress.phase === "download" && progress.total > 0
              ? ` … ${mb(progress.downloaded)} / ${mb(progress.total)} MB (${pct}%)`
              : progress && progress.phase === "download"
                ? ` … ${mb(progress.downloaded)} MB`
                : ""}
          </p>
        </div>
      )}
    </section>
  );
}
