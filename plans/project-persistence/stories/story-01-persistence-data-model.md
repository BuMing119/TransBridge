# Story 01: 持久化数据模型与文件读写

**所属方案**: `plans/project-persistence/plan.md`
**技术模块**: `src/transbridge/persistence/` (新建)
**状态**: 已确认
**创建日期**: 2026-05-08

## 前置依赖

### 上游 Story
无（Phase 1 首个 Story）

### 引用的架构决策
- ADR-006: JSON 文件存储、三层数据分层、Variant 模型、序列化策略

## 验收标准

- [ ] `WorkspaceState` 类正确读写 `workspace.json`
- [ ] `ProjectHandle` 类正确读写 `project.json`
- [ ] `VariantStore` 类正确读写 `current.json`（translations、labels、label_library）
- [ ] `VariantStore.apply_to(entries)` 将缓存数据写入 TranslationEntry 列表
- [ ] `VariantStore.collect_from(entries, entry_labels, label_library)` 从运行时收集数据
- [ ] 文件不存在时返回安全默认值，不抛异常
- [ ] `data/projects/` 目录不存在时自动创建

## 数据流

```
读取流程（启动/版本切换）:
  disk: workspace.json ──→ WorkspaceState.load()
  disk: project.json    ──→ ProjectHandle.load()
  disk: current.json    ──→ VariantStore.load()
                               │
  VariantStore.apply_to(entries)  ← 将 translation/label 注入内存中的 TranslationEntry
                               │
  AppContext 刷新 UI ←──────────┘

保存流程（自动保存/版本切换/关闭）:
  AppContext 运行时状态
       │
  VariantStore.collect_from(entries, entry_labels, label_library)
       │
  VariantStore.save(current.json) ──→ disk

原子写入:
  write .tmp → os.replace(.tmp, target)  （Windows 上原子性足够）
```

## 关键接口

### workspace.py

```python
class WorkspaceState:
    """管理 workspace.json 全局状态"""
    
    def __init__(self, path: Path):
        self._path = path
        self._data: dict = {}
    
    @classmethod
    def load(cls, path: Path) -> 'WorkspaceState':
        """从磁盘加载，文件不存在返回空模板"""
    
    def save(self) -> None:
        """写入磁盘，自动创建父目录"""
    
    # 属性访问
    @property
    def projects(self) -> dict[str, str]:
        """{project_name: project_json_path}"""
    
    @property
    def active_project(self) -> str | None:
        """当前活跃项目名"""
    
    @active_project.setter
    def active_project(self, name: str | None) -> None:
        ...
    
    @property
    def settings(self) -> dict:
        """save_behavior / auto_save_interval_minutes / write_back"""
    
    @property
    def last_session(self) -> dict:
        """上次会话状态（project/variant/filters）"""
    
    @classmethod
    def _empty_template(cls) -> dict:
        """返回 workspace.json 空模板"""
```

### project.py

```python
class ProjectHandle:
    """管理 project.json 项目配置"""
    
    def __init__(self, path: Path):
        self._path = path
        self._data: dict = {}
    
    @classmethod
    def create(cls, base_dir: Path, name: str, sources: list[dict]) -> 'ProjectHandle':
        """创建新项目目录和 project.json"""
    
    @classmethod
    def load(cls, path: Path) -> 'ProjectHandle':
        """从磁盘加载，文件不存在抛 FileNotFoundError（调用方保证存在）"""
    
    def save(self) -> None:
        ...
    
    @property
    def name(self) -> str: ...
    
    @property
    def sources(self) -> list[dict]:
        """[{"key": ..., "type": "esp"/"eet"/"xt", "path": ...}]"""
    
    @property
    def variants(self) -> list[dict]:
        """[{"name": ..., "created": ..., "copied_from": ...}]"""
    
    @property
    def active_variant(self) -> str: ...
    
    @active_variant.setter
    def active_variant(self, name: str) -> None: ...
    
    def add_variant(self, name: str, copied_from: str | None = None) -> None:
        """追加版本到 variants 列表"""
    
    def remove_variant(self, name: str) -> None:
        """从列表移除，不删除磁盘文件"""
    
    def variant_dir(self, variant_name: str) -> Path:
        """返回 {project_dir}/{variant_name}/ 路径"""
    
    @property
    def project_dir(self) -> Path:
        """project.json 所在目录"""
```

