"""Unit tests for the CLI orchestration in pipeline.py.

These cover the branching logic that reshapes cached notes/rhythm at export &
preview time — distinct from the transcription/scoring purity tests elsewhere.
"""

import argparse
import json
from pathlib import Path

from music21 import clef as m21clef

from paperecho.pipeline import (
    ScoreOptions,
    _build_part_score,
    _resolve_grid,
    _resolve_key,
    _scoreable,
)

# --- ScoreOptions builders ------------------------------------------------------

def test_score_options_from_request_maps_keys():
    opts = ScoreOptions.from_request({
        "tempo_mult": 2.0,
        "beat_offset": 1.5,
        "key_sharps": 3,
        "tempo_mode": "variable",
        "octave_shift": -1,
    })
    assert opts.tempo_multiplier == 2.0
    assert opts.beat_offset == 1.5
    assert opts.key_sharps_override == 3
    assert opts.tempo_mode == "variable"
    assert opts.octave_shift == -1


def test_score_options_from_request_defaults():
    opts = ScoreOptions.from_request({})
    assert opts.tempo_multiplier == 1.0
    assert opts.beat_offset == 0.0
    assert opts.key_sharps_override is None
    assert opts.tempo_mode == "fixed"
    assert opts.octave_shift == 0


def test_score_options_from_args():
    ns = argparse.Namespace(
        tempo_mult=0.5, beat_offset=-2.0, key_sharps=None,
        tempo_mode="fixed", octave_shift=2,
    )
    opts = ScoreOptions.from_args(ns)
    assert opts.tempo_multiplier == 0.5
    assert opts.beat_offset == -2.0
    assert opts.key_sharps_override is None
    assert opts.octave_shift == 2


# --- _resolve_grid --------------------------------------------------------------

def _rhythm(beats, *, tempo=120.0, bpb=4, phase=0):
    return {
        "tempo": tempo,
        "beats": beats,
        "beats_per_bar": bpb,
        "downbeat_phase": phase,
        "time_signature": f"{bpb}/4",
    }


def test_resolve_grid_fixed_preserves_phase_at_unit_tempo():
    # At tempo_mult == 1.0 the downbeat phase must survive (no realignment).
    beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
    rhythm = _rhythm(beats, tempo=120.0, bpb=4, phase=2)
    bpm, ts, out_beats, bpb, phase = _resolve_grid(rhythm, ScoreOptions())
    assert bpm == 120.0
    assert bpb == 4
    assert phase == 2  # unchanged
    assert ts == "4/4"


def test_resolve_grid_resets_phase_on_tempo_multiplier():
    # When the user applies ½×/2×, beat indices shift, so the bar phase must
    # reset to 0 (this is the subtle side effect worth pinning).
    beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
    rhythm = _rhythm(beats, tempo=120.0, bpb=4, phase=3)
    _bpm, _ts, _beats, _bpb, phase = _resolve_grid(
        rhythm, ScoreOptions(tempo_multiplier=2.0)
    )
    assert phase == 0


def test_resolve_grid_variable_keeps_detected_beats():
    # "variable" mode must NOT snap to a metronomic grid — the (jittery) detected
    # beats pass through unchanged at unit tempo.
    beats = [0.0, 0.52, 0.98, 1.55, 2.01]
    rhythm = _rhythm(beats, tempo=115.0)
    _bpm, _ts, out_beats, _bpb, _phase = _resolve_grid(
        rhythm, ScoreOptions(tempo_mode="variable")
    )
    assert out_beats == beats


def test_resolve_grid_fixed_is_uniform():
    # "fixed" mode rebuilds an evenly-spaced grid at the detected tempo.
    beats = [0.0, 0.48, 1.05, 1.49, 2.02]
    rhythm = _rhythm(beats, tempo=120.0)  # 120 BPM -> 0.5 s spacing
    _bpm, _ts, out_beats, _bpb, _phase = _resolve_grid(rhythm, ScoreOptions())
    diffs = [round(b - a, 4) for a, b in zip(out_beats, out_beats[1:], strict=False)]
    assert diffs and all(abs(d - 0.5) < 1e-6 for d in diffs)


