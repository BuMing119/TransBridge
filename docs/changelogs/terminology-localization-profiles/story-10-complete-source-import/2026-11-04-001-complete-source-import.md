# 外部词表完整导入译名方案与两种批量策略

- 日期：2026-11-04
- Epic / Story：`terminology-localization-profiles` / Story 10
- 需求：FR5.18.25
- 计划：[可切换译名方案](../../../../plans/terminology-localization-profiles/plan.md)
- 决策：[ADR-039](../../../adr/039-terminology-localization-profiles.md)
- 验证：[独立 QA 报告](../../../test-reports/terminology-source-import-2026-11-04.md)
- Git 基线：`3ccddc01f19e3a155a2e050792425fe4a2b0711d`。本任务开始时已有无关未提交修改；以下仅记录本任务可归属的增量，不把整个工作区差异作为本次成果。无暂存变更，未提交或推送。

## 用户可见变化

“从外部来源创建译名方案”现在同时导入项目基础术语中不存在的有效词。新增词属于该方案，选用后进入生效浏览与 AI 参考；未发布基础术语的项目也可创建方案。导入不修改项目基础术语、其他方案或项目正文。

用户在读取来源前统一选择“保留已有译名”（默认）或“采用导入译名”。已有译名指复制到新方案的项目默认译名，两种策略均导入新词；不再要求逐词处理异译。来源内部同键异译分别取第一条或最后一条；完全相同译文归并。预览显示固定策略、新增/覆盖/保留数量和可浏览明细，创建使用已预览的结果。

本轮不加入自动继承后续基础译名、外部文件自动同步或修改既有方案的批量导入入口。旧孤立映射保持原有未生效行为；来源新词没有历史译名或可靠位置绑定时，不猜测替换已有正文。

## 逐文件变更

### 导入、持久化与消费

- `src/transbridge/ai_translator/term_source_reader.py`（修改）：`_source_entry()` 保留结构化作用域及规范上下文中的插件身份；普通文本不推断为插件范围，错误的结构化作用域提供上下文错误。
- `src/transbridge/application/terminology_profiles/__init__.py`（修改）：导出 `TerminologyProfileImportPolicy`。
- `src/transbridge/application/terminology_profiles/models.py`（修改）：`ProfileTermMapping.scheme_owned` 明确标记新方案独有术语，默认关闭并校验布尔类型；仅开启时进入内容摘要，保持旧摘要不变。
- `src/transbridge/application/terminology_profiles/importing.py`（修改）：按原词与作用域逻辑键合并全部来源词，增加两个整批策略；允许明确无基础版本或已发布空版本，拒绝不可用/损坏基础版本；来源摘要覆盖顺序，统计与策略保存的原始译文一致。
- `src/transbridge/application/terminology_profiles/import_preview.py`（修改）：预览覆盖实际全部映射，新词显示为新增方案术语；冲突展示已按策略处理的结果，不再生成未导入新词的展示占位行。
- `src/transbridge/application/terminology_profiles/effective.py`（修改）：为明确的方案独有映射派生有效术语，保留基础停用保护；组合版本显式捕获“无基础版本”，恢复时不读取后来发布的基础版本。
- `src/transbridge/application/terminology_profiles/browse_queries.py`（修改）：增加 `SCHEME_OWNED` 状态，区分已生效的方案新词和旧孤立映射。
- `src/transbridge/application/terminology_profiles/entry_projection.py`（修改）：无基础版本时允许已获证据支持的方案独有词投影；缺失证据及损坏/不可用状态继续保持原译文。
- `src/transbridge/persistence/terminology/profiles.py`（修改）：JSON 编解码保留可选独立标记，兼容旧字段集合并拒绝非法标记值；不新增 SQL schema 迁移。

### 导入和编辑界面

- `src/transbridge/ui/tools/terminology_profiles/source_picker_dialog.py`（修改）：读取前提供两种策略及来源重复的首/末值规则说明。
- `src/transbridge/ui/tools/terminology/schemes_controller.py`（修改）：向导入流程传递选定策略。
- `src/transbridge/ui/tools/terminology_profiles/source_import_controller.py`（修改）：冻结策略并传给预览，创建前复核上下文，完成信息使用真实新增/覆盖/保留统计。
- `src/transbridge/ui/workbench/terminology_profile_controller.py`（修改）：去除必须已有有效基础词的导入限制；保留创建时基础版本与内容身份复核，新增不可读状态保护。
- `src/transbridge/ui/tools/terminology_profiles/import_dialog.py`（修改）：只读显示所选策略、完整导入说明、实际计数和全量预览。
- `src/transbridge/ui/tools/terminology_profiles/import_preview_model.py`（修改）：更新结果标签和详情，明确来源异译已处理、新词将进入方案。
- `src/transbridge/ui/tools/terminology_profiles/mapping_model.py`（修改）：在非显示行数据中保留独立标记，搜索、脏状态比较、编辑和保存往返不丢失新词权限。
- `src/transbridge/ui/tools/terminology_profiles/manager_layout.py`（修改）：说明外部来源导入的新词无需基础词即可在当前方案中使用。
- `src/transbridge/ui/tools/terminology/source_browser.py`（修改）：增加方案独有生效状态及筛选，更新解释。用户原有 `WrappingLabel` 修改保持原样，未计入本次变更。

