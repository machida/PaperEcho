# Distributing Paper Echo (macOS)

Decisions:

| Question | Choice |
|----------|--------|
| Target | **macOS Apple Silicon (arm64) only** — Intel dropped (ML stack has no x86_64 macOS wheels; see "Universal") |
| Signing | **Unsigned by default** (self / internal — recipients right-click → Open once). Developer ID signing + notarisation is wired and env-driven — see "Signing & notarisation". |
| Python backend | **Slimmed runtime + bundled offline models, downloaded on first launch** from GitHub Releases (not bundled in the `.app`) |

**Why a download, not a bundle.** The Python ML pipeline (torch, scipy, llvmlite,
…) is ~1 GB. Bundling it made a 384 MB DMG / 1.2 GB `.app`. We now ship a tiny
app shell and fetch the runtime once, on first launch:

- The DMG drops to **tens of MB** (just the Tauri shell + ffmpeg + a checksum).
- App updates (shell/bugfixes) are small; the heavy runtime is re-fetched only
  when its version changes.
- Net bytes a new user transfers is about the same, but it's a small install +
  a one-time background download with a progress screen.

The runtime is also **slimmed** (~170 MB of never-loaded payload removed) before
packing — see "Slim" below.

---

## How the first-run download works

1. `scripts/build-python-dist.sh arm64` stages a self-contained CPython +
   the locked deps (see "The runtime"), **slims** it, then packs it as
   `dist-runtime/paperecho-runtime-<ver>-arm64.tar.zst` and writes its sha256 to
   both a sidecar and the bundled resource `src-tauri/resources-arm64/runtime.sha256`.
2. You upload that tarball to a **GitHub Release** tagged `v<ver>`.
3. The `.app` bundles only `resources-arm64/bin/ffmpeg` + `resources-arm64/runtime.sha256`.
4. On launch (`src-tauri/src/runtime.rs`):
   - `runtime_status` reports whether a usable interpreter is resolvable
     (dev `.venv`, an env override, or an already-installed download).
   - If not, the frontend (`src/routes/RuntimeSetup.tsx`) shows a "初回セットアップ"
     screen and calls `download_runtime`, which streams the tarball, **verifies
     its sha256 against the bundled checksum**, extracts it to
     `<app_data>/runtime-<app_version>/python/`, and points the pipeline there.
   - The install dir is keyed to the app version, so a new app version fetches
     its own runtime and a stale one is simply re-fetched.

