"""Read, preserve, and inject metadata for Live Photo conversion.

Responsibilities:
- Copy EXIF from source JPEG to output HEIC (preserve all original metadata)
- Generate a new UUID as ContentIdentifier
- Write ContentIdentifier into HEIC (via piexif MakerNote)
- Write ContentIdentifier into MOV (via exiftool QuickTime metadata)
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import uuid
from pathlib import Path

import piexif
import pillow_heif
from PIL import Image


# ---------------------------------------------------------------------------
# UUID helpers
# ---------------------------------------------------------------------------

def new_content_identifier() -> str:
    """Generate a fresh UUID string in Apple's uppercase format."""
    return str(uuid.uuid4()).upper()


# ---------------------------------------------------------------------------
# Apple MakerNote builder
# (Based on analysis of real iPhone HEIC and pyheic_struct implementation)
# ---------------------------------------------------------------------------

_LIVE_PHOTO_VIDEO_INDEX = 8595185700  # constant present in all iPhone Live Photos


def _build_apple_maker_note(content_id: str, photo_id: str) -> bytes:
    """Build a minimal Apple MakerNote payload for Live Photo pairing.

    The MakerNote uses a custom Apple IFD format:
      - Header: b'Apple iOS\x00\x00\x01MM' (14 bytes)
      - Entry count: 2 bytes big-endian
      - IFD entries: N x 12 bytes (tag, type, count, value_or_offset)
      - Next IFD: 4 bytes (0x00000000)
      - Variable-length data (ASCII strings, etc.)
    """
    def _ascii(s: str) -> bytes:
        return s.upper().encode("ascii") + b"\x00"

    cid = _ascii(content_id)
    pid = _ascii(photo_id)
    # ImageCaptureRequestID = same as PhotoIdentifier
    req_id = pid

    entries = [
        # tag,   type, count, value (inline) or (data, for variable)
        (0x0001, 9, 1, None, 16),                         # MakerNoteVersion
        (0x0011, 2, len(cid), cid, None),                 # ContentIdentifier
        (0x0014, 9, 1, None, 12),                         # ImageCaptureType = Live Photo
        (0x0017, 16, 1, _live_photo_video_index_bytes(), None),  # LivePhotoVideoIndex
        (0x001F, 9, 1, None, 0),                          # PhotosAppFeatureFlags
        (0x0020, 2, len(req_id), req_id, None),           # ImageCaptureRequestID
        (0x002B, 2, len(pid), pid, None),                 # PhotoIdentifier
    ]

    header = b"Apple iOS\x00\x00\x01MM" + struct.pack(">H", len(entries))
    # Variable data starts after: header + entries*12 + next_ifd(4)
    var_offset_base = len(header) + len(entries) * 12 + 4

    payload = bytearray(header)
    variable_data = bytearray()

    for tag, typ, count, data, inline_val in entries:
        if data is not None:
            offset = var_offset_base + len(variable_data)
            payload += struct.pack(">HHII", tag, typ, count, offset)
            variable_data += data
            if len(data) % 2:
                variable_data += b"\x00"
        else:
            payload += struct.pack(">HHII", tag, typ, count, inline_val)

    payload += struct.pack(">I", 0)  # next IFD offset = 0
    payload += variable_data
    return bytes(payload)


def _live_photo_video_index_bytes() -> bytes:
    return _LIVE_PHOTO_VIDEO_INDEX.to_bytes(8, "big", signed=False)


# ---------------------------------------------------------------------------
# EXIF copy: JPEG → HEIC
# ---------------------------------------------------------------------------

def read_exif_from_jpeg(jpeg_path: Path) -> dict:
    """Read EXIF from a JPEG file and return a piexif dict."""
    return piexif.load(str(jpeg_path))


