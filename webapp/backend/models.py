"""Pydantic schemas for the web API.

These models are the contract between the React frontend and the FastAPI
backend.  Keep them small and explicit — the frontend mirrors them in
``frontend/src/types.ts``.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class FileKind(str, Enum):
    """What kind of file the scanner decided this is."""

    MOTION_PHOTO = "motion_photo"   # Android MVIMG — will convert
    STILL_IMAGE = "still_image"     # JPG/HEIC/PNG without motion — copy as-is
    VIDEO = "video"                 # .mp4/.mov — symlink as-is
    UNKNOWN = "unknown"             # anything else — copy as-is, warn


class FileStatus(str, Enum):
    """Lifecycle of a file in the queue."""

    PENDING = "pending"
    HASHING = "hashing"
    QUEUED = "queued"
    CONVERTING = "converting"
    COPYING = "copying"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"   # already converted before (resume)


class ScanItem(BaseModel):
    """One file discovered during scan."""

    path: Path
    kind: FileKind
    size: int
    mtime: float


class ScanResponse(BaseModel):
    items: list[ScanItem]
    total: int


class ConvertRequest(BaseModel):
    """Request body for POST /api/convert.

    ``items`` is the list of scanned files to process.  The frontend usually
    sends the whole scan result; the backend re-checks each file's kind and
    resume status.

    ``enc_preset`` controls the x265 encoder speed preset passed through to
    ``jpeg_to_heic()``.  See ``metadata.jpeg_to_heic`` for available options.

    ``symlink_fallback``: when True, files that are NOT motion photos (still
    images, videos, unknown) and motion photos whose conversion FAILED are
    output as symlinks to the original file instead of being copied.
    Default False (copy2 preserves metadata).
    """

    items: list[ScanItem]
    output_dir: Path
    workers: int = Field(default=4, ge=1, le=32)
    hdr: bool = False
    enc_preset: str = "ultrafast"
    symlink_fallback: bool = False


class JobResult(BaseModel):
    """Result of a single file after processing."""

    path: Path
    kind: FileKind
    status: FileStatus
    heic_path: Optional[Path] = None
    mov_path: Optional[Path] = None
    # All output files produced (for still_image: the copied file;
    # for video: the symlink; for motion_photo: [HEIC, MOV]).
    # Front-end uses this to show the "output column" uniformly.
    output_paths: list[Path] = Field(default_factory=list)
    error: Optional[str] = None
    duration_ms: int = 0


class ProgressEvent(BaseModel):
    """WebSocket message pushed to the frontend for each file state change."""

    path: str
    name: str
    status: FileStatus
    kind: FileKind
    completed: int
    total: int
    error: Optional[str] = None
    duration_ms: int = 0
    heic_path: Optional[str] = None
    mov_path: Optional[str] = None
    output_paths: list[str] = Field(default_factory=list)


class SummaryResponse(BaseModel):
    """Final summary after a batch finishes."""

    total: int
    done: int
    failed: int
    skipped: int
    results: list[JobResult]