**Configurable URL** (`runtime.rs`): the default is
`<RELEASE_BASE>/v<ver>/paperecho-runtime-<ver>-<arch>.tar.zst`. Override the whole
URL with `PAPER_ECHO_RUNTIME_URL` and the expected hash with
`PAPER_ECHO_RUNTIME_SHA256` — handy for testing before a real Release exists, or
to host the tarball elsewhere. **Update `RELEASE_BASE` in `runtime.rs` to the real
repo** before publishing (it's currently a placeholder).

---

## The runtime (`build-python-dist.sh`)

A normal `python/.venv` is **not bundle-able**: its `bin/python` symlinks the dev
toolchain. So we **copy a uv-managed *standalone* CPython** (python-build-standalone,
fully relocatable) into the staged tree and install the locked deps into that copy
— nothing points outside it. uv marks its standalone pythons "externally managed",
so we **delete `runtime/lib/python3.11/EXTERNALLY-MANAGED`** before
`uv pip install --break-system-packages`. `python.rs::venv_python` prefers
`<dir>/runtime/bin/python3.11` over a dev `<dir>/.venv`.

**Always run the printed verify step** on the staged runtime (it runs the
interpreter under `env -i` so PATH leakage can't mask a broken bundle).

### Slim (~170 MB removed, each verified against the import path)

`build-python-dist.sh` prunes payload the pipeline never loads:

- **`music21/corpus` score data** (~66 MB) — we never parse the bundled corpus.
  The `corpus` *package* (its `*.py`) is imported at music21 init, so we keep the
  code and delete only the composer score-data subdirs + `_metadataCache`.
- **`torchcrepe/assets/full.pth`** (~85 MB) — `transcribe.py` pins CREPE
  `model="tiny"`; the full checkpoint is never loaded.
- **`coremltools`** (~19 MB) — basic_pitch picks its backend by what's importable
  (tf > coreml > tflite > **onnx**). Removing coremltools makes
  `ICASSP_2022_MODEL_PATH` resolve to the bundled `nmp.onnx` (onnxruntime is
  present); basic_pitch's `__init__` guards the import, so import still works.

**Kept (load-bearing — do NOT prune):** `matplotlib`
(`piano_transcription_inference/models.py` eager-imports `pyplot` at module top),
`sympy` (torch), `numba`/`llvmlite` (librosa), `scipy`/`scikit-learn` (librosa).
`torch` (406 MB) is the size driver and is required for mps — untouchable.

### ffmpeg (still bundled — it's small)

Homebrew's ffmpeg is dynamically linked (62 dylibs). We bundle a **static** build
at `src-tauri/resources-arm64/bin/ffmpeg`. `lib.rs::wire_bundled_resources` sets
`PAPER_ECHO_FFMPEG` to it; `audio.ffmpeg_bin()` prefers that over PATH.

The binary is **not committed** (it's large + third-party). It's downloaded by
the `ffmpeg-static` devDependency during `npm install` and copied into place by
`scripts/stage-ffmpeg.mjs` (wired to npm's `postinstall`; re-run on demand with
`npm run stage:ffmpeg`). Override the arch with `PAPER_ECHO_ARCH`.

---

## Release workflow (cut a build)

```sh
# 1. stage + slim + pack the runtime, and write the bundled checksum
./scripts/build-python-dist.sh arm64
#    -> dist-runtime/paperecho-runtime-<ver>-arm64.tar.zst (+ .sha256)
#    -> src-tauri/resources-arm64/runtime.sha256   (bundled, the verify source)

# 2. build + ad-hoc sign the app shell (unsigned dist still needs a signature so
#    the bundled ffmpeg loads)
npm run tauri build -- --target aarch64-apple-darwin
codesign --force --deep -s - \
  "src-tauri/target/aarch64-apple-darwin/release/bundle/macos/Paper Echo.app"

# 3. publish: create a GitHub Release tagged v<ver> and upload the tarball as
#    paperecho-runtime-<ver>-arm64.tar.zst (the asset name runtime.rs expects).
#    Then ship the DMG.
```

Requires `zstd` (`brew install zstd`) for packing.

**First-launch flow for the recipient:** right-click → Open the DMG'd app
(unsigned → Gatekeeper warns once), then the app downloads the runtime (~430 MB
— slimmed pipeline + bundled offline models, progress shown) and is ready.
Subsequent launches skip the download, and the **first analyse runs fully
offline** (no model downloads). Notarised builds skip the right-click → Open.

---

## Signing & notarisation (optional, env-driven)

The default build is **unsigned** (ad-hoc) and needs no Apple account. A
**Developer ID + notarised** build removes the right-click → Open step. It's
wired so the *unsigned path is unchanged* — signing only kicks in when the env is
present:

- `src-tauri/entitlements.plist` — minimal Hardened-Runtime entitlements for the
  **app process** (just `allow-jit` for WebKit), referenced from
  `tauri.conf.json` (`bundle.macOS.entitlements`). The downloaded Python runtime
  is a **separate child process** not covered by the app signature, so it needs
  no entitlements; on arm64 it's already ad-hoc-signed (python-build-standalone),
  enough to execute, and as a child it can JIT (torch) freely.
- `src-tauri/src/runtime.rs::strip_quarantine` clears `com.apple.quarantine` from
  the extracted runtime after install, so its mach-o files run under a notarised /
  Gatekeeper'd app without a first-execution block. (Files the app writes aren't
  normally quarantined — this is a belt-and-suspenders, harmless when unsigned.)

**To cut a notarised build** (needs an Apple Developer Program membership + a
*Developer ID Application* certificate in your login keychain):

```sh
export APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"
# notarisation creds — either an app-specific password:
export APPLE_ID="you@example.com"
export APPLE_PASSWORD="abcd-efgh-ijkl-mnop"   # app-specific password
export APPLE_TEAM_ID="TEAMID"
# …or an App Store Connect API key instead:
#   APPLE_API_KEY / APPLE_API_ISSUER / APPLE_API_KEY_PATH
npm run tauri build -- --target aarch64-apple-darwin
```

With `APPLE_SIGNING_IDENTITY` set, `tauri build` signs with the Hardened Runtime
+ the entitlements, then (with the notarisation creds) submits to Apple and
**staples** the ticket. **Caveat — don't re-sign afterwards:** the unsigned
workflow above does a manual `codesign --force --deep -s -` + `hdiutil` DMG; for a
notarised build you must **NOT** run that manual re-sign (it strips the Developer
ID signature + staple). Let `tauri build` produce the signed `.app`/`.dmg`
directly, or regenerate the DMG from the *signed-and-stapled* app. Verify with:
`codesign -dv --verbose=4 "Paper Echo.app"`, `spctl -a -vvv "Paper Echo.app"`,
`xcrun stapler validate "Paper Echo.app"`.

*Not yet run end-to-end here* (no certificate on the build machine). The config,
entitlements, and quarantine handling are in place; the commands above are the
remaining manual step once you have the cert.

---

## Universal (Intel) — ❌ NOT FEASIBLE (decided arm64-only)

The ML stack dropped Intel macOS wheels. Verified with uv
(`--python-platform x86_64-apple-darwin`): `torch==2.12.0` and
`onnxruntime==1.27.0` ship **`macosx_*_arm64` only**. An Intel build would need
downgrading torch (≤2.2) + onnxruntime + the cascade — a behaviour-changing
regression for an EOL platform. `runtime.rs`/`lib.rs` keep arch-selection and the
`$UV`-parameterised script (harmless, ready if x86_64 macOS wheels ever return),
but we ship arm64 only.

---

## Models (bundled in the runtime tarball — fully offline)

The AI model weights ship **inside the runtime tarball** at
`python/model-cache/`, so a **first analyse never downloads** — the only network
hop is the one-time runtime download itself. `build-python-dist.sh` stages
(~294 MB):

| Model | File | Resolved via |
|-------|------|--------------|
| Demucs `htdemucs_6s` | `model-cache/torch/hub/checkpoints/5c90dfd2-34c22ccb.th` (~55 MB) | `TORCH_HOME` → `torch.hub` |
| beat_this `final0` | `model-cache/torch/hub/checkpoints/beat_this-final0.ckpt` (~81 MB) | `TORCH_HOME` → `torch.hub` |
| ByteDance piano | `model-cache/piano/note_F1=0.9677_pedal_F1=0.9186.pth` (~172 MB) | `PAPER_ECHO_MODEL_CACHE` → explicit `checkpoint_path` |

CREPE-tiny (bass/vocals) already ships inside `torchcrepe`; guitar uses the
bundled `nmp.onnx` — neither needs staging.

**Wiring** (`src-tauri/src/python.rs::model_cache_envs`): when the spawned
interpreter's dir has a `model-cache/`, Rust sets `TORCH_HOME=<dir>/model-cache/torch`
(Demucs + beat_this both fetch through `torch.hub.load_state_dict_from_url`, which
reads `$TORCH_HOME/hub`) and `PAPER_ECHO_MODEL_CACHE=<dir>/model-cache` (read by
`transcribe.py::transcribe_piano`, since the ByteDance lib otherwise hardcodes
`~/piano_transcription_inference_data` and `wget`s into it). No user-home writes.
In dev (`.venv`, no `model-cache/`) the env vars are unset and the libs fall back
to their normal `~/.cache` download — so dev is unaffected.

**Staging** (`build-python-dist.sh` step 5.5): reuses the build machine's
`~/.cache/torch/hub/checkpoints` + `~/piano_transcription_inference_data` when
present (fast); otherwise the staged interpreter fetches Demucs + beat_this via
`torch.hub`, and the piano checkpoint is `curl`ed from Zenodo.

**Verify offline** (no home writes, no network for models):
```sh
env -i HOME=/tmp/emptyhome PATH=/opt/homebrew/bin:/usr/bin:/bin \
  PAPER_ECHO_FFMPEG=src-tauri/resources-arm64/bin/ffmpeg \
  TORCH_HOME=<…>/python/model-cache/torch \
  PAPER_ECHO_MODEL_CACHE=<…>/python/model-cache \
  PAPER_ECHO_SHIFTS=0 PAPER_ECHO_DEVICE=cpu \
  python/.venv/bin/python -m paperecho.pipeline analyze --input clip.wav --job-dir /tmp/j
# then assert /tmp/emptyhome/.cache/torch and /tmp/emptyhome/piano_transcription_inference_data DO NOT exist
```

## Notes / gotchas

- `targets` is `[app, dmg]` (macOS); don't use `all` (it tries deb/rpm).
- **Unsigned** apps: recipients right-click → Open (or `xattr -dr
  com.apple.quarantine "Paper Echo.app"`).
- **Notarisation & the downloaded runtime:** the runtime is downloaded *after*
  install, so its mach-o files (interpreter, torch dylibs) are **not covered by
  the app's signature**. They run anyway because (a) they're a *separate child
  process*, not loaded into the app, and on arm64 are already ad-hoc-signed by
  python-build-standalone, and (b) `runtime.rs::strip_quarantine` clears the
  quarantine bit on extract so Gatekeeper doesn't block first execution. See
  "Signing & notarisation" above. (No need to ship them Developer-ID-signed.)
- Enabling `assetProtocol` needs `tauri = { features = ["protocol-asset"] }` in
  Cargo.toml or the build script fails.
- **Sign before the DMG, or rebuild the DMG after signing.** `tauri build` bundles
  the `.dmg` from the *pre-codesign* app, so re-signing the standalone `.app`
  afterwards leaves a stale app inside the DMG. Fix: after `codesign --force
  --deep -s - "Paper Echo.app"`, regenerate the DMG from the signed app —
  `hdiutil create -volname "Paper Echo" -srcfolder "…/Paper Echo.app" -ov -format
  UDZO "Paper Echo_<ver>_aarch64.dmg"`. (tauri's `bundle_dmg.sh` Finder layout can
  also be very slow on machines with many FinderSync extensions; the plain
  `hdiutil` form is fast and good enough for internal builds — it just drops the
  drag-to-Applications layout.)
- MuseScore (PDF export) is **not** bundled — PDF stays optional; MusicXML/MIDI
  work without it.
