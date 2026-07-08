"""Inject a GainMap (HDR aux image) into an existing HEIC file.

Modifies the HEIC binary to add:
  - A new iinf entry (item type 'hvc1' or 'jpeg') for the aux image data
  - A new iloc entry with the aux data offset in mdat
  - A new iref(auxl) entry linking aux item → primary item
  - The raw GainMap data appended to mdat

The GainMap is encoded as a grayscale HEIC (L mode) via pillow_heif.
The pixel values are converted from Xiaomi's Ultra HDR encoding to Apple's
sRGB-encoded linear gainmap format.

Reference algorithm from Apple-photo-to-UltraHDR-motion-photo (Rust):
  apple_linear = (2^(recovery * gainmap_max) - 1) / (headroom - 1)
  apple_pixel  = srgb_encode(apple_linear)
where headroom = 2^gainmap_max.
"""

from __future__ import annotations

import io
import math
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pillow_heif
from PIL import Image


# AUX type URI for Apple HDR GainMap
APPLE_HDR_AUX_TYPE = b"urn:com:apple:photo:2020:aux:hdrgainmap"


# ---------------------------------------------------------------------------
# GainMap pixel conversion: Xiaomi Ultra HDR → Apple sRGB-encoded linear
# ---------------------------------------------------------------------------

def _build_lut_xiaomi_to_apple(gainmap_max: float) -> bytes:
    """Build a 256-entry LUT converting Xiaomi GainMap pixels to Apple format."""
    headroom = 2.0 ** gainmap_max
    hr_1 = headroom - 1.0
    log_hr = math.log(headroom)

    lut = bytearray(256)
    for u8 in range(256):
        recovery = u8 / 255.0
        pixel_gain = math.exp(recovery * log_hr)          # = 2^(recovery*gainmap_max)
        apple_linear = (pixel_gain - 1.0) / hr_1          # [0, 1] linear
        apple_linear = max(0.0, min(1.0, apple_linear))

        # sRGB encode
        if apple_linear <= 0.0031308:
            encoded = apple_linear * 12.92
        else:
            encoded = 1.055 * (apple_linear ** (1.0 / 2.4)) - 0.055
        lut[u8] = int(encoded * 255.0 + 0.5)

    return bytes(lut)


def convert_gainmap_pixels(gainmap_pil: Image.Image, gainmap_max: float) -> Image.Image:
    """Convert a Xiaomi GainMap PIL image (mode L) to Apple's encoding.

    Returns a new mode-L image with pixel values re-encoded for Apple format.
    """
    lut = _build_lut_xiaomi_to_apple(gainmap_max)
    if gainmap_pil.mode != "L":
        gainmap_pil = gainmap_pil.convert("L")
    return gainmap_pil.point(lut)


def encode_gainmap_as_hevc(gainmap_pil: Image.Image) -> tuple[bytes, bytes, bytes]:
    """Encode a grayscale gainmap PIL image.

    Returns (hevc_bitstream, hvcC_box_bytes, ispe_box_bytes).

    - hevc_bitstream: raw HEVC payload extracted from mdat (for HEIC item data)
    - hvcC_box_bytes: the full hvcC box from the aux HEIC (for ipco injection)
    - ispe_box_bytes: the full ispe box from the aux HEIC (for ipco injection)
    """
    heif_file = pillow_heif.from_pillow(gainmap_pil)
    buf = io.BytesIO()
    heif_file.save(buf, quality=85)
    heic_bytes = buf.getvalue()

    hevc_data = None
    hvcc_box = None
    ispe_box = None

    offset = 0
    while offset + 8 <= len(heic_bytes):
        box_size = struct.unpack(">I", heic_bytes[offset:offset + 4])[0]
        box_type = heic_bytes[offset + 4:offset + 8]
        if box_type == b"mdat":
            hevc_data = heic_bytes[offset + 8:offset + box_size]
        elif box_type == b"meta":
            # scan inside meta for iprp > ipco
            meta_content = heic_bytes[offset + 12:offset + box_size]
            mc_off = 0
            while mc_off + 8 <= len(meta_content):
                mc_sz = struct.unpack(">I", meta_content[mc_off:mc_off + 4])[0]
                mc_type = meta_content[mc_off + 4:mc_off + 8]
                if mc_type == b"iprp":
                    iprp = meta_content[mc_off:mc_off + mc_sz]
                    ip_off = 8
                    while ip_off + 8 <= mc_sz:
                        ip_sz = struct.unpack(">I", iprp[ip_off:ip_off + 4])[0]
                        ip_type = iprp[ip_off + 4:ip_off + 8]
                        if ip_type == b"ipco":
                            ipco = iprp[ip_off:ip_off + ip_sz]
                            p_off = 8
                            while p_off + 8 <= ip_sz:
                                p_sz = struct.unpack(">I", ipco[p_off:p_off + 4])[0]
                                p_type = ipco[p_off + 4:p_off + 8]
                                if p_type == b"hvcC":
                                    hvcc_box = bytes(ipco[p_off:p_off + p_sz])
                                elif p_type == b"ispe":
                                    ispe_box = bytes(ipco[p_off:p_off + p_sz])
                                if p_sz < 8:
                                    break
                                p_off += p_sz
                            break
                        if ip_sz < 8:
                            break
                        ip_off += ip_sz
                    break
                if mc_sz < 8:
                    break
                mc_off += mc_sz
        if box_size < 8:
            break
        offset += box_size

    if hevc_data is None:
        raise ValueError("No mdat box found in encoded HEIC")
    if hvcc_box is None:
        raise ValueError("No hvcC property found in encoded HEIC")
    if ispe_box is None:
        raise ValueError("No ispe property found in encoded HEIC")

    return hevc_data, hvcc_box, ispe_box


