import { useEffect, useRef } from "react";
import { OpenSheetMusicDisplay } from "opensheetmusicdisplay";

interface ScorePreviewProps {
  musicxml: string | null;
  loading?: boolean;
}

/** Renders a MusicXML string as engraved notation via OpenSheetMusicDisplay. */
export function ScorePreview({ musicxml, loading }: ScorePreviewProps) {
  const container = useRef<HTMLDivElement>(null);
  const osmd = useRef<OpenSheetMusicDisplay | null>(null);

  useEffect(() => {
    if (!container.current) return;
    if (!osmd.current) {
      osmd.current = new OpenSheetMusicDisplay(container.current, {
        autoResize: true,
        drawTitle: false,
        drawComposer: false,
        drawPartNames: false,
        drawingParameters: "compact",
      });
    }
    if (!musicxml) return;
    let cancelled = false;
    osmd.current
      .load(musicxml)
      .then(() => {
        if (!cancelled) osmd.current?.render();
      })
      .catch(() => {
        /* malformed/empty score — leave the pane blank */
      });
    return () => {
      cancelled = true;
    };
  }, [musicxml]);

  return (
    <div className="score-preview-wrap">
      {loading && <div className="score-preview-loading">Rendering…</div>}
      <div className="score-preview" ref={container} />
    </div>
  );
}
