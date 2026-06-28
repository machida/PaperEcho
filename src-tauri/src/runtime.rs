//! First-run download of the Python ML runtime.
//!
//! To keep the app download small we no longer bundle the ~1 GB Python pipeline
//! inside the `.app`. Instead a slimmed, self-contained runtime is published as a
//! zstd tarball on GitHub Releases (built by `scripts/build-python-dist.sh`,
//! which also emits the sha256 we bundle as `runtime.sha256`). On first launch
//! the frontend gates on [`status`]; if the runtime isn't present yet it calls
//! [`download`], which streams the tarball, verifies its hash against the bundled
//! checksum, extracts it into `<app_data>/runtime-<app_version>/`, and points the
//! pipeline there via [`crate::python::set_python_dir`].
//!
//! Versioning: the install dir is keyed to the app version, so a new app version
//! fetches its own runtime and an out-of-date one is simply re-fetched.
//!
//! Overrides (CLI / testing): `PAPER_ECHO_RUNTIME_URL` (full tarball URL) and
//! `PAPER_ECHO_RUNTIME_SHA256` (expected hex digest) bypass the release/bundle
//! defaults — handy before a real Release exists.

use std::io::{BufReader, Write};
use std::path::PathBuf;

use futures_util::StreamExt;
use serde::Serialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, Manager, Window};

use crate::python;

/// Where release tarballs live. The per-version URL is
/// `<base>/v<ver>/paperecho-runtime-<ver>-<arch>.tar.zst`. Override the whole URL
/// with `PAPER_ECHO_RUNTIME_URL` until the real release is published.
const RELEASE_BASE: &str = "https://github.com/machida/PaperEcho/releases/download";

const PROGRESS_EVENT: &str = "runtime://progress";

/// The runtime tarball's arch slug (matches `build-python-dist.sh <arch>`).
fn arch_slug() -> &'static str {
    match std::env::consts::ARCH {
        "x86_64" => "x86_64",
        _ => "arm64",
    }
}

fn app_version(app: &AppHandle) -> String {
    app.package_info().version.to_string()
}

fn tarball_name(ver: &str, arch: &str) -> String {
    format!("paperecho-runtime-{ver}-{arch}.tar.zst")
}

/// `<app_data>/runtime-<ver>` — the extracted runtime root (holds `python/`).
fn installed_dir(app: &AppHandle, ver: &str) -> Result<PathBuf, String> {
    let parent = app.path().app_data_dir().map_err(|e| e.to_string())?;
    Ok(parent.join(format!("runtime-{ver}")))
}

/// The interpreter that must exist for an install to count as complete.
fn interpreter_in(python_dir: &std::path::Path) -> PathBuf {
    if cfg!(windows) {
        python_dir.join("runtime").join("python.exe")
    } else {
        python_dir.join("runtime").join("bin").join("python3.11")
    }
}

fn download_url(ver: &str, arch: &str) -> String {
    if let Ok(u) = std::env::var("PAPER_ECHO_RUNTIME_URL") {
        return u;
    }
    format!("{RELEASE_BASE}/v{ver}/{}", tarball_name(ver, arch))
}

/// The expected sha256: the `PAPER_ECHO_RUNTIME_SHA256` override, else the
/// `runtime.sha256` we bundle as an app resource (searched the same way as the
/// bundled ffmpeg). `None` means "no checksum available" — we still install but
/// can't verify (used only in dev/override-less scenarios).
fn expected_sha(app: &AppHandle) -> Option<String> {
    if let Ok(s) = std::env::var("PAPER_ECHO_RUNTIME_SHA256") {
        return Some(s.trim().to_lowercase());
    }
    let resources = app.path().resource_dir().ok()?;
    let arch_dir = format!("resources-{}", arch_slug());
    for cand in [
        resources.join(&arch_dir).join("runtime.sha256"),
        resources.join("runtime.sha256"),
    ] {
        if let Ok(text) = std::fs::read_to_string(&cand) {
            // accept "<hex>" or "<hex>  filename"
            if let Some(tok) = text.split_whitespace().next() {
                return Some(tok.trim().to_lowercase());
            }
        }
    }
    None
}

/// If a matching runtime is already installed, point the pipeline at it. Called
/// once at app setup (after the bundled-resource wiring). No-op in dev (the
/// `.venv` fallback in `python.rs` covers that) and when nothing is installed yet.
pub fn resolve_at_startup(app: &AppHandle) {
    let ver = app_version(app);
    let Ok(dir) = installed_dir(app, &ver) else {
        return;
    };
    let python_dir = dir.join("python");
    if interpreter_in(&python_dir).exists() {
        python::set_python_dir(python_dir);
    }
}

#[derive(Serialize)]
pub struct Status {
    /// A usable interpreter is already resolvable (bundled/downloaded/dev/env).
    pub ready: bool,
    pub version: String,
    pub arch: String,
    /// Where the runtime would be fetched from if not ready (for diagnostics).
    pub url: String,
}

pub fn status(app: &AppHandle) -> Status {
    let ver = app_version(app);
    let arch = arch_slug().to_string();
    let url = download_url(&ver, &arch);
    Status {
        ready: python::resolved_python_exists(),
        version: ver,
        arch,
        url,
    }
}

