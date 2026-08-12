# UI 工作台

> **状态**: ✔️ 已实现（Story 1-22）
> **模块**: `src/transbridge/ui/`

## 概述

基于 PyQt6 的桌面 GUI，提供三步翻译工作流、ParaTranz 管理面板、AI 翻译浮动窗口、全局状态管理和信号总线。

## Story 清单

| Story | 标题 | 状态 |
|-------|------|------|
| Story-01 | AppContext 全局状态管理（多 CollectionSlot + Qt 信号） | ✔️ |
| Story-02 | ApiWorker 后台线程 + 全局信号总线（HTTP 错误/API 状态） | ✔️ |
| Story-03 | 主窗口框架（QMainWindow + 工作台/ParaTranz 双 Tab） | ✔️ |
| Story-04 | Step1 源文件解析面板（批量 ESP + 迁移源追加 + JSON 导入） | ✔️ |
| Story-05 | Step2 词条预览面板（多选 checkbox + 筛选栏） | ✔️ |
| Story-06 | Step3 操作面板（上传/下载/写回三卡片） | ✔️ |
| Story-07 | 左侧集合统计面板（分类树形统计） | ✔️ |
| Story-08 | OpCard 操作卡片基类 + UploadCard / DownloadCard / WriteCard | ✔️ |
| Story-09 | AI 翻译浮动窗口（QTabWidget 三标签页：LLM/术语库/后处理） | ✔️ |
| Story-10 | AI 翻译进度窗口 + 后台 Worker（暂停/停止/后台） | ✔️ |
| Story-11 | 批量翻译 UI（插件选择 + 配置 + 进度 + 日志查看） | ✔️ |
| Story-12 | ParaTranz 管理面板（概览/文件/词条/术语/成员/历史/贡献/导出/讨论） | ✔️ |
| Story-13 | API 配置对话框 + 项目列表面板 + 新建项目对话框 | ✔️ |
| Story-14 | 批量操作 UI（SlotSelectDialog / BatchConfirmDialog / BatchResultDialog） | ✔️ |
| Story-15 | 文件菜单 — 集合管理（新建/导入JSON/移除/切换子菜单） | ✔️ |
| Story-16 | 文件菜单 — 解析配置对话框与解析执行 | ✔️ |
| Story-17 | 文件菜单 — 操作菜单项（上传/下载/写回+批量） | ✔️ |
| Story-18 | 工作台布局简化（移除 Step1/3，Step2 全宽，进度嵌入） | ✔️ |
| Story-19 | 分类筛选标签组与面板精简（去标题、移除左侧面板、分类标签筛选） | ✔️ |
| Story-20 | 筛选系统统一化（多选标签 + 状态标签 + 搜索栏 + 移除弹窗） | ✔️ |
| Story-21 | 表格交互升级（行内编辑 + Ctrl/Shift 行选取代复选框） | ✔️ |
| Story-22 | 标记列与可视化系统（三态标记+行背景色+标记筛选+聚焦开关） | ✔️ |

## 关键文件

- `src/transbridge/ui/context.py` — AppContext, CollectionSlot
- `src/transbridge/ui/workers.py` — ApiWorker, _http_error_bus, _api_status_bus
- `src/transbridge/ui/main_window.py` — MainWindow
- `src/transbridge/ui/app.py` — QApplication 入口
- `src/transbridge/ui/workbench/widget.py` — WorkbenchWidget
- `src/transbridge/ui/workbench/step1.py` — Step1SourceWidget
- `src/transbridge/ui/workbench/step2.py` — Step2PreviewWidget
- `src/transbridge/ui/workbench/step3.py` — Step3OpsWidget
- `src/transbridge/ui/workbench/stats_panel.py` — CollectionStatsPanel
- `src/transbridge/ui/workbench/cards/base.py` — OpCard
- `src/transbridge/ui/workbench/cards/upload_card.py` — UploadCard
- `src/transbridge/ui/workbench/cards/download_card.py` — DownloadCard
- `src/transbridge/ui/workbench/cards/write_card.py` — WriteCard
- `src/transbridge/ui/tools/ai_translator/ai_translator_window.py` — AITranslatorWindow
- `src/transbridge/ui/tools/ai_translator/_translation_worker.py` — _TranslationWorker
- `src/transbridge/ui/tools/ai_translator/_translation_progress_window.py` — 进度窗口
- `src/transbridge/ui/tools/ai_translator/_batch_translation_worker.py` — 批量翻译 Worker
- `src/transbridge/ui/paratranz/widget.py` — ParaTranzWidget
- `src/transbridge/ui/workbench/_parse_config_dialog.py` — ParseConfigDialog（NEW: Story-16）

