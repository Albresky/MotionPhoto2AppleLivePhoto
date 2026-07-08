"""Convert MP4 bytes to Apple-compatible MOV using ffmpeg (stream copy, no re-encode)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def _find_ffmpeg() -> str:
    """Return the path to ffmpeg, raising EnvironmentError if not found."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise EnvironmentError(
            "ffmpeg not found. Install with: brew install ffmpeg"
        )
    return ffmpeg


def mp4_bytes_to_mov(mp4_bytes: bytes) -> bytes:
    """Convert MP4 bytes to MOV bytes via ffmpeg stream copy.

    Uses a temp directory; no files are left on disk after return.
    Raises subprocess.CalledProcessError on ffmpeg failure.
    """
    ffmpeg = _find_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input.mp4"
        dst = Path(tmp) / "output.mov"
        src.write_bytes(mp4_bytes)
        _run_ffmpeg_copy(ffmpeg, src, dst)
        return dst.read_bytes()


def mp4_file_to_mov_file(src: Path, dst: Path) -> None:
    """Convert an MP4 file to a MOV file via ffmpeg stream copy.

    dst is created/overwritten. Raises on ffmpeg failure.
    """
    ffmpeg = _find_ffmpeg()
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg_copy(ffmpeg, src, dst)


def _run_ffmpeg_copy(ffmpeg: str, src: Path, dst: Path) -> None:
    """Run ffmpeg -i src -c copy dst (MOV container, stream copy)."""
    cmd = [
        ffmpeg,
        "-y",           # overwrite output
        "-i", str(src),
        "-c", "copy",   # no re-encode
        "-f", "mov",    # force MOV container
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd,
            output=result.stdout,
            stderr=result.stderr,
        )