fn emit(window: &Window, phase: &str, downloaded: u64, total: u64) {
    let _ = window.emit(
        PROGRESS_EVENT,
        json!({ "phase": phase, "downloaded": downloaded, "total": total }),
    );
}

fn hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

/// Recursively clear `com.apple.quarantine` from the extracted runtime (macOS).
///
/// The runtime — including its mach-o files (the CPython interpreter, torch and
/// other dylibs) — is downloaded *after* install, so it is **not covered by the
/// app's code signature**. Files we write ourselves normally carry no quarantine
/// xattr, but if the app is notarised and run under Gatekeeper a stray quarantine
/// bit would make first execution of those binaries fail. Stripping it here is a
/// cheap, harmless safeguard that makes the downloaded runtime robust regardless
/// of the app's signing/notarisation state. Best-effort: errors are ignored.
fn strip_quarantine(_dir: &std::path::Path) {
    #[cfg(target_os = "macos")]
    {
        let _ = std::process::Command::new("/usr/bin/xattr")
            .arg("-dr")
            .arg("com.apple.quarantine")
            .arg(_dir)
            .status();
    }
}

/// Fetch + verify + extract the runtime, then wire the pipeline to it. Emits
/// `runtime://progress` ({phase, downloaded, total}) throughout. Safe to retry:
/// it streams to a `.partial` file and only swaps the install dir into place
/// once extraction succeeds.
pub async fn download(app: AppHandle, window: Window) -> Result<(), String> {
    let ver = app_version(&app);
    let arch = arch_slug().to_string();
    let url = download_url(&ver, &arch);
    let expected = expected_sha(&app);

    let parent = app.path().app_data_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&parent).map_err(|e| e.to_string())?;
    let installed = installed_dir(&app, &ver)?;
    let tmp_tar = parent.join(format!("runtime-{ver}.partial.tar.zst"));

    // --- stream download (async) ---
    emit(&window, "download", 0, 0);
    let resp = reqwest::Client::new()
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("download request failed for {url}: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!("download failed: HTTP {} for {url}", resp.status()));
    }
    let total = resp.content_length().unwrap_or(0);
    let mut file = std::fs::File::create(&tmp_tar).map_err(|e| e.to_string())?;
    let mut hasher = Sha256::new();
    let mut downloaded: u64 = 0;
    let mut last_emit: u64 = 0;
    let mut stream = resp.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| format!("download interrupted: {e}"))?;
        file.write_all(&chunk).map_err(|e| e.to_string())?;
        hasher.update(&chunk);
        downloaded += chunk.len() as u64;
        if downloaded - last_emit >= 2_000_000 {
            emit(&window, "download", downloaded, total);
            last_emit = downloaded;
        }
    }
    file.flush().map_err(|e| e.to_string())?;
    drop(file);
    emit(&window, "download", downloaded, total.max(downloaded));

    // --- verify ---
    emit(&window, "verify", downloaded, downloaded);
    let got = hex(&hasher.finalize());
    if let Some(exp) = &expected {
        if !exp.eq_ignore_ascii_case(&got) {
            let _ = std::fs::remove_file(&tmp_tar);
            return Err(format!(
                "runtime checksum mismatch (expected {exp}, got {got}) — refusing to install"
            ));
        }
    }

    // --- extract (blocking: zstd + tar + fs) ---
    emit(&window, "extract", downloaded, downloaded);
    let staging = parent.join(format!("runtime-{ver}.incoming"));
    let installed_c = installed.clone();
    let tmp_tar_c = tmp_tar.clone();
    let staging_c = staging.clone();
    tauri::async_runtime::spawn_blocking(move || -> Result<(), String> {
        let _ = std::fs::remove_dir_all(&staging_c);
        std::fs::create_dir_all(&staging_c).map_err(|e| e.to_string())?;
        let f = std::fs::File::open(&tmp_tar_c).map_err(|e| e.to_string())?;
        let dec = zstd::Decoder::new(BufReader::new(f)).map_err(|e| e.to_string())?;
        let mut ar = tar::Archive::new(dec);
        ar.unpack(&staging_c)
            .map_err(|e| format!("failed to extract runtime: {e}"))?;
        // The tarball contains a top-level `python/`. Verify before swapping in.
        if !interpreter_in(&staging_c.join("python")).exists() {
            return Err("extracted runtime is missing its interpreter".into());
        }
        let _ = std::fs::remove_dir_all(&installed_c);
        std::fs::rename(&staging_c, &installed_c)
            .map_err(|e| format!("failed to move runtime into place: {e}"))?;
        // Clear any quarantine bit so the downloaded mach-o binaries (interpreter,
        // torch dylibs) run under a notarised/Gatekeeper'd app (they aren't covered
        // by the app's signature — see strip_quarantine).
        strip_quarantine(&installed_c);
        let _ = std::fs::remove_file(&tmp_tar_c);
        Ok(())
    })
    .await
    .map_err(|e| format!("extract task failed: {e}"))??;

    let python_dir = installed.join("python");
    if !interpreter_in(&python_dir).exists() {
        return Err("runtime install finished but the interpreter is missing".into());
    }
    python::set_python_dir(python_dir);
    emit(&window, "done", downloaded, downloaded);
    Ok(())
}
