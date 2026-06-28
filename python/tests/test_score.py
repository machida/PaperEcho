"""Unit tests for the beat-aware quantiser and monophonic cleanup."""

from paperecho.score import (
    _grid_origin,
    _make_time_to_quarter,
    _merge_split_notes,
    _remove_octave_ghosts,
    _trim_monophonic,
    build_score,
)


def test_merge_split_notes_rejoins_fragments():
    # A genuine Basic Pitch fragment: a tiny clipped blip immediately followed by
    # the real note (onsets ~30 ms apart) -> rejoin.
    notes = [
        {"start": 0.0, "end": 0.03, "pitch": 47, "velocity": 80},
        {"start": 0.05, "end": 1.0, "pitch": 47, "velocity": 90},
    ]
    out = _merge_split_notes(notes)
    assert len(out) == 1
    assert abs(out[0]["end"] - 1.0) < 1e-6
    assert out[0]["velocity"] == 90


def test_merge_split_notes_keeps_legato_repeats():
    # Repeated same-pitch eighth notes played legato: the release gap is under
    # 40 ms but the onsets are a real eighth apart -> must stay separate (this is
    # the bug where eighth-note runs collapsed into one long note).
    notes = [
        {"start": 0.00, "end": 0.21, "pitch": 36, "velocity": 90},
        {"start": 0.23, "end": 0.44, "pitch": 36, "velocity": 90},  # ~20ms gap
        {"start": 0.46, "end": 0.67, "pitch": 36, "velocity": 90},
        {"start": 0.69, "end": 0.90, "pitch": 36, "velocity": 90},
    ]
    assert len(_merge_split_notes(notes)) == 4


def test_remove_octave_ghosts_strips_harmonic_on_sustain():
    # A held note (whole-ish) with an octave-up ghost firing mid-way -> ghost
    # removed so the long note survives intact.
    notes = [
        {"start": 0.0, "end": 2.0, "pitch": 31, "velocity": 90},   # sustained G1
        {"start": 0.9, "end": 1.3, "pitch": 43, "velocity": 70},   # G2 ghost
    ]
    out = _remove_octave_ghosts(notes)
    assert {n["pitch"] for n in out} == {31}


def test_remove_octave_ghosts_keeps_real_octave_moves():
    # Fast octave alternation on short notes (real bass line) must be kept: the
    # lower note doesn't sustain past the upper.
    notes = [
        {"start": 0.0, "end": 0.22, "pitch": 31, "velocity": 90},  # G1 eighth
        {"start": 0.22, "end": 0.44, "pitch": 43, "velocity": 90}, # G2 eighth
        {"start": 0.44, "end": 0.66, "pitch": 31, "velocity": 90}, # G1 eighth
    ]
    assert len(_remove_octave_ghosts(notes)) == 3


def test_merge_split_notes_keeps_rearticulations():
    notes = [
        {"start": 0.0, "end": 0.45, "pitch": 47, "velocity": 80},
        {"start": 0.70, "end": 1.0, "pitch": 47, "velocity": 90},  # big gap -> keep
    ]
    assert len(_merge_split_notes(notes)) == 2


def test_time_to_quarter_uses_beat_grid():
    # 120 BPM => 0.5 s per beat (one quarter per beat).
    beats = [0.0, 0.5, 1.0, 1.5, 2.0]
    f = _make_time_to_quarter(beats, bpm=120.0)
    assert abs(f(0.0) - 0.0) < 1e-6
    assert abs(f(0.5) - 1.0) < 1e-6
    assert abs(f(0.25) - 0.5) < 1e-6  # halfway between beats 0 and 1
    # Extrapolation past the last beat using the final interval.
    assert abs(f(2.5) - 5.0) < 1e-6


def test_time_to_quarter_falls_back_to_bpm():
    f = _make_time_to_quarter([], bpm=120.0)
    assert abs(f(1.0) - 2.0) < 1e-6  # 2 quarters/sec at 120 BPM


def test_trim_monophonic_resolves_overlaps():
    notes = [
        {"start": 0.0, "end": 1.0, "pitch": 40, "velocity": 80},
        {"start": 0.5, "end": 1.5, "pitch": 47, "velocity": 90},  # overlaps -> trims prev
    ]
    out = _trim_monophonic(notes)
    assert len(out) == 2
    assert abs(out[0]["end"] - 0.5) < 1e-6  # first trimmed to second's onset


def test_trim_monophonic_same_onset_keeps_loudest():
    notes = [
        {"start": 0.0, "end": 1.0, "pitch": 40, "velocity": 60},
        {"start": 0.0, "end": 1.0, "pitch": 52, "velocity": 100},
    ]
    out = _trim_monophonic(notes)
    assert len(out) == 1
    assert out[0]["pitch"] == 52


