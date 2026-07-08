#!/usr/bin/env python3
"""把转换好的输出目录扁平化成一层软链接，便于导入 Apple Photos。

转换器默认按 mirror 结构输出：

    output_dir/
      Camera/
        MVIMG_x.HEIC
        MVIMG_x.MOV
        IMG_y.jpg            (still_image, copy 或 symlink)
      MiShare/
        ...
      Screenshots/
        ...

Apple Photos 导入时不需要这种分层 —— 它按拍摄时间组织，不依赖源目录。
本脚本在 ``output_dir_flatten/`` 里为每个文件建一个软链接，全部放在
同一层，链接名就是文件名（不带子目录）。这样导入照片 app 时只需选一个
文件夹。

**按时间分批**：文件太多时不便批量导入，默认按 500 个一组分到子目录，
按拍摄时间排序：

    output_flatten/
      batch_0001-0500/
        photo1.HEIC, photo1.MOV, photo2.jpg, ...
      batch_0501-1000/
        ...
      batch_1001-1500/
        ...

Live Photo 配对（HEIC + MOV 同 basename）天然支持 —— 两个文件软链接名
不同扩展名，但 basename 相同，Photos.app 会自动配对。配对的两文件会
被分到同一批次。

冲突处理：
  - Live Photo 的 .HEIC + .MOV 同 basename 是预期的，不冲突
  - 其他同名冲突（不同子目录里同名的 .jpg）会加后缀 _2、_3…

只建软链接，不复制文件内容，几乎不占磁盘空间。

用法（在 Mac 终端）：
    conda activate vphoto
    cd /Users/shikai/projects/MMIMG2LivePhoto
    python flatten_output.py <output_dir> [options]

    # 默认：按拍摄时间升序，每 500 一组
    python flatten_output.py /Volumes/Data/xiaomi/output

    # 指定每组 200，按时间降序（最新的先）
    python flatten_output.py output --batch-size 200 --order desc

    # 不分批，全部放一个目录（旧行为）
    python flatten_output.py output --no-batch

    # 先看会做什么，不实际执行
    python flatten_output.py output --dry-run

    # 指定目标位置
    python flatten_output.py output --dest ~/Desktop/flatten
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# 文件发现
# ---------------------------------------------------------------------------

def find_outputs(root: Path) -> list[Path]:
    """递归找出 root 下所有输出文件（普通文件 + 软链接）。

    跳过隐藏文件（.DS_Store 等）、Apple 双系统文件（._开头）、隐藏目录
    （.globalTrash 等）。
    """
    files: list[Path] = []
    for p in root.rglob("*"):
        if not (p.is_file() or p.is_symlink()):
            continue
        name = p.name
        if name.startswith(".") or name.startswith("._"):
            continue
        if any(part.startswith(".") for part in p.relative_to(root).parts[:-1]):
            continue
        files.append(p)
    return files


# ---------------------------------------------------------------------------
# 拍摄时间提取
# ---------------------------------------------------------------------------

# 文件名里嵌入时间戳的常见格式，比如：
#   IMG_20260324_220411.jpg        → 2026-03-24 22:04:11
#   MVIMG_20250501_175749.jpg      → 2025-05-01 17:57:49
#   VID_20240526_002048.mp4        → 2024-05-26 00:20:48
#   Screenshot_2026-02-10-23-15-33-916.png → 2026-02-10 23:15:33
_FILENAME_TS = re.compile(
    r"(\d{4})[_-]?(\d{2})[_-]?(\d{2})[_-]?(\d{2})[_-]?(\d{2})[_-]?(\d{2})"
)


def _datetime_from_filename(name: str) -> datetime | None:
    """尝试从文件名里解析时间戳。"""
    m = _FILENAME_TS.search(name)
    if not m:
        return None
    try:
        return datetime(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4)), int(m.group(5)), int(m.group(6)),
        )
    except ValueError:
        return None


def _datetime_from_exif(path: Path) -> datetime | None:
    """从 EXIF DateTimeOriginal 读取拍摄时间。

    用 Pillow + pillow_heif，支持 JPEG / HEIC / HEIF。其他类型返回 None。
    Pillow 在解析失败时不抛异常，返回 None；我们也吞掉所有异常，
    失败就 fallback 到文件名/mtime。
    """
    ext = path.suffix.lower()
    if ext not in {".jpg", ".jpeg", ".heic", ".heif", ".png"}:
        return None
    try:
        # 延迟导入：脚本可能在没有 pillow_heif 的环境跑（虽然项目要求装）
        from PIL import Image
        from pillow_heif import register_heif_opener
        register_heif_opener()

        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            # 0x9003 = DateTimeOriginal
            dt = exif.get(0x9003) or exif.get(0x0132)  # DateTime 作为 fallback
            if not dt:
                return None
            # EXIF 格式: "2026:03:24 22:04:11"
            return datetime.strptime(dt, "%Y:%m:%d %H:%M:%S")
    except Exception:
        return None


def capture_time(path: Path) -> datetime:
    """获取拍摄时间，按优先级 fallback：

    1. EXIF DateTimeOriginal
    2. 文件名里嵌入的时间戳
    3. 文件 mtime（最后兜底）

    返回 datetime 对象，永远不会 None。
    """
    # 软链接要 follow 到目标再读 EXIF
    real = path.resolve() if path.is_symlink() else path
    dt = _datetime_from_exif(real)
    if dt is not None:
        return dt
    dt = _datetime_from_filename(path.name)
    if dt is not None:
        return dt
    # 兜底：mtime
    try:
        return datetime.fromtimestamp(real.stat().st_mtime)
    except OSError:
        return datetime(1970, 1, 1)


# ---------------------------------------------------------------------------
# Live Photo 配对
# ---------------------------------------------------------------------------

def pair_live_photos(files: list[Path]) -> list[tuple[Path, ...]]:
    """把 Live Photo 的 HEIC+MOV 配成一组，其他文件单独一组。

    返回 list of tuples，每个 tuple 是同 basename 的文件组（1 或 2 个）。
    组内顺序：HEIC 在前，MOV 在后（如果都有的话）。
    """
    by_stem: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        # stem 去掉扩展名，但注意 .HEIC 和 .MOV 的 stem 相同
        # 比如 IMG_001.HEIC 和 IMG_001.MOV 的 stem 都是 IMG_001
        by_stem[f.stem].append(f)

    groups: list[tuple[Path, ...]] = []
    for stem, group in by_stem.items():
        # 排序：HEIC 在前，MOV 在后，其他按扩展名
        group.sort(key=lambda p: (
            0 if p.suffix.lower() == ".heic" else
            1 if p.suffix.lower() == ".mov" else 2,
            p.suffix,
        ))
        groups.append(tuple(group))
    return groups


# ---------------------------------------------------------------------------
# 软链接
# ---------------------------------------------------------------------------

def link_name_for(path: Path, used: dict[str, int]) -> str:
    """给文件挑一个不冲突的软链接名。同名冲突加 _2、_3 后缀。"""
    name = path.name
    if name not in used:
        used[name] = 1
        return name
    used[name] += 1
    return f"{path.stem}_{used[name]}{path.suffix}"


def make_link(target: Path, link_path: Path, *, force: bool) -> str:
    """创建软链接。返回 'created' / 'skipped' / 'renamed'。

    - 如果 link_path 不存在或 force=True，创建软链接
    - 如果 link_path 已指向同一目标，跳过
    - 如果 link_path 已存在但指向不同目标，重新选名（调用方处理）
    """
    if link_path.is_symlink() or link_path.exists():
        if force:
            link_path.unlink()
        else:
            try:
                existing = os.readlink(link_path)
                if existing == str(target) or existing == str(target.resolve()):
                    return "skipped"
            except OSError:
                pass
            return "conflict"
    # 解析到最终目标，避免双层间接
    real_target = target.resolve() if target.is_symlink() else target
    os.symlink(real_target, link_path)
    return "created"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="把转换输出目录扁平化成一层软链接，按拍摄时间分批",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "output_dir", type=Path,
        help="转换器的输出目录（含 Camera/、MiShare/ 等子目录）",
    )
    parser.add_argument("--dest", type=Path, default=None,
                        help="目标目录（默认 <output_dir>_flatten）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将要做什么，不实际建软链接")
    parser.add_argument("--force", action="store_true",
                        help="目标已存在时覆盖（默认跳过）")
    parser.add_argument("--batch-size", type=int, default=500,
                        help="每批子目录的文件数（默认 500）")
    parser.add_argument("--order", choices=["asc", "desc"], default="asc",
                        help="时间排序：asc 升序（默认），desc 降序")
    parser.add_argument("--no-batch", action="store_true",
                        help="不分批，全部放一个目录（旧行为）")
    args = parser.parse_args(argv)

    if args.batch_size < 1:
        parser.error("--batch-size 必须 >= 1")

    src = args.output_dir.resolve()
    if not src.is_dir():
        print(f"错误：输出目录不存在：{src}", file=sys.stderr)
        return 1

    dest = args.dest or src.parent / f"{src.name}_flatten"

    files = find_outputs(src)
    if not files:
        print(f"源目录 {src} 下没有文件")
        return 0

    # --- 按 Live Photo 配对 ---
    groups = pair_live_photos(files)

    # --- 计算每组时间（取组内最早/最晚，作为该组排序键）---
    # Live Photo 对：用 HEIC 的时间（如果有），否则用组内最早
    def group_time(group: tuple[Path, ...]) -> datetime:
        times = [capture_time(f) for f in group]
        return min(times)  # 组内最早

    print(f"源目录 : {src}")
    print(f"目标   : {dest}")
    print(f"文件数 : {len(files)}（{len(groups)} 组，含 Live Photo 对）")
    print(f"排序   : {'升序 (旧→新)' if args.order == 'asc' else '降序 (新→旧)'}")

    if not args.no_batch:
        print(f"分批   : 每批 {args.batch_size} 个文件")
    print()

    # --- 排序 ---
    groups.sort(key=group_time, reverse=(args.order == "desc"))

    # --- 分批 ---
    if args.no_batch:
        batches = [(None, groups)]
    else:
        batches = []
        for i in range(0, len(groups), args.batch_size):
            batch = groups[i:i + args.batch_size]
            n_start = i + 1
            n_end = i + len(batch)
            batch_name = f"batch_{n_start:04d}-{n_end:04d}"
            batches.append((batch_name, batch))

    # --- 创建软链接 ---
    by_ext: dict[str, int] = defaultdict(int)
    used_names: dict[str, int] = {}  # 全局唯一，避免跨批次同名冲突
    created = skipped = conflict = 0

    if not args.dry_run:
        dest.mkdir(parents=True, exist_ok=True)

    for batch_name, batch_groups in batches:
        batch_dir = dest / batch_name if batch_name else dest
        if not args.dry_run:
            batch_dir.mkdir(parents=True, exist_ok=True)
        elif batch_name:
            print(f"--- {batch_name}/ ---")

        for group in batch_groups:
            for f in group:
                by_ext[f.suffix.lower()] += 1
                link_name = link_name_for(f, used_names)
                link_path = batch_dir / link_name

                if args.dry_run:
                    target = f.resolve() if f.is_symlink() else f
                    rel = link_path.relative_to(dest) if batch_name else link_path.name
                    print(f"  {rel} → {target}")
                    created += 1
                    continue

                # 处理冲突重命名
                while link_path.is_symlink() or link_path.exists():
                    result = make_link(f, link_path, force=args.force)
                    if result == "created":
                        created += 1
                        break
                    elif result == "skipped":
                        skipped += 1
                        break
                    else:  # conflict
                        conflict += 1
                        link_name = link_name_for(f, used_names)
                        link_path = batch_dir / link_name
                else:
                    result = make_link(f, link_path, force=args.force)
                    if result == "created":
                        created += 1
                    elif result == "skipped":
                        skipped += 1
                    else:
                        conflict += 1

    print()
    print("按扩展名统计：")
    for ext, n in sorted(by_ext.items(), key=lambda x: -x[1]):
        print(f"  {ext or '(无)':8s} {n}")
    print()

    batch_summary = ""
    if not args.no_batch and batches:
        batch_summary = f"，{len(batches)} 个批次"

    print(
        f"{'(dry-run) ' if args.dry_run else ''}"
        f"已建软链接 {created}，跳过 {skipped}，冲突重命名 {conflict}"
        f"{batch_summary}"
    )
    if not args.dry_run:
        print(f"\n扁平化目录: {dest}")
        if not args.no_batch:
            print("每个 batch_*/ 子目录可以单独拖进 Mac 照片 app 批量导入。")
        else:
            print("可以拖进 Mac 照片 app 导入了。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
