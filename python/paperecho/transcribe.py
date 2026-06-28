"""Pitch -> note-event transcription.

Monophonic parts (bass, vocals) are onset-driven: librosa onsets set the note
boundaries and CREPE (a neural f0 model, via torchcrepe) labels the pitch within
each span, so repeated same-pitch notes stay separate instead of merging into one
sustain. Polyphonic parts (guitar, piano) use the Basic Pitch model, which
detects onsets natively and handles chords — CREPE is monophonic and cannot.
(librosa.pyin remains in `transcribe_mono` as a lightweight fallback.)
"""

from __future__ import annotations

from pathlib import Path

# Parts that are a single melodic line -> monophonic f0 tracking.
MONO_PARTS = {"bass", "vocals"}
# pYIN frequency bounds per monophonic part (Hz).
_MONO_RANGE = {"bass": (40.0, 400.0), "vocals": (80.0, 1000.0)}
_PYIN_HOP = 1024  # ~64ms frames at 16 kHz; good speed/resolution for held notes
# 16 kHz is ample for bass/vocal fundamentals (<1 kHz) and ~2.6x faster than 22 kHz.
_PYIN_SR = 16000


def transcribe_part(stem_wav: str | Path, part: str) -> list[dict]:
    """Pick a transcription strategy per part.

    Monophonic parts (bass, vocals) use `transcribe_onset`: an *onset-driven*
    tracker where note boundaries come from attack detection, not pitch
    continuity, with the pitch labelled by CREPE. This keeps same-pitch repeated
    notes (the `E E E E` line that every pitch-led tracker merges into one long
    `E────`) and, being a single-line f0 model, avoids the harmony ghosts Basic
    Pitch hears in a monophonic stem. Piano uses the ByteDance piano-specialised
    model (`transcribe_piano`) for clean chords; guitar stays on Basic Pitch (no
    good lightweight guitar-specialised model). Both are polyphonic — CREPE is
    monophonic and cannot voice chords."""
    if part in MONO_PARTS:
        return transcribe_onset(stem_wav, part)
    if part == "piano":
        return transcribe_piano(stem_wav)
    return transcribe(stem_wav, part=part)


def transcribe_mono(stem_wav: str | Path, part: str) -> list[dict]:
    """Monophonic f0 transcription via pYIN. Returns {start,end,pitch,velocity}.

    Tracks one fundamental per frame, smooths it to kill brief octave slips, then
    groups runs of the same semitone into notes (gating unvoiced frames as rests).
    """
    import librosa
    import numpy as np

    from .preprocess import noise_gate

    fmin, fmax = _MONO_RANGE.get(part, (60.0, 1200.0))
    y, sr = librosa.load(str(stem_wav), sr=_PYIN_SR, mono=True)
    y = noise_gate(y, sr)  # remove inter-note bleed before tracking

    f0, voiced, _vprob = librosa.pyin(
        y, fmin=fmin, fmax=fmax, sr=sr, hop_length=_PYIN_HOP
    )
    times = librosa.times_like(f0, sr=sr, hop_length=_PYIN_HOP)
    rms = librosa.feature.rms(y=y, hop_length=_PYIN_HOP)[0]

    # pYIN tracks pitch but not re-articulations: a repeated note on the same
    # pitch reads as one long sustain. Detect attacks separately and split notes
    # there. Onsets use a finer hop (better timing than pYIN's 64 ms frames) and
    # backtrack so each onset sits at the true start of the attack — this is what
    # makes notes land on the right beat.
    onset_times = sorted(
        float(o)
        for o in librosa.onset.onset_detect(
            y=y, sr=sr, hop_length=512, backtrack=True, units="time"
        )
    )

    # Rounded semitone per frame; NaN where unvoiced.
    midi = np.full(len(f0), np.nan)
    valid = voiced & ~np.isnan(f0)
    midi[valid] = np.round(librosa.hz_to_midi(f0[valid]))
    midi = _median_smooth(midi, 5)

    frame_dur = _PYIN_HOP / sr
    notes: list[dict] = []
    cur: dict | None = None
    oi = 0
    for i, t in enumerate(times):
        # Consume any onset landing in this frame; keep its precise time.
        onset_t = None
        while oi < len(onset_times) and onset_times[oi] < float(t) + frame_dur:
            onset_t = onset_times[oi]
            oi += 1

        pitch = midi[i]
        if np.isnan(pitch):
            cur = _close(cur, notes)
            continue
        pitch = int(pitch)
        if cur is not None and cur["pitch"] == pitch and onset_t is None:
            cur["end"] = float(t)
            cur["_rms"].append(float(rms[i]) if i < len(rms) else 0.0)
        else:
            if cur is not None and onset_t is not None:
                cur["end"] = onset_t  # butt the previous note up to the attack
            cur = _close(cur, notes)
            start = onset_t if onset_t is not None else float(t)
            cur = {"start": start, "end": max(start, float(t)), "pitch": pitch,
                   "_rms": [float(rms[i]) if i < len(rms) else 0.0]}
    _close(cur, notes)

    # Drop fragments shorter than a 16th-ish; scale velocity from loudness.
    peak = max((max(n["_rms"]) for n in notes), default=1.0) or 1.0
    out = []
    for n in notes:
        if n["end"] - n["start"] < 0.10:
            continue
        vel = int(max(1, min(127, round((max(n["_rms"]) / peak) * 110 + 12))))
        out.append({"start": round(n["start"], 4), "end": round(n["end"], 4),
                    "pitch": n["pitch"], "velocity": vel})
    return out