## 相关 ADR

- [ADR-004: QThread + 信号总线异步模式](../../docs/adr/004-qthread-async-pattern.md)

---

## Story-15: 文件菜单 — 集合管理

**对应需求**: FR7.7.1  
**状态**: ✔️ 已实现  
**验收标准**:
- [x] `文件 → 集合` 子菜单存在，包含「新建集合」「导入 JSON…」「移除当前集合」
- [x] `文件 → 切换集合` 子菜单列出所有已加载 slot，点击切换当前活跃集合
- [x] 无集合时「移除」和「切换」菜单项灰色禁用
- [x] 新建集合 → 清空解析表单、解锁状态
- [x] 导入 JSON → 弹出文件对话框 → 后台加载 → 注册 slot
- [x] 移除当前集合 → 弹出确认框 → 确认后移除 slot

**实现步骤**:
1. 在 `main_window.py` `_init_menu()` 中重构「文件」菜单，新增「集合」子菜单（新建/导入JSON/移除/分隔线/切换集合列表）→ 涉及文件: `src/transbridge/ui/main_window.py`
2. 在 `MainWindow` 中新增方法 `_rebuild_collection_menu()`，监听 `ctx.collection_list_changed` 信号动态重建切换集合列表 → 涉及文件: `src/transbridge/ui/main_window.py`
3. 实现各菜单项的触发逻辑：新建(`_on_new_slot`)、导入JSON(`_on_import_json`)、移除(`_on_remove_slot`) —— 逻辑从 `step1.py` 提取到 MainWindow → 涉及文件: `src/transbridge/ui/main_window.py`, `src/transbridge/ui/workbench/step1.py`(提取)
4. 实现菜单项动态启用/禁用：`_update_collection_menu_state()` 根据 `ctx.slots` 是否为空控制 → 涉及文件: `src/transbridge/ui/main_window.py`

---

## Story-16: 文件菜单 — 解析配置对话框与解析执行

**对应需求**: FR7.7.2  
**状态**: ✔️ 已实现  
**验收标准**:
- [x] `文件 → 解析插件…` 弹出解析配置对话框
- [x] 对话框包含：ESP多选、EET/XT/已翻译插件/Strings路径选择（每行：显示路径+浏览+清除按钮）
- [x] 对话框包含：来源模式切换（ESP插件/EET XML）、跳过空串下拉、语言选择下拉
- [x] 对话框有「Strings应用到全部」复选框
- [x] 确认后触发后台解析 Worker，进度通过全局信号反馈
- [x] 解析成功后自动注册 slot 到 ctx
- [x] `文件 → 应用迁移源…` 复用同一对话框（已存在 collection 时仅显示迁移源部分）
- [x] 无项目时菜单项不受影响（解析不需要项目）

