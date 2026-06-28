# Paper Echo

Turn audio into editable sheet music. Paper Echo separates an audio file into
parts (bass, vocals, guitar, piano, drums, other) and drafts editable notation
(MusicXML / MIDI) so you can finish in MuseScore, Dorico, or Sibelius. It makes a
**draft**, not a final score — the goal is to cut ear-copy time by ~80%.

## Architecture

Three layers:

- **`src/`** — React + TypeScript + Vite frontend (Home → Analyze → Export).
- **`src-tauri/`** — Tauri 2 / Rust backend. Spawns the Python pipeline as a
  subprocess and streams its JSON progress to the UI.
- **`python/paperecho/`** — ML pipeline (uv-managed venv):
  decode (ffmpeg) → separate (Demucs `htdemucs_6s`, 6 stems) → rhythm → transcribe
  → score (music21) → export, plus compressed audio previews for in-app playback.
  - **Transcription is per-part:** **bass & vocals** use an onset-driven tracker
    (attacks set note boundaries, pitch is filled in per span by the CREPE neural
    f0 model — repeated same-pitch notes survive); **piano** uses the ByteDance
    high-resolution piano model (polyphonic, clean chords); **guitar** uses
    Spotify Basic Pitch. You pick which parts to notate up front (all are still
    separated and playable).
  - **Rhythm** uses `beat_this` (transformer beats + downbeats; librosa fallback),
    with local octave-jump correction so a busy section can't double the tempo.

The Rust↔Python contract: a long-lived `python -m paperecho.pipeline serve`
process takes one JSON request per line on stdin and emits one JSON object per
line on stdout (`progress` / `done` / `error`); the same `analyze` / `export` /
`preview` subcommands also run standalone for CLI use. Artifacts are written to
`app_data_dir/jobs/<id>/`.

## Workflow

1. **Home** — drop an audio file. Choose which pitched parts to notate (bass /
   vocals / guitar / piano); unchecking parts you don't need (e.g. piano, the
   slowest) speeds up analysis. Every part is still separated and auditionable.
2. **Analyze** — watch progress, then audition the separated stems in a synced
   mixer (mute / solo / volume per part, plus a click track) and export a mixdown.
3. **Export** — pick parts and formats (MusicXML / MIDI / PDF), with a live score
   preview and manual reading aids: **Tempo grid** (fixed metronomic vs variable
   live tempo), **Tempo** ½×/1×/2×, **Beat nudge** (±beats), **Key** override, and
   **Octave** shift — all applied at export time on the cached job (no re-analyze).

## Prerequisites

- Node 20+, Rust (stable), Python 3.11, [uv](https://docs.astral.sh/uv/), ffmpeg.

## Setup

```sh
# Frontend deps (also auto-downloads a static ffmpeg into
# src-tauri/resources-arm64/bin via the postinstall step)
npm install

# Python pipeline deps (creates python/.venv)
cd python && uv sync && cd ..
```

> The bundled static `ffmpeg` is not committed; `npm install` fetches it via the
> `ffmpeg-static` package and `scripts/stage-ffmpeg.mjs` stages it. Re-run with
> `npm run stage:ffmpeg` if needed.

## Run

```sh
npm run tauri dev
```

Running from source, the first analyse downloads the AI models — Demucs
separation (~hundreds of MB), the CREPE pitch model, the ByteDance piano model
(~170 MB), and `beat_this` — all cached afterwards. (Packaged `.dmg` builds
**bundle these models** in the downloaded runtime, so their first analyse is
fully offline — see Distribution.)

### Useful env vars

- `PAPER_ECHO_PYTHON_DIR` — override the `python/` project location.
- `PAPER_ECHO_DEVICE` — device for separation, CREPE pitch detection, and the
  ByteDance piano model. **Auto-detects a GPU (Apple `mps` / CUDA) by default**,
  falling back to CPU; set `cpu`/`mps`/`cuda` to override. On Apple Silicon the
  GPU runs the piano model (the slowest stage) ~1.75× faster than CPU.
- `PAPER_ECHO_SHIFTS` — Demucs test-time augmentation passes (default `2`).
  Higher = cleaner separation (less smeared guitar/piano attacks) but ~(1+N)×
  slower; set `0` for fastest separation.
- `PAPER_ECHO_RUNTIME_URL` / `PAPER_ECHO_RUNTIME_SHA256` — override the first-run
  runtime download URL / expected checksum (packaged builds only; see below).

## Distribution

Packaged (`.dmg`) builds don't bundle the ~1 GB Python pipeline — they download a
slimmed, self-contained runtime **once on first launch** (a "初回セットアップ"
screen with progress), from GitHub Releases, into `<app_data>/runtime-<version>/`.
This keeps the DMG to tens of MB. The runtime bundles the AI model weights too, so
the **first analyse runs fully offline** (no further downloads). Builds are
unsigned by default (right-click → Open once); Developer ID signing + notarisation
is wired and env-driven. Build/release steps, slimming, and notarisation are in
[`DISTRIBUTION.md`](DISTRIBUTION.md). macOS Apple Silicon (arm64) only.

## Test the pipeline directly

```sh
cd python
# --transcribe-parts limits which pitched parts are notated (default: all)
./.venv/bin/python -m paperecho.pipeline analyze --input song.mp3 --job-dir /tmp/job \
    --transcribe-parts bass,vocals
# export reading aids: --tempo-mode fixed|variable, --tempo-mult, --beat-offset,
# --key-sharps, --octave-shift
./.venv/bin/python -m paperecho.pipeline export  --job-dir /tmp/job \
    --parts bass,vocals --formats musicxml,midi --tempo-mode fixed
```

## Status / scope

MVP: pitched parts (bass/vocals/guitar/piano) get notation; drums are
audio-only. PDF export requires a MuseScore CLI on PATH. Out of scope: cloud
sync, accounts, DAW features.
