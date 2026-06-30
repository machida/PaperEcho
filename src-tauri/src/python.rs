//! Drives a single long-lived Python process (`paperecho.pipeline serve`).
//!
//! Contract (see python/paperecho/pipeline.py): we send one JSON request per
//! line on stdin; the process replies with one JSON object per line on stdout —
//! `{"event":"progress"|"done"|"error", ...}` — ending each request with a
//! `done` or `error`. Keeping the process warm avoids re-importing music21
//! (~7s) on every preview/export. Requests are serialized by a mutex.
//! Human/library logs go to stderr (inherited so they show in the dev console).

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use serde_json::Value;
use tauri::{Emitter, Window};

/// Max time to wait for *any* line from the pipeline before treating it as hung
/// and killing it (so the next request respawns a fresh process). Resets on each
/// line received, so it's an inter-message heartbeat, not a total deadline — it
/// must exceed the longest silent stretch, which is the separation call (one
/// coarse milestone, then minutes of work). Generous default; override with
/// `PAPER_ECHO_PIPELINE_TIMEOUT_SECS` (0/invalid → default).
const DEFAULT_TIMEOUT_SECS: u64 = 600;

fn pipeline_timeout() -> Duration {
    let secs = std::env::var("PAPER_ECHO_PIPELINE_TIMEOUT_SECS")
        .ok()
        .and_then(|s| s.parse::<u64>().ok())
        .filter(|&s| s > 0)
        .unwrap_or(DEFAULT_TIMEOUT_SECS);
    Duration::from_secs(secs)
}

/// Resolved at app setup to the bundled `python/` resources in a packaged build.
static PYTHON_DIR: OnceLock<PathBuf> = OnceLock::new();

/// Record where the bundled `python/` project lives (called once from the Tauri
/// `setup` hook with the resolved resource dir). No-op in dev, where the
/// relative fallback in `python_dir()` is used instead.
pub fn set_python_dir(dir: PathBuf) {
    let _ = PYTHON_DIR.set(dir);
}

/// Locate the `python/` project dir, in priority order:
///   1. `PAPER_ECHO_PYTHON_DIR` env override (CLI / power users),
///   2. the bundled resource dir recorded at setup (packaged build),
///   3. relative to this crate (dev layout: `<crate>/../python`).
fn python_dir() -> PathBuf {
    if let Ok(p) = std::env::var("PAPER_ECHO_PYTHON_DIR") {
        return PathBuf::from(p);
    }
    if let Some(p) = PYTHON_DIR.get() {
        return p.clone();
    }
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(|p| p.join("python"))
        .unwrap_or_else(|| PathBuf::from("python"))
}

/// The Python interpreter to run the pipeline with.
///   - Packaged build: a self-contained standalone CPython copied into the bundle
///     at `<dir>/runtime` (no external symlinks → works on any machine).
///   - Dev: the uv-managed project venv at `<dir>/.venv`.
fn venv_python(dir: &Path) -> PathBuf {
    let runtime = if cfg!(windows) {
        dir.join("runtime").join("python.exe")
    } else {
        dir.join("runtime").join("bin").join("python3.11")
    };
    if runtime.exists() {
        return runtime;
    }
    if cfg!(windows) {
        dir.join(".venv").join("Scripts").join("python.exe")
    } else {
        dir.join(".venv").join("bin").join("python")
    }
}

/// Env vars that point the ML libraries at the bundled offline model cache
/// (`<dir>/model-cache`, packed into the runtime tarball) so a first analyse
/// never has to download model weights:
///   - `TORCH_HOME` → `<cache>/torch` — Demucs (`htdemucs_6s`) and beat_this both
///     fetch via `torch.hub.load_state_dict_from_url`, which reads `$TORCH_HOME/hub`.
///   - `PAPER_ECHO_MODEL_CACHE` → `<cache>` — read by `transcribe_piano` to pass an
///     explicit `checkpoint_path` (the ByteDance lib otherwise hardcodes `~`).
/// Returns nothing when the cache is absent (dev / pre-bundle jobs), so the
/// libraries fall back to their normal `~/.cache` download behaviour.
fn model_cache_envs(dir: &Path) -> Vec<(String, String)> {
    let cache = dir.join("model-cache");
    if !cache.is_dir() {
        return Vec::new();
    }
    vec![
        (
            "TORCH_HOME".to_string(),
            cache.join("torch").to_string_lossy().into_owned(),
        ),
        (
            "PAPER_ECHO_MODEL_CACHE".to_string(),
            cache.to_string_lossy().into_owned(),
        ),
    ]
}

/// Whether a usable interpreter is resolvable right now — true if the env
/// override, the dir recorded at setup (bundled or a first-run download), or the
/// dev `.venv` points at an existing python. The first-run download screen polls
/// this to decide whether the Python runtime still has to be fetched.
pub fn resolved_python_exists() -> bool {
    venv_python(&python_dir()).exists()
}

/// A classified line from the pipeline's stdout stream.
enum PipelineLine {
    Progress(Value),
    Done(Value),
    Error(String),
    Ignore,
}

/// Parse one stdout line. Non-JSON lines (chatty libraries that bypass our
/// logger) are ignored rather than treated as failures.
fn classify_line(line: &str) -> PipelineLine {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return PipelineLine::Ignore;
    }
    let value: Value = match serde_json::from_str(trimmed) {
        Ok(v) => v,
        Err(_) => return PipelineLine::Ignore,
    };
    match value.get("event").and_then(Value::as_str) {
        Some("progress") => PipelineLine::Progress(value),
        Some("done") => PipelineLine::Done(value),
        Some("error") => PipelineLine::Error(
            value
                .get("message")
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| "pipeline reported an error".into()),
        ),
        _ => PipelineLine::Ignore,
    }
}