**实现步骤**:
1. 新建 `src/transbridge/ui/workbench/_parse_config_dialog.py`，创建 `ParseConfigDialog(QDialog)` 类，包含所有 Step1 表单控件（文件路径行 ×5、来源模式单选、跳过空串、语言选择、「应用到全部」复选框）→ 涉及文件: `src/transbridge/ui/workbench/_parse_config_dialog.py`(增)
2. 对话框提供 `get_config()` 方法返回配置数据类 `ParseConfig`（esp_paths, eet_path, xt_path, tp_path, strings_dir, strings_lang, skip_empty, source_mode）→ 涉及文件: `src/transbridge/ui/workbench/_parse_config_dialog.py`
3. 在 `MainWindow._init_menu()` 中添加 `文件 → 解析插件…` 和 `文件 → 应用迁移源…` 菜单项 → 涉及文件: `src/transbridge/ui/main_window.py`
4. 实现 `_on_parse_plugin()` 方法：弹出对话框 → 获取配置 → 调用与原 `step1.py._start_parse()` 等效的解析逻辑（复用 PluginParser / EET_XmlParser 等，通过 ApiWorker 后台执行）→ 涉及文件: `src/transbridge/ui/main_window.py`, `src/transbridge/ui/workbench/step1.py`(提取解析逻辑)
5. 实现 `_on_apply_migration()` 方法：复用与原 `step1.py._apply_migration_sources()` 等效的迁移逻辑 → 涉及文件: `src/transbridge/ui/main_window.py`

---

## Story-17: 文件菜单 — 操作菜单项

**对应需求**: FR7.7.3  
**状态**: ✔️ 已实现  
**验收标准**:
- [x] `文件 → 上传至 ParaTranz` 菜单项存在，触发原 UploadCard 的上传流程
- [x] `文件 → 批量上传…` 菜单项存在（多 slot 时启用）
- [x] `文件 → 下载合并` 菜单项存在，触发原 DownloadCard 的下载流程
- [x] `文件 → 批量下载…` 菜单项存在（多 slot 时启用）
- [x] `文件 → 写回文件…` 菜单项存在，触发原 WriteCard 的写回流程（含目标选择对话框）
- [x] `文件 → 批量写回…` 菜单项存在（多 slot 时启用）
- [x] 菜单项根据 `_update_button_states()` 逻辑动态启用/禁用（无集合→全禁用，无项目→上传/下载禁用，非成员→上传/下载禁用）
- [x] 项目操作目标指示从 Step3 迁移到状态栏（`_project_indicator`）

**实现步骤**:
1. 在 `MainWindow._init_menu()` 中添加操作菜单组（上传/下载/写回 + 各批量变体 + 分隔线）→ 涉及文件: `src/transbridge/ui/main_window.py`
2. 实现 `_update_operation_menu_state()` 方法，复制 `step3.py._update_button_states()` 逻辑，通过 `QAction.setEnabled()` 控制 → 涉及文件: `src/transbridge/ui/main_window.py`
3. 连接 `ctx.collection_changed` 和 `ctx.project_selected` 到 `_update_operation_menu_state()` → 涉及文件: `src/transbridge/ui/main_window.py`
4. 实现各菜单项的 action handler：`_on_upload()`、`_on_batch_upload()`、`_on_download()`、`_on_batch_download()`、`_on_write()`、`_on_batch_write()` —— 从 `step3.py` 和对应 Card 类提取操作入口逻辑 → 涉及文件: `src/transbridge/ui/main_window.py`, `src/transbridge/ui/workbench/step3.py`(提取)

---

## Story-18: 工作台布局简化

**对应需求**: FR7.7.4, FR7.7.5  
**状态**: ✔️ 已实现  
**验收标准**:
- [x] `widget.py` 中移除 `Step1SourceWidget` 和 `Step3OpsWidget` 的实例化
- [x] 右侧面板仅保留 `Step2PreviewWidget`，占满全宽
- [x] Step2 面板底部嵌入进度条（`QProgressBar`）和状态标签（`QLabel`）
- [x] 进度条和状态标签通过全局信号或 MainWindow 传入状态更新
- [x] 解析、上传、下载、写回的进度均可在此进度区显示
- [x] 左侧集合统计面板不受影响

