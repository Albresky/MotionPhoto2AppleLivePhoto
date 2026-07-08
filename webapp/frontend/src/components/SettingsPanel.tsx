import { useMemo, useState } from "react";
import { DirectoryPicker } from "./DirectoryPicker";
import { ENC_PRESETS } from "../types";

interface Props {
  outputDir: string;
  setOutputDir: (v: string) => void;
  workers: number;
  setWorkers: (v: number) => void;
  hdr: boolean;
  setHdr: (v: boolean) => void;
  encPreset: string;
  setEncPreset: (v: string) => void;
  symlinkFallback: boolean;
  setSymlinkFallback: (v: boolean) => void;
  disabled: boolean;
}

export function SettingsPanel({
  outputDir,
  setOutputDir,
  workers,
  setWorkers,
  hdr,
  setHdr,
  encPreset,
  setEncPreset,
  symlinkFallback,
  setSymlinkFallback,
  disabled,
}: Props) {
  const cpu = useMemo(() => {
    const n = navigator.hardwareConcurrency || 8;
    return Math.max(1, Math.floor(n * 0.8));
  }, []);

  const recommended = useMemo(() => Math.max(1, Math.min(8, cpu)), [cpu]);

  const [pickerOpen, setPickerOpen] = useState(false);

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 12,
        padding: 16,
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        background: "#fff",
      }}
    >
      <div>
        <label style={labelStyle}>输出目录</label>
        <div style={{ display: "flex", gap: 4 }}>
          <input
            type="text"
            value={outputDir}
            onChange={(e) => setOutputDir(e.target.value)}
            placeholder="/Users/you/Pictures/output"
            disabled={disabled}
            style={inputStyle(disabled)}
          />
          <button
            type="button"
            onClick={() => setPickerOpen(true)}
            disabled={disabled}
            style={pickerBtn(disabled)}
            aria-label="选择输出目录"
          >
            📁
          </button>
        </div>
        <div style={hintStyle}>会镜像保留输入目录结构</div>
      </div>

      <div>
        <label style={labelStyle}>并发数</label>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="range"
            min={1}
            max={Math.max(4, cpu)}
            step={1}
            value={workers}
            onChange={(e) => setWorkers(Number(e.target.value))}
            disabled={disabled}
            style={{ flex: 1 }}
          />
          <span style={{ fontSize: 14, minWidth: 24, textAlign: "right" }}>
            {workers}
          </span>
        </div>
        <div style={hintStyle}>推荐 {recommended}（CPU 80%）</div>
      </div>

      <div
        style={{
          gridColumn: "1 / -1",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <input
          type="checkbox"
          id="hdr"
          checked={hdr}
          onChange={(e) => setHdr(e.target.checked)}
          disabled={disabled}
        />
        <label htmlFor="hdr" style={{ fontSize: 13, color: "#0f172a" }}>
          保留 HDR 增益图（若源文件含 GainMap，输出 HEIC 含 Apple HDR aux 图）
        </label>
      </div>

      <div style={{ gridColumn: "1 / -1" }}>
        <label style={labelStyle}>编码速度 (x265 preset)</label>
        <select
          value={encPreset}
          onChange={(e) => setEncPreset(e.target.value)}
          disabled={disabled}
          style={{
            ...inputStyle(disabled),
            cursor: disabled ? "not-allowed" : "pointer",
          }}
        >
          {ENC_PRESETS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
        <div style={hintStyle}>
          ultrafast 约 5 倍速于 medium，画质几乎无差别（PSNR ~62dB）
        </div>
      </div>

      <div
        style={{
          gridColumn: "1 / -1",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <input
          type="checkbox"
          id="symlink"
          checked={symlinkFallback}
          onChange={(e) => setSymlinkFallback(e.target.checked)}
          disabled={disabled}
        />
        <label htmlFor="symlink" style={{ fontSize: 13, color: "#0f172a" }}>
          未转换文件用软链接输出（静态图片、视频、转换失败的 Motion Photo
          直接软链到原文件，不复制）
        </label>
      </div>

      <DirectoryPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onPick={(p) => setOutputDir(p)}
        title="选择输出目录"
      />
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 13,
  color: "#475569",
  marginBottom: 4,
  fontWeight: 500,
};

const hintStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#94a3b8",
  marginTop: 2,
};

function inputStyle(disabled: boolean): React.CSSProperties {
  return {
    flex: 1,
    padding: "8px 12px",
    border: "1px solid #cbd5e1",
    borderRadius: 8,
    fontSize: 14,
    background: disabled ? "#f1f5f9" : "#fff",
    boxSizing: "border-box",
    minWidth: 0,
  };
}

function pickerBtn(disabled: boolean): React.CSSProperties {
  return {
    padding: "8px 12px",
    border: "1px solid #cbd5e1",
    borderRadius: 8,
    background: disabled ? "#f1f5f9" : "#fff",
    cursor: disabled ? "not-allowed" : "pointer",
    fontSize: 14,
  };
}
