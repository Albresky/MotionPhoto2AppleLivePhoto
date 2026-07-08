# MVIMG → Live Photo Web 转换器

安卓 Motion Photo (MVIMG) 批量转 iPhone Live Photo (HEIC + MOV) 的 Web 界面。

## 特性

- **拖拽 / 输入路径** 选择输入目录
- **自动识别** 文件类型:Motion Photo 转换、静态图片复制、视频软链
- **断点续传**:已成功转换的文件(路径 + 内容 hash 一致)再次扫描时自动跳过
- **并发队列**:默认 CPU 80% 核心数,可调
- **进度可视化**:WebSocket 实时推送每个文件的状态
- **失败重试**:汇总失败文件及原因,可一键重试
- **元数据保留**:不修改拍摄时间、GPS、设备信息等任何 EXIF
- **镜像输出结构**:保留输入目录的子目录层级

## 目录结构

```
webapp/
├── backend/
│   ├── main.py              FastAPI 路由 + WebSocket
│   ├── converter_service.py 包装 mvimg2livephoto,处理分类/复制/软链
│   ├── queue_manager.py     并发队列 + 进度事件
│   ├── progress_store.py    SQLite 断点续传存储
│   └── models.py            Pydantic schemas
├── frontend/
│   ├── src/
│   │   ├── App.tsx          主界面
│   │   ├── api.ts           HTTP/WS 客户端
│   │   ├── types.ts         TS 类型(镜像后端 models)
│   │   └── components/
│   │       ├── DropZone.tsx       拖拽/路径输入
│   │       ├── FileList.tsx       文件列表 + 状态
│   │       ├── PreviewPanel.tsx   图片/视频预览
│   │       ├── ProgressBar.tsx    进度条
│   │       └── SettingsPanel.tsx   输出目录/并发/HDR 设置
│   ├── package.json
│   └── vite.config.ts
├── requirements.txt
└── dev.sh
```

每个文件控制在 500 行以内,模块职责单一。

## 启动

### 前提

1. 已激活 conda 环境 `vphoto`(含 `mvimg2livephoto` 依赖:ffmpeg、exiftool、Pillow、pillow-heif 等)
2. Node.js / npm 已安装

### 一键启动

```bash
cd webapp
./dev.sh
```

启动后:
- 前端 http://127.0.0.1:5173
- 后端 http://127.0.0.1:8000 (API 文档: /docs)

### 手动启动

```bash
# 终端 1: 后端
conda activate vphoto
cd /path/to/MMIMG2LivePhoto
pip install -r webapp/requirements.txt
cd webapp
uvicorn backend.main:app --reload --port 8000

# 终端 2: 前端
cd webapp/frontend
npm install
npm run dev
```

## 使用流程

1. 在前端输入框粘贴输入目录路径(或拖拽),点"扫描"
2. 文件列表会显示每个文件的类型(Motion Photo / 静态图片 / 视频 / 其他)
3. 设置输出目录(默认在输入目录同级 `output/`)
4. 调整并发数(默认 CPU 80%)
5. 点"开始转换"
6. 进度条实时显示完成数,文件列表状态实时更新
7. 完成后看"本次结果"汇总;有失败项可点"重试失败项"

## 断点续传

SQLite 数据库 `webapp/data/progress.db` 记录每个已处理文件:

- `input_path` + `sha256`:相同路径且内容一致 → 跳过
- 文件被替换(hash 变了)→ 重新转换
- 失败项保留错误原因,可重试

删除 `progress.db` 可清空所有历史记录。

## 文件处理规则

| 类型 | 判定 | 处理 |
|------|------|------|
| Motion Photo | `.jpg/.jpeg` 且 XMP 含 `GCamera:MotionPhoto` | 调 `convert_one` → HEIC + MOV |
| 静态图片 | `.jpg/.jpeg/.heic/.png/.gif/.webp/.bmp` 但无 XMP 标记 | `shutil.copy2`(保留 mtime/EXIF) |
| 视频 | `.mp4/.mov/.m4v/.avi/.mkv` | `os.symlink`(软链到输出目录) |
| 其他 | 其他扩展名 | `shutil.copy2` |

## 元数据保留

- Motion Photo:由 `mvimg2livephoto.metadata` 模块读取源 EXIF(拍摄时间、GPS、设备信息),注入输出 HEIC
- 静态图片:`shutil.copy2` 完整复制文件,mtime 和 EXIF 都保留
- 视频:软链接,内容不变

## 测试

后端各模块有独立测试:

```bash
cd webapp
python -m pytest backend/tests/ -v
```

## 故障排查

- **扫描无结果**:确认路径是绝对路径且目录存在
- **转换失败 "ffmpeg not found"**:`conda activate vphoto` 后重试
- **预览打不开 HEIC**:浏览器原生不支持 HEIC,这是预期行为;用系统相册查看输出文件
- **WebSocket 不推送进度**:确认后端 :8000 在运行;前端开发模式下 Vite 会代理 `/ws`
