"""Tests for HEIC output integrity after ftyp patching.

These verify that patching the ``ftyp`` box to Apple-compatible brands
doesn't corrupt the file:
  - ftyp brands match iPhone-native HEIC
  - iloc offsets point to valid data inside mdat
  - pillow-heif can decode the image and pixels are not all-black
  - XMP is stripped (no Android markers leak through)

Run:  python -m pytest mvimg2livephoto/tests/test_heic_integrity.py -v
"""

from __future__ import annotations

import sys
import io
from pathlib import Path

import pytest

# Make the repo root importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pillow_heif  # noqa: E402

from mvimg2livephoto.metadata import (  # noqa: E402
    build_heic_exif,
    jpeg_to_heic,
    read_exif_from_jpeg,
)
from mvimg2livephoto.extractor import extract_media  # noqa: E402

# iPhone-native ftyp brands — the set Photos.app expects
_IPHONE_BRANDS = {"mif1", "MiHB", "MiHA", "heix", "MiHE", "MiPr", "heic", "miaf", "tmap"}

# Real test fixtures
_SAMPLE_DIR = _REPO_ROOT / "android_xiaomi_motion_photo"
_SAMPLES = sorted(_SAMPLE_DIR.glob("MVIMG_*.jpg"))


def _parse_ftyp(data: bytes) -> list[str]:
    """Return the list of compatible brands from the ftyp box."""
    assert data[4:8] == b"ftyp", "ftyp must be the first box"
    size = int.from_bytes(data[0:4], "big")
    body = data[8:size]
    return [body[i:i + 4].decode("ascii", errors="replace")
            for i in range(8, len(body), 4)]


def _parse_top_level_boxes(data: bytes) -> dict:
    """Return {box_type: (offset, size)} for top-level boxes."""
    boxes = {}
    pos = 0
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos:pos + 4], "big")
        btype = data[pos + 4:pos + 8].decode("ascii", errors="replace")
        if size == 0:
            size = len(data) - pos
        if size < 8 or pos + size > len(data):
            break
        boxes[btype] = (pos, size)
        pos += size
    return boxes


def _parse_iloc_items(data: bytes, iloc_off: int, iloc_size: int) -> list:
    """Return list of (item_id, base_offset, extent_offset, extent_length)."""
    body = data[iloc_off + 8:iloc_off + iloc_size]
    version = body[0]
    offset_size = (body[4] >> 4) & 0xF
    length_size = body[4] & 0xF
    base_offset_size = (body[5] >> 4) & 0xF
    if version < 2:
        item_count = int.from_bytes(body[6:8], "big")
        p = 8
    else:
        item_count = int.from_bytes(body[6:10], "big")
        p = 10
    items = []
    for _ in range(item_count):
        item_id = int.from_bytes(body[p:p + 2], "big")
        p += 2
        if version >= 1:
            p += 2
        p += 2  # data_reference_index
        base = int.from_bytes(body[p:p + base_offset_size], "big") if base_offset_size else 0
        p += base_offset_size
        ext_count = int.from_bytes(body[p:p + 2], "big")
        p += 2
        for _ in range(ext_count):
            ext_off = int.from_bytes(body[p:p + offset_size], "big") if offset_size else 0
            p += offset_size
            ext_len = int.from_bytes(body[p:p + length_size], "big") if length_size else 0
            p += length_size
            items.append((item_id, base, ext_off, ext_len))
    return items


def _convert_sample(sample: Path, tmp_path: Path) -> Path:
    """Convert a sample MVIMG to HEIC, return the output path."""
    media = extract_media(sample)
    src_exif = read_exif_from_jpeg(sample)
    exif_bytes = build_heic_exif(src_exif, "TEST-CID", "TEST-PID")
    out = tmp_path / f"{sample.stem}.HEIC"
    jpeg_to_heic(media.jpeg_bytes, exif_bytes, out)
    return out


@pytest.fixture(scope="module")
def _converted(tmp_path_factory) -> dict:
    """Convert each sample once per module — HEIC encoding is slow (~10s each).

    Returns {sample_path_str: output_path}.
    """
    out: dict[str, Path] = {}
    tmp = tmp_path_factory.mktemp("heic")
    for s in _SAMPLES:
        out[str(s)] = _convert_sample(s, tmp)
    return out


def _out_for(sample: Path, _converted: dict) -> Path:
    return _converted[str(sample)]


@pytest.mark.parametrize("sample", _SAMPLES, ids=[s.name for s in _SAMPLES])
def test_ftyp_brands_match_iphone(sample: Path, _converted: dict):
    """ftyp must carry all Apple-compatible brands Photos.app expects."""
    out = _out_for(sample, _converted)
    data = out.read_bytes()
    brands = set(_parse_ftyp(data))
    assert _IPHONE_BRANDS.issubset(brands), f"Missing brands: {_IPHONE_BRANDS - brands}"


