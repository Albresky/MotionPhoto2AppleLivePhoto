"""Service layer between FastAPI and the underlying ``mvimg2livephoto`` package.

Responsibilities per scanned file:
1.  classify kind (motion_photo / still_image / video / unknown)
2.  compute sha256 for resume detection
3.  route to the right handler:
        - motion_photo → convert_one()
        - still_image  → shutil.copy2 (preserves mtime, EXIF, everything)
        - video        → os.symlink (mirror input path in output dir)
        - unknown      → shutil.copy2
4.  emit progress events to the queue manager
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

# Import the existing conversion library lazily — keeps this module importable
# in environments where ``mvimg2livephoto``'s heavy runtime deps (ffmpeg,
# exiftool, pillow-heif) aren't installed.  Pure-logic tests can then run
# without the full stack.
from .models import FileKind, FileStatus, JobResult

logger = logging.getLogger(__name__)


def _convert_one(source, output_dir, stem=None, *, hdr=False, enc_preset="ultrafast"):
    """Lazy wrapper around ``mvimg2livephoto.builder.convert_one``."""
    from mvimg2livephoto.builder import convert_one as _impl
    return _impl(source, output_dir, stem=stem, hdr=hdr, enc_preset=enc_preset)


def _parse_motion_photo(path):
    """Lazy wrapper around ``mvimg2livephoto.parser.parse_motion_photo``."""
    from mvimg2livephoto.parser import parse_motion_photo as _impl
    return _impl(path)

# Fast file-type detection by extension.  Case-insensitive.
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".heic", ".png", ".gif", ".webp", ".bmp"}
# Heuristic chunk size for hashing — 1 MiB.  Streaming hash keeps memory
# bounded even for large videos.
_HASH_CHUNK = 1 << 20


class ClassifyError(Exception):
    """Raised when a file cannot be classified."""


# ----------------------------------------------------------------- classify


def classify(path: Path) -> FileKind:
    """Decide what kind of file this is without reading the whole file.

    Order of checks:
      1. extension → video
      2. extension → image (then probe XMP for motion photo markers)
      3. otherwise  → unknown (will be copied)
    """
    ext = path.suffix.lower()
    if ext in _VIDEO_EXTS:
        return FileKind.VIDEO
    if ext in _IMAGE_EXTS:
        # Only JPG/JPEG can be Android Motion Photos (XMP + appended mp4).
        # HEIC from iPhone is already Live Photo — handled separately if ever
        # needed; for now treat as still_image.
        if ext in {".jpg", ".jpeg"}:
            if _is_motion_photo(path):
                return FileKind.MOTION_PHOTO
        return FileKind.STILL_IMAGE
    return FileKind.UNKNOWN


def _is_motion_photo(path: Path) -> bool:
    """True if the file parses as a Motion Photo (GCamera XMP markers).

    ``parse_motion_photo`` reads the whole file into memory; that's fine for
    phone photos (~10 MB) but we wrap exceptions to be defensive.
    """
    try:
        _parse_motion_photo(path)
        return True
    except ValueError:
        return False
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("classify failed for %s: %s", path, exc)
        return False


# ----------------------------------------------------------------- hashing


def sha256_of(path: Path) -> str:
    """Stream ``path`` through sha256, returning the hex digest.

    Used for resume detection: same path + same hash = skip conversion.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------- routing


@dataclass
class _HandlerContext:
    input_path: Path
    output_dir: Path
    kind: FileKind
    rel_path: Path  # path relative to input root — used to mirror structure


def output_for(ctx: _HandlerContext, suffix: str) -> Path:
    """Compute output path that mirrors the input's relative directory.

    The caller sets ``rel_path`` to the path relative to the scanned input
    root.  Output is ``<output_dir>/<rel_dir>/<name><suffix>``.
    """
    return ctx.output_dir / ctx.rel_path.parent / f"{ctx.rel_path.stem}{suffix}"


