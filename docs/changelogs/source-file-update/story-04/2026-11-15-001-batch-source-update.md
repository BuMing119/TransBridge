# 批量更新源文件

2026-11-15 · [source-file-update Story 04](../../../../plans/source-file-update/plan.md#story-04-批量更新源文件)

- `ui/source_update_dialog.py` 新增同一个窗口内的配对和预览。支持多选、递归文件夹查找、逐行指定、更换、原路径重读、跳过与返回改选。
- `ui/source_update_selection.py` 根据文件名、扩展名和双向唯一性给出配对建议，保留同名候选供用户处理。`application/io/catalog.py` 复用格式扩展名提示，`application/projects/source_input_candidates.py` 用文件名索引查找候选。
- `ui/source_update_preview.py` 显示整批影响版本数和可展开的逐来源差异。`ui/coordinators/source_update_coordinator.py` 在后台执行文件发现、预览和提交，复用工作台与只读恢复入口。
- `application/projects/source_update.py` 和 `source_update_models.py` 新增批量准备及逐来源差异，保留单来源预览契约。`source_update_migration.py` 在每个版本中按原来源分别迁移条目，工程和版本修订各推进一次。
- `application/projects/source_input_publication.py` 在写入前重新核对每个新版文件。`source_supplement.py` 适配共用发布接口，继续以单个输入发布。
- `persistence/project_store/source_updates.py` 在同一事务中核对全部来源引用与版本，生成一组更新前快照，写入来源和全部版本。工程无需迁移存储格式。
- `tests/application/projects/test_batch_source_update.py` 覆盖多来源全版本迁移、单组备份、第二个新版失效、第二个版本写入失败及无效文件。`tests/ui/test_source_update.py` 覆盖配对冲突、跳过、返回、取消和恢复入口。

## 验证

- 最终专项运行 `uv --no-cache --offline run --no-sync pytest`，范围含批量与单来源更新、插件、文本编码、补充输入、候选匹配、更新存储与备份、存储装配、来源更新与补充窗口。121 项通过，1 项因 Windows 符号链接能力跳过。
- 同一运行命令扩大到 `tests/application/projects` 和本次相关存储、装配及 UI 文件，381 项通过，1 项跳过，5 项来源路径断言失败。在改动前的 HEAD `30c2fbd7` 临时导出中复跑 `tests/application/projects/test_source_artifact_publication_cost.py`，相同 5 项失败，另有 2 项通过。
- `uv --no-cache --offline run --no-sync ruff check src tests`、`uv --no-cache --offline run --no-sync ruff format --check src tests` 和 `git diff --check` 通过。
- 使用 Windows Qt 平台、微软雅黑和内置浅色主题渲染两页，核对歧义状态、展开后的路径与影响信息、底部按钮顺序。未执行全库 pytest、完整桌面人工验收或真实 ParaTranz 写入。
