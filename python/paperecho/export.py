"""Render a score/stem into the user-selected output formats."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import audio

# Output formats offered in the Export screen.
FORMATS = ["musicxml", "midi", "wav", "mp3", "pdf"]
_EXT = {"musicxml": ".musicxml", "midi": ".mid", "wav": ".wav", "mp3": ".mp3", "pdf": ".pdf"}


def _musescore_bin() -> str | None:
    for name in ("mscore", "musescore", "MuseScore4", "MuseScore3"):
        found = shutil.which(name)
        if found:
            return found
    # Standard macOS install location.
    mac = Path("/Applications/MuseScore 4.app/Contents/MacOS/mscore")
    return str(mac) if mac.exists() else None


def export_part(
    part: str,
    fmt: str,
    out_dir: str | Path,
    score=None,
    stem_wav: str | Path | None = None,
) -> dict:
    """Write one (part, format) artifact. Returns {path, format, part, skipped?}.

    `score` is required for musicxml/midi/pdf; `stem_wav` for wav/mp3/pdf-fallback.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{part}{_EXT[fmt]}"

    if fmt == "musicxml":
        score.write("musicxml", fp=str(dst))
    elif fmt == "midi":
        score.write("midi", fp=str(dst))
    elif fmt in ("wav", "mp3"):
        if stem_wav is None:
            return {"part": part, "format": fmt, "skipped": "no stem audio"}
        audio.encode(stem_wav, dst)
    elif fmt == "pdf":
        ms = _musescore_bin()
        if ms is None or score is None:
            return {"part": part, "format": fmt, "skipped": "MuseScore not found"}
        xml = out_dir / f"{part}.musicxml"
        if not xml.exists():
            score.write("musicxml", fp=str(xml))
        import subprocess
        proc = subprocess.run(
            [ms, "-o", str(dst), str(xml)],
            capture_output=True, text=True, errors="replace",
        )
        if proc.returncode != 0:
            return {"part": part, "format": fmt, "skipped": "MuseScore export failed"}
    else:
        return {"part": part, "format": fmt, "skipped": f"unknown format {fmt}"}

    return {"part": part, "format": fmt, "path": str(dst)}
