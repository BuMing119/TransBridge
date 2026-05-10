# 项目持久化与翻译版本管理

**对应需求**: FR8.1-FR8.12
**技术模块**: `src/transbridge/persistence/` (新建), `src/transbridge/ui/` (修改)
**业务域**: 数据持久化
**状态**: 已确认
**创建日期**: 2026-05-08
**确认日期**: 2026-05-08

## 功能边界

### 范围内
- workspace.json 全局状态管理（项目列表、活跃项目+版本、UI 布局、配置）
- project.json 项目配置（名称、源文件路径列表、版本列表）
- current.json 翻译数据持久化（translations + labels + label_library）
- 项目创建/切换/删除
- 翻译版本（Variant）创建/切换/复制（继承全部译文+标签）
- 启动自动恢复（项目→版本→数据→筛选状态）
- 自动保存（定时保存 + 操作触发防抖 2s + 脏标记）
- 命名快照（按版本粒度，另存/加载）
- .transbridge ZIP 单体文件导出/导入
- 版本写回（仅当前版本 / 全版本分目录输出）
- 版本切换时的保存行为配置（自动保存 / 提示确认）

### 范围外
- 术语转化工具（FR8 写回联动之外的术语批量替换）
- 多设备同步
- 协作翻译实时同步
- 旧版本（v0.11- 无持久化状态）的自动迁移

## Story 清单

### Story-01: 持久化数据模型与文件读写

**Phase**: 1 | **预估**: 4h | **状态**: ✅
**详细文档**: `stories/story-01-persistence-data-model.md`
**对应需求**: FR8.2, FR8.3, FR8.9

**验收标准**:
- [ ] `WorkspaceState` 类正确读写 `workspace.json`（项目列表、活跃引用、配置）
- [ ] `ProjectHandle` 类正确读写 `project.json`（名称、源文件路径、版本列表）
- [ ] `VariantStore` 类正确读写 `current.json`（translations、labels、label_library）
- [ ] `VariantStore.apply_to(entries)` 将缓存的 translation/label 写入 TranslationEntry 列表
- [ ] `VariantStore.collect_from(entries, entry_labels, label_library)` 从运行时状态收集数据
- [ ] 文件不存在时返回安全默认值（空字典/空列表），不抛异常
- [ ] `data/projects/` 目录不存在时自动创建

**实现步骤**:
1. 创建 `src/transbridge/persistence/` 包（`__init__.py`）→ 新建
2. 实现 `workspace.py`：`WorkspaceState` 类（load/save/属性访问）→ 新建
3. 实现 `project.py`：`ProjectHandle` 类（create/load/save/delete）→ 新建
4. 实现 `variant_store.py`：`VariantStore` 类（load/save/apply_to/collect_from/dirty 标记）→ 新建
5. `collect_from` 中处理 `entry_labels: dict[str, set[str]]` 序列化为 JSON 数组

**涉及文件**:
```
新建:
  src/transbridge/persistence/__init__.py
  src/transbridge/persistence/workspace.py
  src/transbridge/persistence/project.py
  src/transbridge/persistence/variant_store.py
```

---

### Story-02: AppContext 扩展

**Phase**: 1 | **预估**: 2h | **状态**: ✅
**详细文档**: `stories/story-02-appcontext-extension.md`
**对应需求**: FR8.1, FR8.9

**验收标准**:
- [ ] AppContext 新增 `workspace` 属性（WorkspaceState 实例）
- [ ] AppContext 新增 `active_project` 属性（ProjectHandle | None）
- [ ] AppContext 新增 `active_variant` 属性（str | None）
- [ ] AppContext 新增 `variant_store` 属性（VariantStore 实例）
- [ ] AppContext 新增 `project_list_changed` 信号
- [ ] AppContext 新增 `variant_changed` 信号（携带 variant_name）
- [ ] `collection_changed` 信号触发时自动设置 `variant_store.dirty = True`

