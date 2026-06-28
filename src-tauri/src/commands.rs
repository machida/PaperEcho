//! Tauri commands invoked from the React frontend.

use std::path::PathBuf;

use serde_json::{json, Value};
use tauri::{AppHandle, Manager, Window};

use crate::python;
use crate::runtime;

const PROGRESS_EVENT: &str = "pipeline://progress";

/// Whether the Python runtime is present (false on first launch of a packaged
/// build before the download). The UI gates the app on this.
#[tauri::command]
pub fn runtime_status(app: AppHandle) -> runtime::Status {
    runtime::status(&app)
}

/// Fetch + verify + extract the Python runtime from GitHub Releases, streaming
/// progress on `runtime://progress`. Only called when `runtime_status` is unready.
#[tauri::command]
pub async fn download_runtime(app: AppHandle, window: Window) -> Result<(), String> {
    runtime::download(app, window).await
}

/// Send a request to the persistent Python process off the main thread (sync
/// Tauri commands run on the main thread; the pipeline blocks, so it must move
/// onto the blocking pool).
async fn run_request(window: Window, req: Value) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || python::request(&window, req, PROGRESS_EVENT))
        .await
        .map_err(|e| format!("pipeline task failed: {e}"))?
}

/// `<app_data_dir>/jobs`, created on demand. One subdir per analysis job.
fn jobs_root(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("jobs");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(dir)
}

/// Decode + separate + rhythm + transcribe. Returns the analysis meta
/// (job_dir, parts, pitched_parts, stems, rhythm) plus the generated job_id.
#[tauri::command]
pub async fn analyze(
    app: AppHandle,
    window: Window,
    file_path: String,
    transcribe_parts: Vec<String>,
) -> Result<Value, String> {
    let id = uuid::Uuid::new_v4().to_string();
    let job_dir = jobs_root(&app)?.join(&id);
    std::fs::create_dir_all(&job_dir).map_err(|e| e.to_string())?;

    let req = json!({
        "cmd": "analyze",
        "input": file_path,
        "job_dir": job_dir.to_string_lossy(),
        "transcribe_parts": transcribe_parts,
    });
    let mut result = run_request(window, req).await?;
    if let Value::Object(map) = &mut result {
        map.insert("job_id".into(), json!(id));
    }
    Ok(result)
}

/// Render the selected parts into the selected formats. Returns the artifact
/// list (each {part, format, path | skipped}).
#[tauri::command]
pub async fn export(
    window: Window,
    job_dir: String,
    parts: Vec<String>,
    formats: Vec<String>,
    dest_dir: String,
    tempo_mult: f64,
    beat_offset: f64,
    key_sharps: Option<i64>,
    tempo_mode: String,
    octave_shift: i64,
) -> Result<Value, String> {
    let req = json!({
        "cmd": "export",
        "job_dir": job_dir,
        "parts": parts,
        "formats": formats,
        "tempo_mult": tempo_mult,
        "beat_offset": beat_offset,
        "key_sharps": key_sharps,
        "tempo_mode": tempo_mode,
        "octave_shift": octave_shift,
    });
    let mut result = run_request(window, req).await?;
    copy_artifacts_to(&mut result, &dest_dir);
    Ok(result)
}

/// Copy each written artifact from the job's working dir into the user-chosen
/// folder and rewrite its `path` so the UI reveals the file the user can keep.
fn copy_artifacts_to(result: &mut Value, dest_dir: &str) {
    let dest = std::path::Path::new(dest_dir);
    if std::fs::create_dir_all(dest).is_err() {
        return;
    }
    let Some(artifacts) = result.get_mut("artifacts").and_then(Value::as_array_mut) else {
        return;
    };
    for art in artifacts {
        let Some(obj) = art.as_object_mut() else { continue };
        let Some(src) = obj.get("path").and_then(Value::as_str).map(PathBuf::from) else {
            continue;
        };
        let Some(name) = src.file_name() else { continue };
        let dst = dest.join(name);
        match std::fs::copy(&src, &dst) {
            Ok(_) => {
                obj.insert("path".into(), json!(dst.to_string_lossy()));
            }
            Err(e) => {
                obj.insert("skipped".into(), json!(format!("copy failed: {e}")));
                obj.remove("path");
            }
        }
    }
}

/// Render one part's score (with the given tempo/nudge) to a MusicXML string
/// for live in-app preview. Returns { part, musicxml }.
#[tauri::command]
pub async fn preview(
    window: Window,
    job_dir: String,
    part: String,
    tempo_mult: f64,
    beat_offset: f64,
    key_sharps: Option<i64>,
    tempo_mode: String,
    octave_shift: i64,
) -> Result<Value, String> {
    let req = json!({
        "cmd": "preview",
        "job_dir": job_dir,
        "part": part,
        "tempo_mult": tempo_mult,
        "beat_offset": beat_offset,
        "key_sharps": key_sharps,
        "tempo_mode": tempo_mode,
        "octave_shift": octave_shift,
    });
    run_request(window, req).await
}

/// Mix the stems at the given per-part gains (the mixer state) into one mp3.
/// `gains` is a `{ part: number }` object. Returns { path }.
#[tauri::command]
pub async fn mixdown(
    window: Window,
    job_dir: String,
    gains: Value,
    dest: String,
) -> Result<Value, String> {
    let req = json!({
        "cmd": "mixdown",
        "job_dir": job_dir,
        "gains": gains,
        "dest": dest,
    });
    run_request(window, req).await
}

/// Reveal a file or folder in the OS file manager (Finder).
#[tauri::command]
pub fn reveal(app: AppHandle, path: String) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    app.opener()
        .reveal_item_in_dir(path)
        .map_err(|e| e.to_string())
}
