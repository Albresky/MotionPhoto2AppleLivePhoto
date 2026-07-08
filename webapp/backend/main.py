"""FastAPI app — HTTP routes + WebSocket for live progress.

Run:
    uvicorn backend.main:app --reload --port 8000

The app serves the React build at ``/`` in production.  In dev, run Vite
separately (``npm run dev``) and let it proxy ``/api`` to :8000.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Make the repo root importable so ``from mvimg2livephoto.builder import ...``
# works without installing the package.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Make ``backend`` importable when run via ``uvicorn backend.main:app``.
if str(_REPO_ROOT / "webapp") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "webapp"))

from backend.converter_service import classify, sha256_of  # noqa: E402
from backend.models import (  # noqa: E402
    ConvertRequest,
    FileKind,
    FileStatus,
    ScanItem,
    ScanResponse,
    SummaryResponse,
)
from backend.progress_store import ProgressStore  # noqa: E402
from backend.queue_manager import QueueManager  # noqa: E402
from backend.scan_cache import ScanCache  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- lifespan
# DB lives in the user's home dir so it's always writable regardless of
# where the repo was cloned (the repo dir may be owned by a different
# user if it was created inside a VM mount).
_DB_PATH = Path.home() / ".mvimg2livephoto" / "progress.db"
_SCAN_CACHE_PATH = Path.home() / ".mvimg2livephoto" / "scan_cache.db"
_STORE: Optional[ProgressStore] = None
_SCAN_CACHE: Optional["ScanCache"] = None
# Currently active queue — only one batch at a time to keep things simple.
_ACTIVE_QM: Optional[QueueManager] = None
# Currently active scan (for progress tracking)
_ACTIVE_SCAN: Optional[dict] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _STORE, _SCAN_CACHE
    _STORE = ProgressStore(_DB_PATH)
    _SCAN_CACHE = ScanCache(_SCAN_CACHE_PATH)
    logger.info("ProgressStore at %s", _DB_PATH)
    logger.info("ScanCache at %s", _SCAN_CACHE_PATH)
    yield
    if _STORE:
        _STORE.close()
    if _SCAN_CACHE:
        _SCAN_CACHE.close()


app = FastAPI(title="MVIMG2LivePhoto Web", lifespan=lifespan)

# In dev, allow the Vite dev server (5173) to call us.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------- routes

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# --- home directories for the OS we're running on ----------------------
import platform

_IS_MAC = platform.system() == "Darwin"
_HOME = str(Path.home())
_USER = os.environ.get("USER", "user")

# Common roots to offer as starting points in the directory browser.
_BROWSE_ROOTS: list[str] = [
    _HOME,
    f"/Users/{_USER}/Pictures",
    "/Users",
    "/tmp",
]
if _IS_MAC:
    _BROWSE_ROOTS = [
        _HOME,
        f"/Users/{_USER}/Pictures",
        f"/Users/{_USER}/Desktop",
        f"/Users/{_USER}/Downloads",
        "/Volumes",
        "/Users",
        "/tmp",
    ]
else:
    _BROWSE_ROOTS = [
        _HOME,
        f"/home/{_USER}",
        "/mnt",
        "/tmp",
    ]


@app.get("/api/browse")
def browse(path: str | None = None) -> dict:
    """List directories under ``path`` (or return roots if path is None).

    Returns only subdirectories — files are filtered out — because this
    endpoint exists solely to let the user pick a directory.

    Each entry includes ``name``, ``path``, and ``is_dir`` (always true).
    The frontend renders these as a clickable breadcrumb + list.
    """
    if path is None or path == "":
        return {
            "path": "",
            "parent": None,
            "dirs": [
                {"name": r, "path": r, "is_dir": True}
                for r in _BROWSE_ROOTS
                if Path(r).exists()
            ],
            "is_root": True,
        }
    p = Path(path)
    if not p.exists():
        return JSONResponse(
            status_code=400, content={"error": f"Path not found: {path}"}
        )
    if not p.is_dir():
        return JSONResponse(
            status_code=400, content={"error": f"Not a directory: {path}"}
        )
    # Resolve to absolute, no symlink expansion (so /Volumes/... stays readable).
    p = p.absolute()
    parent = str(p.parent) if p.parent != p else None
    dirs = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            try:
                if child.is_dir() and not child.name.startswith("."):
                    dirs.append(
                        {
                            "name": child.name,
                            "path": str(child),
                            "is_dir": True,
                        }
                    )
            except (PermissionError, OSError):
                continue
    except PermissionError:
        return JSONResponse(
            status_code=403, content={"error": f"Permission denied: {path}"}
        )
    return {
        "path": str(p),
        "parent": parent,
        "dirs": dirs,
        "is_root": False,
    }


class ScanPayload(BaseModel):
    path: Path
    recursive: bool = True
    use_cache: bool = True
    reindex: bool = False


@app.post("/api/scan", response_model=ScanResponse)
def scan(payload: ScanPayload) -> ScanResponse:
    """Walk ``payload.path`` and classify every file.

    If ``use_cache`` is True and the directory has been indexed before,
    loads instantly from the SQLite cache instead of walking the disk.

    If ``reindex`` is True, forces a full re-scan even if cached.

    Scan progress is tracked via the global ``_ACTIVE_SCAN`` dict, which
    the frontend can poll via ``/api/scan/progress``.
    """
    global _ACTIVE_SCAN
    root = payload.path
    if not root.exists() or not root.is_dir():
        return JSONResponse(
            status_code=400, content={"error": f"Not a directory: {root}"}
        )

    # Try cache first
    if payload.use_cache and not payload.reindex and _SCAN_CACHE:
        if _SCAN_CACHE.is_cached(root):
            cached = _SCAN_CACHE.load(root)
            items = [
                ScanItem(
                    path=d["path"],
                    kind=d["kind"],
                    size=d["size"],
                    mtime=d["mtime"],
                )
                for d in cached
            ]
            items.sort(key=lambda i: i.path.name)
            return ScanResponse(items=items, total=len(items))

    # Full disk scan
    if payload.recursive:
        walker = root.rglob("*")
    else:
        walker = root.glob("*")

    items: list[ScanItem] = []
    raw_items: list[dict] = []
    count = 0
    for p in walker:
        if not p.is_file():
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        try:
            kind = classify(p)
        except Exception:
            kind = FileKind.UNKNOWN
        items.append(
            ScanItem(path=p, kind=kind, size=st.st_size, mtime=st.st_mtime)
        )
        raw_items.append(
            {
                "path": p,
                "kind": kind,
                "size": st.st_size,
                "mtime": st.st_mtime,
            }
        )
        count += 1
        # Update progress every 5 files
        if count % 5 == 0:
            _ACTIVE_SCAN = {"completed": count, "total": None, "done": False}

    _ACTIVE_SCAN = {"completed": count, "total": count, "done": True}

    # Index into cache (async — don't block the response)
    if _SCAN_CACHE:
        try:
            _SCAN_CACHE.index_directory(root, raw_items)
        except Exception as exc:
            logger.warning("Failed to index scan cache: %s", exc)

    items.sort(key=lambda i: i.path.name)
    return ScanResponse(items=items, total=len(items))


@app.get("/api/scan/progress")
def scan_progress() -> dict:
    """Return the current scan progress.

    Returns: {completed, total, done}
    ``total`` is None while scanning is in progress (we don't know the
    file count in advance).  ``done`` is True when the scan finished.
    """
    if _ACTIVE_SCAN is None:
        return {"completed": 0, "total": None, "done": True}
    return _ACTIVE_SCAN


@app.post("/api/scan/clear_cache")
def clear_scan_cache(path: str | None = None) -> dict:
    """Clear the scan cache for a directory (or all if path is None)."""
    if _SCAN_CACHE is None:
        return {"error": "cache not ready"}
    if path:
        _SCAN_CACHE.clear(Path(path))
        return {"status": "cleared", "path": path}
    # Clear all
    with _SCAN_CACHE._tx() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM scan_index")
    return {"status": "cleared_all"}


# --------------------------------------------------------- materialize
# 把 output 目录里的软链接替换成真实文件拷贝。
#
# 用途：Mac 照片 app 导入时不 follow 软链接，会把软链接当 0 字节损坏
# 文件。转换器对视频/静态图用软链接节省空间，但导入照片前必须把它们
# 换成实体文件。

_ACTIVE_MATERIALIZE: Optional[dict] = None


class MaterializeRequest(BaseModel):
    output_dir: Path
    workers: int = 4


@app.post("/api/materialize")
async def materialize(req: MaterializeRequest) -> dict:
    """Start replacing symlinks in ``output_dir`` with real file copies.

    Scans the output directory for symlinks, then for each:
      1. Read the symlink target
      2. Delete the symlink
      3. Copy the target file to the same path (preserving metadata via
         shutil.copy2)

    Progress is tracked in ``_ACTIVE_MATERIALIZE`` and polled via
    ``/api/materialize/progress``.
    """
    global _ACTIVE_MATERIALIZE
    if _ACTIVE_MATERIALIZE is not None and not _ACTIVE_MATERIALIZE.get("done"):
        return JSONResponse(
            status_code=409,
            content={"error": "A materialize operation is already running"},
        )

    out = req.output_dir
    if not out.is_dir():
        return JSONResponse(
            status_code=400, content={"error": f"Not a directory: {out}"}
        )

    # Collect all symlinks (excluding hidden dirs like .globalTrash)
    symlinks: list[Path] = []
    for p in out.rglob("*"):
        if not p.is_symlink():
            continue
        if any(part.startswith(".") for part in p.relative_to(out).parts[:-1]):
            continue
        if p.name.startswith(".") or p.name.startswith("._"):
            continue
        symlinks.append(p)

    total = len(symlinks)
    if total == 0:
        return {"status": "nothing_to_do", "total": 0}

    _ACTIVE_MATERIALIZE = {
        "completed": 0,
        "total": total,
        "done": False,
        "errors": [],
    }

    workers = max(1, min(req.workers, 16))

    def _do_materialize():
        import shutil
        from concurrent.futures import ThreadPoolExecutor

        completed = 0
        errors: list[dict] = []

        def _replace_one(link: Path) -> None:
            nonlocal completed
            try:
                target = Path(os.readlink(link))
                if not target.exists():
                    errors.append({
                        "path": str(link),
                        "error": f"Target not found: {target}",
                    })
                    return
                # Delete symlink then copy target to same path.
                # copy2 preserves mtime, mode, and (on macOS) some flags.
                link.unlink()
                shutil.copy2(target, link)
            except Exception as exc:
                errors.append({
                    "path": str(link),
                    "error": f"{type(exc).__name__}: {exc}",
                })
            finally:
                completed += 1
                # Update progress every few files
                if completed % 5 == 0 or completed == total:
                    _ACTIVE_MATERIALIZE["completed"] = completed  # type: ignore

        with ThreadPoolExecutor(max_workers=workers) as pool:
            pool.map(_replace_one, symlinks)

        _ACTIVE_MATERIALIZE["completed"] = completed  # type: ignore
        _ACTIVE_MATERIALIZE["errors"] = errors  # type: ignore
        _ACTIVE_MATERIALIZE["done"] = True  # type: ignore

    # Run in a thread so we don't block the event loop
    import threading
    t = threading.Thread(target=_do_materialize, daemon=True)
    t.start()

    return {"status": "started", "total": total, "workers": workers}


@app.get("/api/materialize/progress")
def materialize_progress() -> dict:
    """Return the current materialize progress.

    Returns: {completed, total, done, errors}
    """
    if _ACTIVE_MATERIALIZE is None:
        return {"completed": 0, "total": 0, "done": True, "errors": []}
    return _ACTIVE_MATERIALIZE


@app.post("/api/convert")
async def convert(req: ConvertRequest) -> dict:
    """Start a conversion batch.  Returns immediately; progress flows over
    the WebSocket."""
    global _ACTIVE_QM
    if _ACTIVE_QM is not None:
        return JSONResponse(
            status_code=409,
            content={"error": "A batch is already running"},
        )
    if _STORE is None:
        return JSONResponse(status_code=500, content={"error": "Store not ready"})

    # Build (input_root, file_path, output_dir) tuples.
    # Each item carries its own absolute path; derive input_root from the
    # common path.  For individually picked files the parent is the root.
    paths = [i.path for i in req.items]
    if not paths:
        return JSONResponse(status_code=400, content={"error": "No items"})
    common = Path(os.path.commonpath([str(p) for p in paths])) \
        if len(paths) > 1 else paths[0].parent

    work_items = [(common, p, req.output_dir) for p in paths]

    workers = max(1, req.workers)
    _ACTIVE_QM = QueueManager(_STORE)

    async def _run():
        global _ACTIVE_QM
        try:
            await _ACTIVE_QM.run(
                work_items,
                workers,
                enc_preset=req.enc_preset,
                symlink_fallback=req.symlink_fallback,
            )
        finally:
            _ACTIVE_QM = None

    asyncio.create_task(_run())
    return {"status": "started", "total": len(work_items), "workers": workers}


@app.get("/api/batch_id")
def get_batch_id() -> dict:
    """Return the batch id of the currently active or last batch.

    The frontend uses this to scope /api/output_items and /api/summary to
    "this run" instead of the full history.
    """
    if _ACTIVE_QM is not None:
        return {"batch_id": _ACTIVE_QM.batch_id}
    return {"batch_id": None}


@app.post("/api/pause")
def pause_batch() -> dict:
    """Pause submission of new items to the queue.

    Already-submitted work (hash + convert) keeps running.  Returns 409
    if no batch is active.
    """
    if _ACTIVE_QM is None:
        return JSONResponse(
            status_code=409, content={"error": "No active batch"}
        )
    _ACTIVE_QM.pause()
    return {"status": "paused"}


@app.post("/api/resume")
def resume_batch() -> dict:
    """Resume submission of new items after a pause."""
    if _ACTIVE_QM is None:
        return JSONResponse(
            status_code=409, content={"error": "No active batch"}
        )
    _ACTIVE_QM.resume()
    return {"status": "running"}


@app.get("/api/summary", response_model=SummaryResponse)
def summary(batch_id: str | None = None) -> SummaryResponse:
    """Return a summary of the current/last batch.

    If ``batch_id`` is given, only count that batch's results.  Otherwise
    (no batch_id, e.g. before any conversion starts) return totals for
    the whole history — the front-end usually passes the active batch id.
    """
    if _STORE is None:
        return SummaryResponse(total=0, done=0, failed=0, skipped=0, results=[])
    conn = _STORE._conn  # noqa: SLF001 — internal use
    if batch_id:
        rows = conn.execute(
            "SELECT input_path, kind, status, heic_path, mov_path, "
            "       output_paths, error, duration_ms FROM progress "
            "WHERE batch_id = ?",
            (batch_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT input_path, kind, status, heic_path, mov_path, "
            "       output_paths, error, duration_ms FROM progress"
        ).fetchall()
    results = []
    done = failed = skipped = 0
    for r in rows:
        status = FileStatus(r[2])
        if status == FileStatus.DONE:
            done += 1
        elif status == FileStatus.FAILED:
            failed += 1
        elif status == FileStatus.SKIPPED:
            skipped += 1
        results.append(
            {
                "path": r[0],
                "kind": r[1],
                "status": r[2],
                "heic_path": r[3],
                "mov_path": r[4],
                "output_paths": _parse_paths_json(r[5]),
                "error": r[6],
                "duration_ms": r[7] if len(r) > 7 and r[7] is not None else 0,
            }
        )
    return SummaryResponse(
        total=len(results), done=done, failed=failed, skipped=skipped,
        results=results,
    )


@app.get("/api/output_items")
def output_items(
    page: int = 1,
    page_size: int = 50,
    batch_id: str | None = None,
) -> dict:
    """Return a paginated list of processed files for the output column.

    If ``batch_id`` is given, only return that batch's records.  Otherwise
    return everything in the store (full history).
    """
    if _STORE is None:
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "pages": 1,
        }
    page = max(1, page)
    page_size = max(1, min(500, page_size))
    if batch_id:
        total = _STORE.count_batch(batch_id)
        offset = (page - 1) * page_size
        rows = _STORE.list_batch(batch_id, limit=page_size, offset=offset)
    else:
        total = _STORE.count()
        offset = (page - 1) * page_size
        rows = _STORE.list_all(limit=page_size, offset=offset)
    items = [
        {
            "input_path": str(r.path),
            "kind": r.kind.value,
            "status": r.status.value,
            "heic_path": str(r.heic_path) if r.heic_path else None,
            "mov_path": str(r.mov_path) if r.mov_path else None,
            "output_paths": [str(p) for p in r.output_paths],
            "error": r.error,
            "duration_ms": r.duration_ms,
        }
        for r in rows
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if page_size else 1,
    }


def _parse_paths_json(s: str | None) -> list[str]:
    """Parse a JSON array of path strings.  Empty/invalid → []."""
    if not s:
        return []
    try:
        return list(json.loads(s))
    except (json.JSONDecodeError, TypeError):
        return []


@app.get("/api/file")
def get_file(path: str) -> FileResponse:
    """Serve a file (input or output) for preview.

    Used by the frontend's <img> / <video> tags.  Path is absolute.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return JSONResponse(status_code=404, content={"error": "Not found"})
    return FileResponse(p)


