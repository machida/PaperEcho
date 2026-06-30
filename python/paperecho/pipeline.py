"""CLI orchestrator. Invoked by the Rust backend as a subprocess.

Two commands:
  analyze --input <file> --job-dir <dir>
  export  --job-dir <dir> --parts bass,vocals --formats musicxml,midi

Progress and results are streamed as one JSON object per line on stdout
(see paperecho.progress). Human logs go to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from . import progress
from .separate import PITCHED_STEMS, STEMS


@dataclass
class ScoreOptions:
    """Manual reading aids applied when building a score at export/preview time
    (the detected notes/rhythm are cached; these reshape how they're written)."""

    tempo_multiplier: float = 1.0
    beat_offset: float = 0.0
    key_sharps_override: int | None = None
    tempo_mode: str = "fixed"  # "fixed" metronomic grid | "variable" detected
    octave_shift: int = 0

    @classmethod
    def from_request(cls, req: dict) -> ScoreOptions:
        return cls(
            tempo_multiplier=req.get("tempo_mult", 1.0),
            beat_offset=req.get("beat_offset", 0.0),
            key_sharps_override=req.get("key_sharps"),
            tempo_mode=req.get("tempo_mode", "fixed"),
            octave_shift=req.get("octave_shift", 0),
        )

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> ScoreOptions:
        return cls(
            tempo_multiplier=args.tempo_mult,
            beat_offset=args.beat_offset,
            key_sharps_override=args.key_sharps,
            tempo_mode=args.tempo_mode,
            octave_shift=args.octave_shift,
        )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_analyze(
    input_path: str, job_dir: str, transcribe_parts: list[str] | None = None
) -> None:
    from . import audio, rhythm, separate, transcribe

    job = Path(job_dir)
    src = Path(input_path)
    if not src.exists():
        progress.error(f"File not found: {src}")
        return
    if src.suffix.lower() not in audio.SUPPORTED_INPUT:
        progress.error(f"Unsupported input format: {src.suffix}")
        return

    # 1. decode -> normalized wav
    progress.progress("decode", 0, "Decoding audio")
    source_wav = audio.decode_to_wav(src, job / "work" / "source.wav")
    progress.progress("decode", 100, "Decoded")

    # 2. separate -> 6 stems
    progress.progress("separate", 0, "Loading separation model")
    stems = separate.separate(
        source_wav,
        job / "stems",
        on_progress=lambda pct, msg: progress.progress("separate", pct, msg),
    )

    # 3. rhythm (from the full mix)
    progress.progress("rhythm", 0, "Estimating tempo")
    rhythm_info = rhythm.estimate_rhythm(source_wav)
    _write_json(job / "analysis" / "rhythm.json", rhythm_info)
    progress.progress("rhythm", 100, f"~{rhythm_info['tempo']} BPM")

    # 3b. render the detected beats as a click track (an extra audio-only "part")
    import soundfile as sf

    beats = rhythm_info.get("beats") or []
    bpb = rhythm_info.get("beats_per_bar", 4)
    dphase = rhythm_info.get("downbeat_phase", 0)
    downbeats = beats[dphase::bpb] if beats else []
    duration = float(sf.info(str(source_wav)).duration)
    click_path = job / "stems" / "click.wav"
    audio.write_click_track(beats, downbeats, duration, click_path)
    stems["click"] = str(click_path)

    # 4. transcribe each pitched stem -> note events. The user can pick a subset
    # (every part is still separated + auditionable; we just skip notating the
    # ones they don't want — e.g. the slow ByteDance piano on a bass-only job).
    notes_dir = job / "analysis" / "notes"
    pitched = [s for s in PITCHED_STEMS if s in stems]
    if transcribe_parts is not None:
        pitched = [s for s in pitched if s in transcribe_parts]
    for i, stem in enumerate(pitched):
        progress.progress("transcribe", i / len(pitched) * 100, f"Transcribing {stem}")
        notes = transcribe.transcribe_part(stems[stem], part=stem)
        _write_json(notes_dir / f"{stem}.json", {"part": stem, "notes": notes})
    progress.progress("transcribe", 100, "Transcription complete")

    # 5. compressed previews for in-app playback (the full stems are too large to
    # decode into the webview all at once — see audio.write_preview). Each is an
    # ffmpeg subprocess, so run them in parallel (they're independent and release
    # the GIL); a failure is non-fatal (that stem just won't be auditionable).
    progress.progress("preview", 0, "Encoding stem previews")
    previews: dict[str, str] = {}
    prev_dir = job / "stems" / "preview"

    def _encode(item):
        name, wav = item
        return name, str(audio.write_preview(wav, prev_dir / f"{name}.m4a"))

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(8, len(stems) or 1)) as pool:
        for fut in [pool.submit(_encode, it) for it in stems.items()]:
            try:
                name, path = fut.result()
                previews[name] = path
            except Exception as e:  # noqa: BLE001 - preview is best-effort
                progress.log(f"preview encode failed: {e}")
    progress.progress("preview", 100, "Previews ready")

    meta = {
        "input": str(src),
        "source_wav": str(source_wav),
        # "click" is a synthetic audio-only part (not scoreable).
        "parts": [s for s in STEMS if s in stems] + ["click"],
        "pitched_parts": pitched,
        "stems": stems,
        "previews": previews,
        "rhythm": rhythm_info,
    }
    _write_json(job / "analysis" / "meta.json", meta)
    progress.done(stage="analyze", job_dir=str(job), **meta)


