"""Turn note events into a quantised music21 Score ready for MusicXML/MIDI."""

from __future__ import annotations

import bisect

# Snap everything to a 16th-note grid (4 subdivisions per quarter). Kept duple
# (no triplets) so durations stay simple and MuseScore-friendly.
_GRID = 4
_MIN_DURATION_QL = 0.25  # never emit anything shorter than a 16th note
_EPS = 1e-3

# Parts that are effectively a single melodic line; overlaps are transcription
# artefacts and get trimmed away.
MONOPHONIC_PARTS = {"bass", "vocals"}

# Cap simultaneous notes per chord so ghost pitches don't pile up.
_MAX_CHORD = 6


def _make_time_to_quarter(beats: list[float] | None, bpm: float):
    """Return f(seconds) -> quarter-note position.

    With a librosa beat grid we treat each beat as one quarter note and
    piecewise-linearly interpolate between beats (extrapolating past the ends
    with the nearest beat interval). This absorbs tempo drift and the song's
    start offset far better than a constant BPM measured from t=0.
    """
    if beats and len(beats) >= 2:
        bts = list(beats)
        n = len(bts)

        def f(t: float) -> float:
            if t <= bts[0]:
                iv = bts[1] - bts[0]
                return (t - bts[0]) / iv if iv > 0 else 0.0
            if t >= bts[-1]:
                iv = bts[-1] - bts[-2]
                return (n - 1) + ((t - bts[-1]) / iv if iv > 0 else 0.0)
            i = bisect.bisect_right(bts, t) - 1
            iv = bts[i + 1] - bts[i]
            return i + ((t - bts[i]) / iv if iv > 0 else 0.0)

        return f

    qps = (bpm / 60.0) if bpm and bpm > 0 else 2.0
    return lambda t: t * qps


def _grid_origin(qpositions: list[float], bar_len: float, phase_q: float) -> float:
    """Quarter offset to subtract so detected downbeats land on bar lines.

    If notes precede the first downbeat they form a pickup, so we pull the origin
    back by one bar and let those notes fill the tail of an opening measure;
    otherwise the first downbeat simply becomes offset 0.
    """
    if phase_q <= _EPS:
        return 0.0
    has_pickup = any(q < phase_q - _EPS for q in qpositions)
    return (phase_q - bar_len) if has_pickup else phase_q


def _merge_split_notes(
    notes: list[dict], gap: float = 0.04, min_onset_sep: float = 0.08
) -> list[dict]:
    """Re-join same-pitch notes that are fragments of one sustained note.

    Basic Pitch sometimes splits a held note into several pieces. We rejoin two
    same-pitch notes only when BOTH (a) the gap between them is near-zero (<gap)
    AND (b) their onsets are closer than `min_onset_sep` — i.e. the second piece
    starts almost immediately, so it's a micro-fragment, not a real re-articulation.
    Without (b), legato repeated notes (e.g. eighth-note runs, whose release gap
    can fall under 40 ms) were wrongly fused into one long note. A real note —
    even a fast eighth (~190 ms at 160 BPM) — has onsets far enough apart to be
    kept. Each pitch is an independent line, so this is safe for chords.
    """
    by_pitch: dict[int, list[dict]] = {}
    for n in sorted(notes, key=lambda n: n["start"]):
        by_pitch.setdefault(int(n["pitch"]), []).append(dict(n))

    merged: list[dict] = []
    for line in by_pitch.values():
        cur = line[0]
        prev_onset = cur["start"]
        for nxt in line[1:]:
            fragment = (
                nxt["start"] - cur["end"] <= gap
                and nxt["start"] - prev_onset < min_onset_sep
            )
            if fragment:
                cur["end"] = max(cur["end"], nxt["end"])
                cur["velocity"] = max(cur["velocity"], nxt["velocity"])
            else:
                merged.append(cur)
                cur = dict(nxt)
            prev_onset = nxt["start"]
        merged.append(cur)
    merged.sort(key=lambda n: (n["start"], n["pitch"]))
    return merged


