#!/usr/bin/env bash
#
# Stage a self-contained, bundle-able copy of the Python pipeline into
#   src-tauri/resources-<arch>/python
# for inclusion in the macOS app bundle. See DISTRIBUTION.md.
#
# Requires: uv. Run from the repo root:  ./scripts/build-python-dist.sh arm64
#
# A normal `python/.venv` is NOT bundle-able: its bin/python symlinks the dev
# toolchain (uv's managed Python lives outside the venv). Instead we COPY a
# uv-managed *standalone* CPython (python-build-standalone, fully relocatable)
# into the bundle and install the locked deps into that copy — so nothing points
# outside the staged tree. ALWAYS run the printed verify step on the result.
set -euo pipefail

ARCH="${1:-arm64}"            # arm64 | x86_64
PYVER="3.11"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/src-tauri/resources-$ARCH/python"
# Override to cross-build (e.g. x86_64 on Apple Silicon):
#   UV="arch -x86_64 /path/to/x86_64/uv" ./scripts/build-python-dist.sh x86_64
UV="${UV:-uv}"

echo ">> staging Python pipeline for $ARCH -> $DEST"
rm -rf "$DEST"
mkdir -p "$DEST"

# 1. the package (imported from cwd at runtime) + lockfile
cp -R "$ROOT/python/paperecho" "$DEST/paperecho"
cp "$ROOT/python/pyproject.toml" "$ROOT/python/uv.lock" "$DEST/"

# 2. copy a self-contained standalone CPython INTO the bundle
$UV python install "cpython-$PYVER" >/dev/null
STANDALONE="$($UV python find "$PYVER")"                 # .../bin/python3.11
SRC_ROOT="$(cd "$(dirname "$STANDALONE")/.." && pwd)"   # the install root
cp -R "$SRC_ROOT" "$DEST/runtime"
RUNTIME_PY="$DEST/runtime/bin/python3.11"
# uv marks its standalone pythons "externally managed"; this is our private copy,
# so drop the marker to allow installing the app's deps into it.
rm -f "$DEST/runtime/lib/python$PYVER/EXTERNALLY-MANAGED"

# 3. install the locked deps into that copied interpreter (project excluded —
#    paperecho is imported from cwd, not installed)
( cd "$DEST" && $UV export --frozen --no-dev --no-emit-project --no-hashes \
    --format requirements-txt -o requirements-dist.txt )
$UV pip install --python "$RUNTIME_PY" --break-system-packages \
    -r "$DEST/requirements-dist.txt"
rm -f "$DEST/requirements-dist.txt"

# 4. trim bytecode caches
find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# 5. slim: drop large payloads the pipeline never loads (~170 MB). Each deletion
#    is verified safe against the import path (see DISTRIBUTION.md "Slim"):
#    - music21/corpus DATA dirs: we never parse the bundled corpus, but the
#      `corpus` PACKAGE (its *.py) is imported at music21 init, so keep the code
#      and delete only the score-data subdirs + metadata cache.
#    - torchcrepe/assets/full.pth: transcribe.py pins CREPE model="tiny".
#    - coremltools: basic_pitch then resolves its model to nmp.onnx (onnxruntime
#      is present); its __init__ guards the import, so import still works.
#    KEEP matplotlib (piano_transcription_inference eager-imports pyplot) and
#    sympy/numba/llvmlite/scipy (torch / librosa load-bearing).
SP="$DEST/runtime/lib/python$PYVER/site-packages"
if [ -d "$SP/music21/corpus" ]; then
    find "$SP/music21/corpus" -mindepth 1 -maxdepth 1 -type d \
        ! -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
fi
rm -f  "$SP/torchcrepe/assets/full.pth"
rm -rf "$SP"/coremltools "$SP"/coremltools-*.dist-info

# 5.5 stage AI model weights into the runtime so a FIRST ANALYSE never downloads
#     (see DISTRIBUTION.md "Models"). The cache travels inside the tarball at
#     `python/model-cache/`; python.rs sets TORCH_HOME + PAPER_ECHO_MODEL_CACHE to
#     point the libs at it. Demucs (htdemucs_6s) + beat_this both resolve via
#     torch.hub ($TORCH_HOME/hub/checkpoints); the ByteDance piano model takes an
#     explicit checkpoint_path (transcribe_piano reads PAPER_ECHO_MODEL_CACHE).
#     CREPE-tiny ships inside torchcrepe (no download), so it needs no staging.
MC="$DEST/model-cache"
CKPTS="$MC/torch/hub/checkpoints"
mkdir -p "$CKPTS" "$MC/piano"

