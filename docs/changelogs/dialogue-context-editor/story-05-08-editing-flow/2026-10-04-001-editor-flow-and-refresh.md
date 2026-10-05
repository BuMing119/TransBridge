# 词条应用返回、同原文同步与局部刷新

- 日期：2026-10-04
- Epic：dialogue-context-editor；Story：05–08
- 计划：[词条编辑](../../../../plans/dialogue-context-editor/plan.md)
- 范围：用户授权优化编辑闭环、导航、布局及性能；明确排除复制粘贴改动，不新增相关菜单、按钮、标题或快捷键。

## 用户可见行为

- 普通“应用译文”成功并处理同原文同步后关闭窗口，返回工作台，保留筛选、排序、选中与滚动上下文。
- “应用并下一条”恢复译文焦点；末条显示“应用并返回”。普通词条沿用打开时完整筛选及排序结果，对话仍按任务／场景导航，显示当前位置。
- 单条失败保留草稿；关闭时其他草稿仍需确认，取消关闭不会丢失。关闭许可检查不再提前清除草稿，以免后续主窗口关闭失败。
- 当前内容／版本中原文精确相同、译文不同的条目可勾选同步；空原文／空译文不传播。草稿、锁定、隐藏条目禁选，已检查／审核默认不选。选择一次原子提交，冲突留窗，跳过不撤销当前已应用词条。
- 普通词条收起无效任务区和冗余单行表格；任务上下文恢复时保留本窗口中用户调整的分栏比例。

## 文件与动机

- 修改 `ui/dialogue/controller.py`：串联应用、同步、关闭与继续；冻结普通导航范围；仅结构变化重建索引；统一草稿销毁时机。
- 修改 `ui/dialogue/view.py`：折叠无任务上下文、保存分栏比例、显示导航位置和末条返回动作；原有文本编辑和复制粘贴不变。
- 修改 `ui/dialogue/editing.py`：`EntryDraft.commit` 委托 `commit_drafts`，校验整批快照后提交，保留正式工程和旧集合两种权威写入路径。
- 新增 `ui/dialogue/consistency.py`：精确候选匹配、草稿和状态保护、来源与已应用源词条校验、同步失败反馈。
- 新增 `ui/dialogue/sync_dialog.py`：展示候选位置、旧译文、状态和保护原因，确认选择或跳过。
- 新增 `ui/dialogue/index_state.py`：缓存任务结构签名，译文和审核状态改变不再重建关系索引。
- 新增 `ui/dialogue/projection_sync.py`：按快照 identity/revision、scope、集合 identity/revision 跳过重复投影；集合变更通知使缓存失效。
- 新增 `ui/entry_projection.py`：唯一工程状态映射实现，一次建立索引，多槽位复用；编辑提交保留未变化词条与集合对象。
- 修改 `ui/source_hydration.py`：公共投影函数委托唯一映射，保留原来返回新集合和匹配词条新对象的兼容行为，删除重复字段映射。
- 新增 `ui/workbench/entry_refresh.py`：少量纯译文／状态变化且显示成员与排序未变时局部更新；术语方案、结构变化、筛选或排序改变回退既有刷新；提供完整导航键及保留上下文返回。
- 修改 `ui/workbench/step2.py`：只添加专责模块组合及公开委托接口，避免继续增加超限模块的数据刷新职责。
- 修改 `ui/workbench/table_presenter.py`：允许显示结构不变时替换 RenderSession 数据，后续分批渲染使用新内容。
- 修改 `tests/ui/test_dialogue_editor.py`、`test_dialogue_authority.py`：既有非同步测试默认跳过新同步确认，避免无关模态阻塞；新增同步集成测试显式确认。
- 修改 `tests/ui/test_dialogue_scenes.py`：普通应用验证关闭；继续应用仍验证场景和导航保持。
- 新增 `tests/ui/test_dialogue_editing_flow.py`：应用返回、焦点、末条、关闭草稿、筛选排序导航、紧凑布局、索引复用和同步闭环。
- 新增 `tests/ui/test_dialogue_consistency.py`：同步选择、冲突、版本隔离、权威原子提交、多槽位及共享投影兼容性。
- 新增 `tests/ui/test_dialogue_projection_sync.py`：外部修改、来源版本、集合替换、无 revision 等缓存失效路径。
- 新增 `tests/ui/test_entry_refresh.py`：行对象与选择保持、未渲染批次、筛选排序变化、完整导航及返回。
- 修改计划与两个索引中该 Epic 的对应条目；不记录或改写本轮前已有的 AI 翻译、发布、依赖等未提交内容。

