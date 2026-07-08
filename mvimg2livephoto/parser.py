"""Parse XMP metadata from Xiaomi MVIMG to locate embedded media segments."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# XMP namespace map
_NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "GCamera": "http://ns.google.com/photos/1.0/camera/",
    "Container": "http://ns.google.com/photos/1.0/container/",
    "Item": "http://ns.google.com/photos/1.0/container/item/",
}


@dataclass
class MotionPhotoSegment:
    """Represents one embedded segment in a Motion Photo file."""
    mime: str        # e.g. "image/jpeg", "video/mp4"
    semantic: str    # "Primary", "GainMap", "MotionPhoto"
    length: int      # byte length, 0 means "everything up to next segment"
    padding: int = 0


@dataclass
class MotionPhotoLayout:
    """Parsed layout of an Android Motion Photo file."""
    total_size: int
    segments: list[MotionPhotoSegment]
    presentation_timestamp_us: int  # video presentation timestamp in microseconds

    @property
    def primary_jpeg_end(self) -> int:
        """Byte offset where the Primary JPEG ends (exclusive)."""
        # If the Primary segment has an explicit length, use it directly.
        primary = self._segment("Primary")
        if primary is not None and primary.length > 0:
            return primary.length
        # Otherwise: Primary has length=0, everything before GainMap/MotionPhoto
        # from the end belongs to it.
        tail = sum(s.length + s.padding for s in self.segments
                   if s.length > 0 and s.semantic != "Primary")
        return self.total_size - tail

    @property
    def mp4_start(self) -> int:
        """Byte offset where the embedded MP4 begins."""
        mp4_seg = self._segment("MotionPhoto")
        if mp4_seg is None:
            raise ValueError("No MotionPhoto video segment found")
        return self.total_size - mp4_seg.length - mp4_seg.padding

    @property
    def mp4_length(self) -> int:
        seg = self._segment("MotionPhoto")
        if seg is None:
            raise ValueError("No MotionPhoto video segment found")
        return seg.length

    def _segment(self, semantic: str) -> Optional[MotionPhotoSegment]:
        for s in self.segments:
            if s.semantic == semantic:
                return s
        return None


def _extract_xmp_bytes(data: bytes) -> bytes:
    """Find and return raw XMP packet bytes within a JPEG/JPEG-like file."""
    # XMP is stored in APP1 marker with "http://ns.adobe.com/xap/1.0/" namespace
    XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
    pos = data.find(XMP_HEADER)
    if pos == -1:
        # Try alternate header (some cameras omit the namespace declaration)
        pos = data.find(b"<x:xmpmeta")
        if pos == -1:
            raise ValueError("No XMP metadata found in file")
        end = data.find(b"</x:xmpmeta>", pos)
        if end == -1:
            raise ValueError("Malformed XMP: no closing tag")
        return data[pos:end + len(b"</x:xmpmeta>")]

    # Skip the header, find the actual xmpmeta block
    xmp_start = data.find(b"<x:xmpmeta", pos)
    xmp_end = data.find(b"</x:xmpmeta>", xmp_start)
    if xmp_start == -1 or xmp_end == -1:
        raise ValueError("Malformed XMP packet")
    return data[xmp_start:xmp_end + len(b"</x:xmpmeta>")]


def _parse_container_directory(root: ET.Element) -> list[MotionPhotoSegment]:
    """Parse Google Container:Directory format (newer Xiaomi/OPPO format)."""
    segments = []
    # Register namespaces to avoid ns0 prefixes
    for prefix, uri in _NS.items():
        try:
            ET.register_namespace(prefix, uri)
        except Exception:
            pass

    ns_container = _NS["Container"]
    ns_item = _NS["Item"]
    ns_rdf = _NS["rdf"]

    dir_elem = root.find(f".//{{{ns_container}}}Directory")
    if dir_elem is None:
        return []

    for li in dir_elem.findall(f".//{{{ns_rdf}}}li"):
        # Container:Item element is in the Container namespace (not Item namespace)
        item = li.find(f"{{{ns_container}}}Item")
        if item is None:
            item = li
        mime = item.get(f"{{{ns_item}}}Mime", "")
        semantic = item.get(f"{{{ns_item}}}Semantic", "")
        try:
            length = int(item.get(f"{{{ns_item}}}Length", "0"))
        except ValueError:
            length = 0
        try:
            padding = int(item.get(f"{{{ns_item}}}Padding", "0"))
        except ValueError:
            padding = 0
        if mime and semantic:
            segments.append(MotionPhotoSegment(mime=mime, semantic=semantic,
                                               length=length, padding=padding))
    return segments


def _parse_micro_video(desc: ET.Element) -> Optional[tuple[int, int]]:
    """Parse legacy MicroVideo format (older Xiaomi). Returns (offset, ts_us)."""
    ns_gcam = _NS["GCamera"]
    is_mv = desc.get(f"{{{ns_gcam}}}MicroVideo", "0")
    if is_mv != "1":
        return None
    try:
        offset = int(desc.get(f"{{{ns_gcam}}}MicroVideoOffset", "0"))
        ts = int(desc.get(f"{{{ns_gcam}}}MicroVideoPresentationTimestampUs", "0"))
        return offset, ts
    except ValueError:
        return None


def parse_motion_photo(path: Path) -> MotionPhotoLayout:
    """Parse a Xiaomi Motion Photo JPEG and return its layout.

    Supports both:
    - Google Container:Directory format (current Xiaomi)
    - Legacy GCamera:MicroVideoOffset format (older Xiaomi)

    Raises ValueError if the file is not a valid Motion Photo.
    """
    data = path.read_bytes()
    total_size = len(data)

    xmp_bytes = _extract_xmp_bytes(data)
    xmp_str = xmp_bytes.decode("utf-8", errors="replace")

    # Strip processing instructions that confuse ElementTree
    xmp_str = re.sub(r"<\?[^?]*\?>", "", xmp_str)

    try:
        root = ET.fromstring(xmp_str)
    except ET.ParseError as e:
        raise ValueError(f"Failed to parse XMP XML: {e}") from e

    ns_gcam = _NS["GCamera"]
    # Find the rdf:Description element
    desc = root.find(f".//{{{_NS['rdf']}}}Description")
    if desc is None:
        raise ValueError("No rdf:Description found in XMP")

    # Check it's actually a Motion Photo
    is_mp = desc.get(f"{{{ns_gcam}}}MotionPhoto", "0")
    is_mv = desc.get(f"{{{ns_gcam}}}MicroVideo", "0")
    if is_mp != "1" and is_mv != "1":
        raise ValueError("File is not a Motion Photo (GCamera:MotionPhoto/MicroVideo not set)")

    ts = 0
    try:
        ts = int(desc.get(f"{{{ns_gcam}}}MotionPhotoPresentationTimestampUs",
                          desc.get(f"{{{ns_gcam}}}MicroVideoPresentationTimestampUs", "0")))
    except ValueError:
        pass

    # Try Container:Directory format first. Some Xiaomi files have a
    # Container:Directory with only Primary + GainMap, while the video segment
    # is described by legacy GCamera:MicroVideoOffset. In that mixed format,
    # keep going and let the legacy parser below locate the MP4.
    segments = _parse_container_directory(root)
    if segments:
        # Validate: must have Primary + MotionPhoto
        semantics = {s.semantic for s in segments}
        if "Primary" in semantics and "MotionPhoto" in semantics:
            return MotionPhotoLayout(total_size=total_size, segments=segments,
                                     presentation_timestamp_us=ts)
        if is_mv != "1":
            raise ValueError(f"Incomplete Container:Directory, found: {semantics}")

    # Fall back to legacy MicroVideoOffset format
    mv = _parse_micro_video(desc)
    if mv:
        offset, ts = mv
        # MicroVideoOffset = bytes from end-of-file to start of video
        mp4_length = offset
        segments = [
            MotionPhotoSegment(mime="image/jpeg", semantic="Primary",
                               length=total_size - mp4_length),
            MotionPhotoSegment(mime="video/mp4", semantic="MotionPhoto",
                               length=mp4_length),
        ]
        return MotionPhotoLayout(total_size=total_size, segments=segments,
                                 presentation_timestamp_us=ts)

    raise ValueError("Unsupported Motion Photo XMP format")
