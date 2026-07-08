#!/usr/bin/env bash
# 在 macOS 终端运行，从 ~/xiaomi/DCIM 采样代表性文件到 datasets/
#
#   cd /Users/shikai/projects/MMIMG2LivePhoto
#   bash sample_datasets.sh
#
# 输出：datasets/<原相对路径>/<文件名>
set -euo pipefail

XIAOMI_DIR="${XIAOMI_DIR:-$HOME/xiaomi/DCIM}"
DATASETS_DIR="${DATASETS_DIR:-$(pwd)/datasets}"

mkdir -p "$DATASETS_DIR"
rm -rf "$DATASETS_DIR"/*

# 采样函数：从 stdin 读文件路径，按大小百分位取 5 个
sample_5() {
  local total
  total=$(wc -l | awk '{print $1}')
  if [ "$total" -le 5 ]; then
    cat
    return
  fi
  local arr=()
  while IFS= read -r line; do
    arr+=("$line")
  done
  # 用 stat 获取大小，排序
  local n=${#arr[@]}
  local sizes=()
  for f in "${arr[@]}"; do
    sz=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo 0)
    sizes+=("$sz|$f")
  done
  IFS=$'\n' sorted=($(printf '%s\n' "${sizes[@]}" | sort -n -t'|' -k1)); unset IFS
  # 取 0/20/40/60/80% 位置
  for pct in 0 20 40 60 80; do
    idx=$((n * pct / 100))
    [ "$idx" -ge "$n" ] && idx=$((n - 1))
    printf '%s\n' "${sorted[$idx]#*|}"
  done
}

# 统计函数：扫描目录，按文件名前缀分组，每组采样 5 个
scan_and_sample() {
  local dir="$1"
  local label="$2"
  local pattern="$3"
  local count

  echo "扫描 $label: $pattern"

  files=$(find "$dir" -maxdepth 1 -name "$pattern" -type f 2>/dev/null | sort)
  count=$(echo "$files" | grep -c . || true)
  if [ "$count" -eq 0 ]; then
    echo "  无文件"
    return
  fi
  echo "  共 $count 个"

  # 采样
  sampled=$(echo "$files" | sample_5)
  echo "  采样 5 个:"
  echo "$sampled" | while IFS= read -r f; do
    sz=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo 0)
    echo "    $(basename "$f")  ${sz} bytes"
    # 拷贝到 datasets 保持路径结构
    rel="${f#$XIAOMI_DIR/}"
    dest="$DATASETS_DIR/$rel"
    mkdir -p "$(dirname "$dest")"
    cp "$f" "$dest"
  done
}

# 主要类别
scan_and_sample "$XIAOMI_DIR/Camera" "小米 Motion Photo (MVIMG)" "MVIMG_*.jpg"
scan_and_sample "$XIAOMI_DIR/Camera" "小米普通 JPG (IMG)" "IMG_*.jpg"
scan_and_sample "$XIAOMI_DIR/Camera" "小米普通 HEIC" "IMG_*.HEIC"
scan_and_sample "$XIAOMI_DIR/Camera" "小米视频 (VID mp4)" "VID_*.mp4"
scan_and_sample "$XIAOMI_DIR/Camera" "Pixel 照片 (PXL)" "PXL_*.jpg"

# 视频类（MOV）
scan_and_sample "$XIAOMI_DIR/Camera" "MOV 视频" "*.MOV"

# 截图
scan_and_sample "$XIAOMI_DIR/Screenshots" "截图" "*.png"
scan_and_sample "$XIAOMI_DIR/Screenshots" "截图 JPG" "*.jpg"

# 其他子目录
scan_and_sample "$XIAOMI_DIR/MiShare" "MiShare" "*.jpg"
scan_and_sample "$XIAOMI_DIR/LightroomCamera" "LightroomCamera" "*.jpg"
scan_and_sample "$XIAOMI_DIR/Snapseed" "Snapseed" "*.jpg"

echo ""
echo "=== 采样完成 ==="
echo "datasets 目录："
find "$DATASETS_DIR" -type f | sort