**实现步骤**:
1. 在 `Step2PreviewWidget` 底部添加进度区域（`QProgressBar` + `QLabel`），提供 `show_progress(total, msg)` / `update_progress(current, total, msg)` / `hide_progress()` 公共方法 → 涉及文件: `src/transbridge/ui/workbench/step2.py`
2. 修改 `WorkbenchWidget._init_ui()`：移除 `self._step1` 和 `self._step3` 及相关信号连接，右侧仅包含 `step2`，移除 `QScrollArea`（因为只剩一个组件无需滚动）→ 涉及文件: `src/transbridge/ui/workbench/widget.py`
3. 在 `MainWindow` 中将解析/操作的进度信号连接到 `step2` 的进度区域；清理 `step1`/`step3` 相关引用（如 `open_tool` 中的 `_step2` 引用保留）→ 涉及文件: `src/transbridge/ui/main_window.py`

---

## Story-19: 分类筛选标签组与面板精简

**对应需求**: FR7.8, FR7.8.1 ~ FR7.8.4  
**状态**: ✔️ 已实现  
**验收标准**:
- [x] `step2.py` 中去除 QGroupBox「步骤2：解析结果预览」包装，内容直接放入外层 layout
- [x] Step2 表格上方新增分类标签行，标签显示「分类名 + 数量」（如「对话 1,234」），含「全部」标签
- [x] 标签单选切换：点击高亮并过滤表格；再次点击同一标签取消选中恢复全部
- [x] 「全部」标签始终显示总数，点击恢复未筛选状态
- [x] 标签行与现有筛选栏（翻译状态下拉+类型下拉）独立联动
- [x] `widget.py` 中移除 `CollectionStatsPanel` 导入和实例化，移除 `QSplitter`（只剩 Step2 单一组件）
- [x] 无集合时标签行隐藏
- [x] 左侧面板移除后 Step2 表格占满全宽

**实现步骤**:
1. 修改 `Step2PreviewWidget._init_ui()`：去除 QGroupBox 包装，内容直接放入 outer layout；在四格统计卡与筛选提示之间新增分类标签行（QHBoxLayout 中 QPushButton 组，flat 样式，选中高亮用不同 stylesheet）；新增 `_build_category_tags()` 方法从 `_entries` 统计各分类数量并创建标签按钮，连接点击信号到 `_on_category_tag_clicked(category)`；新增 `_category_filter: str | None` 属性存储当前选中分类；修改 `_populate_table()` 在过滤逻辑中叠加分类筛选；无集合时隐藏整个标签行 → 涉及文件: `src/transbridge/ui/workbench/step2.py`
2. 修改 `WorkbenchWidget._init_ui()`：移除 `CollectionStatsPanel` 导入和实例化；移除 `QSplitter`，`self._step2` 直接通过 `QVBoxLayout` 放入；保留 `open_tool()` 不变 → 涉及文件: `src/transbridge/ui/workbench/widget.py`
3. 验证：启动应用 → 解析一个插件 → 确认标签正确显示各分类及数量 → 点击标签过滤表格 → 点击「全部」恢复 → 切换集合确认标签刷新 → 确保四格统计卡仍正确显示 → 涉及文件: `src/transbridge/ui/workbench/step2.py`, `src/transbridge/ui/workbench/widget.py`

---

## Story-20: 筛选系统统一化

**对应需求**: FR7.9.1 ~ FR7.9.3, FR7.9.6  
**状态**: ✔️ 已实现  
**验收标准**:
- [ ] 分类标签支持多选：点击多个标签同时筛选，标签高亮显示选中态
- [ ] 标签数字随其他标签选中联动更新——选中"人名"时"地名"标签显示的是「地名 AND 人名」的交集数量
- [ ] 翻译阶段改为标签形式（未翻译 / 机翻 / 已翻译），与分类标签风格统一，支持多选
- [ ] Key/原文/译文搜索框嵌入主表顶部（分类标签行下方），输入后实时过滤表格
- [ ] _EntryDetailDialog 类完全移除，其所有引用清理干净
- [ ] _apply_filter_to_table 方法移除（该方法的唯一调用方是弹窗的确定按钮）
- [ ] 所有筛选条件（分类标签 + 状态标签 + 搜索框 + 翻译状态下拉）AND 叠加工作
- [ ] 无筛选条件时表格显示全部词条

