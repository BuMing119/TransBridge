# Story 06: 快照操作

**所属方案**: `plans/project-persistence/plan.md`
**技术模块**: `src/transbridge/persistence/`, `src/transbridge/ui/`
**状态**: 已确认
**创建日期**: 2026-05-08

## 前置依赖

### 上游 Story
- Story-05（同 plan）：自动保存/恢复已实现

### 引用的架构决策
- ADR-006: 快照为 current.json 完整拷贝，按版本粒度

## 验收标准

- [ ] 「版本 → 另存为快照」将 current.json 复制到 snapshots/{name}.json
- [ ] 「版本 → 加载快照」列出当前版本所有快照，选择后恢复
- [ ] 快照包含完整数据：translations + labels + label_library + 时间戳
- [ ] 加载快照前提示用户保存当前修改
- [ ] 快照删除

## 关键接口

```python
# variant_store.py 快照方法（S01 已定义接口）
class VariantStore:
    def save_snapshot(self, snapshot_dir: Path, name: str) -> None:
        """copy current.json → snapshots/{name}.json"""
    
    @classmethod
    def load_snapshot(cls, snapshot_path: Path) -> 'VariantStore':
        """从快照文件加载"""
    
    @staticmethod
    def list_snapshots(snapshot_dir: Path) -> list[dict]:
        """列出快照: [{name, path, timestamp, size}]"""
    
    @staticmethod
    def delete_snapshot(snapshot_path: Path) -> None:
        """删除快照文件"""

# _snapshot_dialog.py
class SnapshotDialog(QDialog):
    """快照管理对话框：列表 + 加载/删除按钮"""
```

## 实现步骤

### 步骤 1: 快照 CRUD 方法（VariantStore）

**涉及文件**: `src/transbridge/persistence/variant_store.py`（修改）

**实现要点**:
- `save_snapshot(snapshot_dir, name)`: 复制 current.json 到 snapshots/{name}.json，文件名格式 `{timestamp}-{name}.json`
- `load_snapshot(path)`: 等同于 VariantStore.load(path)
- `list_snapshots(dir)`: glob `*.json`，解析每个文件的 updated 字段作为时间戳
- `delete_snapshot(path)`: Path.unlink()

**边界条件**:
- snapshot_dir 不存在 → 自动创建
- 同名快照 → 追加时间戳后缀避免冲突

### 步骤 2: 快照管理 UI

**涉及文件**: `src/transbridge/ui/workbench/_snapshot_dialog.py`（新建）

**实现要点**:
- 列表显示所有快照：名称、时间、文件大小
- [加载] 按钮 → 确认对话框（提示保存当前修改）→ VariantStore.load_snapshot
- [删除] 按钮 → 确认 → delete_snapshot → 刷新列表
- [另存为] 按钮 → 输入名称 → save_snapshot

### 步骤 3: 菜单集成

**涉及文件**: `src/transbridge/ui/main_window.py`（修改）

**实现要点**:
- 版本菜单下添加分隔线 + 快照相关菜单项
- 快捷键：Ctrl+Shift+S → 另存为快照

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/persistence/variant_store.py` | 修改 | 快照方法实现 |
| `src/transbridge/ui/workbench/_snapshot_dialog.py` | 新建 | 快照管理对话框 |
| `src/transbridge/ui/main_window.py` | 修改 | 快照菜单项 |

## 风险与注意事项

- **快照存储空间**: 每个快照是完整的 current.json 拷贝。大集合的快照可能几 MB。默认不限制快照数量，用户自行管理
