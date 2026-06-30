import { getCurrentWebview } from "@tauri-apps/api/webview";
import { useEffect, useState } from "react";

import { isSupportedAudio, pickAudioFile } from "../lib/ipc";
import { useI18n } from "../lib/i18n";

interface HomeProps {
  onPick: (path: string, transcribeParts: string[]) => void;
}

// Pitched parts that can be notated. Drums/other stay audio-only.
const TRANSCRIBABLE = ["bass", "vocals", "guitar", "piano"] as const;

export function Home({ onPick }: HomeProps) {
  const { t } = useI18n();
  const [hover, setHover] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Which parts to notate. All on by default; unchecking (e.g. the slow piano)
  // skips that part's transcription — every part is still separated & playable.
  const [parts, setParts] = useState<Set<string>>(new Set(TRANSCRIBABLE));

  const toggle = (p: string) =>
    setParts((prev) => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });

  const accept = (path: string) => {
    if (!isSupportedAudio(path)) {
      setError(t("home.unsupported"));
      return;
    }
    setError(null);
    onPick(path, [...parts]);
  };

  // Native OS file-drop (the webview can't read dropped file paths directly).
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    getCurrentWebview()
      .onDragDropEvent((event) => {
        const p = event.payload;
        if (p.type === "over") setHover(true);
        else if (p.type === "leave") setHover(false);
        else if (p.type === "drop") {
          setHover(false);
          const first = p.paths[0];
          if (first) accept(first);
        }
      })
      .then((fn) => (unlisten = fn));
    return () => unlisten?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const browse = async () => {
    const path = await pickAudioFile();
    if (path) accept(path);
  };

  return (
    <section className="home">
      <div
        className={`dropzone ${hover ? "is-hover" : ""}`}
        onClick={browse}
        onKeyDown={(e) => {
          // Keyboard parity with the click handler (Enter/Space activate a button).
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            void browse();
          }
        }}
        role="button"
        tabIndex={0}
      >
        <div className="dropzone-icon">♪</div>
        <h2>{t("home.drop")}</h2>
        <p>{t("home.browse")}</p>
        <span className="formats">mp3 · wav · m4a · aiff</span>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="transcribe-pick">
        <span className="transcribe-label">{t("home.notate")}</span>
        {TRANSCRIBABLE.map((p) => (
          <label key={p} className={`chip ${parts.has(p) ? "on" : ""}`}>
            <input
              type="checkbox"
              checked={parts.has(p)}
              onChange={() => toggle(p)}
            />
            {t(`part.${p}`)}
          </label>
        ))}
      </div>
      <p className="hint">{t("home.hint")}</p>
    </section>
  );
}
