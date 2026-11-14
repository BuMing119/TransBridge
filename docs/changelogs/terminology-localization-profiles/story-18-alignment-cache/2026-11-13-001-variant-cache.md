# S18：翻译版本隔离的持久定位缓存与字段失效

- 日期：2026-11-13
- 关联：FR5.18.17 / FR5.18.22、ADR-039、terminology-localization-profiles Story 18
- 范围：承接 S17，落实用户确认的缓存隔离、字段变更、清理边界，并验证编辑和复用性能。

## 可见行为

确定且通过校验的位置判断持久保存。缓存按 Project / Variant / EntryKey 隔离，新建、复制、另存版本不继承。相同定位依据可服务不同目标译名；实际替换仍采用当前方案。原文、已提交译文字段、作用域、候选位置/归属和计数必须匹配，缓存协议包含规则版本，不添加内容摘要。

译文字段实际提交变化只废除该版本该条的全部定位判断，不保留历史，不迁移坐标，撤销回相同文字也不能复活旧判断。标签、stage、同值提交和纯方案预览不触发译文失效。共享原文/相关来源字段变化使相关版本缓存失效。编辑提交只通知受影响的词条，磁盘清理在后台；AI 请求、旧 UI 回调和持久回写均受失效代数及输入校验。

关闭 AI 后仍可后台读取已有缓存，不初始化模型；全来源缓存预热供预览/保存/导出复用。失败与不确定判断仅在会话内抑制自动重试。设置菜单新增“清除当前版本定位缓存”，清理使旧请求不能回填，不因清理立即自动请求模型。

## 文件与符号

以下生产路径相对于 `src/transbridge/`，仅记录本轮增量，不将其他未提交工作归入本 Story。

- 新增 `application/terminology_profiles/alignment_cache.py`：`VariantAlignmentCache`，逐条索引、有限记录、串行后台清理、相同输入请求合并、临时失效世代、持久 fence 二次校验和冻结结果；编辑通知不扫描所有缓存。
- 新增 `application/terminology_profiles/alignment_cache_codec.py`：完整规范输入与规则版本、判断编解码；定位依据排除替换目标字符串，保留候选身份/区间/计数。
- 修改 `occurrence_alignment.py`、`profile_alignment.py`：共享位置依据比较、批量缓存查询、无模型预览与缓存结果回放；旧显示快照不绕过当前版本的失效检查。
- 修改 `infra/terminology_occurrence_aligner.py`：定位模型输入不携带替换目标字符串，与缓存判断依据一致。
- 新增 `persistence/project_store/alignment_cache.py`、`alignment_cache_schema.py`：可选完整缓存扩展、独立版本 fence、nonce/generation 防 ABA、确定结果存储、容量淘汰、`valid_entries()` 只读复核；触发器在相关字段真实变化时事务内失效。
- 修改 `project_store/connection.py`：只承认原始 schema 或原始加完整已知扩展，仍拒绝未知结构；修改 `archive.py`：交换副本去掉缓存扩展，源库不变。
- 新增 `persistence/v2/translation_epochs.py`：翻译字段专用运行内世代与订阅。修改 `variant.py` 和 `application/projects/lifecycle.py`：提交、fork、确认、成功发布边界接线。未发布候选不通知；同版本已发布 authority 替换保留同一个 live tracker，UI 合并通知也不会漏掉 X→Y→X。驻留数据复用既有比较，分页编辑只读取修改条目。
- 新增 `bootstrap/alignment_cache.py`：Project/Variant runtime registry；修改 `terminology.py`、`terminology_storage.py`：无 DB 查询的路径解析、tracker 绑定、关闭/项目删除前清理排空。
- 修改 `ui/workbench/terminology_preview.py`、`terminology_alignment.py`、`terminology_profile_controller.py`：Qt 线程捕获缓存作用域，worker 执行查询/全来源预热；禁 AI 不请求模型；清理和切换保持冻结快照一致。
- 新增 `ui/shell/alignment_cache_actions.py`，修改 action catalog、menu builder、intent composition 和 `ui/i18n/messages.pot`：当前版本清理入口及启用条件。
- 新增应用层 `test_variant_alignment_cache.py`、`test_alignment_epoch_publication.py`，存储层 `test_alignment_cache.py`、`test_translation_epochs.py`，bootstrap `test_alignment_cache_registry.py`，UI `test_alignment_cache_preview.py`；扩展现有设置菜单测试。
- 同步 requirements、ADR-039、plan 和直接索引。前两份 S17 历史记录保持不变。

## 验证

454 项联合回归通过，另补验成功发布边界 6 项，通过的不同测试共 **460 项**。覆盖：重启/切目标复用、跨版本隔离、修改/撤销、stage/标签/no-op、未发布候选、连续 authority 替换、并发重复输入、源字段变化、清理及迟到结果、损坏记录修复、复制/删除、包导入导出、关闭 AI 后完整缓存预热与既有保存/写出流程。

