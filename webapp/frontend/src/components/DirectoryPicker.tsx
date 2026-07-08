import { useCallback, useEffect, useState } from "react";
import { browseDirectory, type BrowseEntry } from "../api";

interface Props {
  open: boolean;
  onClose: () => void;
  onPick: (path: string) => void;
  title?: string;
}

/**
 * Modal directory browser.
 *
 * Why this exists: browsers cannot read the real filesystem path of a
 * picked folder (security restriction).  So instead of <input webkitdirectory>
 * we walk the filesystem via the backend's /api/browse endpoint and let the
 * user click through directories.
 *
 * Layout:
 *   ┌─────────────────────────────────────┐
 *   │  /Users/foo/Pictures  ↑ 父目录      │  ← breadcrumb + parent
 *   │  ─────────────────────────────────  │
 *   │  📁 Camera                          │
 *   │  📁 Screenshots                     │  ← click to enter
 *   │  📁 Wallpapers                      │
 *   │  ...                                │
 *   │  ─────────────────────────────────  │
 *   │  [取消]            [选择此目录]    │
 *   └─────────────────────────────────────┘
 */
export function DirectoryPicker({
  open,
  onClose,
  onPick,
  title = "选择目录",
}: Props) {
  const [cwd, setCwd] = useState<string>("");
  const [parent, setParent] = useState<string | null>(null);
  const [dirs, setDirs] = useState<BrowseEntry[]>([]);
  const [isRoot, setIsRoot] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (path: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const res = await browseDirectory(path);
      setCwd(res.path);
      setParent(res.parent);
      setDirs(res.dirs);
      setIsRoot(res.is_root);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) load(null);
  }, [open, load]);

  const enter = useCallback(
    (path: string) => {
      load(path);
    },
    [load],
  );

  const goParent = useCallback(() => {
    if (parent) load(parent);
  }, [parent, load]);

  if (!open) return null;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "#fff",
          borderRadius: 12,
          width: "min(640px, 92vw)",
          maxHeight: "80vh",
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 20px 60px rgba(0,0,0,0.3)",
        }}
      >
        {/* header */}
        <div
          style={{
            padding: "16px 20px",
            borderBottom: "1px solid #e2e8f0",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div style={{ fontSize: 16, fontWeight: 600 }}>{title}</div>
          <button
            onClick={onClose}
            style={{
              border: "none",
              background: "transparent",
              fontSize: 20,
              cursor: "pointer",
              color: "#94a3b8",
              lineHeight: 1,
            }}
            aria-label="关闭"
          >
            ×
          </button>
        </div>

        {/* breadcrumb */}
        <div
          style={{
            padding: "8px 20px",
            background: "#f8fafc",
            borderBottom: "1px solid #e2e8f0",
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 13,
          }}
        >
          {parent && !isRoot && (
            <button
              onClick={goParent}
              style={crumbBtn}
              title="返回上级目录"
            >
              ↑
            </button>
          )}
          <span style={{ color: "#0f172a", fontFamily: "monospace" }}>
            {cwd || "选择起始位置"}
          </span>
        </div>

        {/* list */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "8px 0",
            minHeight: 240,
          }}
        >
          {loading && (
            <div style={centerStyle}>加载中…</div>
          )}
          {error && (
            <div style={{ ...centerStyle, color: "#dc2626" }}>{error}</div>
          )}
          {!loading && !error && dirs.length === 0 && (
            <div style={centerStyle}>没有子目录</div>
          )}
          {!loading &&
            dirs.map((d) => (
              <button
                key={d.path}
                onDoubleClick={() => enter(d.path)}
                onClick={() => {
                  // Single click previews — but for directories we just enter.
                  enter(d.path);
                }}
                style={rowStyle}
                title={d.path}
              >
                <span style={{ marginRight: 8 }}>📁</span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                  {d.name}
                </span>
              </button>
            ))}
        </div>

        {/* footer */}
        <div
          style={{
            padding: "12px 20px",
            borderTop: "1px solid #e2e8f0",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 8,
          }}
        >
          <div style={{ fontSize: 12, color: "#94a3b8" }}>
            双击进入目录 · 点"选择此目录"确认
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={onClose} style={secondaryBtn}>
              取消
            </button>
            <button
              onClick={() => {
                if (cwd) {
                  onPick(cwd);
                  onClose();
                }
              }}
              disabled={!cwd || isRoot}
              style={primaryBtn(!cwd || isRoot)}
            >
              选择此目录
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const rowStyle: React.CSSProperties = {
  display: "flex",
  width: "100%",
  padding: "8px 20px",
  border: "none",
  background: "transparent",
  textAlign: "left",
  cursor: "pointer",
  fontSize: 14,
  color: "#0f172a",
  alignItems: "center",
};

const crumbBtn: React.CSSProperties = {
  border: "1px solid #cbd5e1",
  background: "#fff",
  borderRadius: 6,
  padding: "2px 8px",
  cursor: "pointer",
  fontSize: 13,
};

const centerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 32,
  color: "#94a3b8",
  fontSize: 14,
};

const primaryBtn = (disabled: boolean): React.CSSProperties => ({
  padding: "8px 16px",
  borderRadius: 8,
  border: "none",
  background: disabled ? "#94a3b8" : "#2563eb",
  color: "#fff",
  fontSize: 14,
  cursor: disabled ? "not-allowed" : "pointer",
});

const secondaryBtn: React.CSSProperties = {
  padding: "8px 16px",
  borderRadius: 8,
  border: "1px solid #cbd5e1",
  background: "#fff",
  color: "#0f172a",
  fontSize: 14,
  cursor: "pointer",
};