# Derived "parts" that reuse another part's notes with a clef/transpose tweak.
# `bass_treble` lets a guitarist who struggles with the bass clef read the bass
# line in treble clef, written two octaves up. Available whenever `bass` is.
# +24 (not +12) lands the bass on/within the treble staff: a one-octave lift
# still left the low notes 2-3 ledger lines below, so we raise it another octave.
DERIVED_PARTS = {
    "bass_treble": {"source": "bass", "clef_name": "treble", "transpose": 24},
}

# Per-part clef overrides for real parts. Guitar is a transposing instrument that
# sounds an octave below where it's written, so it uses the octave-treble (8vb)
# clef — the score reads an octave up (standard guitar notation) while the notes'
# actual pitch (and the exported MIDI) stays at concert pitch.
PART_CLEF = {"guitar": "treble8vb"}


def _scoreable(meta: dict) -> tuple[list[str], list[str]]:
    """(real scoreable parts, derived scoreable parts) for this job."""
    pitched = meta.get("pitched_parts", [])
    derived = [d for d, c in DERIVED_PARTS.items() if c["source"] in pitched]
    return pitched, derived


def _build_part_score(
    job: Path, part: str, *, bpm, time_sig, beats, beats_per_bar,
    downbeat_phase, key_sharps, beat_offset, octave_shift=0,
):
    """Build a music21 score for a real or derived part, or None if its notes
    are missing. Derived parts reuse their source part's notes."""
    from .score import MONOPHONIC_PARTS, build_score

    spec = DERIVED_PARTS.get(part)
    source = spec["source"] if spec else part
    notes_path = job / "analysis" / "notes" / f"{source}.json"
    if not notes_path.exists():
        return None
    notes = _read_json(notes_path)["notes"]
    monophonic = source in MONOPHONIC_PARTS
    # Coarser 8th-note grid for single-line parts keeps their rhythm readable;
    # 16th for the busier polyphonic parts.
    grid = 2 if monophonic else 4
    if spec:
        extra = {"clef_name": spec["clef_name"], "transpose": spec["transpose"]}
    else:
        extra = {}
        if part in PART_CLEF:
            extra["clef_name"] = PART_CLEF[part]
    # Manual ±octave placement (the pitch is right; the user picks the register
    # to read it in). Stacks on any derived-part transpose.
    if octave_shift:
        extra["transpose"] = extra.get("transpose", 0) + octave_shift * 12
    return build_score(
        notes, bpm, time_sig, part_name=part, beats=beats,
        monophonic=monophonic, beats_per_bar=beats_per_bar,
        downbeat_phase=downbeat_phase, key_sharps=key_sharps, grid=grid,
        beat_offset=beat_offset, **extra,
    )


