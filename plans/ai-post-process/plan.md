# AI 翻译后处理

> **状态**: Story-01~14 已实现
> **模块**: `src/transbridge/ai_translator/post_processor/`

## 概述

AI 翻译完成后的五阶段质量保障流水线：检测 → 修复 → 润色 → 裁决 → 执行。自动发现并修复翻译质量问题，生成结构化 Excel 报告。

## Story 清单

| Story | 标题 | 状态 |
|-------|------|------|
| Story-01 | 一致性检查器（术语一致性、风格一致性） | ✔️ |
| Story-02 | 格式验证器（特殊标签、占位符） | ✔️ |
| Story-03 | 质量门禁（可配置阈值判定） | ✔️ |
| Story-04 | LLM 修复智能体（LLMRefiner：针对性修复问题条目） | ✔️ |
| Story-05 | LLM 润色智能体（LLMPolisher：提升流畅度和风格） | ✔️ |
| Story-06 | LLM 裁决智能体（LLMArbiter：pass/reject/pending） | ✔️ |
| Story-07 | 后处理主控器（PostProcessor：协调五阶段执行 + 断点续传） | ✔️ |
| Story-08 | 后处理报告生成（多 Sheet Excel：Summary/Entries/Issues/Refinements/Arbitrations） | ⚠️ 已废弃（被 Story-10~13 替代） |
| Story-09 | 独立润色入口（AI 翻译窗口润色模式，跳过翻译直接润色已翻译条目） | ✔️ 已实现 |
| Story-10 | 报告生成后端与Excel导出（ReportGenerator + 翻译5Sheet/润色3Sheet + 文件轮转） | ✔️ 已实现 · [详细](stories/story-10-report-backend.md) |
| Story-11 | 应用内报告对话框（多Tab QDialog：汇总/条目/问题，翻译/润色双模板，双击跳转Step2） | ✔️ 已实现 · [详细](stories/story-11-report-dialog.md) |
| Story-12 | 完成流程集成（替换QMessageBox，翻译/润色/批量完成→报告对话框，批量跨插件汇总） | ✔️ 已实现 · [详细](stories/story-12-integration.md) |
| Story-13 | 历史报告查看（工具面板入口 + 历史文件列表 + 双击打开Excel） | ✔️ 已实现 · [详细](stories/story-13-history-viewer.md) |
| Story-14 | 后处理提示词契约修复与阶段级缓存 | ✔️ 已实现 · [详细](stories/story-14-prompt-contract-stage-cache.md) |

## Story-14：后处理提示词契约修复与阶段级缓存

**状态**：✔️ 已实现
**详细文档**：[stories/story-14-prompt-contract-stage-cache.md](stories/story-14-prompt-contract-stage-cache.md)

### 目标

修复质量检测、问题修复、翻译润色和质量裁决提示词与代码之间的既有契约偏差，并为每个“阶段 × 单条/批量”提示词建立一个完整稳定 `SYSTEM` 前缀和一个阶段级缓存断点；动态条目、问题、设置和现有术语继续位于 `USER`。

### 验收标准

- [ ] 八个提示词变体均保持 `SYSTEM -> USER` 两消息结构，每个变体只有一个位于完整稳定 `SYSTEM` 末尾的缓存断点。
- [ ] 不建立跨后处理阶段共享的 A/B 双层 System；各阶段使用独立 cache key。
- [ ] 润色 System 中的游戏、源语言和目标语言变量被正确渲染，不向模型泄漏 `$...` 占位符。
- [ ] Refiner 恢复“只修复明确问题”的既有 Story 边界，不承担 Polisher 的润色职责。
- [ ] 单条 Arbiter 能看到润色后译文、润色详情和润色者信心度。
- [ ] 批量 QualityGate 使用每条现有动态术语，术语匹配、顺序和语义不变。
- [ ] 单条/批量输出协议、结果数据类、解析器及裁决阈值保持兼容。
- [ ] JSON 示例均为合法 JSON，枚举约束与示例数据分开表达。
- [ ] 官方 OpenAI、Anthropic 与非官方兼容端点复用 Story-15 的 Provider 缓存转换与清理边界。
- [ ] 缓存未达 token 门槛或 Provider 不支持时安全降级，不填充无意义提示词。

### Story-09: 独立润色入口

**详细文档**: `plans/ai-post-process/stories/story-09-standalone-polish.md`

**对应需求**: FR6.9（独立润色入口）
**状态**: ✔️ 已实现
**归属**: ai-post-process（追加）

#### 验收标准

- [ ] AI 翻译窗口顶部有翻译/润色模式切换控件
- [ ] 润色模式下，翻译范围选项替换为「润色选中已翻译条目」，无译文条目自动跳过
- [ ] 「开始润色」按钮替换「开始翻译」按钮
- [ ] 润色配置（强度 light/moderate/aggressive、范围）复用后处理标签页现有控件
- [ ] 新增配置项「润色后预览确认」（checkbox，默认关闭），自动保存到 LLMConfig
- [ ] 预览确认开启：润色完成后弹出 `_PolishPreviewDialog`，三列对比原文/原译文/润色结果，逐条接受/拒绝
- [ ] 预览确认关闭：润色结果直接写入条目（通过 `entry._replace()`）
- [ ] 润色过程显示进度窗口（复用 `_TranslationProgressWindow` 模式），支持暂停/停止
- [ ] 选中条目均无译文时弹出提示「所选条目均无译文，无法润色」
- [ ] LLM 调用失败时保留原译文并在预览中标注失败原因