# Reuse the build machine's caches when present (fast, deterministic); otherwise
# the staged interpreter fetches them via torch.hub into the same place below.
HOST_CKPTS="$HOME/.cache/torch/hub/checkpoints"
for f in 5c90dfd2-34c22ccb.th beat_this-final0.ckpt; do
    [ -f "$HOST_CKPTS/$f" ] && cp -n "$HOST_CKPTS/$f" "$CKPTS/$f" 2>/dev/null || true
done

echo ">> staging model weights (demucs htdemucs_6s + beat_this via torch.hub) ..."
TORCH_HOME="$MC/torch" "$RUNTIME_PY" - <<'PY'
import sys
from demucs.pretrained import get_model
get_model("htdemucs_6s")              # -> $TORCH_HOME/hub/checkpoints/<sig>.th
from beat_this.inference import File2Beats
File2Beats(device="cpu")              # -> $TORCH_HOME/hub/checkpoints/beat_this-final0.ckpt
print("model weights staged", file=sys.stderr)
PY

# ByteDance piano checkpoint — explicit path (the lib otherwise wgets it to ~).
PIANO_CKPT="$MC/piano/note_F1=0.9677_pedal_F1=0.9186.pth"
HOST_PIANO="$HOME/piano_transcription_inference_data/note_F1=0.9677_pedal_F1=0.9186.pth"
if [ ! -f "$PIANO_CKPT" ]; then
    if [ -f "$HOST_PIANO" ]; then
        cp "$HOST_PIANO" "$PIANO_CKPT"
    else
        echo ">> downloading ByteDance piano checkpoint from Zenodo ..."
        curl -fL --retry 3 -o "$PIANO_CKPT" \
          "https://zenodo.org/record/4034264/files/CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1"
    fi
fi
echo ">> model-cache staged: $(du -sh "$MC" | cut -f1)"

echo ">> staged $(du -sh "$DEST" | cut -f1) at $DEST"

# 6. pack the runtime as a versioned zstd tarball + sha256, for first-run
#    download (see runtime.rs / DISTRIBUTION.md). The app NO LONGER bundles
#    `python/`; it bundles only `runtime.sha256` and fetches this tarball.
command -v zstd >/dev/null || { echo "ERROR: zstd not found (brew install zstd)"; exit 1; }
VER="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        "$ROOT/src-tauri/tauri.conf.json" | head -1)"
[ -n "$VER" ] || { echo "ERROR: could not read app version from tauri.conf.json"; exit 1; }
DIST="$ROOT/dist-runtime"
mkdir -p "$DIST"
TARBALL="$DIST/paperecho-runtime-$VER-$ARCH.tar.zst"
echo ">> packing $TARBALL (zstd -19) ..."
# tar the top-level `python/` dir (runtime.rs extracts it back as `python/`).
tar -C "$ROOT/src-tauri/resources-$ARCH" -cf - python \
    | zstd -19 -T0 -q -o "$TARBALL" -f
# sha256 -> sidecar (for the release) AND the bundled resource (the app's source
# of truth for verifying the download).
SHA="$(shasum -a 256 "$TARBALL" | awk '{print $1}')"
echo "$SHA" > "$TARBALL.sha256"
echo "$SHA" > "$ROOT/src-tauri/resources-$ARCH/runtime.sha256"

echo ">> built $(du -sh "$TARBALL" | cut -f1) tarball"
echo ">>   sha256: $SHA"
echo ">>   bundled resource: src-tauri/resources-$ARCH/runtime.sha256"
echo ">> VERIFY the runtime runs detached from the dev toolchain (no PATH leakage):"
echo "     env -i HOME=\"\$HOME\" \"$RUNTIME_PY\" -m paperecho.pipeline --help"
echo ">> NEXT: upload to GitHub Releases tag v$VER as the asset:"
echo "     $(basename "$TARBALL")"
echo "   (matches runtime.rs default URL .../v$VER/$(basename "$TARBALL"))"