**实现步骤**:
1. 在 `AppContext.__init__()` 中初始化新属性（默认 None）→ `context.py`
2. 添加 property getter/setter（含信号 emit）→ `context.py`
3. `active_variant` setter 中触发 `variant_changed` 信号 → `context.py`
4. 连接 `collection_changed` 到脏标记设置 → `context.py`

**涉及文件**:
```
修改:
  src/transbridge/ui/context.py
```

---

### Story-03: 项目管理

**Phase**: 2 | **预估**: 3h | **状态**: ✅
**详细文档**: `stories/story-03-project-management.md`
**对应需求**: FR8.1, FR8.2, FR8.7

**验收标准**:
- [ ] 首次启动无 workspace.json 时，以空白状态启动，不报错
- [ ] 「文件 → 新建项目」创建项目目录和 project.json
- [ ] 「文件 → 打开项目」加载已有项目
- [ ] 项目切换时自动保存当前项目状态
- [ ] 工作台工具栏显示当前项目名称
- [ ] 无项目时相关操作（解析/上传/下载/写回）正常禁用

**实现步骤**:
1. 启动时读取 `workspace.json`，恢复活跃项目 → `main_window.py`
2. 「文件 → 新建项目」菜单项 + 对话框（项目名称、初始源文件）→ `main_window.py`
3. 「文件 → 打开项目」菜单项 + 选择已有项目目录 → `main_window.py`
4. 项目切换逻辑：保存当前 → 加载新项目 → 重新解析源文件 → 加载版本数据 → 刷新 UI → `main_window.py`
5. 工作台工具栏项目标签（当前项目名 + 切换下拉）→ `workbench/` (新增或修改 widget)

**涉及文件**:
```
修改:
  src/transbridge/ui/main_window.py
  src/transbridge/ui/workbench/widget.py
新建:
  src/transbridge/ui/workbench/_project_bar.py   (项目工具栏组件)
```

---

### Story-04: 翻译版本管理

**Phase**: 2 | **预估**: 3h | **状态**: ✅
**详细文档**: `stories/story-04-variant-management.md`
**对应需求**: FR8.9, FR8.10, FR8.11

**验收标准**:
- [ ] 项目下至少有一个默认版本（项目创建时自动创建"默认"版本）
- [ ] 「版本 → 新建版本」创建空白新版本
- [ ] 「版本 → 复制版本」从当前版本继承全部译文+标签创建新版本
- [ ] 版本切换下拉显示所有版本，切换时更新 Step2 表格
- [ ] 版本切换保存行为：根据配置自动保存或弹出提示对话框
- [ ] 版本删除（至少保留一个版本）
- [ ] 标签库随版本切换（每个版本独立的 label_library）

**实现步骤**:
1. 项目工具栏添加版本下拉选择器 + 版本管理按钮 → `_project_bar.py` 或独立组件
2. 新建版本对话框：输入版本名称 → `_variant_dialog.py` (新建)
3. 复制版本：读取当前版本 current.json → 写入新版本目录 → `main_window.py`
4. 版本切换：VariantStore.save() → 加载新 VariantStore → apply_to(entries) → 刷新表格 → `context.py` + `step2.py`
5. 版本切换时保存行为配置读写 → `workspace.py` settings 字段
6. 版本删除：确认对话框 → 删除版本目录 → 切换到剩余版本 → `main_window.py`

**涉及文件**:
```
修改:
  src/transbridge/ui/context.py
  src/transbridge/ui/main_window.py
  src/transbridge/ui/workbench/step2.py
新建:
  src/transbridge/ui/workbench/_variant_dialog.py
```

---

### Story-05: 自动保存与启动恢复

**Phase**: 3 | **预估**: 3h | **状态**: ✅
**详细文档**: `stories/story-05-auto-save-and-restore.md`
**对应需求**: FR8.2, FR8.4, FR8.6

