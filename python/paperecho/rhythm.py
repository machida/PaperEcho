"""Tempo / beat / meter estimation, shared by every part as the score grid.

Primary: beat_this (2024, transformer) gives real beats AND downbeats, which
fixes the bar/downbeat alignment librosa couldn't. librosa is the fallback.
"""

from __future__ import annotations

from pathlib import Path

_F2B = None


def _file2beats():
    """Lazily build & cache the beat_this model (so the serve process reuses it)."""
    global _F2B
    if _F2B is None:
        from beat_this.inference import File2Beats

        _F2B = File2Beats(device="cpu")
    return _F2B


def estimate_rhythm(src_wav: str | Path) -> dict:
    try:
        return _rhythm_via_beat_this(src_wav)
    except Exception:
        return _rhythm_via_librosa(src_wav)


def _rhythm_via_beat_this(src_wav: str | Path) -> dict:
    import numpy as np

    beats_arr, downbeats_arr = _file2beats()(str(src_wav))
    beats = [float(b) for b in beats_arr]
    downbeats = [float(d) for d in downbeats_arr]
    if len(beats) < 4:
        raise ValueError("beat_this returned too few beats")

    # beat_this sometimes locks onto double-time for a busier section (e.g. a
    # 30 s stretch at 158 BPM inside a 77 BPM song), which renders the click and
    # the score grid at 2× there. Drop those inserted sub-beats back to the
    # song's base pulse before deriving tempo/meter.
    beats = _fix_octave_jumps(beats)

    bpm = float(60.0 / np.median(np.diff(beats)))

    # downbeat_phase = index of the first beat that coincides with a downbeat.
    downbeat_phase = 0
    for i, b in enumerate(beats):
        if any(abs(b - d) < 0.05 for d in downbeats[:4]):
            downbeat_phase = i
            break

    # beats_per_bar = typical number of beats between consecutive downbeats.
    beats_per_bar = 4
    if len(downbeats) >= 2:
        counts = [
            sum(1 for b in beats if downbeats[k] - 0.02 <= b < downbeats[k + 1] - 0.02)
            for k in range(len(downbeats) - 1)
        ]
        counts = [c for c in counts if c > 0]
        if counts:
            m = int(round(float(np.median(counts))))
            beats_per_bar = m if m in (2, 3, 4, 6) else 4

    return {
        "tempo": round(bpm, 2),
        "beats": [round(b, 4) for b in beats],
        "beats_per_bar": beats_per_bar,
        "downbeat_phase": downbeat_phase,
        "time_signature": f"{beats_per_bar}/4",
    }


def _rhythm_via_librosa(src_wav: str | Path) -> dict:
    import librosa
    import numpy as np

    y, sr = librosa.load(str(src_wav), mono=True)
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")

    # librosa may return tempo as a 0-d/1-d array depending on version.
    bpm = float(np.atleast_1d(tempo)[0])
    if not bpm or bpm <= 0:
        bpm = 120.0

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    beat_times = _fix_octave_jumps([float(b) for b in beats])
    # Note: tempo octave (e.g. 80 vs 160 BPM) is genuinely ambiguous and song
    # dependent, so we trust librosa's detection and let the user pick ½×/2× in
    # the UI rather than auto-correcting (which guessed wrong on real songs).
    bpm, beat_times = _normalize_tempo(bpm, beat_times)
    beats_per_bar, downbeat_phase = _estimate_meter(onset_env, sr, beat_times)

    return {
        "tempo": round(bpm, 2),
        "beats": [round(b, 4) for b in beat_times],
        "beats_per_bar": beats_per_bar,
        # Index (in beats) of the first downbeat within the first bar.
        "downbeat_phase": downbeat_phase,
        "time_signature": f"{beats_per_bar}/4",
    }


def apply_tempo_multiplier(
    bpm: float, beats: list[float], multiplier: float
) -> tuple[float, list[float]]:
    """Re-grid for a user-chosen ½×/2× tempo. 2× subdivides the beat grid
    (insert midpoints); ½× keeps every other beat. One beat stays one quarter."""
    beats = list(beats)
    if multiplier >= 2 and len(beats) >= 2:
        doubled: list[float] = []
        for i in range(len(beats) - 1):
            doubled.append(beats[i])
            doubled.append((beats[i] + beats[i + 1]) / 2.0)
        doubled.append(beats[-1])
        return bpm * 2.0, doubled
    if 0 < multiplier <= 0.5 and len(beats) >= 2:
        return bpm / 2.0, beats[::2]
    return bpm, beats


