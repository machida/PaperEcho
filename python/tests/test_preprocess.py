"""Unit tests for the noise gate."""

import numpy as np

from paperecho.preprocess import noise_gate


def _rms(x):
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


def test_gate_silences_quiet_and_keeps_loud():
    sr = 16000
    t = np.arange(sr) / sr
    loud = (0.5 * np.sin(2 * np.pi * 110 * t)).astype(np.float32)  # 1s strong tone
    quiet = (0.003 * np.sin(2 * np.pi * 110 * t)).astype(np.float32)  # 1s bleed
    y = np.concatenate([loud, quiet])

    gated = noise_gate(y, sr)

    # Loud half stays; quiet bleed (well below peak) is strongly attenuated.
    assert _rms(gated[:sr]) > 0.3
    assert _rms(gated[sr + 4000:]) < _rms(quiet) * 0.3


def test_gate_leaves_loud_continuous_signal_intact():
    sr = 16000
    t = np.arange(3 * sr) / sr
    y = (0.4 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)  # always loud
    gated = noise_gate(y, sr)
    assert _rms(gated) > _rms(y) * 0.95  # essentially untouched


def test_gate_handles_empty():
    assert noise_gate(np.zeros(0, dtype=np.float32), 16000).size == 0