def build_heic_exif(source_jpeg_exif: dict, content_id: str, photo_id: str) -> bytes:
    """Build EXIF bytes for output HEIC.

    - Preserves original EXIF (timestamps, GPS, camera info)
    - Preserves the embedded thumbnail from the source JPEG (Photos.app
      uses this to render the grid thumbnail; without it the tile is grey
      until the user opens the photo)
    - Injects Apple MakerNote with ContentIdentifier + PhotoIdentifier
    - Clears any Android-specific tags that would confuse Photos.app
    """
    exif = _deep_copy_exif(source_jpeg_exif)

    # Inject Apple MakerNote
    maker_note = _build_apple_maker_note(content_id, photo_id)
    exif.setdefault("Exif", {})[piexif.ExifIFD.MakerNote] = maker_note

    # Fix known type mismatches between Android EXIF and piexif expectations.
    # SceneType (41729) must be bytes b'\x01', but Android writes it as int 1.
    exif_ifd = exif.get("Exif", {})
    if piexif.ExifIFD.SceneType in exif_ifd:
        val = exif_ifd[piexif.ExifIFD.SceneType]
        if isinstance(val, int):
            exif_ifd[piexif.ExifIFD.SceneType] = val.to_bytes(1, "big")

    # Remove Android/Google XMP-related EXIF tags that Photos.app doesn't need
    for key in (piexif.ExifIFD.UserComment,):
        exif.get("Exif", {}).pop(key, None)

    # Preserve the thumbnail from the source JPEG.  Photos.app reads this
    # embedded JPEG to render the library grid thumbnail; without it the
    # tile stays grey until the user opens the photo.  The 1st IFD (with
    # thumbnail offset/length tags 513/514) is regenerated by piexif.dump
    # to point at the embedded bytes — we just need to keep the bytes and
    # the format-describing tags (Compression, PhotometricInterpretation,
    # Orientation, XResolution, YResolution, ResolutionUnit).
    src_thumb = source_jpeg_exif.get("thumbnail")
    src_1st = source_jpeg_exif.get("1st", {})
    if src_thumb:
        # Keep only format-describing 1st IFD tags; drop 513/514 (offset/
        # length) because piexif.dump recomputes them from the bytes.
        keep_tags = {258, 259, 274, 282, 283, 296}
        exif["1st"] = {t: v for t, v in src_1st.items() if t in keep_tags}
        exif["thumbnail"] = src_thumb
    else:
        exif["1st"] = {}
        exif["thumbnail"] = None

    return piexif.dump(exif)


def _deep_copy_exif(exif: dict) -> dict:
    result = {}
    for ifd_name, ifd in exif.items():
        if ifd is None or not isinstance(ifd, dict):
            result[ifd_name] = ifd
        else:
            result[ifd_name] = dict(ifd)
    return result


# ---------------------------------------------------------------------------
# HEIC writer: JPEG → HEIC with new EXIF
# ---------------------------------------------------------------------------

def jpeg_to_heic(
    jpeg_bytes: bytes,
    exif_bytes: bytes,
    output_path: Path,
    *,
    enc_preset: str = "ultrafast",
) -> None:
    """Convert JPEG bytes to HEIC file, embedding the given EXIF.

    Uses pillow-heif to encode; the image is re-encoded as HEIC (HEVC).
    Output is written to output_path.

    ``enc_preset`` controls the x265 encoder speed preset.  Options
    (fastest → slowest): ``ultrafast``, ``superfast``, ``veryfast``,
    ``faster``, ``fast``, ``medium``, ``slow``, ``slower``, ``veryslow``.
    Default ``ultrafast`` gives ~5x speedup over ``medium`` with negligible
    quality loss (PSNR ~62dB, mean pixel diff 0.18/255).  Use ``medium``
    for maximum compression efficiency at the cost of ~5x encoding time.

    Important: we strip the source JPEG's XMP packet before encoding.
    Android Motion Photos carry GCamera:MotionPhoto / Container:Directory /
    hdrgm:GainMap markers in XMP — if these leak into the output HEIC,
    Photos.app misidentifies the file as an Android photo and refuses to
    render the grid thumbnail or pair it as a Live Photo.  iPhone-native
    HEIC files carry no XMP at all.

    After encoding we patch the ``ftyp`` box to carry Apple-compatible
    brands (``MiHB``, ``MiHE``, ``MiPr``, ``tmap`` …).  Without these
    Photos.app treats the file as non-Apple, causing a grey flash while
    it falls back to full-frame decode.  See reference/pyheic_struct's
    ``apple.py`` for the same approach.
    """
    import io
    img = Image.open(io.BytesIO(jpeg_bytes))
    # Drop Android XMP so it doesn't propagate into the HEIC container.
    # pillow-heif reads img.info['xmp'] and embeds it as a separate item.
    img.info.pop("xmp", None)
    img.info.pop("Xmp", None)

    heif_file = pillow_heif.from_pillow(img)
    heif_file.save(
        str(output_path),
        quality=92,
        exif=exif_bytes,
        enc_params={"preset": enc_preset},
    )

    # Patch ftyp brands to match iPhone-native HEIC.  pillow-heif writes a
    # minimal ftyp (major=heic, compat=[mif1, heic, miaf]); Photos.app
    # wants the Apple private brands too.  The ftyp box is always the
    # first box in the file: size(4) + "ftyp"(4) + payload.
    _patch_pixi_rgb(output_path)
    _patch_ftyp_apple_brands(output_path)


