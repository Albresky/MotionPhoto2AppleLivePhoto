// Types mirroring backend/models.py.  Keep in sync when API changes.

export type FileKind =
  | "motion_photo"
  | "still_image"
  | "video"
  | "unknown";

export type FileStatus =
  | "pending"
  | "hashing"
  | "queued"
  | "converting"
  | "copying"
  | "done"
  | "failed"
  | "skipped";

export interface ScanItem {
  path: string;
  kind: FileKind;
  size: number;
  mtime: number;
}

export interface ScanResponse {
  items: ScanItem[];
  total: number;
}

export interface ConvertRequest {
  items: ScanItem[];
  output_dir: string;
  workers: number;
  hdr: boolean;
  enc_preset: string;
  symlink_fallback: boolean;
}

export const ENC_PRESETS = [
  { value: "ultrafast", label: "极速 (ultrafast, ~5x 快)" },
  { value: "fast", label: "快速 (fast)" },
  { value: "medium", label: "标准 (medium, 默认画质)" },
  { value: "slow", label: "慢速 (slow, 最高压缩比)" },
] as const;

export interface ProgressEvent {
  type: "progress" | "done" | "idle";
  path?: string;
  name?: string;
  status?: FileStatus;
  kind?: FileKind;
  completed?: number;
  total?: number;
  error?: string;
  duration_ms?: number;
  heic_path?: string | null;
  mov_path?: string | null;
  output_paths?: string[];
}

export interface JobResult {
  path: string;
  kind: FileKind;
  status: FileStatus;
  heic_path: string | null;
  mov_path: string | null;
  output_paths: string[];
  error: string | null;
  duration_ms: number;
}

export interface SummaryResponse {
  total: number;
  done: number;
  failed: number;
  skipped: number;
  results: JobResult[];
}

// Status → human-readable label in Chinese
export const STATUS_LABELS: Record<FileStatus, string> = {
  pending: "待处理",
  hashing: "计算哈希",
  queued: "排队中",
  converting: "转换中",
  copying: "复制中",
  done: "完成",
  failed: "失败",
  skipped: "已跳过",
};

export const KIND_LABELS: Record<FileKind, string> = {
  motion_photo: "Motion Photo",
  still_image: "静态图片",
  video: "视频",
  unknown: "其他",
};
