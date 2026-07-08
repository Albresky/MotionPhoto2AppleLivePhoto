import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchBatchId,
  fetchFailed,
  fetchMaterializeProgress,
  fetchOutputItems,
  fetchSummary,
  fetchScanProgress,
  openProgressSocket,
  pauseBatch,
  resumeBatch,
  scanDirectory,
  startConvert,
  startMaterialize,
  type OutputItem,
} from "./api";
import { DropZone } from "./components/DropZone";
import { FileColumn } from "./components/FileColumn";
import { ProgressBar } from "./components/ProgressBar";
import { SettingsPanel } from "./components/SettingsPanel";
import { usePersistentState } from "./hooks/usePersistentState";
import {
  KIND_LABELS,
  type FileKind,
  type FileStatus,
  type ScanItem,
  type SummaryResponse,
} from "./types";

const CPU_DEFAULT = Math.max(
  1,
  Math.floor((navigator.hardwareConcurrency || 8) * 0.8),
);
const PAGE_SIZE = 50;

export function App() {
  // --- persistent settings (remembered across sessions)
  const [inputDir, setInputDir] = usePersistentState<string>(
    "mvimg:last_input_dir",
    "",
  );
  const [outputDir, setOutputDir] = usePersistentState<string>(
    "mvimg:last_output_dir",
    "",
  );
  const [workers, setWorkers] = usePersistentState<number>(
    "mvimg:workers",
    CPU_DEFAULT,
  );
  const [hdr, setHdr] = usePersistentState<boolean>("mvimg:hdr", false);
  const [encPreset, setEncPreset] = usePersistentState<string>(
    "mvimg:enc_preset",
    "ultrafast",
  );
  const [symlinkFallback, setSymlinkFallback] = usePersistentState<boolean>(
    "mvimg:symlink_fallback",
    false,
  );

  // --- scan state
  const [items, setItems] = useState<ScanItem[]>([]);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [scanProgress, setScanProgress] = useState<{
    completed: number;
    total: number | null;
    fromCache: boolean;
  } | null>(null);
  const scanProgressTimer = useRef<ReturnType<typeof setInterval> | null>(
    null,
  );

  // --- runtime state
  const [statuses, setStatuses] = useState<Record<string, FileStatus>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [durations, setDurations] = useState<Record<string, number>>({});
  const [completed, setCompleted] = useState(0);
  const [running, setRunning] = useState(false);
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  // ID of the batch currently (or last) running, so we can scope output
  // and summary to "this run" instead of the full history.
  const [batchId, setBatchId] = useState<string | null>(null);
  // Whether submission of new items is paused (the convert button area
  // shows a pause/resume toggle while a batch is running).
  const [paused, setPaused] = useState(false);

  // Materialize: replacing symlinks in output dir with real file copies
  // so Mac Photos can import them.  Tracks progress polled from backend.
  const [materializeProgress, setMaterializeProgress] = useState<{
    completed: number;
    total: number;
    done: boolean;
    errors: { path: string; error: string }[];
  } | null>(null);
  const materializeTimer = useRef<ReturnType<typeof setInterval> | null>(
    null,
  );

  // --- input list pagination (front-end side; all items are already in
  //     memory after scan, but we only render the current page's slice).
  const [inputPage, setInputPage] = useState(1);

  // --- output list (paginated)
  const [outputItems, setOutputItems] = useState<OutputItem[]>([]);
  const [outputTotal, setOutputTotal] = useState(0);
  const [outputPage, setOutputPage] = useState(1);
  const [selectedInput, setSelectedInput] = useState<string | null>(null);
  const [selectedOutput, setSelectedOutput] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const liveOutputPathsRef = useRef<Set<string>>(new Set());

  // Load failed files on mount.
  useEffect(() => {
    fetchFailed().then((failed) => {
      if (failed.length === 0) return;
      const errs: Record<string, string> = {};
      for (const f of failed) errs[f.path] = f.error || "之前失败";
      setErrors(errs);
    }).catch(() => {});
  }, []);

  // Auto-scan if we have a remembered input dir.
  useEffect(() => {
    if (inputDir) {
      handleScan([inputDir]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Clean up WebSocket.
  useEffect(() => {
    return () => wsRef.current?.close();
  }, []);

  // Load output items for a given batch (or all if batchId is null).
  const refreshOutput = useCallback(
    async (page = 1, bid: string | null = null) => {
      try {
        const res = await fetchOutputItems(page, PAGE_SIZE, bid ?? undefined);
        setOutputItems(res.items);
        setOutputTotal(res.total);
        setOutputPage(res.page);
      } catch {
        // Backend not ready.
      }
    },
    [],
  );

  const appendLiveOutput = useCallback((item: OutputItem) => {
    const existed = liveOutputPathsRef.current.has(item.input_path);
    liveOutputPathsRef.current.add(item.input_path);
    setOutputItems((prev) => {
      const next = prev.filter((existing) => existing.input_path !== item.input_path);
      return [item, ...next].slice(0, PAGE_SIZE);
    });
    setOutputTotal((prev) => prev + (existed ? 0 : 1));
    setOutputPage(1);
  }, []);

  useEffect(() => {
    refreshOutput(1);
  }, [refreshOutput]);

  // Refetch output items when the user changes page in the output column.
  // Uses the current batch_id so pagination stays within the batch.
  useEffect(() => {
    if (batchId === null && outputItems.length === 0) return;
    refreshOutput(outputPage, batchId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outputPage]);

  const handleScan = useCallback(
    async (paths: string[], opts?: { reindex?: boolean }) => {
      if (paths.length === 0) return;
      setScanning(true);
      setScanError(null);
      setScanProgress(null);

      // Poll scan progress every 500ms while scanning
      const pollProgress = async () => {
        try {
          const p = await fetchScanProgress();
          setScanProgress({
            completed: p.completed,
            total: p.total,
            fromCache: false,
          });
        } catch {
          // ignore
        }
      };
      scanProgressTimer.current = setInterval(pollProgress, 500);

      try {
        const res = await scanDirectory(paths[0], true, opts);
        setItems(res.items);
        setStatuses({});
        setErrors({});
        setDurations({});
        setCompleted(0);
        setSummary(null);
        setInputPage(1);
        setInputDir(paths[0]);
        if (!outputDir) {
          const parts = paths[0].replace(/\/$/, "").split("/");
          parts[parts.length - 1] = "output";
          setOutputDir(parts.join("/"));
        }
        setScanProgress({
          completed: res.total,
          total: res.total,
          fromCache: true,
        });
      } catch (e) {
        setScanError((e as Error).message);
      } finally {
        setScanning(false);
        if (scanProgressTimer.current) {
          clearInterval(scanProgressTimer.current);
          scanProgressTimer.current = null;
        }
      }
    },
    [outputDir, setInputDir, setOutputDir],
  );

  const handleConvert = useCallback(async () => {
    if (items.length === 0 || !outputDir) return;
    setRunning(true);
    setCompleted(0);
    setStatuses({});
    setErrors({});
    setDurations({});
    setSummary(null);
    setBatchId(null);
    setPaused(false);
    liveOutputPathsRef.current.clear();

    const initStatus: Record<string, FileStatus> = {};
    for (const it of items) initStatus[it.path] = "pending";
    setStatuses(initStatus);

    try {
      await startConvert({
        items,
        output_dir: outputDir,
        workers,
        hdr,
        enc_preset: encPreset,
        symlink_fallback: symlinkFallback,
      });
    } catch (e) {
      setRunning(false);
      setErrors({ global: (e as Error).message });
      return;
    }

    // Fetch the batch_id so we can scope output/summary to this run.
    const bid = await fetchBatchId();
    setBatchId(bid);
    // Pre-clear the output column so old history isn't shown mixed with
    // the new batch's results as they arrive.
    setOutputItems([]);
    setOutputTotal(0);
    setOutputPage(1);

    const ws = openProgressSocket((evt) => {
      if (evt.type === "done") {
        setRunning(false);
        setPaused(false);
        fetchSummary(bid ?? undefined).then(setSummary).catch(() => {});
        refreshOutput(1, bid);
        wsRef.current?.close();
        return;
      }
      if (evt.type === "idle") {
        setRunning(false);
        return;
      }
      if (evt.type === "progress" && evt.path) {
        const path = evt.path;
        setStatuses((prev) => ({ ...prev, [path]: evt.status! }));
        if (evt.error) {
          setErrors((prev) => ({ ...prev, [path]: evt.error! }));
        }
        if (typeof evt.duration_ms === "number") {
          setDurations((prev) => ({ ...prev, [path]: evt.duration_ms! }));
        }
        setCompleted(evt.completed ?? 0);
        if (
          (evt.status === "done" || evt.status === "skipped") &&
          evt.output_paths &&
          evt.output_paths.length > 0
        ) {
          appendLiveOutput({
            input_path: path,
            kind: evt.kind!,
            status: evt.status,
            heic_path: evt.heic_path ?? null,
            mov_path: evt.mov_path ?? null,
            output_paths: evt.output_paths,
            error: evt.error ?? null,
            duration_ms: evt.duration_ms,
          });
        }
      }
    });
    wsRef.current = ws;
  }, [
    items,
    outputDir,
    workers,
    hdr,
    encPreset,
    symlinkFallback,
    refreshOutput,
    appendLiveOutput,
  ]);

  const handleRetry = useCallback(() => {
    const failedPaths = Object.entries(errors)
      .filter(([_, e]) => e && e !== "之前失败")
      .map(([p]) => p);
    if (failedPaths.length === 0) return;
    const failedItems = items.filter((it) => failedPaths.includes(it.path));
    setItems(failedItems);
    setErrors({});
    setCompleted(0);
    setSummary(null);
    setTimeout(() => handleConvert(), 0);
  }, [errors, items, handleConvert]);

  const handlePauseResume = useCallback(async () => {
    try {
      if (paused) {
        await resumeBatch();
        setPaused(false);
      } else {
        await pauseBatch();
        setPaused(true);
      }
    } catch {
      // ignore — button will retry on next click
    }
  }, [paused]);

  const handleMaterialize = useCallback(async () => {
    if (!outputDir) return;
    try {
      const res = await startMaterialize(outputDir, workers);
      if ("error" in res) {
        setErrors({ global: res.error });
        return;
      }
      if (res.total === 0) {
        setErrors({ global: "输出目录里没有软链接，无需转换" });
        return;
      }
      // Start polling progress
      setMaterializeProgress({
        completed: 0,
        total: res.total,
        done: false,
        errors: [],
      });
      const poll = async () => {
        const p = await fetchMaterializeProgress();
        setMaterializeProgress(p);
        if (p.done) {
          if (materializeTimer.current) {
            clearInterval(materializeTimer.current);
            materializeTimer.current = null;
          }
        }
      };
      materializeTimer.current = setInterval(poll, 500);
    } catch (e) {
      setErrors({ global: (e as Error).message });
    }
  }, [outputDir, workers]);

  // Clean up materialize timer on unmount
  useEffect(() => {
    return () => {
      if (materializeTimer.current) {
        clearInterval(materializeTimer.current);
      }
    };
  }, []);

  // --- rows for the two columns
  // Input: slice to the current page so we only render ~50 rows even
  // when the scan returned 14k items.
  const inputStart = (inputPage - 1) * PAGE_SIZE;
  const inputRows = useMemo(
    () =>
      items.slice(inputStart, inputStart + PAGE_SIZE).map((it) => ({
        path: it.path,
        name: it.path.split("/").pop() || it.path,
        kind: it.kind,
        size: it.size,
        status: statuses[it.path],
        duration_ms: durations[it.path],
      })),
    [items, statuses, durations, inputStart],
  );

  const outputRows = useMemo(
    () =>
      outputItems.map((o) => ({
        path: o.output_paths[0] || o.input_path,
        name: (o.output_paths[0] || o.input_path).split("/").pop() || "",
        kind: o.kind as FileKind,
        status: o.status as FileStatus,
        error: o.error,
        duration_ms: o.duration_ms,
      })),
    [outputItems],
  );

  const counts = useMemo(() => {
    const c: Record<FileKind, number> = {
      motion_photo: 0,
      still_image: 0,
      video: 0,
      unknown: 0,
    };
    for (const it of items) c[it.kind] += 1;
    return c;
  }, [items]);

  return (
    <div style={{ maxWidth: 1400, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontSize: 24, fontWeight: 600, marginBottom: 4 }}>
        MVIMG → Live Photo 转换器
      </h1>
      <div style={{ fontSize: 13, color: "#94a3b8", marginBottom: 20 }}>
        Android Motion Photo → iPhone Live Photo · 批量转换 · 断点续传 · 元数据保留
      </div>

      <DropZone onPaths={handleScan} disabled={scanning || running} />
      {inputDir && (
        <div style={rememberedStyle}>
          上次输入: <code>{inputDir}</code>
          <button
            onClick={() => handleScan([inputDir], { reindex: true })}
            disabled={scanning || running}
            style={{
              marginLeft: 8,
              padding: "2px 8px",
              fontSize: 11,
              border: "1px solid #cbd5e1",
              borderRadius: 4,
              background: "transparent",
              cursor: scanning || running ? "not-allowed" : "pointer",
              color: scanning || running ? "#94a3b8" : "#475569",
            }}
          >
            重新扫描
          </button>
        </div>
      )}

      {scanning && scanProgress && (
        <div style={scanProgressStyle}>
          <div style={{ marginBottom: 4, fontSize: 13, color: "#475569" }}>
            扫描中… 已索引 {scanProgress.completed} 个文件
            {scanProgress.total ? ` / ${scanProgress.total}` : ""}
          </div>
          <div style={progressBarBg}>
            <div
              style={{
                ...progressBarFill,
                width: scanProgress.total
                  ? `${(scanProgress.completed / scanProgress.total) * 100}%`
                  : "100%",
                animation: !scanProgress.total
                  ? "pulse 1.5s ease-in-out infinite"
                  : undefined,
              }}
            />
          </div>
        </div>
      )}

      {!scanning && scanProgress?.fromCache && inputDir && (
        <div
          style={{
            ...rememberedStyle,
            color: "#16a34a",
          }}
        >
          从缓存加载 {scanProgress.total} 个文件
        </div>
      )}

      {scanError && <div style={errBoxStyle}>{scanError}</div>}
      {errors.global && <div style={errBoxStyle}>{errors.global}</div>}

      {items.length > 0 && (
        <>
          <div style={summaryBarStyle}>
            共 {items.length} 个文件 ·
            {" "}{KIND_LABELS.motion_photo} {counts.motion_photo} ·
            {" "}{KIND_LABELS.still_image} {counts.still_image} ·
            {" "}{KIND_LABELS.video} {counts.video} ·
            {" "}{KIND_LABELS.unknown} {counts.unknown}
          </div>

          <div style={{ marginTop: 16 }}>
            <SettingsPanel
              outputDir={outputDir}
              setOutputDir={setOutputDir}
              workers={workers}
              setWorkers={setWorkers}
              hdr={hdr}
              setHdr={setHdr}
              encPreset={encPreset}
              setEncPreset={setEncPreset}
              symlinkFallback={symlinkFallback}
              setSymlinkFallback={setSymlinkFallback}
              disabled={running}
            />
          </div>

          <ProgressBar
            completed={completed}
            total={items.length}
            running={running}
          />

          <div style={{ marginTop: 16, display: "flex", gap: 8, alignItems: "center" }}>
            <button
              onClick={handleConvert}
              disabled={running || items.length === 0 || !outputDir}
              style={primaryBtn(running || items.length === 0 || !outputDir)}
            >
              {running && (
                <span
                  style={{
                    display: "inline-block",
                    width: 14,
                    height: 14,
                    border: "2px solid rgba(255,255,255,0.4)",
                    borderTopColor: "#fff",
                    borderRadius: "50%",
                    animation: "spin 0.8s linear infinite",
                    marginRight: 6,
                    verticalAlign: "middle",
                  }}
                />
              )}
              {running ? "转换中…" : "开始转换"}
            </button>
            {running && (
              <button
                onClick={handlePauseResume}
                style={{
                  ...secondaryBtn(false),
                  background: paused ? "#f59e0b" : "#fff",
                  color: paused ? "#fff" : "#0f172a",
                  borderColor: paused ? "#f59e0b" : "#cbd5e1",
                }}
              >
                {paused ? "继续" : "暂停"}
              </button>
            )}
            <button
              onClick={handleRetry}
              disabled={running || Object.keys(errors).length === 0}
              style={secondaryBtn(
                running || Object.keys(errors).length === 0,
              )}
            >
              重试失败项 ({Object.keys(errors).length})
            </button>
            <button
              onClick={handleMaterialize}
              disabled={
                !outputDir ||
                (materializeProgress != null && !materializeProgress.done)
              }
              style={secondaryBtn(
                !outputDir ||
                  (materializeProgress != null && !materializeProgress.done),
              )}
            >
              软链接转实体文件
            </button>
          </div>

          {materializeProgress && !materializeProgress.done && (
            <div style={scanProgressStyle}>
              <div style={{ marginBottom: 4, fontSize: 13, color: "#475569" }}>
                实体化中… 已复制 {materializeProgress.completed} /{" "}
                {materializeProgress.total} 个文件
              </div>
              <div style={progressBarBg}>
                <div
                  style={{
                    ...progressBarFill,
                    width: `${
                      materializeProgress.total > 0
                        ? (materializeProgress.completed /
                            materializeProgress.total) *
                          100
                        : 0
                    }%`,
                  }}
                />
              </div>
            </div>
          )}

          {materializeProgress &&
            materializeProgress.done &&
            materializeProgress.total > 0 && (
              <div
                style={{
                  ...scanProgressStyle,
                  color:
                    materializeProgress.errors.length > 0
                      ? "#dc2626"
                      : "#16a34a",
                }}
              >
                实体化完成：{materializeProgress.completed}/
                {materializeProgress.total} 已复制
                {materializeProgress.errors.length > 0 && (
                  <>
                    ，失败 {materializeProgress.errors.length} 个
                    <div style={{ marginTop: 4, fontSize: 12 }}>
                      {materializeProgress.errors
                        .slice(0, 5)
                        .map((e) => `${e.path}: ${e.error}`)
                        .join("\n")}
                    </div>
                  </>
                )}
              </div>
            )}

          {/* Two-column layout: input on the left, output on the right */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: 16,
              marginTop: 24,
            }}
          >
            <FileColumn
              title="输入"
              rows={inputRows}
              page={inputPage}
              pageSize={PAGE_SIZE}
              total={items.length}
              onPageChange={setInputPage}
              selected={selectedInput}
              onSelect={setSelectedInput}
              statusHint={
                running
                  ? `转换中 ${completed}/${items.length} (${items.length === 0 ? 0 : Math.round((completed / items.length) * 100)}%)`
                  : scanning
                    ? "扫描中…"
                    : undefined
              }
            />
            <FileColumn
              title="输出"
              rows={outputRows}
              page={outputPage}
              pageSize={PAGE_SIZE}
              total={outputTotal}
              onPageChange={setOutputPage}
              selected={selectedOutput}
              onSelect={setSelectedOutput}
              statusHint={
                outputTotal > 0 ? `${outputTotal} 个已处理` : undefined
              }
            />
          </div>

          {summary && (
            <div style={summaryStyle}>
              <h3 style={{ marginTop: 0 }}>本次结果</h3>
              <div>
                共 {summary.total} · 成功 {summary.done} ·
                {" "}跳过 {summary.skipped} · 失败 {summary.failed}
              </div>
              {summary.failed > 0 && (
                <div style={{ marginTop: 8, fontSize: 13, color: "#dc2626" }}>
                  失败的文件已记录，可点"重试失败项"再次转换。
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

const rememberedStyle: React.CSSProperties = {
  marginTop: 8,
  fontSize: 12,
  color: "#64748b",
  padding: "6px 12px",
  background: "#f8fafc",
  borderRadius: 6,
};

const errBoxStyle: React.CSSProperties = {
  marginTop: 12,
  padding: "10px 14px",
  background: "#fef2f2",
  color: "#dc2626",
  border: "1px solid #fecaca",
  borderRadius: 8,
  fontSize: 13,
};

const summaryBarStyle: React.CSSProperties = {
  marginTop: 16,
  padding: "8px 14px",
  background: "#f8fafc",
  borderRadius: 8,
  fontSize: 13,
  color: "#475569",
};

const summaryStyle: React.CSSProperties = {
  marginTop: 16,
  padding: 16,
  background: "#f0fdf4",
  border: "1px solid #bbf7d0",
  borderRadius: 8,
  fontSize: 14,
};

function primaryBtn(disabled: boolean): React.CSSProperties {
  return {
    padding: "10px 20px",
    borderRadius: 8,
    border: "none",
    background: disabled ? "#94a3b8" : "#2563eb",
    color: "#fff",
    fontSize: 14,
    fontWeight: 500,
    cursor: disabled ? "not-allowed" : "pointer",
  };
}

function secondaryBtn(disabled: boolean): React.CSSProperties {
  return {
    padding: "10px 20px",
    borderRadius: 8,
    border: "1px solid #cbd5e1",
    background: disabled ? "#f1f5f9" : "#fff",
    color: disabled ? "#94a3b8" : "#0f172a",
    fontSize: 14,
    cursor: disabled ? "not-allowed" : "pointer",
  };
}

const scanProgressStyle: React.CSSProperties = {
  marginTop: 12,
  padding: 12,
  background: "#f8fafc",
  borderRadius: 8,
};

const progressBarBg: React.CSSProperties = {
  height: 6,
  background: "#e2e8f0",
  borderRadius: 3,
  overflow: "hidden",
};

const progressBarFill: React.CSSProperties = {
  height: "100%",
  background: "#2563eb",
  borderRadius: 3,
  transition: "width 0.3s ease",
};
