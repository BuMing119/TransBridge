# Story 05: GUI 集成

**所属方案**: `plans/translation-memory/plan.md`
**技术模块**: frontend（PyQt6）
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- Story 01~04（同 plan）：已完成 → 提供 `TranslationMemoryManager` 全能力（add/query/save_from_collection/apply）

### 跨 Plan 依赖
- `ui/context.py`（AppContext）→ 复用 `active_slot.collection`
- `ui/workers.py`（ApiWorker/信号总线）→ 后台执行
- `ui/main_window.py` → 挂载入口

### 引用的架构决策
- ADR-014 决策 3（`translation_memory/` 后端包，UI 单独组织）、决策 3.1；ADR-004（QThread + 信号总线）复用

## 验收标准

> ⚠ 注：本节残留旧设计（「键表/文本表」「标签」）。最终以 plan.md 与 ADR-014 为准——实际实现为「翻译词典」菜单入口 + `DictionaryPanel`/`SaveToDictionaryDialog`，词典标签仅筛选不参与匹配。

- [ ] 主窗口「小工具」菜单提供「翻译词典」入口，打开词典管理面板
- [ ] 词典管理面板：查看各词典、键索引/文本索引条目、按词典/词典标签筛选、查看来源与命中计数、存为词典按钮
- [ ] 存为词典对话框：选择 scope/scope_id、粒度（整个集合/选中条目）、词典标签
- [ ] 操作后台执行（ApiWorker/信号总线复用），不阻塞 UI

## 数据流

```
用户点击「存为词典」
    │
    ├─ 弹 dialog：选 scope（project/game/global）+ scope_id、粒度（全量/选中）、标签
    ├─ 后台 ApiWorker 调 manager.save_from_collection(...)
    ├─ manager.save() 持久化
    └─ 结果提示（新增键数）

用户打开「词典库管理」面板
    │
    ├─ manager.load() 读取全部词典
    ├─ 左侧：词典列表（按 scope/scope_id）
    ├─ 右侧：选中词典的键表/文本表条目表格（译文/来源/命中/标签）
    └─ 顶部筛选：按词典 + 标签过滤
```

## 关键接口

### UI 组件

```python
class SaveToDictionaryDialog(QDialog):
    """目标词典（scope/scope_id）+ 粒度 + 标签选择"""
    # 返回 (scope, scope_id, entry_ids | None, tags)

class DictionaryPanel(QWidget):
    """词典库管理面板"""
    def __init__(self, manager, parent=None): ...
    def refresh(self): ...
    def _apply_filter(self): ...
```

## 实现步骤

### 步骤 1: 存为词典入口与对话框

**涉及文件**: `src/transbridge/ui/tools/dictionary_dialog.py`（新建）、`ui/main_window.py`（修改）

**实现要点**:
- 文件菜单/工具栏新增「存为词典」菜单项
- `SaveToDictionaryDialog`：scope 下拉（project/game/global）、scope_id 文本、粒度 radio（整个集合/选中条目）、标签文本（逗号分隔）
- 确认后取 `AppContext.active_slot.collection`，后台执行 `save_from_collection` + `save()`

**边界条件**:
- 无活跃集合 → 菜单项禁用或弹提示
- scope_id 留空 + scope=project → 提示必须填（或降级）；scope=global 时忽略 scope_id
- 后台执行失败 → 弹错误提示

**伪代码**:
```python
def _on_save_to_dict(self):
    slot = self._ctx.active_slot
    if not slot or not slot.collection:
        QMessageBox.warning(self, "提示", "请先解析并加载翻译集合"); return
    dlg = SaveToDictionaryDialog(self)
    if dlg.exec() != QDialog.Accepted:
        return
    scope, scope_id, entry_ids, tags = dlg.result()
    worker = ApiWorker(lambda: _do_save(slot.collection, scope, scope_id, entry_ids, tags))
    worker.result.connect(self._on_dict_saved); worker.error.connect(...)
    self._workers.append(worker); worker.start()
```

### 步骤 2: 词典库管理面板

**涉及文件**: `src/transbridge/ui/tools/dictionary_panel.py`（新建）

**实现要点**:
- `DictionaryPanel`：左侧词典列表（QListWidget，按 scope 分组展示）+ 右侧条目表格（QTableWidget：译文/来源/hit_count/tags）
- 顶部标签筛选（复用 manager 的词典 tags 聚合）
- 打开时 `manager.load()`；切换词典刷新右侧
- 面板作为 DockWidget 或独立对话框挂载

**边界条件**:
- manager 文件不存在 → 显示空提示
- 大量条目 → 分批加载预留（本期全量）
- 键表与文本表条目同显 → 表格加「表类型」列（键/文本）区分

**测试策略**:
- 集成测试（pytest-qt 可选）：入口存在、无集合提示、存词典后台不卡 UI、面板展示与筛选

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/tools/dictionary_dialog.py` | 新建 | 存词典对话框 |
| `src/transbridge/ui/tools/dictionary_panel.py` | 新建 | 词典库管理面板 |
| `src/transbridge/ui/main_window.py` | 修改 | 挂载菜单入口与面板 |
| `tests/test_translation_memory_gui.py` | 新建 | GUI 测试（可选） |

## 风险与注意事项

- **风险 1**: GUI 依赖 AppContext slot 结构 → 缓解：经 `active_slot.collection` 委托属性访问（ADR-002）
- **风险 2**: UI 文件位置 → 遵循 `ui/tools/` 现有组织，新建平行文件
- **注意 1**: 后台 ApiWorker 务必保留引用防 GC（ARCHITECTURE.md 已强调）
- **注意 2**: 持久化路径默认 `ParatranzConfig.get_data_dir()/translation_memory/`，沿用数据目录约定
- **注意 3**: 本次 GUI 为「存为词典 + 词典库查看」最小闭环；「套用到集合」的 GUI 入口（一键套用）若时间允许加工具栏按钮，否则留给调用方（fomod Epic）接入