# --- Onset-driven bass transcription -------------------------------------------
# Design principle: never let the pitch detector decide note boundaries. Detect
# attacks first, treat each onset->next-onset span as one note, then fill in the
# pitch per span. This keeps repeated same-pitch notes separate (the pitch-led
# trackers' fatal `E E E E` -> `E────` merge) and suits live, compressed,
# mute-heavy bass.
_ONSET_HOP = 512  # fine hop for attack timing
_MIN_NOTE_S = 0.08  # merge/drop onsets closer than this (spurious double-triggers)
_REST_RMS_FRAC = 0.06  # a span quieter than this * peak is a rest (mute/gap)

# CREPE (neural f0) labels the pitch inside each onset span. "tiny" is plenty for
# bass fundamentals and ~an order of magnitude faster than "full"; 10 ms frames.
_CREPE_HOP = 160
_CREPE_MODEL = "tiny"
_CREPE_BATCH = 1024
_CREPE_PERIODICITY = 0.3  # frames less periodic than this carry no reliable pitch


def _crepe_device() -> str:
    """Device for CREPE + the ByteDance piano model (see paperecho.device)."""
    from .device import resolve_device

    return resolve_device()


def _crepe_f0(y, sr: int, fmin: float, fmax: float):
    """Per-frame f0 via CREPE (torchcrepe). Returns (midi, times, periodicity)
    where midi is NaN on unvoiced frames; `y` is mono float32 already at `sr`.

    CREPE is a neural pitch estimator with markedly fewer octave slips than pYIN
    on a separated, compressed bass stem. It only *labels* pitch inside the
    onset-defined spans — it never decides note boundaries."""
    import numpy as np
    import torch
    import torchcrepe

    audio = torch.tensor(np.ascontiguousarray(y), dtype=torch.float32)[None]
    kw = dict(
        hop_length=_CREPE_HOP, fmin=fmin, fmax=fmax, model=_CREPE_MODEL,
        batch_size=_CREPE_BATCH, return_periodicity=True,
    )
    try:
        pitch, periodicity = torchcrepe.predict(audio, sr, device=_crepe_device(), **kw)
    except Exception:  # e.g. an op missing on MPS/CUDA -> fall back to CPU
        pitch, periodicity = torchcrepe.predict(audio, sr, device="cpu", **kw)

    hz = pitch[0].cpu().numpy()
    per = periodicity[0].cpu().numpy()
    midi = np.full(len(hz), np.nan)
    voiced = (per >= _CREPE_PERIODICITY) & (hz > 0)
    midi[voiced] = np.round(69.0 + 12.0 * np.log2(hz[voiced] / 440.0))
    times = np.arange(len(hz)) * (_CREPE_HOP / sr)
    return midi, times, per