# A lower-octave note must still be sounding at least this long past the upper
# note's onset for the upper to count as a harmonic ghost. Tuned so only clearly
# sustained notes (~half/whole at typical tempos) trigger removal: that's where
# the "held note suddenly jumps an octave" artefact happens, while genuine
# octave leaps and fast octave alternations (short lower notes) are spared.
_GHOST_SUSTAIN_S = 0.7


def _remove_octave_ghosts(notes: list[dict], eps: float = 0.03) -> list[dict]:
    """Drop octave-up harmonic ghosts that ride a sustained note (monophonic).

    A permissive transcription threshold (needed to catch fast repeated notes)
    also lets Basic Pitch fire the first harmonic: while a long note of pitch p
    sustains, a spurious note at p+12 onsets mid-way. On a monophonic line the
    instrument can't sound both, and the ghost otherwise truncates the real note
    into "long note, then suddenly an octave up". We remove a note H when a
    lower-octave note L (pitch H-12) started first and is still sounding
    `_GHOST_SUSTAIN_S` past H's onset — i.e. H interrupts a genuinely held note.
    Short lower notes (eighth-run octave moves, real leaps) don't trigger it.
    """
    ordered = sorted(notes, key=lambda n: n["start"])
    keep: list[dict] = []
    for h in ordered:
        hp, hs = int(h["pitch"]), h["start"]
        ghost = any(
            int(lower["pitch"]) == hp - 12
            and lower["start"] <= hs + eps
            and lower["end"] - hs >= _GHOST_SUSTAIN_S
            for lower in ordered
            if lower is not h
        )
        if not ghost:
            keep.append(h)
    return keep


def _trim_monophonic(notes: list[dict]) -> list[dict]:
    """Collapse a note list to a single voice: at shared onsets keep the
    loudest; when notes overlap, trim the earlier one to the next onset."""
    ordered = sorted(notes, key=lambda n: (n["start"], -n["velocity"]))
    out: list[dict] = []
    for n in ordered:
        if out and n["start"] < out[-1]["end"] - _EPS:
            if n["start"] <= out[-1]["start"] + _EPS:
                continue  # same onset, louder already kept
            out[-1] = {**out[-1], "end": n["start"]}
        out.append(dict(n))
    return [n for n in out if n["end"] - n["start"] > _EPS]


def _single_voice(events, grid):
    """Reduce events to one pitch at a time: at a shared onset keep the loudest;
    when notes overlap, trim the earlier one. Returns (onset, dur, [pitch], vel)."""
    out: list[tuple[float, float, list[int], int]] = []
    for onset, dur, pitch, vel in sorted(events, key=lambda e: (e[0], -e[3])):
        if out:
            po, pd, pp, pv = out[-1]
            if onset < po + pd - 1e-9:
                if onset <= po + 1e-9:
                    continue  # same onset: keep the louder (already first)
                out[-1] = (po, onset - po, pp, pv)  # trim previous to this onset
        out.append((onset, dur, [pitch], vel))
    return out


def _chord_voice(events, grid):
    """Homophonic reduction: notes sharing an onset become one chord, truncated
    to the next onset so the part stays a single renderable voice while keeping
    harmony. Returns (onset, dur, [pitches], vel)."""
    by_onset: dict[float, list[tuple[float, int, int]]] = {}
    for onset, dur, pitch, vel in events:
        by_onset.setdefault(onset, []).append((dur, pitch, vel))

    onsets = sorted(by_onset)
    out: list[tuple[float, float, list[int], int]] = []
    for i, onset in enumerate(onsets):
        group = by_onset[onset]
        pitches = sorted({p for _, p, _ in group})
        if len(pitches) > _MAX_CHORD:  # keep the loudest pitches
            loudest = sorted(group, key=lambda g: -g[2])[:_MAX_CHORD]
            pitches = sorted({p for _, p, _ in loudest})
        end = onset + max(d for d, _, _ in group)
        if i + 1 < len(onsets):
            end = min(end, onsets[i + 1])  # no overlap with the next chord
        dur = max(1.0 / grid, end - onset)
        vel = max(v for _, _, v in group)
        out.append((onset, dur, pitches, vel))
    return out


