"""Audio decoding/encoding helpers built on ffmpeg."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Input formats accepted from the UI (see product spec MVP > 入力).
SUPPORTED_INPUT = {".mp3", ".wav", ".m4a", ".aiff", ".aif"}


def ffmpeg_bin() -> str:
    """Resolve ffmpeg: the bundled binary (PAPER_ECHO_FFMPEG, set by the Rust app
    in a packaged build) wins, else whatever's on PATH (dev / CLI)."""
    bundled = os.environ.get("PAPER_ECHO_FFMPEG")
    if bundled and Path(bundled).exists():
        return bundled
    return shutil.which("ffmpeg") or "ffmpeg"


def _run(cmd: list[str]) -> None:
    # errors="replace": ffmpeg echoes ID3 tags (often Shift-JIS in JP mp3s) on
    # stderr, which would otherwise crash the UTF-8 decode.
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({' '.join(cmd[:2])} ...):\n{proc.stderr[-2000:]}")


def decode_to_wav(src: str | Path, dst: str | Path, sr: int = 44100) -> Path:
    """Normalise any supported input to stereo 16-bit PCM wav at `sr`.

    Stereo is kept because Demucs separates better from a stereo field.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg_bin(), "-y", "-i", str(src),
        "-ac", "2", "-ar", str(sr), "-c:a", "pcm_s16le",
        str(dst),
    ])
    return dst


def write_click_track(
    beats: list[float],
    downbeats: list[float],
    duration_s: float,
    dst: str | Path,
    sr: int = 44100,
) -> Path:
    """Render a metronome click wav: a click at each beat, a higher accent on
    downbeats. Lets the user hear/verify the detected beat grid."""
    import numpy as np
    import soundfile as sf

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    n = max(int(duration_s * sr), sr)
    track = np.zeros(n, dtype=np.float32)

    def click(freq: float, amp: float, length: float = 0.045) -> np.ndarray:
        t = np.arange(int(sr * length)) / sr
        return (amp * np.sin(2 * np.pi * freq * t) * np.exp(-t * 45)).astype(np.float32)

    accent = click(1600.0, 0.7)
    plain = click(1000.0, 0.45)
    db = sorted(downbeats)

    for b in beats:
        c = accent if any(abs(b - d) < 0.03 for d in db) else plain
        i = int(b * sr)
        if i >= n:
            continue
        end = min(n, i + len(c))
        track[i:end] += c[: end - i]

    sf.write(str(dst), np.stack([track, track], axis=1), sr, subtype="PCM_16")
    return dst


def _build_tempo_track(beats: list[float], beats_per_bar: int, ticks_per_beat: int):
    """A conductor MidiTrack: time signature + a `set_tempo` at every beat (one
    beat == one quarter == `ticks_per_beat` ticks), so a DAW's grid follows the
    song including tempo drift."""
    import mido

    track = mido.MidiTrack()
    track.append(
        mido.MetaMessage("time_signature", numerator=max(1, beats_per_bar),
                         denominator=4, time=0)
    )
    n = len(beats)
    last = 0
    for i in range(n):
        ibi = beats[i + 1] - beats[i] if i < n - 1 else (
            beats[i] - beats[i - 1] if i > 0 else 0.5
        )
        tempo = mido.bpm2tempo(60.0 / max(ibi, 1e-3))
        tick = i * ticks_per_beat
        track.append(mido.MetaMessage("set_tempo", tempo=tempo, time=tick - last))
        last = tick
    return track


def write_tempo_midi(
    beats: list[float],
    downbeats: list[float],
    beats_per_bar: int,
    dst: str | Path,
    ticks_per_beat: int = 480,
) -> Path:
    """Tempo-map MIDI: the conductor track plus audible woodblock click notes
    (downbeats accented)."""
    import mido

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    mid.tracks.append(_build_tempo_track(beats, beats_per_bar, ticks_per_beat))

    click_track = mido.MidiTrack()
    mid.tracks.append(click_track)
    db = sorted(downbeats)
    last = 0
    dur = ticks_per_beat // 4
    for i, b in enumerate(beats):
        accent = any(abs(b - d) < 0.03 for d in db)
        note = 76 if accent else 77  # GM hi/lo woodblock
        on = i * ticks_per_beat
        click_track.append(
            mido.Message("note_on", channel=9, note=note,
                         velocity=110 if accent else 75, time=on - last)
        )
        click_track.append(
            mido.Message("note_off", channel=9, note=note, velocity=0, time=dur)
        )
        last = on + dur

    mid.save(str(dst))
    return dst


def apply_tempo_map_to_midi(
    midi_path: str | Path, beats: list[float], beats_per_bar: int
) -> None:
    """Rewrite a part MIDI's tempo with the detected beat map (replacing the
    score's single constant tempo), so it lines up with the song in a DAW. The
    notes are already on the beat grid, so this just remaps ticks→time."""
    import mido

    midi_path = str(midi_path)
    mid = mido.MidiFile(midi_path)
    tpb = mid.ticks_per_beat

    rebuilt = [_build_tempo_track(beats, beats_per_bar, tpb)]
    for track in mid.tracks:
        abs_time = 0
        kept = []
        for msg in track:  # strip existing tempo/time-sig, keep absolute times
            abs_time += msg.time
            if msg.type in ("set_tempo", "time_signature"):
                continue
            kept.append((abs_time, msg))
        new_track = mido.MidiTrack()
        prev = 0
        for at, msg in kept:
            new_track.append(msg.copy(time=at - prev))
            prev = at
        rebuilt.append(new_track)

    mid.tracks = rebuilt
    mid.save(midi_path)


def encode(src: str | Path, dst: str | Path) -> Path:
    """Transcode a stem wav to the format implied by `dst`'s extension."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.suffix.lower() == ".mp3":
        cmd = [ffmpeg_bin(), "-y", "-i", str(src), "-b:a", "192k", str(dst)]
    else:  # wav and anything else ffmpeg infers from extension
        cmd = [ffmpeg_bin(), "-y", "-i", str(src), str(dst)]
    _run(cmd)
    return dst


def write_preview(src: str | Path, dst: str | Path, bitrate: str = "96k") -> Path:
    """Encode a small mono/22 kHz preview of a stem for in-app playback.

    The full 44.1 kHz stereo stems are far too large to decode into the webview
    together — a 7-minute song is ~1 GB of decoded audio across 7 stems, which
    makes Web Audio fail silently. The player decodes stems into buffers (so all
    parts can start sample-accurately and not garble the summed mix), so the
    previews are **mono and 22.05 kHz** to keep each decoded buffer ~4× smaller.
    AAC `.m4a` is used for native WKWebView decode support."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg_bin(), "-y", "-i", str(src),
        "-ac", "1", "-ar", "22050", "-c:a", "aac", "-b:a", bitrate,
        str(dst),
    ])
    return dst
