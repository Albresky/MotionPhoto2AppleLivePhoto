"""Scan Xiaomi MVIMG files and report Motion Photo detection coverage.

Default:
    python scripts/scan_motion_photo_coverage.py

The scanner recursively checks files under F:\\xiaomi whose names start with
MVIMG_, skipping .mov and .heic files. Use --all-files to scan every filename.
Results are written to CSV.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mvimg2livephoto.parser import parse_motion_photo


SKIP_SUFFIXES = {".mov", ".heic"}


def iter_candidates(root: Path, *, mvimg_only: bool = True) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if mvimg_only and not path.name.startswith("MVIMG_"):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path


def image_resolution(path: Path) -> tuple[int | None, int | None, str | None]:
    try:
        with Image.open(path) as image:
            return image.width, image.height, None
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}"


def scan_one(path: Path) -> dict[str, object]:
    stat = path.stat()
    width, height, resolution_error = image_resolution(path)

    is_motion_photo = False
    parser_error = ""
    mp4_start: int | None = None
    mp4_length: int | None = None
    primary_jpeg_end: int | None = None
    presentation_timestamp_us: int | None = None
    segment_summary = ""

    try:
        layout = parse_motion_photo(path)
        is_motion_photo = True
        mp4_start = layout.mp4_start
        mp4_length = layout.mp4_length
        primary_jpeg_end = layout.primary_jpeg_end
        presentation_timestamp_us = layout.presentation_timestamp_us
        segment_summary = ";".join(
            f"{segment.semantic}:{segment.mime}:{segment.length}:{segment.padding}"
            for segment in layout.segments
        )
    except Exception as exc:
        parser_error = f"{type(exc).__name__}: {exc}"

    return {
        "path": str(path.resolve()),
        "file_name": path.name,
        "suffix": path.suffix.lower(),
        "size_bytes": stat.st_size,
        "width": width or "",
        "height": height or "",
        "resolution": f"{width}x{height}" if width and height else "",
        "is_motion_photo": "yes" if is_motion_photo else "no",
        "mp4_start": mp4_start if mp4_start is not None else "",
        "mp4_length": mp4_length if mp4_length is not None else "",
        "primary_jpeg_end": primary_jpeg_end if primary_jpeg_end is not None else "",
        "presentation_timestamp_us": (
            presentation_timestamp_us if presentation_timestamp_us is not None else ""
        ),
        "segments": segment_summary,
        "parser_error": parser_error,
        "resolution_error": resolution_error or "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan MVIMG_* files and write Motion Photo detection coverage to CSV."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(r"F:\xiaomi"),
        help=r"Directory to scan recursively. Default: F:\xiaomi",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="CSV output path. Default: motion_photo_coverage_<timestamp>.csv",
    )
    parser.add_argument(
        "--all-files",
        action="store_true",
        help="Scan all filenames instead of only files whose names start with MVIMG_.",
    )
    args = parser.parse_args()

    root = args.root
    if not root.exists():
        raise SystemExit(f"Root directory does not exist: {root}")
    if not root.is_dir():
        raise SystemExit(f"Root path is not a directory: {root}")

    output = args.output
    if output is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = Path(f"motion_photo_coverage_{timestamp}.csv")
    output.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "path",
        "file_name",
        "suffix",
        "size_bytes",
        "width",
        "height",
        "resolution",
        "is_motion_photo",
        "mp4_start",
        "mp4_length",
        "primary_jpeg_end",
        "presentation_timestamp_us",
        "segments",
        "parser_error",
        "resolution_error",
    ]

    total = 0
    motion = 0
    with output.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for path in iter_candidates(root, mvimg_only=not args.all_files):
            row = scan_one(path)
            total += 1
            if row["is_motion_photo"] == "yes":
                motion += 1
            writer.writerow(row)
            if total % 100 == 0:
                print(f"Scanned {total} files, motion={motion}, output={output}")

    print(f"Done. Scanned {total} files, motion={motion}, non_motion={total - motion}")
    print(f"CSV: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