@pytest.mark.parametrize("sample", _SAMPLES, ids=[s.name for s in _SAMPLES])
def test_iloc_offsets_in_range(sample: Path, _converted: dict):
    """Every iloc extent must point to data inside the file."""
    out = _out_for(sample, _converted)
    data = out.read_bytes()
    boxes = _parse_top_level_boxes(data)
    meta_off, meta_size = boxes["meta"]
    # Find iloc inside meta (meta is full box: +4 bytes after header)
    mp = meta_off + 12
    iloc_off = None
    while mp + 8 <= meta_off + meta_size:
        ms = int.from_bytes(data[mp:mp + 4], "big")
        mt = data[mp + 4:mp + 8].decode("ascii", errors="replace")
        if mt == "iloc":
            iloc_off = mp
            iloc_size = ms
            break
        mp += ms
    assert iloc_off is not None, "iloc box not found"

    items = _parse_iloc_items(data, iloc_off, iloc_size)
    for item_id, base, ext_off, ext_len in items:
        abs_off = base + ext_off
        assert abs_off + ext_len <= len(data), \
            f"item {item_id}: extent [{abs_off}..{abs_off + ext_len}) out of bounds (file size {len(data)})"


@pytest.mark.parametrize("sample", _SAMPLES, ids=[s.name for s in _SAMPLES])
def test_image_not_black(sample: Path, _converted: dict):
    """Decoded pixels must not be all-zero (the bug we fixed)."""
    out = _out_for(sample, _converted)
    h = pillow_heif.open_heif(str(out))
    images = list(h)
    assert len(images) >= 1
    pil_img = images[0].to_pillow()
    extrema = pil_img.getextrema()
    # Each channel's max must be > 0 (i.e. not all-black)
    for ch_idx, (lo, hi) in enumerate(extrema):
        assert hi > 0, f"Channel {ch_idx} is all-black (extrema={extrema})"


@pytest.mark.parametrize("sample", _SAMPLES, ids=[s.name for s in _SAMPLES])
def test_no_android_xmp(sample: Path, _converted: dict):
    """Output must not carry Android Motion Photo XMP markers."""
    out = _out_for(sample, _converted)
    h = pillow_heif.open_heif(str(out))
    xmp = h.info.get("xmp", b"")
    assert len(xmp) == 0, f"XMP leaked: {len(xmp)} bytes"
    # Also check the bytes don't contain GCamera markers
    data = out.read_bytes()
    assert b"GCamera:MotionPhoto" not in data
    assert b"Container:Directory" not in data


def _find_pixi(data: bytes) -> tuple[int, int] | None:
    """Return (offset, size) of the pixi box inside meta→iprp→ipco, or None."""
    # Locate meta
    pos = 0
    meta_off = meta_size = None
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos:pos + 4], "big")
        if data[pos + 4:pos + 8] == b"meta":
            meta_off, meta_size = pos, size
            break
        pos += size
    if meta_off is None:
        return None
    # Locate iprp inside meta
    iprp_off = iprp_size = None
    mp = meta_off + 12
    while mp + 8 <= meta_off + meta_size:
        ms = int.from_bytes(data[mp:mp + 4], "big")
        if data[mp + 4:mp + 8] == b"iprp":
            iprp_off, iprp_size = mp, ms
            break
        mp += ms
    if iprp_off is None:
        return None
    # Locate ipco inside iprp
    ipco_off = ipco_size = None
    ip = iprp_off + 8
    while ip + 8 <= iprp_off + iprp_size:
        ies = int.from_bytes(data[ip:ip + 4], "big")
        if data[ip + 4:ip + 8] == b"ipco":
            ipco_off, ipco_size = ip, ies
            break
        ip += ies
    if ipco_off is None:
        return None
    # Locate pixi inside ipco
    ic = ipco_off + 8
    while ic + 8 <= ipco_off + ipco_size:
        ics = int.from_bytes(data[ic:ic + 4], "big")
        if data[ic + 4:ic + 8] == b"pixi":
            return ic, ics
        ic += ics
    return None


@pytest.mark.parametrize("sample", _SAMPLES, ids=[s.name for s in _SAMPLES])
def test_pixi_declares_rgb(sample: Path, _converted: dict):
    """pixi box must declare 3 channels (RGB), not 1 (monochrome).

    pillow-heif writes a buggy 1-channel pixi; Photos.app on iOS reads
    this and renders the image grey in its viewer.  Our patch fixes it.
    """
    out = _out_for(sample, _converted)
    data = out.read_bytes()
    found = _find_pixi(data)
    assert found is not None, "pixi box not found"
    pixi_off, pixi_size = found
    assert pixi_size == 16, f"pixi box size should be 16, got {pixi_size}"
    payload = data[pixi_off + 8:pixi_off + pixi_size]
    assert payload[4] == 3, f"pixi num_channels should be 3, got {payload[4]}"
    assert payload[5:8] == b"\x08\x08\x08", "pixi bits_per_channel should be 8,8,8"
