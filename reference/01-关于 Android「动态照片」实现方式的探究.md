> 原文 URL：https://zhuanlan.zhihu.com/p/11126715794

---

关于 Android「动态照片」实现方式的探究

Ye Han​​
iPhone话题下的优秀答主
​关注他
30 人赞同了该文章
发布于 2024-12-06 18:42・安徽
1. 前言
在回答这个问题[1]时我发现自己对 Android 手机实现「动态照片」的原理一无所知，也不知道 iOS 的 Live Photo 与 Android 上面「动态照片」的区别是什么，于是测试小米手机「动态照片」发现得不到理想结果，就到网吧电脑上对其一探究竟，现在已经有了初步了解，所以写篇文章记录一下，以备日后创作引用。

2. 原理探究
在小米手机上，不论是「相册」还是「文件管理」均显示「动态照片」为 .jpg 格式。但问题在于 .jpg 是静态图像的压缩标准[2]，难不成小米手机把「动态照片」的视频隐藏到其他文件目录中了吗？

2.1 导出测试
如果「小米手机把『动态照片』的视频隐藏到其他文件目录中」这一猜想成立，那么只导出「动态照片」那张照片，再查看的时候由于链接不到隐藏的视频，从而变成一张「静态照片」。

但是在导出以后发现脱离了小米手机的「动态照片」在电脑上照样可以播放，「小米手机把『动态照片』的视频隐藏到其他文件目录中」这一猜想被证明是不成立的。


即使离开手机，「动态照片」依旧可以正常播放
2.2 XMP 元数据
经过检索资料发现我的思路从一开始就错了，Android 手机采用了与 iOS 的 HEIF 完全不同的实现方案，就是把「动态照片」里面的 Micro Video 封装进了 .jpg 静态图像的结尾，然后通过 XMP[3] 元数据来标记视频位置[4]。


在动态照片中，XMP 元数据标记了视频数据开始和结束的位置
「动态照片」嵌入的XMP元数据主要包含以下信息：

      GCamera:MicroVideoVersion="1"
      GCamera:MicroVideo="1"
      GCamera:MicroVideoOffset="9131117"
      GCamera:MicroVideoPresentationTimestampUs="823341"
Xmp.GCamera.MicroVideoVersion：视频版本号
Xmp.GCamera.MicroVideo：是否为动态照片
Xmp.GCamera.MicroVideoOffset：视频文件偏移量
MicroVideoPresentationTimestampUs：视频播放时间戳

在 IrFanView 里面可以查看 XMP Tag 元数据
「动态照片」中的图片封面和视频均可以被已取出来，具体可参考这里：

如何提取MVIMG的照片/视频组件？ - image - SO中文参考 - www.soinside.com
www.soinside.com/question/UTyr7uQBEU8ewK6e5dneqZ
2.3 原理总结
「动态照片」是 Android 手机通行的一种实现方式，小米，三星，谷歌均采用相同原理实现，华为也一样。这个实现原理总结起来其实也简单：

Android「动态照片」的视频是直接写入到 .jpg 图片中，追加在图片数据结尾，并使用 XMP 元数据标记了视频数据的起止位置信息。在受支持的图片查看器中显示显示为「动态照片」，在不受支持的查看其中仅显示封面图片。
事实上拆分和导出「动态照片」的封面与视频并不是一件容易的事情，因为 .jpg 并不是理想的多媒体封装格式，里面的照片和视频片段均以编码数据形式存放缺乏独立性，而且 .jpg 的特性一旦被修改就会丢失视频元数据，无法再逆向还原，属于一种破坏性编辑。


一经修改就会丢失数据
3. 「动态照片」与「实况照片」
这里通过横向对比进一步加深对这种格式的认识：

对比项目	动态照片	实况照片
封装格式	.jpg	.heif
内容形式	照片编码数据+视频编码数据	照片+视频
编辑支持	一经编辑就会丢失视频数据	编辑过后不会丢失数据
还原支持	一经编辑不可还原	编辑过后仍可还原
压缩效率	压缩效率低，文件较大	压缩效率高，文件较小
导出支持	支持 .jpg 直接导出	不支持 .heif 导出
兼容性	容易被魔成独占格式	编解码特殊，兼容性差
内容提取	提取封面图片或视频困难	原文件导出即可分离出图片和视频
就现阶段来看，Android 把「动态照片」做成 .jpg 导致其可编辑性很差，一经编辑就会丢失视频内容，而且无法再还原回来，在未来可能还会向 .heif 靠拢。

参考
^比起live图为什么不直接拍视频？ - 知乎  https://www.zhihu.com/question/5129928205
^JPEG 是用于连续色调静态图像压缩的一种标准，文件后缀名为.jpg或.jpeg，是最常用的图像文件格式。 https://wenku.baidu.com/view/60645f1559fafab069dc5022aaea998fcc2240a7?fr=xueshu_top
^XMP（可扩展元数据平台）是Experience Manager Assets用于所有元数据管理的元数据标准。 XMP为各种应用程序的元数据的创建、处理和交换提供了一个标准格式。 https://experienceleague.adobe.com/zh-hans/docs/experience-manager-cloud-service/content/assets/admin/xmp-metadata
^解析Android的动态照片  https://wszqkzqk.github.io/2024/08/01/%E8%A7%A3%E6%9E%90Android%E7%9A%84%E5%8A%A8%E6%80%81%E7%85%A7%E7%89%87/
Android
iOS
