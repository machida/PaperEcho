import { getCurrentWebview } from "@tauri-apps/api/webview";
import { useEffect, useState } from "react";

import { isSupportedAudio, pickAudioFile } from "../lib/ipc";
import { PART_META } from "../lib/types";

interface HomeProps {
  onPick: (path: string, transcribeParts: string[]) => void;
}

// Pitched parts that can be notated. Drums/other stay audio-only.
const TRANSCRIBABLE = ["bass", "vocals", "guitar", "piano"] as const;

export function Home({ onPick }: HomeProps) {
  const [hover, setHover] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Which parts to notate. All on by default; unchecking (e.g. the slow piano)
  // skips that part's transcription — every part is still separated & playable.
  const [parts, setParts] = useState<Set<string>>(new Set(TRANSCRIBABLE));

  const toggle = (p: string) =>
    setParts((prev) => {
      const next = new Set(prev);
      next.has(p) ? next.delete(p) : next.add(p);
      return next;
    });

  const accept = (path: string) => {
    if (!isSupportedAudio(path)) {
      setError("Unsupported file. Use mp3, wav, m4a, or aiff.");
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
        role="button"
        tabIndex={0}
      >
        <div className="dropzone-icon">♪</div>
        <h2>Drop an audio file</h2>
        <p>or click to browse</p>
        <span className="formats">mp3 · wav · m4a · aiff</span>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="transcribe-pick">
        <span className="transcribe-label">Notate:</span>
        {TRANSCRIBABLE.map((p) => (
          <label key={p} className={`chip ${parts.has(p) ? "on" : ""}`}>
            <input
              type="checkbox"
              checked={parts.has(p)}
              onChange={() => toggle(p)}
            />
            {PART_META[p].label}
          </label>
        ))}
      </div>
      <p className="hint">
        Paper Echo separates the parts and drafts editable notation — finish it in
        MuseScore, Dorico, or Sibelius. Uncheck parts you don't need to notate
        (e.g. piano) to analyse faster — every part is still separated and playable.
      </p>
    </section>
  );
}
