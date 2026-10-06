# 批量译文导入与轻量隔离快照

- 日期：2026-10-06
- Epic / Story：maintenance / translation-import
- 范围：在本会话已完成的导入安全修复基础上，减少快照复制和 XML/Strings 批量导入开销；不重复归属先前修复或用户既有修改。

## 文件变化

- 新增 [migration_snapshot.py](../../../../src/transbridge/application/io/migration_snapshot.py)：`detached_entries()` 复制可变词条外壳，只共享已知不可变的身份、修订和元数据；可变嵌套 metadata、external_refs、provenance 继续深拷贝，保留全部插件字段。
- 修改 [legacy_migration.py](../../../../src/transbridge/application/io/legacy_migration.py)：使用同一隔离快照方法创建草稿，公共准备函数即使没有来源也保留独立词条所有权。
- 修改 [migration_coordinator.py](../../../../src/transbridge/ui/coordinators/migration_coordinator.py)：捕获目标时使用轻量隔离快照；没有 XML/Strings 来源时跳过第二次集合复制和建索引。保留版本、集合、内容及路径变化校验。
- 新增 [translation_import_matching.py](../../../../src/transbridge/converter/translation_import_matching.py)：提取 EET、XT、Strings 的匹配规则，保持精确匹配与回退顺序、首条有效来源、空译文、覆盖和计数语义。
- 修改 [translation_entry_collection.py](../../../../src/transbridge/converter/translation_entry_collection.py)：上述三个入口锁内批量准备替换，只复制一次集合并重建一次外部引用索引；保留逐词条 revision、集合更新次数和元数据。匹配异常及全局引用冲突在发布前拒绝整批结果。
- 新增 [test_migration_snapshot.py](../../../../tests/contracts/io/test_migration_snapshot.py)：5 项词条字段完整性、嵌套可变数据隔离、过期目标检测与空来源草稿所有权回归。
- 新增 [test_bulk_translation_import.py](../../../../tests/converter/test_bulk_translation_import.py)：13 项匹配兼容、回滚、引用冲突与 2,001 条跨命名空间批量更新回归。

没有删除或移动文件。无数据格式或存储迁移。批内后续异常改为不发布任何部分结果；外部 API 不变。集合类仍为历史超阈值模块，本轮提取完整匹配职责、缩减文件，新增原子替换属于原有集合提交职责；后续增加职责前仍应拆分存储与导出边界。将来新增可变词条字段时，必须同步扩展快照隔离规则。

## 实际验证

快照与迁移聚焦验证：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
uv run --no-cache --no-sync pytest tests/contracts/io/test_migration_snapshot.py tests/ui/test_legacy_migration_safety.py tests/ui/test_workbench_translation_import.py tests/ui/test_structured_migration_import.py -q -p no:cacheprovider --basetemp .tmp-import-opt-01a10fab/snapshot-tests
```

结果：29 passed。批量匹配验证：

```powershell
uv run --no-cache --no-sync pytest tests/converter/test_bulk_translation_import.py tests/converter/tests_translation_entry_collection.py tests/contracts/io/test_migration_import.py tests/ui/test_legacy_migration_safety.py -q --basetemp .tmp-import-opt-01a10fab/bulk/regression
```

结果：56 passed。另将冻结的优化前集合实现与新实现进行 240 个确定性随机差分，返回计数、完整词条、词条/集合修订及外部引用索引一致。独立只读审查未发现本轮优化新增的正确性缺陷。

扩大验证：

```powershell
uv run --no-cache --no-sync pytest tests/ui tests/contracts/io tests/converter -m 'not integration' -q -p no:cacheprovider --basetemp .tmp-import-opt-01a10fab/full-regression
uv run --no-cache --no-sync pytest tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py -q -p no:cacheprovider --basetemp .tmp-import-opt-01a10fab/assistant-recheck --disable-warnings
uv run --no-cache --no-sync ruff check src tests
uv run --no-cache --no-sync ruff format --check src tests
git diff --check
```

综合结果：1585 passed、1 skipped、1 failed。失败为助手确认持久化失败测试的 lease 断言。模块复跑 42 passed、2 failed，原失败项通过，但批准/忽略确认操作两项失败。不得把本轮综合检查记为全绿。Ruff、格式检查（1525 个文件）和 diff 空白检查通过。

## 已确认的独立遗留问题

助手确认卡片出现后，`RequestBinding.release()` 在后台释放 execution lease，却立即清空 admission。快速确认可能在旧 lease 仍被占用时遭拒；卡片已禁用并消费点击，导致操作丢失。该链路及相关正式测试本轮均未修改，也未调用此次导入优化路径。

使用临时测试受控阻塞“确认记录已保存后的 execution lease 释放”，分别验证批准、忽略、持久化失败三种操作。当前实现和冻结优化前模块各 3 项证明测试通过（通过表示复现该缺陷）：均确认点击被消费、业务未执行、确认记录保留；放行后 lease 可申请，但点击不会自动恢复。优化前后证据一致，故本轮没有通过调整测试等待或改动助手业务来掩盖该问题。助手确认竞态仍待单独修复。

## 性能验证边界

Windows / Python 3.12.12 / 仓库 uv 环境，同一合成输入、每种情况与版本 5 个独立进程。基线来自本轮优化前的工作区冻结副本，包含先前安全修复；测量实际迁移协调器的捕获、后台准备及非权威发布检查，包含 ESP 2,340/10,000 条、Strings 2,340/10,000 条、无来源 50,000 条复制场景。计时与 Windows peak working set 增量结果已在会话报告；没有建立持久性能基线或写入 docs/performance。

不包含真实 Qt 调度、工程权威提交落盘及表格重绘；不是朋友原始 ESP 的实物测量。未运行全仓测试、联网/LLM 测试或安装包构建。所有本轮基准输入、冻结副本和临时测试均集中在任务专属 `.tmp-import-opt-01a10fab`，收尾清理，不纳入交付。
