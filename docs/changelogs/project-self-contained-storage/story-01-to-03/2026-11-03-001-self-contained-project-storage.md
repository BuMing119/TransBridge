# 项目自包含保存、来源状态升级与便携闭包

- **日期**：2026-11-03。
- **Epic / Stories**：`project-self-contained-storage` / S01～S03。
- **计划**：[项目自包含保存](../../../../plans/project-self-contained-storage/plan.md)。
- **需求与决策**：FR8.3、FR8.4、FR8.8、FR19.7、FR19.10、FR19.11、FR19.14.5；[ADR-051](../../../adr/051-self-contained-project-storage.md)。
- **类型**：持久化语义修复、输入完整性修复、自动升级与归档范围收敛。

## 行为与归属

正常新建来源已有持久 artifact，本次不重复建设存储。正式 Project/Variant 与其引用的来源 artifact 共同拥有原文、译文、Stage、标签、上下文、类型、顺序、输出模板和实际 Strings；原路径仅为参考。来源未捕获或读取时发现缺失的工程仍可保留译文，但保存结果必须明示不完整。旧记录统一经现有升级链转换，不在运行期重新解析外部原路径兜底。

仅记录本任务可靠归属的差异。工作区既有分类上传、文件传输并发、对话排序及其测试/文档不归入本记录；共享 `gui_facade.py` 只记录保存投影一处，`plugin_parser.py` 只记录缺 Strings 检查，不将已存在的 captured-content 解析或对话布局改动计入本次。

## 逐文件变更

### S01：来源规范状态与升级

- 修改 `src/transbridge/persistence/v2/models.py`：当前 schema 提升到 5。
- 修改 `src/transbridge/persistence/v2/schema.py`：来源必需 `content_state`，captured 要求有效 `source_artifact_id`；missing 不含有效引用，规范序列化省略，schema 允许 null。
- 修改 `src/transbridge/persistence/v2/migration.py`：增加 v4→v5 并接入统一链；仅依据已有 artifact 引用补捕获状态，不读外部源、不猜原文。
- 修改 `src/transbridge/application/projects/source_registry.py`：模型验证状态与引用一致，序列化总是输出状态；读取规范结构要求该字段。
- 修改 `src/transbridge/persistence/saved_project_sources.py`：新来源 hydration 缺失返回 `PROJECT_SOURCE_CONTENT_REQUIRED`；保存、读取成果时同步规范状态。
- 修改 `src/transbridge/persistence/source_artifact_codec.py`：内容 ID 编码排除引用与捕获状态，避免引用自身或纯登记状态改变内容身份。
- 修改 `src/transbridge/persistence/project_source_inputs.py`：只按规范捕获状态读取 artifact，删除 baseline loader fallback。
- 修改 `src/transbridge/persistence/current_project.py`、`src/transbridge/bootstrap/persistence.py`：删除上述旧 loader/preparer 死注入。
- 修改 `src/transbridge/persistence/archive_source_inputs.py`：旧包的明确输入在导入边界持久化后设置 captured，运行期不兼容旧输入读取分支。

### S02：保存、导航与 localized 捕获

- 新增 `src/transbridge/application/projects/save_result.py`：统一完整/部分保存回执；从已知 missing inputs 与登记状态判断，不重新读取、hash 或重写 artifact。`translations_were_saved()` 只认可明确的已持久化缺来源回执。
- 修改 `src/transbridge/application/projects/lifecycle.py`：dirty 保存与 clean no-op 共用回执；关闭/切换仅在确实保存译文后允许继续，不把其他 partial 当成功。
- 修改 `src/transbridge/application/projects/catalog_commands.py`：创建空白 Variant 也保留工程缺输入事实，不能借空版本隐藏缺来源。
- 修改 `src/transbridge/application/projects/gui_facade.py`：部分保存有权威结果时刷新投影，及时反映持久化 revision。
- 修改 `src/transbridge/ui/shell/project_save.py`：部分保存不闪“项目已保存”，不因缺来源持续重试；后来新编辑仍保持 dirty。
- 修改 `src/transbridge/ui/workbench/save_presenter.py`、`_project_bar.py`：增加 INCOMPLETE 警告状态，显示“译文已保存，来源待补充”，普通绑定不抹掉该状态。
- 修改 `src/transbridge/ui/coordinators/project_open_operation.py`：显式已保存译文的 partial 可继续打开目标，并呈现来源警告；其他失败继续阻止导航。
- 修改 `src/transbridge/parser/plugin/plugin_with_context.py`：记录首个 unresolved localized ID 的插件、record、field、FormID，并提供明确失败；ID 0 是合法空引用，保留 skip-empty 语义。
- 修改 `src/transbridge/parser/plugin_parser.py`：正式解析要求全部被引用的非零 localized ID 可解析，避免静默跳过条目后发布不完整来源。只需要实际引用的 Strings，不要求无关表齐全。

规模责任审查：`lifecycle.py`（约 1087 行）、`gui_facade.py`（约 544 行）仅修改既有边界接线，新保存结果责任抽到独立 `save_result.py`；`source_registry.py`（约 505 行）只增加同一来源登记职责字段。后续新增生命周期/GUI 责任前须拆分，当前局部修复不扩大其职责。`schema.py` 为数据声明文件，适用数字阈值例外。

### S03：正式资产归档与换源历史依赖