def _shared_key_sharps(job: Path, pitched_parts: list[str]) -> int | None:
    """Estimate one key for the whole song from all pitched parts' notes."""
    from music21 import note as m21note
    from music21 import stream

    s = stream.Stream()
    for part in pitched_parts:
        path = job / "analysis" / "notes" / f"{part}.json"
        if not path.exists():
            continue
        for n in _read_json(path)["notes"]:
            s.append(m21note.Note(int(n["pitch"])))
    if not s.notes:
        return None
    try:
        return int(s.analyze("key").sharps)
    except Exception:
        return None


def _resolve_grid(rhythm: dict, opts: ScoreOptions):
    """Turn the cached rhythm into a render grid per the user's tempo options.

    Returns (bpm, time_sig, beats, beats_per_bar, downbeat_phase). "fixed" snaps
    to a metronomic grid (cleaner for studio takes); "variable" keeps the detected
    per-beat timing (faithful to live/rubato). Then applies the manual ½×/2×.
    """
    from .rhythm import apply_tempo_multiplier, to_fixed_grid

    bpm = rhythm["tempo"]
    time_sig = rhythm.get("time_signature", "4/4")
    beats = rhythm.get("beats") or []
    beats_per_bar = rhythm.get("beats_per_bar", 4)
    downbeat_phase = rhythm.get("downbeat_phase", 0)
    if opts.tempo_mode == "fixed":
        beats, downbeat_phase = to_fixed_grid(bpm, beats, downbeat_phase, beats_per_bar)
    bpm, beats = apply_tempo_multiplier(bpm, beats, opts.tempo_multiplier)
    if opts.tempo_multiplier != 1.0:
        downbeat_phase = 0  # beat indices shifted; realign bars to the new grid
    return bpm, time_sig, beats, beats_per_bar, downbeat_phase


def _resolve_key(job: Path, meta: dict, opts: ScoreOptions) -> int | None:
    """One song-wide key for every part. The user's pin wins (music21's key
    analysis is unreliable); otherwise auto-estimate from the pitched parts."""
    if opts.key_sharps_override is not None:
        return opts.key_sharps_override
    return _shared_key_sharps(job, meta.get("pitched_parts", []))


def cmd_export(
    job_dir: str,
    parts: list[str],
    formats: list[str],
    opts: ScoreOptions | None = None,
) -> None:
    from . import export as exporter

    opts = opts or ScoreOptions()
    job = Path(job_dir)
    meta = _read_json(job / "analysis" / "meta.json")
    bpm, time_sig, beats, beats_per_bar, downbeat_phase = _resolve_grid(meta["rhythm"], opts)
    key_sharps = _resolve_key(job, meta, opts)
    stems = meta["stems"]
    out_dir = job / "export"

    pitched, derived = _scoreable(meta)
    scoreable = set(pitched) | set(derived)

    artifacts: list[dict] = []
    needs_score = any(f in ("musicxml", "midi", "pdf") for f in formats)

    total = max(1, len(parts))
    for i, part in enumerate(parts):
        progress.progress("export", i / total * 100, f"Exporting {part}")

        score = None
        if needs_score and part in scoreable:
            score = _build_part_score(
                job, part, bpm=bpm, time_sig=time_sig, beats=beats,
                beats_per_bar=beats_per_bar, downbeat_phase=downbeat_phase,
                key_sharps=key_sharps, beat_offset=opts.beat_offset,
                octave_shift=opts.octave_shift,
            )

        for fmt in formats:
            # The click "part" exports a tempo-map MIDI instead of a score MIDI.
            if part == "click" and fmt == "midi":
                from . import audio

                downbeats = beats[downbeat_phase::beats_per_bar] if beats else []
                dst = out_dir / "click.mid"
                audio.write_tempo_midi(beats, downbeats, beats_per_bar, dst)
                artifacts.append({"part": part, "format": fmt, "path": str(dst)})
                continue
            if fmt in ("musicxml", "midi", "pdf") and score is None:
                # e.g. drums/other have no score in the MVP.
                artifacts.append({"part": part, "format": fmt, "skipped": "no score for this part"})
                continue
            art = exporter.export_part(part, fmt, out_dir, score=score, stem_wav=stems.get(part))
            # Bake the detected tempo map into every part MIDI so it lines up
            # with the song when imported into a DAW.
            if fmt == "midi" and art.get("path") and beats:
                from . import audio

                audio.apply_tempo_map_to_midi(art["path"], beats, beats_per_bar)
            artifacts.append(art)

    progress.progress("export", 100, "Export complete")
    progress.done(stage="export", job_dir=str(job), artifacts=artifacts)


