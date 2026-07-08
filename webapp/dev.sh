#!/usr/bin/env bash
# 一键启动后端 + 前端开发服务器
#
# 用法:
#   cd webapp
#   ./dev.sh
#
# 前提:
#   - 已激活 conda 环境 vphoto (含 mvimg2livephoto 依赖)
#   - Node.js / npm 已安装

set -euo pipefail

# 仓库根目录 (webapp/ 的上一级)
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEBAPP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$WEBAPP_DIR"

# 让 Python 能找到仓库根目录的 mvimg2livephoto 包 + webapp 下的 backend 包
export PYTHONPATH="$REPO_ROOT:$WEBAPP_DIR:${PYTHONPATH:-}"

# --- 后端依赖
echo "[1/4] 检查 Python 依赖..."
if ! python -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "  安装 requirements.txt..."
  pip install -r requirements.txt
fi

# --- mvimg2livephoto 可导入
echo "[2/4] 检查 mvimg2livephoto..."
if ! python -c "from mvimg2livephoto.builder import convert_one" 2>/dev/null; then
  echo "  ERROR: mvimg2livephoto 不可导入"
  echo "  PYTHONPATH=$PYTHONPATH"
  echo "  请确认已激活 vphoto conda 环境"
  exit 1
fi

# --- 前端依赖
echo "[3/4] 检查前端依赖..."
if [ ! -d frontend/node_modules ]; then
  echo "  安装 npm 依赖..."
  (cd frontend && npm install)
fi

# --- 启动
echo "[4/4] 启动服务..."
echo ""
echo "  后端:  http://127.0.0.1:8000  (API 文档: /docs)"
echo "  前端:  http://127.0.0.1:5173"
echo ""
echo "  按 Ctrl-C 停止两个服务"
echo ""

# 同时启动后端和前端,任一退出则全部退出
trap 'kill 0' EXIT INT TERM

(uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload) &
(cd frontend && npm run dev) &

wait