def to_fixed_grid(
    bpm: float, beats: list[float], downbeat_phase: int, beats_per_bar: int
) -> tuple[list[float], int]:
    """Replace a variable beat track with a metronomic grid at `bpm`, phase-aligned
    to the first downbeat.

    For studio recordings (near-constant tempo) this yields cleaner notation and a
    steadier feel than baking the tracker's per-beat jitter into the score; live
    recordings keep the variable track instead. Returns the rebuilt beats and the
    downbeat phase index within that grid. Spans the same time range as the input.
    """
    import math

    if not beats or bpm <= 0:
        return list(beats), downbeat_phase
    interval = 60.0 / bpm
    anchor = beats[downbeat_phase] if 0 <= downbeat_phase < len(beats) else beats[0]
    start, end = beats[0], beats[-1]
    grid: list[float] = []
    k = math.floor((start - anchor) / interval)
    while True:
        t = anchor + k * interval
        if t > end + interval * 0.5:
            break
        if t >= start - interval * 0.5:
            grid.append(round(t, 4))
        k += 1
    if not grid:
        return list(beats), downbeat_phase
    anchor_idx = round((anchor - grid[0]) / interval)
    return grid, anchor_idx % max(1, beats_per_bar)


def _fix_octave_jumps(beats: list[float]) -> list[float]:
    """Remove local double-time runs from a beat track.

    Beat trackers sometimes lock onto 2× the pulse for a busy section, inserting
    a beat between every real beat there (IOI ~halves). Using the song's global
    median IOI as the true pulse, we keep a beat only when it is at least 3/4 of
    that pulse past the previously kept beat — which drops the spurious in-between
    beats while leaving steady sections (and genuine gaps) untouched. Purely
    subtractive: we never invent beats, so a real tempo *drop* is preserved.

    Assumes the doubled stretch is a minority of the song (so the median still
    reflects the base pulse). If most of the song were double-time the median
    would itself be doubled and nothing is dropped — the safe no-op."""
    import numpy as np

    if len(beats) < 4:
        return beats
    beats = sorted(float(b) for b in beats)
    med = float(np.median(np.diff(beats)))
    if med <= 0:
        return beats
    min_gap = med * 0.75
    kept = [beats[0]]
    for b in beats[1:]:
        if b - kept[-1] >= min_gap:
            kept.append(b)
    return kept


def _normalize_tempo(bpm: float, beats: list[float]) -> tuple[float, list[float]]:
    """Fold detections into a typical felt-tempo range [70, 160) BPM, resampling
    the beat grid so one beat still equals one quarter note. This is the default
    octave guess; the user overrides with ½×/2× in the UI when it's wrong."""
    beats = list(beats)
    for _ in range(4):  # bounded; one fold per iteration
        if bpm >= 160 and len(beats) >= 2:
            bpm /= 2.0
            beats = beats[::2]  # keep every other beat
        elif bpm < 70 and len(beats) >= 2:
            bpm *= 2.0
            doubled: list[float] = []
            for i in range(len(beats) - 1):
                doubled.append(beats[i])
                doubled.append((beats[i] + beats[i + 1]) / 2.0)  # subdivide
            doubled.append(beats[-1])
            beats = doubled
        else:
            break
    return bpm, beats


def _estimate_meter(onset_env, sr, beat_times: list[float]) -> tuple[int, int]:
    """Guess (beats_per_bar, downbeat_phase) from accent periodicity.

    Beats carrying the strongest onsets tend to be downbeats. For each candidate
    bar length we find the beat phase with the strongest mean accent and how much
    it stands out; 3/4 only wins if its accent contrast clearly beats 4/4.
    """
    import numpy as np
    import librosa

    if len(beat_times) < 6:
        return 4, 0

    frames = np.clip(librosa.time_to_frames(beat_times, sr=sr), 0, len(onset_env) - 1)
    strength = onset_env[frames]

    def best_phase(bpb: int) -> tuple[int, float]:
        means = [
            strength[p::bpb].mean() if strength[p::bpb].size else 0.0
            for p in range(bpb)
        ]
        phase = int(np.argmax(means))
        contrast = max(means) / (float(np.mean(means)) + 1e-9)
        return phase, contrast

    phase4, c4 = best_phase(4)
    phase3, c3 = best_phase(3)
    if c3 > c4 * 1.15:
        return 3, phase3
    return 4, phase4
