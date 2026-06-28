import { useCallback, useEffect, useState } from "react";

import { Analyze } from "./routes/Analyze";
import { Export } from "./routes/Export";
import { Home } from "./routes/Home";
import { RuntimeSetup } from "./routes/RuntimeSetup";
import { runtimeStatus } from "./lib/ipc";
import type { AnalysisResult } from "./lib/types";
import "./App.css";

type Screen = "home" | "analyze" | "export";
// Gate the whole app on the Python runtime being present (it's downloaded on
// first launch rather than bundled — see routes/RuntimeSetup + src-tauri/runtime.rs).
type RuntimeState = "checking" | "needs-download" | "ready";

function App() {
  const [runtime, setRuntime] = useState<RuntimeState>("checking");
  const [screen, setScreen] = useState<Screen>("home");
  const [filePath, setFilePath] = useState<string | null>(null);
  const [transcribeParts, setTranscribeParts] = useState<string[]>([]);
  const [result, setResult] = useState<AnalysisResult | null>(null);

  useEffect(() => {
    runtimeStatus()
      .then((s) => setRuntime(s.ready ? "ready" : "needs-download"))
      // If the status check itself fails, don't hard-block — assume ready and
      // let the pipeline surface a clear error if it's genuinely missing.
      .catch(() => setRuntime("ready"));
  }, []);

  const handlePick = useCallback((path: string, parts: string[]) => {
    setFilePath(path);
    setTranscribeParts(parts);
    setResult(null);
    setScreen("analyze");
  }, []);

  const reset = useCallback(() => {
    setFilePath(null);
    setResult(null);
    setScreen("home");
  }, []);

  return (
    <div className="app">
      <header className="app-bar">
        <button className="brand" onClick={reset} title="Home">
          Paper&nbsp;Echo
        </button>
        <span className="tagline">Turn audio into editable sheet music</span>
      </header>

      <main className="app-body">
        {runtime === "checking" && (
          <section className="runtime-setup">
            <div className="dropzone-icon">♪</div>
            <p>起動中…</p>
          </section>
        )}

        {runtime === "needs-download" && (
          <RuntimeSetup onReady={() => setRuntime("ready")} />
        )}

        {runtime === "ready" && screen === "home" && (
          <Home onPick={handlePick} />
        )}

        {screen === "analyze" && filePath && (
          <Analyze
            filePath={filePath}
            transcribeParts={transcribeParts}
            result={result}
            onDone={setResult}
            onExport={() => setScreen("export")}
            onBack={reset}
          />
        )}

        {screen === "export" && result && (
          <Export
            result={result}
            onBack={() => setScreen("analyze")}
            onHome={reset}
          />
        )}
      </main>
    </div>
  );
}

export default App;