### 回归测试

- `tests/application/terminology_profiles/test_complete_source_import.py`（新增）：两策略、新词、重复/作用域、原文保真、无基础版本冻结恢复、选择隔离、旧孤立映射/停用保护及安全投影。
- `tests/application/terminology_profiles/test_source_import.py`（修改）：对齐默认保留、完整导入、作用域分离和顺序摘要合同。
- `tests/application/terminology_profiles/test_import_preview.py`（修改）：对齐完整映射、策略结果及原始译文统计。
- `tests/persistence/terminology/test_profiles.py`（修改）：SQLite 旧摘要兼容、新独立标记往返及非法值拒绝。
- `tests/ai_translator/test_term_source_reader.py`（修改）：结构化作用域保留、普通上下文不误判、错误元数据拒绝。
- `tests/ui/tools/test_terminology_source_import_controller.py`（修改）：策略传递、取消、关闭和预览期间项目切换不得写入。
- `tests/ui/tools/test_terminology_source_import_dialog.py`（修改）：完整预览、两个策略的冻结展示、计数与新词标签。
- `tests/ui/tools/test_terminology_profile_mapping_model.py`（修改）：独立标记经管理器编辑、保存、复制、发布和筛选保持。
- `tests/ui/tools/terminology/test_schemes_view.py`（修改）：宿主向导入流程传递选定策略。
- `tests/ui/tools/terminology/test_source_browser.py`（修改）：方案独有生效状态与筛选。
- `tests/ui/workbench/test_terminology_profile_switching.py`（修改）：无基础版本创建与选用、预览/创建时损坏或不可用状态保护。

### 需求、计划和验证记录

- `docs/requirements.md`（修改）：原位更新 FR5.18.25 的完整导入和整批策略合同及相关实现状态，保留原有无关修改。
- `docs/adr/039-terminology-localization-profiles.md`（修改）：更新独立术语权威、兼容与无基础版本契约，技术修订保持提议状态。
- `plans/terminology-localization-profiles/plan.md`（修改）：新增并完成 Story 10；保留旧 Story 的历史事实，记录文件归属、风险和真实验证证据。
- `plans/INDEX.md`（修改）：仅更新本 Epic 的 10/10 状态和本次记录链接，保留其他工作线修改。
- `docs/test-reports/terminology-source-import-2026-11-04.md`（新增）：Astra 独立审核的四项修复、精确命令、环境失败归因、旧实现摘要对照及验证边界。
- 本文件（新增）；`docs/changelogs/INDEX.md`（修改）：归档 Story 10 并最小更新本 Epic 索引，既有增量文件未改写。

## 验证证据

需求、计划、分工、独立审核及 QA 使用 Astra high；扫库、核心和 UI 编码使用 GPT-6.1 Sol high。核心最终聚焦回归 `114 passed`，UI 聚焦回归 `55 passed`。独立 QA 在所有修复后的最终相关回归 `173 passed`；批次存在重叠，不相加。

较早的扩大集成为 `360 passed, 2 failed`，两项在未修改的报告发布 `os.link` 处受到 sandbox 权限限制。最小硬链接复现确认同因；经工具自动审查允许后，两项原始测试单独复验 `2 passed, 6 deselected`。未修改或 mock 掉报告硬链接逻辑，不把首次集成运行写成单次全绿。独立窄窗口验证 `4 passed`。

最终执行并通过：

```powershell
uv --cache-dir D:/MyCode/TransBridge/.tmp-scheme-import-20261104-b39e/uv-cache run --no-sync ruff check src tests
uv --cache-dir D:/MyCode/TransBridge/.tmp-scheme-import-20261104-b39e/uv-cache run --no-sync ruff format --check src tests
git diff --check
```

格式检查覆盖 2367 个文件。全部 pytest 精确命令及每次结果见 [QA 报告](../../../test-reports/terminology-source-import-2026-11-04.md)。使用现有 uv 环境和 `--no-sync`，未修改依赖或锁文件。pytest 有三项既有 SWIG 弃用警告。

## 兼容、职责与遗留

没有数据库 schema 迁移；旧方案摘要与旧孤立映射行为保持。只有新导入的独立词写入可选字段，旧软件不认识此字段时会拒绝读取，降级应先取消选择并使用兼容备份。来源文件不建立持续关联。

生产模块责任复核：最大改动模块 `persistence/terminology/profiles.py` 为 487 行、20 个方法，其余相关模块未超出 500 行/30 方法门槛。本轮未引入新的聚合大类。

未运行全仓 pytest、真实 LLM、真实 ParaTranz、用户游戏资产、人工桌面操作及新的大规模性能基准；已使用真实 SQLite、本地 fixture 和 offscreen Qt 验证相关行为。未解决功能问题：无。任务临时目录在最终收尾时按已记录归属和绝对路径检查清理，命令中的路径仅保留为复现证据。