**验收标准**:
- [ ] 启动时读取 `workspace.json`，恢复上次活跃项目+版本
- [ ] 启动时重新解析源文件，加载对应版本的 `current.json` 到 VariantStore
- [ ] 启动时恢复 Step2 筛选状态（stage/category/search）
- [ ] 定时自动保存：可配置间隔（默认 5 分钟），后台 `QTimer` 触发
- [ ] 操作触发自动保存：编辑译文/修改标签后防抖 2 秒
- [ ] 脏标记管理：无修改时不触发保存
- [ ] 关闭应用时自动保存当前版本
- [ ] 手动保存入口：工具栏「保存」按钮 / Ctrl+S

**实现步骤**:
1. 启动恢复流程：`_restore_workspace()` 方法 → `main_window.py`
2. 恢复逻辑：读 workspace.json → 加载项目 → 解析源文件 → 加载 VariantStore → apply_to → 恢复筛选 → 刷新 UI
3. `AutoSaveManager` 类：QTimer 定时器 + 防抖定时器 → `main_window.py` 或独立文件
4. 脏标记集成：`variant_store.dirty` 检查 → 各修改点设置 dirty=True
5. Ctrl+S 快捷键 + 工具栏保存按钮 → `main_window.py`
6. 源文件哈希变更检测：启动时对比 project.json 中记录的哈希，变更时提示用户

**涉及文件**:
```
修改:
  src/transbridge/ui/main_window.py
  src/transbridge/ui/context.py
  src/transbridge/persistence/project.py   (源文件哈希记录)
```

---

### Story-06: 快照操作

**Phase**: 3 | **预估**: 2h | **状态**: ✅
**详细文档**: `stories/story-06-snapshot-operations.md`
**对应需求**: FR8.5

**验收标准**:
- [ ] 「版本 → 另存为快照」将当前 current.json 复制到 snapshots/{name}.json
- [ ] 「版本 → 加载快照」列出当前版本所有快照，选择后恢复
- [ ] 快照包含完整数据：translations + labels + label_library + 时间戳
- [ ] 加载快照前提示用户保存当前修改
- [ ] 快照删除

**实现步骤**:
1. `VariantStore.save_snapshot(name)` 方法 → `variant_store.py`
2. `VariantStore.load_snapshot(name)` 方法 → `variant_store.py`
3. `VariantStore.list_snapshots()` 方法 → `variant_store.py`
4. 快照 UI：版本菜单下「另存为快照」「加载快照」「管理快照」→ `main_window.py`
5. 快照选择对话框：列表 + 时间戳 + 加载/删除按钮 → `_snapshot_dialog.py` (新建)

**涉及文件**:
```
修改:
  src/transbridge/persistence/variant_store.py
  src/transbridge/ui/main_window.py
新建:
  src/transbridge/ui/workbench/_snapshot_dialog.py
```

---

### Story-07: .transbridge 单体项目文件

**Phase**: 3 | **预估**: 2h | **状态**: ✅
**详细文档**: `stories/story-07-transbridge-archive.md`
**对应需求**: FR8.8

**验收标准**:
- [ ] 「文件 → 导出 .transbridge」将整个项目目录打包为 ZIP
- [ ] ZIP 内容：project.json + 所有版本的 current.json + 所有快照
- [ ] 「文件 → 导入 .transbridge」解压到 data/projects/ 下
- [ ] 导入时检测项目名冲突，提示覆盖或重命名
- [ ] 支持双击 .transbridge 文件打开（通过命令行参数或文件关联）

**实现步骤**:
1. `export_transbridge(project_name, output_path)` 函数 → `persistence/` 或 `main_window.py`
2. `import_transbridge(file_path)` 函数 → 同上
3. 菜单项：「文件 → 导出 .transbridge」「文件 → 导入 .transbridge」→ `main_window.py`
4. 冲突检测对话框 → `main_window.py`
5. 命令行参数支持：启动时检测 `sys.argv` 中的 .transbridge 路径 → `main.py` 或 `app.py`

**涉及文件**:
```
修改:
  src/transbridge/ui/main_window.py
  src/transbridge/main.py
```

---

