# Lyria STUDIO v0.28 Windows 候选版

本版以 v0.27 主版为基底，使用单一主版构建，用户可从字幕菜单或核对页「更多」选择历史识别结果。先生成原词主轴，再导入无时间中文字幕时，打开「与翻译配对」可逐项核对已有译文。

## OCR 选区

在「导入 / 识别」页可探测字幕带或增加最多四个选区。画布支持拖动和缩放，右侧可分别设置名称、说话人、样式及启用状态。探测结果会列出命中次数和样例，固定短角标可手动勾选。全新工程首次识别可直接进入核对。已有主轴时会打开本轮结果，可勾选要应用的句子或打开「与翻译配对」。应用后可整步撤销。选区属性变更时可选择只作用于后续识别，或同步现有句子中仍由选区管理的属性。

自动打轴页可直接导入演唱原文。该页的可选中文放入译文池供配对。说话人名称保存在字幕行和选区，两个核对视图均可回车指定、多选后一次撤销。请保留旧工程原件，v0.28 另存的 .lyria 工程使用格式 4。

## Build and resource boundary

The Windows x64 Python 3.12 build requires the audited `resources/` directory, pinned dependencies and `LyriaQt.spec`. Run `tools/audit_portable_runtime.py source`, prebuild checks, the full applicable tests and postbuild audit against the final tree. Use isolated evidence directories and `tools/private_desktop.py` for GUI tests; never fall back to the user's desktop. The build script changes its isolated build environment; do not point it at an existing frozen environment.

Private media, user projects, credentials, raw advice and experimental output are not source-package inputs. Dependency bundling, third-party notices, corresponding sources, known media-library security issues and clean-Windows acceptance remain explicit release gates, not automatically resolved by a new version number.

## 便携启动

保留应用目录、shared-runtime 与相对 VBS 的目录关系，移动时复制整个交付目录。冻结应用在相邻的 profiles/Lyria-v0.28-main 创建设置与恢复资料，也支持显式 --storage-dir。测试时请使用工程副本。

单主版构建使用 tools/build_v028.py。它复用源文件审计、独立 Git 检查、冻结前全量回归、冻结后运行时审计和隔离桌面自检。实际候选的提交、源码指纹和 EXE 哈希见 build-metadata.json。
