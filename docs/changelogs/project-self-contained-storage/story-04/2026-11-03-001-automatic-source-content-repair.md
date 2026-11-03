# 缺内容项目在既有升级流程中自动恢复

- **日期**：2026-11-03。
- **Epic / Story**：`project-self-contained-storage` / S04。
- **计划**：[项目自包含保存 S04](../../../../plans/project-self-contained-storage/plan.md)。
- **需求与决策**：FR8.4、FR19.7；[ADR-051](../../../adr/051-self-contained-project-storage.md)。
- **类型**：缺来源自动恢复、打开失败语义与回归验证。

## 行为与归属

打开准备发现必需来源内容缺失时，先在既有升级流程内尝试恢复，再决定是否正常激活。schema 4 升级后的缺内容和已有 schema 5 missing 都适用。优先读取保存成果，以真实 Variant 快照补足来源身份/指纹证据，再使用项目所属备份、同存储中有相同指纹的明确登记引用，最后尝试该来源登记的原路径；不全盘搜索、不按文件名猜配。正常完整项目不枚举恢复候选、不读取外部源内容。

恢复复用现有来源补齐匹配、artifact 和来源更新事务，全部正式版本的已存译文、Stage、标签、引用保持；不能匹配的存活条目不会被删除或冒充恢复成功。候选不足则停止正常打开，并保留工程、译文与已有备份，不直接删除。真实快照不会制造模板或 Strings。已知指纹不同的外源拒绝；旧项目缺指纹时，仅在稳定 namespace/明确原路径和完整唯一条目匹配成立时恢复，多个内容版本候选仍拒绝猜测。

本记录仅涵盖 S04。本轮之前的 schema 5、自包含保存、部分保存反馈及归档闭包属于 [S01～S03 记录](../story-01-to-03/2026-11-03-001-self-contained-project-storage.md)，不重复归功。工作区既有分类上传、传输并发、对话排序及其文档/测试也不属于本记录。旧增量文件未改。

## 逐文件变更

- 新增 `src/transbridge/persistence/project_content_candidates.py`：读取固定存储根内的保存成果、真实 Variant 快照、来源备份及明确登记引用；沿用 schema 迁移与现有校验，候选不可用记录原因，取消/内部错误继续向上传递。同次修复缓存候选，多个缺来源只枚举一次。
- 新增 `src/transbridge/persistence/project_content_migration.py`：识别缺内容并匹配恢复候选；补齐当前全部正式版本和折叠译文来源，以一次既有来源更新事务备份并发布。全部内容准备失败时不发布不完整 Project/Variant，返回 `PROJECT_CONTENT_REPAIR_FAILED` 和缺失来源/候选原因。恢复时保留当前译文状态；快照仅提供真实身份/指纹证据。对 `skip_empty` 旧选项允许使用已捕获的完整来源，不扩大到任意格式选项忽略。
- 修改 `src/transbridge/persistence/current_project.py`：在来源读取发现缺内容后调用修复并重新读取保存结果；失败不激活。`open_path` 遇显式 `DirtyDecision.CANCEL` 在打开准备之前返回既有取消结果，避免先修复再取消。
- 修改 `src/transbridge/persistence/project_lifecycle_loader.py`：直接/后台切换入口共用同一修复阶段，最多重试一次；历史快照缺少必要内容时保留证据并拒绝正常加载，不启动循环恢复。
- 修改 `src/transbridge/bootstrap/persistence.py`：共用已保存来源 preparer、artifact store 和来源更新 store，注入打开入口与生命周期 loader。没有另建升级系统；最终移除修复对象未使用的 repository 构造参数。
- 修改 `src/transbridge/application/projects/source_supplement_mapping.py`：增加可选 `additional_namespaces`，供唯一主来源的 `legacy:v1` 条目复用既有唯一匹配规则，不复制另一套匹配器。
- 修改 `src/transbridge/persistence/saved_project_sources.py`：增加恢复专用 `prepare_recovery_source`，只在恢复捕获时允许修复损坏 artifact。
- 修改 `src/transbridge/persistence/source_artifact_store.py`：增加显式 `repair_corrupt` 分支；重建确定的内容寻址 manifest/blob 前先保存损坏原字节到 quarantine，并检查修复期间原记录未改变，再沿用原子发布。普通 put/save 继续拒绝不同内容覆盖，不增加日常全量检查。
- 新增 `tests/persistence/test_project_automatic_repair.py`：覆盖 schema 4/5、保存成果优先、明确路径与备份、真实快照、已知相同/不同指纹、未知指纹唯一匹配、多版本状态、折叠来源、损坏 manifest/blob、失败保留现场、正常项目禁止扫描、取消和 `skip_empty`；80 来源场景验证一次候选扫描、一次事务提交。
- 新增 `tests/persistence/test_project_open_cancellation.py`：验证 dirty 工程显式取消打开不会触发修复发布，原 dirty 译文仍可继续保存。
- 修改 `tests/persistence/test_editable_missing_sources.py`、`test_project_recovery.py`、`test_project_source_identity.py`：对齐“先恢复，不能恢复则拒绝正常激活”，保留多版本状态、未匹配条目、活动状态和备份证据断言。
- 修改 `tests/persistence/v2/test_legacy_project_archive.py`：导入解码仍只读，打开时可恢复明确来源；无法匹配时保留已存条目/历史并拒绝激活，旧来源稀疏快照不被当前补齐覆盖。
- 修改 `tests/application/projects/test_source_supplement.py`：保留已激活工程按需补输入测试，将无法正常重开的 fixture 明确安装为已激活旧视图；未匹配内容重开应失败。
- 修改 `tests/integration/bootstrap/test_persistence_v2_projection_composition.py`：验证直接 facade 切换也先自动恢复，再编辑并完整保存；不把 S01～S03 已有 fixture 调整重复计入本轮。
- 修改 `tests/persistence/v2/test_project_snapshots.py`：使用真实捕获来源 fixture 验证快照保存/恢复/重开，移除依赖仅内存 baseline 的旧假设。
- 修改 `tests/persistence/test_assistant_attachment_cleanup.py`：将遗漏的当前 schema 4 fixture/备份文件名改为 `SCHEMA_VERSION`，恢复旧测试原本要验证的附件清理合同，不改生产清理行为。
- 修改 `docs/requirements.md` 的 FR8.4/FR19.7、ADR-050/051 与本 Epic plan：直接纠正旧的“不尝试备份/缺内容直接正常打开”条款；扩展 S04，保留 S01～S03 阶段历史。最小更新 docs/plans/changelog 索引。