# ---------------------------------------------------------------------------
# Low-level HEIC box helpers
# ---------------------------------------------------------------------------

class _BoxReader:
    """Read top-level boxes from a HEIC file."""

    def __init__(self, data: bytes):
        self.data = data

    def find(self, box_type: str) -> tuple[int, int]:
        """Return (absolute_offset, size) of first matching box."""
        offset = 0
        target = box_type.encode("latin1")
        while offset + 8 <= len(self.data):
            size = struct.unpack(">I", self.data[offset:offset + 4])[0]
            if self.data[offset + 4:offset + 8] == target:
                return offset, size
            if size < 8:
                break
            offset += size
        raise KeyError(f"Box {box_type!r} not found")

    def find_in_meta(self, box_type: str) -> tuple[int, int]:
        """Find a box inside the meta box. Returns absolute file offsets."""
        meta_off, meta_size = self.find("meta")
        # meta box: 4 size + 4 type + 4 version/flags = 12 bytes header
        meta_content_start = meta_off + 12
        meta_content = self.data[meta_content_start:meta_off + meta_size]

        offset = 0
        target = box_type.encode("latin1")
        while offset + 8 <= len(meta_content):
            size = struct.unpack(">I", meta_content[offset:offset + 4])[0]
            if meta_content[offset + 4:offset + 8] == target:
                return meta_content_start + offset, size
            if size < 8:
                break
            offset += size
        raise KeyError(f"Box {box_type!r} not found in meta")


def _pack_box(box_type: str, content: bytes) -> bytes:
    size = 8 + len(content)
    return struct.pack(">I", size) + box_type.encode("latin1") + content


def _pack_fullbox(box_type: str, version: int, flags: int, content: bytes) -> bytes:
    ver_flags = struct.pack(">I", (version << 24) | (flags & 0xFFFFFF))
    return _pack_box(box_type, ver_flags + content)


# ---------------------------------------------------------------------------
# HEIC aux item injection
# ---------------------------------------------------------------------------