独立审查发现并修复：外部进程修改后撤销导致存储拒绝但当前返回仍使用旧判断；损坏同键记录不能被有效结果覆盖；连续 authority 发布丢失监听器。性能测量发现并修复逐条淘汰查询缺少 entry 级索引引发的平方级批量写入开销；查询计划回归要求 `(variant_id, entry_id, used, id)` 索引及无临时排序。

使用已有 Python 3.12.12 `.venv`，TEMP/TMP 和 pytest basetemp 指向任务独占目录。沙箱默认临时目录权限、既有 FAISS 原生库导入及硬链接限制导致部分尝试失败；获自动批准后用同一环境和隔离合成项目完成复验。没有真实模型调用或用户项目写入。

```powershell
.venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp .tmp-term-cache-qa/combined tests/application/terminology_profiles tests/application/projects/test_profile_variant_commands.py tests/application/projects/test_durable_variant.py tests/persistence/project_store tests/persistence/v2/test_variant_materialization.py tests/persistence/v2/test_translation_epochs.py tests/infra/test_terminology_occurrence_aligner.py tests/bootstrap/test_terminology_alignment_wiring.py tests/bootstrap/test_alignment_cache_registry.py tests/ui/workbench/test_alignment_cache_preview.py tests/ui/workbench/test_terminology_alignment.py tests/ui/workbench/test_alignment_consent.py tests/ui/workbench/test_terminology_preview_async.py tests/ui/workbench/test_terminology_profile_switching.py tests/ui/workbench/test_terminology_projection_consistency.py tests/ui/workbench/test_profile_variant_save.py tests/ui/operations/test_write_schemes.py tests/ui/operations/test_scheme_comparison.py tests/contracts/io/test_terminology_profile_write.py tests/ui/test_ai_task_shutdown.py tests/ui/test_worker_shutdown_registry.py tests/ui/test_runtime_task_shutdown.py tests/ui/test_project_creation_reuse.py tests/ui/test_alignment_settings_menu.py tests/ui/test_main_window_shell.py tests/ui/test_intent_router.py tests/ui/test_command_palette_help.py tests/ui/foundation/test_locale_service.py::test_source_template_covers_menu_catalog_and_critical_settings_msgids -q
.venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp .tmp-term-cache-qa/publication-final tests/application/projects/test_alignment_epoch_publication.py -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
git -c core.safecrlf=false diff --check
```

全仓 Ruff、2,644 文件格式检查与 diff 检查通过。临时测试目录在交付前清理。

## 性能证据

5 次独立进程、合成原文与译文、不调用网络。100,000 条场景中：

- 实际 tracker 单条失效通知中位 **0.0512 ms**，范围 0.0485–0.0545 ms；999 条批量通知中位 2.991 ms。
- 后台单条磁盘清理中位 **4.36 ms**，范围 4.14–4.96 ms；清理 1,000 条中位 22.25 ms。单条成本未随总缓存条目数线性增长。
- 100 个疑点的热缓存查询中位 **7.94 ms**，范围 7.15–8.84 ms；切换目标 7.84 ms，重新创建会话后读取 8.44 ms。每轮都断言修改一条后只重新请求一条。
- 10,000 个疑点的后台热查询中位 **448.13 ms**，范围 410.44–626.04 ms，额外分配峰值 24.74 MiB；冻结 10,000 条结果中位 3.97 ms。
- 同一合成项目的单条译文提交：含缓存触发器中位 5.90 ms，未安装缓存对照 6.07 ms，测量范围重叠，不能声称性能提升。

旧历史合并在 100,000 条缓存时中位 320.98 ms，说明不适合放进每次编辑通知；与新通知属于不同操作，不据此计算加速倍数。100,000 记录初始压力填充显式提高容量，修复后 capture/publish 中位约 1.85/1.76 s；产品默认每版本最多 10,000 条记录、每词条最多 16 份候选依据。首次填充、批量查询、序列化和模型时间不计入编辑通知数字。

可复现脚本、原始 JSON、测量模块快照及环境/盲区保存在本次会话附件目录 `C:/Users/admin/.codex/visualizations/2026/11/13/01a1d4b7-7dfd-7bc2-8bf0-532b375733f8/term-cache-perf/`，入口 `README.md`。它们是 API/SQLite 微基准，不是 Qt 端到端交互、真实项目性能或模型精度保证；SQL 基准未包含正式项目路径跨进程租约开销。未写入长期 `docs/performance` 基线。

## 兼容与未验证项

首次使用持久缓存会安装完整可选扩展。新版接受原始项目与扩展项目；旧程序的严格 schema 校验无法直接打开带扩展的原生数据库。导出包/导入副本会剥离扩展，保持原交换格式与空缓存，不改源库。无新增依赖或锁文件修改。

未运行全仓 pytest、原生界面端到端性能验收、真实模型精度验证或安装打包。模型合法返回仍不等于语义必然正确；继续保留原有不确定诊断与保存约束。
