"""Unit tests for onset-driven bass transcription.

The contract that matters: a string of same-pitch repeated notes must stay
*separate* notes, not collapse into one sustain (the failure mode of every
pitch-led tracker). Pitch accuracy is secondary to boundary accuracy here.
"""

import librosa
import numpy as np
import soundfile as sf

from paperecho.transcribe import (
    _piano_events_to_notes,
    transcribe_bass_onset,
    transcribe_onset,
    transcribe_piano,
)

_SR = 16000


def _render(pitches, note_s=0.30, gap_s=0.06):
    """Synthesise a plucky monophonic bass line (sharp attack, decaying tail)."""
    chunks = []
    for mp in pitches:
        f = float(librosa.midi_to_hz(mp))
        t = np.arange(int(note_s * _SR)) / _SR
        env = np.exp(-t * 6)  # plucky decay -> a clear attack per note
        tone = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t)
        chunks.append((tone * env).astype(np.float32))
        chunks.append(np.zeros(int(gap_s * _SR), dtype=np.float32))
    return np.concatenate(chunks)


def test_repeated_same_pitch_notes_stay_separate(tmp_path):
    path = tmp_path / "bass.wav"
    sf.write(str(path), _render([40] * 8), _SR)  # E2 x8

    notes = transcribe_bass_onset(path)

    # The whole point: NOT merged into one (or two) long sustains.
    assert len(notes) >= 6
    # Every note is the same pitch we played (within a semitone of E2).
    assert all(abs(n["pitch"] - 40) <= 1 for n in notes)
    # Notes are ordered and non-overlapping (monophonic line).
    for a, b in zip(notes, notes[1:], strict=False):
        assert a["end"] <= b["start"] + 1e-6


def test_silence_produces_no_notes(tmp_path):
    path = tmp_path / "silence.wav"
    sf.write(str(path), np.zeros(_SR, dtype=np.float32), _SR)
    assert transcribe_bass_onset(path) == []


def test_crepe_labels_distinct_pitches(tmp_path):
    # A moving line: CREPE (the neural f0 model) should label each onset span with
    # the correct pitch, in order, not just keep boundaries.
    line = [40, 43, 45, 47]  # E2 G2 A2 B2
    path = tmp_path / "bass.wav"
    sf.write(str(path), _render(line, note_s=0.35), _SR)

    notes = transcribe_bass_onset(path)
    pitches = [n["pitch"] for n in notes]

    # Each played pitch appears, in ascending order of first occurrence.
    first_seen = {}
    for p in pitches:
        first_seen.setdefault(p, len(first_seen))
    for expected in line:
        assert any(abs(p - expected) <= 1 for p in pitches), f"missing {expected}"
    assert pitches == sorted(pitches), "pitches should rise with the line"


def test_vocals_use_onset_path_in_vocal_range(tmp_path):
    # Vocals route through the same onset-driven CREPE tracker, with a wider f0
    # band: a sung A3-C4-E4 line should come back at the right (higher) pitches.
    line = [57, 60, 64]  # A3 C4 E4
    path = tmp_path / "vocals.wav"
    sf.write(str(path), _render(line, note_s=0.40), _SR)

    notes = transcribe_onset(path, "vocals")
    pitches = [n["pitch"] for n in notes]

    assert notes, "vocals onset path produced no notes"
    for expected in line:
        assert any(abs(p - expected) <= 1 for p in pitches), f"missing {expected}"


def test_piano_events_to_notes_keeps_simultaneous_chord_notes():
    # The ByteDance piano model emits one event per note; a chord = several events
    # sharing an onset. Our converter must keep them all (polyphony), drop
    # zero-length events, and normalise the field names.
    events = [
        {"midi_note": 60, "onset_time": 1.0, "offset_time": 2.0, "velocity": 90},
        {"midi_note": 64, "onset_time": 1.0, "offset_time": 2.0, "velocity": 80},
        {"midi_note": 67, "onset_time": 1.0, "offset_time": 2.0, "velocity": 70},
        {"midi_note": 72, "onset_time": 3.0, "offset_time": 3.0, "velocity": 50},  # zero-length
    ]
    notes = _piano_events_to_notes(events)

    assert [n["pitch"] for n in notes] == [60, 64, 67]  # C-E-G chord kept, sorted
    assert all(n["start"] == 1.0 and n["end"] == 2.0 for n in notes)
    assert all(1 <= n["velocity"] <= 127 for n in notes)


def test_piano_events_drop_sub_grid_blips():
    # On a separated stem the model emits a tail of ultra-short, quiet notes that
    # score.py would promote to printed 16ths (clutter). Notes shorter than
    # _PIANO_MIN_DURATION (50 ms) are dropped; real notes at/above it are kept.
    events = [
        {"midi_note": 60, "onset_time": 0.0, "offset_time": 0.6, "velocity": 80},  # real
        {"midi_note": 62, "onset_time": 1.0, "offset_time": 1.02, "velocity": 50},  # 20ms blip
        {"midi_note": 64, "onset_time": 2.0, "offset_time": 2.08, "velocity": 60},  # 80ms -> kept
        {"midi_note": 65, "onset_time": 3.0, "offset_time": 3.04, "velocity": 55},  # 40ms blip
    ]
    notes = _piano_events_to_notes(events)
    assert [n["pitch"] for n in notes] == [60, 64]


def test_silent_piano_stem_skips_model(tmp_path):
    # A song with no real piano: the stem is ~silent after gating, so the slow
    # ByteDance model must be skipped (return [] fast, not run for minutes).
    import time

    path = tmp_path / "piano.wav"
    sf.write(str(path), np.zeros(_SR * 5, dtype=np.float32), _SR)
    t0 = time.time()
    notes = transcribe_piano(path)
    assert notes == []
    assert time.time() - t0 < 5  # nowhere near a real model run (minutes)


def test_first_note_is_not_dropped(tmp_path):
    # onset_detect misses the very first attack; we seed a boundary from the
    # first sounding frame, so the opening note must survive.
    path = tmp_path / "bass.wav"
    sf.write(str(path), _render([45, 45, 45]), _SR)
    notes = transcribe_bass_onset(path)
    assert notes and notes[0]["start"] < 0.15
