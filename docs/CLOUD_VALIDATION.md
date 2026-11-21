# 云端后端阶段修补记录 · 2026-10-30

> 这是用户解除前端暂缓之前的阶段记录。最新前端候选与测试状态见 `FRONTEND_CANDIDATE.md`。

## 结论

在 v0.10.0 源码独立副本上继续开发，未改前端布局，也未改为 Web/SaaS。当前仅交付源码修补候选；尚未生成或验收新的 Windows 便携程序。

| 检查 | 原始 v0.10 基线 | 本轮修改后 |
| --- | ---: | ---: |
| 发现的测试总数 | 145 | 167 |
| 无界面实际通过 | 90 | 112 |
| 已选择但跳过 | 5 | 5 |
| 显式未运行的桌面测试 | 50 | 50 |
| 无界面失败/错误 | 0 / 0 | 0 / 0 |

本轮新增 **22 项** 离线回归。5 项跳过中，4 项需要先生成 `reports/synthetic` 的完整 OCR 流水线证据，1 项需显式启用 Windows 凭据写入。50 项 Tk 测试因当前云环境无可用显示而排除；尝试启动虚拟显示失败，未将这些测试伪装成通过。

环境：Python 3.12.14；PyAV 16.0.1、NumPy 2.5.4、OpenCV 5.0.0.93、Pillow 13.0.0、RapidOCR 3.4.1、CPU ONNX Runtime 1.23.0。Windows 的 `requirements.txt` 与 `requirements.lock.txt` 保持原样；云端另有 `requirements.cloud*.txt`。`pip check`、源码编译检查与 CRLF 感知的 Git 空白检查通过。

机器可读证据位于 `docs/validation/`；其中工作区绝对路径已改为相对/基线标签，测试数与结果未变。

## 已复现并修复

1. **重复本句重新匹配后消失。** 重复操作现在保护新出现次数；若原句尚未获得人工保护，也保留原出现次数。不会冒充确认，不编造重复句时间。覆盖已确认/未确认源、连续重匹配、取消、独立修改、撤销精确恢复。
2. **草稿遗漏导出时间判据不一致。** 原正常校验为视频末尾加 1 微秒，遗漏识别却放宽 40 毫秒，造成稍越界条目既被拒绝又无法遗漏。现在共用 1 微秒容差；合法行能显式导出，遗漏清单准确，工程不变，结构错误仍不得绕过。
3. **损坏工程 `null`/数组/标量根节点无法恢复备份。** `Project.from_dict` 明确拒绝非对象 JSON，进入既有可恢复错误通路；保留合法旧版 version 2 数据与已有有效备份。
4. **重复稳定 ID 可能静默覆盖内容。** 导出前分别检查译文、原文、视觉片段、映射的内部唯一性；拒绝歧义数据，不重写 ID 或中文。不同类型之间同名 ID 仍合法。
5. **固定 `.tmp` 名可能覆盖硬链接素材，或并发写互相踩临时文件。** JSON 和 ASS 写入改用排他创建、短随机名的同目录临时文件，flush/fsync 后替换，失败清理；拒绝指向输入同一文件的硬链接。回归覆盖原素材不变、失败保留旧文件、并发临时名、长文件名、备份别名、ASS BOM。
6. **移除旧开发机专用 OCR 回退路径。** 保留打包资源、`LYRICSYNC_TESSERACT`、标准 Windows 安装路径与 PATH；用模拟 OCR 模块验证显式路径，未启动任何 OCR EXE。

独立审查曾发现修补中的“只保留重复句而丢原句”和“临时名过长”两个回归，均已补针对性测试并修正后重新执行检查。

## ASS 独立渲染

使用系统原生 FFmpeg 7.1.5/libass，不运行资源包内 Windows EXE。每种帧率生成 240 帧、30 个合成字幕事件：

| 帧率 | 旧量化对照错误帧 | 当前量化错误帧 |
| --- | ---: | ---: |
| 30 | 20 | 0 |
| 30000/1001 | 29 | 0 |
| 60 | 20 | 0 |

这是原有导出算法在本云环境的重新验证，**不是用户 Aegisub 原偏移案例已解决**。本轮未改变帧量化算法。既有 VFR/真实 PTS 单元回归也包含在无界面套件中。

## 未验证、暂缓与下一步

- 此后端阶段结束时前端四项尚暂缓（后续授权后的实现见前端候选文档）：正式 Moonlit Lilac、标记字间拖放、底部帧级条、OS 文件拖入。
- 未做 Windows GUI、微软/搜狗输入法、waveOut 音频、DPI、DirectML、真实长时间拖动、凭据写入或打包验收。
- 未运行完整实际 OCR 模型流水线；不把图像处理单测、已安装依赖或既有 Windows 日志当作本轮 OCR 准确率。
- 未收到真实歌曲人工首末可见帧/配对真值和 Aegisub 原异常样本；不能声称自动时间/语义准确率通过。
- 全文 AI 与图片 API 使用已有模拟接口测试；未发真实歌词、视频、截图或密钥给外部服务。
- 下一可直接推进项：在获准的 Windows 环境执行完整回归与便携构建；用户提供最小原偏移样本后做逐帧交叉验证。没有这些输入，不擅自销项。

## 复现命令

```bash
.venv-linux/bin/python tools/run_headless_tests.py --verbose --json-report cloud-test-results/headless.json
.venv-linux/bin/python tools/validate_ass_render.py --output cloud-test-results/ass-render
.venv-linux/bin/python -m compileall -q lyricsync tests tools app.py
.venv-linux/bin/python -m pip check
git -c core.whitespace=cr-at-eol diff --check
```

保留原有 `python -m unittest discover -s tests -p 'test_*.py' -v` 作为完整套件入口；无界面工具只是明确分范围的补充，不替换 Windows 验收。
