# Story 02: AppContext 扩展

**所属方案**: `plans/project-persistence/plan.md`
**技术模块**: `src/transbridge/ui/context.py` (修改)
**状态**: 已确认
**创建日期**: 2026-05-08

## 前置依赖

### 上游 Story
- Story-01（同 plan）：提供 WorkspaceState / ProjectHandle / VariantStore 类

### 引用的架构决策
- ADR-006: AppContext 扩展方案——添加 workspace/active_project/active_variant/variant_store，新增 project_list_changed/variant_changed 信号

## 验收标准

- [ ] AppContext 新增 `workspace` 属性
- [ ] AppContext 新增 `active_project` 属性
- [ ] AppContext 新增 `active_variant` 属性
- [ ] AppContext 新增 `variant_store` 属性
- [ ] AppContext 新增 `project_list_changed` 信号
- [ ] AppContext 新增 `variant_changed` 信号（携带 variant_name）
- [ ] `collection_changed` 信号触发时自动设置 `variant_store.dirty = True`

## 数据流

```
AppContext.__init__()
  ├─ 原有属性不变
  ├─ 新增: self._workspace = None
  ├─ 新增: self._active_project = None
  ├─ 新增: self._active_variant = None
  └─ 新增: self._variant_store = None

collection_changed 信号处理:
  emit collection_changed(collection)
    → slot: _on_collection_modified()
        if self._variant_store:
            self._variant_store.dirty = True

variant_changed 信号流程:
  active_variant setter
    → self._active_variant = name
    → self.variant_changed.emit(name)
    → UI 刷新 Step2 表格
```

## 关键接口

```python
class AppContext(QObject):
    # ── 新增信号 ──
    project_list_changed = pyqtSignal()       # 项目增删
    variant_changed = pyqtSignal(str)         # variant_name
    
    def __init__(self, parent=None):
        super().__init__(parent)
        # ... 原有初始化 ...
        self._workspace: WorkspaceState | None = None
        self._active_project: ProjectHandle | None = None
        self._active_variant: str | None = None
        self._variant_store: VariantStore | None = None
    
    # ── workspace ──
    @property
    def workspace(self) -> WorkspaceState | None:
        return self._workspace
    
    @workspace.setter
    def workspace(self, ws: WorkspaceState | None):
        self._workspace = ws
    
    # ── active_project ──
    @property
    def active_project(self) -> ProjectHandle | None:
        return self._active_project
    
    @active_project.setter
    def active_project(self, proj: ProjectHandle | None):
        self._active_project = proj
        self.project_list_changed.emit()
    
    # ── active_variant ──
    @property
    def active_variant(self) -> str | None:
        return self._active_variant
    
    @active_variant.setter
    def active_variant(self, name: str | None):
        old = self._active_variant
        self._active_variant = name
        if old != name:
            self.variant_changed.emit(name or "")
    
    # ── variant_store ──
    @property
    def variant_store(self) -> VariantStore | None:
        return self._variant_store
    
    @variant_store.setter
    def variant_store(self, vs: VariantStore | None):
        self._variant_store = vs
```

## 实现步骤

### 步骤 1: 导入 + 属性初始化

**涉及文件**: `src/transbridge/ui/context.py`（修改）

**实现要点**:
- 文件顶部添加条件导入（避免循环依赖）：`from src.transbridge.persistence.workspace import WorkspaceState` 等
- `__init__()` 中初始化 4 个新属性为 None
- 保持现有 `_slots`、`_active_key` 等属性不变

**边界条件**:
- 导入 persistence 模块时若模块不存在 → ImportError（S01 必须先完成）
- 属性为 None 时各 getter 返回 None（调用方负责检查）

**伪代码**:
```python
# context.py 顶部追加
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.transbridge.persistence.workspace import WorkspaceState
    from src.transbridge.persistence.project import ProjectHandle
    from src.transbridge.persistence.variant_store import VariantStore

class AppContext(QObject):
    # 新增信号（在现有信号定义后）
    project_list_changed = pyqtSignal()
    variant_changed = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        # ... 原有属性 ...
        self._workspace = None
        self._active_project = None
        self._active_variant = None
        self._variant_store = None
```

**测试策略**:
- 集成测试：AppContext 初始化后新属性均为 None
- 集成测试：setter/getter 往返

### 步骤 2: 添加 property + 信号联动

**涉及文件**: `src/transbridge/ui/context.py`（修改）

**实现要点**:
- 4 个 property getter/setter（参照现有 `active_slot` 风格）
- `active_project` setter 中 emit `project_list_changed`
- `active_variant` setter 中 emit `variant_changed`（仅值变更时）
- `collection_changed` 信号保持现有行为不变

**边界条件**:
- 重复设置相同值 → 不 emit 信号（避免不必要的 UI 刷新）

**伪代码**:
```python
    # 参照现有 collection property 风格
    @property
    def active_project(self) -> 'ProjectHandle | None':
        return self._active_project
    
    @active_project.setter
    def active_project(self, v: 'ProjectHandle | None') -> None:
        self._active_project = v
        self.project_list_changed.emit()
```

**测试策略**:
- 集成测试：设置 active_variant 触发 variant_changed 信号
- 集成测试：重复设置相同 variant 不重复 emit

### 步骤 3: collection_changed → dirty 联动

**涉及文件**: `src/transbridge/ui/context.py`（修改）

**实现要点**:
- 在现有 `collection_changed.emit()` 调用后追加 dirty 标记逻辑
- 不改变现有信号发射时机

**边界条件**:
- variant_store 为 None 时跳过 dirty 标记

**伪代码**:
```python
    def add_slot(self, key, slot):
        # ... 现有逻辑 ...
        self.collection_changed.emit(slot.collection)
        self._mark_dirty()  # 新增
    
    def _mark_dirty(self):
        if self._variant_store is not None:
            self._variant_store.dirty = True
```

**测试策略**:
- 集成测试：修改 collection（add_slot/编辑译文）后 variant_store.dirty 为 True

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/context.py` | 修改 | 添加 4 属性 + 2 信号 + dirty 联动 |

## 风险与注意事项

- **TYPE_CHECKING 避免循环导入**: persistence 模块不依赖 context，但 context 导入 persistence。使用 `TYPE_CHECKING` 保持类型提示可用，运行时延迟导入
- **与现有 slots/collection 不冲突**: 新增属性独立于现有 `_slots`/`_active_key`，新旧两套机制并行运行