- 修改 `src/transbridge/persistence/project_archive_files.py`：以业务资产白名单替代任意工程目录递归夹带；保留 canonical 术语 DB、备份和 artifacts，排除日志、缓存、运行 staging、AI 任务/会话内容和未知文件。
- 新增 `src/transbridge/persistence/source_update_archive.py`：读取现有换源 manifest 的 base64 previous，校验既有 digest/身份/版本完整性，通过统一 migration+schema 读取历史引用，收集旧 artifact 闭包。
- 修改 `src/transbridge/persistence/project_archive.py`：导出当前及历史所引用 artifact；导入忽略旧包非业务杂项；历史 preimage 原样放入新项目 owned/source-updates，复制和再次导出仍保留证据。
- 修改 `src/transbridge/persistence/source_artifact_archive.py`：包校验覆盖旧来源成果及 bytes，不能借目标机器缓存掩盖包内缺失；仅接受被当前/历史引用的成果成员。

历史备份仅只读保留，不激活旧 journal、不重放源项目事务，不新增全历史恢复 API。其原项目身份继续作为历史证据。

### 回归与文档

- 新增 `tests/persistence/v2/test_project_content_storage.py`：v4 升级、规范字段、拒绝缺 hydration、删原文件后的增量保存、get/put 禁调用、空版本缺来源。
- 新增 `tests/application/projects/test_partial_save_transition.py`、`tests/ui/test_partial_save_open.py`；修改 `tests/ui/test_save_completion_state.py`：安全关闭/切换、明确警告、新编辑保护及非相关 partial 拒绝。
- 修改 `tests/parser/test_localized_capture_boundaries.py`、`test_localized_variant_identity.py`、`test_plugin_parser_failures.py`：缺文件/缺非零 ID 失败、ID 0 与真实空文本、单实际使用表，以及合法插件 fixture。
- 新增 `tests/persistence/v2/test_archive_business_assets.py`、`test_source_update_archive.py`：白名单、旧杂项忽略、换源备份跨存储根/复制/再导出、缺历史依赖拒绝及旧 schema preimage。
- 修改 `tests/persistence/v2/test_project_archive.py`、`test_legacy_project_archive.py`、`test_source_artifact_archive.py`、`test_saved_project_sources.py`、`test_project_provisioning.py`、`test_assistant_session_migration.py`：对齐当前 schema、规范状态及删除旧运行注入后的入口。
- 修改 `tests/persistence/v2/test_task_recovery_archive.py`、`test_legacy_project_task_assets.py`：项目包不再携带 AI 运行恢复内容，仍验证正式项目可用。
- 修改 `tests/persistence/test_editable_missing_sources.py`、`tests/application/projects/test_known_original_consumers.py`、`test_source_supplement.py`、`test_source_update.py` 和 `tests/integration/bootstrap/test_persistence_v2_projection_composition.py`：新来源状态、partial 保存与规范组装回归。
- 新增本 Epic 计划及 ADR-051；直接更新 `docs/requirements.md` 对应原条款、ADR-050 兼容边界，并最小更新 docs/plans/changelog 索引。FR3.4 和分类上传既有差异保持。

## 验证

最终稳定单次联合命令如下（环境：`TEMP/TMP=.tmp-q`、`QT_QPA_PLATFORM=offscreen`、`UV_CACHE_DIR=.tmp-self-contained-qa/uv-cache`）：

```powershell
uv run --no-sync pytest tests/persistence/v2 tests/application/projects tests/persistence/test_project_recovery.py tests/persistence/test_editable_missing_sources.py tests/ui/test_save_completion_state.py tests/ui/test_partial_save_open.py tests/ui/test_project_open_operation.py tests/ui/test_project_open_guards.py tests/ui/test_save_recovery.py tests/ui/test_cancel_application_close.py tests/ui/test_project_source_hydration.py tests/parser/test_localized_capture_boundaries.py tests/parser/test_localized_variant_identity.py tests/parser/test_localized_resource_capture.py tests/parser/test_plugin_scan_logging.py tests/parser/test_plugin_parser.py tests/parser/test_plugin_parser_failures.py tests/parser/test_plugin_parser_captured_content.py tests/contracts/io/test_localized_hydrated_publication.py tests/integration/publish/test_project_hydrated_write.py tests/integration/publish/test_bsa_localized_write.py tests/integration/bootstrap/test_persistence_v2_projection_composition.py -q --basetemp .tmp-q/final
```

结果：**999 passed、1 skipped、7 warnings，158.08 秒，exit 0**。唯一跳过为 `test_directory_discovery_does_not_follow_symlinks`，Windows 创建目录符号链接缺少权限（WinError 1314）；7 个警告为 3 个 SWIG 警告和 4 个 TranslationEntryCollection 兼容 facade 弃用警告。无剩余测试失败或已知审查 finding。

前期聚焦解析、归档、核心与 UI 回归包含在上述扩大选集内，不将不同批次重叠计数相加。首次扩大运行有旧测试合同/fixture 断言失败，修正后完成本次稳定联合重跑；不把旧结果当最终失败，也不把分批复验伪装成单次通过。

`uv run --no-sync ruff check src tests`、`uv run --no-sync ruff format --check src tests` 已通过；格式检查覆盖 2345 个文件。新文档相对链接检查和 `git diff --check` 通过。

## 兼容与未执行边界

schema 5 由既有备份/迁移/原子替换升级；没有成果引用的历史来源保持 missing，不自动搜索任意备份，也不把译文、key 或占位文本造为原文。content_state 是持久捕获事实，不保证保存后磁盘永不损坏；读取仍沿用 artifact 完整性规则。

未运行真实用户 data 迁移、人工桌面操作、真实 LLM 或网络请求；测试使用合成输入和独立存储根。没有新增依赖或修改 lockfile，没有提交 Git。临时验证目录由主会话按本任务归属清理，预先存在的用户目录保留。未提供全历史一键恢复功能，这是明确范围外事项。
