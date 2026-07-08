"""Integration tests: full pipeline MVIMG → HEIC + MOV."""

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mvimg2livephoto.builder import convert_one, convert_batch, find_motion_photos
from mvimg2livephoto.cli import main as cli_main

MVIMG1 = Path("android_xiaomi_motion_photo/MVIMG_20260324_220411.jpg")
MVIMG2 = Path("android_xiaomi_motion_photo/MVIMG_20260314_194711.jpg")
MOTION_PHOTO_DIR = Path("android_xiaomi_motion_photo")

UUID_RE = re.compile(
    r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}"
)


def _exiftool(*args):
    r = subprocess.run(["exiftool"] + list(args), capture_output=True, text=True)
    return r.stdout


# ---------------------------------------------------------------------------
# Default (SDR) mode
# ---------------------------------------------------------------------------

def test_convert_one_produces_heic_mov(tmp_path):
    result = convert_one(MVIMG1, tmp_path)
    assert result.heic_path.exists()
    assert result.mov_path.exists()
    assert result.heic_path.suffix.upper() == ".HEIC"
    assert result.mov_path.suffix.upper() == ".MOV"
    assert result.hdr is False
    print(f"  HEIC: {result.heic_path.stat().st_size} bytes")
    print(f"  MOV:  {result.mov_path.stat().st_size} bytes")
    print(f"  ContentIdentifier: {result.content_identifier}")


def test_heic_has_correct_ftyp(tmp_path):
    result = convert_one(MVIMG1, tmp_path)
    data = result.heic_path.read_bytes()
    assert data[4:8] == b"ftyp"
    assert data[8:12] == b"heic", f"Expected heic brand, got {data[8:12]!r}"


def test_heic_content_identifier_matches_mov(tmp_path):
    result = convert_one(MVIMG1, tmp_path)
    heic_ids = UUID_RE.findall(_exiftool("-ContentIdentifier", str(result.heic_path)))
    mov_ids = UUID_RE.findall(_exiftool("-ContentIdentifier", str(result.mov_path)))
    assert heic_ids and mov_ids
    assert heic_ids[0] == mov_ids[0] == result.content_identifier
    print(f"  ContentIdentifier matches: {heic_ids[0]}")


def test_heic_preserves_original_timestamp(tmp_path):
    result = convert_one(MVIMG1, tmp_path)
    out = _exiftool("-DateTimeOriginal", str(result.heic_path))
    assert "2026:03:24" in out, f"Timestamp lost:\n{out}"
    print(f"  DateTimeOriginal: {out.strip()}")


def test_heic_preserves_gps(tmp_path):
    result = convert_one(MVIMG1, tmp_path)
    out = _exiftool("-GPS*", str(result.heic_path))
    assert "GPS Latitude" in out or "GPS Position" in out
    print(f"  GPS preserved")


def test_heic_maker_note_live_photo(tmp_path):
    result = convert_one(MVIMG1, tmp_path)
    mn_out = _exiftool("-MakerNoteVersion", str(result.heic_path))
    assert "16" in mn_out, f"MakerNoteVersion not 16: {mn_out}"
    cid_out = _exiftool("-ContentIdentifier", str(result.heic_path))
    assert result.content_identifier in cid_out
    print(f"  MakerNote OK, CID: {result.content_identifier}")


def test_mov_is_quicktime(tmp_path):
    result = convert_one(MVIMG1, tmp_path)
    assert result.mov_path.read_bytes()[8:12] == b"qt  "


# ---------------------------------------------------------------------------
# HDR mode
# ---------------------------------------------------------------------------

def test_hdr_mode_produces_heic_with_aux(tmp_path):
    result = convert_one(MVIMG1, tmp_path, hdr=True)
    assert result.hdr is True
    assert result.heic_path.suffix.upper() == ".HEIC"
    out = _exiftool("-AuxiliaryImageType", str(result.heic_path))
    assert "hdrgainmap" in out, f"HDR aux image not found:\n{out}"
    print(f"  HDR HEIC: {result.heic_path.stat().st_size} bytes")
    print(f"  aux: {out.strip()}")


def test_hdr_heic_has_correct_aux_type(tmp_path):
    result = convert_one(MVIMG1, tmp_path, hdr=True)
    out = _exiftool("-AuxiliaryImageType", str(result.heic_path))
    assert "urn:com:apple:photo:2020:aux:hdrgainmap" in out
    print(f"  Aux type OK")


def test_hdr_heic_content_identifier_matches_mov(tmp_path):
    result = convert_one(MVIMG1, tmp_path, hdr=True)
    heic_ids = UUID_RE.findall(_exiftool("-ContentIdentifier", str(result.heic_path)))
    mov_ids = UUID_RE.findall(_exiftool("-ContentIdentifier", str(result.mov_path)))
    assert heic_ids and mov_ids
    assert heic_ids[0] == mov_ids[0] == result.content_identifier
    print(f"  HDR ContentIdentifier matches: {heic_ids[0]}")


