import { useCallback, useEffect, useRef, useState } from "react";

import {
  downloadRuntime,
  onRuntimeProgress,
  type RuntimeProgress,
} from "../lib/ipc";

interface RuntimeSetupProps {
  /** Called once the runtime is installed and the pipeline is ready. */
  onReady: () => void;
}

const PHASE_LABEL: Record<RuntimeProgress["phase"], string> = {
  download: "ダウンロード中",
  verify: "検証中",
  extract: "展開中",
  done: "完了",
};

function mb(bytes: number): string {
  return (bytes / 1_000_000).toFixed(0);
}

/** First-run gate: the ~350 MB Python runtime isn't bundled, so fetch it once
 * (from GitHub Releases) before the app can analyse anything. */
export function RuntimeSetup({ onReady }: RuntimeSetupProps) {
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
      <h2>初回セットアップ</h2>
      <p>
        音楽解析エンジン（約 350 MB）を一度だけダウンロードします。
        次回からはこの画面は出ません。
      </p>

      {error ? (
        <>
          <p className="error">セットアップに失敗しました: {error}</p>
          <button className="btn primary" onClick={retry}>
            再試行
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
            {progress ? PHASE_LABEL[progress.phase] : "準備中"}
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
