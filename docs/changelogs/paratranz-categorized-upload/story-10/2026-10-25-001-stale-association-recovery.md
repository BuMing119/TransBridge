# 分类上传失效关联自动恢复

- 日期：2026-10-25
- Epic / Story：`paratranz-categorized-upload` / S10
- 计划：[plan](../../../../plans/paratranz-categorized-upload/plan.md)
- 需求：[FR3.4.4](../../../requirements.md)
- 决策：[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)
- 使用说明：[分类上传](../../../dev/paratranz-categorized-upload.md)

## 问题与结果

本地日志 `20261025_014357.log` 的文件列表响应为普通空数组，完整快照为 0 文件、0 词条。两个本地旧文件关联分别涉及 7 条和 34 条，对照却将它们列为“已关联的远端文件缺失或 ID 重复”，汇总为新建 20121、待处理 41。原因是规划器把不存在的旧 ID 和重复身份合并处理；用户没有需要手工解决的真实云端冲突，界面要求“重新确认目标”也无法完成恢复。

现在文件 ID 不存在时按当前分类完整路径重新定位；词条 ID 不存在时回到所属文件内的 key、原文及必要上下文匹配。存在唯一对象则复用，无对象且无归属/内容歧义则新增。仍有效的 ID 优先，重复身份、同 key 不同原文及其他来源占用仍保留保护。对照显示自动恢复说明，确认执行并核实后才保存新的关联；不清空未选来源的引用，也不修改译文和状态。

此规则适用于空项目、部分删除和分次恢复多个插件，不依赖“项目必须一直为空”。重新对照已成功文件或上次本地保存失败的云端结果，不会重复上传。无需用户手工删除工程内的关联，也无需开启整理旧云端词条。

恢复所依据的快照必须完整。网关校验文件列表及逐文件词条分页：明确 null、错误页码、数量矛盾、多页文件列表和提前结束都不能产生“远端已不存在”的结论；保留普通数组和一致完整分页。独立审查发现词条分页 `rowCount=0、pageCount=2、results=[]` 曾被当作完整空内容，本轮同时修复并复验。

## 逐文件变化

开始时已有大量未提交改动及未跟踪分类上传文件。以下修改以本轮启动副本为基线归属，保留既有工作，不把整个 Git 差异算作本轮新增。

- **修改 `src/transbridge/application/categorized_upload/planner.py`**：文件 ID/词条 ID 缺失回落现有匹配规则；分离自动恢复说明与阻塞原因，重复身份提示不再混入“缺失”；原有效 ID、归属检查、确认指纹和并发校验保留。
- **修改 `src/transbridge/application/categorized_upload/models.py`**：明确 `CategorizedUploadPort.snapshot` 必须完整返回项目，否则抛错；缺失身份会被规划器作为失效关联处理。签名和数据格式不变。
- **修改 `src/transbridge/paratranz/categorized_upload.py`**：新增私有 `_complete_page` / `_complete_files`，复用现有分页状态并验证声明一致性；跨页省略元数据时保留此前声明的总量约束。公共 `pagination.py`、HTTP 端点和写入重试策略不变。
- **修改 `tests/application/categorized_upload/test_planning.py`**：替换原来将缺失文件固定为冲突的断言，要求只读对照后可创建且说明自动恢复。
- **新增 `tests/application/categorized_upload/test_recovery.py`**：11 项回归覆盖 7+34 条旧引用、路径复用、现有文件新增、原文歧义、未归属旧 key、有效 ID 优先、未选来源占用、关联保存失败再对照，以及重复 ID 保护。
- **修改 `tests/paratranz/test_categorized_upload_gateway.py`**：新增 45 个参数化用例，覆盖文件列表和词条页的完整性/一致性、合法空页与合法元数据省略。
- **新增 `tests/ui/operations/test_categorized_upload_recovery.py`**：3 项真实工程服务回归，使用单调递增远端身份模拟清空云端；验证新关联落盘、未选插件译文/状态/引用保真、分两次恢复和后续执行无重复写入。
- **修改 `docs/requirements.md`、`docs/adr/047-opt-in-categorized-project-upload.md`、`docs/dev/paratranz-categorized-upload.md`**：补充 FR3.4.4、自动恢复及完整快照契约，移除“失效引用一律待处理”的旧决策，更新排查和验证说明。
- **修改 `plans/paratranz-categorized-upload/plan.md`、`docs/INDEX.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`**：记录 S10 验收和直接相关索引；新增本记录，既有历史增量不改写。

生产改动仅涉及规划器、端口说明和分类网关，均少于 500 行；没有增加公共 API、UI 控件或新的持久化机制。

## 验证

使用仓库 uv 环境/锁文件，`UV_CACHE_DIR=.tmp-categorized-recovery/uv-cache`、`TMP/TEMP=.tmp-categorized-recovery`（执行时为仓库内绝对路径），`PYTHONIOENCODING=utf-8`、`QT_QPA_PLATFORM=offscreen`。

先写回归验证旧实现：核心恢复与规划测试 8 失败/19 通过；3 个工程恢复回归全部失败；文件列表新增 30 例中 24 失败/6 通过；词条分页新增 15 例中 10 失败/5 通过。修复后聚焦通过，独立审查另验证跨来源保护、有效 ID 优先和保存失败恢复；这些集合有重叠，不相加。

最终联合回归 **390 passed、3 warnings，9.33 秒，退出码 0**：

```powershell
uv run pytest tests/application/categorized_upload tests/application/projects/test_categorized_upload.py tests/paratranz/test_categorized_upload_gateway.py tests/paratranz/test_categorized_file_import.py tests/paratranz/test_categorized_snapshot.py tests/contracts/paratranz/test_categorized_unbound_source_download.py tests/ui/operations/test_categorized_upload_bridge.py tests/ui/operations/test_categorized_upload_checkpoints.py tests/ui/operations/test_categorized_upload_recovery.py tests/ui/operations/test_categorized_upload_feedback.py tests/ui/operations/test_categorized_upload_dialog.py tests/ui/operations/test_upload_mode_routing.py tests/ui/operations/test_paratranz_reference_metadata.py tests/ui/test_logging_setup.py tests/contracts/paratranz/test_sync_execution_logging.py tests/contracts/paratranz/test_sync_diagnostics_logging.py tests/ui/operations/test_paratranz_upload_roundtrip.py tests/contracts/paratranz/test_upload_resolution.py tests/paratranz/test_key_roundtrip.py -q --tb=short --show-capture=no
```

3 条警告为 SWIG 类型缺少 `__module__` 的 DeprecationWarning。一轮中间运行在导入 `faiss/swigfaiss` 时输出 Windows `0xc0000008`，随后在 UI 测试阶段退出 1；未修改代码或依赖，原命令单独复跑得到上述 390 项通过，原生异常未复现，其独立原因未定位。该中间运行不计为通过。

全库静态检查和格式检查通过（2142 文件，退出码 0）：

```powershell
uv run ruff check src tests
uv run ruff format --check src tests
```

## 兼容性与边界

工程 schema、普通上传、分类选择和批次保存接口不变，不需要迁移或手工清空已有本地数据。对照仍为只读，正常确认上传才写入。没有真实云端请求或写入，未运行全库 pytest、构建或重复 S09 性能测量；这些不属于本次恢复逻辑的受控验证。真实账号的服务端导入、移动和历史保留验收仍待执行。
