# 小米 Motion Photo → iPhone Live Photo 转换指南

## 一、格式分析结论（基于真实文件实测）

### 1. 小米 MVIMG 文件结构

测试文件：`MVIMG_20260324_220411.jpg`（9.4 MB）

**文件布局（从头到尾拼接）：**
```
[Primary JPEG]   0 → 3,999,729 bytes  (3.81 MB)  封面图
[GainMap JPEG]   3,999,729 → 4,646,783 bytes  (647,054 bytes)  HDR 增益图
[MP4 视频]        4,646,783 → 9,421,990 bytes  (4,775,207 bytes)
```

**XMP 元数据（Google Motion Photo v1 Container 格式）：**
```xml
GCamera:MotionPhoto="1"
GCamera:MotionPhotoVersion="1"
GCamera:MotionPhotoPresentationTimestampUs="1222965"
Container:Directory:
  - Item:Mime="image/jpeg"  Item:Semantic="Primary"     (Length=0，到 GainMap 前为止)
  - Item:Mime="image/jpeg"  Item:Semantic="GainMap"     Item:Length="647054"
  - Item:Mime="video/mp4"   Item:Semantic="MotionPhoto" Item:Length="4775207"
```

**提取各段的方法：**
- 各段长度从 XMP `Item:Length` 读取
- 从文件末尾倒推：`mp4_start = total_size - mp4_length`
- 主 JPEG 结束位置：`total_size - gainmap_length - mp4_length`

> **注意**：旧版小米（2023 年以前）使用 `GCamera:MicroVideoOffset` 格式，新版（2024+）已升级为 Container:Directory 格式。本工具均支持。

**内嵌 MP4 规格：**
- 容器：MP4 (`ftyp brand: mp42`)
- 视频：HEVC/H.265 (`hvc1`)，~30fps
- 音频：AAC (`mp4a`), 44100Hz，单声道
- 时长：约 3 秒

**GainMap 规格（Ultra HDR / Adobe hdrgm 格式）：**
- 独立 JPEG，单通道灰度（mode L）
- XMP 中含 `hdrgm:GainMapMax`（本例 = 1.11991，即 `2^1.12 ≈ 2.17x` HDR 容量）
- 像素编码：`encoded_recovery = pixel/255 * GainMapMax` 对应 log2 增益

---

### 2. iPhone Live Photo 文件结构

测试文件：`IMG_1635.HEIC` (2.5 MB) + `IMG_1635.MOV` (3.1 MB)

**HEIC 文件：**
- `ftyp brand: heic`，compatible brands: `mif1 heic miaf tmap ...`
- Box 结构：`ftyp` + `meta` + `mdat`
- 图片编码：HEVC；分辨率 5712×4284 (24.5MP)
- **ContentIdentifier**：存储在 EXIF MakerNote（Apple iOS IFD tag 0x0011）
- **PhotoIdentifier**：Apple iOS IFD tag 0x002B
- **HDR GainMap**：作为 aux image 存储在 HEIC meta/iinf 中，类型 `urn:com:apple:photo:2020:aux:hdrgainmap`，由 `iref(auxl)` 关联到主图

**MOV 文件：**
- `ftyp brand: qt`（Apple QuickTime）
- 视频：HEVC，1920×1440，~30fps，约 2 秒
- **ContentIdentifier**：存储在 QuickTime metadata，key `com.apple.quicktime.content.identifier`

**Live Photo 识别机制（iOS Photos.app）：**
1. HEIC 与 MOV **文件名相同**（仅扩展名不同）
2. 两者的 **ContentIdentifier UUID 完全一致**（二者缺一不可）

**Apple Photos Library 内部实际存储：**
```
~/Pictures/Photos Library.photoslibrary/originals/9/
  {ASSET-UUID}.heic       ← 静态图
  {ASSET-UUID}_3.mov      ← 视频（_3 是 Photos.app 内部命名约定）
```
文件名 UUID 是 Photos 分配的 asset ID，与 ContentIdentifier 无关。

---

### 3. Apple MakerNote 格式（Live Photo 标识）

