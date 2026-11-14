# S17：冲突审核主从窗口

- 日期：2026-11-13
- 关联：[计划 S17](../../../../plans/project-terminology-build-versioning-reporting/plan.md)
- 来源：用户确认交互 demo 后要求按 bm-pilot 落地。仅记录本轮 UI 重构与必要应用接口，不包含工作区其他未提交修改。

## 用户可见结果

左侧按待处理、已处理、无需处理筛选；右侧直接选择译名并阅读对应原文、译文和逐例证据。候选显示去重使用次数，未决定项不默认代选。已处理列表显示采用译名，判断理由和进阶操作折叠，导出集中到菜单。

保存后以稳定术语组 ID 定位下一项，不复用旧草稿游标或回放此前页面；“完整队列”可返回前面的暂缓项。支持撤销当前窗口上次决定，要求草稿仍处于该次保存版本。保存失败保留输入，切换或关闭前保护未保存选择。窗口沿用可用屏幕边界限制，长内容局部滚动。

## 文件与职责

- 新增 `src/transbridge/ui/tools/terminology/conflict_review_pane.py`：候选选择、对应证据逐例阅读、自定义译名、插件特例/排除及输入状态。
- 新增同目录 `conflict_review_controller.py`：异步选择详情、过期响应隔离、保存并继续、受保护撤销；复用 AsyncActionRunner 和 DraftWriteQueue。
- 修改同目录 `conflicts_view.py`：主从布局、三种状态、两行队列代理、选中与分页；删除上一轮工作区中的 `conflict_evidence_panel.py`，不再并存重复证据面板。
- 修改同目录 `window.py`、`presenter.py`、`asset_presentation.py`：新控制器接线、取消参数、撤销接口、摘要进度及保存后刷新协调。
- 修改同目录 `object_views.py`、`task_dialog.py`、`save_close.py`：独立任务窗口路由、关闭守卫和未保存输入保护；`export_controller.py` 将既有完整导出移入次级菜单。
- 修改 `application/terminology/conflict_queries.py` 与 `persistence/terminology/conflict_queries.py`：新增状态与稳定 ID 起点筛选；SQLite 在草稿状态覆盖之后、分页计数之前筛选。
- 新增 `application/terminology/conflict_undo.py`：使用 DraftService 预期版本事务恢复该术语组，追加撤销审计；`models.py` 增加审计动作类型，`decisions.py` 复用按持久化顺序规范化的既有审计摘要。
- 修改 `bootstrap/terminology_review.py`、`bootstrap/terminology_workloads.py`、`persistence/terminology/conflict_reads.py`：撤销用例及详情读取取消贯通。
- 新增 `tests/ui/tools/terminology/test_conflict_review_controller.py`、`test_conflict_review_pane.py`；更新该目录的 `test_conflict_evidence_panel.py`、`test_conflict_judgment_presentation.py`、`test_conflict_filters.py`、`test_filter_interactions.py`、`test_source_browser.py`，保持懒加载、迟到结果、取消和控件布局覆盖。
- 更新 `tests/contracts/terminology/test_conflict_queries.py`、`tests/integration/bootstrap/test_terminology_conflict_review.py`、`tests/persistence/terminology/test_conflict_reads.py`：全局筛选、分页锚点、撤销、旧快照拒绝及有界读取。

## 验证

使用现有 uv 环境；临时目录为 `.tmp-review-integration`，完成后清理。

- `uv run --no-sync --cache-dir .tmp-review-integration/uv pytest tests/ui/tools/terminology --ignore=tests/ui/tools/terminology/test_shell_icon_theme.py --basetemp .tmp-review-integration/final-ui -q --tb=short`：318 通过，1 项旧来源浏览替身缺少新接口；修正替身后该文件 7 项复验通过（退出码 0）。真实项目测试需要正常权限创建临时数据库硬链接，初次沙箱运行的权限失败未作为产品失败处理。
- 同一 uv 命令单独执行 `tests/ui/tools/terminology/test_conflict_review_controller.py`：最终 5 项通过，退出码 0，覆盖保存下一条、跨页继续、撤销、失败输入保护、拒绝切换和屏幕边界。
- 同一 uv 命令执行 `tests/integration/bootstrap/test_terminology_conflict_review.py tests/contracts/terminology/test_conflict_queries.py tests/persistence/terminology/test_conflict_reads.py`：26 项通过。
- 后端广泛回归：`.venv/Scripts/python.exe -m pytest tests/application/terminology tests/contracts/terminology tests/persistence/terminology tests/integration/bootstrap/test_terminology_conflict_review.py -q --tb=short --basetemp .tmp-review-backend-tests`：390 通过，8 项因沙箱硬链接被拒绝；在正常权限环境复验 `test_reports.py` 与 `test_renderer_parity.py` 共 13 项全部通过。
- `ruff check src tests`、`ruff format --check src tests`（既有 `.venv/Scripts/ruff.exe`）以及 `git diff --check` 通过。
- 实际 Qt 截图检查 1100×720 和 800×600，验证长标题局部滚动、分栏、持续可见保存按钮；主题键盘回归覆盖明暗模式。独立图标主题文件在首轮回归通过，最终未重复。
- 有一次来源浏览与控制器合并运行打印 12 项通过后进程退出码 1、未输出诊断；随后两文件分别复验均退出码 0。未将该合并运行记录为成功。

## 兼容及边界

不迁移用户数据，不更改抽取/LLM 策略，不更换数据库或导出格式。新增撤销审计枚举，新版本能读取既有记录；旧程序可能不认识新撤销记录，不承诺降级写入兼容。撤销仅限本窗口最近一次保存且版本未变；其他修改发生后需重新审阅。未验证打包程序或真实用户项目的大规模人工审核操作。
