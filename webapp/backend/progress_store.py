"""SQLite-backed progress store for resume support.

The store records every processed file keyed by ``(input_path, sha256)``.
When the same file (same content hash) is queued again, the backend reads
the previous result and skips re-conversion.

Schema (v2 — adds ``output_paths`` JSON column for uniform output listing):
    progress(
        input_path     TEXT PRIMARY KEY,
        sha256         TEXT NOT NULL,
        status         TEXT NOT NULL,
        kind           TEXT NOT NULL,
        heic_path      TEXT,
        mov_path       TEXT,
        output_paths   TEXT,        -- JSON array of paths
        error          TEXT,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL
    )
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .models import FileKind, FileStatus, JobResult


_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS progress (
    input_path     TEXT PRIMARY KEY,
    sha256         TEXT NOT NULL,
    status         TEXT NOT NULL,
    kind           TEXT NOT NULL,
    heic_path      TEXT,
    mov_path       TEXT,
    output_paths   TEXT,
    error          TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_progress_status ON progress(status);
CREATE INDEX IF NOT EXISTS idx_progress_sha    ON progress(sha256);
"""

# Older databases (schema v1) lack the output_paths column.  Add it
# idempotently so existing users' resume history isn't lost.
_MIGRATE_V1_TO_V2 = (
    "ALTER TABLE progress ADD COLUMN output_paths TEXT"
)

# v2 → v3: add duration_ms column for per-file timing.
_MIGRATE_V2_TO_V3 = (
    "ALTER TABLE progress ADD COLUMN duration_ms INTEGER NOT NULL DEFAULT 0"
)

# v3 → v4: add batch_id column so we can distinguish per-batch results
# from the full history.  NULL for rows from older batches.
_MIGRATE_V3_TO_V4 = (
    "ALTER TABLE progress ADD COLUMN batch_id TEXT"
)


class ProgressStore:
    """Thin SQLite wrapper.

    Thread-safe via a single ``threading.Lock`` guarding the connection.
    With ``isolation_level=None`` (autocommit) + explicit ``BEGIN IMMEDIATE``
    in ``_tx()``, concurrent worker threads would otherwise interleave
    ``BEGIN``/``COMMIT`` calls on the shared connection, producing
    ``cannot start a transaction within a transaction``.  The lock
    serializes transaction entry/exit so that can't happen.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(db_path.parent, 0o755)
        except OSError:
            pass
        # check_same_thread=False because FastAPI runs on a thread pool
        self._conn = sqlite3.connect(
            str(db_path), check_same_thread=False, isolation_level=None
        )
        self._lock = threading.Lock()
        self._conn.executescript(_SCHEMA_V2)
        try:
            self._conn.execute(_MIGRATE_V1_TO_V2)
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute(_MIGRATE_V2_TO_V3)
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute(_MIGRATE_V3_TO_V4)
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_progress_batch "
                "ON progress(batch_id)"
            )
        except sqlite3.OperationalError:
            pass

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        # Hold the lock across the entire BEGIN/COMMIT window so concurrent
        # worker threads can't interleave and trip the nested-BEGIN error.
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------------ read

    def get(self, input_path: Path) -> Optional[JobResult]:
        """Return the stored result for a path, or None if never recorded."""
        with self._lock:
            row = self._conn.execute(
                "SELECT input_path, kind, status, heic_path, mov_path, "
                "       output_paths, error, duration_ms "
                "FROM progress WHERE input_path = ?",
                (str(input_path),),
            ).fetchone()
        if row is None:
            return None
        output_paths: list[Path] = []
        if row[5]:
            try:
                output_paths = [Path(p) for p in json.loads(row[5])]
            except (json.JSONDecodeError, TypeError):
                pass
        return JobResult(
            path=Path(row[0]),
            kind=FileKind(row[1]),
            status=FileStatus(row[2]),
            heic_path=Path(row[3]) if row[3] else None,
            mov_path=Path(row[4]) if row[4] else None,
            output_paths=output_paths,
            error=row[6],
            duration_ms=row[7] if row[7] is not None else 0,
        )

    def has_done(self, input_path: Path, sha256: str) -> bool:
        """True if this path was successfully processed with this hash.

        If the stored hash differs from ``sha256`` the file has been replaced
        and must be re-processed — returns False.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT sha256, status FROM progress WHERE input_path = ?",
                (str(input_path),),
            ).fetchone()
        if row is None:
            return False
        stored_sha, status = row
        if status != FileStatus.DONE.value:
            return False
        return stored_sha == sha256

    # ----------------------------------------------------------------- write

    def record(self, result: JobResult, sha256: str,
               batch_id: str | None = None) -> None:
        """Insert or update a file's result.

        ``batch_id`` tags the row with the batch that processed it, so the
        UI can show "this run" results separately from the full history.
        """
        now = datetime.now(timezone.utc).isoformat()
        out_json = json.dumps(
            [str(p) for p in result.output_paths], ensure_ascii=False
        )
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO progress
                    (input_path, sha256, status, kind, heic_path, mov_path,
                     output_paths, error, duration_ms, batch_id,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(input_path) DO UPDATE SET
                    sha256       = excluded.sha256,
                    status       = excluded.status,
                    kind         = excluded.kind,
                    heic_path    = excluded.heic_path,
                    mov_path     = excluded.mov_path,
                    output_paths = excluded.output_paths,
                    error        = excluded.error,
                    duration_ms  = excluded.duration_ms,
                    batch_id     = excluded.batch_id,
                    updated_at   = excluded.updated_at
                """,
                (
                    str(result.path),
                    sha256,
                    result.status.value,
                    result.kind.value,
                    str(result.heic_path) if result.heic_path else None,
                    str(result.mov_path) if result.mov_path else None,
                    out_json,
                    result.error,
                    result.duration_ms,
                    batch_id,
                    now,
                    now,
                ),
            )

    # ----------------------------------------------------------------- misc

    def list_failed(self) -> list[JobResult]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT input_path, kind, status, heic_path, mov_path, "
                "       output_paths, error, duration_ms "
                "FROM progress WHERE status = ?",
                (FileStatus.FAILED.value,),
            ).fetchall()
        return [self._row_to_result(r) for r in rows]

    def list_all(self, limit: int = 500, offset: int = 0) -> list[JobResult]:
        """Return a page of all records — used by /api/output_items."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT input_path, kind, status, heic_path, mov_path, "
                "       output_paths, error, duration_ms "
                "FROM progress ORDER BY input_path LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_result(r) for r in rows]

    def list_batch(self, batch_id: str, limit: int = 500,
                   offset: int = 0) -> list[JobResult]:
        """Return a page of records for a specific batch."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT input_path, kind, status, heic_path, mov_path, "
                "       output_paths, error, duration_ms "
                "FROM progress WHERE batch_id = ? "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (batch_id, limit, offset),
            ).fetchall()
        return [self._row_to_result(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM progress").fetchone()
        return row[0] if row else 0

    def count_batch(self, batch_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM progress WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------- internals

    @staticmethod
    def _row_to_result(row: tuple) -> JobResult:
        output_paths: list[Path] = []
        if row[5]:
            try:
                output_paths = [Path(p) for p in json.loads(row[5])]
            except (json.JSONDecodeError, TypeError):
                pass
        return JobResult(
            path=Path(row[0]),
            kind=FileKind(row[1]),
            status=FileStatus(row[2]),
            heic_path=Path(row[3]) if row[3] else None,
            mov_path=Path(row[4]) if row[4] else None,
            output_paths=output_paths,
            error=row[6],
            duration_ms=row[7] if len(row) > 7 and row[7] is not None else 0,
        )