Apple 在 HEIC EXIF MakerNote 中存储 Live Photo 元数据，格式为自定义 IFD：

```
Header: 'Apple iOS\x00\x00\x01MM' + entry_count (big-endian)
IFD entries (12 bytes each): tag + type + count + value_or_offset
Variable data: ASCII strings (null-terminated)

关键 tags:
  0x0001 (MakerNoteVersion)    = 16
  0x0011 (ContentIdentifier)   = UUID string
  0x0014 (ImageCaptureType)    = 12 (Live Photo; exiftool 显示为 "Scene")
  0x0017 (LivePhotoVideoIndex) = 8595185700
  0x002B (PhotoIdentifier)     = UUID string（独立于 ContentIdentifier）
```

---

### 4. HDR GainMap 格式对比

| 项目 | 小米 MVIMG | iPhone HEIC |
|------|-----------|-------------|
| 格式标准 | Adobe Ultra HDR (`hdrgm` namespace) | Apple 私有 aux image |
| 存储位置 | 独立 JPEG，拼接在主图 JPEG 后 | HEIC meta 内，`iinf` + `iref(auxl)` |
| GainMap 像素编码 | sRGB 反变换后对数域编码 | sRGB 编码的线性增益值 |
| 像素转换公式 | `pixel_gain = 2^(pixel/255 * GainMapMax)` | `apple_linear = (gain-1)/(headroom-1)`；`apple_pixel = sRGB_encode(linear)` |
| 直接互用？ | 否，需像素级转换 | — |

**Xiaomi → Apple 像素转换算法（来自 Apple-photo-to-UltraHDR-motion-photo 项目逆向）：**

```python
headroom = 2.0 ** gainmap_max          # e.g. 2.177
pixel_gain = 2.0 ** (u8/255 * gainmap_max)
apple_linear = (pixel_gain - 1.0) / (headroom - 1.0)
# sRGB encode
encoded = 1.055 * linear**(1/2.4) - 0.055  # if linear > 0.0031308
apple_u8 = int(encoded * 255 + 0.5)
```

---

## 二、转换方案实现

### 工具结构

```
mvimg2livephoto/
  parser.py        解析 MVIMG XMP，返回 MotionPhotoLayout（各段偏移+长度）
  extractor.py     根据 layout 提取 Primary JPEG bytes 和 MP4 bytes
  converter.py     MP4 → MOV（ffmpeg -c copy，stream copy 不重编码）
  metadata.py      构建 Apple MakerNote、复制 EXIF、注入 ContentIdentifier
  hdr_injector.py  GainMap 像素转换 + 将 aux image 注入 HEIC box 结构
  builder.py       流水线整合，支持单文件/并发批量/HDR 模式
  cli.py           命令行入口（convert / scan）
  __main__.py      支持 python -m mvimg2livephoto 调用
```

### 转换流水线

**默认模式（SDR）：**
```
MVIMG.jpg
   ↓ parser.py     解析 XMP，定位 JPEG/MP4 偏移
   ↓ extractor.py  提取 Primary JPEG + MP4
   ↓ converter.py  ffmpeg -c copy → .MOV
   ↓ metadata.py
       生成 UUID (ContentIdentifier + PhotoIdentifier)
       piexif 复制原始 EXIF（时间戳/GPS/相机信息）
       修复 SceneType 类型（Android=int，piexif 需 bytes）
       pillow-heif 将 JPEG 重编码为 HEIC，附带新 EXIF
       exiftool 将 ContentIdentifier 写入 .MOV QuickTime metadata
   ↓
OUTPUT.HEIC + OUTPUT.MOV
```