def inject_aux_image(
    heic_path: Path,
    aux_hevc_data: bytes,
    aux_hvcc_box: bytes,
    aux_ispe_box: bytes,
    output_path: Path,
) -> None:
    """Inject an aux image into an existing HEIC file.

    Parameters:
        heic_path: base HEIC to modify
        aux_hevc_data: raw HEVC bitstream (mdat payload only, not a nested HEIC)
        aux_hvcc_box: full hvcC box bytes (from aux HEIC ipco)
        aux_ispe_box: full ispe box bytes (from aux HEIC ipco)
        output_path: where to write the modified HEIC

    Modifies meta (iinf, iloc, iref, iprp) and appends aux bitstream after the file.
    Each aux item gets hvcC + ispe + auxC properties in ipma so ImageIO can decode it.
    """
    data = bytearray(heic_path.read_bytes())
    reader = _BoxReader(bytes(data))

    # Record original meta size so we can fix iloc offsets at the end
    orig_meta_off, orig_meta_size = reader.find("meta")

    # --- Determine the new item ID ---
    iinf_off, iinf_size = reader.find_in_meta("iinf")
    iinf_data = bytes(data[iinf_off:iinf_off + iinf_size])
    item_count = struct.unpack(">H", iinf_data[12:14])[0]
    new_item_id = item_count + 1

    # --- Determine primary item ID ---
    pitm_off, pitm_size = reader.find_in_meta("pitm")
    primary_item_id = struct.unpack(">H", bytes(data[pitm_off + 12:pitm_off + 14]))[0]

    # --- Append aux bitstream to end of file ---
    aux_data_offset = len(data)
    aux_data_length = len(aux_hevc_data)
    data.extend(aux_hevc_data)

    # --- Update iinf: add new infe entry ---
    infe_content = struct.pack(">HH", new_item_id, 0) + b"hvc1" + b"\x00"
    new_infe = _pack_fullbox("infe", 2, 0, infe_content)
    new_iinf_content = struct.pack(">H", item_count + 1) + iinf_data[14:] + new_infe
    new_iinf = _pack_fullbox("iinf", 0, 0, new_iinf_content)
    _replace_box(data, iinf_off, iinf_size, new_iinf)

    reader = _BoxReader(bytes(data))

    # --- Update iloc: add entry for aux item ---
    new_iloc_entry = struct.pack(">HH", new_item_id, 0)
    new_iloc_entry += struct.pack(">I", aux_data_offset)
    new_iloc_entry += struct.pack(">H", 1)
    new_iloc_entry += struct.pack(">II", 0, aux_data_length)

    iloc_off, iloc_size = reader.find_in_meta("iloc")
    iloc_data = bytes(data[iloc_off:iloc_off + iloc_size])
    existing_count = struct.unpack(">H", iloc_data[14:16])[0]
    new_iloc_body = iloc_data[12:14] + struct.pack(">H", existing_count + 1) + iloc_data[16:] + new_iloc_entry
    new_iloc = _pack_fullbox("iloc", 0, 0, new_iloc_body)
    _replace_box(data, iloc_off, iloc_size, new_iloc)

    reader = _BoxReader(bytes(data))

    # --- Add/update iref with auxl reference ---
    new_auxl = struct.pack(">I", 14) + b"auxl"
    new_auxl += struct.pack(">HH", new_item_id, 1) + struct.pack(">H", primary_item_id)

    try:
        iref_off, iref_size = reader.find_in_meta("iref")
        old_iref = bytes(data[iref_off:iref_off + iref_size])
        new_iref = _pack_fullbox("iref", 0, 0, old_iref[12:] + new_auxl)
        _replace_box(data, iref_off, iref_size, new_iref)
    except KeyError:
        new_iref = _pack_fullbox("iref", 0, 0, new_auxl)
        reader2 = _BoxReader(bytes(data))
        pitm_off2, pitm_size2 = reader2.find_in_meta("pitm")
        insert_at = pitm_off2 + pitm_size2
        data[insert_at:insert_at] = new_iref
        _fix_meta_size(data)

    # --- Add hvcC + ispe + auxC properties in iprp, link via ipma ---
    _add_aux_type_property(data, new_item_id, aux_hvcc_box, aux_ispe_box)

    # --- Fix iloc base_offsets for existing items ---
    # All meta modifications above may have grown the meta box, shifting mdat.
    # Existing iloc entries use absolute offsets into mdat; adjust by the meta size delta.
    reader = _BoxReader(bytes(data))
    _, new_meta_size = reader.find("meta")
    meta_delta = new_meta_size - orig_meta_size
    if meta_delta != 0:
        _adjust_iloc_offsets(data, meta_delta, new_item_id)

    output_path.write_bytes(bytes(data))


def _replace_box(data: bytearray, offset: int, old_size: int, new_box: bytes) -> None:
    """Replace a box in data bytearray at offset. Updates meta box size."""
    data[offset:offset + old_size] = new_box
    _fix_meta_size(data)


def _adjust_iloc_offsets(data: bytearray, delta: int, skip_item_id: int) -> None:
    """Add delta to all base_offsets in iloc for items other than skip_item_id.

    Called after meta box grows by delta bytes, which shifts all mdat content.
    iloc v0 layout (after 8-byte box header + 4-byte ver/flags):
      1 byte: (offset_size << 4) | length_size
      1 byte: (base_offset_size << 4) | index_size
      2 bytes: item_count
      then for each item:
        2 bytes: item_id
        2 bytes: data_ref_idx
        base_offset_size bytes: base_offset
        2 bytes: extent_count
        extent_count * (offset_size + length_size) bytes: extents
    """
    reader = _BoxReader(bytes(data))
    iloc_off, iloc_size = reader.find_in_meta("iloc")
    iloc = bytearray(data[iloc_off:iloc_off + iloc_size])

    offset_size = (iloc[12] >> 4) & 0xF
    length_size = iloc[12] & 0xF
    base_offset_size = (iloc[13] >> 4) & 0xF
    item_count = struct.unpack(">H", bytes(iloc[14:16]))[0]

    pos = 16  # start of item entries
    for _ in range(item_count):
        item_id = struct.unpack(">H", bytes(iloc[pos:pos + 2]))[0]
        # data_ref_idx at pos+2
        base_off_pos = pos + 4

        if item_id != skip_item_id and base_offset_size > 0:
            if base_offset_size == 4:
                old_base = struct.unpack(">I", bytes(iloc[base_off_pos:base_off_pos + 4]))[0]
                new_base = old_base + delta
                struct.pack_into(">I", iloc, base_off_pos, new_base)
            elif base_offset_size == 8:
                old_base = struct.unpack(">Q", bytes(iloc[base_off_pos:base_off_pos + 8]))[0]
                new_base = old_base + delta
                struct.pack_into(">Q", iloc, base_off_pos, new_base)

        pos += 4 + base_offset_size
        extent_count = struct.unpack(">H", bytes(iloc[pos:pos + 2]))[0]
        pos += 2 + extent_count * (offset_size + length_size)

    data[iloc_off:iloc_off + iloc_size] = iloc


