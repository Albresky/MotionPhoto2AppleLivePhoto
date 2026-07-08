"""SQLite-backed scan index cache.

When scanning a large directory (e.g. 160 GB external disk with 12k+ files),
repeatedly walking the filesystem and classifying each file is very slow.
This module stores scan results keyed by directory path + mtime so subsequent
scans of the same directory load instantly from the database.

Schema:
    scan_index(
        dir_path     TEXT,         -- the scanned directory (absolute path)
        file_path    TEXT,         -- absolute path of the file
        kind         TEXT,         -- motion_photo / still_image / video / unknown
        size         INTEGER,      -- file size in bytes
        mtime        REAL,         -- file modification time (Unix timestamp)
        indexed_at   TEXT,         -- when this row was inserted
        PRIMARY KEY (dir_path, file_path)
    )

A directory is considered "fresh" if its own mtime hasn't changed since the
last indexing.  Individual file existence is checked lazily at convert time
(not during scan) — files deleted after indexing will be caught by the
pre-convert path check.

Progress tracking: the scanner calls a heartbeat callback every 5 files
so the UI can show a progress bar during long scans.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

from .models import FileKind


_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_index (
    dir_path     TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    size         INTEGER NOT NULL,
    mtime        REAL NOT NULL,
    indexed_at   TEXT NOT NULL,
    PRIMARY KEY (dir_path, file_path)
);
CREATE INDEX IF NOT EXISTS idx_scan_dir ON scan_index(dir_path);
"""


class ScanCache:
    """SQLite-backed scan index cache.

    Thread-safe via a single ``threading.Lock`` guarding the connection —
    same pattern as ``ProgressStore``.  ``index_directory()`` holds the
    lock for the whole batch insert (with a heartbeat callback every 5
    files), which is fine because indexing is fast and not contended by
    the queue.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(db_path.parent, 0o755)
        except OSError:
            pass
        self._conn = sqlite3.connect(
            str(db_path), check_same_thread=False, isolation_level=None
        )
        self._lock = threading.Lock()
        self._conn.executescript(_SCHEMA)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ----------------------------------------------------------------- read

    def is_cached(self, dir_path: Path) -> bool:
        """Check if a directory has been indexed before."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM scan_index WHERE dir_path = ?",
                (str(dir_path),),
            ).fetchone()
        return row[0] > 0 if row else False

    def load(self, dir_path: Path) -> list[dict]:
        """Load all indexed files for a directory from the cache.

        Returns a list of dicts: {path, kind, size, mtime}.
        Does NOT check file existence — that's done at convert time.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT file_path, kind, size, mtime FROM scan_index "
                "WHERE dir_path = ? ORDER BY file_path",
                (str(dir_path),),
            ).fetchall()
        return [
            {
                "path": Path(r[0]),
                "kind": FileKind(r[1]),
                "size": r[2],
                "mtime": r[3],
            }
            for r in rows
        ]

    # ---------------------------------------------------------------- write

    def clear(self, dir_path: Path) -> None:
        """Remove all cached entries for a directory."""
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM scan_index WHERE dir_path = ?",
                (str(dir_path),),
            )

    def index_directory(
        self,
        dir_path: Path,
        items: list[dict],
        *,
        on_heartbeat: Optional[Callable[[int, int], None]] = None,
    ) -> int:
        """Index a list of scanned files into the cache.

        ``items`` is a list of dicts: {path, kind, size, mtime}.
        Replaces any existing entries for the directory.

        ``on_heartbeat`` is called every 5 files with (completed, total)
        so the UI can show a progress bar.

        Returns the number of files indexed.
        """
        now = datetime.now(timezone.utc).isoformat()
        total = len(items)
        if total == 0:
            self.clear(dir_path)
            return 0

        with self._tx() as conn:
            conn.execute(
                "DELETE FROM scan_index WHERE dir_path = ?",
                (str(dir_path),),
            )
            for i, item in enumerate(items):
                conn.execute(
                    "INSERT OR REPLACE INTO scan_index "
                    "(dir_path, file_path, kind, size, mtime, indexed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(dir_path),
                        str(item["path"]),
                        item["kind"].value
                        if hasattr(item["kind"], "value")
                        else str(item["kind"]),
                        item["size"],
                        item["mtime"],
                        now,
                    ),
                )
                # Heartbeat every 5 files
                if on_heartbeat and (i + 1) % 5 == 0:
                    on_heartbeat(i + 1, total)
            # Final heartbeat
            if on_heartbeat:
                on_heartbeat(total, total)

        return total

    def close(self) -> None:
        self._conn.close()