#### 实现步骤

**步骤 1**: AITranslatorWindow 模式切换
- 在窗口顶部（LLM 配置区上方或翻译范围区上方）增加模式切换控件（QButtonGroup + 两个 QRadioButton：「翻译」「润色」）
- 切换为润色模式时：隐藏翻译范围区三个选项，显示「润色选中已翻译条目」提示；翻译范围选项变灰或隐藏；「开始翻译」按钮文案变为「开始润色」
- 切换回翻译模式时恢复原状
- 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`

**步骤 2**: 新增 `_PolishWorker`（QThread）
- 新建 `_polish_worker.py`，实现 `_PolishWorker(QThread)`
- 构造函数接收：`llm_client`, `entries: list[TranslationEntry]`, `polish_level: str`, `term_manager`
- `run()`: 逐条或分批调用 `LLMPolisher.polish()` / `polish_batch()`，发出 `progress(int current, int total)` 和 `entry_done(str entry_id, PolishResult)` 信号
- 支持 `stop()` / `pause()` （通过 Event 控制）
- 涉及文件: `src/transbridge/ui/tools/ai_translator/_polish_worker.py` (新)

**步骤 3**: 新增 `_PolishPreviewDialog`
- 新建 `_polish_preview_dialog.py`，实现 `_PolishPreviewDialog(QDialog)`
- 布局：顶部工具栏（「全部接受」「全部拒绝」按钮 + 统计标签（已处理/总数）），中间 QTableWidget（三列：原文 | 原译文 | 润色结果），底部「确认应用」按钮
- 润色结果列使用不同颜色标记：绿色=接受的润色，红色=拒绝的润色（保留原译文），默认行=待处理
- 每行支持右键菜单或行内按钮「接受」「拒绝」
- `get_results()` → `dict[str, str | None]`（entry_id → 最终译文，None 表示拒绝润色保留原译文）
- 涉及文件: `src/transbridge/ui/tools/ai_translator/_polish_preview_dialog.py` (新)

**步骤 4**: `_on_start()` 模式分流
- 在 `_on_start()` 开头检查当前模式
- 润色模式：
  1. 检查选中条目中是否有已翻译条目，无则弹窗提示返回
  2. 创建 LLMClient，创建 LLMPolisher
  3. 创建 `_PolishWorker`，启动
  4. 检查配置项 `polish_preview_enabled`
  5. 若开启预览 → Worker 完成后弹出 `_PolishPreviewDialog`，用户确认后写入条目
  6. 若关闭预览 → Worker 完成后直接写入条目
  7. 写入方式：构造新 TranslationEntry（translation=润色结果, stage 不变），调用 `entry._replace()`
- 翻译模式：保持现有逻辑不变
- 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`

**步骤 5**: LLMConfig 扩展
- 新增字段 `polish_preview_enabled: bool = False`（润色后预览确认开关）
- 在 `_load_config()` / `_save_config()` 中读写该字段
- 涉及文件: `src/transbridge/paratranz/config_manager.py`

#### 架构依赖

- `LLMPolisher` (`src/transbridge/ai_translator/post_processor/polisher.py`) — 已实现，无需修改
- `LLMConfig` (`src/transbridge/paratranz/config_manager.py`) — 新增 1 个字段
- `TranslationEntry._replace()` (`src/transbridge/converter/translation_entry.py`) — 已有方法，直接使用
- 复用 `_TranslationProgressWindow` 进度窗口模式

## 关键文件

- `src/transbridge/ai_translator/post_processor/post_processor.py` — PostProcessor, PostProcessorConfig (14字段)
- `src/transbridge/ai_translator/post_processor/consistency_checker.py` — 一致性检查
- `src/transbridge/ai_translator/post_processor/quality_gate.py` — 质量门禁
- `src/transbridge/ai_translator/post_processor/llm_refiner.py` — LLMRefiner, RefineResult
- `src/transbridge/ai_translator/post_processor/polisher.py` — LLMPolisher, PolishResult
- `src/transbridge/ai_translator/post_processor/llm_arbiter.py` — LLMArbiter, ArbiterDecision
- `src/transbridge/ai_translator/post_processor/checkpoint.py` — PostProcessCheckpoint
- `src/transbridge/ui/tools/ai_translator/ai_translator_window.py` — AI 翻译窗口（Story-09 改造主战场）
- `src/transbridge/ui/tools/ai_translator/_polish_worker.py` — 润色后台线程（Story-09 新增）
- `src/transbridge/ui/tools/ai_translator/_polish_preview_dialog.py` — 润色预览对话框（Story-09 新增）

## 相关文档

- [后处理报告设计](../../docs/dev/post_process_report.md)

## 综合整改状态增量（2026-08-18）

- `partially-verified`：保留 13 Story 历史交付；候选阶段传递、精确 scope、失败终态、幂等恢复和报告单源未通过本轮验收。
- `blocked_by`：`unified-task-translation-runtime-v2` S03/S06/S07、`release-hardening-v2` S02/S03。
- `superseded_by`：各阶段直接/隐式修改正式值与多种报告 DTO 由候选链、唯一 commit 和 ReportSnapshot 取代。