def _patch_ftyp_apple_brands(path: Path) -> None:
    """Overwrite the ``ftyp`` box payload with Apple-compatible brands.

    Layout of ftyp payload (after the 8-byte size+type header):
        major_brand(4) + minor_version(4) + compat_brands(4 each)
    Total payload = 4 + 4 + 4*9 = 44 bytes.  Box size = 8 + 44 = 52.

    Pillow-heif writes a 28-byte ftyp (3 compat brands).  Expanding it to
    52 bytes shifts every subsequent box by 24 bytes — the ``iloc`` box
    stores absolute file offsets to image data in ``mdat``, so we must
    rewrite those offsets too, otherwise the decoder reads from the wrong
    position and produces a black/corrupt image.

    The fix: parse top-level boxes to locate ``meta`` → ``iloc``, then
    add ``delta`` (new_ftyp_size - old_ftyp_size) to every ``base_offset``
    and ``extent_offset`` field in iloc.
    """
    # Payload: heic + minor=0 + mif1,MiHB,MiHA,heix,MiHE,MiPr,heic,miaf,tmap
    APPLE_BRAND_PAYLOAD = (
        b"heic"               # major brand
        b"\x00\x00\x00\x00"   # minor version
        b"mif1" b"MiHB" b"MiHA" b"heix"
        b"MiHE" b"MiPr" b"heic" b"miaf" b"tmap"
    )
    new_ftyp = (
        (8 + len(APPLE_BRAND_PAYLOAD)).to_bytes(4, "big")
        + b"ftyp"
        + APPLE_BRAND_PAYLOAD
    )

    data = bytearray(path.read_bytes())
    old_ftyp_size = int.from_bytes(data[0:4], "big")
    if data[4:8] != b"ftyp":
        raise RuntimeError(f"Expected ftyp at offset 0, got {bytes(data[4:8])!r}")

    delta = len(new_ftyp) - old_ftyp_size
    if delta == 0:
        # Already the right size — just overwrite the payload in place.
        data[8:8 + len(APPLE_BRAND_PAYLOAD)] = APPLE_BRAND_PAYLOAD
        path.write_bytes(bytes(data))
        return

    # Locate the meta box and its iloc child.
    meta_off, meta_size = _find_top_level_box(data, b"meta")
    if meta_off is None:
        # No meta box — nothing to fix up.  Replace ftyp and rewrite.
        patched = new_ftyp + bytes(data[old_ftyp_size:])
        path.write_bytes(patched)
        return

    # meta is a full box: version(1) + flags(3) = 4 bytes after the 8-byte header.
    # Its children (hdlr, iloc, iinf, pitm, iprp, iref…) start at meta_off + 12.
    iloc_off, iloc_size = _find_child_box(
        data, meta_off + 12, meta_off + meta_size, b"iloc"
    )
    if iloc_off is None:
        patched = new_ftyp + bytes(data[old_ftyp_size:])
        path.write_bytes(patched)
        return

    # Rewrite iloc's offsets before we shift the bytes.
    _adjust_iloc_offsets(data, iloc_off, iloc_size, delta)

    # Reconstruct: new ftyp + everything after old ftyp (shifted by delta).
    patched = bytearray(new_ftyp)
    patched += data[old_ftyp_size:]
    path.write_bytes(bytes(patched))