**实现步骤**:
1. 重构分类标签为多选：`_category_filter: str | None` → `_category_filters: set[str]`；`_on_category_tag_clicked` 改为 toggle 添加/移除；标签计数改为考虑其他已选中标签的交集 → 涉及文件: `src/transbridge/ui/workbench/step2.py`
2. 新增翻译状态标签行：在分类标签行下方添加状态标签（全部/未翻译/机翻/已翻译），复用分类标签的样式和交互模式；`_stage_filters: set[int]` 存储选中状态；`_populate_table` 叠加状态筛选 → 涉及文件: `src/transbridge/ui/workbench/step2.py`
3. 新增搜索栏：在状态标签行下方添加 Key/原文/译文三个 QLineEdit 搜索框 + 清除按钮；`_search_filters: dict[str, str]` 存储搜索词；使用 QTimer 防抖（150ms）触发 `_apply_all_filters`；`_populate_table` 叠加文本搜索（子串匹配）→ 涉及文件: `src/transbridge/ui/workbench/step2.py`
4. 移除 `_EntryDetailDialog` 类（原 line 92-375 整段）；移除 `_on_double_clicked` 中对 `_EntryDetailDialog` 的创建和调用（保留空方法体，Story-21 赋予新行为）；移除 `_apply_filter_to_table` 方法 → 涉及文件: `src/transbridge/ui/workbench/step2.py`

---

## Story-21: 表格交互升级

**对应需求**: FR7.9.4 ~ FR7.9.5  
**状态**: ✔️ 已实现  
**验收标准**:
- [ ] 表格无复选框列，列布局调整为 Key / 原文 / 译文 / 类型
- [ ] 表格设置为行选模式（ExtendedSelection），Ctrl+点击追加，Shift+点击范围选择
- [ ] 双击译文单元格进入编辑模式（QTableWidget 默认 EditTrigger 或自定义 delegate），回车或焦点离开保存
- [ ] 编辑后 TranslationEntry.translation 和 stage 更新（stage≥1 保持不变，stage=0 且输入了译文则设为 2）
- [ ] 原文列不可编辑
- [ ] `get_selected_entries()` 改为从 `self._table.selectedItems()` 去重获取，接口签名不变
- [ ] AI 翻译窗口调用 `get_selected_entries()` 仍正常工作
- [ ] 底部计数标签「已选 N 条 / 共 M 条」改为从选中行数计算

**实现步骤**:
1. 重构表格列和选择模式：`_COL_CHECK` 常量移除，`_NUM_COLS` 改为 4；`_init_ui` 中表头改为 `["Key", "原文", "译文", "类型"]`，设置 `setSelectionBehavior(SelectRows)` + `setSelectionMode(ExtendedSelection)`；移除 `_header_check` 复选框和相关信号连接；移除 `_on_header_check_changed` 和 `_on_item_changed`；`_populate_table` 移除第 0 列复选框逻辑 → 涉及文件: `src/transbridge/ui/workbench/step2.py`
2. 实现行内编辑：设置 `setEditTriggers(DoubleClicked)` 或 `CurrentChanged`，仅译文列 (col 2) 可编辑，其他列设为 `ItemIsEditable` 为 False 的 flag；连接 `itemChanged` 信号，在回调中检测译文列变化，更新 entry.translation 和 entry.stage（若 stage==0 且有译文 → stage=2）；刷新该行显示 → 涉及文件: `src/transbridge/ui/workbench/step2.py`
3. 重写 `get_selected_entries()`：遍历 `self._table.selectedItems()`，按行去重，通过 `item.data(UserRole)` 获取 entry；移除 `_selected_entry_ids` 属性和所有相关引用；`_update_count_label` 改为用 `len(self._table.selectedIndexes())` 或选中行数 → 涉及文件: `src/transbridge/ui/workbench/step2.py`