### variant_store.py

```python
class VariantStore:
    """管理 current.json 翻译数据缓存"""
    
    def __init__(self, path: Path):
        self._path = path
        self.translations: dict[str, str] = {}     # entry_id → translation
        self.labels: dict[str, set[str]] = {}       # entry_id → set[label_id]
        self.label_library: dict[str, dict] = {}    # label_id → {name, color}
        self.dirty: bool = False
    
    @classmethod
    def load(cls, path: Path) -> 'VariantStore':
        """从磁盘加载，文件不存在返回空 VariantStore"""
    
    def save(self) -> None:
        """原子写入：写 .tmp → os.replace"""
    
    def apply_to(self, entries: list[TranslationEntry]) -> int:
        """
        将缓存的 translation 和 label 注入 TranslationEntry 列表。
        返回更新的条目数。entry_id 不匹配的跳过。
        """
    
    def collect_from(
        self,
        entries: list[TranslationEntry],
        entry_labels: dict[str, set[str]],
        label_library: dict[str, dict],
    ) -> None:
        """从运行时状态收集数据到缓存，设置 dirty=True"""
    
    def save_snapshot(self, snapshot_dir: Path, name: str) -> None:
        """另存为 snapshots/{name}.json"""
    
    @classmethod
    def load_snapshot(cls, snapshot_path: Path) -> 'VariantStore':
        """从快照文件加载"""
    
    @staticmethod
    def list_snapshots(snapshot_dir: Path) -> list[dict]:
        """列出快照目录下所有快照文件及其元数据"""
    
    @staticmethod
    def delete_snapshot(snapshot_path: Path) -> None:
        """删除指定快照文件"""
```

## 实现步骤

### 步骤 1: 创建 persistence 包 + WorkspaceState

**涉及文件**: `src/transbridge/persistence/__init__.py`（新建）, `src/transbridge/persistence/workspace.py`（新建）

**实现要点**:
- `WorkspaceState.load(path)` — JSON 文件读取，文件不存在返回 `_empty_template()`
- `WorkspaceState.save()` — 原子写入（写 .tmp → os.replace）
- 属性访问使用 `self._data.get()` 提供安全默认值
- `_empty_template()` 返回含 `version: 1` 的空模板

**边界条件**:
- 路径不存在 → `Path.mkdir(parents=True, exist_ok=True)`
- JSON 解析失败 → 返回空模板 + 打印警告
- 并发写入 → 不处理（单用户桌面应用）

**伪代码**:
```python
class WorkspaceState:
    @classmethod
    def load(cls, path: Path) -> 'WorkspaceState':
        ws = cls(path)
        if path.exists():
            try:
                ws._data = json.loads(path.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, OSError):
                ws._data = cls._empty_template()
        else:
            ws._data = cls._empty_template()
        return ws
    
    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(self._path)
```

**测试策略**:
- 单测：空文件 → 返回空模板
- 单测：正常读写往返
- 单测：JSON 格式错误 → 降级返回空模板

---

### 步骤 2: 实现 ProjectHandle

**涉及文件**: `src/transbridge/persistence/project.py`（新建）

**实现要点**:
- `ProjectHandle.create(base_dir, name, sources)` — 创建 `data/projects/{name}/` 目录 + `project.json`
- `ProjectHandle.load(path)` — 读取已有 project.json
- 源文件路径保持绝对路径（用户环境固定）
- `add_variant()` / `remove_variant()` 维护 variants 列表

**边界条件**:
- create 时项目目录已存在 → 抛 FileExistsError（调用方先检查）
- sources 为空列表 → 允许（先创建空项目，后续添加源文件）
- variant 重名 → add_variant 抛 ValueError

