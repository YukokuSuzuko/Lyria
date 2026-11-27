# 第三方依赖、字体与外部模型归属说明

本仓库自有源代码采用 GNU General Public License v3.0（[LICENSE](LICENSE)）发布，并附带 [GPL-3.0 第 7 条硬件运行时附加许可声明](ADDITIONAL_PERMISSION_RUNTIME.txt)。各第三方库、辅助字体及外部模型各自适用其原始许可协议。

## 一、系统字体与辅助字体边界

- **Windows 界面与字幕样式**：桌面端界面请求系统字体 `Microsoft YaHei UI`，默认字幕样式请求 `Microsoft YaHei`；字幕字体名称由用户自行选定，导出 ASS 仅写入字体名称，**软件源码与安装包均不打包、不分发微软雅黑或其他 Windows 商业系统字体文件**。
- **依赖自带的辅助字体数据**：
  - Tesseract `tessdata/pdf.ttf`（572 字节，内置于部分 `libtesseract` 资源中）：用于 PDF 文本层占位的 GlyphLessFont，上游标注为 Apache-2.0（[Tesseract 5.5.3 LICENSE](https://raw.githubusercontent.com/tesseract-ocr/tesseract/5.5.3/LICENSE)）。
  - Pillow `13.0.0` 内置默认字形子集：Aileron Regular 子集（CC0 公共领域贡献）及 Pillow 默认位图字形 `courB08`（遵循 [Pillow 13.0.0 MIT-CMU 许可证](https://raw.githubusercontent.com/python-pillow/Pillow/13.0.0/LICENSE)）。

## 二、桌面与后端依赖组件

核心 Python 依赖清单见 `requirements.txt`、`requirements.qt.txt` 与 `requirements.qt.tests.txt`，仓库 [`licenses/`](licenses/) 目录保留对应许可档案：

- **Qt / PySide6 / shiboken6（6.8.3）**：遵循 `LGPL-3.0` / `GPL-3.0`（Windows 包内 Qt6Multimedia 自带的 FFmpeg 7.1 动态库为 `LGPL-2.1-or-later`）。相关说明见 [Qt for Python 许可说明](https://doc.qt.io/qtforpython-6/licenses.html)。
- **PyAV（16.0.1）与内置 FFmpeg（8.0）**：PyAV 绑定代码遵循 BSD-3-Clause；官方 Windows 发行包内附带的 `av.libs` 启用了 `libx264` 与 `libx265` 编码器，组合分发遵循 `GPL-3.0` / `GPL-2.0+`。
- **独立 FFmpeg CLI（Gyan 9.0.2-full_build）**：遵循 `GPL-3.0`，官方构建说明见 [`licenses/FFmpeg-CLI-9.0.2/`](licenses/) 与 [FFmpeg 官网](https://ffmpeg.org/download.html)。
- **OpenCV / NumPy / Pillow / PyInstaller / `regex`**：`regex` 许可原件保存于 [`licenses/regex/`](licenses/regex/)。
- **RapidOCR（3.4.1）与 FlatBuffers（25.12.19）**：遵循 Apache-2.0，固定版本许可原件与校验记录保存于 `licenses/rapidocr-3.4.1-LICENSE`、`licenses/flatbuffers-25.12.19-LICENSE` 及 [补充许可证说明](licenses/SUPPLEMENTAL_LICENSES_v0.22.md)；PP-OCR ONNX 模型归属见 [`licenses/ppocrv6/`](licenses/ppocrv6/)。
- **ONNX Runtime & Microsoft DirectML（1.15.4）**：ONNX Runtime 遵循 MIT（详见 [`licenses/onnxruntime-directml/`](licenses/onnxruntime-directml/)），`DirectML.dll` 遵循随包附带的 Microsoft Software License Terms for DirectML。
- **Tesseract OCR 与 Windows 图像运行时**：主程序及语言包遵循 Apache-2.0（`resources/tesseract/doc/LICENSE`）；`liblzma 5.8.4-1` 源码与配方记录保存于 [`licenses/windows-runtime/liblzma-5.8.4/`](licenses/windows-runtime/liblzma-5.8.4/)，`jbigkit 2.1` 遵循 `GPL-2.0-or-later`。

Windows 安装包内全部外部二进制组件的版本、编译配置及上游对应源码包对照见 [二进制组件与源码对照清单](docs/THIRD_PARTY_BINARIES.md)。

## 三、按需下载的独立本地模型与外部环境

软件本体与安装包**不内置**对齐、人声分离或语音听写大模型权重；用户在模型管理中心按需下载或手动导入的组件适用以下独立许可：

- **NextFire（`NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn`）**：模型权重遵循 **CC-BY-NC-SA-4.0（署名-非商业性使用-相同方式共享 4.0）**，仅限非商业研究与个人非营利使用。
- **UVR 人声分离模型（`UVR-MDX-NET-Inst_HQ_3` 等）**：归属与来源说明见 [UVR 模型署名说明](docs/UVR_MODEL_ATTRIBUTION.md)。
- **外部独立推理环境依赖**：按需安装模型运行环境时，如引入 `pykakasi`（`GPL-3.0-or-later`）、`soynlp`（`GPL-3.0`）等第三方组件，应遵守各自组件所示开源条款。
