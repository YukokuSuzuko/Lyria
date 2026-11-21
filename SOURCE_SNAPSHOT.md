# Lyria STUDIO v0.28 开发源码快照

本分支对应开发快照 `97c187c78ec00606875f97de3069a3866fbab55c`，只用于源码协作，不是安装包、可直接分发的测试版或公开发布。

- 应用入口：`app_qt.py`；应用实现位于 `desktop_qt/` 与 `lyricsync/`。
- 依赖、构建与测试：见 [README_QT.md](README_QT.md)、[Windows 构建说明](docs/WINDOWS_BUILD_NOTES.md) 和 `requirements*.txt`。Windows 构建需要独立准备其所需运行时、资源及模型。所附测试源码中的媒体或外部依赖相关用例，需另外准备相应输入。
- 第三方归属：见 [THIRD_PARTY.md](THIRD_PARTY.md) 和 [`licenses/`](licenses/)。保留这些通知不代表已完成项目本身的公开许可或分发审查。

快照不含本机配置、凭据、个人工程或媒体、验收录屏或截图、历史构建输出、模型权重及打包运行时。