**HDR 模式（`--hdr`）：**
```
MVIMG.jpg
   ↓ (同上，先生成 SDR HEIC)
   ↓ hdr_injector.py
       从 MVIMG 末尾提取 GainMap JPEG
       读取 XMP 中的 hdrgm:GainMapMax
       将 GainMap 像素从 Ultra HDR 格式转换为 Apple sRGB 编码格式（256项 LUT）
       pillow-heif 将 GainMap 编码为 HEVC（mode L）
       修改 HEIC 二进制：
         iinf → 新增 infe（type=hvc1，item_id=N）
         iloc → 新增 entry（base_offset 指向文件末尾追加的 aux 数据）
         iref → 新增 auxl 引用（from=N, to=primary_item_id）
         iprp/ipco → 新增 auxC 属性（含 Apple HDR aux URN）
         iprp/ipma → 新增 item→property 关联
         修正所有旧 iloc 条目的 base_offset（补偿 meta box 增大的字节数）
       aux 数据追加到文件末尾
   ↓
OUTPUT.HEIC（含 Apple HDR aux image）+ OUTPUT.MOV
```

### 关键实现决策

1. **JPEG → HEIC 用 pillow-heif 重新编码（不是直接改 ftyp）**
   - libheif 生成的 HEIC box 结构正确，`ftyp brand=heic`，iOS 直接识别
   - 代价：x265 软件编码，~4 秒/张（12MP）

2. **MP4 → MOV 用 ffmpeg stream copy**
   - `-c copy -f mov`，不重编码，<1 秒/张，画质无损

3. **EXIF 复制用 piexif + pillow-heif**
   - pillow-heif 原生支持写入 EXIF bytes
   - 需修复 `SceneType(41729)`：Android 写 int，piexif dump 要求 bytes

4. **ContentIdentifier 写入 MOV 用 exiftool**
   - QuickTime metadata 格式复杂（mebx box），exiftool 是最可靠方式

5. **HDR GainMap 注入用手工 HEIC box 操作（不用 ctypes/libheif C API）**
   - ctypes 调用 libheif 有 segfault 风险（pillow_heif 内部 dylib 不稳定）
   - pillow_heif Python API 不暴露写 aux image 的接口
   - 手工修改 HEIC binary：在 meta box 内新增 iinf/iloc/iref/ipma/auxC 条目，aux 数据追加到文件末尾
   - 关键修复：meta box 增大后，必须补偿所有旧 iloc base_offset（mdat 相对位置不变，但绝对偏移增大了 meta_delta 字节）

6. **GainMap 像素转换用 256-entry LUT**
   - 小米 GainMap：对数域编码（Ultra HDR 标准）
   - Apple GainMap：sRGB 编码的线性增益（通过 sRGB 反变换 + 线性变换）
   - 转换算法来源：Apple-photo-to-UltraHDR-motion-photo（Rust 实现逆向）

---

## 三、依赖工具

| 工具 | 安装 | 用途 |
|------|------|------|
| `ffmpeg` | `brew install ffmpeg` | MP4 → MOV 容器转换 |
| `exiftool` | `brew install exiftool` | 写入 MOV ContentIdentifier |
| `pillow-heif` | `pip install pillow-heif` | JPEG → HEIC 编码，GainMap 编码 |
| `piexif` | `pip install piexif` | EXIF 读写 |
| `Pillow` | `pip install Pillow` | 图像处理 |

---

## 四、使用方法

```bash
# 激活环境
conda activate vphoto

# 必须从项目根目录运行（或使用绝对路径）
cd /path/to/MMIMG2LivePhoto

# 转换单张（SDR，默认）
python -m mvimg2livephoto convert android_xiaomi_motion_photo/MVIMG_*.jpg -o output/

# 转换并保留 HDR
python -m mvimg2livephoto convert android_xiaomi_motion_photo/MVIMG_*.jpg -o output/ --hdr

# 批量（find 适配任意目录）
python -m mvimg2livephoto convert $(find /path/to/photos -name 'MVIMG_*.jpg') -o output/

# 指定并发数
python -m mvimg2livephoto convert MVIMG_*.jpg -o output/ -j 4

# 扫描目录中的 Motion Photo
python -m mvimg2livephoto scan ./photos/

# 运行全部测试
bash run_tests.sh
```

**注意：** `python mvimg2livephoto/cli.py ...` 会报 relative import 错误，必须用 `python -m mvimg2livephoto` 形式。

---

## 五、导入到 iOS Photos

