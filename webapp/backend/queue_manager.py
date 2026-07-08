"""Async queue manager.

Bridges FastAPI's async world and the sync ``convert_one`` call.

Architecture
------------
Two-stage pipeline with bounded queues:

  Stage 1 (hasher):  file → sha256 + resume check
  Stage 2 (worker):  process_file (convert / copy / symlink)

Both stages have their own thread pool.  Hashing is I/O-bound (reading
5–10 MB per file from disk); conversion is CPU-bound (x265 encode).
Separating them means a slow disk read doesn't block an encoder thread.

Backpressure: the submit loop only enqueues up to ``queue_depth`` items
at a time across both stages.  Default ``queue_depth = workers * 2``,
so at most ~2× the parallelism worth of work is in flight.  This keeps
memory bounded for 14k-file batches (was: all 14k futures created at
once, ~1 GB of futures + sha buffers sitting idle).

Resume logic:
    For each file, compute sha256 then ask ``ProgressStore.has_done``.
    If the same path + hash was already done, emit a SKIPPED event and
    move on — no work is done.

Pre-convert path check:
    Before hashing, the file's existence is verified.  Missing files
    (e.g. deleted after scan cache was built) are logged to the failure
    log and marked as FAILED.

Failure logging:
    All failures are written to ``~/.mvimg2livephoto/failures.log``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .converter_service import (
    make_context,
    process_file,
    sha256_of,
)
from .models import (
    FileKind,
    FileStatus,
    JobResult,
    ProgressEvent,
)
from .progress_store import ProgressStore

logger = logging.getLogger(__name__)

_LOG_PATH = Path.home() / ".mvimg2livephoto" / "failures.log"


def _log_failure(path: Path, kind: FileKind, error: str) -> None:
    """Append a failure record to the log file."""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {path}\n  kind: {kind.value}\n  error: {error}\n\n")
    except OSError as log_exc:
        logger.error("Failed to write failure log: %s", log_exc)


@dataclass
class _WorkItem:
    input_root: Path
    file_path: Path
    output_dir: Path
    enc_preset: str = "ultrafast"


class QueueManager:
    """Manages a single conversion batch.

    A new instance is created per ``POST /api/convert``.  When the batch
    finishes the instance is discarded — the ProgressStore persists across
    batches.
    """

    def __init__(
        self,
        store: ProgressStore,
        on_event_cb=None,
    ) -> None:
        self._store = store
        self._on_event_cb = on_event_cb
        self._event_q: asyncio.Queue[ProgressEvent] = asyncio.Queue()
        self._total = 0
        self._done = 0
        self._done_lock = threading.Lock()
        self._enc_preset = "ultrafast"
        self._symlink_fallback = False
        self._batch_id = uuid.uuid4().hex
        # Pause gate.  Set when running; cleared by pause().  The submit
        # loop awaits this event before acquiring an in-flight slot for
        # the next item, so clearing it blocks NEW submissions while
        # already-submitted work (hash + convert) continues to completion.
        self._resume_event: asyncio.Event = asyncio.Event()
        self._resume_event.set()
        self._paused = False

    @property
    def batch_id(self) -> str:
        """The ID of the current/last batch."""
        return self._batch_id

    @property
    def paused(self) -> bool:
        """True if submission of new items is paused."""
        return self._paused

    def pause(self) -> None:
        """Pause submission of new items.

        Items already submitted to the hash/convert pools keep running to
        completion — this only stops the submit loop from enqueuing more.
        """
        self._paused = True
        self._resume_event.clear()

    def resume(self) -> None:
        """Resume submission of new items after a pause()."""
        self._paused = False
        self._resume_event.set()

    # ---------------------------------------------------------- public API

    async def run(
        self,
        items: list[tuple[Path, Path, Path]],
        workers: int,
        enc_preset: str = "ultrafast",
        symlink_fallback: bool = False,
    ) -> None:
        """Process a list of ``(input_root, file_path, output_dir)`` tuples.

        Uses a two-stage pipeline (hash → convert) with bounded queues to
        avoid submitting all 14k futures at once.
        """
        self._total = len(items)
        self._done = 0
        self._enc_preset = enc_preset
        self._symlink_fallback = symlink_fallback
        if self._total == 0:
            await self._event_q.put(None)
            return

        # Two pools: hashing (I/O) and conversion (CPU).  Hashing is cheap
        # so we use fewer threads; conversion is the bottleneck.
        hash_workers = max(1, min(workers, 4))
        conv_workers = workers
        # Bounded in-flight count: at most 2× the conversion parallelism
        # worth of work sitting between stages.  Keeps memory bounded
        # without starving the converters.
        queue_depth = conv_workers * 2

        hash_pool = ThreadPoolExecutor(max_workers=hash_workers)
        conv_pool = ThreadPoolExecutor(max_workers=conv_workers)
        loop = asyncio.get_running_loop()

        # Bounded semaphore: limits how many items are "in flight" across
        # both stages.  Acquire before submitting to hash; release when
        # conversion finishes.
        in_flight = asyncio.Semaphore(queue_depth)

        # Track all futures so we can await them at the end.
        conv_futures: list = []
        # Track how many items have been submitted (for the submit loop).
        submitted = 0
        # Track completion for the drain.
        completed_count = 0

        async def _submit_one(input_root, file_path, output_dir):
            """Submit one file through both stages."""
            nonlocal submitted
            # Pause gate: blocks here when paused, without holding the
            # in-flight semaphore.  Already-submitted work continues.
            await self._resume_event.wait()
            await in_flight.acquire()
            ctx = make_context(input_root, file_path, output_dir)
            # Stage 1: hash (in hash_pool)
            hash_fut = loop.run_in_executor(
                hash_pool, self._hash_and_check, ctx
            )
            try:
                hash_result = await hash_fut
            except Exception as exc:
                logger.exception("Hash stage crashed on %s", ctx.input_path)
                error_msg = f"Hash crash: {exc}"
                _log_failure(ctx.input_path, ctx.kind, error_msg)
                self._emit(
                    JobResult(
                        path=ctx.input_path,
                        kind=ctx.kind,
                        status=FileStatus.FAILED,
                        error=error_msg,
                    ),
                    ctx,
                )
                in_flight.release()
                return
            # If hash stage decided to skip/fail, emit directly.
            if hash_result is not None:
                self._emit(hash_result, ctx)
                in_flight.release()
                return
            # Stage 2: convert (in conv_pool)
            conv_fut = loop.run_in_executor(
                conv_pool, self._convert, ctx
            )
            conv_futures.append((ctx, asyncio.ensure_future(
                _await_conv(conv_fut, ctx, in_flight)
            )))
            submitted += 1

        async def _await_conv(fut, ctx, sem):
            """Await a conversion future, emit result, release semaphore."""
            nonlocal completed_count
            try:
                result = await asyncio.wrap_future(fut)
                self._emit(result, ctx)
            except Exception as exc:
                logger.exception("Convert stage crashed on %s", ctx.input_path)
                error_msg = f"Convert crash: {exc}"
                _log_failure(ctx.input_path, ctx.kind, error_msg)
                self._emit(
                    JobResult(
                        path=ctx.input_path,
                        kind=ctx.kind,
                        status=FileStatus.FAILED,
                        error=error_msg,
                    ),
                    ctx,
                )
            finally:
                sem.release()
                completed_count += 1

        # Submit all items, respecting backpressure (semaphore caps in-flight).
        for input_root, file_path, output_dir in items:
            await _submit_one(input_root, file_path, output_dir)

        # Wait for all conversion futures to complete.
        for _, fut in conv_futures:
            await fut

        # Drain the event queue.
        await self._event_q.put(None)

        hash_pool.shutdown(wait=True)
        conv_pool.shutdown(wait=True)

    def events(self) -> asyncio.Queue:
        """Return the queue a WebSocket subscriber can read from."""
        return self._event_q

    # ---------------------------------------------------------- internals

    def _hash_and_check(self, ctx) -> Optional[JobResult]:
        """Stage 1: hash + resume check.

        Returns:
            - JobResult (FAILED/SKIPPED) if the file should not be converted.
            - None if the file should proceed to conversion.
        """
        # Pre-convert existence check
        if not ctx.input_path.exists():
            error = f"File not found: {ctx.input_path}"
            _log_failure(ctx.input_path, ctx.kind, error)
            return JobResult(
                path=ctx.input_path,
                kind=ctx.kind,
                status=FileStatus.FAILED,
                error=error,
            )

        try:
            sha = sha256_of(ctx.input_path)
        except OSError as exc:
            error = f"Hash failed: {exc}"
            _log_failure(ctx.input_path, ctx.kind, error)
            return JobResult(
                path=ctx.input_path,
                kind=ctx.kind,
                status=FileStatus.FAILED,
                error=error,
            )

        if self._store.has_done(ctx.input_path, sha):
            prev = self._store.get(ctx.input_path)
            # Validate that the previous outputs are still usable for THIS
            # run's output_dir.  If the user changed output_dir, or the old
            # outputs were deleted, we can't just reuse them — the new
            # output folder would stay empty.  Fall through to re-process.
            if prev and self._outputs_still_valid(prev, ctx):
                return JobResult(
                    path=ctx.input_path,
                    kind=ctx.kind,
                    status=FileStatus.SKIPPED,
                    heic_path=prev.heic_path if prev else None,
                    mov_path=prev.mov_path if prev else None,
                    output_paths=prev.output_paths if prev else [],
                    duration_ms=prev.duration_ms if prev else 0,
                )
            # Outputs missing or under a different output_dir → re-process.
            logger.info(
                "Re-processing %s: previous outputs not in %s or missing",
                ctx.input_path.name, ctx.output_dir,
            )

        # Stash sha on ctx for stage 2 (process_file needs it).
        ctx._sha = sha  # type: ignore[attr-defined]
        return None

    @staticmethod
    def _outputs_still_valid(prev: JobResult, ctx) -> bool:
        """True if the previous run's outputs are still usable for this run.

        Conditions:
          1. At least one output path exists.
          2. Every output path exists on disk.
          3. Every output path is under the current run's output_dir
             (so changing output_dir re-triggers processing even when the
             old files are still on disk elsewhere).
        """
        if not prev.output_paths:
            return False
        for p in prev.output_paths:
            try:
                if not p.exists():
                    return False
            except OSError:
                return False
            # Check that the output is under the requested output_dir.
            # Resolve both to compare real paths (handles symlinks).
            try:
                p.resolve().relative_to(ctx.output_dir.resolve())
            except ValueError:
                return False
        return True

    def _convert(self, ctx) -> JobResult:
        """Stage 2: convert / copy / symlink."""
        sha = getattr(ctx, "_sha", "")
        result = process_file(
            ctx,
            sha,
            enc_preset=self._enc_preset,
            symlink_fallback=self._symlink_fallback,
        )
        if result.status == FileStatus.FAILED and result.error:
            _log_failure(ctx.input_path, ctx.kind, result.error)
        self._store.record(result, sha, batch_id=self._batch_id)
        return result

    def _emit(self, result: JobResult, ctx) -> None:
        """Push a ProgressEvent onto the queue for WebSocket subscribers."""
        with self._done_lock:
            self._done += 1
            done = self._done
        name = ctx.input_path.name
        event = ProgressEvent(
            path=str(ctx.input_path),
            name=name,
            status=result.status,
            kind=ctx.kind,
            completed=done,
            total=self._total,
            error=result.error,
            duration_ms=result.duration_ms,
            heic_path=str(result.heic_path) if result.heic_path else None,
            mov_path=str(result.mov_path) if result.mov_path else None,
            output_paths=[str(p) for p in result.output_paths],
        )
        try:
            self._event_q.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full — dropping event for %s", name)
        if self._on_event_cb:
            self._on_event_cb(event)
