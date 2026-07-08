"""Extract Primary JPEG and MP4 bytes from a Xiaomi Motion Photo."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .parser import MotionPhotoLayout, parse_motion_photo


@dataclass
class ExtractedMedia:
    """Raw bytes extracted from a Motion Photo."""
    jpeg_bytes: bytes   # Primary JPEG (image)
    mp4_bytes: bytes    # Embedded MP4 (video)
    source_path: Path


def extract_media(path: Path) -> ExtractedMedia:
    """Parse and extract Primary JPEG + MP4 from a Motion Photo file.

    Does not write any files — returns raw bytes for further processing.
    Raises ValueError if the file is not a valid Motion Photo.
    """
    layout = parse_motion_photo(path)
    return extract_media_with_layout(path, layout)


def extract_media_with_layout(path: Path, layout: MotionPhotoLayout) -> ExtractedMedia:
    """Extract media using a pre-parsed layout (avoids re-reading XMP)."""
    data = path.read_bytes()

    jpeg_bytes = data[:layout.primary_jpeg_end]
    mp4_bytes = data[layout.mp4_start:layout.mp4_start + layout.mp4_length]

    # Sanity checks
    if jpeg_bytes[:2] != b"\xff\xd8":
        raise ValueError("Extracted JPEG does not start with FF D8")
    if mp4_bytes[4:8] != b"ftyp":
        raise ValueError(f"Extracted MP4 does not start with ftyp box, got {mp4_bytes[4:8]!r}")

    return ExtractedMedia(jpeg_bytes=jpeg_bytes, mp4_bytes=mp4_bytes, source_path=path)


def save_extracted(media: ExtractedMedia, output_dir: Path,
                   stem: str | None = None) -> tuple[Path, Path]:
    """Save extracted JPEG and MP4 to output_dir.

    Returns (jpeg_path, mp4_path).
    stem defaults to the source filename stem.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    if stem is None:
        stem = media.source_path.stem

    jpeg_path = output_dir / f"{stem}.jpg"
    mp4_path = output_dir / f"{stem}.mp4"

    jpeg_path.write_bytes(media.jpeg_bytes)
    mp4_path.write_bytes(media.mp4_bytes)
    return jpeg_path, mp4_path
