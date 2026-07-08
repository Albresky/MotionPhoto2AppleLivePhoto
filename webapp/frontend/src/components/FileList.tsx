import { useMemo } from "react";
import {
  KIND_LABELS,
  STATUS_LABELS,
  type FileKind,
  type FileStatus,
  type ScanItem,
} from "../types";

interface Props {
  items: ScanItem[];
  statuses: Record<string, FileStatus>; // path → live status
  errors: Record<string, string>;
  selected: string | null;
  onSelect: (path: string) => void;
}

const STATUS_COLORS: Record<FileStatus, string> = {
  pending: "#94a3b8",
  hashing: "#f59e0b",
  queued: "#94a3b8",
  converting: "#2563eb",
  copying: "#0891b2",
  done: "#16a34a",
  failed: "#dc2626",
  skipped: "#a3a3a3",
};

const KIND_BADGE: Record<FileKind, string> = {
  motion_photo: "#dbeafe",
  still_image: "#f1f5f9",
  video: "#fef3c7",
  unknown: "#fee2e2",
};

export function FileList({
  items,
  statuses,
  errors,
  selected,
  onSelect,
}: Props) {
  // Sort: motion_photo first, then by name — easier to see what will convert.
  const sorted = useMemo(() => {
    const rank: Record<FileKind, number> = {
      motion_photo: 0,
      still_image: 1,
      video: 2,
      unknown: 3,
    };
    return [...items].sort((a, b) => {
      const r = rank[a.kind] - rank[b.kind];
      if (r !== 0) return r;
      return a.path.localeCompare(b.path);
    });
  }, [items]);

  return (
    <div
      style={{
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        overflow: "hidden",
        maxHeight: "60vh",
        overflowY: "auto",
      }}
    >
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead style={{ background: "#f8fafc", position: "sticky", top: 0 }}>
          <tr>
            <th style={cellStyle}>文件</th>
            <th style={cellStyle}>类型</th>
            <th style={cellStyle}>大小</th>
            <th style={cellStyle}>状态</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((it) => {
            const st = statuses[it.path] ?? "pending";
            const err = errors[it.path];
            return (
              <tr
                key={it.path}
                onClick={() => onSelect(it.path)}
                style={{
                  cursor: "pointer",
                  background: selected === it.path ? "#eff6ff" : "transparent",
                  borderBottom: "1px solid #f1f5f9",
                }}
              >
                <td style={{ ...cellStyle, textAlign: "left" }}>
                  {it.path.split("/").pop()}
                  {err && (
                    <div style={{ color: "#dc2626", fontSize: 11, marginTop: 2 }}>
                      {err}
                    </div>
                  )}
                </td>
                <td style={cellStyle}>
                  <span
                    style={{
                      background: KIND_BADGE[it.kind],
                      padding: "2px 8px",
                      borderRadius: 4,
                      fontSize: 11,
                    }}
                  >
                    {KIND_LABELS[it.kind]}
                  </span>
                </td>
                <td style={cellStyle}>{formatSize(it.size)}</td>
                <td style={cellStyle}>
                  <span
                    style={{
                      color: STATUS_COLORS[st],
                      fontWeight: st === "failed" ? 600 : 400,
                    }}
                  >
                    {STATUS_LABELS[st]}
                  </span>
                </td>
              </tr>
            );
          })}
          {sorted.length === 0 && (
            <tr>
              <td
                colSpan={4}
                style={{ ...cellStyle, color: "#94a3b8", padding: 24 }}
              >
                还没有扫描的文件，先在上方输入路径并点扫描
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

const cellStyle: React.CSSProperties = {
  padding: "6px 12px",
  textAlign: "center",
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
