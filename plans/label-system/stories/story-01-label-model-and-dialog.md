# Story 01: 标签库模型与管理

**所属方案**: `plans/label-system/plan.md`
**技术模块**: ui (PyQt6)
**状态**: 已确认
**创建日期**: 2026-05-07

## 前置依赖

### 上游 Story
- Story-22（ui-workbench）：三态标记系统 → 将被本 Story 替换

### 引用的架构决策
- ADR-004: QThread + 信号总线异步模式

## 验收标准

- [ ] `_label_library: dict[str, dict]` 和 `_entry_labels: dict[str, set[str]]` 数据结构
- [ ] `_LabelManagerDialog` 对话框（标签列表 + 添加/编辑/删除 + 颜色选择）
- [ ] 工具栏「管理标签」按钮打开对话框
- [ ] 移除旧 `_entry_marks`、`_MARK_TYPES`、`_MARK_CYCLE`、`_MARK_COLORS`、`_MARK_LABELS`

## 数据流

```
初始化 → _ensure_default_labels() → 3 个默认标签

「管理标签」→ _LabelManagerDialog
  ├─ 标签列表: [名称] [颜色圆点] [编辑] [删除]
  ├─ 添加: 输入名称+选颜色 → label_id = uuid4().hex[:8]
  ├─ 编辑: 修改名称/颜色 → _label_library[id] 更新
  └─ 删除: 确认 → del _label_library[id] + 清理 _entry_labels

关闭对话框 → 更新 _label_library → 通知标签筛选行重建
```

## 关键接口

```python
# 替代 _entry_marks
_label_library: dict[str, dict]
# {"a1b2c3d4": {"name": "待处理", "color": "#2196F3"}, ...}

_entry_labels: dict[str, set[str]]
# {"entry_id_123": {"a1b2c3d4", "e5f6g7h8"}, ...}

# 预设标签颜色
_PRESET_COLORS = ["#2196F3", "#FF9800", "#4CAF50", "#F44336", "#9C27B0", "#00BCD4", "#795548", "#607D8B"]

def _ensure_default_labels(self):
    """若无标签则创建 3 个默认标签"""
    if not self._label_library:
        for name, color in [("待处理", "#2196F3"), ("有疑问", "#FF9800"), ("已确认", "#4CAF50")]:
            lid = _new_label_id()
            self._label_library[lid] = {"name": name, "color": color}

class _LabelManagerDialog(QDialog):
    def __init__(self, label_library: dict, parent=None):
        ...
    def get_label_library(self) -> dict:
        return self._labels  # 修改后的标签库
```

## 实现步骤

### 步骤 1: 定义数据模型

**涉及文件**: `src/transbridge/ui/workbench/step2.py`（修改）

**实现要点**:
- 在 `Step2PreviewWidget.__init__` 中新增 `_label_library: dict[str, dict]` 和 `_entry_labels: dict[str, set[str]]`
- 新增 `_ensure_default_labels()` 方法
- 移除 `_entry_marks`、`_MARK_TYPES`、`_MARK_CYCLE`、`_MARK_COLORS`、`_MARK_LABELS`

**边界条件**:
- `_entry_marks` 中已有的标记数据 → 迁移到 `_entry_labels`？（建议不迁移，标签系统是全新起点）
- 集合刷新 → `_entry_labels` 清理失效 entry_id

**伪代码**:
```python
# 在 __init__ 中：
self._label_library: dict[str, dict] = {}  # label_id → {name, color}
self._entry_labels: dict[str, set[str]] = {}  # entry_id → set[label_id]
self._label_filters: set[str] = set()  # 替代 _mark_filters

def _ensure_default_labels(self):
    import uuid
    if not self._label_library:
        defaults = [
            ("待处理", "#2196F3"),
            ("有疑问", "#FF9800"),
            ("已确认", "#4CAF50"),
        ]
        for name, color in defaults:
            lid = uuid.uuid4().hex[:8]
            self._label_library[lid] = {"name": name, "color": color}
```

**测试策略**: 启动应用 → 无标签时自动创建 3 个默认标签

### 步骤 2: 创建 _LabelManagerDialog