def cmd_preview(job_dir: str, part: str, opts: ScoreOptions | None = None) -> None:
    """Build one part's score with the given options and return its MusicXML as a
    string (no files written) for live in-app preview."""
    from music21.musicxml.m21ToXml import GeneralObjectExporter

    opts = opts or ScoreOptions()
    job = Path(job_dir)
    meta = _read_json(job / "analysis" / "meta.json")
    pitched, derived = _scoreable(meta)
    if part not in set(pitched) | set(derived):
        progress.error(f"No score for part: {part}")
        return

    bpm, time_sig, beats, beats_per_bar, downbeat_phase = _resolve_grid(meta["rhythm"], opts)
    key_sharps = _resolve_key(job, meta, opts)
    score = _build_part_score(
        job, part, bpm=bpm, time_sig=time_sig, beats=beats,
        beats_per_bar=beats_per_bar, downbeat_phase=downbeat_phase,
        key_sharps=key_sharps, beat_offset=opts.beat_offset,
        octave_shift=opts.octave_shift,
    )
    xml = GeneralObjectExporter().parse(score).decode("utf-8")
    progress.done(stage="preview", part=part, musicxml=xml)


def cmd_mixdown(job_dir: str, gains: dict, dest: str) -> None:
    """Mix the stems at the given per-part gains (the mixer's solo/mute/volume
    state) into a single mp3 at `dest`."""
    import os
    import tempfile

    import numpy as np
    import soundfile as sf

    from . import audio

    job = Path(job_dir)
    meta = _read_json(job / "analysis" / "meta.json")
    stems = meta["stems"]

    progress.progress("mixdown", 0, "Mixing")
    active = [(p, float(g)) for p, g in gains.items() if g and float(g) > 0 and p in stems]

    mix = None
    sr = 44100
    for i, (part, g) in enumerate(active):
        y, sr = sf.read(stems[part], dtype="float32", always_2d=True)
        if mix is None:
            mix = np.zeros_like(y, dtype=np.float64)
        n = min(len(mix), len(y))
        mix[:n] += y[:n] * g
        progress.progress("mixdown", (i + 1) / max(1, len(active)) * 70, f"Mixing {part}")

    if mix is None:  # nothing audible — write a short silence
        mix = np.zeros((sr, 2), dtype=np.float64)
    peak = float(np.max(np.abs(mix)))
    if peak > 1.0:  # avoid clipping from summed stems
        mix = mix / peak

    fd, tmp = tempfile.mkstemp(suffix=".wav")  # mkstemp: no name-reservation race
    os.close(fd)  # soundfile writes by path; we only need the unique name
    sf.write(tmp, mix.astype("float32"), sr)
    progress.progress("mixdown", 85, "Encoding mp3")
    try:
        audio.encode(tmp, dest)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    progress.done(stage="mixdown", path=dest)


