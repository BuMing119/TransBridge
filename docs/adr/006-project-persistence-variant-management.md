# ADR-006: 项目持久化与翻译版本管理

**状态**: 已接受
**日期**: 2026-05-08
**对应方案**: `plans/project-persistence/plan.md`（待创建）

## 背景

TransBridge v0.11+ 所有运行时状态（翻译数据、标签、筛选状态、集合列表）仅存在于 AppContext 内存中，应用关闭即全部丢失。用户每次启动需重新解析 ESP/EET/XT 文件、重建翻译环境。

同时，用户存在「同一 Mod 维护多套术语变体」的需求：例如 Dragonborn 汉化需要同时交付「和光术语版」和「ank术语版」，两个版本仅术语不同、源码相同，需在同一个项目内管理。

当前架构缺少：
- 项目级持久化层（关闭→重启状态丢失）
- 翻译版本（Variant）概念（无法区分术语变体）
- 快照/版本历史（无法回退修改）
- 跨会话的工作区恢复

## 决策

### 1. 存储格式：JSON 文件

**选择 JSON**。理由：
- 零外部依赖（Python 标准库 `json`）
- 人类可读、Git 可 diff，方便用户手动检查/编辑译文变更
- 与现有格式一致：DSD JSON（导入导出）、ParaTranz API（平台数据交互）
- TransBridge 词条量级通常为数百至数万条，JSON 文件 I/O 完全可接受
- SQLite 引入不必要的复杂度（schema 迁移、二进制不可读）

### 2. 数据分层：三层架构

```
┌─────────────────────────────────────────────────────────┐
│ Layer 1 — 持久化存储（磁盘）                              │
│                                                         │
│  workspace.json          全局状态（项目列表、活跃引用）     │
│  data/projects/          项目数据根目录                   │
│    └── {project}/                                       │
│          ├── project.json    项目配置                    │
│          ├── {variant}/                                 │
│          │     ├── current.json   当前译文+标签          │
│          │     └── snapshots/     命名快照               │
│          └── {variant}/                                 │
│                ├── current.json                         │
│                └── snapshots/                           │
├─────────────────────────────────────────────────────────┤
│ Layer 2 — 运行时状态（内存，AppContext 扩展）             │
│                                                         │
│  WorkspaceState   项目列表、活跃项目+版本引用              │
│  VariantStore     当前版本的译文缓存 + 标签缓存 + 脏标记   │
│  CollectionSlot   源文件解析结果（现有，不变）             │
│  LabelLibrary     标签库（现有，扩展为按版本持久化）       │
├─────────────────────────────────────────────────────────┤
│ Layer 3 — 视图状态（每次重建或 QSettings）                │
│                                                         │
│  筛选状态、搜索文本、表格滚动位置 → QSettings             │
│  UI 布局（窗口位置/大小/DockWidget 状态）→ QSettings       │
└─────────────────────────────────────────────────────────┘
```

**关键原则**：源文件解析结果（TranslationEntry 的 key/original/context）**不持久化**——每次启动重新解析源文件。仅持久化用户产生的数据（translation + labels + label_library）。

### 3. 版本（Variant）数据模型

Variant 是翻译数据的分组键，影响**存储路径**和**运行时视图**，但不影响源文件解析逻辑。

**目录结构**：
```
data/projects/dragonborn/
├── project.json                # 项目配置
├── 和光术语版/
│   ├── current.json            # 当前译文+标签
│   └── snapshots/
│       ├── 2026-05-08-v1.json
│       └── 2026-05-10-review.json
└── ank术语版/
    ├── current.json
    └── snapshots/
```

**project.json 结构**：
```json
{
  "name": "Dragonborn Translation",
  "created": "2026-05-08T12:00:00",
  "sources": [
    {"key": "dragonborn_esp", "type": "esp", "path": "C:/.../Dragonborn.esp"},
    {"key": "dragonborn_eet", "type": "eet",  "path": "C:/.../Dragonborn_eet.xml"}
  ],
  "variants": [
    {"name": "和光术语版", "created": "2026-05-08T12:00:00", "copied_from": null},
    {"name": "ank术语版",  "created": "2026-05-08T12:05:00", "copied_from": "和光术语版"}
  ],
  "active_variant": "和光术语版",
  "esp_key_format": true
}
```

**current.json 结构**：
```json
{
  "variant": "和光术语版",
  "updated": "2026-05-08T14:30:00",
  "translations": {
    "0x00012345_NPC_:FULL": "西格德",
    "0x00012346_BOOK:CNAM": "龙裔之书"
  },
  "labels": {
    "0x00012345_NPC_:FULL": ["a1b2c3d4"],
    "0x00012346_BOOK:CNAM": ["a1b2c3d4", "e5f6g7h8"]
  },
  "label_library": {
    "a1b2c3d4": {"name": "待复核", "color": "#FF9800"},
    "e5f6g7h8": {"name": "术语确认", "color": "#2196F3"}
  }
}
```

### 4. workspace.json 全局状态

```json
{
  "version": 1,
  "active_project": "dragonborn",
  "projects": {
    "dragonborn": "data/projects/dragonborn/project.json"
  },
  "settings": {
    "save_behavior": "prompt",
    "auto_save_interval_minutes": 5,
    "auto_save_on_edit": true,
    "write_back": {
      "mode": "current_variant",
      "output_base_dir": null
    }
  },
  "last_session": {
    "project": "dragonborn",
    "variant": "和光术语版",
    "step2_filter_stage": [],
    "step2_filter_category": [],
    "step2_search_text": ""
  }
}
```

### 5. AppContext 扩展

在现有 `AppContext` 上添加持久化相关属性，不新建独立的 ProjectManager 层：

