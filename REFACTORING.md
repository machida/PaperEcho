# Refactoring plan

Cleanup of duplication / parameter sprawl that accumulated while adding the
transcription engines, playback, rhythm fixes, and the export reading-aids
(tempo grid, octave, part selection, …). Each task is behaviour-preserving and
verified green by `pytest` / `tsc --noEmit` / `cargo check`.

Status: `[ ]` todo · `[x]` done — **all complete (2026-06-24)**; verified green by
`pytest` (46), `tsc --noEmit`, and `cargo check`.

## Tasks

- [x] **R1 — Unify device detection.** `separate._device()` and
  `transcribe._crepe_device()` are now near-identical GPU-auto-detect helpers.
  Extract one shared `paperecho.device.resolve_device()` and have both call it.
  *Files:* `python/paperecho/device.py` (new), `separate.py`, `transcribe.py`.

- [x] **R2 — Group score-shaping params into `ScoreOptions`.** `cmd_export` and
  `cmd_preview` each take 5 loose params (`tempo_multiplier`, `beat_offset`,
  `key_sharps_override`, `tempo_mode`, `octave_shift`) that are also re-listed in
  the serve dispatch, `argparse`, and `main()`. Collapse them into a single
  `@dataclass ScoreOptions` (with a `from_request` / `from_args` builder) to kill
  the repetition and make future options one-line additions.
  *Files:* `python/paperecho/pipeline.py`.

- [x] **R3 — Extract `_resolve_grid()`.** `cmd_export` and `cmd_preview` repeat
  the same ~8 lines: read `bpm/time_sig/beats/beats_per_bar/downbeat_phase`, apply
  `to_fixed_grid` (when fixed), then `apply_tempo_multiplier` (resetting the
  downbeat phase). Pull it into one helper returning the resolved grid.
  *Files:* `python/paperecho/pipeline.py`.

- [x] **R4 — `<Segmented>` control component.** `Export.tsx` hand-rolls the same
  `.tempo-toggle` + `.seg` button map four times (Tempo grid, Tempo, Beat nudge,
  Octave). Extract one `<Segmented options value onChange>` component.
  *Files:* `src/components/Segmented.tsx` (new), `src/routes/Export.tsx`.

- [x] **R5 — Options object for the export/preview IPC calls.** `exportParts` (8
  positional args) and `previewScore` (7) are error-prone at the call site.
  Bundle the score-shaping args into one `ScoreControls` object on the TS side
  (the `invoke` payload keeps the same keys, so the Rust commands are unchanged).
  *Files:* `src/lib/ipc.ts`, `src/routes/Export.tsx`.

## Out of scope (noted, not doing now)

- Grouping the Rust `export`/`preview` command args into a serde struct — would
  change the wire format for no real gain; the commands are thin passthroughs.
- Renaming `transcribe.MONO_PARTS` vs `score.MONOPHONIC_PARTS` to one name —
  cosmetic, touches the hot transcription path; skip.