def cmd_serve() -> None:
    """Long-lived request loop. The Rust backend keeps one of these alive and
    sends one JSON request per line on stdin; each handler streams its usual
    progress/done/error JSON on stdout. Warming the heavy music21 import once
    here makes previews/exports near-instant (vs ~7s per fresh process)."""
    import importlib
    import os

    import music21  # noqa: F401  (warm the import)

    from . import audio, export, preprocess, rhythm, score, transcribe

    # Hot-reload our own (lightweight) modules when their source changes, so code
    # edits apply without restarting the app. Heavy libs (music21/torch/...) are
    # imported inside these modules' functions, so this stays cheap; when nothing
    # changed it's just a few stat() calls.
    reloadable = [audio, preprocess, rhythm, score, transcribe, export]
    mtimes = {m.__file__: os.path.getmtime(m.__file__) for m in reloadable}

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        for mod in reloadable:
            try:
                mt = os.path.getmtime(mod.__file__)
                if mt != mtimes.get(mod.__file__):
                    mtimes[mod.__file__] = mt
                    importlib.reload(mod)
            except Exception:
                pass
        cmd = req.get("cmd")
        try:
            if cmd == "analyze":
                cmd_analyze(req["input"], req["job_dir"], req.get("transcribe_parts"))
            elif cmd == "export":
                cmd_export(
                    req["job_dir"], req["parts"], req["formats"],
                    ScoreOptions.from_request(req),
                )
            elif cmd == "preview":
                cmd_preview(req["job_dir"], req["part"], ScoreOptions.from_request(req))
            elif cmd == "mixdown":
                cmd_mixdown(req["job_dir"], req["gains"], req["dest"])
            else:
                progress.error(f"unknown command: {cmd}")
        except Exception as exc:
            progress.log(traceback.format_exc())
            progress.error(str(exc))


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="paperecho")
    sub = parser.add_subparsers(dest="command", required=True)

    p_an = sub.add_parser("analyze")
    p_an.add_argument("--input", required=True)
    p_an.add_argument("--job-dir", required=True)
    p_an.add_argument("--transcribe-parts", type=_csv, default=None,
                      help="comma-separated pitched parts to notate (default: all)")

    p_ex = sub.add_parser("export")
    p_ex.add_argument("--job-dir", required=True)
    p_ex.add_argument("--parts", required=True, type=_csv)
    p_ex.add_argument("--formats", required=True, type=_csv)
    p_ex.add_argument("--tempo-mult", type=float, default=1.0)
    p_ex.add_argument("--beat-offset", type=float, default=0.0)
    p_ex.add_argument("--key-sharps", type=int, default=None,
                      help="pin the key signature (sharps +, flats -); omit to auto-detect")
    p_ex.add_argument("--tempo-mode", choices=("fixed", "variable"), default="fixed",
                      help="fixed: metronomic grid (default); variable: keep detected timing")
    p_ex.add_argument("--octave-shift", type=int, default=0,
                      help="shift the written notes by N octaves (pitch is kept; reading aid)")

    p_pv = sub.add_parser("preview")
    p_pv.add_argument("--job-dir", required=True)
    p_pv.add_argument("--part", required=True)
    p_pv.add_argument("--tempo-mult", type=float, default=1.0)
    p_pv.add_argument("--beat-offset", type=float, default=0.0)
    p_pv.add_argument("--key-sharps", type=int, default=None,
                      help="pin the key signature (sharps +, flats -); omit to auto-detect")
    p_pv.add_argument("--tempo-mode", choices=("fixed", "variable"), default="fixed",
                      help="fixed: metronomic grid (default); variable: keep detected timing")
    p_pv.add_argument("--octave-shift", type=int, default=0,
                      help="shift the written notes by N octaves (pitch is kept; reading aid)")

    p_mx = sub.add_parser("mixdown")
    p_mx.add_argument("--job-dir", required=True)
    p_mx.add_argument("--gains", required=True, type=json.loads,
                      help='per-part gains as JSON, e.g. \'{"bass":1.0,"vocals":0.5}\'')
    p_mx.add_argument("--dest", required=True, help="output mp3 path")

    sub.add_parser("serve")  # long-lived request loop driven by the Rust backend

    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            cmd_serve()
        elif args.command == "analyze":
            cmd_analyze(args.input, args.job_dir, args.transcribe_parts)
        elif args.command == "export":
            cmd_export(
                args.job_dir, args.parts, args.formats, ScoreOptions.from_args(args),
            )
        elif args.command == "preview":
            cmd_preview(args.job_dir, args.part, ScoreOptions.from_args(args))
        elif args.command == "mixdown":
            cmd_mixdown(args.job_dir, args.gains, args.dest)
    except Exception as exc:  # surface a clean error to the backend
        progress.log(traceback.format_exc())
        progress.error(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