# --- _scoreable -----------------------------------------------------------------

def test_scoreable_derives_bass_treble_when_bass_present():
    pitched, derived = _scoreable({"pitched_parts": ["bass", "vocals"]})
    assert pitched == ["bass", "vocals"]
    assert "bass_treble" in derived


def test_scoreable_no_derived_without_source():
    pitched, derived = _scoreable({"pitched_parts": ["vocals", "piano"]})
    assert derived == []  # bass_treble needs bass


# --- _resolve_key ---------------------------------------------------------------

def _write_notes(job: Path, part: str, pitches):
    notes = [
        {"start": i * 0.5, "end": i * 0.5 + 0.4, "pitch": p, "velocity": 80}
        for i, p in enumerate(pitches)
    ]
    path = job / "analysis" / "notes" / f"{part}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"part": part, "notes": notes}), encoding="utf-8")


def test_resolve_key_override_wins(tmp_path):
    meta = {"pitched_parts": ["bass"]}
    _write_notes(tmp_path, "bass", [60, 62, 64])
    sharps = _resolve_key(tmp_path, meta, ScoreOptions(key_sharps_override=-3))
    assert sharps == -3  # the user's pin beats any auto-estimate


def test_resolve_key_auto_estimates_from_notes(tmp_path):
    meta = {"pitched_parts": ["bass"]}
    _write_notes(tmp_path, "bass", [60, 62, 64, 65, 67, 69, 71, 72])  # C major scale
    sharps = _resolve_key(tmp_path, meta, ScoreOptions())
    assert isinstance(sharps, int)  # music21 returned a key signature


def test_resolve_key_none_when_no_notes(tmp_path):
    meta = {"pitched_parts": ["bass"]}
    assert _resolve_key(tmp_path, meta, ScoreOptions()) is None


# --- _build_part_score ----------------------------------------------------------

def _midi_pitches(score):
    return sorted(n.pitch.midi for n in score.recurse().notes if n.isNote)


def test_build_part_score_missing_notes_returns_none(tmp_path):
    score = _build_part_score(
        tmp_path, "bass", bpm=120, time_sig="4/4", beats=None,
        beats_per_bar=4, downbeat_phase=0, key_sharps=0, beat_offset=0.0,
    )
    assert score is None


def test_build_part_score_derived_bass_treble_transposes_up_two_octaves(tmp_path):
    # bass_treble reuses bass notes, lifted +24 semitones into a treble read.
    _write_notes(tmp_path, "bass", [40])  # E1
    score = _build_part_score(
        tmp_path, "bass_treble", bpm=120, time_sig="4/4", beats=None,
        beats_per_bar=4, downbeat_phase=0, key_sharps=0, beat_offset=0.0,
    )
    assert _midi_pitches(score) == [64]  # 40 + 24
    clefs = list(score.recurse().getElementsByClass(m21clef.Clef))
    assert any(isinstance(c, m21clef.TrebleClef) for c in clefs)


def test_build_part_score_octave_shift_stacks_on_derived_transpose(tmp_path):
    # octave_shift stacks on top of the derived +24 (so +24 + 12 = +36).
    _write_notes(tmp_path, "bass", [40])
    score = _build_part_score(
        tmp_path, "bass_treble", bpm=120, time_sig="4/4", beats=None,
        beats_per_bar=4, downbeat_phase=0, key_sharps=0, beat_offset=0.0,
        octave_shift=1,
    )
    assert _midi_pitches(score) == [76]  # 40 + 24 + 12


def test_build_part_score_guitar_uses_octave_treble_clef(tmp_path):
    _write_notes(tmp_path, "guitar", [55, 59, 62])  # concert pitch kept
    score = _build_part_score(
        tmp_path, "guitar", bpm=120, time_sig="4/4", beats=None,
        beats_per_bar=4, downbeat_phase=0, key_sharps=0, beat_offset=0.0,
    )
    clefs = list(score.recurse().getElementsByClass(m21clef.Clef))
    assert any(isinstance(c, m21clef.Treble8vbClef) for c in clefs)
    # Concert pitch is unchanged (the 8vb clef only changes how it's read).
    assert _midi_pitches(score) == [55, 59, 62]