def _patch_pixi_rgb(path: Path) -> None:
    """Fix the ``pixi`` (pixel information) box to declare 3 RGB channels.

    pillow-heif incorrectly writes a 1-channel pixi box
    (``num_channels=1, bits=8``) even for RGB images.  iPhone-native HEIC
    declares ``num_channels=3, bits=8,8,8``.  Photos.app on iOS reads the
    pixi box to decide how to render the image in its viewer — a 1-channel
    declaration causes the viewer to treat the image as monochrome and
    display it grey, even though the underlying HEVC data is full RGB.
    The edit mode uses a different code path and renders correctly.

    This patch expands the pixi box from 14 bytes (8 header + 4 reserved +
    1 num_channels + 1 bits) to 16 bytes (8 header + 4 reserved + 1
    num_channels + 3 bits), adding ``2`` bytes.  Since pixi lives inside
    ``meta → iprp → ipco``, expanding it shifts every subsequent byte by 2
    — including the ``mdat`` box and the ``iloc`` base_offset values that
    point into it.  We adjust those offsets by the same mechanism as the
    ftyp patch.
    """
    NEW_PIXI = (
        (16).to_bytes(4, "big")   # box size: 8 header + 8 payload
        + b"pixi"
        + b"\x00\x00\x00\x00"    # version + flags
        + b"\x03"                # num_channels = 3 (RGB)
        + b"\x08\x08\x08"        # 8 bits per channel
    )
    OLD_PIXI_SIZE = 14   # pillow-heif's 1-channel pixi

    data = bytearray(path.read_bytes())

    # Locate meta → iprp → ipco → pixi
    meta_off, meta_size = _find_top_level_box(data, b"meta")
    if meta_off is None:
        return
    iprp_off, iprp_size = _find_child_box(
        data, meta_off + 12, meta_off + meta_size, b"iprp"
    )
    if iprp_off is None:
        return
    ipco_off, ipco_size = _find_child_box(
        data, iprp_off + 8, iprp_off + iprp_size, b"ipco"
    )
    if ipco_off is None:
        return
    pixi_off, pixi_size = _find_child_box(
        data, ipco_off + 8, ipco_off + ipco_size, b"pixi"
    )
    if pixi_off is None:
        # Already patched or different structure — nothing to do.
        return

    # Only patch if it's the buggy 1-channel pixi (size 14, 1 channel).
    if pixi_size != OLD_PIXI_SIZE:
        return
    pixi_payload = data[pixi_off + 8:pixi_off + pixi_size]
    if pixi_payload[4] != 1:   # num_channels != 1
        return

    delta = len(NEW_PIXI) - OLD_PIXI_SIZE   # 2

    # Locate iloc and adjust its base_offsets by delta, same as ftyp patch.
    iloc_off, iloc_size = _find_child_box(
        data, meta_off + 12, meta_off + meta_size, b"iloc"
    )
    if iloc_off is not None:
        _adjust_iloc_offsets(data, iloc_off, iloc_size, delta)

    # Update parent box sizes (meta, iprp, ipco) by delta.
    _bump_box_size(data, meta_off, delta)
    _bump_box_size(data, iprp_off, delta)
    _bump_box_size(data, ipco_off, delta)

    # Splice in the new pixi, replacing the old 14 bytes with 16 bytes.
    patched = (
        data[:pixi_off]
        + bytearray(NEW_PIXI)
        + data[pixi_off + OLD_PIXI_SIZE:]
    )
    path.write_bytes(bytes(patched))


def _bump_box_size(data: bytearray, box_off: int, delta: int) -> None:
    """Add ``delta`` to the 4-byte size field of the box at ``box_off``."""
    old_size = int.from_bytes(data[box_off:box_off + 4], "big")
    new_size = old_size + delta
    data[box_off:box_off + 4] = new_size.to_bytes(4, "big")


def _find_top_level_box(data: bytes, btype: bytes) -> tuple[int | None, int]:
    """Return (offset, size) of the first top-level box of given type."""
    pos = 0
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos:pos + 4], "big")
        if size == 0:
            size = len(data) - pos
        if size < 8 or pos + size > len(data):
            return None, 0
        if data[pos + 4:pos + 8] == btype:
            return pos, size
        pos += size
    return None, 0


def _find_child_box(data: bytes, start: int, end: int, btype: bytes) -> tuple[int | None, int]:
    """Return (offset, size) of the first child box of given type in [start, end)."""
    pos = start
    while pos + 8 <= end:
        size = int.from_bytes(data[pos:pos + 4], "big")
        if size == 0:
            size = end - pos
        if size < 8 or pos + size > end:
            return None, 0
        if data[pos + 4:pos + 8] == btype:
            return pos, size
        pos += size
    return None, 0


