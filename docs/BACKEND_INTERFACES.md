# 现有业务入口与状态映射

依据 v0.10 业务源码及 0.11.0.dev1 桌面适配核对。此表用于防止界面改造丢功能，**不是 HTTP/WebSocket 协议**。用户随后已授权使用原交接 Moonlit Lilac；Windows 实机验收仍待完成。

旧 Tk 入口表保留作历史对照；当前 Qt 时间轴接口见文末 2026-11-04 补充。图片 API 已移除，不得按旧接口记录恢复。

## 状态与更新原则

- `Project` 是业务来源；`Mapping.id` 等 ID 稳定，列表序号只用于显示。各类型内部 ID 不得重复。
- 当前中文统一读 `editing.mapping_text(project, mapping)`；`Translation.text` 保留原始导入内容，`text_override=None` 与空串不同。
- `Editor(project, history, on_change)` 校验后立即变更合法值。变更后刷新列表/正文/预览并自动保存；确认只标记审核，不提交隐藏草稿。
- `History.restore()` 返回新 `Project`；调用者须替换当前引用并刷新选择/正文，不能继续使用旧对象。
- 后台 OCR/全文 AI 使用 `Project.from_dict(project.to_dict())` 快照；任务前 `ProjectSaver.flush()`，完成后主线程提交结果。不能让后台和交互线程同时改同一对象。
- UI 帧从 1 开始、末帧包含；内部时间 `[start,end)`。`FrameIndex` 使用真实 PTS；不要平均 FPS 反复换算。
- 播放选中与用户主动选中须区分。进度拖动由用户拥有滑块，旧 decode generation 不能覆盖最新 seek。

## 业务能力与正式接入点

| 能力 | 当前代码入口 | 必须保留的规则 |
| --- | --- | --- |
| 新建、打开、保存、恢复 | `Application.new/open/save`；`storage.load_project/save_project/ProjectSaver/copy_project_cache`；`LiveEdits.restore_last_task` | 打开工作副本；保存失败可见；WAL/API 预算随副本；不覆盖素材 |
| 文件分类与角色选择 | `importing.plan_files/resolve_plan` | 文本歧义由用户选角色；工程单独开；系统拖入由 `file_drop`/`DesktopFrontend` 接入；替换前确认保存成功 |
| 双输入/三输入识别、取消 | `pipeline.run(..., progress, cancel, api_key)`；`Application.start_run/cancel` | 原文可选；时间来自画面；取消保存已完成检查点 |
| 当前中文/起止时间 | `Editor.set_text/set_time`；`LiveEdits.commit_*` | 合法值立即写入；输入中间态保留上次合法值；编辑撤销确认但保留人工保护 |
| 确认/取消确认 | `Editor.confirm`；`LiveEdits.confirm_current/cancel_confirmation` | 不改正文和时间；不把算法分数伪造成 100% |
| 拆分、合并 | `editing.split_mapping/merge_mappings`；`ReviewActions.split_dialog/merge_selected` | 缺时间也可拆合；不编造外侧边界；原中文不重写；连续合并可用 |
| 重复本句 | `Application.repeat_mapping` | 新 ID、初始时间未知；原句和重复句均不能在重匹配时丢失；可撤销 |
| 新增空白、填空档 | `Editor.insert_blank/fill_gap`；`LiveEdits.add_blank_subtitle/fill_current_gap` | 新条初始未知；空档要合法且无其他占用，不移动邻句 |
| 撤销/重做/分组 | `History.push/begin/end/restore`；`ReviewActions.undo` | 一次长按/一次边缘拖动一个命令；不是每像素一个命令 |
| 固定帧标记 | `Editor.add_marker/delete_marker/split_at_marker`；`LiveEdits.add_current_marker` | 原标记不移动、使用后保留；字符偏移为 Python Unicode，不是 JS UTF-16 |
| 顺序导航、筛选、编号、重叠 | `Application.refresh`；`WorkspaceActions.previous_mapping`；`ReviewActions.confirm_next`；`workspace.overlap_ids` | 可见顺序导航，不跳过已确认；筛选不重编号；真实重叠仅标红 |
| 播放、逐帧、seek、本句播放 | `Application.seek/request_seek/request_step/step/toggle_play/play_current_cue`；`PlaybackBuffer` | 真实帧索引；音频时钟；容量 6；暂停不阻塞等待解码 |
| 跳到句首、长按 | `LiveEdits.jump_to_current_start`；`PreviewHold`；`WorkspaceActions.nudge_boundary` | 句首按钮一次定位；播放位置与字幕边界分开；失焦松手停止 |
| 单句 OCR/轮廓候选 | `rescan.rescan_cue/adopt_rescan`；`LiveEdits.rescan_current_cue`；`WorkspaceActions.offer_outline_assist/apply_outline_time` | 显示候选后主动采用；不覆盖人工锁定；不冒充可靠真值 |
| 全文文字 AI、单句参考 | `api.suggest/translate_reference`；`LiveEdits.run_text_review`；`ReviewActions.reference_translation` | 仅文本/ID；调用预算含失败重试；不改中文/猜时间；旧建议过期 |
| AI 设置与凭据 | `ai_config`；`credentials.KeyStore`；`Application.api_settings` | 统一文字配置；旧 namespace 不变；不将密钥写入工程或仓库 |
| 中文布局、成品/草稿/遗漏导出 | `export.placement_options/layout_entries/validate_project/export_project`；`Application.export/style_settings` | 预览/导出同配置；草稿显式；遗漏报告；结构错误不能绕过 |
| 独立成片质检 | 当前 `audit_service.run_audit/rescan_audit/record_audit_review` | 本地渲染与两处 ROI 的真实 OCR；文字 API 可选；不传视频或图片；一帧容差，证据不足不算通过 |
| KTV 独立转换 | `export.convert_ktv`；`Application.converter_ui/convert` | 不依赖视频/API；保留真实重叠；无重叠模式主动选择 |
| 计算/主题/布局/日志/帮助 | `WorkspaceActions.ocr_settings/appearance_settings/reset_layout/show_history/show_log/show_help`；`Application.write_log` | 保留主题设置与默认强调色；不因视觉改版删除工具能力 |

