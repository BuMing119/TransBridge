# AI 工作流预设与校改润色实施计划

- **状态**：已完成（2026-08-26，综合 QA 通过）
- **日期**：2026-08-26
- **需求**：FR5.13、FR6.9、FR26.7.1
- **架构**：ADR-026

## 目标

让翻译、润色、混合成为可编辑且可分别记忆的预设；以用户当前配置生成不可变有效执行档案；让独立润色和混合润色按所选阶段执行错误检测、定向修复、语言润色和裁决，并只在唯一提交边界修改正式集合。

## 非目标

- 不重写三轮翻译算法、术语检索实现或 TaskRuntime。
- 不新增外部依赖，不改变翻译文件格式。
- 不在本次重做进度窗口和 Excel 报告的视觉布局。

## 当前实现事实与约束

- `WindowConfigView` 已能采集全部后处理控件，但 `LLMConfig` 只保存一份全局后处理设置。
- `AITranslatorWindow._on_polish_start()` 当前只创建 `LLMPolisher`；检测、修复和裁决开关被忽略。
- `PostProcessor` 已包含完整阶段组件，但会直接修改传入条目，并存在历史 `id`/`key` 映射差异；不能直接对正式集合运行。
- `AiRunSpec` 已保存不可变配置摘要，适合扩展有效档案摘要。
- `ai_translator_window.py` 和 `run_controller.py` 接近模块规模门禁；新业务责任必须进入独立模块。

## Story 1：模式预设配置与有效执行档案

### 验收标准

- 三个预设分别保存后处理设置，切换后互不污染；旧配置自动迁移且无需用户操作。
- 润色首次默认启用检测、修复、润色和裁决；用户显式关闭后再次打开仍保持关闭。
- 当前界面值覆盖预设保存值，并冻结进入单次运行；后续 UI/文件修改不改变已启动任务。
- 有效档案可输出不含密钥的阶段摘要和稳定 digest。

### 文件与步骤

- 新增 `application/translation/ai_execution_profile.py`：定义冻结档案、预设默认与配置合并合同。
- 修改 `config/llm.py`：增加版本化预设 JSON 的安全序列化、校验和旧字段迁移；旧 `pp_*` 字段保留兼容。
- 修改 `ui/tools/ai_translator/config_presenter.py` 与配置 adapter：切换预设前保存当前字段，切换后渲染目标预设。
- 修改 `run_spec.py`：记录有效阶段摘要/digest；不复制 secret。

### 测试

- 配置往返、畸形 JSON 回退、旧配置迁移、预设互不污染。
- UI 切换后控件恢复、用户修改优先级、运行冻结快照。

## Story 2：候选式校改润色流水线

### 验收标准

- 默认按检测 → 修复 → 润色 → 裁决执行，且每个开关都能真正跳过对应阶段。
- 检测出的错误进入 Refiner；Polisher 接收到修复后的译文，而不是旧译文。
- 正式 `TranslationEntry` 在 worker 执行和预览取消时不发生修改。
- 所有结果按稳定 EntryKey 归一化；`id != key` 时仍能关联问题、修复、润色和裁决。
- 自动应用只提交 pass；pending/reject/failed 保留原译文并有原因。

### 文件与步骤

- 新增 `ai_translator/post_processor/proofread_pipeline.py`：组合现有 Checker/Refiner/Polisher/Arbiter，返回结构化候选与兼容的润色结果 projection。
- 修正 `post_processor.py` 的阶段输入链和 identity 归一化，保证翻译后公共后处理也不会丢失修复结果。
- 改造 `_polish_worker.py` 为通用 pipeline worker，保持现有 Qt signal 生命周期协议。
- 扩展 `result_presenter.py`：按候选 verdict 提交，保留不可变 Entry identity。

### 测试

- 用 stub checker/refiner/polisher/arbiter 验证阶段顺序、关闭阶段、修复后再润色、保守失败语义和无正式 mutation。
- 覆盖纯润色、只检查、检查+修复、完整校改四种组合。

## Story 3：翻译/润色/混合入口统一接线

### 验收标准

- 独立润色从有效档案创建校改流水线，不再固定只调用 `LLMPolisher`。
- 混合润色复用同一流水线，且不再强制关闭用户选择的阶段。
- 启动前摘要显示实际启用阶段；预览和直接应用都使用同一最终候选。
- 同一稳定 EntryKey 在混合任务内只分配一个主动作，不重复执行。
- 现有暂停、取消、进度、报告入口和三模式按钮保持可用。

### 文件与步骤

- 新增 `ui/tools/ai_translator/workflow_profiles.py` 或等价薄协调器，避免扩张窗口 facade。
- 修改 `run_controller.py` 的 worker factory 和 `_mixed_worker.py` 的润色子流程，统一从档案构建 pipeline。
- 最小修改 `ai_translator_window.py`：模式切换委托预设协调器；启动时传递有效档案和候选结果。
- 调整快速运行摘要、预览/报告映射，使其反映“检测/修复/润色/裁决”实际阶段。

### 测试

- 运行控制器 factory 参数映射、独立润色和混合润色等价性、混合去重、迟到结果 guard。
- 相关 UI slice 测试、AI post-process 聚焦测试、Ruff 检查与格式检查。

## 依赖顺序

Story 1 → Story 2 → Story 3。Story 2 的纯 Python 流水线可先在不接 UI 的情况下验证；Story 3 只负责组合。

## 风险、兼容与回退

- 预设 JSON 损坏时回退到默认/旧字段并忽略非法项，不覆盖原文件直到用户再次保存。
- 旧配置 API 和 `pp_*` 属性继续有效；非 UI 入口未传预设时使用翻译预设兼容行为。
- 完整校改会增加 API 调用；有效阶段摘要和每个独立开关提供成本控制。
- 任一入口回归可切回旧 adapter，但不得删除预设数据或恢复静默忽略配置。

## 明确假设

- 用户已经确认“用户配置优先、模式只是预设”以及“润色默认检查并修复错误翻译”，实现范围以此为准。
- 默认非严格裁决把不确定结果留给人工审核，不自动覆盖。

## 完成证据

- AI 翻译、后处理、UI slice 与配置等价性回归：153 passed。
- 统一配置仓库契约：12 passed。
- 本次变更相关 Ruff lint/format：全部通过。
