# S28 场景对话按开始阶段排列

2026-11-21 · [S28 实施计划](../../../../plans/paratranz-categorized-upload/plan.md#s28-纯插件对话阅读布局) · [分类上传说明](../../../dev/paratranz-categorized-upload.md)

## 用户可见变化

同一场景内，译者按对话动作的开始阶段读到整个话题。同阶段开始时保留动作在插件中的先后。动作区间重叠时，这一顺序仅表示开始先后。如果有引用话题的动作缺少可靠的阶段区间，整个场景采用原有动作顺序。

## 实现与兼容性

- `parser/plugin/scene_links.py` 在一个 SCEN 内收集对话动作、话题和阶段区间，稳定排序后才对共用话题去重。`dialogue_order_binary.py` 在插件字节及已解析记录两条路径都保留阶段边界 HNAM、开始 SNAM 和结束 ENAM。
- `parser/plugin/dialogue_catalog.py` 与 `dialogue_order_evidence.py` 分别以目录 v2、顺序证据 v3 保存新顺序。`application/io/plugin_dialogue_catalog.py` 与 `plugin_dialogue_order.py` 在旧快照带有插件字节时从字节重新提取。仅保存旧话题列表的工程沿用原先的顺序。
- `persistence/project_store/archive_native.py` 兼容新旧归档。`reading_order_refresh.py` 将仅从旧资料恢复的分页行号标记为 v2。通过工程的更新来源功能选用原来的插件时，可重新生成阶段顺序与分页行号。
- FR3.4.7、ADR-047、分类上传开发说明与 S28 计划同步记录同阶段和缺失区间的行为。

## 验证

- 用备份的 Skyrim.esm 和配套英文字符串表的临时副本，按默认选项解析 64,766 条非空词条。解析对象与字节读取所得的 1,706 个 SCEN 结果一致。
- 逐一核对先前报告标出的 191 个场景。在原始记录中，一项动作结束阶段严格小于另一项开始阶段的 13,092 对关系全部顺序正确。分类输出中能看到双方台词的 12,270 对也全部顺序正确。其余 822 对只核对了记录关系。
- 《龙之崛起》抽查的四条 INFO 依次为 `0005C9D7 → 0005C9D5 → 0005C9D6 → 0005C9D4`。《无声之语》墨瑟开锁依次为 `000CED8C → 000CED95 → 000CED8F → 000CED91`。两份任务文件分别有 228 行、103 行，本地分类 JSON 与离线上传载荷内容一致，按 TB2 key 排列一致。
- `tests/parser/test_scene_phase_order.py` 验证同阶段、区间重叠、阶段无效、跨场景隔离及共用话题。`tests/contracts/io/test_dialogue_sequence_integration.py` 以物理顺序逆于阶段顺序的场景验证对话树、筛选、分类 JSON 与上传载荷。
- `tests/parser/test_scene_order_legacy.py`、`tests/persistence/project_store/test_dialogue_catalog.py` 和 `tests/application/projects/test_source_update_plugin.py` 验证旧快照、便携归档、同一原版插件刷新，以及刷新后的译文、状态和阅读顺序。
- 在仓库环境运行 `uv run --offline --no-sync --no-cache pytest`，工程存储、I/O、ParaTranz、分类脚本与相关对话用例共 1,783 项通过。另一次来源更新及解析专项共 43 项通过。两轮有重叠。全仓 `ruff check src tests` 和 `ruff format --check src tests` 均通过。验证均在本地完成。
