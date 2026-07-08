"""Orchestrate the full MVIMG → Live Photo (HEIC + MOV) conversion pipeline."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .converter import mp4_file_to_mov_file
from .extractor import extract_media, save_extracted
from .metadata import (
    build_heic_exif,
    inject_content_id_to_mov,
    jpeg_to_heic,
    new_content_identifier,
    read_exif_from_jpeg,
)
from .parser import parse_motion_photo

logger = logging.getLogger(__name__)

# Default workers: min(CPU count, 4) — HEIC encoding is CPU-bound via x265;
# more than 4 workers typically saturates the encoder without extra throughput.
_DEFAULT_WORKERS = min(os.cpu_count() or 4, 4)


@dataclass
class ConversionResult:
    source: Path
    heic_path: Path
    mov_path: Path
    content_identifier: str
    hdr: bool = False  # True when output HEIC contains Apple HDR aux image


def convert_one(
    source: Path,
    output_dir: Path,
    stem: str | None = None,
    *,
    hdr: bool = False,
    enc_preset: str = "ultrafast",
) -> ConversionResult:
    """Convert a single Xiaomi Motion Photo to an Apple Live Photo pair.

    Always outputs <stem>.HEIC + <stem>.MOV.

    When hdr=False (default):
      SDR HEIC — compatible with all iPhones, HDR effect not preserved.

    When hdr=True:
      If the source has an embedded GainMap, the GainMap is injected into
      the HEIC as an Apple aux image (urn:com:apple:photo:2020:aux:hdrgainmap).
      iOS 13+ reads the aux image; HDR display requires iPhone 12+ with
      ProMotion and iOS 17+.
      If no GainMap is found, falls back to SDR silently.

    ``enc_preset`` is passed through to ``jpeg_to_heic()`` as the x265
    encoder speed preset.  See ``jpeg_to_heic()`` for available options.

    Raises ValueError if source is not a valid Motion Photo.
    Raises EnvironmentError if ffmpeg or exiftool is missing.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    if stem is None:
        stem = source.stem

    logger.info("Parsing %s", source.name)
    layout = parse_motion_photo(source)

    logger.info("Extracting media")
    media = extract_media(source)

    import tempfile
    with tempfile.TemporaryDirectory(dir=output_dir, prefix=".tmp_") as tmp:
        tmp_path = Path(tmp)
        _jpeg_path, mp4_path = save_extracted(media, tmp_path, stem=stem)

        logger.info("Converting MP4 → MOV")
        mov_out = output_dir / f"{stem}.MOV"
        mp4_file_to_mov_file(mp4_path, mov_out)

    content_id = new_content_identifier()
    photo_id = new_content_identifier()
    heic_out = output_dir / f"{stem}.HEIC"

    # Build base HEIC
    source_exif = read_exif_from_jpeg(source)
    heic_exif = build_heic_exif(source_exif, content_id, photo_id)
    jpeg_to_heic(media.jpeg_bytes, heic_exif, heic_out, enc_preset=enc_preset)

    out_hdr = False
    if hdr and _has_gainmap(layout):
        logger.info("Injecting HDR GainMap into HEIC")
        try:
            _inject_hdr_gainmap(source, layout, heic_out)
            out_hdr = True
        except Exception as e:
            logger.warning("HDR GainMap injection failed, keeping SDR: %s", e)

    logger.info("Injecting ContentIdentifier into MOV")
    inject_content_id_to_mov(mov_out, content_id)

    logger.info("Done: %s + %s%s", heic_out.name, mov_out.name,
                " [HDR]" if out_hdr else "")
    return ConversionResult(
        source=source,
        heic_path=heic_out,
        mov_path=mov_out,
        content_identifier=content_id,
        hdr=out_hdr,
    )


