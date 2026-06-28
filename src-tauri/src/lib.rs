mod commands;
mod python;
mod runtime;

use tauri::Manager;

/// Point the pipeline at the bundled Python project + ffmpeg in a packaged build.
/// In dev these resources aren't present, so the relative-path fallbacks stay in
/// effect (python.rs::python_dir / the `ffmpeg` on PATH).
fn wire_bundled_resources(app: &tauri::App) {
    let Ok(resources) = app.path().resource_dir() else {
        return;
    };
    let ffmpeg_name = if cfg!(windows) { "ffmpeg.exe" } else { "ffmpeg" };
    // A universal build bundles BOTH `resources-arm64/` and `resources-x86_64/`;
    // pick the slice matching THIS binary's running arch (the other arch's
    // interpreter/ffmpeg can't execute). Then fall back to the resource root for
    // single-arch builds. A `python/` dir holding `runtime/` is the marker.
    let arch_dir = match std::env::consts::ARCH {
        "aarch64" => "resources-arm64",
        "x86_64" => "resources-x86_64",
        _ => "resources-arm64",
    };
    let roots = [resources.join(arch_dir), resources.clone()];
    for root in roots {
        let py = root.join("python");
        if py.join("runtime").is_dir() || py.join(".venv").is_dir() {
            python::set_python_dir(py);
            // Bundled ffmpeg wins over the user's PATH (audio.py reads
            // PAPER_ECHO_FFMPEG; the spawned Python inherits it).
            let ffmpeg = root.join("bin").join(ffmpeg_name);
            if ffmpeg.is_file() {
                std::env::set_var("PAPER_ECHO_FFMPEG", ffmpeg);
            }
            break;
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            // ffmpeg (+ any legacy bundled python) from app resources, then a
            // first-run-downloaded runtime in app-data. The Python pipeline is no
            // longer bundled — it's fetched on first launch (see runtime.rs).
            wire_bundled_resources(app);
            runtime::resolve_at_startup(app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::runtime_status,
            commands::download_runtime,
            commands::analyze,
            commands::export,
            commands::preview,
            commands::mixdown,
            commands::reveal,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
