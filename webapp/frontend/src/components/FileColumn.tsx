import { filePreviewUrl } from "../api";
import {
  KIND_LABELS,
  STATUS_LABELS,
  type FileKind,
  type FileStatus,
} from "../types";

interface Row {
  path: string;            // input or output path (first one if multiple)
  name: string;
  kind?: FileKind;
  status?: FileStatus;
  size?: number;
  error?: string | null;
  duration_ms?: number;
}

interface Props {
  title: "输入" | "输出";
  rows: Row[];
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (p: number) => void;
  selected: string | null;
  onSelect: (path: string) => void;
  /** Live badge text (e.g. "转换中…" or "完成"). */
  statusHint?: string;
}

/**
 * One column of the two-column layout:
 *
 *   ┌────────────────────────┐
 *   │  预览                  │
 *   ├────────────────────────┤
 *   │  分页表格              │
 *   │  fileA   <kind>  <st>  │
 *   │  fileB   <kind>  <st>  │
 *   │  ...                   │
 *   │  ‹ 1 2 3 4 ›   50/页   │
 *   └────────────────────────┘
 */
export function FileColumn({
  title,
  rows,
  page,
  pageSize,
  total,
  onPageChange,
  selected,
  onSelect,
  statusHint,
}: Props) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, pages);

  const previewPath = selected ?? rows[0]?.path ?? null;
  const previewName = previewPath?.split("/").pop() ?? "(无选择)";

  return (
    <div style={colWrap}>
      <div style={colHeader}>
        <span style={{ fontWeight: 600 }}>{title}</span>
        {statusHint && (
          <span style={{ fontSize: 12, color: "#64748b" }}>{statusHint}</span>
        )}
        <span style={{ marginLeft: "auto", fontSize: 12, color: "#94a3b8" }}>
          共 {total} 项
        </span>
      </div>

      <PreviewBox path={previewPath} name={previewName} />

      <Table
        rows={rows}
        selected={selected}
        onSelect={onSelect}
      />

      <Pagination
        page={safePage}
        pages={pages}
        pageSize={pageSize}
        total={total}
        onPageChange={onPageChange}
      />
    </div>
  );
}

// ------------------------------------------------------------- preview box

function PreviewBox({ path, name }: { path: string | null; name: string }) {
  const ext = path?.split(".").pop()?.toLowerCase() ?? "";
  const isImg = ["jpg", "jpeg", "png", "gif", "webp", "bmp"].includes(ext);
  const isVideo = ["mp4", "mov", "m4v", "webm"].includes(ext);
  const isHeic = ext === "heic";

  return (
    <div style={previewWrap}>
      {path && isImg && (
        <img
          src={filePreviewUrl(path)}
          alt={name}
          style={previewImg}
          onError={(e) => {
            (e.target as HTMLImageElement).style.opacity = "0.3";
          }}
        />
      )}
      {path && isVideo && (
        <video
          src={filePreviewUrl(path)}
          controls
          style={{ maxWidth: "100%", maxHeight: 180, borderRadius: 6 }}
        />
      )}
      {path && isHeic && (
        <div style={previewPlaceholder}>HEIC · 浏览器无法预览</div>
      )}
      {path && !isImg && !isVideo && !isHeic && (
        <div style={previewPlaceholder}>无法预览 .{ext}</div>
      )}
      {!path && <div style={previewPlaceholder}>选择下方文件查看预览</div>}
      <div style={previewNameStyle}>{name}</div>
    </div>
  );
}

// ------------------------------------------------------------------- table