def process_file(
    ctx: _HandlerContext,
    sha: str,
    *,
    enc_preset: str = "ultrafast",
    symlink_fallback: bool = False,
) -> JobResult:
    """Run the right handler for ``ctx.kind`` and return a JobResult.

    This function is the unit of work consumed by the queue.  It does not
    raise — failures become ``status=FAILED`` with an error message so the
    batch continues.

    ``enc_preset`` is the x265 encoder speed preset, passed through to
    ``convert_one`` for Motion Photo files.  Ignored for other kinds.

    ``symlink_fallback``: when True, still_image / unknown / video files are
    symlinked to the source instead of being copied.  Also, if a Motion
    Photo conversion FAILS, the original file is symlinked as output so the
    user still has a usable file.  When False (default), still_image and
    unknown files are copied with ``shutil.copy2`` (preserves mtime/EXIF).
    """
    start = time.monotonic()
    status = FileStatus.FAILED
    heic_path: Path | None = None
    mov_path: Path | None = None
    output_paths: list[Path] = []
    error: str | None = None

    def _link_or_copy_output():
        """Mirror input, preferring symlink, then hardlink, then copy."""
        out = output_for(ctx, ctx.input_path.suffix)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.is_symlink() or out.exists():
            out.unlink()
        try:
            os.symlink(ctx.input_path, out)
            return [out]
        except OSError as symlink_exc:
            logger.info(
                "Symlink failed for %s -> %s; trying hardlink: %s",
                ctx.input_path,
                out,
                symlink_exc,
            )
        try:
            os.link(ctx.input_path, out)
            return [out]
        except OSError as hardlink_exc:
            logger.info(
                "Hardlink failed for %s -> %s; copying: %s",
                ctx.input_path,
                out,
                hardlink_exc,
            )
        shutil.copy2(ctx.input_path, out)
        return [out]

    try:
        if ctx.kind == FileKind.MOTION_PHOTO:
            status = FileStatus.CONVERTING
            result = _convert_one(ctx.input_path, output_for(ctx, "").parent,
                                  stem=ctx.rel_path.stem, hdr=False,
                                  enc_preset=enc_preset)
            heic_path = result.heic_path
            mov_path = result.mov_path
            output_paths = [result.heic_path, result.mov_path]
            status = FileStatus.DONE

        elif ctx.kind in (FileKind.STILL_IMAGE, FileKind.UNKNOWN):
            status = FileStatus.COPYING
            if symlink_fallback:
                output_paths = _link_or_copy_output()
            else:
                # Use the same filename, preserving extension.
                out = output_for(ctx, ctx.input_path.suffix)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ctx.input_path, out)
                output_paths = [out]
            status = FileStatus.DONE

        elif ctx.kind == FileKind.VIDEO:
            status = FileStatus.COPYING
            # Videos are always symlinked — they're too large to copy
            # and the content is never modified.
            output_paths = _link_or_copy_output()
            status = FileStatus.DONE

        else:  # pragma: no cover — classify never returns other kinds
            raise ClassifyError(f"Unknown kind: {ctx.kind}")

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.error("Failed %s: %s", ctx.input_path, error)
        status = FileStatus.FAILED
        # Fallback: if a motion photo conversion failed and
        # symlink_fallback is on, symlink the original so the user still
        # has a usable output file.
        if symlink_fallback and ctx.kind == FileKind.MOTION_PHOTO:
            try:
                output_paths = _link_or_copy_output()
                # Status remains FAILED so the user knows conversion didn't
                # happen — but we still provide a usable symlinked output.
                logger.info("Mirrored failed motion photo: %s", ctx.input_path)
            except Exception:
                pass

    return JobResult(
        path=ctx.input_path,
        kind=ctx.kind,
        status=status,
        heic_path=heic_path,
        mov_path=mov_path,
        output_paths=output_paths,
        error=error,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


def make_context(input_root: Path, file_path: Path, output_dir: Path) -> _HandlerContext:
    """Build a context with the correct relative path for mirror output."""
    try:
        rel = file_path.relative_to(input_root)
    except ValueError:
        # file_path not under input_root (e.g. user picked individual files)
        rel = Path(file_path.name)
    return _HandlerContext(
        input_path=file_path,
        output_dir=output_dir,
        kind=classify(file_path),
        rel_path=rel,
    )