**伪代码**:
```python
class ProjectHandle:
    @classmethod
    def create(cls, base_dir: Path, name: str, sources: list[dict]) -> 'ProjectHandle':
        proj_dir = base_dir / name
        proj_dir.mkdir(parents=True, exist_ok=False)
        ph = cls(proj_dir / 'project.json')
        ph._data = {
            'name': name,
            'created': datetime.now().isoformat(),
            'sources': sources,
            'variants': [],
            'active_variant': None,
            'esp_key_format': True,
        }
        ph.save()
        return ph
```

**测试策略**:
- 单测：create → load → 数据一致
- 单测：add_variant / remove_variant
- 单测：重复项目名 → FileExistsError

---

### 步骤 3: 实现 VariantStore

**涉及文件**: `src/transbridge/persistence/variant_store.py`（新建）

**实现要点**:
- `VariantStore.load(path)` — 读取 current.json，按 ADR-006 结构解析
- `VariantStore.save()` — 原子写入
- `apply_to(entries)` — O(n) 遍历，entry_id 匹配时设置 translation/标签
- `collect_from(entries, entry_labels, label_library)` — 收集并设置 dirty=True
- 快照方法：save_snapshot / load_snapshot / list_snapshots / delete_snapshot

**边界条件**:
- current.json 中 entry_id 在 entries 中不存在 → 跳过（源文件变更导致）
- entries 中新条目（current.json 中无对应 translation）→ translation 保持空字符串
- labels 序列化：set → JSON array

**伪代码**:
```python
class VariantStore:
    def apply_to(self, entries: list[TranslationEntry]) -> int:
        updated = 0
        for entry in entries:
            if not entry.id:
                continue
            if entry.id in self.translations:
                entry.translation = self.translations[entry.id]
                updated += 1
        return updated
    
    def collect_from(self, entries, entry_labels, label_library) -> None:
        self.translations = {}
        for e in entries:
            if e.id and e.translation:
                self.translations[e.id] = e.translation
        self.labels = {k: set(v) for k, v in entry_labels.items()}
        self.label_library = {k: dict(v) for k, v in label_library.items()}
        self.dirty = True
    
    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'variant': self._path.parent.name,
            'updated': datetime.now().isoformat(),
            'translations': self.translations,
            'labels': {k: list(v) for k, v in self.labels.items()},
            'label_library': self.label_library,
        }
        tmp = self._path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(self._path)
        self.dirty = False
```

**测试策略**:
- 单测：apply_to 正确覆盖 translation 字段
- 单测：collect_from → save → load 往返一致性
- 单测：空文件 load → 返回空 VariantStore
- 单测：entry_id 不匹配时 apply_to 不修改

---

### 步骤 4: 创建 __init__.py + 包导出

**涉及文件**: `src/transbridge/persistence/__init__.py`（修改）

**实现要点**:
- 导出三个核心类：WorkspaceState, ProjectHandle, VariantStore
- 定义 `PERSISTENCE_ROOT = Path("data/projects")` 常量

**边界条件**:
- PERSISTENCE_ROOT 使用相对路径，开发环境和 PyInstaller 打包环境均适用

**测试策略**:
- 导入测试：`from src.transbridge.persistence import WorkspaceState, ProjectHandle, VariantStore`

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/persistence/__init__.py` | 新建 | 包初始化 + 导出 |
| `src/transbridge/persistence/workspace.py` | 新建 | WorkspaceState 类 |
| `src/transbridge/persistence/project.py` | 新建 | ProjectHandle 类 |
| `src/transbridge/persistence/variant_store.py` | 新建 | VariantStore 类 |

## 风险与注意事项

- **原子写入在 Windows 上**: `os.replace` 在 NTFS 上是原子操作，但 FAT32/exFAT 不是。TransBridge 目标平台 NTFS，可接受
- **JSON 编码中文**: 使用 `ensure_ascii=False` 保持可读性，但注意 git diff 可能显示 Unicode 差异
- **VariantStore 与 labels 耦合**: labels 的持久化 key 是 entry_id，与 Step2 的 `_entry_labels` 格式一致，不用转换
