# CLAUDE.md — Paper Echo

Audio → editable sheet music (MusicXML/MIDI). Produces a **draft** to cut
ear-copy time; users finish in MuseScore/Dorico/Sibelius. Full product notes in
`README.md`.

## Layout

- `src/` — React + TS + Vite frontend. Screens: `routes/Home`, `routes/Analyze`,
  `routes/Export`; `lib/ipc.ts` (Tauri invoke/event wrappers), `lib/audio.ts`
  (Web Audio stem mixer — see gotcha), `lib/types.ts`.
- `src-tauri/` — Tauri 2 / Rust. `src/python.rs` spawns the venv Python and
  streams its JSON stdout (`classify_line`); `src/commands.rs` (`analyze`,
  `export`, `reveal`).
- `python/paperecho/` — ML pipeline (uv venv). `pipeline.py` orchestrates
  `audio`→`separate`→`rhythm`→`transcribe`→`score`→`export`. `transcribe.py`
  is per-part: **bass & vocals** → `transcribe_onset` (onset-driven — onsets set
  the note boundaries, pitch is the median of **CREPE** (torchcrepe) frames per
  span, so repeated same-pitch notes don't merge into one sustain); **piano** →
  `transcribe_piano` (ByteDance `piano_transcription_inference` — polyphonic,
  clean chords; `_piano_events_to_notes` drops sub-grid blips
  <`_PIANO_MIN_DURATION` (50ms) that would otherwise become printed-16th clutter
  after grid-snap); **guitar** → Basic Pitch. `device.py::resolve_device()` is the
  one shared GPU-auto-detect (separation + CREPE + piano). Export/preview reading
  aids travel as a single `pipeline.ScoreOptions` dataclass
  (`from_request`/`from_args`), applied via `_resolve_grid`/`_resolve_key`.

## Pipeline contract (Rust ⇄ Python)

Rust keeps one long-lived `python -m paperecho.pipeline serve` process alive
(`src-tauri/src/python.rs::request`, serialized by a mutex). It sends **one JSON
request per line** on stdin (`{"cmd":"analyze|export|preview", ...}`); Python
replies with **one JSON object per line** on stdout:
`{"event":"progress"|"done"|"error", ...}`, ending each request with `done` or
`error`. Human/library logs must go to **stderr** (stdout is the machine
channel). The `serve` loop warm-imports music21 so previews are ~1.5s. The same
subcommands also work standalone for CLI use (`analyze`/`export`/`preview` with
`--` flags). Artifacts land in `app_data_dir/jobs/<uuid>/`
(`stems/`, `analysis/`, `export/`).

## Commands

```sh
npm install                      # frontend deps
cd python && uv sync             # python deps (creates python/.venv)
npm run tauri dev                # run the app (needs a display)

# Tests
cd python && ./.venv/bin/python -m pytest tests/ -q
cd src-tauri && cargo test --lib
./node_modules/.bin/tsc --noEmit # frontend type-check

# Run the pipeline directly (no GUI)
cd python
./.venv/bin/python -m paperecho.pipeline analyze --input song.mp3 --job-dir /tmp/j
./.venv/bin/python -m paperecho.pipeline export --job-dir /tmp/j --parts bass --formats musicxml,midi
```

Env: `PAPER_ECHO_PYTHON_DIR` (override python dir), `PAPER_ECHO_DEVICE`
(`cpu`/`mps`/`cuda`; **auto-detects a GPU when unset** — `separate._device` and
`transcribe._crepe_device` share this, so CREPE + piano default to mps too),
`PAPER_ECHO_SHIFTS` (Demucs test-time
augmentation passes, default 2; higher = cleaner separation but ~(1+N)× slower,
`0` = fastest).

## Gotchas (learned the hard way)

- **`cargo ... | tail` hides cargo's exit code** — the pipe returns tail's
  status. Capture cargo's exit directly (`cargo test > out 2>&1; echo $?`).
- Enabling `assetProtocol` in `tauri.conf.json` requires
  `tauri = { features = ["protocol-asset"] }` in Cargo.toml, or the build script
  fails.
- demucs 4.0.1: no `demucs.api`; use `pretrained.get_model` + `apply.apply_model`
  (no progress callback). Its `save_audio` needs torchcodec — we write stems with
  **soundfile** instead. Quality lever: `apply_model(shifts=N)` (env
  `PAPER_ECHO_SHIFTS`) — htdemucs_6s is the only 6-stem model so it can't be
  swapped for better guitar/piano; shifts is the one knob.
- **Don't read separation input with `demucs.audio.AudioFile`** — it shells out to
  ffmpeg AND **ffprobe**, but we only bundle a static ffmpeg (no ffprobe), so it
  fails on a clean Mac. `src_wav` is always a plain PCM WAV we wrote, so
  `separate.py` reads it with **soundfile** + demucs's pure-torch `convert_audio`
  (julius, no subprocess). Keeps the pipeline self-contained on the bundled ffmpeg.
- **Offline models (no first-analyse download).** Demucs/beat_this/piano weights
  ship inside the runtime tarball at `python/model-cache/` (`build-python-dist.sh`
  step 5.5). `python.rs::model_cache_envs` sets `TORCH_HOME` (Demucs + beat_this
  go through `torch.hub`) and `PAPER_ECHO_MODEL_CACHE` (read by `transcribe_piano`,
  whose ByteDance lib otherwise hardcodes `~`). CREPE-tiny + guitar nmp.onnx are
  already bundled. Dev (no `model-cache/`) is unaffected — env unset, libs fall
  back to `~/.cache`. Adding/changing models re-packs the tarball → new
  `runtime.sha256` → re-upload to the Release + rebuild the app.
- **`separate.py` is NOT in the `serve` hot-reload list** (`pipeline.cmd_serve`'s
  `reloadable` = audio/preprocess/rhythm/score/transcribe/export). Editing it
  needs an **app restart**, not just a re-analyze. Transcription/scoring edits
  hot-reload, but transcription changes still need a re-analyze (notes are cached).
- basic-pitch prints to stdout — wrap `predict()` in
  `redirect_stdout(sys.stderr)`.
- **Stem playback: buffer-decode at the hardware rate, shrink the buffer.**
  `lib/audio.ts` decodes each stem and plays them as `BufferSource`s that all
  `start` at the same ctx time — **sample-accurate**, which a stem mixer needs
  (the parts sum back into the song; any skew comb-filters/garbles the mix —
  streaming `<audio>` elements drift and sound "ぐちゃぐちゃ", don't use them).
  A full 44.1k/stereo decode is ~150 MB × 7 ≈ 1 GB → webview OOM, audio fails
  **silently**, so `load()` shrinks each decode to mono + ~22 kHz (`monoDownsample`,
  a plain createBuffer + decimation loop; ~4× smaller, decode is sequential so
  peak is one decode). **Critical: keep the AudioContext at the default hardware
  rate.** Forcing a custom rate (`new AudioContext({sampleRate})` OR
  `OfflineAudioContext(...,22050)`) makes some WebKit builds render **silence** —
  that bit us twice. The low-rate buffer just resamples up on playback. The
  on-screen diag under the stem list (`audio: N/M loaded · playing … @ <rate>Hz`)
  surfaces decode failures + the context rate since the webview console isn't
  visible. `cmd_analyze` writes mono/22k AAC previews (`audio.write_preview` →
  ffmpeg, `stems/preview/<part>.m4a`) as `meta["previews"]`; the UI decodes those
  (falls back to `stems` for pre-preview jobs — still bounded, it downmixes).
- `piano_transcription_inference` only `.to()`s the model when the device string
  contains `"cuda"` (else prints "Using CPU." and runs on CPU regardless). For
  `mps` we `pt.model.to(device)` ourselves in `transcribe_piano` — `forward()`
  reads the model's param device and moves inputs to match (~1.75× faster on mps;
  still ~0.58× realtime). It also prints to stdout → `redirect_stdout(sys.stderr)`.
- Needs Rust ≥1.85 (edition2024 in deps). Toolchain bumped 1.82→1.96.
- Tauri 2 **sync commands run on the main thread** → long work freezes the UI.
  Heavy commands must be `async` + `tauri::async_runtime::spawn_blocking`.
- music21 → MuseScore: overlapping notes make music21 emit multiple voices incl.
  `<voice>0</voice>`, which MuseScore drops → **empty measures**. `score.py`
  collapses each part to a single voice on a 16th grid to avoid this.
- Iterate on notation without re-analysis: reuse cached `jobs/<id>/analysis/` and
  render with `mscore -o out.png x.musicxml` (view via `sips -Z 1500`).
- **Distribution: the Python runtime is NOT bundled — it's downloaded on first
  launch** from GitHub Releases (`src-tauri/src/runtime.rs`; gated by
  `src/routes/RuntimeSetup.tsx`). `scripts/build-python-dist.sh` stages, **slims**
  (~170 MB: drops music21 corpus data, torchcrepe `full.pth` since CREPE uses
  `tiny`, and coremltools so basic_pitch falls back to the bundled `nmp.onnx`),
  then packs a versioned `dist-runtime/*.tar.zst` + writes `runtime.sha256` (the
  app bundles only that checksum + static ffmpeg). Keep matplotlib
  (`piano_transcription_inference` eager-imports pyplot) and torch/scipy/llvmlite.
  Full workflow in `DISTRIBUTION.md`. arm64-only (ML stack has no x86_64 macOS
  wheels). Override the download with `PAPER_ECHO_RUNTIME_URL` /
  `PAPER_ECHO_RUNTIME_SHA256`.

## Scope

MVP: pitched parts (bass/vocals/guitar/piano) get notation; drums/other are
audio-only. Meter limited to x/4. Out of scope: cloud, accounts, DAW features.
