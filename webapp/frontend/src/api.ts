// Thin HTTP client.  All endpoints return parsed JSON.
// In dev, Vite proxies /api → http://127.0.0.1:8000 (see vite.config.ts).

import type {
  ConvertRequest,
  ProgressEvent as MmProgressEvent,
  ScanResponse,
  SummaryResponse,
} from "./types";

export interface BrowseEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

export interface BrowseResponse {
  path: string;
  parent: string | null;
  dirs: BrowseEntry[];
  is_root: boolean;
}

export async function browseDirectory(
  path: string | null,
): Promise<BrowseResponse> {
  const url = path
    ? `/api/browse?path=${encodeURIComponent(path)}`
    : "/api/browse";
  const res = await fetch(url);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Browse failed: ${res.status}`);
  }
  return res.json();
}

export async function scanDirectory(
  path: string,
  recursive = true,
  options: { useCache?: boolean; reindex?: boolean } = {},
): Promise<ScanResponse> {
  const body = { path, recursive, ...options };
  const res = await fetch("/api/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Scan failed: ${res.status}`);
  }
  return res.json();
}

export interface ScanProgress {
  completed: number;
  total: number | null;
  done: boolean;
}

export async function fetchScanProgress(): Promise<ScanProgress> {
  const res = await fetch("/api/scan/progress");
  if (!res.ok) throw new Error(`Progress failed: ${res.status}`);
  return res.json();
}

export async function clearScanCache(path?: string): Promise<void> {
  const url = path
    ? `/api/scan/clear_cache?path=${encodeURIComponent(path)}`
    : "/api/scan/clear_cache";
  await fetch(url, { method: "POST" });
}

export async function startConvert(req: ConvertRequest): Promise<void> {
  const res = await fetch("/api/convert", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok && res.status !== 409) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Convert failed: ${res.status}`);
  }
}

export async function fetchSummary(batchId?: string): Promise<SummaryResponse> {
  const url = batchId
    ? `/api/summary?batch_id=${encodeURIComponent(batchId)}`
    : "/api/summary";
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Summary failed: ${res.status}`);
  return res.json();
}

export interface OutputItem {
  input_path: string;
  kind: string;
  status: string;
  heic_path: string | null;
  mov_path: string | null;
  output_paths: string[];
  error: string | null;
  duration_ms?: number;
}

export interface OutputItemsResponse {
  items: OutputItem[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export async function fetchOutputItems(
  page = 1,
  pageSize = 50,
  batchId?: string,
): Promise<OutputItemsResponse> {
  let url = `/api/output_items?page=${page}&page_size=${pageSize}`;
  if (batchId) url += `&batch_id=${encodeURIComponent(batchId)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Output items failed: ${res.status}`);
  return res.json();
}

export async function fetchBatchId(): Promise<string | null> {
  const res = await fetch("/api/batch_id");
  if (!res.ok) return null;
  const data = await res.json();
  return data.batch_id;
}

export async function pauseBatch(): Promise<void> {
  await fetch("/api/pause", { method: "POST" });
}

export async function resumeBatch(): Promise<void> {
  await fetch("/api/resume", { method: "POST" });
}

export interface MaterializeProgress {
  completed: number;
  total: number;
  done: boolean;
  errors: { path: string; error: string }[];
}

export async function startMaterialize(
  outputDir: string,
  workers: number = 4,
): Promise<{ status: string; total: number; workers: number } | { error: string }> {
  const res = await fetch("/api/materialize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ output_dir: outputDir, workers }),
  });
  return res.json();
}

export async function fetchMaterializeProgress(): Promise<MaterializeProgress> {
  const res = await fetch("/api/materialize/progress");
  if (!res.ok) {
    return { completed: 0, total: 0, done: true, errors: [] };
  }
  return res.json();
}

export async function fetchFailed(): Promise<
  { path: string; kind: string; error: string }[]
> {
  const res = await fetch("/api/failed");
  if (!res.ok) return [];
  return res.json();
}

// Open a WebSocket for live progress.  Returns the socket (caller closes).
export function openProgressSocket(
  onMessage: (e: MmProgressEvent) => void,
  onClose?: () => void,
): WebSocket {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${window.location.host}/ws/progress`;
  const ws = new WebSocket(url);
  ws.onmessage = (ev) => {
    try {
      onMessage(JSON.parse(ev.data));
    } catch (e) {
      console.error("Bad WS message", e);
    }
  };
  ws.onclose = () => onClose?.();
  return ws;
}

// File preview URL — backend streams the file content.
export function filePreviewUrl(path: string): string {
  return `/api/file?path=${encodeURIComponent(path)}`;
}
