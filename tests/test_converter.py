"""Tests for converter.py — MP4 → MOV conversion."""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mvimg2livephoto.extractor import extract_media
from mvimg2livephoto.converter import mp4_bytes_to_mov, mp4_file_to_mov_file

MVIMG1 = Path("android_xiaomi_motion_photo/MVIMG_20260324_220411.jpg")


def _get_mp4_bytes():
    return extract_media(MVIMG1).mp4_bytes


def test_mov_starts_with_ftyp():
    mp4 = _get_mp4_bytes()
    mov = mp4_bytes_to_mov(mp4)
    assert mov[4:8] == b"ftyp", f"MOV should start with ftyp, got {mov[4:8]!r}"
    major_brand = mov[8:12]
    print(f"  MOV major brand: {major_brand!r}")


def test_mov_brand_is_qt():
    mp4 = _get_mp4_bytes()
    mov = mp4_bytes_to_mov(mp4)
    major_brand = mov[8:12]
    # ffmpeg -f mov outputs 'qt  ' brand
    assert major_brand == b"qt  ", f"Expected qt  , got {major_brand!r}"


def test_mov_has_moov_box():
    """MOV must contain a moov box for proper QuickTime compatibility."""
    mp4 = _get_mp4_bytes()
    mov = mp4_bytes_to_mov(mp4)
    assert b"moov" in mov, "MOV must contain a moov box"


def test_mov_size_reasonable():
    """Output size should be in same ballpark as input (stream copy)."""
    mp4 = _get_mp4_bytes()
    mov = mp4_bytes_to_mov(mp4)
    ratio = len(mov) / len(mp4)
    assert 0.8 < ratio < 1.5, f"MOV/MP4 size ratio {ratio:.2f} out of expected range"
    print(f"  mp4={len(mp4)}, mov={len(mov)}, ratio={ratio:.3f}")


def test_mp4_file_to_mov_file(tmp_path):
    mp4 = _get_mp4_bytes()
    src = tmp_path / "input.mp4"
    dst = tmp_path / "output.mov"
    src.write_bytes(mp4)
    mp4_file_to_mov_file(src, dst)
    assert dst.exists()
    assert dst.stat().st_size > 0
    mov = dst.read_bytes()
    assert mov[4:8] == b"ftyp"
    print(f"  file conversion OK: {dst.stat().st_size} bytes")


def run_all():
    import tempfile
    tests = [
        test_mov_starts_with_ftyp,
        test_mov_brand_is_qt,
        test_mov_has_moov_box,
        test_mov_size_reasonable,
        lambda: test_mp4_file_to_mov_file(Path(tempfile.mkdtemp())),
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
