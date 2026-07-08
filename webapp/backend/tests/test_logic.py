"""Pure-logic tests for the backend.

These tests do NOT require the full mvimg2livephoto runtime (ffmpeg,
exiftool, pillow-heif) — they verify the orchestration layer only:
  - classify() picks the right FileKind
  - ProgressStore records and resumes
  - sha256_of streams correctly

Run:  python -m pytest backend/tests/test_logic.py -v
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Make backend importable without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.converter_service import classify, sha256_of  # noqa: E402
from backend.models import FileKind, FileStatus, JobResult  # noqa: E402
from backend.progress_store import ProgressStore  # noqa: E402


# --------------------------------------------------------------- classify


def test_classify_video_extensions():
    assert classify(Path("foo.MP4")) == FileKind.VIDEO
    assert classify(Path("foo.mov")) == FileKind.VIDEO
    assert classify(Path("foo.mkv")) == FileKind.VIDEO


def test_classify_still_image_extensions():
    # PNG / GIF / WEBP are always still_image (no Motion Photo XMP).
    assert classify(Path("foo.png")) == FileKind.STILL_IMAGE
    assert classify(Path("foo.GIF")) == FileKind.STILL_IMAGE
    assert classify(Path("foo.webp")) == FileKind.STILL_IMAGE


def test_classify_unknown_extension():
    assert classify(Path("foo.txt")) == FileKind.UNKNOWN
    assert classify(Path("foo.zip")) == FileKind.UNKNOWN


def test_classify_jpg_without_motion_xmp(tmp_path: Path):
    # A real JPEG without XMP Motion Photo markers → still_image.
    # Write minimal JPEG SOI marker so it's a real file, but content
    # doesn't matter — parse_motion_photo will raise ValueError.
    p = tmp_path / "normal.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    assert classify(p) == FileKind.STILL_IMAGE


def test_classify_heic_is_still_image(tmp_path: Path):
    # HEIC is always treated as still_image (no Motion Photo detection).
    p = tmp_path / "photo.HEIC"
    p.write_bytes(b"\x00\x00\x00\x24 ftypheic" + b"\x00" * 50)
    assert classify(p) == FileKind.STILL_IMAGE


# --------------------------------------------------------------- sha256


def test_sha256_of_known_content(tmp_path: Path):
    import hashlib
    p = tmp_path / "f.bin"
    content = b"hello world" * 1000
    p.write_bytes(content)
    assert sha256_of(p) == hashlib.sha256(content).hexdigest()


def test_sha256_of_large_file_streaming(tmp_path: Path):
    # Verify streaming works for files larger than the 1 MiB chunk.
    import hashlib
    p = tmp_path / "big.bin"
    content = b"abcdef" * (2 * 1024 * 1024)  # ~12 MB
    p.write_bytes(content)
    assert sha256_of(p) == hashlib.sha256(content).hexdigest()


# --------------------------------------------------------- progress store


def test_progress_store_records_and_resumes(tmp_path: Path):
    db = tmp_path / "p.db"
    store = ProgressStore(db)

    p = Path("/photos/MVIMG_x.jpg")
    sha = "abc123"
    result = JobResult(
        path=p,
        kind=FileKind.MOTION_PHOTO,
        status=FileStatus.DONE,
        heic_path=Path("/out/x.HEIC"),
        mov_path=Path("/out/x.MOV"),
    )
    store.record(result, sha)

    # Same path + same hash → already done.
    assert store.has_done(p, sha) is True
    # Same path + different hash → file changed, must re-process.
    assert store.has_done(p, "different") is False
    # Unknown path → not done.
    assert store.has_done(Path("/other.jpg"), sha) is False

    # get() returns the stored result.
    got = store.get(p)
    assert got is not None
    assert got.status == FileStatus.DONE
    assert got.heic_path == Path("/out/x.HEIC")


def test_progress_store_failed_not_resumed(tmp_path: Path):
    """A previously failed file should NOT be skipped on resume."""
    db = tmp_path / "p.db"
    store = ProgressStore(db)

    p = Path("/photos/bad.jpg")
    sha = "deadbeef"
    failed = JobResult(
        path=p,
        kind=FileKind.MOTION_PHOTO,
        status=FileStatus.FAILED,
        error="ffmpeg exploded",
    )
    store.record(failed, sha)

    # has_done only returns True for DONE status.
    assert store.has_done(p, sha) is False
    # But the failure is recorded and retrievable.
    got = store.get(p)
    assert got is not None
    assert got.status == FileStatus.FAILED
    assert got.error == "ffmpeg exploded"
    assert p in [r.path for r in store.list_failed()]


def test_progress_store_update_on_retry(tmp_path: Path):
    """Failed → DONE transition: record() should update the same row."""
    db = tmp_path / "p.db"
    store = ProgressStore(db)
    p = Path("/photos/x.jpg")
    sha = "hash"

    # First attempt fails.
    store.record(
        JobResult(
            path=p, kind=FileKind.MOTION_PHOTO,
            status=FileStatus.FAILED, error="boom",
        ),
        sha,
    )
    assert store.has_done(p, sha) is False

    # Retry succeeds — same path, same hash.
    store.record(
        JobResult(
            path=p, kind=FileKind.MOTION_PHOTO, status=FileStatus.DONE,
            heic_path=Path("/out/x.HEIC"),
        ),
        sha,
    )
    assert store.has_done(p, sha) is True
    assert store.get(p).status == FileStatus.DONE

    # list_failed should be empty now.
    assert store.list_failed() == []


def test_progress_store_records_output_paths(tmp_path: Path):
    """output_paths (list of paths) should round-trip through SQLite."""
    db = tmp_path / "p.db"
    store = ProgressStore(db)
    p = Path("/photos/MVIMG_x.jpg")
    sha = "abc"
    result = JobResult(
        path=p,
        kind=FileKind.MOTION_PHOTO,
        status=FileStatus.DONE,
        heic_path=Path("/out/x.HEIC"),
        mov_path=Path("/out/x.MOV"),
        output_paths=[Path("/out/x.HEIC"), Path("/out/x.MOV")],
    )
    store.record(result, sha)

    got = store.get(p)
    assert got is not None
    assert got.output_paths == [Path("/out/x.HEIC"), Path("/out/x.MOV")]


def test_progress_store_list_all_paginated(tmp_path: Path):
    """list_all returns a page of records, count returns the total."""
    db = tmp_path / "p.db"
    store = ProgressStore(db)
    for i in range(5):
        store.record(
            JobResult(
                path=Path(f"/photos/{i}.jpg"),
                kind=FileKind.STILL_IMAGE,
                status=FileStatus.DONE,
                output_paths=[Path(f"/out/{i}.jpg")],
            ),
            sha256=f"hash{i}",
        )
    assert store.count() == 5
    page = store.list_all(limit=2, offset=0)
    assert len(page) == 2
    page2 = store.list_all(limit=2, offset=2)
    assert len(page2) == 2
    page3 = store.list_all(limit=2, offset=4)
    assert len(page3) == 1


def test_progress_store_v1_migration(tmp_path: Path):
    """Old DBs without output_paths column should migrate transparently."""
    import sqlite3
    db = tmp_path / "p.db"
    # Create v1 schema manually.
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE progress (
            input_path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            kind TEXT NOT NULL,
            heic_path TEXT,
            mov_path TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO progress VALUES
            ('/old.jpg', 'oldsha', 'done', 'motion_photo',
             '/old.HEIC', '/old.MOV', NULL, '2024-01-01', '2024-01-01');
        """
    )
    conn.commit()
    conn.close()

    # Opening with ProgressStore should add output_paths column.
    store = ProgressStore(db)
    got = store.get(Path("/old.jpg"))
    assert got is not None
    assert got.status == FileStatus.DONE
    # output_paths defaults to [] for legacy rows.
    assert got.output_paths == []
    # New writes should work.
    store.record(
        JobResult(
            path=Path("/new.jpg"),
            kind=FileKind.STILL_IMAGE,
            status=FileStatus.DONE,
            output_paths=[Path("/new.jpg")],
        ),
        sha256="newsha",
    )
    assert store.count() == 2


def test_progress_store_records_duration(tmp_path: Path):
    """duration_ms is persisted and recovered via get/list_all."""
    db = tmp_path / "p.db"
    store = ProgressStore(db)
    p = Path("/photos/MVIMG_x.jpg")
    result = JobResult(
        path=p,
        kind=FileKind.MOTION_PHOTO,
        status=FileStatus.DONE,
        heic_path=Path("/out/x.HEIC"),
        mov_path=Path("/out/x.MOV"),
        output_paths=[Path("/out/x.HEIC"), Path("/out/x.MOV")],
        duration_ms=12345,
    )
    store.record(result, "sha")
    got = store.get(p)
    assert got is not None
    assert got.duration_ms == 12345

    # list_all should also return duration_ms.
    rows = store.list_all()
    assert len(rows) == 1
    assert rows[0].duration_ms == 12345
