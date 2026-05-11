# Story 03: AppContext ViewModel 扩展

**所属方案**: `plans/agent-tool-expansion/plan.md`
**技术模块**: backend (ui/context.py)
**状态**: 已确认 (v2)
**创建日期**: 2026-05-11
**更新日期**: 2026-05-11（v2: +标签数据上移(B1) +translation_scope正式化(E8) +filter_state映射契约(E6)）

## 前置依赖

### 上游 Story
- Story 01（同 plan）→ 提供 `ToolResult` 数据类

### 引用的架构决策
- ADR-008: 架构师路线 — 纯数据操作 + 信号驱动 UI，工具不碰 QWidget
- ADR-004: pyqtSignal 异步模式

## 验收标准

- [ ] `AppContext` 新增 `filter_state` 属性（dict）：`{stage: list[int], category: list[str], label: list[str], search_query: str, search_field: str}`
- [ ] `AppContext` 新增 `filter_changed` pyqtSignal
- [ ] `AppContext` 新增 `set_filter(**kwargs)` 方法 — 合并更新 filter_state 并 emit
- [ ] `AppContext` 新增 `clear_filters()` 方法 — 重置 filter_state 为默认值
- [ ] **B1: 标签数据上移** — `AppContext` 新增 `label_library: dict[str, dict]`、`entry_labels: dict[str, set[str]]`、`label_data_changed` pyqtSignal
- [ ] 标签数据加载路径：VariantStore → AppContext → UI 订阅信号 + Tools 直接读写（替代原 VariantStore → Step2PreviewWidget 的私有路径）
- [ ] **E8: _translation_scope 纳入正式属性** — property getter/setter，setter 中类型校验（stages 为 list[int]、action 为枚举值）
- [ ] **E6: filter_state 映射契约文档** — 明确 `search_field`（Agent 统一字段）与 Step2 三个独立搜索框（ID/Key/Text）的映射关系
- [ ] 新增属性不破坏现有 AppContext 使用方

## 数据流

```
Agent 工具（如 filter_by_stage）
    │
    ├─→ ctx.set_filter(stage=[0, 1])
    │       └─ _filter_state["stage"] = [0, 1]
    │       └─ filter_changed.emit(_filter_state)
    │
    └─→ Step2 表格订阅 filter_changed
            └─ _on_filter_changed(state)
                └─ 遍历表格行，setRowHidden() 匹配 stage

Agent 工具（如 clear_all_filters）
    │
    └─→ ctx.clear_filters()
            └─ _filter_state 重置为默认值
            └─ filter_changed.emit(_filter_state)

Agent 工具（如 get_current_filters）
    │
    └─→ ctx.filter_state  # 纯读取
```

## 关键接口

```python
# ui/context.py 追加

from PyQt6.QtCore import pyqtSignal

class AppContext(QObject):
    # 新增信号
    filter_changed = pyqtSignal(dict)
    
    # 新增属性
    _filter_state: dict
    
    DEFAULT_FILTER_STATE = {
        "stage": [],        # list[int] — 空列表 = 不筛选
        "category": [],     # list[str]
        "label": [],        # list[str]
        "search_query": "", # str
        "search_field": "all",  # "key" | "original" | "translation" | "all"
    }
    
    @property
    def filter_state(self) -> dict:
        """获取当前筛选状态（只读副本）。"""
    
    def set_filter(self, **kwargs) -> None:
        """合并更新筛选状态并发射 filter_changed 信号。"""
    
    def clear_filters(self) -> None:
        """重置所有筛选条件为默认值。"""
```

## 实现步骤

### 步骤 1: 初始化 filter_state 属性和信号

**涉及文件**: `src/transbridge/ui/context.py`（修改）

**实现要点**:
- 在 `__init__` 中初始化 `_filter_state = dict(DEFAULT_FILTER_STATE)`
- 定义 `filter_changed = pyqtSignal(dict)`

**边界条件**:
- 确保 AppContext 继承自 QObject（或已有信号机制）

---

### 步骤 2: 实现 `set_filter()` 和 `clear_filters()`

**涉及文件**: 同上

**实现要点**:
- `set_filter(**kwargs)`: 深度合并 kwargs 到 `_filter_state`（仅更新传入的 key），发射 `filter_changed.emit(dict(self._filter_state))`
- `clear_filters()`: 重置为 `DEFAULT_FILTER_STATE` 的深拷贝，发射信号

**边界条件**:
- `set_filter()` 传入空 dict → 不改变状态，不发射信号
- `set_filter(stage=[])` → 清空 stage 筛选（恢复全部），合法操作

---

### 步骤 3: 实现 `filter_state` property

**涉及文件**: 同上

**实现要点**:
- getter: 返回 `_filter_state` 的深拷贝（防止外部意外修改内部状态）
- setter: 整体替换 `_filter_state` + 发射信号（用于恢复已保存的筛选状态）

**边界条件**:
- 深拷贝性能：`_filter_state` 最多 5 个 key，值均为简单类型，深拷贝开销可忽略

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `ui/context.py` | 修改 | +filter_state +filter_changed +label_library +entry_labels +label_data_changed +translation_scope |
| `ui/workbench/step2.py` | 修改 | 标签读写从私有变量改为 AppContext 属性，订阅 label_data_changed 信号 |
| `docs/` 映射契约 | 新增 | search_field ↔ Step2 搜索框映射文档（E6） |

## 风险与注意事项

- **注意**: `filter_changed` 信号携带完整 filter_state 而非增量变更，订阅方需全量处理
- **注意**: 此 Story 仅定义数据层，Step2 表格订阅信号的逻辑属于后续 UI 适配
- **注意**: 标签数据上移后，VariantStore 持久化链路不变（save_labels/load_labels），仅运行时读写路径从 UI 层移至 AppContext
- **注意**: Step2PreviewWidget 的 `_label_library`/`_entry_labels` 需改为从 `ctx.label_library`/`ctx.entry_labels` 读取，操作后 emit `label_data_changed`
- **E6 映射契约**: `search_field="id"` → 匹配 `entry.id`；`search_field="key"` → 匹配 `entry.key`；`search_field="text"` → 匹配 `entry.original`（默认）