_SHARP_ORDER = ["F", "C", "G", "D", "A", "E", "B"]
_FLAT_ORDER = ["B", "E", "A", "D", "G", "C", "F"]


def _strip_redundant_accidentals(part, key_sharps: int) -> None:
    """Hide accidentals music21 prints but standard notation doesn't need.

    music21's makeAccidentals stamps courtesy accidentals that no cautionary
    flag turns off: a natural on an in-key pitch repeated across a rest (a 2nd
    F in a bar), or a flat/sharp that merely restates the key signature in
    another octave. An accidental is only required when the note's alteration
    differs from what's currently in effect for that step+octave — the key
    signature, or a prior accidental in the same measure+octave. We walk each
    measure tracking the prevailing alteration and clear the display on any
    accidental that just restates it. Hide-only: we never force one on, so
    genuinely needed accidentals (which makeAccidentals already shows) stay.
    """
    from music21 import stream

    if key_sharps > 0:
        key_alter = {s: 1 for s in _SHARP_ORDER[:key_sharps]}
    elif key_sharps < 0:
        key_alter = {s: -1 for s in _FLAT_ORDER[: -key_sharps]}
    else:
        key_alter = {}

    for measure in part.getElementsByClass(stream.Measure):
        active: dict[tuple[str, int], float] = {}
        for el in measure.notes:
            for p in el.pitches:
                oct_key = (p.step, p.octave)
                prevailing = active.get(oct_key, key_alter.get(p.step, 0))
                cur = p.accidental.alter if p.accidental else 0
                if p.accidental and p.accidental.displayStatus and cur == prevailing:
                    p.accidental.displayStatus = False  # redundant restatement
                active[oct_key] = cur


