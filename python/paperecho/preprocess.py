"""Audio conditioning applied to a separated stem before transcription.

A separated stem still carries low-level bleed and reverb tails between the real
notes; pitch trackers hear those as ghost notes. A conservative noise gate
silences the quiet stretches while leaving the actual notes (and their tails)
untouched.
"""

from __future__ import annotations

_FRAME_S = 0.01  # 10 ms analysis frames
_ATTACK_S = 0.04  # open the gate this long BEFORE the attack so its transient survives
_HOLD_S = 0.12  # keep the gate open this long after the last loud frame
_SMOOTH_S = 0.03  # fade the gate over ~30 ms to avoid clicks


def noise_gate(y, sr: int):
    """Return `y` with quiet (below-noise-floor) stretches gated to silence.

    Conservative by design: the threshold sits just above the estimated noise
    floor and well below the peaks, and note tails are held, so real notes —
    including soft ones — survive.
    """
    import librosa
    import numpy as np

    if y.size == 0:
        return y

    hop = max(1, int(sr * _FRAME_S))
    rms = librosa.feature.rms(y=y, frame_length=hop * 4, hop_length=hop, center=True)[0]
    if rms.size == 0 or float(np.max(rms)) <= 0:
        return y

    # Peak-relative threshold only (~-28 dB). A percentile "noise floor" fails on
    # continuously-playing parts (guitar/piano) where the quiet frames are still
    # real signal — that gated ~97% of the guitar. This only removes content far
    # below the loudest notes, i.e. inter-note bleed, leaving real notes intact.
    peak = float(np.max(rms))
    threshold = peak * 0.04

    above = rms > threshold
    hold_frames = max(1, int(_HOLD_S / _FRAME_S))
    attack_frames = max(1, int(_ATTACK_S / _FRAME_S))
    gate = np.zeros_like(rms)
    countdown = 0
    for i, is_above in enumerate(above):
        if is_above:
            countdown = hold_frames
        if countdown > 0:
            gate[i] = 1.0
            countdown -= 1

    # Open the gate a few frames BEFORE each rising edge. Without this, the
    # smoothing below centres its fade on the attack frame and eats ~15 ms of the
    # note's transient — exactly the pick/pluck attack that onset detection (and
    # the ear) relies on. Pre-rolling the open by > the smoothing half-width lets
    # the fade complete before the real attack, so the transient passes untouched.
    if attack_frames:
        rolled = gate.copy()
        for shift in range(1, attack_frames + 1):
            rolled[:-shift] = np.maximum(rolled[:-shift], gate[shift:])
        gate = rolled

    # Fade the gate edges to avoid clicks, then stretch to sample resolution.
    k = max(1, int(_SMOOTH_S / _FRAME_S))
    gate = np.convolve(gate, np.ones(k) / k, mode="same")
    gate = np.clip(gate, 0.0, 1.0)
    gate_samples = np.interp(
        np.arange(len(y)), np.arange(len(gate)) * hop, gate
    ).astype(y.dtype)
    return (y * gate_samples).astype(y.dtype)
