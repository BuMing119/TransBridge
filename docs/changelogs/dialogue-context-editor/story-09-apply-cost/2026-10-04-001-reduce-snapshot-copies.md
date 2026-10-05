# 应用译文减少整份快照复制

- 日期：2026-10-04
- Epic / Story：dialogue-context-editor / 09
- 计划：[词条编辑](../../../../plans/dialogue-context-editor/plan.md)
- 用户批准范围：同步应用流程内减少重复处理；不采用此前后台准备/提交方案，不新增线程、队列或修订协议。

## 本轮差异

- 修改 `application/projections/models.py`：冻结数组支持只读遍历和长度读取；原展开函数作为 `copy_projection_value` 供选择性复制使用，保留 JSON 序列化输出与不可变数据。
- 修改 `ui/entry_projection.py`：直接读取 snapshot.values；按本次草稿完整身份筛选查找项。引用和 provenance 的嵌套 JSON 单独展开，保留原有领域对象内容。提交后复用此前已有的一次索引及未变化词条复用机制。
- 修改 `ui/dialogue/editing.py`：校验前仅选择草稿目标；既有工程命令、期望修订、失败保留草稿与提交后刷新顺序不变。
- 修改 `ui/context.py`：读取不可变字段，只复制小型目录、标签库和绑定元数据；内部精确标签键改为来源/词条二元组，公开接口不变。
- 新增 `ui/project_labels.py`：提取标签读取责任，保留未变化标签集合，避免身份 JSON 序列化；保留来源隔离、删除和重复标签语义。超限 AppContext 缩减原方法，未增加职责。
- 新增 `tests/ui/test_project_labels.py`；修改 `tests/ui/test_dialogue_consistency.py`、`tests/integration/gui/test_app_context_projection.py`、`tests/application/projections/test_projection_store_contract.py`：覆盖禁止整份复制、嵌套引用兼容、标签来源隔离/复用/删除/重复及选择性复制的防御性。
- 当前 plan 用获批局部方案替换未实施的 S09–10 异步草案；仅更新两个索引的本 Epic 行。未修改其他既有工作线、工程格式或依赖。

## 验证

聚焦 41 项通过，扩大相关回归 156 项通过（21 条既有弃用警告）；下列命令退出 0：

```powershell
uv run --no-sync --no-cache pytest tests/ui/test_dialogue_reopen.py tests/ui/test_dialogue_projection_sync.py tests/ui/test_dialogue_consistency.py tests/ui/test_dialogue_authority.py tests/ui/test_dialogue_editing_flow.py tests/ui/test_dialogue_scenes.py tests/ui/test_dialogue_editor.py tests/ui/test_dialogue_input_visuals.py tests/ui/test_dialogue_worker_lifetime.py tests/ui/test_entry_refresh.py tests/ui/test_translation_table_sorting.py tests/ui/test_step2_incremental_rendering.py tests/ui/test_project_labels.py tests/application/test_dialogue_index.py tests/application/test_dialogue_loading.py tests/integration/gui/test_app_context_projection.py tests/application/projections -q -p no:cacheprovider
uv run --no-sync --no-cache ruff check src tests --no-cache
uv run --no-sync --no-cache ruff format --check src tests --no-cache
git diff --check
```

Ruff format 检查 1,508 个文件通过。未执行全仓库 pytest、真实桌面或用户工程验收；无临时文件、提交、打包或发布。

## 前后测量

Windows / Python 3.12.12，现有 uv 环境，同一未提交工作区；离屏 QApplication。输入 make_test_collection(8291)，context 统一 MGEF:FULL，使用 test_dialogue_authority.authority 的真实 VariantAggregate / GuiProjectCommandFacade / ProjectionStore，另含一个跨来源隔离条目。构造 Step2PreviewWidget 与 DialogueEditorController，初始索引与表格批次完成后预热打开一次。

命令为 PowerShell here-string 管道到 `uv run --no-sync --no-cache python -`。每轮打开相同目标、写入不同 Profile apply n，用 perf_counter 包住完整 controller.apply()；确认窗口关闭和集合值正确，等待索引和表格批次完成后进入下一轮。前后各十次；第十一次独立使用 tracemalloc，避免污染计时。

- 修改前（ms）：351.563, 394.277, 350.065, 367.679, 413.882, 378.434, 377.066, 408.060, 372.332, 356.471。
- 修改后（ms）：220.573, 192.303, 186.093, 227.713, 190.947, 236.990, 198.719, 194.910, 183.832, 231.715。
- 中位数：374.699 → 196.815 ms，减少约 47.5%；范围 350.065–413.882 → 183.832–236.990 ms。
- 独立一次 apply 的峰值 Python 分配：26.343 → 14.294 MiB，减少约 45.7%，不是总进程驻留内存。

边界：内存测试工程，无磁盘自动保存、真实插件加载或人工确认等待；包含命令与同步界面刷新，不含返回后的最终屏幕绘制。不能推断用户工程绝对耗时。底层工程提交仍同步处理全量状态，不宣称卡顿完全消失。无功能未完成项。
