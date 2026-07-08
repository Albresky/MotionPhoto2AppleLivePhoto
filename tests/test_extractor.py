"""Tests for extractor.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mvimg2livephoto.extractor import extract_media, save_extracted

MVIMG1 = Path("android_xiaomi_motion_photo/MVIMG_20260324_220411.jpg")
MVIMG2 = Path("android_xiaomi_motion_photo/MVIMG_20260314_194711.jpg")
MVIMG_LEGACY_GAINMAP1 = Path("android_xiaomi_motion_photo/MVIMG_20250625_165801.jpg")
MVIMG_LEGACY_GAINMAP2 = Path("android_xiaomi_motion_photo/MVIMG_20250625_165056.jpg")


def test_extract_mvimg1():
    media = extract_media(MVIMG1)
    assert media.jpeg_bytes[:2] == b"\xff\xd8", "JPEG must start with FF D8"
    assert media.mp4_bytes[4:8] == b"ftyp", "MP4 must start with ftyp box"
    assert len(media.jpeg_bytes) > 100_000, "JPEG should be non-trivial size"
    assert len(media.mp4_bytes) > 100_000, "MP4 should be non-trivial size"
    print(f"  jpeg={len(media.jpeg_bytes)} bytes, mp4={len(media.mp4_bytes)} bytes")


def test_extract_mvimg2():
    media = extract_media(MVIMG2)
    assert media.jpeg_bytes[:2] == b"\xff\xd8"
    assert media.mp4_bytes[4:8] == b"ftyp"
    print(f"  MVIMG2 jpeg={len(media.jpeg_bytes)}, mp4={len(media.mp4_bytes)}")


def test_extract_legacy_microvideo_with_gainmap_directory():
    for path in (MVIMG_LEGACY_GAINMAP1, MVIMG_LEGACY_GAINMAP2):
        media = extract_media(path)
        assert media.jpeg_bytes[:2] == b"\xff\xd8"
        assert media.mp4_bytes[4:8] == b"ftyp"
        print(f"  {path.name} jpeg={len(media.jpeg_bytes)}, mp4={len(media.mp4_bytes)}")


def test_jpeg_is_valid():
    """Extracted JPEG should end with FF D9 (EOI marker)."""
    media = extract_media(MVIMG1)
    assert media.jpeg_bytes[-2:] == b"\xff\xd9", \
        f"JPEG should end with FF D9, got {media.jpeg_bytes[-2:].hex()}"


def test_mp4_ftyp_brand():
    """Extracted MP4 ftyp major brand should be a known value."""
    import struct
    media = extract_media(MVIMG1)
    mp4 = media.mp4_bytes
    ftyp_size = struct.unpack(">I", mp4[:4])[0]
    assert mp4[4:8] == b"ftyp"
    major_brand = mp4[8:12]
    assert major_brand in (b"mp42", b"isom", b"mp41", b"avc1"), \
        f"Unexpected major brand: {major_brand!r}"
    print(f"  MP4 major brand: {major_brand!r}")


def test_save_extracted(tmp_path):
    media = extract_media(MVIMG1)
    jpeg_p, mp4_p = save_extracted(media, tmp_path, stem="test")
    assert jpeg_p.exists()
    assert mp4_p.exists()
    assert jpeg_p.read_bytes()[:2] == b"\xff\xd8"
    assert mp4_p.read_bytes()[4:8] == b"ftyp"
    print(f"  saved: {jpeg_p.name}, {mp4_p.name}")


def test_jpeg_mp4_sum_equals_total():
    """Primary JPEG + GainMap + MP4 should sum to total file size."""
    from mvimg2livephoto.parser import parse_motion_photo
    layout = parse_motion_photo(MVIMG1)
    total_segments = sum(s.length + s.padding for s in layout.segments if s.length > 0)
    # primary_jpeg_end + total tail = total file size
    assert layout.primary_jpeg_end + total_segments == layout.total_size, \
        f"{layout.primary_jpeg_end} + {total_segments} != {layout.total_size}"


def run_all():
    import tempfile
    tests = [
        test_extract_mvimg1,
        test_extract_mvimg2,
        test_extract_legacy_microvideo_with_gainmap_directory,
        test_jpeg_is_valid,
        test_mp4_ftyp_brand,
        lambda: test_save_extracted(Path(tempfile.mkdtemp())),
        test_jpeg_mp4_sum_equals_total,
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