### Story-08: 版本写回

**Phase**: 4 | **预估**: 2h | **状态**: ✅
**详细文档**: `stories/story-08-variant-write-back.md`
**对应需求**: FR8.12

**验收标准**:
- [ ] 写回对话框新增「写回模式」选项：仅当前版本 / 所有版本
- [ ] 仅当前版本：现有行为，输出到用户指定目录
- [ ] 所有版本：每个版本输出到独立子目录（`{output_dir}/{variant_name}/`）
- [ ] 不修改 ESP/EET/XML 源文件名
- [ ] workspace.json 中保存写回配置（默认模式、输出目录）

**实现步骤**:
1. WriteCard 对话框扩展：添加「写回模式」单选组 → `cards/write_card.py`
2. 全版本写回逻辑：遍历 variant 列表 → 切换 VariantStore → 写回 → 恢复当前版本 → `cards/write_card.py` 或 `main_window.py`
3. 写回配置持久化到 `workspace.json` settings.write_back → `workspace.py`
4. 写回完成后恢复当前活跃版本的数据视图

**涉及文件**:
```
修改:
  src/transbridge/ui/workbench/cards/write_card.py
  src/transbridge/persistence/workspace.py
```

## 新建文件清单

```
src/transbridge/persistence/
├── __init__.py
├── workspace.py                  # Story-01: WorkspaceState
├── project.py                    # Story-01: ProjectHandle
└── variant_store.py              # Story-01: VariantStore

src/transbridge/ui/workbench/
├── _project_bar.py               # Story-03: 项目工具栏
├── _variant_dialog.py            # Story-04: 版本管理对话框
└── _snapshot_dialog.py           # Story-06: 快照管理对话框
```

## 需修改的现有文件

| 文件 | Story | 修改内容 |
|------|-------|---------|
| `src/transbridge/ui/context.py` | S02, S04, S05 | AppContext 扩展：workspace/active_project/active_variant/variant_store 属性 + 信号 + 脏标记 |
| `src/transbridge/ui/main_window.py` | S03, S04, S05, S07 | 启动恢复、项目菜单、版本菜单、自动保存、.transbridge 导入导出 |
| `src/transbridge/ui/workbench/widget.py` | S03 | 嵌入项目工具栏 |
| `src/transbridge/ui/workbench/step2.py` | S04 | 版本切换时刷新表格数据 |
| `src/transbridge/ui/workbench/cards/write_card.py` | S08 | 写回模式选择（当前版本/所有版本） |
| `src/transbridge/main.py` | S07 | 命令行参数支持 .transbridge 文件打开 |

## 架构依赖

- [ADR-006: 项目持久化与翻译版本管理](../../docs/adr/006-project-persistence-variant-management.md) — 存储格式、数据分层、Variant 模型、AppContext 扩展方案
- [ADR-002: Collection 数据中枢与双索引设计](../../docs/adr/002-collection-central-data-hub.md) — TranslationEntryCollection 保持不变，VariantStore 作为独立缓存层叠加
- [ADR-004: QThread + 信号总线异步模式](../../docs/adr/004-qthread-async-pattern.md) — 自动保存使用 QTimer，写回使用 ApiWorker 后台线程

## 风险与回退方案

| 风险 | 影响 | 回退方案 |
|------|------|---------|
| JSON 文件损坏（磁盘满/写入中断） | 丢失用户翻译数据 | 写入前先写 `.tmp` 文件，写入成功后原子 rename；自动保存保留最近 3 个备份 |
| 版本切换频繁导致性能问题 | 大集合切换卡顿 | VariantStore.apply_to() 仅更新 translation 字段，O(n) 遍历，10 万条以内可接受 |
| 源文件路径变更（用户移动文件） | 启动恢复失败 | project.json 记录源文件哈希，启动时检测路径有效性，无效时提示重新定位 |
| .transbridge 文件过大 | ZIP 打包/解压耗时 | 仅打包当前版本数据（不含快照），快照可选是否包含 |