```python
class AppContext:
    # 现有属性（不变）
    _slots: dict[str, CollectionSlot]
    _active_key: str | None

    # 新增 — 持久化相关
    _workspace: dict | None           # workspace.json 内容
    _active_project: str | None       # 当前项目名
    _active_variant: str | None       # 当前版本名
    _variant_store: VariantStore | None  # 当前版本的译文/标签缓存
    _dirty: bool                      # 是否有未保存修改

    # 新增信号
    project_list_changed = pyqtSignal()
    variant_changed = pyqtSignal(str)   # variant_name
```

**VariantStore** 是一个轻量运行时缓存层：
```python
class VariantStore:
    translations: dict[str, str]           # entry_id → translation
    labels: dict[str, set[str]]            # entry_id → set[label_id]
    label_library: dict[str, dict]         # label_id → {name, color}
    dirty: bool                            # 是否有未保存修改
    
    def apply_to(self, entries: list[TranslationEntry]) -> None: ...
    def collect_from(self, entries: list[TranslationEntry], 
                     entry_labels: dict, label_library: dict) -> None: ...
    def save(self, path: Path) -> None: ...
    @classmethod
    def load(cls, path: Path) -> 'VariantStore': ...
```

### 6. 序列化策略

| TranslationEntry 字段 | 是否持久化 | 原因 |
|----------------------|-----------|------|
| `id` | 否（作为 key 使用） | 从源文件解析，每次重建 |
| `key` | 否 | 从源文件解析 |
| `original` | 否 | 从源文件解析 |
| `translation` | **是** | 用户产生数据 |
| `stage` | 否（从 translation 推导） | 有译文→1，无译文→0 |
| `context` | 否 | 从源文件解析 |
| `form_id_with_plugin` | 否 | 从源文件解析 |
| `string_id` | 否 | 从源文件解析 |
| `dsd_type` | 否 | 从源文件解析 |

**标签数据**独立于 TranslationEntry 持久化：`labels`（entry_id → [label_id]）和 `label_library`（label_id → {name, color}）存储在 `current.json` 中，通过 entry_id 关联。

### 7. 快照设计

快照是 `current.json` 的完整时间点拷贝，存储在 `snapshots/{name}.json`。快照内容与 `current.json` 结构完全相同（translations + labels + label_library + meta），确保快照加载即 `current.json` 加载的代码路径复用。

快照按**版本粒度**操作——一个快照包含该版本下所有源文件的翻译数据。

### 8. 写回策略

写回按版本分开执行，输出到独立子目录：

```
用户选择「写回当前版本(和光术语版)」:
  → {output_dir}/和光术语版/plugin_chinese.strings
  → {output_dir}/和光术语版/plugin_eet.xml

用户选择「写回所有版本」:
  → {output_dir}/和光术语版/plugin_chinese.strings
  → {output_dir}/ank术语版/plugin_chinese.strings
```

不修改 ESP/EET/XML 源文件名，按版本目录区分。

### 9. 迁移路径

当前无持久化状态 → 新架构采用「首次启动创建」策略：

1. 启动时检测 `data/projects/` 目录是否存在
2. 不存在 → 以空白工作区启动，用户在 UI 中创建第一个项目
3. 存在 → 读取 `workspace.json`，恢复上次活跃的项目+版本
4. 若项目源文件路径变更（文件移动/重命名）→ 提示用户重新定位
5. 若 `current.json` 不存在 → 初始化空白版本状态

不提供从「无持久化状态」到「新架构」的自动迁移——旧版本用户手动重建项目。

## 备选方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| SQLite 存储 | ACID 事务、并发安全、SQL 查询 | 二进制不可读、需 schema 迁移、增加复杂度、用户无法手动编辑 |
| 嵌入式 KV 存储（LMDB等） | 高性能、事务支持 | 引入 C 扩展依赖、Windows 兼容性风险、不可读 |
| 云同步（Firebase等） | 多设备同步 | 网络依赖、隐私风险、成本、远超需求范围 |

## 影响

### 目录变更

```
新增:
  data/projects/                    # 项目持久化根目录
  src/transbridge/persistence/      # 持久化模块
    ├── __init__.py
    ├── workspace.py                # WorkspaceState 管理
    ├── variant_store.py            # VariantStore 类
    └── project.py                  # ProjectHandle + project.json 读写

修改:
  src/transbridge/ui/context.py     # AppContext 扩展（workspace/active_variant/variant_store）
  src/transbridge/ui/main_window.py # 启动时恢复工作区
  src/transbridge/ui/workbench/     # UI 入口（项目/版本切换控件）
```

### 接口变更

- AppContext 新增属性：`workspace`, `active_project`, `active_variant`, `variant_store`
- AppContext 新增信号：`project_list_changed`, `variant_changed`
- CollectionSlot 不变（保持纯运行时角色）
- TranslationEntry 不变（不添加序列化方法，由 VariantStore 独立管理持久化）

### 依赖变更

无新增外部依赖。使用 Python 标准库 `json`, `pathlib`, `shutil`（ZIP 打包用 `zipfile`）。

### 更新：2026-08-18 — 状态所有权与 V2 持久化（已接受）

JSON、Project/Variant 和快照资产继续保留；以下旧决策被 [ADR-018](018-project-session-persistence-v2.md) 部分取代：

- 不再由 AppContext 直接拥有 Project/Variant 持久化状态，AppContext 改为 GUI projection/facade；
- Variant 不再以非空译文 overlay 方式应用，改为完整快照和 replace materialization；
- Stage、空译文、labels、provenance、revision、source namespace/fingerprint 必须持久化，不得从 translation 推导 Stage；
- workspace/project/variant 文件必须带 schema version、校验、备份、迁移和 quarantine 语义；
- 项目/版本切换采用两阶段提交，失败不提前改变 active pointer。
