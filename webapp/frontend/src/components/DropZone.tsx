import { useCallback, useState } from "react";
import { DirectoryPicker } from "./DirectoryPicker";

interface Props {
  onPaths: (paths: string[]) => void;
  disabled?: boolean;
}

/**
 * Input area — three ways to specify a directory:
 *
 * 1. "选择目录" button → opens DirectoryPicker modal (the reliable way,
 *    works on all browsers including Safari 15.7)
 * 2. Text input → paste/type an absolute path, press Enter or click 扫描
 * 3. Drag a folder onto the drop zone → on most browsers (Chrome/Edge)
 *    we can read the path; on Safari we fall back to using the text input
 *
 * The DirectoryPicker is the primary mechanism because browsers refuse to
 * expose real filesystem paths from <input type=file> for security reasons.
 */
export function DropZone({ onPaths, disabled }: Props) {
  const [path, setPath] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  const submit = useCallback(() => {
    const trimmed = path.trim();
    if (!trimmed || disabled) return;
    onPaths([trimmed]);
  }, [path, disabled, onPaths]);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      if (disabled) return;
      // Browsers that expose file.path (Chromium-based) — we get the real path.
      const files = e.dataTransfer.files;
      if (files && files.length > 0) {
        const first = files[0] as File & { path?: string };
        if (first.path) {
          // If it's a file, use its parent directory
          const p = first.path;
          setPath(p);
          onPaths([p]);
          return;
        }
      }
      // Fallback: if user typed a path, submit it
      if (path) submit();
    },
    [path, disabled, onPaths, submit],
  );

  const handlePick = useCallback(
    (picked: string) => {
      setPath(picked);
      onPaths([picked]);
    },
    [onPaths],
  );

  return (
    <>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        style={{
          border: dragOver ? "2px dashed #2563eb" : "2px dashed #cbd5e1",
          borderRadius: 12,
          padding: 24,
          textAlign: "center",
          background: dragOver ? "#eff6ff" : "#fafafa",
          transition: "all 0.15s",
        }}
      >
        <div style={{ marginBottom: 12, fontSize: 15, color: "#475569" }}>
          拖拽文件夹到这里，或点下方按钮选择目录
        </div>
        <div
          style={{
            display: "flex",
            gap: 8,
            justifyContent: "center",
            flexWrap: "wrap",
          }}
        >
          <button
            onClick={() => setPickerOpen(true)}
            disabled={disabled}
            style={primaryBtn(!!disabled)}
          >
            📁 选择目录
          </button>
          <input
            type="text"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder="/Users/you/Pictures/android_photos"
            disabled={disabled}
            style={{
              flex: 1,
              minWidth: 240,
              maxWidth: 420,
              padding: "8px 12px",
              border: "1px solid #cbd5e1",
              borderRadius: 8,
              fontSize: 14,
            }}
          />
          <button
            onClick={submit}
            disabled={disabled || !path.trim()}
            style={secondaryBtn(disabled || !path.trim())}
          >
            扫描
          </button>
        </div>
        <div style={{ marginTop: 8, fontSize: 11, color: "#94a3b8" }}>
          macOS Safari 不支持拖拽获取路径，请用"选择目录"按钮
        </div>
      </div>

      <DirectoryPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onPick={handlePick}
      />
    </>
  );
}

function primaryBtn(disabled: boolean): React.CSSProperties {
  return {
    padding: "8px 16px",
    borderRadius: 8,
    border: "none",
    background: disabled ? "#94a3b8" : "#2563eb",
    color: "#fff",
    fontSize: 14,
    cursor: disabled ? "not-allowed" : "pointer",
  };
}

function secondaryBtn(disabled: boolean): React.CSSProperties {
  return {
    padding: "8px 16px",
    borderRadius: 8,
    border: "1px solid #cbd5e1",
    background: disabled ? "#f1f5f9" : "#fff",
    color: disabled ? "#94a3b8" : "#0f172a",
    fontSize: 14,
    cursor: disabled ? "not-allowed" : "pointer",
  };
}
