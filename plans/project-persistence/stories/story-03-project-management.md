# Story 03: 项目管理

**所属方案**: `plans/project-persistence/plan.md`
**技术模块**: `src/transbridge/ui/` (修改)
**状态**: 已确认
**创建日期**: 2026-05-08

## 前置依赖

### 上游 Story
- Story-02（同 plan）：AppContext 已扩展 workspace/active_project/variant_store 属性

### 引用的架构决策
- ADR-006: workspace.json 全局状态结构、项目目录布局

## 验收标准

- [ ] 首次启动无 workspace.json 时，以空白状态启动，不报错
- [ ] 「文件 → 新建项目」创建项目目录和 project.json
- [ ] 「文件 → 打开项目」加载已有项目
- [ ] 项目切换时自动保存当前项目状态
- [ ] 工作台工具栏显示当前项目名称
- [ ] 无项目时相关操作（解析/上传/下载/写回）正常禁用

## 数据流

```
启动:
  WorkspaceState.load("data/workspace.json")
    ├─ 文件不存在 → 空白模板，不创建文件
    ├─ active_project 有值 → ProjectHandle.load(project_json_path)
    │     ├─ 文件存在 → 解析源文件列表 → 加载 VariantStore → 恢复 UI
    │     └─ 文件不存在 → 清除 active_project，提示用户重新打开
    └─ active_project 无值 → 空白启动，显示"无项目"

新建项目:
  用户输入名称 + 选择源文件
    → ProjectHandle.create(base_dir, name, sources)
    → workspace.projects[name] = path
    → workspace.active_project = name
    → workspace.save()
    → 解析源文件 → 创建默认版本 → 刷新 UI

切换项目:
  保存当前项目（VariantStore.save + ProjectHandle.save）
    → workspace.active_project = new_name
    → workspace.save()
    → 加载新项目 → 刷新 UI
```

## 关键接口

### _project_bar.py

```python
class ProjectBar(QWidget):
    """工作台顶部项目工具栏"""
    
    project_changed = pyqtSignal(str)   # 切换项目信号
    new_project = pyqtSignal()          # 新建项目信号
    open_project = pyqtSignal()         # 打开项目信号
    
    def __init__(self, ctx: AppContext, parent=None): ...
    def set_project_name(self, name: str | None): ...
    def set_variant_list(self, variants: list[str], active: str): ...
    def refresh(self): ...
```

### main_window.py 新增方法

```python
class MainWindow:
    def _init_workspace(self) -> None:
        """启动时读取 workspace.json"""
    
    def _restore_session(self) -> None:
        """恢复上次项目+版本"""
    
    def _on_new_project(self) -> None:
        """弹出新建项目对话框 → 创建 project.json + 目录"""
    
    def _on_open_project(self) -> None:
        """弹出选择项目目录 → 加载 project.json"""
    
    def _switch_project(self, project_name: str) -> None:
        """保存当前 → 加载新项目 → 解析源文件 → 刷新 UI"""
    
    def _save_current_project(self) -> None:
        """VariantStore.save() + ProjectHandle.save()"""
```

## 实现步骤

### 步骤 1: 创建 ProjectBar 组件

**涉及文件**: `src/transbridge/ui/workbench/_project_bar.py`（新建）

**实现要点**:
- 水平布局：[项目: 名称 ▼] [版本: 名称 ▼] [管理▼]
- 项目下拉列出 workspace 中所有项目 + "新建项目…" + "打开项目…"
- 无项目时显示 "[无项目] 新建 | 打开"
- 信号连接：选中项目 → emit project_changed

**边界条件**:
- workspace 为 None → 显示"无项目"
- projects 为空 → 下拉仅显示"新建…""打开…"

**测试策略**:
- 手动验证：无项目状态 UI 正确渲染
- 手动验证：创建项目后工具栏更新

### 步骤 2: 新建/打开项目菜单

**涉及文件**: `src/transbridge/ui/main_window.py`（修改）

**实现要点**:
- 「文件 → 新建项目」→ 弹出 QDialog：输入项目名 + 选择 ESP/EET 文件（可选）→ ProjectHandle.create() → 解析源文件
- 「文件 → 打开项目」→ QFileDialog 选择 project.json → ProjectHandle.load() → 恢复会话
- 项目菜单项与操作菜单联动（无项目时禁用操作项）

**边界条件**:
- 新建时项目名已存在 → 提示冲突
- 打开时 project.json 格式错误 → 提示文件损坏
- 首次启动无 workspace.json → _init_workspace() 返回空白模板

**伪代码**:
```python
def _on_new_project(self):
    name, ok = QInputDialog.getText(self, "新建项目", "项目名称:")
    if not ok or not name.strip():
        return
    name = name.strip()
    base_dir = Path("data/projects")
    if (base_dir / name).exists():
        QMessageBox.warning(self, "冲突", f"项目「{name}」已存在")
        return
    # 可选：选择初始源文件
    esp_paths, _ = QFileDialog.getOpenFileNames(...)
    sources = [{"key": Path(p).stem, "type": "esp", "path": p} for p in esp_paths]
    ph = ProjectHandle.create(base_dir, name, sources)
    # 创建默认版本
    default_dir = ph.variant_dir("默认")
    default_dir.mkdir(parents=True)
    ph.add_variant("默认")
    ph.active_variant = "默认"
    ph.save()
    # 注册到 workspace
    ws = self._ctx.workspace or WorkspaceState.load(Path("data/workspace.json"))
    ws.projects[name] = str(ph._path)
    ws.active_project = name
    ws.save()
    self._ctx.workspace = ws
    self._ctx.active_project = ph
    # 解析源文件
    self._parse_and_load(ph)
```

### 步骤 3: 项目切换逻辑

**涉及文件**: `src/transbridge/ui/main_window.py`（修改）

**实现要点**:
- `_switch_project(name)`: 保存当前 → 加载新项目 → 解析源文件 → apply VariantStore → 刷新 Step2
- `_save_current_project()`: ctx.variant_store.collect_from() → save() → project.save()
- 切换前检查脏标记，脏则先保存

**边界条件**:
- 源文件路径不存在 → 提示用户重新定位，不崩溃
- 项目无版本 → 自动创建"默认"版本

### 步骤 4: 集成 ProjectBar 到 WorkbenchWidget

**涉及文件**: `src/transbridge/ui/workbench/widget.py`（修改）

**实现要点**:
- WorkbenchWidget 顶部添加 ProjectBar
- ProjectBar 信号连接到 MainWindow 对应槽函数
- 无项目时 Step2 面板显示"请新建或打开项目"

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/workbench/_project_bar.py` | 新建 | 项目工具栏组件 |
| `src/transbridge/ui/main_window.py` | 修改 | 新建/打开项目菜单 + 切换逻辑 |
| `src/transbridge/ui/workbench/widget.py` | 修改 | 嵌入 ProjectBar |

## 风险与注意事项

- **首次启动体验**: workspace.json 不存在时不应弹出错误，静默以空白状态启动
- **项目名 vs 目录名**: 项目名和目录名保持一致，避免歧义。project.json 中的 `name` 字段是权威来源