function Table({
  rows,
  selected,
  onSelect,
}: {
  rows: Row[];
  selected: string | null;
  onSelect: (p: string) => void;
}) {
  if (rows.length === 0) {
    return (
      <div style={emptyTableStyle}>还没有文件</div>
    );
  }
  return (
    <div style={tableWrapStyle}>
      <table style={tableStyle}>
        <colgroup>
          <col style={{ width: "auto" }} />
          <col style={{ width: 126 }} />
          <col style={{ width: 76 }} />
          <col style={{ width: 76 }} />
        </colgroup>
        <thead>
          <tr>
            <th style={thStyle}>文件</th>
            <th style={{ ...thStyle, width: 126 }}>类型</th>
            <th style={{ ...thStyle, width: 70 }}>状态</th>
            <th style={{ ...thStyle, width: 70, textAlign: "right" }}>耗时</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const isSel = r.path === selected;
            return (
              <tr
                key={r.path}
                onClick={() => onSelect(r.path)}
                style={{
                  cursor: "pointer",
                  background: isSel ? "#eff6ff" : "transparent",
                  borderBottom: "1px solid #f1f5f9",
                }}
              >
                <td style={tdStyle}>
                  <div title={r.path} style={pathCellStyle}>
                    <span style={pathTextStyle}>{r.path}</span>
                  </div>
                  {r.error && (
                    <div style={{ color: "#dc2626", fontSize: 11, marginTop: 2 }}>
                      {r.error}
                    </div>
                  )}
                </td>
                <td style={{ ...tdStyle, textAlign: "center" }}>
                  {r.kind && (
                    <span style={kindBadge(r.kind)}>
                      {KIND_LABELS[r.kind]}
                    </span>
                  )}
                </td>
                <td style={{ ...tdStyle, textAlign: "center" }}>
                  {r.status && (
                    <span style={{ color: statusColor(r.status), fontSize: 12 }}>
                      {STATUS_LABELS[r.status]}
                    </span>
                  )}
                </td>
                <td style={{ ...tdStyle, textAlign: "right", color: "#64748b", fontSize: 12 }}>
                  {formatDuration(r.duration_ms)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------- pagination

function Pagination({
  page,
  pages,
  pageSize,
  total,
  onPageChange,
}: {
  page: number;
  pages: number;
  pageSize: number;
  total: number;
  onPageChange: (p: number) => void;
}) {
  if (total === 0) return null;
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);
  return (
    <div style={paginationStyle}>
      <span style={{ fontSize: 12, color: "#64748b" }}>
        {from}–{to} / {total}
      </span>
      <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
        <button
          onClick={() => onPageChange(1)}
          disabled={page <= 1}
          style={pageBtn(page <= 1)}
          aria-label="第一页"
        >
          ⏮
        </button>
        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1}
          style={pageBtn(page <= 1)}
          aria-label="上一页"
        >
          ‹
        </button>
        <span style={{ fontSize: 13, minWidth: 50, textAlign: "center" }}>
          {page} / {pages}
        </span>
        <button
          onClick={() => onPageChange(page + 1)}
          disabled={page >= pages}
          style={pageBtn(page >= pages)}
          aria-label="下一页"
        >
          ›
        </button>
        <button
          onClick={() => onPageChange(pages)}
          disabled={page >= pages}
          style={pageBtn(page >= pages)}
          aria-label="最后一页"
        >
          ⏭
        </button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------- styles

const colWrap: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  flex: 1,
  minWidth: 0,
};

const colHeader: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "8px 12px",
  background: "#f8fafc",
  borderRadius: "8px 8px 0 0",
  borderBottom: "1px solid #e2e8f0",
  fontSize: 14,
};

const previewWrap: React.CSSProperties = {
  height: 200,
  background: "#fafafa",
  border: "1px solid #e2e8f0",
  borderRadius: 8,
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  padding: 8,
  gap: 6,
};

const previewImg: React.CSSProperties = {
  maxWidth: "100%",
  maxHeight: 160,
  objectFit: "contain",
  borderRadius: 4,
};

const previewPlaceholder: React.CSSProperties = {
  color: "#94a3b8",
  fontSize: 13,
};

const previewNameStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#475569",
  maxWidth: "100%",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const tableWrapStyle: React.CSSProperties = {
  maxHeight: "40vh",
  overflowY: "auto",
  border: "1px solid #e2e8f0",
  borderRadius: 8,
};

const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  tableLayout: "fixed",
  fontSize: 13,
};

const thStyle: React.CSSProperties = {
  padding: "6px 10px",
  textAlign: "left",
  background: "#f8fafc",
  position: "sticky",
  top: 0,
  borderBottom: "1px solid #e2e8f0",
  fontWeight: 500,
  fontSize: 12,
  color: "#64748b",
};

const tdStyle: React.CSSProperties = {
  padding: "6px 10px",
  textAlign: "left",
  minWidth: 0,
};

const pathCellStyle: React.CSSProperties = {
  maxWidth: "100%",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  direction: "rtl",
  textAlign: "left",
};

const pathTextStyle: React.CSSProperties = {
  direction: "ltr",
  unicodeBidi: "bidi-override",
};

const emptyTableStyle: React.CSSProperties = {
  padding: 24,
  textAlign: "center",
  color: "#94a3b8",
  fontSize: 13,
  border: "1px solid #e2e8f0",
  borderRadius: 8,
};

const paginationStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "8px 4px",
};

function pageBtn(disabled: boolean): React.CSSProperties {
  return {
    padding: "4px 10px",
    border: "1px solid #cbd5e1",
    borderRadius: 6,
    background: disabled ? "#f1f5f9" : "#fff",
    color: disabled ? "#94a3b8" : "#0f172a",
    cursor: disabled ? "not-allowed" : "pointer",
    fontSize: 13,
    lineHeight: 1.4,
  };
}

function kindBadge(kind: FileKind): React.CSSProperties {
  const bg: Record<FileKind, string> = {
    motion_photo: "#dbeafe",
    still_image: "#f1f5f9",
    video: "#fef3c7",
    unknown: "#fee2e2",
  };
  return {
    background: bg[kind],
    padding: "2px 8px",
    borderRadius: 4,
    fontSize: 11,
    whiteSpace: "nowrap",
  };
}

function statusColor(s: FileStatus): string {
  const map: Record<FileStatus, string> = {
    pending: "#94a3b8",
    hashing: "#f59e0b",
    queued: "#94a3b8",
    converting: "#2563eb",
    copying: "#0891b2",
    done: "#16a34a",
    failed: "#dc2626",
    skipped: "#a3a3a3",
  };
  return map[s];
}

function formatDuration(ms?: number): string {
  if (!ms || ms <= 0) return "";
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s % 60);
  return `${m}m${rem}s`;
}