**涉及文件**: 同上

**实现要点**:
- 创建 `_LabelManagerDialog(QDialog)` 类
- 布局：左侧 QListWidget 显示标签列表（名称 + 颜色圆点字符），右侧编辑区（名称 QLineEdit + 颜色按钮组 + 添加/删除按钮）
- 颜色选择：预设 8 色 QPushButton 网格

**边界条件**:
- 标签名为空 → 不允许添加
- 标签名重复 → 提示
- 删除最后一个标签 → 允许（标签库可以为空）

**伪代码**:
```python
class _LabelManagerDialog(QDialog):
    def __init__(self, label_library, parent=None):
        super().__init__(parent)
        self._labels = dict(label_library)  # 副本，确认后才写入
        self.setWindowTitle("管理标签")
        self.resize(400, 300)
        
        layout = QHBoxLayout(self)
        # 左侧列表
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_select)
        layout.addWidget(self._list)
        
        # 右侧编辑
        right = QVBoxLayout()
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("标签名称")
        right.addWidget(self._name_edit)
        
        # 颜色选择
        color_row = QHBoxLayout()
        for c in _PRESET_COLORS:
            btn = QPushButton()
            btn.setFixedSize(24, 24)
            btn.setStyleSheet(f"background: {c}; border-radius: 12px;")
            btn.clicked.connect(lambda checked, col=c: self._on_color_pick(col))
            color_row.addWidget(btn)
        right.addLayout(color_row)
        
        # 按钮
        btn_row = QHBoxLayout()
        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self._on_add)
        del_btn = QPushButton("删除")
        del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        right.addLayout(btn_row)
        
        layout.addLayout(right)
        self._refresh_list()
    
    def _refresh_list(self):
        self._list.clear()
        for lid, info in self._labels.items():
            self._list.addItem(f"● {info['name']}")
            # 设置颜色
            self._list.item(self._list.count() - 1).setForeground(QColor(info["color"]))
```

**测试策略**: 打开对话框 → 添加标签 → 关闭 → 再次打开确认保留

### 步骤 3: 工具栏集成

**涉及文件**: 同上

**实现要点**:
- 在 `_mark_tags_widget` 附近或标记筛选行添加「管理标签」按钮
- 连接 `_on_manage_labels()` 方法
- 对话框确认后更新 `_label_library`，调用 `_build_label_tags()` 重建筛选行

**边界条件**:
- 标签库变更后 → 需要重建标签筛选行、AI翻译窗口的标签维度
- 删除标签后 → 筛选行移除该标签按钮

**伪代码**:
```python
def _on_manage_labels(self):
    dlg = _LabelManagerDialog(self._label_library, self)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        new_library = dlg.get_label_library()
        # 清理已删除标签的引用
        removed_ids = set(self._label_library) - set(new_library)
        for entry_id, labels in self._entry_labels.items():
            labels.difference_update(removed_ids)
        self._label_library = new_library
        self._build_label_tags()
        self._populate_table()
```

**测试策略**: 添加标签 → 确认筛选行出现新标签按钮

### 步骤 4: 移除旧标记代码

**涉及文件**: 同上

**实现要点**:
- 删除 `_entry_marks` 属性
- 删除 `_MARK_TYPES`, `_MARK_CYCLE`, `_MARK_COLORS`, `_MARK_LABELS` 常量
- 删除 `_build_mark_tags()`, `_on_mark_tag_clicked()` 方法
- `_update_count_label` 和 `_apply_all_filters` 暂保留旧逻辑（S3 重写）

**边界条件**:
- `_on_cell_clicked` 中的标记切换逻辑暂时注释（S2 重写）
- `get_selected_entries()` 暂时保留（S4 适配后调整）

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/workbench/step2.py` | 修改 | 数据模型 + 对话框 + 工具栏 + 移除旧代码 |

## 风险与注意事项

- **风险 1**: 默认标签自动创建可能与用户自定义标签偏好冲突。缓解：默认标签仅在没有标签时创建，用户可删除
- **注意 1**: `_LabelManagerDialog` 操作的是 `_label_library` 副本，确认后才写回，取消不影响原数据