规模责任审查：两个新增生产模块分别约 112/305 行；当前打开、loader、成果存储均低于 500 行。候选发现与恢复发布分开承担责任，入口只做接线，没有向超阈值类新增职责。

## 实际验证

以下由主会话、实现和独立 QA 在隔离目录执行，批次有重叠，不相加为总通过数。

最终广域回归（`QT_QPA_PLATFORM=offscreen`，`TEMP/TMP=D:\MyCode\TransBridge\.tmp-arq`，`UV_CACHE_DIR` 为其 `uv` 子目录）：

```powershell
uv run --no-sync pytest tests/persistence tests/application/projects tests/integration/bootstrap tests/ui/test_project_open_operation.py tests/ui/test_project_source_hydration.py tests/ui/test_partial_save_open.py tests/ui/test_save_completion_state.py -q --basetemp=.tmp-arq/j
```

结果：**1158 passed、4 failed、1 skipped、3 warnings，181.27 秒，exit 1**。该批收集时自动恢复专项为 21 项；之后新增 `skip_empty` 第 22 项，专项单独完成 **22 passed，15.10 秒，exit 0**：

```powershell
uv run --no-sync pytest tests/persistence/test_project_automatic_repair.py -q --basetemp=.tmp-arq/k
```

广域失败与复验：

- `tests/persistence/test_assistant_attachment_cleanup_cli.py::test_cli_preview_then_explicit_offline_cleanup_in_owned_temporary_root`：首次创建会话出现 `PERMISSION_DENIED`；同环境单独复跑 `uv run --no-sync pytest tests/persistence/test_assistant_attachment_cleanup_cli.py -q --basetemp=.tmp-arq/l --tb=long`，**1 passed，1.07 秒**。该错误未在复跑中重现，不将广域原失败抹去。
- `tests/integration/bootstrap/test_composition.py::test_legacy_tool_wrappers_reject_missing_injected_context`：旧断言期望质量报告查询成功，但现有工具返回 `TASK_RESULT_SCOPE_REQUIRED`。主会话核对对应生产和测试文件均无本轮差异。
- `tests/integration/bootstrap/test_terminology_task_wiring.py::test_production_runtime_closes_build_decision_publish_history_restore_and_render_loop[False]` 及 `[True]`：主会话定位到未改动的 `renderers/_artifact.py` 内 `os.link(staging, target)` 返回 Windows `WinError 5`，属于当前环境硬链接权限失败；对应生产和测试文件均无本轮差异。