## 0.11.0.dev1 桌面适配

`frontend.DesktopFrontend` 接真实工程状态；`frame_timeline.FrameTimeline` 提供三轨 PTS 边界编辑与吸附；`marker_track.MarkerTrack` 提供固定标记/字间切分；`file_drop.NativeFileDrop` 提供 Windows 文件拖入适配，非 Windows 保留选择文件。`appearance` 的模式/阶段过渡只移动小 Canvas 图元，不再改变整页 padding。

以上代码已有无界面回归，但 Windows IME、waveOut 音频、DPI、真实鼠标/Explorer 拖入、长时间播放器、凭据、DirectML 与便携打包仍需 Windows 实测。真实 MV 人工真值与用户 Aegisub 原案例需要另行提供样本，不得用合成测试替代。

## 2026-11-04 Qt 时间轴接口

- `Bridge.timelineRows` 是独立的只读 `CueModel`，包含全部字幕，不受核对列表的“仅未确认”筛选影响。`count`、`get(index)` 提供数量和单行副本；模型重置、单行变化与数量变化各发对应信号。
- `uiState.selectedTimelineIndex`、最多五行的 `timelineContext`、`timelineContextFirstIndex` 与 `timelineBounds` 提供有限上下文。视频呈现回调不重建或深拷贝整个时间轴；完整列表不进入 `uiState`。
- `timeline.select {id}` 只选择字幕，不暂停或定位视频。显式选择被核对筛选隐藏的字幕有效，`selectedVisibleIndex` 此时为 `-1`。
- `timeline.move {id, delta}` 使用整数帧位移，按实际 PTS 一次提交首末两边。保持包含的帧数，允许真实重叠，超出视频端点时夹紧整段；锁定、未知时间和缺少索引时拒绝而不修改历史。
- 拖动预览在 QML 内完成，释放提交一次。`history.begin/end` 仍可将多个 move 合为一次撤销。取消/失焦不提交未释放的片段移动。
- 撤销历史保存当时的选中 ID，但不将 UI 上下文写进工程文件。结构编辑、文字与时间编辑后保留选中上下文；用户显式播放、定位或逐帧操作才重新允许播放跟随。
- C 剃刀以真实帧 F 立即切时，前段截至 F−1、后段自 F 起，保留外侧时间和来源引用。中文全文暂留前段，后段真实为空；`project.settings.pending_text_splits` 持久化待分配关系，AE 仅以占位“待分割字幕”显示空段。用户之后主动选字间断点分配，切时和分字各自一次撤销，不自动猜字或要求额外时间确认；原确认/导出条件不变。参见 [AE 剃刀与事后中文分配](RAZOR_DEFERRED_TEXT_20261104.md)。既有固定标记拖至字间切分仍保留。