产出的 `STEM.HEIC` + `STEM.MOV` 两个文件，放入同一目录后：

1. **Mac Photos.app 导入（推荐批量）**：`文件 → 导入` → Photos 自动识别为 Live Photo → iCloud 同步到 iPhone
2. **AirDrop**：同时选中两个文件发送到 iPhone，接收后自动识别

`.livp` 格式（百度网盘包装）是中间格式，不是 iOS 系统原生格式，不推荐批量迁移使用。

**HDR 显示要求：** HEIC 含 aux:hdrgainmap 后，需要 iPhone 12 Pro 以上 + iOS 17+ 才能显示 HDR 效果；其他设备正常显示 SDR 封面图，不受影响。

---

## 六、测试验证

```
test_parser.py       5 tests  — XMP 解析、偏移计算、两种格式（MicroVideo/Container）
test_extractor.py    6 tests  — JPEG/MP4 提取、格式校验、JPEG EOI 标记
test_converter.py    5 tests  — MP4→MOV、qt brand、moov box 存在
test_metadata.py    10 tests  — MakerNote 构建、EXIF 保留/修复、ContentIdentifier 注入、GainMap LUT
test_integration.py 18 tests  — 端到端流水线（SDR/HDR）、ContentIdentifier 配对、
                                时间戳/GPS 保留、HDR aux type 验证、
                                并发批量、CLI（含 --hdr）
test_heic_integrity.py 10 tests — HEIC 输出完整性（ftyp 品牌、iloc 偏移、pixi 通道数、
                                  像素非黑、XMP 剥离）
```

---

## 七、HEIC 格式修复记录（Photos.app 显示问题）

转换后的 HEIC 在 Apple Photos.app 中出现了一系列显示问题，根因都在于 pillow_heif
生成的 HEIC 容器与 iPhone 原生 HEIC 的结构差异。以下按发现顺序记录。

### 修复 1：缩略图灰色（网格视图）

**现象**：导入 Photos.app 后，网格视图里缩略图是灰色的，直到点开才显示彩色。

**根因**：Android JPEG 的 XMP（`GCamera:MotionPhoto`、`Container:Directory`、
`hdrgm:GainMap`）通过 `pillow_heif.from_pillow()` 泄漏进 HEIC 容器。Photos.app
识别到这些 Android 标记后拒绝渲染缩略图。iPhone 原生 HEIC 不携带任何 XMP。

**修复**：在 `jpeg_to_heic()` 中编码前清除 XMP：
```python
img.info.pop("xmp", None)
img.info.pop("Xmp", None)
heif_file = pillow_heif.from_pillow(img)
```

### 修复 2：打开时灰色闪烁（彩色→灰→彩色）

**现象**：点击照片打开时，先彩色再变灰再恢复彩色，有约 0.3 秒的灰色闪烁。

**根因**：`ftyp` box 缺少 Apple 私有品牌。pillow_heif 只写 `mif1, heic, miaf`，
但 Photos.app 需要 `MiHB, MiHA, heix, MiHE, MiPr, tmap` 才能识别为 Apple 原生 HEIC。
缺少这些品牌时 Photos.app 先尝试 Apple 解码路径（彩色），失败后回退到通用解码
（灰色闪烁），最终渲染成功。

**修复**：`_patch_ftyp_apple_brands()` 覆写 ftyp payload，品牌列表改为：
`mif1, MiHB, MiHA, heix, MiHE, MiPr, heic, miaf, tmap`（box 从 28 字节扩展到 52 字节）。

### 修复 3：ftyp 扩展导致黑图

**现象**：修复 2 后生成的 HEIC 完全无法解码，显示全黑。

**根因**：ftyp 从 28 字节扩展到 52 字节（delta=24），所有后续 box 偏移 24 字节。
`iloc` box 的 `base_offset` 是绝对文件偏移，指向 `mdat` 里的图像数据——偏移没更新
导致解码器读到错误位置。

**修复**：`_adjust_iloc_offsets()` 解析 iloc box，给每个 `base_offset` 加 delta。
关键细节：`extent_offset` 是相对 base_offset 的偏移（通常为 0），不能也加 delta，
否则偏移两倍。

