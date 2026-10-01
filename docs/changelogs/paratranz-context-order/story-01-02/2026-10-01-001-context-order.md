# ParaTranz 八位上下文序号与回读兼容

- 日期：2026-10-01
- Epic：paratranz-context-order
- Story：01 导出与上传；02 回读与同步
- 计划：[plan](../../../../plans/paratranz-context-order/plan.md)

## 用户可见变化

本地结构化词条导出到 ParaTranz 时，上下文形如 `00000428|INFO:NAM1|00123456`、`00000431|BOOK:FULL`。
序号为八位补零、零基，优先保留已有插件源序号及间隔；key 和本地上下文不变。
读取新文件时拆出展示序号并单独保留，再次导出不会叠加前缀。自由文本上下文不改写。
格式提供文本排序依据，不代表 ParaTranz 网页自动按上下文排序；本次未修改远端项目。

## 逐文件变化

- 新增 `src/transbridge/application/io/paratranz_context_order.py`：严格结构化 context 格式识别、8 位范围检查、幂等格式化与完整批次序号选择；缺失/重复源序号整批确定性回退。
- 修改 `src/transbridge/application/io/paratranz.py`：统一写入边界对本地词条/snapshot 投影序号；原生 ParaTranz DTO 默认保真；支持显式关闭投影与分类传入已计算序号。写前验证超限，不覆盖已有目标。
- 修改 `src/transbridge/converter/translation_entry_collection_export.py`：分类前保留既有任务内恢复顺序，建立全局序号映射，防止逐分类重新编号；上传继续复用此导出路径。
- 修改 `src/transbridge/application/io/paratranz_mapping.py`：DTO 保留 wire context；本地投影归一化 context，将序号保存为 `transbridge.io.paratranz.context_order`，再次映射恢复前缀。
- 修改 `src/transbridge/paratranz/sync_snapshot.py`：归一化用于比较的远端 context，保留独立序号；remote revision 仍基于原始 payload，防止隐藏远端变化。
- 修改 `src/transbridge/application/sync/models.py`：本地/远端 snapshot 与 EntrySummary 增加可选序号；snapshot 验证范围；摘要保留顺序证据，但翻译内容比较不纳入展示序号。
- 修改 `src/transbridge/application/sync/planner.py`：完整 snapshot hash 包含序号，阻止顺序变化后执行过期计划。
- 修改 `src/transbridge/application/sync/executor.py`：上传格式化 context，优先本地序号、其次远端已知序号；下载向本地 snapshot 传递独立顺序；重试摘要校验检测顺序变化。
- 修改 `src/transbridge/ui/operations/production_support.py`：完整集合生成序号；本地替换词条时保留独立元数据，不把前缀写进分类 context。权威工程源结构仍遵守既有只更新译文/状态/引用的约束。
- 修改 `src/transbridge/smart_assistant/tools/tool_paratranz.py`：本地同步 snapshot 在筛选前计算序号。
- 修改 `src/transbridge/smart_assistant/tools/_entry_upload.py`：助手直接上传同样在筛选前计算序号，避免绕过文件导出入口时丢失前缀。
- 新增 `tests/contracts/io/test_paratranz_order_export.py`：7 项导出回归，覆盖稀疏序号、不变性、混合分类、重复/缺失回退、DTO 保真、溢出及幂等。
- 新增 `tests/contracts/io/test_paratranz_order_readback.py`：21 项回读回归，覆盖本地/DTO/snapshot/序列化往返、旧格式与自由文本、远端原始 hash。
- 新增 `tests/contracts/paratranz/test_sync_context_order.py`：7 项同步回归，覆盖创建/更新/远端序号保留、筛选、完整计划及重试过期拒绝。
- 新增 `tests/paratranz/test_entry_upload_order.py`：2 项直接上传回归，覆盖筛选前编号、稀疏插件顺序、自由文本及本地不变性。
- 新增计划并最小更新 `plans/INDEX.md`、`docs/changelogs/INDEX.md`。原有助手 UI/压缩未提交变更未归入本记录。

## 验证

最终聚焦命令（80 passed）：

```powershell
uv run pytest tests/contracts/io/test_paratranz_order_export.py tests/contracts/io/test_paratranz_order_readback.py tests/contracts/paratranz/test_sync_context_order.py tests/contracts/paratranz/test_sync_execution.py tests/contracts/paratranz/test_sync_plan_confirmation.py tests/paratranz/test_entry_upload_order.py -q
```

联合回归（385 passed、1 skipped、1 failed；运行期间新增的最终重试用例已包含于上述收尾复验）：

```powershell
uv run pytest tests/contracts/io tests/contracts/paratranz tests/paratranz tests/converter tests/smart_assistant/tools/test_paratranz_sync_plan.py tests/smart_assistant/tools/test_paratranz_tools.py tests/ui/operations/test_paratranz_download_authority.py tests/ui/operations/test_paratranz_download_recovery.py -q
```

唯一失败为 `tests/paratranz/test_entry_upsert.py::test_assistant_upload_reuses_only_the_target_projects_known_identity[8]`：测试同时期待重复 key 导致零上传和 success=True；原实现对此返回失败。主会话使用 `git show HEAD:src/transbridge/smart_assistant/tools/_entry_upload.py` 在内存替换当前上传函数后运行同一测试，复现相同断言失败，未改工作区或放宽错误语义。

以下检查通过：

```powershell
uv run ruff check src tests
uv run ruff format --check src tests
git diff --check
```

最初 sandbox 下 uv 缓存/pytest 临时目录出现访问拒绝，使用现有 uv 环境提权执行后完成验证。子 Agent 创建的专属 `.tmp-order-*` 目录已清理；未删除来源不明的共享缓存。

## 兼容、责任审查与遗留项

- 无工程 schema 迁移，不重命名 key。既有远端文件不会自动变化，后续原文更新才可刷新上下文；仅导入译文不承担此刷新。
- 仅序号差异不触发翻译同步内容更新，保持 SKIP；新计划和重试检查仍保护顺序证据。
- 源序号沿用零基，缺失/重复数据使用明确回退；非插件不推测任务关系。
- 新格式职责在独立模块，超过阈值的 adapter/executor/工具模块仅接入其现有职责；详见计划责任审查。后续工具扩展前应拆分既有超过 700 行的 tool_paratranz 模块，本次不夹带大规模重构。
- 既有失败测试未修改。未运行全仓库 pytest、真实 ParaTranz 上传或网页排序验收；本次以受影响 I/O/同步/入口回归为准。
