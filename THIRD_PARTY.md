# Dependencies and resources

## dev9 私人测试包字体核查（2026-11-15）

当前交付为 Windows 私人测试包，已实际执行构建、资源审计与离屏检查；下面 2026-11-04 的准备状态保留为历史，不代表本轮未运行 Windows 程序。整包公开分发/开源许可审查仍未完成，不能把应用源码许可套给所有二进制、字体或模型。

按用户最新决定，Windows UI 继续指定系统 **Microsoft YaHei UI**，不附带微软雅黑或其他 Windows 系统字体文件，不为了开源擅自换字体。字幕默认请求 **Microsoft YaHei**，字体仍由用户在字幕样式中选择；ASS 输出记录字体名称，不嵌入该字体文件。调用系统已安装字体与分发字体文件是两件事，微软雅黑不是开源字体；参考 [Microsoft 字体家族说明](https://learn.microsoft.com/en-us/typography/font-list/microsoft-yahei) 和 [Windows 字体分发 FAQ](https://learn.microsoft.com/en-us/typography/fonts/font-faq)。

最终候选 `build/dev9-final-v3-20261115/dist/Lyria`（EXE SHA256 `3907d914c0b3cd6717ecf5fefefe3a5f47d4ce49b91b4cc1fa3f327084ba37b8`）扫描 1312 个文件、1,035,335,912 字节，并检查 EXE PYZ、Qt 资源和已识别的 DLL 字体数据。本轮未检出 Windows 系统字体，但**不等于包内没有字体**：

| 实际字体数据 | 来源、用途与许可核查 |
|---|---|
| Tesseract `tessdata/pdf.ttf`，572 字节；两个 libtesseract DLL 另嵌入相同数据 | 用于 PDF 的 GlyphLessFont，不是 UI 字体。SHA256 `c7845420925a23d88ed830a63957b8af85a66a8daf8d9fc90e843673b2ef1a59`，与 [Tesseract 5.5.3 pdf_ttf.h](https://raw.githubusercontent.com/tesseract-ocr/tesseract/5.5.3/src/api/pdf_ttf.h) 数组前 572 字节一致；上游标注 2020 Google Inc.、作者 Zdenko Podobny、Apache-2.0。保留包内 Tesseract LICENSE 及本说明，见 [固定版本许可证](https://raw.githubusercontent.com/tesseract-ocr/tesseract/5.5.3/LICENSE)。完整源码 ZIP 包含便携资源，因此也包含这个辅助字体，不能套用旧的“仅 Git 源码归档没有字体”结论。 |
| Pillow 13.0.0 的 Aileron Regular 默认字体子集，内嵌 EXE PYZ | 12,676 字节，SHA256 `69853909b940023570964e29cffe30da95aea8de3627736b5cd15ab30143169f`，与 [Pillow 固定版本 ImageFont.py](https://raw.githubusercontent.com/python-pillow/Pillow/13.0.0/src/PIL/ImageFont.py) 数据一致。作者 Sora Sagano 的 [Aileron 页面](https://dotcolon.net/fonts/aileron/) 声明 No Rights Reserved；[Pillow 引入记录 #7354](https://github.com/python-pillow/Pillow/pull/7354) 记录 CC0 字体选取与子集处理。不是应用指定的 UI 字体，也不应误写为 OFL。 |
| Pillow 历史 `courB08` 默认位图字体，内嵌 metrics 与 PNG | 包内保留 [Pillow 13.0.0 MIT-CMU 软件许可证](https://raw.githubusercontent.com/python-pillow/Pillow/13.0.0/LICENSE)。本轮尚未核实这个旧位图字形的独立、精确原始来源/版权链，**列为待核实**；不凭相同名字套用其他字体许可，也不将“待核实”表述成已知无许可。 |

实际字体选择与指定名称分开记录：本机 Qt 离屏环境仅设置 FONTDIR 时未枚举 YaHei TTC，曾回退 Arial；在单一测试进程中只读注册本机 `msyh.ttc` 后，QFontInfo 正确解析 YaHei UI，生产代码已加引号的 Canvas 字体也通过。代表性 glyph runs 中汉字/日文使用 YaHei UI，韩文、部分符号、emoji 分别回退本机其他系统字体。没有安装、复制或分发系统字体；这些离屏结果不等于 Windows 前台字体已验收。Qt 行为依据：[QFontInfo](https://doc.qt.io/qt-6.8/qfontinfo.html)、[QFont 回退](https://doc.qt.io/qt-6.8/qfont.html)。

发布检查必须对**最终程序 ZIP 和完整源码 ZIP**同时检查松散字体、依赖内嵌字体及资源，防止误带 `msyh*.ttc`、`segoe*.ttf` 或其他受限系统字体；保留已识别依赖的许可证和字体版权说明，`courB08` 来源核查及其余完整第三方分发审查完成前不宣称整包已通过公开发布审计。本轮不做跨平台字体替换。

## Evidence status (2026-11-04, latest preparation)

This remains a source/development candidate, **not an audited Windows release**.
The active Linux test tree deliberately does not contain Windows EXEs. Original
Windows inputs have now been recovered and hash-checked in separate candidate
trees, with fixed chi_sim and official documentation. A new isolated candidate
replaces only liblzma with the reviewed 5.8.4 MSYS2 payload and keeps the original
bytes and independently verified rollback. No Windows program has been run.

The current source, prebuild and postbuild inventory does not grant distribution,
security or Windows acceptance. Actual component licenses, corresponding source,
Qt collection scope, PyAV's bundled codec build, DirectML terms and model
provenance remain distinct gates. Do not infer these from a generic BSD/LGPL,
Apache or MIT label. The latest exact findings and preparation are in
[Runtime remediation](docs/RUNTIME_REMEDIATION_20261104.md), with the original
[license inventory](docs/RUNTIME_LICENSE_REVIEW_20261104.md) retained as history.

The historical component descriptions below are provenance notes, not claims
that absent Windows files are in this source ZIP or that a final package exists.

## Qt migration candidate additions (0.12.0.dev0)

The isolated development runtime currently pins PySide6 / Qt 6.8.3 and regex
2026.9.29. These pins describe tested development inputs, not a claim that this
is the latest security-patched Qt release or that Windows redistribution has
completed license review. Release qualification must review supported Qt patch
levels, rerun the same tests for any upgrade, and inventory the libraries and QML
plugins actually collected in the Windows package.

Installed PySide6, PySide6-Essentials, PySide6-Addons and shiboken6 metadata declare
`LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`; this does not settle every bundled
Qt module and third-party dependency's licensing. Do not ship optional GPL-only
modules inadvertently or treat a private repository as satisfying distribution
obligations. The open-source/commercial choice and complete notices/source access
for the actual build remain a release gate.

- https://doc.qt.io/qtforpython-6/licenses.html
- https://www.qt.io/development/open-source-lgpl-obligations
- https://doc.qt.io/qt-6/licensing.html
- https://github.com/mrabarnett/mrab-regex (Unicode grapheme segmentation)

The regex wheel's license has been copied unchanged into `licenses/regex/`.
Windows system fonts are still not bundled. Linux OCR QA uses installed Noto or
DejaVu fonts and does not convert that test setup into a Windows font shipment.

## Retained backend/runtime baseline

The retained backend baseline used Python 3.12 x64, Tkinter/Tcl, PyInstaller, PyAV (FFmpeg libraries), NumPy, OpenCV, Pillow, pytesseract, RapidOCR, ONNX Runtime and their dependencies. The Qt candidate excludes Tkinter/Tcl. Historical Windows pins are in requirements.lock.txt; the Qt additions are in requirements.qt.txt. These pins are not evidence of a completed Windows build. Historical copied upstream distribution license files are in licenses/.

Version 0.8 uses onnxruntime-directml 1.23.0 on Windows, retaining CPU as the default provider. The wheel includes DirectML.dll; upstream LICENSE and ThirdPartyNotices are copied to licenses/onnxruntime-directml/. GPU support requires compatible Windows/DirectX drivers. Provider documentation: https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html

The historical handoff recorded Tesseract 5.5.3.20260724 and eng/jpn/jpn_vert language files copied read-only from the supplied Windows runtime installation. The Chinese QC default additionally requires chi_sim.traineddata; its presence was not established by that handoff. Those Windows inputs are absent from this source checkpoint and their provenance/notices must be rechecked when restored. Tesseract and official tessdata sources:

- https://github.com/tesseract-ocr/tesseract
- https://github.com/tesseract-ocr/tessdata_fast
- https://github.com/UB-Mannheim/tesseract/wiki

The historical handoff attributes the RapidOCR 3.4.1 PP-OCRv4 ONNX resources to the package distribution and its official model registry. The current inventory records actual file hashes; this attribution is not a completed model-license review. Japanese model SHA256: e1075a67dba758ecfc7ebc78a10ae61c95ac8fb66a9c86fab5541e33f085cb7a.

- https://github.com/RapidAI/RapidOCR
- https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.4.0/onnx/PP-OCRv4/rec/japan_PP-OCRv4_rec_infer.onnx
- https://github.com/PaddlePaddle/PaddleOCR

The historical FFmpeg notice describes the user's gyan.dev 9.0.2 full build, including libass and GPL/version3-enabled components. Its copied license and README are retained without alteration; no corresponding Windows binary or current reports/build_environment.json is present here. The build audit rejects a restored binary whose reported version differs from that notice, but a matching version does not establish license compliance. Source/build provenance:

- https://www.gyan.dev/ffmpeg/builds/
- https://ffmpeg.org/download.html
- https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz
- https://github.com/libass/libass

Do not infer a single license for all third-party binaries from the application's own source. Consult the upstream license notices before redistribution. No Windows commercial fonts are distributed; OCR visualization uses existing Windows fonts locally.

API documentation used for implementation:

- https://api-docs.deepseek.com/guides/json_mode/
- https://api-docs.deepseek.com/api/create-chat-completion/
- https://aegisub.org/docs/latest/ass_tags/
- https://rapidai.github.io/RapidOCRDocs/
- https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html

## dev8 可选外部日语模型与注音依赖

NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn 固定版本见本地模型目录，权重采用 CC-BY-NC-SA-4.0，不随编辑器分发，不属于 MIT。须遵守非商业、署名与适用的相同方式共享条件。官方模型说明和许可链接显示在安装与许可页。

可选自动读音 pykakasi 2.3.0 为 GPL-3.0-or-later，仅外部模型环境使用，不捆绑进编辑器。安装前查看相关许可；旧环境不会静默修改。详见 docs/DEV8_NEXTFIRE_BACKEND.md 与 docs/DEV8_MODEL_WORKFLOW.md。


## v0.22 exact-version supplemental license files

The installed RapidOCR 3.4.1 and FlatBuffers 25.12.19 wheels omit license files in their RECORD. Their version-locked official Apache-2.0 license texts are included unchanged in `licenses/rapidocr-3.4.1-LICENSE` and `licenses/flatbuffers-25.12.19-LICENSE`, preserving the RapidOCR Authors / RapidAI and Google / FlatBuffers attributions. See `licenses/SUPPLEMENTAL_LICENSES_v0.22.md` for official commit links and SHA256. No nonexistent upstream NOTICE has been invented; this supplement is not blanket legal clearance for all dependencies or model weights.