def build_score(
    notes: list[dict],
    bpm: float,
    time_signature: str = "4/4",
    part_name: str = "Part",
    beats: list[float] | None = None,
    monophonic: bool = False,
    beats_per_bar: int = 4,
    downbeat_phase: int = 0,
    key_sharps: int | None = None,
    grid: int = _GRID,
    beat_offset: float = 0.0,
    clef_name: str | None = None,
    transpose: int = 0,
):
    from music21 import chord, clef, duration, key, meter, note, stream, tempo

    bpm = float(bpm) if bpm and bpm > 0 else 120.0
    if transpose:  # e.g. +12 to lift the bass an octave for a treble-clef read
        notes = [{**n, "pitch": int(n["pitch"]) + transpose} for n in notes]
    notes = _merge_split_notes(notes)
    if monophonic:
        notes = _remove_octave_ghosts(notes)
        notes = _trim_monophonic(notes)

    t2q = _make_time_to_quarter(beats, bpm)

    # Align bar lines to the detected downbeat (one beat == one quarter here).
    bar_len = float(beats_per_bar) if beats_per_bar > 0 else 4.0
    phase_q = float(downbeat_phase) % bar_len
    starts_q = [t2q(float(n["start"])) for n in notes]
    origin = _grid_origin(starts_q, bar_len, phase_q)

    score = stream.Score()
    part = stream.Part()
    part.partName = part_name
    part.append(tempo.MetronomeMark(number=round(bpm, 2)))
    try:
        part.append(meter.TimeSignature(time_signature))
    except Exception:
        part.append(meter.TimeSignature("4/4"))

    # Quantize onsets/durations to the grid. We then keep a SINGLE renderable
    # voice (overlaps/voices otherwise make music21 emit huge <divisions> and
    # voice-0 that MuseScore imports as empty measures): monophonic parts keep
    # one pitch at a time; polyphonic parts collapse simultaneous notes into
    # chords (homophonic), truncated so chords never overlap.
    def snap(q: float) -> float:
        return round(q * grid) / grid

    # beat_offset (quarters) lets the user nudge the whole grid to line bar lines
    # up with the felt beat when beat tracking is a fraction off.
    events: list[tuple[float, float, int, int]] = []
    for n in notes:
        onset = snap(max(0.0, t2q(float(n["start"])) - origin + beat_offset))
        end = snap(max(0.0, t2q(float(n["end"])) - origin + beat_offset))
        dur = max(1.0 / grid, end - onset)
        events.append((onset, dur, int(n["pitch"]), int(n.get("velocity") or 64)))

    voiced = _single_voice(events, grid) if monophonic else _chord_voice(events, grid)
    for onset, dur, pitches, vel in voiced:
        element = (
            note.Note(pitches[0])
            if len(pitches) == 1
            else chord.Chord(sorted(pitches))
        )
        element.duration = duration.Duration(quarterLength=dur)
        element.volume.velocity = vel
        part.insert(onset, element)

    # Prefer a shared, song-wide key (passed in) so every part agrees; only fall
    # back to per-part estimation when none is given.
    if key_sharps is None:
        try:
            key_sharps = part.analyze("key").sharps
        except Exception:
            key_sharps = 0
    ks = key.KeySignature(key_sharps)
    part.insert(0, ks)

    if clef_name == "treble":
        part.insert(0, clef.TrebleClef())
    elif clef_name == "treble8vb":  # guitar clef: notes drawn an octave up
        part.insert(0, clef.Treble8vbClef())
    elif clef_name == "bass":
        part.insert(0, clef.BassClef())
    else:
        part.insert(0, clef.BassClef() if part_name == "bass" else clef.TrebleClef())

    # Build display notation explicitly rather than via Stream.makeNotation():
    # makeNotation's exporter path emits gap-filling rests as non-printing
    # (print-object="no"), which MuseScore shows as faint grey rests. Calling
    # makeRests with hideRests=False yields real, visible "instrument is silent"
    # rests instead.
    try:
        part.makeMeasures(inPlace=True)
        part.makeTies(inPlace=True)  # split notes spanning bar lines into ties
        # Resolve accidentals against the key signature so only genuinely needed
        # accidentals print (otherwise in-key black keys show an accidental and
        # in-key naturals print on every note). Must be done per Measure:
        # Part.makeAccidentals has a different signature and ignores
        # useKeySignature. Pass the KeySignature object itself rather than
        # `True` — the context lookup for `True` is unreliable on a freshly
        # made measure (it falls back to C major → spurious accidentals). Only
        # measure 1 carries the KeySignature, so reuse `ks` for every measure.
        # Setting displayStatus here makes the exporter (overrideStatus=False)
        # respect our choices instead of redoing them.
        # cautionaryNotImmediateRepeat/cautionaryPitchClass default to True, which
        # adds courtesy naturals on a repeated pitch separated by a rest (e.g. a
        # 2nd in-key C in a bar) or on the same pitch class in another octave.
        # Those read as spurious accidentals on a draft, so disable them — only
        # strictly-required accidentals print.
        for measure in part.getElementsByClass(stream.Measure):
            try:
                measure.makeAccidentals(
                    useKeySignature=ks,
                    cautionaryNotImmediateRepeat=False,
                    cautionaryPitchClass=False,
                    inPlace=True,
                )
            except Exception:
                pass
        _strip_redundant_accidentals(part, key_sharps)
        part.makeRests(fillGaps=True, inPlace=True, hideRests=False)
        try:
            part.makeBeams(inPlace=True)
        except Exception:
            pass  # beaming is cosmetic
        notated = part
    except Exception:
        notated = part.makeNotation()

    score.insert(0, notated)
    return score