def _inject_hdr_gainmap(source: Path, layout, heic_out: Path) -> None:
    """Extract GainMap from MVIMG, convert pixels, encode, and inject into HEIC."""
    import io, re
    from PIL import Image
    from .hdr_injector import (
        convert_gainmap_pixels,
        encode_gainmap_as_hevc,
        inject_aux_image,
    )

    data = source.read_bytes()
    gainmap_seg = next(s for s in layout.segments if s.semantic == "GainMap")
    gm_start = layout.total_size - layout.mp4_length - gainmap_seg.length
    gainmap_bytes = data[gm_start:gm_start + gainmap_seg.length]

    # Parse GainMapMax from GainMap JPEG XMP
    xmp_start = gainmap_bytes.find(b"<x:xmpmeta")
    xmp_str = gainmap_bytes[xmp_start:xmp_start + 2000].decode("utf-8", errors="replace")
    m = re.search(r'GainMapMax=["\']([^"\']+)["\']', xmp_str)
    gainmap_max = float(m.group(1)) if m else 1.0

    gm_pil = Image.open(io.BytesIO(gainmap_bytes)).convert("L")
    apple_gm = convert_gainmap_pixels(gm_pil, gainmap_max)
    aux_hevc_data, aux_hvcc_box, aux_ispe_box = encode_gainmap_as_hevc(apple_gm)

    # inject_aux_image reads heic_out, modifies in-place via temp then overwrites
    import tempfile, shutil
    tmp_out = heic_out.with_suffix(".tmp.heic")
    inject_aux_image(heic_out, aux_hevc_data, aux_hvcc_box, aux_ispe_box, tmp_out)
    shutil.move(str(tmp_out), str(heic_out))


def _has_gainmap(layout) -> bool:
    """Return True if the Motion Photo has an embedded GainMap segment."""
    return any(s.semantic == "GainMap" and s.length > 0 for s in layout.segments)


def convert_batch(
    sources: list[Path],
    output_dir: Path,
    *,
    hdr: bool = False,
    enc_preset: str = "ultrafast",
    workers: int = _DEFAULT_WORKERS,
    on_progress: Optional[Callable[[int, int, Path], None]] = None,
    skip_errors: bool = True,
) -> tuple[list[ConversionResult], list[tuple[Path, Exception]]]:
    """Convert a list of Motion Photo files, processing up to `workers` files in parallel.

    hdr=False (default): SDR HEIC + MOV.
    hdr=True: HEIC with Apple HDR aux image + MOV (when GainMap available).

    ``enc_preset`` is passed to ``convert_one()`` as the x265 encoder speed.

    Returns (successes, failures).
    """
    successes: list[ConversionResult] = []
    failures: list[tuple[Path, Exception]] = []
    total = len(sources)
    completed = 0

    if workers <= 1 or total <= 1:
        for src in sources:
            try:
                result = convert_one(src, output_dir, hdr=hdr, enc_preset=enc_preset)
                successes.append(result)
            except Exception as e:
                if skip_errors:
                    logger.error("Failed %s: %s", src.name, e)
                    failures.append((src, e))
                else:
                    raise
            finally:
                completed += 1
                if on_progress:
                    on_progress(completed, total, src)
        return successes, failures

    def _task(src: Path):
        return convert_one(src, output_dir, hdr=hdr, enc_preset=enc_preset)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_src = {pool.submit(_task, src): src for src in sources}
        for future in as_completed(future_to_src):
            src = future_to_src[future]
            completed += 1
            try:
                result = future.result()
                successes.append(result)
            except Exception as e:
                if skip_errors:
                    logger.error("Failed %s: %s", src.name, e)
                    failures.append((src, e))
                else:
                    for f in future_to_src:
                        f.cancel()
                    raise
            if on_progress:
                on_progress(completed, total, src)

    return successes, failures


def find_motion_photos(directory: Path, recursive: bool = True) -> list[Path]:
    """Scan a directory for Xiaomi Motion Photo files (.jpg/.JPG with MVIMG_ prefix).

    Also detects non-prefixed Motion Photos by checking XMP (slower).
    Returns a sorted list of paths.
    """
    pattern = "**/*.jpg" if recursive else "*.jpg"
    candidates = sorted(directory.glob(pattern)) + sorted(directory.glob(
        pattern.replace(".jpg", ".JPG")))

    results = []
    for path in candidates:
        # Fast path: filename starts with MVIMG_
        if path.stem.upper().startswith("MVIMG_"):
            results.append(path)
            continue
        # Slow path: parse XMP
        try:
            parse_motion_photo(path)
            results.append(path)
        except ValueError:
            pass

    return results
