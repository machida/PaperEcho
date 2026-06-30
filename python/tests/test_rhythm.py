"""Unit tests for tempo normalization (safety-net folding of extremes)."""

from paperecho.rhythm import (
    _fix_octave_jumps,
    _normalize_tempo,
    apply_tempo_multiplier,
    to_fixed_grid,
)


def test_tempo_multiplier_double():
    bpm, out = apply_tempo_multiplier(80.0, [0.0, 0.5, 1.0, 1.5], 2)
    assert abs(bpm - 160.0) < 1e-6
    assert len(out) == 7  # midpoints inserted
    assert abs(out[1] - 0.25) < 1e-6


def test_tempo_multiplier_half():
    bpm, out = apply_tempo_multiplier(160.0, [0.0, 0.25, 0.5, 0.75, 1.0], 0.5)
    assert abs(bpm - 80.0) < 1e-6
    assert out == [0.0, 0.5, 1.0]


def test_tempo_multiplier_identity():
    beats = [0.0, 0.5, 1.0]
    bpm, out = apply_tempo_multiplier(120.0, beats, 1)
    assert abs(bpm - 120.0) < 1e-6
    assert out == beats


def test_halves_extreme_fast():
    beats = [i * (60 / 220) for i in range(9)]  # 220 BPM grid
    bpm, out = _normalize_tempo(220.0, beats)
    assert abs(bpm - 110.0) < 1e-6
    assert len(out) == 5  # every other beat kept


def test_doubles_extreme_slow():
    beats = [0.0, 1.2, 2.4, 3.6]  # 50 BPM grid
    bpm, out = _normalize_tempo(50.0, beats)
    assert abs(bpm - 100.0) < 1e-6
    assert len(out) == 7  # midpoints inserted


def test_leaves_in_range_tempo_untouched():
    # 70..160 is the kept range; users override the octave with ½×/2× in the UI.
    for t in (90.0, 120.0, 140.0):
        bpm, out = _normalize_tempo(t, [0.0, 0.5, 1.0, 1.5])
        assert abs(bpm - t) < 1e-6
        assert out == [0.0, 0.5, 1.0, 1.5]


def test_octave_fix_drops_local_double_time():
    # 0.8 s pulse, but a middle section locks to double-time (0.4 s). The fix
    # restores the section to the base pulse without touching the steady parts.
    beats = [round(i * 0.8, 3) for i in range(10)]  # 0.0 .. 7.2
    base_end = beats[-1]
    beats += [round(base_end + 0.4 * k, 3) for k in range(1, 13)]  # double-time run
    tail_start = beats[-1]
    beats += [round(tail_start + 0.8 * k, 3) for k in range(1, 6)]  # back to base

    out = _fix_octave_jumps(beats)
    iois = [round(b - a, 3) for a, b in zip(out, out[1:], strict=False)]
    # No interval is anywhere near the half-pulse anymore.
    assert min(iois) >= 0.6
    assert max(iois) <= 1.0
    # Subtractive only: never invents beats.
    assert len(out) < len(beats)


def test_octave_fix_leaves_steady_track_untouched():
    beats = [round(i * 0.5, 3) for i in range(16)]  # clean 120 BPM
    assert _fix_octave_jumps(beats) == beats


def test_octave_fix_preserves_genuine_gap():
    # A real half-time drop (a longer gap) must NOT be filled — purely subtractive.
    beats = [0.0, 0.8, 1.6, 2.4, 4.0, 4.8, 5.6]  # gap 2.4->4.0
    assert _fix_octave_jumps(beats) == beats


def test_fixed_grid_is_uniform_at_tempo():
    # Jittery 120 BPM beats (0.5 s nominal) -> a perfectly even 0.5 s grid.
    beats = [0.0, 0.52, 0.98, 1.51, 2.03, 2.48, 3.01]
    grid, phase = to_fixed_grid(120.0, beats, downbeat_phase=0, beats_per_bar=4)
    iois = [round(b - a, 6) for a, b in zip(grid, grid[1:], strict=False)]
    assert iois and all(abs(i - 0.5) < 1e-6 for i in iois)
    assert phase == 0  # first beat was the downbeat


def test_fixed_grid_aligns_phase_to_downbeat():
    # Downbeat is the 2nd beat (index 1); the grid's phase must point at it.
    beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
    grid, phase = to_fixed_grid(120.0, beats, downbeat_phase=1, beats_per_bar=4)
    assert grid[phase] == beats[1]


def test_fixed_grid_empty_is_safe():
    assert to_fixed_grid(120.0, [], 0, 4) == ([], 0)
