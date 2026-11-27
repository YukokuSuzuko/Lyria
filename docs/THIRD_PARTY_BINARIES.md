# Lyria STUDIO v0.3.4 Windows x64 二进制组件与源码对照清单

本文档记录 `Lyria STUDIO v0.3.4 测试版` Windows x64 安装包内所含主要第三方二进制组件、实际版本号、适用许可证及对应上游源码获取方式。各组件原始许可证正文与构建档案同步收录于安装目录 `v0.3.4/licenses/` 下。

---

## 一、Lyria 本体

| 组件 | 版本 | 许可证 | 对应源码 / 说明 |
| :--- | :--- | :--- | :--- |
| `Lyria STUDIO` (`Lyria.exe`) | `0.3.4`（`v0.3.4 测试版`） | `GPL-3.0` + 硬件运行时附加许可 | 完整源码见本仓库 `main` 分支 / 标签 `v0.3.4` 及随附 [GPL-3.0](../LICENSE)、[运行时附加许可](../licenses/ADDITIONAL_PERMISSION_RUNTIME.txt) |

---

## 二、多媒体、界面框架与核心第三方二进制组件

| 组件 | 实际打包版本 | 适用许可证 | 对应源码与构建材料位置 |
| :--- | :--- | :--- | :--- |
| **Qt / PySide6 / shiboken6** | `6.8.3` | `LGPL-3.0` / `GPL-3.0` | 安装目录 `v0.3.4/_internal/PySide6/` 下全部 DLL/PYD 以独立动态库形式分发，用户可自行替换兼容版本的 Qt / PySide6 动态库。随包 `v0.3.4/licenses/Qt-PySide6-6.8.3/` 附有完整 `LGPL-3.0.txt`、`GPL-3.0.txt`、`LGPL-2.1.txt` 与源码/重链接说明；官方源码：`https://download.qt.io/official_releases/qt/6.8/6.8.3/` 与 `https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.8.3` |
| **Qt6 内置 FFmpeg 库**（`PySide6/avutil-59.dll`、`avcodec-61.dll`、`avformat-61.dll`） | FFmpeg `7.1` | `LGPL-2.1-or-later` | 由 Qt 6.8.3 官方采用纯 LGPL 配置编译（不含 `libx264` / `libx265` 等 GPL 编码器），位于 `PySide6/` 目录支持动态库替换；完整编译配置记录于 `v0.3.4/licenses/Qt-PySide6-6.8.3/QT_SOURCE_AND_RELINKING.md` |
| **PyAV 及其内置 FFmpeg (`av.libs`)** | PyAV `16.0.1` · FFmpeg `8.0`（`pyav-ffmpeg 8.0-2`） | `GPL-3.0` / `GPL-2.0+`（PyAV 绑定层本身为 BSD-3-Clause，因内置 `--enable-libx264 --enable-libx265` 故整套组合按 GPL 分发） | `PyAV-v16.0.1`、`pyav-ffmpeg-8.0-2`（含 `scripts/build-ffmpeg.py` 与 `patches/ffmpeg.patch`）、`FFmpeg-n8.0`、`x264 (32c3b801191522961102d4bea292cdb61068d0dd)`、`x265 (4.1)` 完整对应源码归档已打包于同页发布的 `Lyria-v0.3.4-GPL-Corresponding-Sources.zip`。同时于包内 `v0.3.4/licenses/PyAV-16.0.1-and-av.libs/` 附带 `AVLIBS_BUILD_CONFIG.txt` 与构建脚本；其余上游编解码依赖精确版本：`gmp 6.3.0`、`libunistring 1.3`、`nettle 3.10.1`、`gnutls 3.8.9`、`libaom 3.11.0`、`dav1d 1.5.1`、`SVT-AV1 3.1.0`、`lame 3.100`、`libogg 1.3.5`、`opus 1.5.2`、`speex 1.2.1`、`twolame 0.4.0`、`libvorbis 1.3.7`、`libvpx 1.15.1`、`libpng 1.6.47`、`libwebp 1.5.0`、`openh264 2.6.0`、`opencore-amr 0.1.5`、`nv-codec-headers n13.0.19.0` |
| **独立 FFmpeg CLI (`ffmpeg.exe`)** | Gyan `9.0.2-full_build` | `GPL-3.0` | 对应上游 FFmpeg 提交 `946fcce07b6dcd0331c8cc609192aeff5e1924f8`；完整 `--enable-...` 构建参数收录于 `v0.3.4/licenses/FFmpeg-CLI-9.0.2/BUILD_CONFIGURATION.txt`，完整对应源码已打包入同页发布的 `Lyria-v0.3.4-GPL-Corresponding-Sources.zip` |
| **OpenCV (`opencv-python`)** | `5.0.0.93` | `Apache-2.0` | 完整第三方声明（含 `opencv_videoio_ffmpeg500_64.dll` LGPL 说明）位于 `v0.3.4/_internal/cv2/LICENSE-3RD-PARTY.txt` 与 `LICENSE.txt` |
| **ONNX Runtime & DirectML** | ORT DirectML · DirectML `1.15.4+241025-1615.1.dml-1.15.fac7597` | ORT：`MIT`；DirectML：`Microsoft Software License Terms` | 仅在独立隔离工作进程按需调用；安装目录 `v0.3.4/licenses/DirectML-1.15.4/` 及 `accelerators/directml/` 下完整附带微软官方 `LICENSE.txt`、`ThirdPartyNotices.txt` 与 ONNX Runtime 声明 |
| **RapidOCR & PP-OCR ONNX 模型** | RapidOCR `3.4.1` | `Apache-2.0` | 详见 `v0.3.4/licenses/rapidocr-3.4.1-LICENSE` 与 `v0.3.4/licenses/ppocrv6/` |
| **Tesseract OCR 与 MSYS2 图像库** | Tesseract / `jbigkit 2.1-5` / `liblzma 5.8.4-1` / `libtiff 4.7.2-1` / `leptonica 1.87.0-1` / `giflib 6.1.3-1` | 主程序：`Apache-2.0`；`jbigkit`：`GPL-2.0-or-later` | 备选 OCR 组件位于 `resources/tesseract/`；`liblzma-5.8.4`、`jbigkit 2.1` 等已核验对应源码与构建声明收录于 `v0.3.4/licenses/windows-runtime/` 及伴随源码包。注：当前为公开测试版（`v0.3.4`），个别辅助 MinGW/GCC 运行库仍在持续补充完整构建账本 |
