## 关于本仓库

本仓库用于将 安卓的 MMIMG 图片(.jpg) 动态图片 转换成 iPhone/iOS 的 Live Photo 实况图片。包括但不限于 格式完美转换、图片 meta info 保留（拍摄时间、位置信息、设备信息等等，都需要完美保留，不可修改时间）。

##  1. 关于测试用例

- `android_xiaomi_motion_photo/MVIMG_20260324_220411.jpg` 和 `android_xiaomi_motion_photo/MVIMG_20260314_194711.jpg` 为安卓小米系统中拍摄的 Motion Photo 动态图片，经过小米手机测试，可以播放、有声音。

- `android_oneplus_normal_jpg/IMG_20250108_103106.jpg` 为安卓一加手机拍摄的正常 JPG，非 Motion Photo。

- `android_xiaomi_normal_heic/IMG_20260507_173522.HEIC` 为安卓小米手机拍摄的正常 HPIC，非 Motion Photo。

- `iphone_live_photo/IMG_1635.HEIC`和`iphone_live_photo/IMG_1635.MOV` 为从 iPhone 手机（iOS26） 导出的动态实况图片


## 2. 关于安卓 Motion Photo 和 iPhone 实况 Live Photo 转换的相关博客和源码

- `reference/00-写了一个苹果实况照片批量转小米动态照片的脚本.md`
- `reference/01-关于 Android「动态照片」实现方式的探究.md`
- `reference/02-iPhone 的「实况照片」和 Android 手机的「动态照片」有什么区别.md`
- `reference/03-从编解码角度看，iPhone拍摄的Live Photos实况照片究竟是什么.md`
- `reference/04-国内厂商动态照片·实况照片格式对比.md`
- `reference/Apple-photo-to-UltraHDR-motion-photo`
- `reference/AppleLIVP_to_XiaomiMotionPhoto`
- `reference/pyheic_struct` 安卓三星动态图片转 IOS 实况


## 3. 如何研究分析怎么转换？

1. 先阅读2 中的博客和源码。博客中可能提到一些源码，可以 clone 到 `reference/` 后（如果本地该目录没有对应源码），本地分析对应源码。

2. 如果用 python，请先用 conda 激活虚拟环境 vphoto。

3. 可以利用各种 pip 包或 brew 安装工具，结合【1. 关于测试用例】中的图片进行真实的文件格式分析

4. 确凿的结论、关键信息，记录在 `guide.md` 