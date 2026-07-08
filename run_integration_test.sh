#!/usr/bin/env bash
# 集成稳定性测试：用 datasets/ 中的 40 个样本测试转换管线
#
# 用法（在 Mac 终端）：
#   conda activate vphoto
#   cd /Users/shikai/projects/MMIMG2LivePhoto
#   bash run_integration_test.sh
#
# 或直接启动 webapp 测试：
#   cd webapp && ./dev.sh
#   # 浏览器打开 http://127.0.0.1:5173
#   # 输入框粘贴：/Users/shikai/projects/MMIMG2LivePhoto/datasets
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
DATASETS="$REPO_ROOT/datasets"
OUTPUT="$REPO_ROOT/test_output_integration"

echo "=== 集成稳定性测试 ==="
echo "输入: $DATASETS"
echo "输出: $OUTPUT"
echo ""

# 清理旧输出
rm -rf "$OUTPUT"
mkdir -p "$OUTPUT"

# 运行 pytest（HEIC 完整性 + parser + extractor + converter + integration）
echo "--- 1. 运行单元测试 ---"
cd "$REPO_ROOT"
python -m pytest mvimg2livephoto/tests/ -v --tb=short 2>&1 | tail -30
echo ""

# 用 Python 直接测试所有 motion photo 转换
echo "--- 2. 转换 datasets/ 中所有 Motion Photo ---"
python3 << 'PYEOF'
import sys, time, os
from pathlib import Path
sys.path.insert(0, '.')
sys.path.insert(0, 'webapp')
from backend.converter_service import classify, process_file, make_context, sha256_of
from backend.models import FileKind

root = Path('datasets')
output_dir = Path('test_output_integration')
output_dir.mkdir(parents=True, exist_ok=True)

files = sorted([p for p in root.rglob('*') if p.is_file()])
print(f'扫描到 {len(files)} 个文件')

# 分类统计
from collections import Counter
kinds = Counter()
motion_photos = []
for p in files:
    kind = classify(p)
    kinds[kind.value] += 1
    if kind == FileKind.MOTION_PHOTO:
        motion_photos.append(p)

print(f'分类: {dict(kinds)}')
print(f'Motion Photo 数量: {len(motion_photos)}')
print()

# 转换每个 motion photo
results = []
for p in motion_photos:
    sz = p.stat().st_size
    print(f'转换: {p.relative_to(root)}  ({sz/1048576:.1f}MB)')
    ctx = make_context(root, p, output_dir)
    sha = sha256_of(p)
    t0 = time.monotonic()
    result = process_file(ctx, sha)
    elapsed = time.monotonic() - t0
    print(f'  状态: {result.status.value}  耗时: {elapsed:.2f}s')
    if result.error:
        print(f'  错误: {result.error}')
    for op in result.output_paths:
        if Path(op).exists():
            osz = Path(op).stat().st_size
            print(f'  输出: {Path(op).name}  ({osz/1048576:.1f}MB)')
    results.append((p.name, result.status.value, elapsed, result.error))
    print()

# 汇总
ok = sum(1 for _, s, _, _ in results if s == 'done')
fail = sum(1 for _, s, _, _ in results if s == 'failed')
print('=== 转换汇总 ===')
print(f'成功: {ok}  失败: {fail}  总计: {len(results)}')
if fail > 0:
    print()
    print('失败列表:')
    for name, status, elapsed, err in results:
        if status == 'failed':
            print(f'  {name}: {err}')

# 测试静态图片复制和视频软链
print()
print('--- 测试非 Motion Photo 文件 ---')
non_motion = [p for p in files if classify(p) != FileKind.MOTION_PHOTO]
for p in non_motion[:5]:  # 只测前 5 个
    kind = classify(p)
    ctx = make_context(root, p, output_dir)
    sha = sha256_of(p)
    result = process_file(ctx, sha)
    print(f'  [{kind.value:13}] {result.status.value:7}  {p.name}')
    if result.error:
        print(f'    ERROR: {result.error}')
PYEOF

echo ""
echo "--- 3. 验证输出文件 ---"
find "$OUTPUT" -type f -o -type l | sort | while read f; do
    sz=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo 0)
    echo "  $f  ($sz bytes)"
done

echo ""
echo "=== 测试完成 ==="
echo "输出目录: $OUTPUT"