def test_build_score_quantises_onto_beats():
    beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
    notes = [
        {"start": 0.0, "end": 0.5, "pitch": 45, "velocity": 90},
        {"start": 0.5, "end": 1.0, "pitch": 47, "velocity": 90},
        {"start": 1.0, "end": 2.0, "pitch": 48, "velocity": 90},
    ]
    score = build_score(notes, bpm=120.0, beats=beats, part_name="bass", monophonic=True)
    flat = score.recurse().notes
    assert len(flat) == 3
    # First note sits on the downbeat (offset 0) with quarter-note length.
    first = flat[0]
    assert abs(first.offset - 0.0) < 1e-6
    assert abs(first.quarterLength - 1.0) < 1e-6


def test_build_score_ties_across_barline():
    beats = [i * 0.5 for i in range(12)]  # 120 BPM
    # Note from quarter 3 lasting 4 quarters -> crosses the bar line at quarter 4.
    notes = [{"start": 1.5, "end": 3.5, "pitch": 60, "velocity": 90}]
    score = build_score(notes, bpm=120.0, beats=beats, part_name="piano", downbeat_phase=0)
    middle_c = [n for n in score.recurse().notes if n.pitch.midi == 60]
    assert len(middle_c) >= 2  # split into tied notes across the bar line
    assert any(n.tie is not None for n in middle_c)


def test_build_score_rests_are_visible():
    # Gap-filling rests must be printable (not print-object="no"), otherwise
    # MuseScore renders them as confusing faint grey rests.
    beats = [i * 0.5 for i in range(12)]
    notes = [
        {"start": 0.0, "end": 0.25, "pitch": 45, "velocity": 90},
        {"start": 2.0, "end": 2.25, "pitch": 47, "velocity": 90},  # gap -> rests
    ]
    score = build_score(notes, bpm=120.0, beats=beats, part_name="bass", monophonic=True)
    rests = list(score.recurse().getElementsByClass("Rest"))
    assert rests
    assert all(not r.style.hideObjectOnPrint for r in rests)


def test_polyphonic_part_makes_chords():
    from music21.chord import Chord

    beats = [i * 0.5 for i in range(12)]
    # Three pitches sharing one onset should become a single chord.
    notes = [
        {"start": 1.0, "end": 1.5, "pitch": 60, "velocity": 90},
        {"start": 1.0, "end": 1.5, "pitch": 64, "velocity": 90},
        {"start": 1.0, "end": 1.5, "pitch": 67, "velocity": 90},
    ]
    score = build_score(notes, bpm=120.0, beats=beats, part_name="guitar",
                        monophonic=False, grid=4)
    chords = [e for e in score.recurse().notes if isinstance(e, Chord)]
    assert len(chords) == 1
    assert sorted(p.midi for p in chords[0].pitches) == [60, 64, 67]


def test_monophonic_part_has_no_chords():
    from music21.chord import Chord

    beats = [i * 0.5 for i in range(12)]
    notes = [
        {"start": 1.0, "end": 1.5, "pitch": 40, "velocity": 90},
        {"start": 1.0, "end": 1.5, "pitch": 47, "velocity": 70},  # simultaneous
    ]
    score = build_score(notes, bpm=120.0, beats=beats, part_name="bass",
                        monophonic=True, grid=2)
    assert not any(isinstance(e, Chord) for e in score.recurse().notes)


def test_bass_gets_bass_clef():
    notes = [{"start": 0.0, "end": 0.5, "pitch": 40, "velocity": 90}]
    score = build_score(notes, bpm=120.0, beats=[0.0, 0.5, 1.0], part_name="bass", monophonic=True)
    clefs = score.recurse().getElementsByClass("Clef")
    assert any(c.classes[0] == "BassClef" for c in clefs)


def test_beat_offset_shifts_placement():
    beats = [i * 0.5 for i in range(12)]  # 120 BPM
    notes = [{"start": 1.0, "end": 1.5, "pitch": 48, "velocity": 90}]  # quarter 2.0
    base = build_score(notes, bpm=120.0, beats=beats, part_name="bass", grid=4)
    shifted = build_score(notes, bpm=120.0, beats=beats, part_name="bass", grid=4,
                          beat_offset=0.25)
    bo = float(next(iter(base.recurse().notes)).offset)
    so = float(next(iter(shifted.recurse().notes)).offset)
    assert abs((so - bo) - 0.25) < 1e-6


def test_build_score_handles_empty_notes():
    score = build_score([], bpm=120.0, part_name="piano")
    assert score is not None
    assert len(score.recurse().notes) == 0


def test_grid_origin():
    assert _grid_origin([0.0, 4.0], bar_len=4, phase_q=0) == 0.0
    # Downbeat at quarter 1, no notes before it -> origin = phase.
    assert _grid_origin([1.0, 5.0], bar_len=4, phase_q=1) == 1.0
    # A pickup note before the downbeat -> pull origin back one bar.
    assert _grid_origin([0.0, 1.0, 5.0], bar_len=4, phase_q=1) == 1.0 - 4.0


