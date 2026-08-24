# Story-04：Workbench 功能切片与增量渲染保真

- **所属 Plan**：[UI 展示层模块化与上帝窗口拆分](../plan.md)
- **状态**：已完成（2026-08-19）
- **优先级**：P0
- **前置依赖**：S01、S02；S03 的公开 shell port
- **下游**：S05、S07、S08

## 目标

把 Step1/Step2 和操作卡拆成输入、筛选、表格、标签/菜单、进度与 operation 切片，同时保留大表增量渲染、编辑安全和稳定选择。

## 原始验收标准

- [x] `Step1Widget`、`Step2PreviewWidget` 和既有 cards import 路径保持兼容 facade。
- [x] 数据源/解析、筛选、翻译表格、标签、entry menu、进度和 upload/download/write cards 各有内聚 View/Presenter 或 controller。
- [x] table row identity、selection、edit safety、projection revision、render generation 和 queued batch 行为与基线一致。
- [x] filter revision、projection revision、render generation 明确分离，不用全量 `refresh()` 隐式推进多个状态。
- [x] 标签/Stage/TranslationEntry 修改只通过既有 application command/projection，不由对话框直接改业务对象。
- [x] 大数据交互不复制完整 collection 到 ViewState，窗口打开/筛选/滚动/编辑回归满足 NFR1.5。

## 当前入口与切片

- Step1：`_start_parse()`、batch/single `_run_parse_*()`、`_finish_parse()`、migration source → `SourceInputView` + `ParsePresenter`，实际顶层编排接 S03 `ParseCoordinator`。
- Step2 filter：category/stage/label/focus/search 与 `get/apply_filter_state()` → `FiltersView/Presenter`。
- Step2 table：`refresh()`、`_populate_table()`、`_append_table_batch(generation)`、edit/click/locate → `TranslationTable` + `TablePresenter`。
- labels/menu：`_LabelManagerDialog`、label commit/reload、context menu/stage → `LabelsView/Presenter` + `EntryMenu`。
- progress：`show/update/hide_progress()` → `ProgressView`。
- cards：对话框只是 form/value object；Presenter/controller 负责验证和 application/worker 请求，`OpCard` 保留共同视觉壳。

## 状态与接口

- `FilterState` 与 `filter_revision`：只描述筛选 intent。
- `ProjectionPort`：暴露 revision、stable entry ID、按当前 projection 迭代/批次读取、公开 mutation command；不暴露可写 collection。
- `RenderSession(generation, projection_revision, ordered_ids)`：新 refresh 递增 generation；迟到 batch 必须匹配两种 revision。
- `SelectionPort.selected_entry_ids()/locate_entry(id)`：供 AI 和 shell 使用；不暴露 `_table` 或列常量。
- ViewState 只含统计、筛选摘要、busy/progress 和必要 IDs；不复制全部 TranslationEntry。

```text
filter intent -> FiltersPresenter -> filter state/revision
             -> TablePresenter opens RenderSession
             -> batches by stable IDs -> TranslationTable
edit/label/stage intent -> application command -> new projection revision -> targeted render
```

## 实施步骤

1. 冻结 `_COL_*`、`_entry_category` 等真实消费者；把可复用语义移到公开 projection/column contract，不直接重导出 UI 私有 helper。
2. 先抽 ProgressView 和 Filters；保持现有 Step2 facade 方法委托。
3. 抽 TranslationTable/TablePresenter，原样迁移 generation、queued batch、`_find_rendered_translation_item()` 和 edit guard，再用 stable ID 替换跨组件列索引读取。
4. 迁 Labels/EntryMenu，所有修改走既有 projected mutation path；验证默认标签和快速创建。
5. 迁 Step1 source/parse UI，复用 S03 coordinator，避免 Step1 与 MainWindow 各有一套解析控制器。
6. 按 upload、download、write 迁 card dialogs/form 与 controller；共享批次选择/结果 UI 可抽公共 primitive，但不能合并业务分支。
7. 收敛 Step1/Step2 为 facade/composition root；删除已迁私有路径，复核尺寸与 import cycle。

## 边界与错误

- 新 filter 到达时旧 batch 立即失效；不得将旧 row append 到新 projection。
- 编辑中 refresh 保持现有 commit/cancel 语义，不能因 presenter 重建 model 丢编辑。
- entry 删除/Stage 改变导致当前选择不可见时，按基线选择下一个/清空，不持有失效 QObject item。
- 批次 operation 的部分成功、冲突解决、取消和 result dialog 语义不变。
- Parse/operation worker owner 仍为既有 runtime；View 销毁只解绑和忽略迟到结果。

## 文件变更

- 修改 `workbench/step1.py`、`step2.py` 与 `cards/*.py`
- 新增 `source_input_view.py`、`parse_presenter.py`、`filters_view.py`、`filters_presenter.py`
- 新增 `translation_table.py`、`table_presenter.py`、`labels_view.py`、`labels_presenter.py`、`entry_menu.py`、`progress_view.py`
- 新增/修改 Step1/Step2/cards tests 与 performance cases

## 测试与建议命令

- `pytest tests/ui/test_step2_incremental_rendering.py tests/ui/test_background_gui_operations.py -q`
- `pytest tests/ui -k "step1 or step2 or upload_card or download_card or write_card" -q`
- 10k+ entry filter/render/edit/locate、快速连续 filter、销毁时 queued batch 和 100 次生命周期循环。

## 风险与回退

表格是最高性能风险，必须单独切换且保留旧 facade。回退 composition 时保留相同 projection，不做数据转换。Cards 按 operation 独立切换，不能一次替换三种路径。

## 未决问题

- 是否从 `QTableWidget` 迁到 model/view 超出“零行为结构重构”范围；除非 S01 证明当前结构无法满足预算，否则本 Story 保留现有控件模型。