唯一跳过为 `tests/application/projects/test_source_input_candidates.py` 的目录符号链接场景，系统目录 symlink 权限不可用；3 warnings 为 SWIG 弃用警告。广域回归没有全绿，后三项已知失败未在本任务范围修复。

显式取消守卫和死参数清理完成后，再运行最终聚焦回归（`UV_CACHE_DIR=D:\MyCode\TransBridge\.tmp-ar-active-cache`）：

```powershell
uv run pytest tests/persistence/test_project_open_cancellation.py tests/persistence/test_project_recovery.py tests/persistence/test_project_automatic_repair.py -q --basetemp=.tmp-ar-cancel2 --tb=short
uv run ruff check src tests
uv run ruff format --check src tests
```

结果：**42 passed**；全库 Ruff 通过，格式检查 **2349 files already formatted**。该局部修改后未重复广域选集。此前根会话来源身份/旧包聚焦命令 `uv run --no-sync pytest tests/persistence/test_project_source_identity.py tests/persistence/v2/test_legacy_project_archive.py -q --tb=short` 为 **34 passed**；实现侧补齐/成果存储/来源事务/身份/旧包选集为 **107 passed**。这些是不同批次证据，不与最终回归累加。

## 兼容、限制与未执行项

已确认“废弃”是停止本次正常打开，保留项目和备份，不删除用户数据。成功恢复后内容成为内部正式成果，后续打开不依赖历史候选或原路径。已知不同内容版本不自动当作来源更新；需要用户显式更新来源。

`prepare_path` 是可能持久化修复的打开准备，仅承诺不改变当前 GUI，并非无写盘预览。普通 UI 在调用前已有保存/放弃/取消决策；直接 `open_path` 显式取消也先返回。本轮不另建延后修复或跨调用预览系统。目录只读发现仍不修复、不写盘。

测试仅使用合成输入和隔离存储；未运行真实用户 data 迁移、人工桌面、真实 LLM 或网络验收。没有新增依赖、修改 lockfile 或提交 Git。任务临时目录由创建者核对归属后清理，预先存在的用户目录保留。最终遗留为上述广域既有合同失败、硬链接环境失败和一次未重现权限失败；没有把这些结果写成全部通过。


## 收尾审查与最终复验

主会话最终修正 `project_content_migration.py::_source_identity` 的候选谓词：已知来源指纹时，只接受相同指纹的身份；单来源 fallback 仅在没有已知指纹时使用，避免历史其他版本快照的 namespace 混入并误拒绝恢复。`tests/persistence/test_project_source_identity.py` 新增真实旧快照含不同 namespace/hash 的回归，确认当前已知指纹仍能恢复且译文状态保留。

最后一次聚焦验证使用 `QT_QPA_PLATFORM=offscreen`、`TEMP/TMP=D:\MyCode\TransBridge\.tmp-arf`、`UV_CACHE_DIR` 为该目录的 `uv` 子目录：

```powershell
uv run --no-sync pytest tests/persistence/test_project_open_cancellation.py tests/persistence/test_project_recovery.py tests/persistence/test_project_automatic_repair.py tests/persistence/test_project_source_identity.py -q --tb=short
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
```

结果：**50 passed、3 warnings，25.24 秒**；全库 Ruff 通过，**2349 files already formatted**。本段是最后局部修正后的证据；前述 42 项和广域结果保留为各自批次事实，不相加、不改写为全库通过。最后修改后未再次运行广域选集。

本轮新增/修改的功能文档链接检查通过，目标已跟踪文档的 `git diff --check` 通过，S04 同日编号唯一。全量读取 changelog 索引时另发现一条 HEAD 已有的无关坏链接：`2026-08-18-010-Story确认.md`（实际文件含 `Phase3-` 前缀）；未修改该历史索引条目，也不将整库链接检查表述为全部通过。