def transcribe_onset(stem_wav: str | Path, part: str = "bass") -> list[dict]:
    """Onset-driven monophonic transcription (bass, vocals).

    Returns {start,end,pitch,velocity}.

    1. Onset detection (backtracked) sets every note boundary.
    2. Each span [onset_i, onset_{i+1}) is one note — repeated notes survive.
    3. Pitch = median of CREPE's voiced frames inside the span (the detector only
       labels pitch, it never splits or joins notes).
    4. Trailing silence inside a span is trimmed and fully-silent spans dropped,
       so mutes and gaps become rests instead of over-sustained notes.

    `part` selects the f0 search range (`_MONO_RANGE`); vocals get a wider band
    than bass. Bass attacks are crisp plucks; vocal "onsets" are syllable starts,
    which are still musically the note boundaries for a sung melody.
    """
    import librosa
    import numpy as np

    from .preprocess import noise_gate

    fmin, fmax = _MONO_RANGE.get(part, _MONO_RANGE["bass"])
    y, sr = librosa.load(str(stem_wav), sr=_PYIN_SR, mono=True)
    y = noise_gate(y, sr)  # the attack pre-roll in noise_gate keeps onsets intact
    if y.size == 0:
        return []
    duration = len(y) / sr

    # 1. Onsets -> note boundaries. backtrack lands each onset at the true start
    # of the attack so notes fall on the beat.
    onsets = [
        float(o)
        for o in librosa.onset.onset_detect(
            y=y, sr=sr, hop_length=_ONSET_HOP, backtrack=True, units="time"
        )
    ]
    # Collapse double-triggers (one attack often fires two close onsets).
    merged: list[float] = []
    for o in onsets:
        if not merged or o - merged[-1] >= _MIN_NOTE_S:
            merged.append(o)
    onsets = merged
    if not onsets:
        return []

    # Per-frame pitch via CREPE (neural f0 — cleaner than pYIN on a separated
    # bass) and loudness (RMS). CREPE only labels pitch; the onsets above set the
    # note boundaries.
    midi, f_times, _per = _crepe_f0(y, sr, fmin, fmax)

    rms = librosa.feature.rms(y=y, hop_length=_PYIN_HOP)[0]
    r_times = librosa.times_like(rms, sr=sr, hop_length=_PYIN_HOP)
    peak = float(np.max(rms)) if rms.size else 0.0
    if peak <= 0:
        return []
    rest_floor = _REST_RMS_FRAC * peak
    tail = _PYIN_HOP / sr

    # onset_detect tends to miss an attack at the very start (no pre-onset
    # baseline to rise from). If the audio is already sounding before the first
    # detected onset, that opening note exists — seed a boundary at its start.
    sounding_t = r_times[rms >= rest_floor]
    if sounding_t.size and onsets[0] - float(sounding_t[0]) >= _MIN_NOTE_S:
        onsets = [float(sounding_t[0])] + onsets

    # 2-4. One note per onset span; last span runs to the end of audio.
    bounds = onsets + [duration]
    notes: list[dict] = []
    for start, end in zip(bounds, bounds[1:]):
        seg = (r_times >= start) & (r_times < end)
        seg_rms, seg_t = rms[seg], r_times[seg]
        if seg_rms.size == 0:
            continue
        loud = float(np.max(seg_rms))
        if loud < rest_floor:
            continue  # whole span is silence -> rest

        # Trim trailing silence so a release/mute before the next attack is a rest.
        sounding = seg_t[seg_rms >= rest_floor]
        note_end = min(end, float(sounding[-1]) + tail) if sounding.size else end
        if note_end - start < _MIN_NOTE_S:
            continue

        # Pitch = median voiced semitone in the span; borrow the nearest frame for
        # spans too short to contain one (fast runs).
        m = midi[(f_times >= start) & (f_times < note_end) & ~np.isnan(midi)]
        if m.size == 0:
            idx = int(np.argmin(np.abs(f_times - (start + note_end) / 2)))
            if not np.isnan(midi[idx]):
                m = midi[idx : idx + 1]
        if m.size == 0:
            continue  # genuinely unvoiced -> rest

        vel = int(max(1, min(127, round((loud / peak) * 110 + 12))))
        notes.append({"start": round(start, 4), "end": round(note_end, 4),
                      "pitch": int(np.median(m)), "velocity": vel})
    return notes


def transcribe_bass_onset(stem_wav: str | Path) -> list[dict]:
    """Bass alias for the onset-driven transcriber (kept for callers/tests)."""
    return transcribe_onset(stem_wav, "bass")


def _close(cur: dict | None, notes: list[dict]) -> None:
    if cur is not None:
        notes.append(cur)
    return None


def _median_smooth(values, window: int):
    import numpy as np

    n = len(values)
    half = window // 2
    out = values.copy()
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        seg = values[lo:hi]
        seg = seg[~np.isnan(seg)]
        out[i] = np.median(seg) if seg.size else np.nan
    return out