---

## Story-22: 标记列与可视化系统

**对应需求**: FR7.10.1 ~ FR7.10.6  
**状态**: ✔️ 已实现  
**详细文档**: `plans/ui-workbench/stories/story-22-mark-and-visual.md`  
**验收标准**:
- [ ] 第 0 列为标记列，显示图标：无标记="" / ★待处理 / ?有疑问 / ✓已确认
- [ ] 点击标记列循环切换：无→★→?→✓→无；标记存储在 `_entry_marks: dict[str, str]`
- [ ] 行背景色按翻译阶段：未翻译白色、已翻译浅绿 #E8F5E9、有疑问浅黄 #FFF8E1
- [ ] 标记筛选标签行（★待处理/?有疑问/✓已确认），点击筛选对应标记条目，与现有筛选 AND 叠加
- [ ] 「👁 只看已标记」切换按钮，一键过滤出所有有标记的条目
- [ ] 底部计数显示「★ N / ? N / ✓ N | 显示 M 条（共 K 条）」
- [ ] `get_selected_entries()` 返回 ★ 标记条目（向后兼容 AI 翻译窗口）
- [ ] 标记在筛选/搜索/切换集合间持久化（会话内）

**实现步骤**:
1. **标记列替换复选框列**: `_COL_CHECK` 保留但语义改为标记列；`_selected_entry_ids: set[str]` → `_entry_marks: dict[str, str]`（entry_id → "star"/"question"/"confirmed"）；`_init_ui` 表头文本、"  " → "标记"；`_populate_table` 中复选框 `QTableWidgetItem` 改为显示标记字符的普通项（不可编辑、不可勾选）；`_on_cell_clicked` 改为循环切换标记：读取当前标记→按序切到下一个→更新 `_entry_marks` → 刷新该 Cell 显示；`_on_item_changed` 移除复选框分支，仅保留译文编辑逻辑 → 涉及文件: `src/transbridge/ui/workbench/step2.py`
2. **行背景色**: `_populate_table` 中为每行的 key/orig/trans/ctx 四个 item 设置 `setBackground(QColor(...))`：stage=0 且无译文=白色、stage=1=浅黄 #FFF8E1、已翻译=浅绿 #E8F5E9 → 涉及文件: `src/transbridge/ui/workbench/step2.py`
3. **标记筛选标签**: 在搜索栏下方新增 `_mark_tags_widget`，内置「★待处理」「?有疑问」「✓已确认」三个 QPushButton 标签按钮；复用 `_TAG_NORMAL/_TAG_ACTIVE` 样式和 `_category_filters` 的多选交互模式；`_mark_filters: set[str]` 存储选中状态；`_apply_all_filters` 叠加标记筛选 → 涉及文件: `src/transbridge/ui/workbench/step2.py`
4. **聚焦开关**: 在标记筛选标签行末尾添加「👁 只看已标记」QPushButton（toggle 模式），`_focus_marked: bool = False`；激活时 `_apply_all_filters` 仅返回 `_entry_marks` 中有标记的条目；无标记条目时按钮禁用（setEnabled(False)）→ 涉及文件: `src/transbridge/ui/workbench/step2.py`
5. **标记计数**: `_update_count_label` 改为统计 `_entry_marks` 中各标记类型的数量，显示「★ {star} / ? {question} / ✓ {confirmed} | 显示 {shown} 条（共 {total} 条）」→ 涉及文件: `src/transbridge/ui/workbench/step2.py`
6. **get_selected_entries() 适配**: 改为返回所有 ★ 标记（`mark_type == "star"`）的条目列表，保持接口签名不变，确保 AI 翻译窗口无需修改 → 涉及文件: `src/transbridge/ui/workbench/step2.py`

