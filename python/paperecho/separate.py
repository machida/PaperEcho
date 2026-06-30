"""Source separation via Demucs (htdemucs_6s -> 6 stems).

Targets the demucs 4.0.1 API (pretrained.get_model + apply.apply_model); the
higher-level demucs.api module does not exist in this version.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

# htdemucs_6s yields exactly the six parts in the product spec.
STEMS = ["drums", "bass", "other", "vocals", "guitar", "piano"]

# Stems we hand to the pitch transcriber. Drums are audio-only in the MVP and
# `other` is a residual bucket not worth scoring.
PITCHED_STEMS = ["bass", "vocals", "guitar", "piano"]

_MODEL = "htdemucs_6s"


def _shifts() -> int:
    """Demucs test-time augmentation: average predictions over N random time
    shifts. N>0 noticeably cleans up transients/artefacts (htdemucs_6s smears
    guitar/piano attacks), at ~(1+N)x the separation time. Default 2; override
    with PAPER_ECHO_SHIFTS (e.g. 0 for fastest, 1 for a lighter quality bump)."""
    try:
        return max(0, int(os.environ.get("PAPER_ECHO_SHIFTS", "2")))
    except ValueError:
        return 2


def _device() -> str:
    # Prefer a GPU when available — full-song CPU separation is painfully slow.
    # Shared policy in paperecho.device; `separate()` still retries on CPU.
    from .device import resolve_device

    return resolve_device()


def separate(
    src_wav: str | Path,
    stems_dir: str | Path,
    on_progress: Callable[[float, str], None] | None = None,
) -> dict[str, str]:
    """Run Demucs and write one wav per stem. Returns {stem_name: path}.

    The model (~hundreds of MB) is downloaded and cached by Demucs on first run.
    """
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.audio import convert_audio
    from demucs.pretrained import get_model

    stems_dir = Path(stems_dir)
    stems_dir.mkdir(parents=True, exist_ok=True)

    model = get_model(_MODEL)
    model.eval()
    device = _device()
    shifts = _shifts()

    # Read with soundfile, NOT demucs.AudioFile: AudioFile shells out to ffmpeg
    # AND ffprobe, but we only bundle a static ffmpeg (no ffprobe) — that path
    # fails on a clean machine. `src_wav` is always a plain PCM WAV we wrote
    # (`audio.decode_to_wav`), so soundfile reads it directly; demucs's pure-torch
    # `convert_audio` (julius, no subprocess) matches the model's rate/channels.
    data, in_sr = sf.read(str(src_wav), dtype="float32", always_2d=True)  # (frames, ch)
    wav = torch.from_numpy(data.T.copy())  # -> (channels, frames)
    wav = convert_audio(wav, in_sr, model.samplerate, model.audio_channels)
    ref = wav.mean(0)
    wav = (wav - ref.mean()) / (ref.std() + 1e-8)

    # apply_model (demucs 4.0.1) has no progress callback, so we bracket the
    # long call with coarse milestones rather than per-segment updates.
    if on_progress:
        msg = "Separating stems (this can take a while)"
        if shifts:
            msg = f"Separating stems (shifts={shifts}, higher quality, slower)"
        on_progress(15.0, msg)

    with torch.no_grad():
        try:
            sources = apply_model(
                model, wav[None], device=device, shifts=shifts, progress=False
            )[0]
        except Exception:
            if device == "cpu":
                raise
            # e.g. an MPS/CUDA op is unsupported — fall back to CPU.
            sources = apply_model(
                model, wav[None], device="cpu", shifts=shifts, progress=False
            )[0]
    sources = sources * (ref.std() + 1e-8) + ref.mean()

    if on_progress:
        on_progress(90.0, "Writing stems")

    out: dict[str, str] = {}
    for name, source in zip(model.sources, sources, strict=False):
        path = stems_dir / f"{name}.wav"
        # soundfile wants (frames, channels); demucs gives (channels, frames).
        data = source.t().cpu().numpy()
        sf.write(str(path), data, model.samplerate, subtype="PCM_16")
        out[name] = str(path)

    if on_progress:
        on_progress(100.0, "Separation complete")
    return out
