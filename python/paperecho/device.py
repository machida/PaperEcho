"""Shared compute-device selection for the torch-based stages.

Separation (Demucs), CREPE pitch, and the ByteDance piano model all want the
same policy: honour a `PAPER_ECHO_DEVICE` override, otherwise auto-prefer a GPU
(Apple MPS / CUDA) and fall back to CPU. Callers should still guard the actual
device op and retry on CPU, since some kernels are unsupported on MPS.
"""

from __future__ import annotations

import os


def resolve_device() -> str:
    """Return a torch device string: ``"cpu"`` | ``"mps"`` | ``"cuda"``.

    `PAPER_ECHO_DEVICE` (cpu/mps/cuda) overrides; an override naming a device
    that isn't available degrades to CPU. When unset, auto-detect a GPU.
    """
    import torch

    override = os.environ.get("PAPER_ECHO_DEVICE")
    if override:
        dev = override.lower()
        if dev == "mps" and torch.backends.mps.is_available():
            return "mps"
        if dev == "cuda" and torch.cuda.is_available():
            return "cuda"
        return "cpu"
    try:
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"