def test_hdr_heic_preserves_timestamp(tmp_path):
    result = convert_one(MVIMG1, tmp_path, hdr=True)
    out = _exiftool("-DateTimeOriginal", str(result.heic_path))
    assert "2026:03:24" in out, f"Timestamp lost in HDR HEIC: {out}"
    print(f"  HDR HEIC timestamp OK")


# ---------------------------------------------------------------------------
# Batch, CLI, misc
# ---------------------------------------------------------------------------

def test_convert_batch(tmp_path):
    successes, failures = convert_batch([MVIMG1, MVIMG2], tmp_path)
    assert len(failures) == 0
    assert len(successes) == 2
    for r in successes:
        assert r.heic_path.exists()
        assert r.mov_path.exists()
    print(f"  Batch: {len(successes)} files converted")


def test_find_motion_photos():
    found = find_motion_photos(MOTION_PHOTO_DIR)
    assert len(found) >= 2
    for p in found:
        print(f"  Found: {p.name}")


def test_cli_convert(tmp_path):
    ret = cli_main(["convert", str(MVIMG1), "-o", str(tmp_path)])
    assert ret == 0
    heic = tmp_path / (MVIMG1.stem + ".HEIC")
    mov = tmp_path / (MVIMG1.stem + ".MOV")
    assert heic.exists(), f"HEIC not at {heic}"
    assert mov.exists(), f"MOV not at {mov}"
    print(f"  CLI convert OK (SDR)")


def test_cli_convert_hdr(tmp_path):
    ret = cli_main(["convert", str(MVIMG1), "-o", str(tmp_path), "--hdr"])
    assert ret == 0
    heic = tmp_path / (MVIMG1.stem + ".HEIC")
    assert heic.exists()
    out = _exiftool("-AuxiliaryImageType", str(heic))
    assert "hdrgainmap" in out, f"HDR aux missing:\n{out}"
    print(f"  CLI --hdr OK, aux: {out.strip()}")


def test_cli_scan():
    ret = cli_main(["scan", str(MOTION_PHOTO_DIR)])
    assert ret == 0


def test_second_mvimg_converts(tmp_path):
    result = convert_one(MVIMG2, tmp_path)
    assert result.heic_path.exists()
    assert result.mov_path.exists()
    out = _exiftool("-DateTimeOriginal", str(result.heic_path))
    assert "2026:03:14" in out
    print(f"  MVIMG2 timestamp: {out.strip()}")


def test_convert_batch_parallel(tmp_path):
    successes, failures = convert_batch([MVIMG1, MVIMG2], tmp_path, workers=2)
    assert len(failures) == 0
    assert len(successes) == 2
    ids = {r.content_identifier for r in successes}
    assert len(ids) == 2
    for r in successes:
        heic_cid = _exiftool("-ContentIdentifier", str(r.heic_path))
        mov_cid = _exiftool("-ContentIdentifier", str(r.mov_path))
        assert r.content_identifier in heic_cid
        assert r.content_identifier in mov_cid
    print(f"  Parallel batch OK, CIDs: {ids}")


def run_all():
    import tempfile
    tests = [
        lambda: test_convert_one_produces_heic_mov(Path(tempfile.mkdtemp())),
        lambda: test_heic_has_correct_ftyp(Path(tempfile.mkdtemp())),
        lambda: test_heic_content_identifier_matches_mov(Path(tempfile.mkdtemp())),
        lambda: test_heic_preserves_original_timestamp(Path(tempfile.mkdtemp())),
        lambda: test_heic_preserves_gps(Path(tempfile.mkdtemp())),
        lambda: test_heic_maker_note_live_photo(Path(tempfile.mkdtemp())),
        lambda: test_mov_is_quicktime(Path(tempfile.mkdtemp())),
        lambda: test_hdr_mode_produces_heic_with_aux(Path(tempfile.mkdtemp())),
        lambda: test_hdr_heic_has_correct_aux_type(Path(tempfile.mkdtemp())),
        lambda: test_hdr_heic_content_identifier_matches_mov(Path(tempfile.mkdtemp())),
        lambda: test_hdr_heic_preserves_timestamp(Path(tempfile.mkdtemp())),
        lambda: test_convert_batch(Path(tempfile.mkdtemp())),
        test_find_motion_photos,
        lambda: test_cli_convert(Path(tempfile.mkdtemp())),
        lambda: test_cli_convert_hdr(Path(tempfile.mkdtemp())),
        test_cli_scan,
        lambda: test_second_mvimg_converts(Path(tempfile.mkdtemp())),
        lambda: test_convert_batch_parallel(Path(tempfile.mkdtemp())),
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