def _fix_meta_size(data: bytearray) -> None:
    """Recalculate and patch the meta box size after modifications."""
    reader = _BoxReader(bytes(data))
    try:
        meta_off, _ = reader.find("meta")
    except KeyError:
        return
    # Find actual meta end by scanning child boxes
    pos = meta_off + 12
    while pos + 8 <= len(data):
        sz = struct.unpack(">I", bytes(data[pos:pos + 4]))[0]
        btype = bytes(data[pos + 4:pos + 8]).decode("latin1", errors="replace")
        if sz < 8 or btype not in ("hdlr", "iloc", "iinf", "pitm", "iprp", "iref", "grpl", "dinf"):
            break
        pos += sz
    new_meta_size = pos - meta_off
    struct.pack_into(">I", data, meta_off, new_meta_size)


def _add_aux_type_property(
    data: bytearray,
    aux_item_id: int,
    aux_hvcc_box: bytes,
    aux_ispe_box: bytes,
) -> None:
    """Add hvcC + ispe + auxC properties for the aux item and link via ipma.

    The aux item needs all three so ImageIO can decode it:
    - hvcC: HEVC decoder configuration (essential)
    - ispe: image spatial extent / resolution
    - auxC: identifies aux type as Apple HDR GainMap
    """
    reader = _BoxReader(bytes(data))

    iprp_off, iprp_size = reader.find_in_meta("iprp")
    iprp_data = bytes(data[iprp_off:iprp_off + iprp_size])

    # ipco starts right after iprp 8-byte header
    ipco_off_rel = 8
    ipco_size = struct.unpack(">I", iprp_data[ipco_off_rel:ipco_off_rel + 4])[0]

    # Count existing properties to know the starting index for new ones
    prop_idx_base = 1
    pos = ipco_off_rel + 8
    while pos < ipco_off_rel + ipco_size:
        psz = struct.unpack(">I", iprp_data[pos:pos + 4])[0]
        prop_idx_base += 1
        pos += psz

    # Three new properties; their 1-based indices:
    hvcc_idx = prop_idx_base        # hvcC (essential for decoding)
    ispe_idx = prop_idx_base + 1    # ispe
    auxc_idx = prop_idx_base + 2    # auxC

    auxc_content = APPLE_HDR_AUX_TYPE + b"\x00"
    auxc_box = _pack_fullbox("auxC", 0, 0, auxc_content)

    # Append hvcC + ispe + auxC to ipco
    old_ipco_content = iprp_data[ipco_off_rel + 8:ipco_off_rel + ipco_size]
    new_ipco = _pack_box("ipco", old_ipco_content + aux_hvcc_box + aux_ispe_box + auxc_box)

    # ipma immediately follows ipco inside iprp
    ipma_off_rel = ipco_off_rel + ipco_size
    ipma_size = struct.unpack(">I", iprp_data[ipma_off_rel:ipma_off_rel + 4])[0]
    ipma_data = iprp_data[ipma_off_rel:ipma_off_rel + ipma_size]

    # New ipma entry: item_id + assoc_count(3) + [hvcC(essential), ispe, auxC]
    new_ipma_entry = struct.pack(">H", aux_item_id)
    new_ipma_entry += struct.pack(">B", 3)                          # 3 associations
    new_ipma_entry += struct.pack(">B", 0x80 | (hvcc_idx & 0x7F))  # hvcC, essential
    new_ipma_entry += struct.pack(">B", ispe_idx & 0x7F)            # ispe
    new_ipma_entry += struct.pack(">B", auxc_idx & 0x7F)            # auxC

    existing_count = struct.unpack(">I", ipma_data[12:16])[0]
    new_ipma = _pack_fullbox("ipma", 0, 0,
                             struct.pack(">I", existing_count + 1) + ipma_data[16:] + new_ipma_entry)

    new_iprp = _pack_box("iprp", new_ipco + new_ipma)
    _replace_box(data, iprp_off, iprp_size, new_iprp)
    _fix_meta_size(data)