@app.get("/api/failed")
def list_failed() -> list[dict]:
    """Return files that failed last time, so the user can retry them."""
    if _STORE is None:
        return []
    return [
        {
            "path": str(r.path),
            "kind": r.kind.value,
            "error": r.error,
        }
        for r in _STORE.list_failed()
    ]


# ------------------------------------------------------------- websocket

@app.websocket("/ws/progress")
async def ws_progress(ws: WebSocket) -> None:
    """Stream ProgressEvents until the batch ends or client disconnects."""
    await ws.accept()
    if _ACTIVE_QM is None:
        await ws.send_json({"type": "idle"})
        await ws.close()
        return
    queue = _ACTIVE_QM.events()
    try:
        while True:
            event = await queue.get()
            if event is None:  # batch finished
                await ws.send_json({"type": "done"})
                break
            await ws.send_json(
                {
                    "type": "progress",
                    "path": event.path,
                    "name": event.name,
                    "status": event.status.value,
                    "kind": event.kind.value,
                    "completed": event.completed,
                    "total": event.total,
                    "error": event.error,
                    "duration_ms": event.duration_ms,
                }
            )
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception:  # pragma: no cover
        logger.exception("WebSocket error")


# --------------------------------------------------------- serve frontend

_FRONTEND_DIST = _REPO_ROOT / "webapp" / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=_FRONTEND_DIST / "assets"),
        name="assets",
    )

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_FRONTEND_DIST / "index.html")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        # SPA fallback: serve index.html for any non-API path.
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            return JSONResponse(status_code=404, content={"error": "Not found"})
        return FileResponse(_FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    def index_no_frontend() -> dict:
        return {
            "message": "Frontend not built.  Run `npm run build` in webapp/frontend.",
            "api_docs": "/docs",
        }