def test_build_score_aligns_downbeat_to_barline():
    # 8 beats at 120 BPM; downbeat is the 2nd beat (phase 1), 4/4.
    beats = [i * 0.5 for i in range(8)]
    notes = [
        {"start": 0.0, "end": 0.5, "pitch": 45, "velocity": 70},  # pickup
        {"start": 0.5, "end": 1.0, "pitch": 50, "velocity": 100},  # downbeat
        {"start": 2.5, "end": 3.0, "pitch": 52, "velocity": 100},  # next downbeat
    ]
    score = build_score(
        notes, bpm=120.0, beats=beats, part_name="bass",
        monophonic=True, beats_per_bar=4, downbeat_phase=1,
    )
    by_pitch = {n.pitch.midi: n for n in score.recurse().notes}
    # After makeMeasures, .offset is measure-relative; downbeats start a measure.
    assert abs(by_pitch[50].offset - 0.0) < 1e-6
    assert abs(by_pitch[52].offset - 0.0) < 1e-6
    # The pickup note sits later within the opening (partial) measure.
    assert by_pitch[45].offset > 0.0


def _printed_accidentals(score):
    from music21 import chord, stream
    out = []
    for m in score.parts[0].getElementsByClass(stream.Measure):
        for n in m.notes:
            for p in (n.pitches if isinstance(n, chord.Chord) else [n.pitch]):
                a = p.accidental
                if a and a.displayStatus:
                    out.append((p.nameWithOctave, a.name))
    return out


def test_no_courtesy_natural_on_in_key_repeat():
    # Two in-key F2 separated by a rest in F major (1 flat). music21 wants to
    # stamp a courtesy natural on the second; it must not appear.
    beats = [i * 0.5 for i in range(8)]
    notes = [
        {"start": 1.0, "end": 1.25, "pitch": 41, "velocity": 90},  # F2
        {"start": 1.5, "end": 2.0, "pitch": 41, "velocity": 90},   # F2 after a rest
    ]
    score = build_score(notes, bpm=120.0, beats=beats, part_name="bass",
                        monophonic=True, key_sharps=-1)
    assert ("F2", "natural") not in _printed_accidentals(score)


def test_no_redundant_flat_restating_key():
    # B-flat is in the key (1 flat); an explicit flat must not be printed when it
    # only restates the key signature, even after a B-natural in another octave.
    beats = [i * 0.5 for i in range(8)]
    notes = [
        {"start": 0.0, "end": 0.5, "pitch": 59, "velocity": 90},  # B3 natural
        {"start": 0.5, "end": 1.0, "pitch": 46, "velocity": 90},  # Bb1 (other octave)
    ]
    score = build_score(notes, bpm=120.0, beats=beats, part_name="bass",
                        monophonic=True, key_sharps=-1)
    printed = _printed_accidentals(score)
    assert ("B3", "natural") in printed       # genuinely needed (cancels key)
    assert ("B-1", "flat") not in printed     # redundant restatement of key


def test_needed_natural_cancelling_accidental_kept():
    # G#1 then G1 in the same bar: the natural is required and must stay.
    beats = [i * 0.5 for i in range(8)]
    notes = [
        {"start": 0.0, "end": 0.5, "pitch": 32, "velocity": 90},  # G#1
        {"start": 0.5, "end": 1.0, "pitch": 31, "velocity": 90},  # G1
    ]
    score = build_score(notes, bpm=120.0, beats=beats, part_name="bass",
                        monophonic=True, key_sharps=0)
    printed = _printed_accidentals(score)
    assert ("G#1", "sharp") in printed
    assert ("G1", "natural") in printed


def test_transpose_and_clef_for_treble_bass():
    # bass_treble: same notes lifted an octave, rendered in treble clef.
    from music21 import clef
    notes = [{"start": 0.0, "end": 0.5, "pitch": 40, "velocity": 90}]  # E2
    beats = [i * 0.5 for i in range(4)]
    base = build_score(notes, bpm=120.0, beats=beats, part_name="bass",
                       monophonic=True)
    treble = build_score(notes, bpm=120.0, beats=beats, part_name="bass_treble",
                         monophonic=True, clef_name="treble", transpose=12)
    assert isinstance(base.parts[0].recurse().getElementsByClass(clef.Clef)[0],
                      clef.BassClef)
    assert isinstance(treble.parts[0].recurse().getElementsByClass(clef.Clef)[0],
                      clef.TrebleClef)
    bp = next(iter(base.recurse().notes)).pitches[0].midi
    tp = next(iter(treble.recurse().notes)).pitches[0].midi
    assert tp - bp == 12


def test_guitar_octave_clef_keeps_concert_pitch():
    # Guitar uses the treble-8vb clef: the score reads an octave up but the
    # actual pitch (and thus the MIDI) must stay at concert pitch.
    from music21 import clef
    notes = [{"start": 0.0, "end": 0.5, "pitch": 43, "velocity": 90}]  # G2
    beats = [i * 0.5 for i in range(4)]
    score = build_score(notes, bpm=120.0, beats=beats, part_name="guitar",
                        clef_name="treble8vb")
    cl = score.parts[0].recurse().getElementsByClass(clef.Clef)[0]
    assert isinstance(cl, clef.Treble8vbClef)
    assert cl.octaveChange == -1
    assert next(iter(score.recurse().notes)).pitches[0].midi == 43  # unchanged
