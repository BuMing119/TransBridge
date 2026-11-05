# 实际译名方案关联、全工作台预览与独立版本保存

- 日期：2026-11-05
- Epic：terminology-localization-profiles
- Story：11～13
- 对应需求：FR5.18
- 对应决策：ADR-039、ADR-035
- 状态：本地实现与相关自动化 QA 完成

## 用户可见变化

当前 Variant 的唯一方案关联表示实际保存正文采用的固定修订。切换 B 只临时预览完整工作台，不改 A、不自动创建版本；重复术语全部转换，但只有词条原文命中同作用域术语时才转换中文译文。预览 B 点击保存默认另存独立 B，已有 B 时明确选择新建或覆盖；B 可独立编辑与 AI，A/B 不自动同步。只维护 A、预览 B 并直接文件导出的方式继续保留。AI 始终使用当前实际 Variant 方案。

## 本轮文件变更

以下清单与本轮开始的 manifest/baseline 比较，不把原有来源导入、ParaTranz、依赖和其他工作线变化归入本次。括号内为操作类型。

### 转换与方案权威

- `application/terminology_profiles/transition_projection.py`（新增）：实际 A→B 与明确基础→B 的同 logical key 交集转换、scope 遮蔽、固定 revision 相同判断。
- `application/terminology_profiles/entry_projection.py`（修改）：接收实际 source_profile；基础来源使用当前冻结术语；正常转换缺匹配静默跳过，空译文保持；历史读取兼容保留。
- `application/terminology_profiles/projection.py`（修改）：有效译名重复全部替换、忽略无变化映射，仍从原输入一次应用避免级联。
- `application/terminology_profiles/in_memory.py`、`service.py`、`publication.py`（修改）：发布不推动实际关联，归档保留关联与固定修订，发布并预览不写正文身份。
- `application/terminology_profiles/effective.py`、`browse_queries.py`（修改）：实际方案在新 B 尚无独立基础版本时可提供完整有效映射，保留明确抑制和缺失隔离；AI/浏览一致。新冻结使用 `profiled:v2:` 与 v2 摘要域，恢复旧 v1 保留旧术语权威与摘要。
- `persistence/terminology/profiles.py`、`profile_reads.py`（修改）：SQLite 关联固定 revision、归档仍读历史，保留唯一关联存储。
- `persistence/terminology/schema.py`、`migration.py`（修改）：schema 5，在摘要备份与事务内清除 schema<5 的旧预览偏好，正文和已发布方案不改写。
- `bootstrap/terminology.py`、`terminology_storage.py`（修改）：实际方案有效读取与窄存储边界接线。

### 保存与恢复

- `application/projects/profile_variant_commands.py`（新增）：完整来源正文转换、新建/显式覆盖、源/目标修订校验和成功激活；不以当前可见页为保存输入。
- `application/projects/source_terminology_scope.py`（新增）、`prepared_source_results.py`、`persistence/saved_project_sources.py`（修改）：在 canonical 来源物化与重开边界保留明确 parser 插件 scope，修复真实插件词条错误采用项目级映射。
- `application/projects/profile_entry_inputs.py`（新增）：从权威 Variant 和来源快照恢复完整转换输入，保持来源 identity。
- `application/projects/models.py`、`catalog_commands.py`、`gui_facade.py`、`__init__.py`（修改）：关联变更提交契约、覆盖候选与窄 facade 委托。
- `persistence/v2/terminology_selection_participant.py`（新增）：唯一 SQLite selection 的预/目标镜像、CAS、prepared 回滚与 committed 恢复参与者。
- `persistence/v2/project_activation_journal.py`、`bootstrap/persistence.py`（修改）：将关联参与者加入现有可恢复 Project/Variant 激活事务；日志是恢复证据，不是第二选择权威。
- `ui/coordinators/profile_variant_save.py`（新增）：手动预览保存、默认另存、明确覆盖/取消、后台读取与保存、原 revision 捕获和成功重载。
- `ui/coordinators/variant_coordinator.py`（修改）：显式保存入口转交预览保存协调器；普通实际正文保存保持原流程。

### 工作台、AI 与文件写出