/// The persistent Python process: its stdin (we write requests) and a channel
/// fed by a dedicated reader thread (so the request loop can wait with a timeout
/// rather than block forever on a hung process — std pipes have no read timeout).
struct Server {
    child: Child,
    stdin: ChildStdin,
    rx: Receiver<PipelineLine>,
}

impl Server {
    /// Kill the child so its stdout closes and the reader thread exits.
    fn kill(&mut self) {
        let _ = self.child.kill();
    }
}

fn server_slot() -> &'static Mutex<Option<Server>> {
    static SLOT: OnceLock<Mutex<Option<Server>>> = OnceLock::new();
    SLOT.get_or_init(|| Mutex::new(None))
}

fn spawn_server() -> Result<Server, String> {
    let dir = python_dir();
    let py = venv_python(&dir);
    if !py.exists() {
        return Err(format!(
            "Python venv not found at {}. Run `uv sync` in the python/ directory.",
            py.display()
        ));
    }

    let mut child = Command::new(&py)
        .current_dir(&dir)
        .envs(model_cache_envs(&dir))
        .arg("-u") // unbuffered so we read each reply line promptly
        .arg("-m")
        .arg("paperecho.pipeline")
        .arg("serve")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("failed to start python ({}): {e}", py.display()))?;

    let stdin = child.stdin.take().ok_or("no stdin for python process")?;
    let stdout = child.stdout.take().ok_or("no stdout for python process")?;

    // Reader thread: classify each stdout line and forward it. Lets `request`
    // wait with a timeout (channel recv) instead of an un-cancellable read_line.
    // The sender drops (closing the channel) on EOF/read error — which `request`
    // sees as Disconnected.
    let (tx, rx) = mpsc::channel::<PipelineLine>();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut buf = String::new();
        loop {
            buf.clear();
            match reader.read_line(&mut buf) {
                Ok(0) | Err(_) => break, // EOF or read error -> close the channel
                Ok(_) => {
                    // Forward every classified line (incl. Ignore) so any output
                    // counts as a liveness heartbeat for the timeout below.
                    if tx.send(classify_line(&buf)).is_err() {
                        break; // request side gone
                    }
                }
            }
        }
    });

    Ok(Server { child, stdin, rx })
}

/// Send one request to the persistent Python process, emitting each progress
/// object on `progress_event`, and return the `done` payload (or an error).
/// Calls are serialized by the mutex. A dead OR hung process is killed and
/// respawned on the next call: each line resets a `pipeline_timeout()` heartbeat,
/// and exceeding it (or the process ending) drops the server and returns an error.
pub fn request(window: &Window, req: Value, progress_event: &str) -> Result<Value, String> {
    let timeout = pipeline_timeout();
    let mut guard = server_slot().lock().map_err(|e| e.to_string())?;
    if guard.is_none() {
        *guard = Some(spawn_server()?);
    }

    let line = serde_json::to_string(&req).map_err(|e| e.to_string())?;
    {
        let server = guard.as_mut().unwrap();
        if writeln!(server.stdin, "{line}")
            .and_then(|_| server.stdin.flush())
            .is_err()
        {
            *guard = None; // process likely dead; the next call respawns it
            return Err("lost connection to the Python process".into());
        }
    }

    loop {
        let recv = guard.as_ref().unwrap().rx.recv_timeout(timeout);
        match recv {
            Ok(PipelineLine::Progress(value)) => {
                let _ = window.emit(progress_event, &value);
            }
            Ok(PipelineLine::Done(value)) => return Ok(value),
            Ok(PipelineLine::Error(message)) => return Err(message),
            Ok(PipelineLine::Ignore) => {} // chatter — just resets the heartbeat
            Err(RecvTimeoutError::Timeout) => {
                // No output for `timeout`: assume hung. Kill it so the mutex is
                // freed and the next request gets a fresh process.
                if let Some(mut s) = guard.take() {
                    s.kill();
                }
                return Err(format!(
                    "the Python process timed out after {}s (no output)",
                    timeout.as_secs()
                ));
            }
            Err(RecvTimeoutError::Disconnected) => {
                if let Some(mut s) = guard.take() {
                    s.kill();
                }
                return Err("the Python process ended unexpectedly".into());
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{classify_line, PipelineLine};

    #[test]
    fn parses_progress() {
        match classify_line(r#"{"event":"progress","stage":"separate","pct":42}"#) {
            PipelineLine::Progress(v) => assert_eq!(v["pct"], 42),
            _ => panic!("expected progress"),
        }
    }

    #[test]
    fn parses_done() {
        assert!(matches!(
            classify_line(r#"{"event":"done","job_dir":"/tmp/x"}"#),
            PipelineLine::Done(_)
        ));
    }

    #[test]
    fn parses_error_message() {
        match classify_line(r#"{"event":"error","message":"boom"}"#) {
            PipelineLine::Error(m) => assert_eq!(m, "boom"),
            _ => panic!("expected error"),
        }
    }

    #[test]
    fn error_without_message_has_fallback() {
        match classify_line(r#"{"event":"error"}"#) {
            PipelineLine::Error(m) => assert!(!m.is_empty()),
            _ => panic!("expected error"),
        }
    }

    #[test]
    fn ignores_non_json_and_blank() {
        assert!(matches!(
            classify_line("Predicting MIDI for foo.wav..."),
            PipelineLine::Ignore
        ));
        assert!(matches!(classify_line("   "), PipelineLine::Ignore));
        assert!(matches!(
            classify_line(r#"{"event":"chatter"}"#),
            PipelineLine::Ignore
        ));
    }
}