# Per-part Basic Pitch tuning. Frequencies in Hz, note length in milliseconds.
# Constraining the frequency range to each instrument's tessitura removes a lot
# of octave errors and harmonic ghosts; a floor on note length kills fragments.
_PARAM_DEFAULTS = {
    "onset_threshold": 0.5,
    "frame_threshold": 0.3,
    "minimum_note_length": 120.0,
    "minimum_frequency": None,
    "maximum_frequency": None,
    "melodia_trick": True,
}

# Thresholds are deliberately conservative: separated stems carry artefacts that
# Basic Pitch hears as ghost notes, so we trade a few missed quiet notes for far
# fewer false ones (the tool is a draft to clean up, not a perfect transcription).
_PART_PARAMS = {
    # Bass: ~E1..~G4, monophonic. Tuned to capture near-continuous eighth-note
    # runs (common in driving basslines): onset 0.55 + min_note_length 100ms
    # admit eighths (~220ms at 136 BPM); frame_threshold 0.3 (vs 0.5) fills the
    # gaps between repeated notes — at 0.5 ~43% of eighth slots were empty
    # (rests where the bass actually plays), at 0.3 ~72% are filled. Trade-off:
    # 0.3 lets through some octave-up harmonic ghosts (easy to fix in a draft);
    # we favour correct rhythm over a sparse-but-clean line.
    "bass": {"minimum_frequency": 30.0, "maximum_frequency": 400.0,
             "minimum_note_length": 100.0, "onset_threshold": 0.55,
             "frame_threshold": 0.3},
    # Vocals: melodic line; keep a bit more permissive so phrases survive.
    "vocals": {"minimum_frequency": 80.0, "maximum_frequency": 1200.0,
               "minimum_note_length": 150.0, "onset_threshold": 0.6,
               "frame_threshold": 0.4},
    # Guitar: very noisy after separation — strict.
    "guitar": {"minimum_frequency": 70.0, "maximum_frequency": 1600.0,
               "minimum_note_length": 150.0, "onset_threshold": 0.65,
               "frame_threshold": 0.45},
    # Piano: near full range.
    "piano": {"minimum_frequency": 27.5, "maximum_frequency": 4200.0,
              "minimum_note_length": 130.0, "onset_threshold": 0.6,
              "frame_threshold": 0.4},
}


def transcribe(stem_wav: str | Path, part: str | None = None) -> list[dict]:
    """Return a list of note events: {start, end, pitch, velocity}.

    `part` selects instrument-specific tuning (bass/vocals/guitar/piano). Works
    for pitched, mostly mono/poly-phonic instruments; not meaningful for drums.
    """
    import contextlib
    import os
    import sys
    import tempfile

    import librosa
    import soundfile as sf
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict

    from .preprocess import noise_gate

    params = {**_PARAM_DEFAULTS, **_PART_PARAMS.get(part or "", {})}

    # Gate inter-note bleed first, then hand the cleaned audio to Basic Pitch
    # (which only reads from a path) via a temp wav.
    y, sr = librosa.load(str(stem_wav), sr=None, mono=True)
    gated = tempfile.mktemp(suffix=".wav")
    sf.write(gated, noise_gate(y, sr), sr)

    # basic-pitch prints progress/debug to stdout; keep our JSON stdout clean.
    try:
        with contextlib.redirect_stdout(sys.stderr):
            _model_output, _midi, note_events = predict(
                gated,
                ICASSP_2022_MODEL_PATH,
                onset_threshold=params["onset_threshold"],
                frame_threshold=params["frame_threshold"],
                minimum_note_length=params["minimum_note_length"],
                minimum_frequency=params["minimum_frequency"],
                maximum_frequency=params["maximum_frequency"],
                melodia_trick=params["melodia_trick"],
            )
    finally:
        try:
            os.remove(gated)
        except OSError:
            pass

    notes: list[dict] = []
    for ev in note_events:
        # (start_s, end_s, pitch_midi, amplitude[0..1], pitch_bends)
        start, end, pitch, amplitude = ev[0], ev[1], ev[2], ev[3]
        velocity = int(max(1, min(127, round(float(amplitude) * 127))))
        notes.append({
            "start": round(float(start), 4),
            "end": round(float(end), 4),
            "pitch": int(pitch),
            "velocity": velocity,
        })
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes


# --- Polyphonic piano transcription (ByteDance high-resolution model) -----------
# Basic Pitch is instrument-agnostic and noisy on a separated piano stem; the
# ByteDance "Note_pedal" model is piano-specialised and pretrained, so it reads
# stacked chords far more cleanly. CREPE can't be used here — it's monophonic.
_PIANO_SR = 16000  # the model's required sample rate
# Drop sub-grid piano blips. On a separated stem the ByteDance model emits a tail
# of very short, quiet notes (~7% of notes are <60 ms — far shorter than a 16th,
# which is ~125-200 ms at typical tempos, and a median velocity ~56 vs ~68
# overall). score.py snaps every note to the 16th grid with a 16th-note floor, so
# each of these blips gets PROMOTED to a printed 16th — pure clutter on the staff.
# 50 ms is conservative: shorter than a 32nd note even at 150 BPM, so it can't
# remove a note the grid could have rendered distinctly anyway.
_PIANO_MIN_DURATION = 0.05  # seconds


def _piano_events_to_notes(events: list[dict]) -> list[dict]:
    """Convert the ByteDance model's note events ({midi_note, onset_time,
    offset_time, velocity}) to our {start, end, pitch, velocity} format.
    Drops sub-grid blips shorter than `_PIANO_MIN_DURATION` (see above)."""
    notes: list[dict] = []
    for ev in events:
        start = round(float(ev["onset_time"]), 4)
        end = round(float(ev["offset_time"]), 4)
        if end - start < _PIANO_MIN_DURATION:
            continue
        notes.append({
            "start": start,
            "end": end,
            "pitch": int(ev["midi_note"]),
            "velocity": int(max(1, min(127, ev["velocity"]))),
        })
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes


def transcribe_piano(stem_wav: str | Path) -> list[dict]:
    """Polyphonic piano transcription via the ByteDance high-resolution model.

    Returns {start, end, pitch, velocity}. Chord-capable (unlike the monophonic
    CREPE bass/vocals path) and trained on piano, so stacked notes come out far
    cleaner than Basic Pitch on a separated stem. Uses the bundled offline
    checkpoint when `PAPER_ECHO_MODEL_CACHE` is set (packaged build); otherwise
    the ByteDance lib downloads a ~170 MB checkpoint on first use (cached to
    ~/piano_transcription_inference_data)."""
    import contextlib
    import os
    import sys

    import librosa
    import numpy as np

    from .preprocess import noise_gate

    y, sr = librosa.load(str(stem_wav), sr=_PIANO_SR, mono=True)
    if y.size == 0:
        return []
    y = noise_gate(y, sr)
    # Skip the (slow, ~minutes) ByteDance run when the gated stem is essentially
    # silent — i.e. a song with no real piano (the stem is just separation bleed,
    # which the noise gate flattens to ~0). It would produce no notes anyway.
    if float(np.max(np.abs(y))) < 1e-3:
        return []

    # The model load, checkpoint download, and post-processing all chat on stdout;
    # redirect to stderr to protect our JSON stdout contract (as with Basic Pitch).
    # Bundled offline checkpoint (packaged build). The ByteDance lib hardcodes
    # `~/piano_transcription_inference_data/<name>.pth` and wgets it when absent;
    # pointing checkpoint_path at the runtime's model-cache avoids that download
    # (and the wget dependency) without touching the user's home dir.
    ckpt = None
    cache = os.environ.get("PAPER_ECHO_MODEL_CACHE")
    if cache:
        candidate = os.path.join(
            cache, "piano", "note_F1=0.9677_pedal_F1=0.9186.pth"
        )
        if os.path.exists(candidate):
            ckpt = candidate

    with contextlib.redirect_stdout(sys.stderr):
        from piano_transcription_inference import PianoTranscription

        result = None
        for device in (_crepe_device(), "cpu"):  # env device, then CPU fallback
            try:
                pt = PianoTranscription(device=device, checkpoint_path=ckpt)
                # The library only moves the model to GPU when the device string
                # contains "cuda" (inference.py:57); for "mps" it silently stays on
                # CPU. forward() reads the model's param device and moves inputs to
                # match, so a manual .to() is enough to actually run on mps (~1.75x
                # faster here). Guard it so an unsupported op falls back to CPU.
                if device != "cpu":
                    pt.model.to(device)
                result = pt.transcribe(y, None)
                break
            except Exception:
                if device == "cpu":
                    raise
    return _piano_events_to_notes(result["est_note_events"])