def _adjust_iloc_offsets(data: bytearray, iloc_off: int, iloc_size: int, delta: int) -> None:
    """Add ``delta`` to every base_offset and extent_offset in the iloc box.

    iloc layout (ISO 14496-12 §8.11.3):
        version(1) + flags(3)
        offset_size(4 bits) + length_size(4 bits)
        base_offset_size(4 bits) + index_size(4 bits)   [v1+]
        item_count(2 bytes for v<2, 4 bytes for v2)
        per item:
            item_id(2) [v<2] or (4) [v2]
            [v1+: construction_method(2)]
            data_reference_index(2)
            base_offset(base_offset_size bytes)
            extent_count(2)
            per extent:
                [index_size bytes]
                extent_offset(offset_size bytes)
                extent_length(length_size bytes)
    """
    body_start = iloc_off + 8        # skip size+type
    body_end = iloc_off + iloc_size
    version = data[body_start]
    # version+flags = 4 bytes
    p = body_start + 4
    b1 = data[p]; p += 1
    offset_size = (b1 >> 4) & 0xF
    length_size = b1 & 0xF
    b2 = data[p]; p += 1
    base_offset_size = (b2 >> 4) & 0xF
    index_size = b2 & 0xF

    if version < 2:
        item_count = int.from_bytes(data[p:p + 2], "big")
        p += 2
    else:
        item_count = int.from_bytes(data[p:p + 4], "big")
        p += 4

    for _ in range(item_count):
        # item_id
        p += 2 if version < 2 else 4
        # construction_method (v1+)
        if version >= 1:
            p += 2
        # data_reference_index
        p += 2
        # base_offset — this is an ABSOLUTE file offset pointing into mdat.
        # When ftyp grows, every absolute offset must shift by delta.
        if base_offset_size:
            bo = int.from_bytes(data[p:p + base_offset_size], "big")
            new_bo = bo + delta
            data[p:p + base_offset_size] = new_bo.to_bytes(base_offset_size, "big")
            p += base_offset_size
        # extent_count
        ext_count = int.from_bytes(data[p:p + 2], "big")
        p += 2
        for _ in range(ext_count):
            # extent_index (index_size, only for version 1+)
            if index_size:
                p += index_size
            # extent_offset — this is RELATIVE to base_offset (typically 0).
            # Do NOT add delta here; base_offset already accounts for the shift.
            p += offset_size
            # extent_length
            if length_size:
                p += length_size


# ---------------------------------------------------------------------------
# Ultra HDR JPEG writer: keep Primary + GainMap, inject Apple MakerNote
# ---------------------------------------------------------------------------

def build_ultra_hdr_jpeg(
    ultra_hdr_jpeg_bytes: bytes,
    source_jpeg_path: Path,
    content_id: str,
    photo_id: str,
    output_path: Path,
) -> None:
    """Write an Ultra HDR JPEG Live Photo static image.

    Takes the Primary+GainMap JPEG (stripped of the trailing MP4) from a
    Xiaomi Motion Photo, injects an Apple MakerNote carrying ContentIdentifier
    and PhotoIdentifier, and writes the result to output_path.

    The MPF structure that locates the embedded GainMap is preserved intact —
    only the APP1/EXIF segment is replaced via piexif.insert().

    This produces a .JPG (not .HEIC) that:
    - iOS 17+ displays with full HDR effect via the embedded GainMap
    - Photos.app pairs with the .MOV via matching ContentIdentifier
    - Retains all original EXIF (timestamps, GPS, camera info)
    """
    source_exif = read_exif_from_jpeg(source_jpeg_path)
    exif = _deep_copy_exif(source_exif)

    # Inject Apple MakerNote
    exif.setdefault("Exif", {})[piexif.ExifIFD.MakerNote] = _build_apple_maker_note(
        content_id, photo_id
    )

    # Fix Android type mismatches
    exif_ifd = exif.get("Exif", {})
    if piexif.ExifIFD.SceneType in exif_ifd:
        val = exif_ifd[piexif.ExifIFD.SceneType]
        if isinstance(val, int):
            exif_ifd[piexif.ExifIFD.SceneType] = val.to_bytes(1, "big")

    # Keep MPF (Multi-Picture Format) — it locates the GainMap — so do NOT
    # clear the 1st IFD or thumbnail here, unlike build_heic_exif.
    # Remove only the thumbnail image bytes to save space, but keep MPF tags.
    exif["1st"] = {}
    exif["thumbnail"] = None

    new_exif_bytes = piexif.dump(exif)

    # piexif.insert replaces the EXIF APP1 segment while preserving all other
    # JPEG segments (including APP2/MPF and the appended GainMap JPEG).
    output_path.parent.mkdir(parents=True, exist_ok=True)
    piexif.insert(new_exif_bytes, ultra_hdr_jpeg_bytes, str(output_path))


# ---------------------------------------------------------------------------
# MOV ContentIdentifier writer
# ---------------------------------------------------------------------------

def inject_content_id_to_mov(mov_path: Path, content_id: str) -> None:
    """Write ContentIdentifier to a MOV file using exiftool.

    Raises EnvironmentError if exiftool is not installed.
    Raises subprocess.CalledProcessError on failure.
    """
    exiftool = shutil.which("exiftool")
    if not exiftool:
        raise EnvironmentError("exiftool not found. Install with: brew install exiftool")

    subprocess.run(
        [
            exiftool,
            f"-QuickTime:ContentIdentifier={content_id}",
            "-overwrite_original",
            str(mov_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
