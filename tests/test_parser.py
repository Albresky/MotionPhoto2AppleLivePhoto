"""Tests for parser.py — XMP parsing and layout extraction."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mvimg2livephoto.parser import parse_motion_photo, MotionPhotoLayout

MVIMG1 = Path("android_xiaomi_motion_photo/MVIMG_20260324_220411.jpg")
MVIMG2 = Path("android_xiaomi_motion_photo/MVIMG_20260314_194711.jpg")
MVIMG_LEGACY_GAINMAP1 = Path("android_xiaomi_motion_photo/MVIMG_20250625_165801.jpg")
MVIMG_LEGACY_GAINMAP2 = Path("android_xiaomi_motion_photo/MVIMG_20250625_165056.jpg")
NORMAL_JPG = Path("android_oneplus_normal_jpg/IMG_20250108_103106.jpg")
NORMAL_HEIC = Path("android_xiaomi_normal_heic/IMG_20260507_173522.HEIC")


def test_parse_mvimg1():
    layout = parse_motion_photo(MVIMG1)
    assert isinstance(layout, MotionPhotoLayout)
    assert layout.total_size == MVIMG1.stat().st_size
    # Must have Primary + MotionPhoto segments
    semantics = {s.semantic for s in layout.segments}
    assert "Primary" in semantics
    assert "MotionPhoto" in semantics
    print(f"  segments: {[(s.semantic, s.length) for s in layout.segments]}")
    print(f"  mp4_start={layout.mp4_start}, mp4_length={layout.mp4_length}")
    print(f"  primary_jpeg_end={layout.primary_jpeg_end}")
    print(f"  ts_us={layout.presentation_timestamp_us}")


def test_parse_mvimg2():
    layout = parse_motion_photo(MVIMG2)
    assert layout.mp4_length > 0
    assert layout.mp4_start + layout.mp4_length <= layout.total_size
    print(f"  MVIMG2 mp4_length={layout.mp4_length}, total={layout.total_size}")


def test_mp4_offset_integrity():
    """MP4 must start with ftyp box."""
    import struct
    for path in (MVIMG1, MVIMG_LEGACY_GAINMAP1, MVIMG_LEGACY_GAINMAP2):
        layout = parse_motion_photo(path)
        data = path.read_bytes()
        mp4_start = layout.mp4_start
        box_type = data[mp4_start + 4:mp4_start + 8]
        assert box_type == b"ftyp", f"{path}: expected ftyp, got {box_type!r}"


def test_parse_legacy_microvideo_with_gainmap_directory():
    """Mixed Xiaomi files may describe only Primary/GainMap in Container."""
    for path in (MVIMG_LEGACY_GAINMAP1, MVIMG_LEGACY_GAINMAP2):
        layout = parse_motion_photo(path)
        semantics = {s.semantic for s in layout.segments}
        assert "Primary" in semantics
        assert "MotionPhoto" in semantics
        assert layout.mp4_length > 0
        assert path.read_bytes()[layout.mp4_start + 4:layout.mp4_start + 8] == b"ftyp"
        print(f"  {path.name}: mp4_start={layout.mp4_start}, mp4_length={layout.mp4_length}")


def test_primary_jpeg_integrity():
    """Primary JPEG must start with FF D8 and end before GainMap/MP4."""
    layout = parse_motion_photo(MVIMG1)
    data = MVIMG1.read_bytes()
    assert data[:2] == b"\xff\xd8", "File must start with JPEG magic"
    end = layout.primary_jpeg_end
    assert end > 0
    assert end < layout.total_size


def test_non_motion_photo_raises():
    """Normal JPG/HEIC should raise ValueError."""
    import traceback
    for p in (NORMAL_JPG, NORMAL_HEIC):
        if not p.exists():
            print(f"  skip (not found): {p}")
            continue
        try:
            parse_motion_photo(p)
            assert False, f"Expected ValueError for {p}"
        except ValueError as e:
            print(f"  OK {p.name}: {e}")


def run_all():
    tests = [
        test_parse_mvimg1,
        test_parse_mvimg2,
        test_mp4_offset_integrity,
        test_parse_legacy_microvideo_with_gainmap_directory,
        test_primary_jpeg_integrity,
        test_non_motion_photo_raises,
    ]
    passed = failed = 0
    for t in tests:
        try:
            print(f"[RUN] {t.__name__}")
            t()
            print(f"[OK]  {t.__name__}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"[FAIL] {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    import os
    os.chdir(Path(__file__).parent.parent)
    success = run_all()
    sys.exit(0 if success else 1)
