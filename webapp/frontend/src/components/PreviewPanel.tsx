import { filePreviewUrl } from "../api";
import type { ScanItem } from "../types";

interface Props {
  item: ScanItem | null;
}

/**
 * Preview a scanned file.
 *
 * - Images (jpg/png/heic/gif): show via <img>.  HEIC may not render in
 *   most browsers — that's expected; the user just sees a placeholder.
 * - Videos (mp4/mov): show via <video> with controls.
 * - Unknown: show a message.
 */
export function PreviewPanel({ item }: Props) {
  if (!item) {
    return (
      <div style={emptyStyle}>
        点左侧文件列表中的某一行查看预览
      </div>
    );
  }

  const ext = item.path.split(".").pop()?.toLowerCase() ?? "";
  const url = filePreviewUrl(item.path);

  return (
    <div style={wrapStyle}>
      <div style={{ marginBottom: 8, fontSize: 13, color: "#475569", wordBreak: "break-all" }}>
        {item.path}
      </div>
      <div style={previewBox}>
        {["jpg", "jpeg", "png", "gif", "webp", "bmp"].includes(ext) && (
          <img
            src={url}
            alt={item.path}
            style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }}
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = "none";
              const p = (e.target as HTMLImageElement).parentElement;
              if (p) p.textContent = "图片预览不可用";
            }}
          />
        )}
        {["heic"].includes(ext) && (
          <div style={{ color: "#94a3b8", fontSize: 13 }}>
            HEIC 格式 — 浏览器无法直接预览，请用系统相册查看
          </div>
        )}
        {["mp4", "mov", "m4v", "webm"].includes(ext) && (
          <video
            src={url}
            controls
            style={{ maxWidth: "100%", maxHeight: "100%" }}
          />
        )}
        {!["jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "mp4", "mov", "m4v", "webm"]
          .includes(ext) && (
          <div style={{ color: "#94a3b8", fontSize: 13 }}>
            无法预览此格式 (.{ext})
          </div>
        )}
      </div>
    </div>
  );
}

const wrapStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
};

const emptyStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  height: "100%",
  color: "#94a3b8",
  fontSize: 14,
  background: "#fafafa",
  borderRadius: 8,
  border: "1px solid #e2e8f0",
};

const previewBox: React.CSSProperties = {
  flex: 1,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "#fafafa",
  borderRadius: 8,
  border: "1px solid #e2e8f0",
  minHeight: 240,
  overflow: "hidden",
};
