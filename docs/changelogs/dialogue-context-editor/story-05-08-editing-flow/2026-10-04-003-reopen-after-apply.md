# 应用后重开复用已同步工程数据

- 日期：2026-10-04
- Epic / Story：dialogue-context-editor / 08
- 计划：[词条编辑](../../../../plans/dialogue-context-editor/plan.md)
- 问题：单条或同原文批量提交后已经把最新工程快照应用到集合，但通用变更通知清除了编辑器缓存，下一次打开又在 UI 线程执行全量投影。普通词条显示还逐条查询完整导航列表。

## 本轮变更

- `src/transbridge/ui/dialogue/projection_sync.py`：新增 `publish_applied`，在提交方完成权威投影后发送原有通知并保存同步凭据。凭据绑定快照 identity/revision、scope、集合 identity/revision 及通知次数；只有恰好本次一次通知且状态没有改变时才复用，嵌套通知、集合替换、快照变化仍失效。普通 sync 也校验通知期间的状态。
- `src/transbridge/ui/dialogue/editing.py`：`EntryDraft.commit` / `commit_drafts` 增加可选同步发布端口。仅实际成功提交、最新快照已应用到各槽位时使用；失败或缺失快照不生成凭据，未注入端口的旧调用者保持原行为。
- `src/transbridge/ui/dialogue/consistency.py`：同原文批量提交也传递同步端口，保留批量提交后最新凭据。
- `src/transbridge/ui/dialogue/controller.py`：组合发布端口；普通导航只在结构变化时剪除已删除键，避免每次显示全量 collection.get；没有已选任务节点时不遍历任务树。
- 新增 `tests/ui/test_dialogue_reopen.py`：真实 Variant 命令提交后重开、外部权威更新、批量同步后重开、失败提交与草稿恢复四项回归。
- 修改 `tests/ui/test_dialogue_projection_sync.py`：补充提交发布后的缓存复用、嵌套旧式修改通知、通知内集合替换和快照变化保护。
- 修改 `tests/ui/test_dialogue_editing_flow.py`：验证外部删除词条后导航立即剪除旧键，且继续顺序正确。
- 更新当前计划阶段及两个索引的该 Epic 条目。未改动复制粘贴、文件格式、依赖或既有其他工作线。

## 验证

最终以下命令退出 0，146 passed：

```powershell
uv run --no-sync --no-cache pytest tests/ui/test_dialogue_reopen.py tests/ui/test_dialogue_projection_sync.py tests/ui/test_dialogue_consistency.py tests/ui/test_dialogue_authority.py tests/ui/test_dialogue_editing_flow.py tests/ui/test_dialogue_scenes.py tests/ui/test_dialogue_editor.py tests/ui/test_dialogue_input_visuals.py tests/ui/test_dialogue_worker_lifetime.py tests/ui/test_entry_refresh.py tests/ui/test_translation_table_sorting.py tests/ui/test_step2_incremental_rendering.py tests/application/test_dialogue_index.py tests/application/test_dialogue_loading.py tests/integration/gui/test_app_context_projection.py -q -p no:cacheprovider
uv run --no-sync --no-cache ruff check src tests
uv run --no-sync --no-cache ruff format --check src tests
git -c core.safecrlf=false diff --check -- src/transbridge/ui/dialogue plans/dialogue-context-editor/plan.md tests/ui/test_dialogue_editing_flow.py
```

- Ruff check 通过；format check 1,506 个文件通过；diff 检查通过。
- 未执行全仓库测试、真实桌面输入及真实用户工程性能验收。本次无临时文件、无提交或发布。

## 五次完整应用后重开测量

环境：现有 Python 3.12.12 / Windows / 同一未提交工作区；离屏 QApplication；`tests.conftest.make_test_collection(8291)`，全部改为普通 MGEF 上下文。复用 `tests.ui.test_dialogue_authority.authority` fixture 的真实 VariantAggregate、GuiProjectCommandFacade 和 ProjectionStore，输入不含用户数据。

步骤：PowerShell here-string 管道给 `uv run --no-sync --no-cache python -`；构造父窗口、工作台和控制器，完成初始索引和表格批次，预热打开一次。每轮实际打开、编辑目标译文、调用 apply、确认关闭，待表格批次完成，再以 perf_counter 计时 open_entry，另计一次 processEvents；共五轮。构造数据不计时，不写磁盘工程。

- 修复前 open_entry：192.265 / 158.303 / 784.978 / 692.381 / 533.805 ms，中位数 533.805，范围 158.303–784.978。
- 修复后 open_entry：1.591 / 1.395 / 6.016 / 6.387 / 5.592 ms，中位数 5.592，范围 1.395–6.387。
- 修复后含一次事件处理：2.357 / 2.442 / 8.547 / 8.809 / 8.217 ms。
- 提交自身仍有其他开销，修复前 apply 为 379.546 / 405.010 / 1293.578 / 1370.535 / 1380.418 ms，修复后为 352.964 / 425.955 / 375.259 / 1215.797 / 1469.204 ms。本轮不把提交波动算作稳定收益，也不声称优化完整存储或所有工程操作。
- 该真实提交 fixture 的完整快照与此前只发通知的最小状态合成样本不同，不能直接与此前约 122 ms 中位数混算。单机负载有明显波动，最终主要保证由回归测试验证：本地提交后的下一次打开不再重复全量投影。

## 边界与遗留

本轮授权的应用后重开路径已完成。首次打开、外部权威变更、来源或版本切换仍需同步；它们不能因缓存而略过。无信号且绕过集合修订契约的裸字段写入仍不属于可安全缓存的受支持路径。公开文件格式和现有调用接口保持兼容。
