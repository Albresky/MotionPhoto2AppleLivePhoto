"""Tests for metadata.py — EXIF preservation, MakerNote injection, ContentIdentifier."""

import io
import re
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import piexif
import pillow_heif
from mvimg2livephoto.metadata import (
    new_content_identifier,
    read_exif_from_jpeg,
    build_heic_exif,
    jpeg_to_heic,
    inject_content_id_to_mov,
    _build_apple_maker_note,
)
from mvimg2livephoto.extractor import extract_media
from mvimg2livephoto.converter import mp4_bytes_to_mov

MVIMG1 = Path("android_xiaomi_motion_photo/MVIMG_20260324_220411.jpg")
IPHONE_HEIC = Path("iphone_live_photo/IMG_1635.HEIC")
IPHONE_MOV = Path("iphone_live_photo/IMG_1635.MOV")

UUID_RE = re.compile(
    r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$"
)


def test_new_content_identifier():
    cid = new_content_identifier()
    assert UUID_RE.match(cid), f"Bad UUID format: {cid!r}"
    assert cid == cid.upper()
    # Generate two and verify they differ
    cid2 = new_content_identifier()
    assert cid != cid2
    print(f"  ContentIdentifier: {cid}")


def test_read_exif_preserves_timestamp():
    exif = read_exif_from_jpeg(MVIMG1)
    dt = exif["Exif"].get(piexif.ExifIFD.DateTimeOriginal, b"")
    assert dt, "DateTimeOriginal must be present"
    assert b"2026:03:24" in dt, f"Unexpected date: {dt}"
    print(f"  DateTimeOriginal: {dt}")


def test_read_exif_preserves_gps():
    exif = read_exif_from_jpeg(MVIMG1)
    gps = exif.get("GPS", {})
    assert gps, "GPS IFD must be present"
    assert piexif.GPSIFD.GPSLatitude in gps
    assert piexif.GPSIFD.GPSLongitude in gps
    print(f"  GPS lat keys present: {list(gps.keys())[:4]}")


def test_build_apple_maker_note():
    cid = "CEEE58DF-95FE-4D41-8B9A-F0AF1103A2DC"
    pid = "8D077A46-0D15-4781-9E17-7516A722A0DB"
    mn = _build_apple_maker_note(cid, pid)
    assert mn[:9] == b"Apple iOS"
    assert mn[12:14] == b"MM"  # big-endian
    # ContentIdentifier must be in the payload
    assert cid.encode("ascii") in mn, "ContentIdentifier not found in MakerNote"
    assert pid.encode("ascii") in mn, "PhotoIdentifier not found in MakerNote"
    print(f"  MakerNote size: {len(mn)} bytes")


def test_build_heic_exif_preserves_timestamps():
    source_exif = read_exif_from_jpeg(MVIMG1)
    cid = new_content_identifier()
    pid = new_content_identifier()
    exif_bytes = build_heic_exif(source_exif, cid, pid)
    rebuilt = piexif.load(exif_bytes)
    dt = rebuilt["Exif"].get(piexif.ExifIFD.DateTimeOriginal, b"")
    assert b"2026:03:24" in dt, f"Timestamp lost: {dt}"


def test_build_heic_exif_injects_maker_note():
    source_exif = read_exif_from_jpeg(MVIMG1)
    cid = new_content_identifier()
    pid = new_content_identifier()
    exif_bytes = build_heic_exif(source_exif, cid, pid)
    rebuilt = piexif.load(exif_bytes)
    mn = rebuilt["Exif"].get(piexif.ExifIFD.MakerNote, b"")
    assert mn[:9] == b"Apple iOS", f"MakerNote header wrong: {mn[:9]!r}"
    assert cid.encode("ascii") in mn, "ContentIdentifier not in MakerNote"


def test_jpeg_to_heic(tmp_path):
    media = extract_media(MVIMG1)
    source_exif = read_exif_from_jpeg(MVIMG1)
    cid = new_content_identifier()
    pid = new_content_identifier()
    exif_bytes = build_heic_exif(source_exif, cid, pid)
    out_heic = tmp_path / "output.HEIC"
    jpeg_to_heic(media.jpeg_bytes, exif_bytes, out_heic)

    assert out_heic.exists()
    assert out_heic.stat().st_size > 100_000

    # Verify it's a valid HEIC
    heif = pillow_heif.open_heif(str(out_heic))
    assert heif.size[0] > 0 and heif.size[1] > 0
    print(f"  Output HEIC: {out_heic.stat().st_size} bytes, size={heif.size}")


def test_heic_exif_roundtrip(tmp_path):
    """HEIC output must contain original EXIF timestamps."""
    media = extract_media(MVIMG1)
    source_exif = read_exif_from_jpeg(MVIMG1)
    cid = new_content_identifier()
    pid = new_content_identifier()
    exif_bytes = build_heic_exif(source_exif, cid, pid)
    out_heic = tmp_path / "output.HEIC"
    jpeg_to_heic(media.jpeg_bytes, exif_bytes, out_heic)

    heif = pillow_heif.open_heif(str(out_heic))
    heic_exif = piexif.load(heif.info.get("exif", b""))
    dt = heic_exif["Exif"].get(piexif.ExifIFD.DateTimeOriginal, b"")
    assert b"2026:03:24" in dt, f"Timestamp lost in HEIC: {dt}"


def test_inject_content_id_to_mov(tmp_path):
    media = extract_media(MVIMG1)
    mov_bytes = mp4_bytes_to_mov(media.mp4_bytes)
    mov_path = tmp_path / "output.mov"
    mov_path.write_bytes(mov_bytes)

    cid = new_content_identifier()
    inject_content_id_to_mov(mov_path, cid)

    # Verify via exiftool
    result = subprocess.run(
        ["exiftool", "-ContentIdentifier", str(mov_path)],
        capture_output=True, text=True,
    )
    assert cid in result.stdout, f"ContentIdentifier not found in exiftool output:\n{result.stdout}"
    print(f"  MOV ContentIdentifier verified: {cid}")


def test_build_ultra_hdr_jpeg(tmp_path):
    """GainMap pixel LUT conversion: min/max values must map correctly."""
    from mvimg2livephoto.hdr_injector import _build_lut_xiaomi_to_apple
    gainmap_max = 1.11991
    lut = _build_lut_xiaomi_to_apple(gainmap_max)
    assert lut[0] == 0, "pixel=0 (no boost) must map to 0"
    assert lut[255] == 255, "pixel=255 (max boost) must map to 255"
    assert lut[128] > 0, "mid-range pixel must map to positive value"
    print(f"  LUT check: 0→{lut[0]}, 128→{lut[128]}, 255→{lut[255]}")


def run_all():
    import tempfile
    tests = [
        test_new_content_identifier,
        test_read_exif_preserves_timestamp,
        test_read_exif_preserves_gps,
        test_build_apple_maker_note,
        test_build_heic_exif_preserves_timestamps,
        test_build_heic_exif_injects_maker_note,
        lambda: test_jpeg_to_heic(Path(tempfile.mkdtemp())),
        lambda: test_heic_exif_roundtrip(Path(tempfile.mkdtemp())),
        lambda: test_inject_content_id_to_mov(Path(tempfile.mkdtemp())),
        lambda: test_build_ultra_hdr_jpeg(Path(tempfile.mkdtemp())),
    ]
    passed = failed = 0
    for t in tests:
        name = getattr(t, "__name__", repr(t))
        try:
            print(f"[RUN] {name}")
            t()
            print(f"[OK]  {name}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    import os
    os.chdir(Path(__file__).parent.parent)
    success = run_all()
    sys.exit(0 if success else 1)