## 验证

最终命令（退出 0，173 passed）：

```powershell
uv run --no-sync --no-cache pytest tests/ui/test_dialogue_editor.py tests/ui/test_dialogue_authority.py tests/ui/test_dialogue_scenes.py tests/ui/test_dialogue_worker_lifetime.py tests/ui/test_dialogue_editing_flow.py tests/ui/test_dialogue_consistency.py tests/ui/test_dialogue_projection_sync.py tests/ui/test_entry_refresh.py tests/ui/test_translation_table_sorting.py tests/ui/test_step2_incremental_rendering.py tests/ui/test_workbench_slices.py tests/ui/test_workbench_theme_migration.py tests/ui/test_main_window_shell.py tests/ui/test_main_window_coordinators.py tests/ui/workbench tests/application/test_dialogue_index.py tests/application/test_dialogue_loading.py tests/integration/gui/test_app_context_projection.py -q -p no:cacheprovider --basetemp .tmp-entry-editor-20261004/final-pytest
uv run --no-sync --no-cache ruff check src tests
uv run --no-sync --no-cache ruff format --check src tests
git diff --check
```

- Ruff 检查通过，格式检查 1,504 文件通过，diff 检查通过。既有 Qt/SWIG 及直接字段修改弃用警告未在本轮扩张处理。
- 共享投影调用方的 source update、localized hydrated publication、safe write back 额外 41 项回归通过。
- 沙箱首次运行主题测试时，5 项因 pytest 临时目录 WinError 5 在准备阶段出错；在本次专用目录中提权复跑通过，最终 173 项也全部通过。
- 离屏渲染了普通词条、任务上下文、同步确认三种布局，检查区块分配和无溢出；离屏环境字体显示为方框，不能据此声称真实桌面字体或主题人工验收完成。

## 局部性能证据

Python 3.12.12 / Windows / 当前已有未提交工作区；通过 PowerShell here-string 经现有 uv Python 的 stdin 执行，固定样本各 5 次，无外部 API。

- 重复投影同步：`make_test_collection` 配合真实 ProjectionSnapshot，比较 `to_dict → apply_variant_projection → tuple equality` 与已预热 `EditorProjectionSync.sync`。
  - 1,000 条原路径：11.076 / 11.146 / 11.524 / 11.542 / 11.498 ms；缓存：0.0062 / 0.0030 / 0.0028 / 0.0033 / 0.0032 ms。
  - 10,000 条原路径：132.539 / 117.071 / 133.210 / 115.900 / 139.394 ms；缓存：0.0050 / 0.0052 / 0.0045 / 0.0046 / 0.0054 ms。
- 工作台单条刷新：1,000 条合成输入，计时 collection_changed 同步处理段；关闭局部路径复用原刷新作为对照。
  - 原路径：9.737 / 9.903 / 9.485 / 9.094 / 8.980 ms，中位数 9.485。
  - 局部路径：1.580 / 1.141 / 1.133 / 2.585 / 1.147 ms，中位数 1.147。
- 不包含完整窗口响应、工程命令、真实插件解析、磁盘或原表后续分批渲染。首次投影仍需处理完整状态；局部刷新仍有 O(N) 比较／筛选，未宣称所有操作常数时间。

## 兼容与遗留

- 无文件格式、工程 schema、依赖或锁文件变更；不需要迁移。不提交 Git、不构建或发布安装包。
- 本轮授权功能无未完成项。未运行全仓库测试或真实桌面人工验收；下一次真实工程使用可进一步确认端到端响应和显示效果。
- 临时截图及 pytest 目录仅用于本次验证，交付前清理。