- `ui/workbench/profile_refresh.py`、`terminology_profile_controller.py`、`terminology_profile_bar.py`（修改）：会话目标与实际方案分离，当前实际旧 revision/归档版本可回到原正文；选择不写库，导出捕获固定投影。
- `ui/workbench/terminology_preview.py`、`step2.py`（修改）：完整条目集合后台转换，source_profile 进入快照与缓存，回实际正文恢复普通编辑。
- `ui/workbench/entry_refresh.py`、`inline_edit.py`、`translation_cell_text.py`（修改）：预览期间编辑保护及说明，实际正文保持正常编辑。
- `ui/tools/ai_translator/naming_scheme_controller.py`（修改）：AI 摘要/刷新依据 applied revision，明确临时 B 预览不改变 A 任务。
- `ui/tools/terminology/schemes_view.py`（修改）：预览选择显示与实际方案区分。
- `ui/tools/terminology_profiles/manager_dialog.py`、`manager_workflows.py`、`manager_layout.py`、`publication_dialog.py`、`edit_prompts.py`（修改）：发布并预览 signal、实际关联保留和归档/重命名语义；不变更来源导入算法。
- `application/terminology_profiles/write_projection.py`（修改）：默认输出实际正文，显式临时目标捕获来源/目标组合；不借写 selection 传递预览。
- `application/io/operation_write.py`、`ui/operations/write_feature.py`、`write_plan.py`、`write_batch.py`、`ui/coordinators/operation_coordinator.py`（修改）：固定临时投影贯穿导出请求/摘要，格式 adapter 仍只接收普通条目。

以上 production 路径均相对 `src/transbridge/`。责任审查：新增保存与恢复职责拆入专用模块；超限 facade/step2/bootstrap 只做必要薄接线，schema 数据声明豁免，未扩大成新的全能控制器。

### 测试与文档

- `tests/application/terminology_profiles/test_entry_projection.py`、`test_profile_projection.py`（修改）：增加实际/基础来源、重复、原文门控、scope、非级联、无变化/空译文与同 ID 新 revision 回归。
- 同目录 `test_profile_service.py`、`test_reviewed_publication.py`、`test_profile_effective.py`、`test_profile_browsing.py`、`test_complete_source_import.py`、`test_write_projection.py`（修改）：实际关联固定、历史读取、无底板 AI 与默认导出正文合同。
- `tests/persistence/terminology/test_profile_selection_migration.py`（新增）；同目录 `test_profiles.py`、`test_profile_archive_review.py`、`test_browse_read_cache.py`、`test_storage.py`、`test_sync_state.py`（修改）：schema 5、一次语义迁移/备份/回滚、新关联重开保留，发布和归档不改变正文方案。
- `tests/ui/workbench/test_profile_variant_save.py`（新增）；同目录 `test_terminology_profile_switching.py`、`test_profile_refresh_async.py`、`test_terminology_preview_async.py`、`test_terminology_projection_consistency.py`（修改）：新建/覆盖/取消、完整300条多来源筛选预览、回A可编辑、AI实际方案、后台generation和导出一致性。
- `tests/ui/tools/test_profile_manager_workflows.py`、`test_terminology_profile_edit_protection.py`（修改）：发布只请求预览、归档仍保存实际关联。
- `tests/ai_translator/test_project_terminology_runtime.py`（修改）：真实生产 composition 的无底板实际 B gate/freeze/load 回归。
- `tests/contracts/io/test_terminology_profile_write.py`（修改）：显式临时目标进入写出冻结，不修改持久关联。
- `tests/application/projects/test_profile_variant_commands.py`、`tests/persistence/v2/test_profile_selection_activation.py`（新增）：保存与可恢复参与者的独立 QA，最终结果见专项报告。
- `docs/requirements.md`、`docs/adr/039-terminology-localization-profiles.md`（修改）：原位更新当前语义，移除与新需求冲突的旧持久预览/唯一位置/禁止独立正文约束。
- `plans/terminology-localization-profiles/plan.md`、`plans/INDEX.md`（修改）：保留已完成历史 Story，增加本轮三 Story 与验收、文件归属和责任审查。
- `docs/test-reports/variant-scheme-preview-save-2026-11-05.md`（新增）：精确执行命令、发现和修复、过程失败与验证边界。
- 本文件（新增），`docs/changelogs/INDEX.md`（修改）：只增加本轮索引，不改既有日志。

## 验证证据

最终命令与结果以[专项 QA](../../../test-reports/variant-scheme-preview-save-2026-11-05.md)为准。最终核心/术语持久化/IO合同 245 项通过；UI 113 项通过；AI runtime/adapter/UI 32 项通过。保存/恢复扩大批次 134 项通过、2 项系统临时目录权限失败，任务专有 TMP/TEMP 下相关 6 项全部通过；39 项新增保存/恢复测试全部通过。不把重叠批次数量累加，不将重跑误记为单次全绿。全库 Ruff/格式（2381 文件）与 diff 检查由主会话最终复核通过，详见 QA。

## 兼容、范围与遗留

旧 schema selection 只代表预览偏好，升级备份后重置；不能据此冒称旧正文的方案。正文、方案内容及历史快照保留，无关联版本继续使用基础术语。新实际关联不随发布推进，归档仍可读；回退需配套数据库与 Project/Variant 备份，不能只退代码。

未操作真实 LLM、ParaTranz 或用户游戏资产，未构建安装包。导入和上传本轮排除。保留原有未提交改动，临时目录由主会话结束前核验归属并清理。本轮未发现尚未解决问题；人工桌面、真实网络与安装包验证未执行。