### 修复 4：iOS 相册查看模式灰色（编辑模式正常）

**现象**：隔空投送到 iPhone 17（iOS 26）后，缩略图正常、长按播放实况正常、
点编辑按钮后编辑状态下颜色也正常，但正常查看模式点开后整张图是灰色的。

**根因**：pillow_heif 写的 `pixi`（pixel information）box 声明 `num_channels=1`
（单色），但实际 HEVC 数据是 RGB 3 通道。

- iPhone 原生 HEIC 的 pixi：`num_channels=3, bits=8,8,8`（16 bytes）
- pillow_heif 生成的 pixi：`num_channels=1, bits=8`（14 bytes）

iOS 相册的查看模式读 pixi 决定渲染方式——看到 1 通道按灰度处理。编辑模式走
不同解码路径所以正常。Mac Photos.app 容错更强，不依赖 pixi。

**修复**：`_patch_pixi_rgb()` 把 pixi 从 14 字节扩展到 16 字节：
- 旧 payload：`00000000 01 08`（1 通道，8 位）
- 新 payload：`00000000 03 08 08 08`（3 通道，各 8 位）
- 同时更新 meta/iprp/ipco 的 box size 和 iloc base_offset（+2 字节偏移）

### 修复顺序

在 `jpeg_to_heic()` 中，pillow_heif 保存后依次执行：
1. `_patch_pixi_rgb()` — 修 pixi 通道数，delta=+2
2. `_patch_ftyp_apple_brands()` — 修 ftyp 品牌，delta=+24

每个 patch 独立调整 iloc 偏移，互不影响。两个 patch 累计使 mdat 偏移 +26 字节。

### iPhone 原生 HEIC 与我们输出的对比

| 结构 | iPhone 原生 | pillow_heif 默认 | 修复后 |
|------|------------|-----------------|--------|
| ftyp 品牌 | mif1,MiHB,MiHA,heix,MiHE,MiPr,heic,miaf,tmap | mif1,heic,miaf | 与 iPhone 一致 |
| pixi 通道数 | 3 (RGB) | 1 (monochrome) | 3 (RGB) |
| XMP | 无 | 有（Android 残留） | 无 |
| iloc 偏移 | 正确 | 正确 | 正确（patch 后调整） |

---

## 八、Parser MicroVideo 格式修复（MiShare 兼容）

**现象**：从 MiShare 目录来的照片（经 iPhone → 小米分享 → 安卓相册）虽然 XMP 标记了
`GCamera:MicroVideo="1"`，转换时报错 `Extracted JPEG does not start with FF D8`。

**根因**：`parser.py` 的 `primary_jpeg_end` 属性假设所有 `length > 0` 的段都在文件末尾，
用 `total_size - sum(all_lengths)` 计算 Primary JPEG 结束位置。但 MicroVideo 格式
（旧版小米/Google 格式）在解析时给 Primary 段设置了显式 length = `total_size - mp4_length`，
导致 `sum(lengths) == total_size`，`primary_jpeg_end` 算出来是 0。

**两种 XMP 格式的段布局对比：**

| 格式 | Primary length | 计算方式 |
|------|---------------|---------|
| Container:Directory（新版小米） | 0（未指定） | `total - sum(其他段)` 倒推 |
| MicroVideo（旧版/MiShare） | `total - mp4_length`（显式） | 应直接用 length |

**修复**：`primary_jpeg_end` 优先用 Primary 段的显式 length（若 > 0），否则才倒推：
```python
@property
def primary_jpeg_end(self) -> int:
    primary = self._segment("Primary")
    if primary is not None and primary.length > 0:
        return primary.length
    tail = sum(s.length + s.padding for s in self.segments
               if s.length > 0 and s.semantic != "Primary")
    return self.total_size - tail
```

这同时修复了 MicroVideo 格式下 `mp4_start` 的计算（`mp4_start = total - mp4_length`，
与 `primary_jpeg_end` 相等，两者拼接覆盖整个文件）。

