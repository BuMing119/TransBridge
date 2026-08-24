# Story-05：AI Translator 配置、作用域、运行与结果切片

- **所属 Plan**：[UI 展示层模块化与上帝窗口拆分](../plan.md)
- **状态**：已完成（2026-08-19）
- **优先级**：P1
- **前置依赖**：S01、S02；S04 的公开 selection/projection port
- **下游**：S07、S08

## 目标

保留 AI Translator 的翻译、混合、润色和报告能力，把配置、作用域、运行与结果提交从 1600+ 行窗口中解耦，并移除对 MainWindow/Step2 私有实现的反向依赖。

## 原始验收标准

- [x] `AITranslatorWindow` 公开入口、现有翻译/润色/混合入口和报告行为兼容。
- [x] Config、Scope、Run、Result 四个切片职责独立；配置保存、workload 构建、worker/task、结果映射没有循环依赖。
- [x] 不再 import MainWindow 或 Step2 私有 helper/常量，不再使用 `_find_main_window()`；定位/选择/进度使用显式 ports。
- [x] 正式 TranslationEntry 更新仍走既有唯一提交点，预览/报告不能隐式写回。
- [x] run_id/owner/generation 防止窗口关闭、重跑或切换 scope 后的迟到结果污染当前 UI。
- [x] 高频进度和结果呈现满足 NFR1.5，无额外全量 projection 复制。

## 当前职责与目标接口

- `_load_config()`、`_save_config()`、`_schedule_save()`、provider/mode handlers、`_build_llm_config()` → `ConfigView/ConfigPresenter`。
- scope preset/stage/label/category、`_build_scope_candidates()`、`_get_filtered_entry_ids()` → `ScopeView/ScopePresenter`，依赖 S04 `SelectionPort`/`ProjectionPort`。
- `_on_start()`、`_on_mixed_start()`、`_on_polish_start()` 及 worker/progress ownership → `RunController`。
- mixed/polish finished、preview、apply、report/history/locate → `ResultPresenter`，通过 `ResultViewPort` 与 `EntryMutationPort`。

计划 DTO：

- `TranslatorConfigState`：UI 字段与 validation errors；不持有 widget。
- `TranslationScope(stage, label_ids, categories, selected_ids, projection_revision)`。
- `TranslationRunRequest(mode, config, scope, owner_id, run_id)`；构建 application request 时再次校验 revision。
- `TranslationRunResult(run_id, candidates/report, terminal_state)`；预览和正式提交分离。

```text
config/scope intents -> presenters -> validated snapshot
start -> RunController -> existing worker/Task/application request
progress/result(run_id) -> ResultPresenter -> preview/report
accept -> existing canonical mutation command -> projection -> Workbench render
```

## 实施步骤

1. 将 `_ALL_CATEGORIES`、`_entry_category`、`_COL_KEY` 的真实语义迁到 S04 公开 projection/column ports；保留必要兼容重导出。
2. 抽 ConfigView/Presenter，维持当前防抖、默认值、连接测试和错误消息；配置失败不启动运行。
3. 抽 ScopeView/Presenter，以 stable IDs 和 projection revision 表达候选；不接触 Step2 widget 私有字段。
4. 建立统一 `TranslationRunRequest`，让三种入口复用 owner/run_id、取消和进度连接，但保留各自业务 service。
5. 抽 ResultPresenter；预览只持候选/报告，accept 才调用唯一 mutation port；history/locate 通过 shell/workbench port。
6. 让 `AITranslatorWindow` 只组装四个切片，删除 `_find_main_window()`、私有 imports 和重复完成路径。

## 边界与错误

- scope 为空、projection revision 已变化、provider 配置无效、术语为空等保持当前阻断/提示语义。
- 切换 scope 或重跑后，旧 run 的 progress/result 只允许进入历史诊断，不更新当前控件。
- close 时按现有策略取消或保留 Task；无论哪种都必须解除 View 订阅。
- mixed/polish 部分成功不能被包装成全成功；报告分类和 accepted/rejected/failed 计数保持一致。
- 配置 autosave 异常不得使已验证运行结果丢失，也不得在 ResultPresenter 中悄悄重写配置。

## 文件变更

- 修改 `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`
- 新增 `config_view.py`、`config_presenter.py`、`scope_view.py`、`scope_presenter.py`、`run_controller.py`、`result_presenter.py`
- 修改相关 progress/report dialogs 的公开 adapter
- 新增/修改 `tests/ui/tools/test_ai_translator_*.py`

## 测试与建议命令

- `pytest tests/ui -k "ai_translator or polish or translation_progress" -q`
- 三种 mode、配置错误/保存失败、scope revision 变化、取消/重跑/关闭、迟到结果、preview accept/reject、report/history/locate。
- 高频 progress 下 heartbeat/RSS 与无全量 collection copy 检查。

## 风险与回退

先迁 Config/Scope，再迁 Run/Result。每一切片通过 facade 委托，可单独恢复旧 handler；旧新 run path 不能同时连接 worker signal。结果提交前保存 canonical mutation 调用序列，避免重复写回。

## 未决问题

- Worker 是否最终由 TaskRuntime 统一并非本 Story 的架构选择；本次只通过 adapter 接当前权威入口，不顺带重写运行时。
