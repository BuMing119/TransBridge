# ui 模块

## 职责

PyQt6 用户界面，包括主窗口、工作台、ParaTranz 管理面板、浮动工具窗口。负责用户交互、状态展示、后台任务调度。

---

## 目录结构

```
ui/
├── __init__.py
├── app.py                     # QApplication 入口
├── main_window.py             # 主窗口（工作台 + ParaTranz 管理双 Tab）
├── context.py                 # AppContext 全局状态
├── workers.py                 # ApiWorker 后台线程 + 全局信号总线
│
├── workbench/                 # 翻译工作台
│   ├── __init__.py
│   ├── widget.py              # 工作台主 Widget（左统计 + 右三步）
│   ├── step1.py               # 步骤1：源文件解析
│   ├── step2.py               # 步骤2：词条预览与选择
│   ├── step3.py               # 步骤3：操作（上传/下载/写回）
│   ├── stats_panel.py         # 左侧集合统计面板
│   ├── project_prompt_overlay.py  # 项目提示遮罩层
│   └── cards/                 # 操作卡片组件
│       ├── __init__.py
│       ├── base.py            # OpCard 基类
│       ├── upload_card.py     # 上传卡片
│       ├── download_card.py   # 下载卡片
│       └── write_card.py      # 写回卡片
│
├── tools/                     # 浮动工具窗口
│   ├── __init__.py
│   └── ai_translator/         # AI 翻译工具（子包）
│       ├── __init__.py
│       ├── ai_translator_window.py          # AITranslatorWindow 配置窗口
│       ├── _translation_progress_window.py  # 进度窗口（暂停/停止/后台）
│       ├── _translation_worker.py           # 后台翻译线程
│       ├── _llm_log_viewer.py               # LLM 原始响应日志查看窗口（Tab 视图）
│       ├── _term_editor_dialog.py           # 动态术语库查看对话框
│       ├── _translation_target_dialog.py    # 翻译目标选择对话框（单插件/批量）
│       ├── _batch_translation_dialog.py     # 批量翻译对话框（插件排序+勾选）
│       ├── _batch_config_dialog.py          # 批量翻译配置对话框
│       ├── _batch_translation_worker.py     # 批量翻译后台线程
│       ├── _batch_translation_progress_window.py  # 批量翻译进度窗口
│       └── _batch_llm_log_viewer.py         # 批量翻译 LLM 日志查看窗口
│
└── paratranz/                 # ParaTranz 管理面板
    ├── __init__.py
    ├── widget.py              # 管理面板主 Widget（左项目列表 + 右多 Tab）
    ├── config_dialog.py       # API Token 配置对话框
    ├── project_panel.py       # 左侧项目列表面板
    ├── user_dialog.py         # 用户信息对话框
    ├── mails_dialog.py        # 私信对话框
    ├── _strings_common.py     # 词条 Tab 公共逻辑
    ├── string_detail_dialog.py # 词条详情对话框
    ├── string_dialogs.py      # 词条编辑对话框
    ├── overview_tab.py        # 概览 Tab
    ├── files_tab.py           # 文件管理 Tab
    ├── strings_tab.py         # 词条管理 Tab
    ├── terms_tab.py           # 术语管理 Tab
    ├── members_tab.py         # 成员管理 Tab
    ├── history_tab.py         # 历史记录 Tab
    ├── contribution_tab.py    # 贡献统计 Tab
    ├── export_tab.py          # 导出管理 Tab
    └── issues_tab.py          # 讨论 Tab
```

---

## 核心类

### CollectionSlot

**路径**: `src/transbridge/ui/context.py`

**职责**: 单次解析的所有上下文打包为一个槽位，由 `AppContext` 统一管理。

```python
@dataclass
class CollectionSlot:
    label: str                          # ComboBox 显示名（文件 stem）
    collection: TranslationEntryCollection
    esp_path: str | None = None
    eet_path: str | None = None
    xt_path: str | None = None
    strings_path: str | None = None     # Strings 目录路径（用于导入翻译）
    strings_lang: str = "chinese"       # strings 文件语言标签
    migrate_count: int = 0
    plugin: object = None               # 解析出的 Plugin 实例
    strings_lookup: object = None       # PluginStringsLookup（本地化插件）
```

`plugin` 为 `None` 时表示该集合由 EET XML 构建，**不支持写回 ESP 插件**。`strings_lang` 记录解析时用户选择的语言，用于写入时生成正确命名的 strings 文件。

---

### AppContext

**路径**: `src/transbridge/ui/context.py`

**职责**: 全局状态持有者，通过 Qt 信号广播状态变化。所有 UI 组件通过持有同一个 `AppContext` 实例共享状态。支持多集合同时打开与切换。

```python
class AppContext(QObject):
    # 信号
    config_changed = pyqtSignal(object)       # ParatranzConfig
    user_changed = pyqtSignal(object)         # dict | None
    project_selected = pyqtSignal(object)     # dict | None
    collection_changed = pyqtSignal(object)   # TranslationEntryCollection | None
    collection_list_changed = pyqtSignal()    # 集合列表有增删（Step1 ComboBox 刷新）
    navigate_to = pyqtSignal(int)             # 请求切换主 tab（0=工作台, 1=ParaTranz 管理）
    project_list_changed = pyqtSignal()       # 请求刷新项目列表

    # 只读属性（委托到活跃槽位，向后兼容）
    collection: Collection | None     # 当前活跃集合
    esp_path: str | None              # 当前活跃集合的插件路径
    eet_path: str | None              # 当前活跃集合的 EET XML 路径
    xt_path: str | None               # 当前活跃集合的 XT XML 路径
    strings_lang: str                 # strings 文件语言标签（默认 "chinese"）
    migrate_count: int                # 当前活跃集合的迁移条目数
    plugin: object | None             # 当前活跃集合的 Plugin 实例
    strings_lookup: object | None     # 当前活跃集合的 PluginStringsLookup
    active_slot: CollectionSlot | None  # 当前活跃槽位
    active_key: str | None            # 当前活跃槽位的 key（文件全路径）
    slots: dict[str, CollectionSlot]  # 所有已注册槽位

    # 其他属性
    config: ParatranzConfig           # ParaTranz + LLM 配置
    current_user: dict | None         # 当前用户信息
    current_project: dict | None      # 当前选中项目
    mine_project_ids: set[int]        # 「我参与的」项目 ID 集合
```

**多集合管理方法**:
- `add_slot(key, slot)` → 注册或覆盖槽位，激活并触发 `collection_changed` + `collection_list_changed`
- `remove_slot(key)` → 移除槽位，自动激活最近的其他槽位（无则 None）
- `activate_slot(key)` → 激活指定槽位，触发 `collection_changed`

**辅助方法**:
- `is_admin()` → 当前用户是否为当前项目的管理员或所有者
- `is_member()` → 当前用户是否为当前项目的成员

**向后兼容说明**: `collection`、`esp_path`、`eet_path`、`xt_path`、`migrate_count`、`plugin`、`strings_lookup`、`strings_lang` 均为委托到当前活跃 `CollectionSlot` 的 property，下游组件无需感知多集合机制。

---

### ApiWorker

**路径**: `src/transbridge/ui/workers.py`

**职责**: 在后台线程执行任意可调用对象，通过信号将结果或错误返回主线程。所有 API 请求和耗时操作必须通过此类执行。

```python
class ApiWorker(QThread):
    result = pyqtSignal(object)        # 成功结果
    error = pyqtSignal(str)            # 错误信息
    progress = pyqtSignal(int, int, str)  # current, total, message

    def __init__(self, fn: Callable, *args, **kwargs): ...

    def make_progress_callback(self):
        """返回一个可在工作线程中调用的进度回调。"""
        def _cb(current: int, total: int, msg: str = ""):
            self.progress.emit(current, total, msg)
        return _cb
```

**用法示例**:

```python
def _fetch():
    return api.list_projects()

worker = ApiWorker(_fetch)
worker.result.connect(self._on_projects_loaded)
worker.error.connect(self._on_error)
worker.finished.connect(lambda: self._set_loading(False))
worker.start()
self._workers.append(worker)   # 必须保留引用，防止 GC
```

---

### 全局信号总线

**路径**: `src/transbridge/ui/workers.py`

| 总线 | 信号 | 用途 |
|------|------|------|
| `_http_error_bus` | `http_error(int, str)` | 401/403 错误路由到 MainWindow 统一处理（弹出登录框） |
| `_api_status_bus` | `request_started()` | 追踪 API 请求开始 |
| `_api_status_bus` | `request_finished(bool)` | 追踪 API 请求结束（True=成功, False=失败） |

**获取方式**:
```python
from src.transbridge.ui.workers import get_http_error_bus, get_api_status_bus

get_http_error_bus().http_error.connect(self._on_http_error)
get_api_status_bus().request_started.connect(self._on_request_started)
```

---

### MainWindow

**路径**: `src/transbridge/ui/main_window.py`

**职责**: 主窗口，包含工作台和 ParaTranz 管理两个 Tab，协调全局状态和菜单。

**结构**:
```
MainWindow
├── MenuBar
│   ├── 小工具 → AI 自动翻译
│   ├── 文件 → 刷新项目列表 / 设置 / 退出
│   ├── 账户 → 我的信息 / 私信
│   └── 帮助 → 关于
├── QTabWidget
│   ├── Tab 0: WorkbenchWidget（工作台）
│   └── Tab 1: ParaTranzWidget（ParaTranz 管理）
└── StatusBar
    ├── 用户标签
    ├── 项目标签
    ├── API 状态指示器
    └── 消息标签
```

**关键方法**:
- `_load_current_user()` → 通过 API 加载当前用户信息
- `_show_config_dialog()` → 显示 API Token 配置对话框
- `_on_http_error(status, message)` → 集中处理 401/403 错误

---

### WorkbenchWidget

**路径**: `src/transbridge/ui/workbench/widget.py`

**职责**: 翻译工作台主界面，左侧统计面板 + 右侧三步骤面板。

**结构**:
```
WorkbenchWidget
├── QSplitter (Horizontal)
│   ├── CollectionStatsPanel（左侧统计）
│   └── QScrollArea
│       └── 右侧容器
│           ├── Step1SourceWidget（步骤1）
│           ├── Step2PreviewWidget（步骤2）
│           └── Step3OpsWidget（步骤3）
```

**公共方法**:
- `open_tool(tool_id: str)` → 打开工具窗口（如 AI 翻译）

---

### Step1SourceWidget

**路径**: `src/transbridge/ui/workbench/step1.py`

**职责**: 源文件选择与解析。支持同时打开多个集合并切换，支持两种解析来源模式，支持批量选择ESP文件。

#### 集合管理栏（顶部）

```
当前集合: [ MyPlugin.esp ▼ ]  [＋ 新建]  [✕ 移除]
```

- **ComboBox**：列出所有已解析集合，切换时激活对应槽位（`ctx.activate_slot`）并锁定表单
- **新建**：清空所有输入框，解锁表单，等待新一次解析
- **移除**：确认后从 ctx 移除当前集合；若还有其他集合则切换并锁定，否则解锁

#### 批量选择 ESP 文件

点击「浏览」按钮可选择多个 ESP/ESM/ESL 文件（Ctrl/Shift 多选）：
- 选择单个文件：输入框显示完整路径
- 选择多个文件：输入框显示「已选择 N 个文件」，tooltip 列出所有路径
- 点击「解析插件」后逐个解析，每个文件创建独立集合槽位
- 批量解析时不应用迁移源，后续可单独为每个集合配置

#### 解析来源切换

| 模式 | 可见行 | 构建方式 |
|------|--------|---------|
| ESP 插件（标准） | 插件文件（必填）、EET XML（可选）、XT XML（可选）、已翻译插件（可选）、Strings 目录（可选） | `PluginParser` → Collection |
| EET XML（仅迁移旧译文） | EET XML（必填）、XT XML（可选）、已翻译插件（可选） | `Collection.from_eet_xml()` → Collection |

EET 模式构建的集合 `plugin=None`。WriteCard 的"写回 ESP"选项将被禁用。

#### Strings 目录（本地化插件导入翻译）

用于从已翻译的 strings 文件导入译文，仅对本地化插件有效：
- **输入框**：Strings 目录或文件路径
- **语言下拉框**：选择目标语言（chinese/english/german 等）
- **「全部」勾选框**：勾选后点击「应用迁移源」，将 Strings 路径应用到所有已加载集合
- 解析时自动加载 `PluginStringsLookup.from_strings_dir()` 并调用 `update_from_strings_lookup()`
- 通过 `entry.string_id` 精确匹配 strings 文件中的翻译

#### 表单锁定与迁移源追加机制

已加载集合后，表单改为**部分锁定**模式：
- ESP 路径：始终锁定，不可修改
- 迁移源按钮（EET/XT/Strings）：根据 slot 中是否已有对应路径决定是否可用
- 已有路径的迁移源按钮禁用（每种迁移源只能配置一次）
- 「解析插件」按钮隐藏，显示「应用迁移源」按钮

| 触发时机 | 状态 |
|----------|------|
| 解析成功后 | 部分锁定，迁移源按钮按需启用 |
| ComboBox 切换到已有集合 | 部分锁定，同步显示已有路径 |
| 点击「＋ 新建」 | 完全解锁 |
| 移除后仍有其他集合 | 部分锁定 |
| 移除最后一个集合 | 完全解锁 |

#### 应用迁移源

点击「应用迁移源」按钮后：
- EET/XT/已翻译插件：仅应用到当前选中集合
- Strings：若勾选「全部」，应用到所有未配置 Strings 路径的集合
- 更新成功后对应按钮禁用，状态栏显示迁移条目数

**信号**:
- `parse_started` → 解析开始
- `parse_finished(collection)` → 解析完成

**解析完成后写入 ctx**（通过 `ctx.add_slot(key, CollectionSlot(...))`）:
- `slot.esp_path` ← ESP 文件路径（EET 模式为 None）
- `slot.eet_path` ← EET XML 路径
- `slot.xt_path` ← XT XML 路径
- `slot.strings_path` ← Strings 目录路径
- `slot.strings_lang` ← strings 文件语言标签（用于写入时命名）
- `slot.migrate_count` ← 迁移条目总数
- `slot.plugin` / `slot.strings_lookup` ← 供 PluginWriter 使用（EET 模式为 None）

**key 冲突处理**: 相同文件路径重新解析时弹框确认覆盖，选「否」则保留原集合不变。

**解析流程**:
```
[ESP 模式]
ESP/ESM ──PluginParser──> TranslationEntryCollection
                              │
                              ├─► update_from_eet_xml()    [可选]
                              ├─► apply_xt_entries()       [可选]
                              ├─► update_from_translated_plugin()  [可选]
                              └─► update_from_strings_lookup()     [可选，本地化插件]

[批量 ESP 模式]
ESP_1 ──PluginParser──> Collection_1 ──► slot_1（无迁移源）
ESP_2 ──PluginParser──> Collection_2 ──► slot_2（无迁移源）
...
后续可单独为每个 slot 配置迁移源

[EET 模式]
EET XML ──Collection.from_eet_xml()──> TranslationEntryCollection
                                           │
                                           ├─► apply_xt_entries()       [可选]
                                           └─► update_from_translated_plugin()  [可选]
```

---

### Step2PreviewWidget

**路径**: `src/transbridge/ui/workbench/step2.py`

**职责**: 词条预览与选择。显示解析进度、四格统计卡、词条表格。支持多选、筛选、详情查看。

**表格列**:
| 列 | 内容 | 说明 |
|----|------|------|
| 0 | 复选框 | 勾选词条供 AI 翻译使用 |
| 1 | Key | 词条 ID（截断显示） |
| 2 | 原文 | 原文内容（截断显示） |
| 3 | 译文 | 译文内容，无译文显示灰色占位 |
| 4 | 类型 | 分类名称（人名/物品/对话等） |

**统计卡**:
- 总词条
- 已有译文
- 迁移
- 未翻译

**公共方法**:
- `get_selected_entries() -> list[TranslationEntry]` → 返回当前勾选的词条
- `get_filtered_count() -> int` → 返回当前筛选后显示的条数
- `set_parsing(parsing: bool)` → 设置解析中状态
- `refresh(collection)` → 刷新表格（由 `collection_changed` 信号触发）

**详情对话框**: 双击词条打开 `_EntryDetailDialog`，支持按 Key/原文/译文筛选、翻译状态筛选、类型筛选、全选操作。

**`itemChanged` 信号管理**（重要）:
- `itemChanged → _on_item_changed` 仅在 `_init_ui` 中连接**一次**
- `_populate_table` 批量填充期间调用 `blockSignals(True/False)` 屏蔽所有信号，填充完成后统一调用 `_update_count_label()` 一次
- 不可在 `_populate_table` 内重复 `connect`，否则每次刷新都会累积一条新连接，导致大词条量时 O(n²) 卡顿

---

### Step3OpsWidget

**路径**: `src/transbridge/ui/workbench/step3.py`

**职责**: 操作面板，三个独立操作卡片：上传 ParaTranz、下载合并、写回 ESP。批量操作已集成到各卡片中。

**结构**:
```
Step3OpsWidget
├── 项目指示条
├── 警告条（初始隐藏）
├── 卡片行
│   ├── UploadCard（含批量按钮，slots > 1 时显示）
│   ├── DownloadCard（含批量按钮，slots > 1 时显示）
│   └── WriteCard（含批量按钮，slots > 1 时显示）
├── 共享进度条
└── ProjectPromptOverlay（遮罩层，未选项目时显示）
```

**批量操作功能**（当 `len(ctx.slots) > 1` 时，各卡片显示「批量」按钮）:
- 批量上传：点击后弹出插件选择对话框 → 上传模式选择对话框 → 确认对话框 → 执行
- 批量下载：点击后弹出插件选择对话框 → 确认对话框 → 执行
- 批量写回：点击后弹出插件选择对话框 → 选择输出目录 → 执行

**按钮状态控制**:
- 上传/下载：需 `collection` + `current_project` + `is_member`
- 写回：仅需 `collection`
- 批量上传/下载按钮：需 `current_project` + `is_member`
- 批量写回按钮：始终可用（无 plugin 的 slot 自动过滤）

**ProjectPromptOverlay 遮罩层**:
- 触发条件：`has_collection and not has_project`（已加载集合但未选项目）
- 覆盖范围：**仅覆盖 UploadCard + DownloadCard**，WriteCard 不受遮罩影响，保持可交互
- 几何计算：`_update_overlay_geometry()` 以 `_card_upload.mapTo` 确定起始 y（跳过 GroupBox 标题和指示条），以 `_card_write.mapTo` 确定右边界，使 overlay 仅覆盖 UploadCard + DownloadCard 所在区域（fallback 宽度为 widget 宽度的 2/3）

**项目切换逻辑**:
- 切换项目时若已有集合，弹窗确认是否切换
- 选中非参与项目时显示警告条

---

### OpCard

**路径**: `src/transbridge/ui/workbench/cards/base.py`

**职责**: 操作卡片基类，包含标题、说明、主按钮、可选批量按钮。

```python
class OpCard(QGroupBox):
    def __init__(self, title: str, desc: str, btn_text: str, parent=None):
        ...
        self.btn = QPushButton(btn_text)        # 主按钮
        self.batch_btn = QPushButton("批量")     # 批量按钮（默认隐藏）

    def set_batch_visible(self, visible: bool):
        """设置批量按钮可见性。"""
```

**子类**:
| 类 | 路径 | 说明 |
|----|------|------|
| `UploadCard` | `cards/upload_card.py` | 上传到 ParaTranz（支持分类/普通模式，4 种原文/译文处理策略，分类模式下支持选择上传的文件） |
| `DownloadCard` | `cards/download_card.py` | 从 ParaTranz 下载并合并 |
| `WriteCard` | `cards/write_card.py` | 写回 ESP/EET XML/XT XML，点击后弹出目标选择对话框 |

#### 批量操作通用对话框

各卡片均使用以下对话框：

| 类 | 用途 |
|----|------|
| `_SlotSelectDialog` | 插件选择对话框，全选/全不选，滚动区域支持大量插件 |
| `_BatchConfirmDialog` | 批量操作确认对话框，最大高度 400px，支持大量项目滚动查看 |
| `_BatchResultDialog` | 批量操作结果展示对话框，显示成功/失败/总数及详情 |

#### WriteCard — 批量写回对话框

`WriteCard` 提供两个可滚动对话框用于批量操作：

| 类 | 用途 |
|----|------|
| `_BatchConfirmDialog` | 批量写回确认对话框，最大高度 400px，支持大量插件滚动查看 |
| `_BatchResultDialog` | 批量写回结果展示对话框，显示成功/失败/总数及详情 |

对话框结构与 `UploadCard` / `DownloadCard` 中的同名类相同，确保插件数量较多时按钮始终可见可点击。

#### WriteCard — `_WriteTargetDialog` 写回目标选择

点击「写回」按钮后弹出，三选一：

| 选项 | 说明 | 禁用条件 |
|------|------|---------|
| 写回 ESP 插件 | 将译文写入插件副本，输出汉化版 ESP 文件 | `ctx.plugin is None`（EET 构建的集合） |
| 写回 EET XML | 将译文更新到 EET XML 文件，需指定路径 | — |
| 写回 XT XML | 将译文更新到 XT XML 文件，需指定路径 | — |

- EET/XT 路径输入框在对应选项选中时才激活，路径为空则「确认写回」按钮禁用
- 打开时自动用 `ctx.eet_path` / `ctx.xt_path` 预填路径
- **EET 构建的集合**（`ctx.plugin is None`）：ESP 选项禁用并显示说明文字，默认选中 EET 选项
- 确认后弹出「另存为」对话框，支持覆盖原文件或另存新路径

`_WriteTargetDialog(eet_path, xt_path, has_esp=True)` — `has_esp` 参数控制 ESP 选项是否可用。

#### WriteCard — ESP 写入行为

**本地化插件**（`ctx.strings_lookup is not None`）：
- 弹出**目录选择对话框**选择 Strings 文件输出目录
- 使用 `ctx.strings_lang` 作为语言标签，生成 `{PluginName}_{Lang}.*strings` 文件
- **纯本地化模式**：仅输出 strings 文件，原 ESP 不修改
- **混合模式**：同时保存 ESP 副本和 strings 文件

**非本地化插件**：
- 弹出**文件保存对话框**选择 ESP 保存路径
- 译文内嵌到 ESP 文件中

#### UploadCard — `_BatchUploadModeDialog` 批量上传模式

批量上传时弹出的模式选择对话框（简化版，无文件名输入）。

**说明**:
- 每个插件将作为单个 JSON 文件上传（不分类）
- 文件名自动使用插件名（如 `MyPlugin.json`）

**已存在文件处理方式** (`translation_mode`):

| 选项 | `translation_mode` | 操作 |
|------|-------------------|------|
| 仅更新原文 | `orig_only` | 不改动已有译文 |
| 仅导入译文 — 安全 | `trans_safe` | 不覆盖已人工编辑的词条；新文件跳过 |
| 仅导入译文 — 强制覆盖 | `trans_force` | 覆盖所有译文；新文件跳过 |
| 更新原文并导入译文 | `both` | 安全模式，不覆盖已人工编辑的词条 |

#### UploadCard — `_UploadModeDialog` 上传模式

**上传格式**（上半区）:
- **分类上传**（推荐）：按词条类型拆分为多个文件，可选同时导出本地备份。确认后若文件数 > 1，弹出 `_FileSelectionDialog` 供用户选择要上传的分类文件。
- **普通上传**：全部词条合并为单个 JSON 文件，需填写文件名

#### UploadCard — `_FileSelectionDialog` 文件选择

仅在**分类上传**且文件数 > 1 时弹出，列出所有将生成的分类文件及其条目数（默认全选）。

| 元素 | 说明 |
|------|------|
| 全选 / 全不选 按钮 | 快速切换所有复选框 |
| 文件列表 | 每行显示文件名和条目数，支持逐项勾选 |
| 确认上传 | 无选中时置灰 |

本地备份（若启用）不受文件选择影响，始终导出完整集合。

**已存在文件处理方式**（下半区，`translation_mode`）:

| 选项 | `translation_mode` | 操作 | 新文件行为 |
|------|-------------------|------|-----------|
| 仅更新原文 | `orig_only` | `reupload_file` | `upload_file` |
| 仅导入译文 — 安全 | `trans_safe` | `update_file_translation(force=False)` | 跳过 |
| 仅导入译文 — 强制覆盖 | `trans_force` | `update_file_translation(force=True)` | 跳过 |
| 更新原文并导入译文 | `both` | `reupload_file` → `update_file_translation(force=False)` | `upload_file` → `update_file_translation` |

"仅导入译文"选中时，子选项（安全/强制）才启用。

---

### CollectionStatsPanel

**路径**: `src/transbridge/ui/workbench/stats_panel.py`

**职责**: 工作台左侧集合统计面板，显示总词条数、已翻译数、来源文件名以及按分类的树形统计。

**分类映射**:
```python
_CONTEXT_TO_CATEGORY = {
    "NPC_:FULL": "人名",
    "BOOK:FULL": "书籍_书名",
    "BOOK:DESC": "书籍_内容",
    "CELL:FULL": "地名与门",
    "QUST:FULL": "任务日志",
    "ENCH:FULL": "法术_龙吼_技能",
    "ACTI:FULL": "物品",
    # ... 更多映射
    # INFO/DIAL → "对话"（特殊处理）
}
```

---

### AITranslatorWindow

**路径**: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`

**子包结构**:

| 文件 | 类 | 职责 |
|------|-----|------|
| `ai_translator_window.py` | `AITranslatorWindow` | 配置窗口（翻译开始前） |
| `_translation_progress_window.py` | `_TranslationProgressWindow` | 进度窗口（暂停/停止/后台） |
| `_translation_worker.py` | `_TranslationWorker` | 后台翻译线程（QThread） |
| `_llm_log_viewer.py` | `_LLMLogViewer` | LLM 原始响应日志查看窗口 |
| `_term_editor_dialog.py` | `_TermEditorDialog` | 动态术语库查看对话框 |
| `_translation_target_dialog.py` | `_TranslationTargetDialog` | 翻译目标选择对话框（单插件/批量） |
| `_batch_translation_dialog.py` | `_BatchTranslationDialog` | 批量翻译对话框（插件排序+勾选） |
| `_batch_config_dialog.py` | `_BatchConfigDialog` | 批量翻译配置对话框（简化版） |
| `_batch_translation_worker.py` | `_BatchTranslationWorker` | 批量翻译后台线程 |
| `_batch_translation_progress_window.py` | `_BatchTranslationProgressWindow` | 批量翻译进度窗口 |
| `_batch_llm_log_viewer.py` | `_BatchLLMLogViewer` | 批量翻译 LLM 日志查看窗口 |

**职责**: AI 翻译浮动工具，采用双窗口架构：
- **配置窗口** (`AITranslatorWindow`)：翻译开始前使用，配置 LLM、Embedding、术语库、翻译范围
- **进度窗口** (`_TranslationProgressWindow`)：翻译进行中使用，显示进度、计时日志，支持暂停/停止/后台继续
- **批量翻译**：支持多插件顺序翻译，插件间术语实时共享

**配置窗口分区**:
1. **LLM 配置**：供应商、**目标语言**、模型名、API Key、Base URL、并发数、Token 限制
2. **语义检索配置（Embedding）**：模式（本地模型/API 服务）、模型名、API Key（可选）
3. **术语库来源**：优先级列表、本地 JSON/Excel 路径
4. **翻译范围**：全部/筛选/选中，覆盖选项
5. **后处理配置**：检测阶段（一致性/格式/质量检测）、修复与润色阶段（修复开关、润色开关及范围/强度）、裁决阶段（裁决开关、严格模式）

**目标语言配置**:
- 下拉框选择，对应 `data/prompts/langs/{lang}.toml` 语言配置文件
- 当前支持：`zh_CN`（简体中文）
- 默认值：`zh_CN`
- 自动保存到 INI 文件的 `target_lang` 字段

**`_TranslationWorker` 信号**:

| 信号 | 类型 | 说明 |
|------|------|------|
| `progress` | `(int, int, str, int, int, int)` | current, total, message, success, failed, new_terms |
| `log` | `(int, str)` | `(batch_idx, line)` 详细日志行；`batch_idx=-1` 为轮次级消息，`>=1` 为批次专属消息 |
| `result` | `(object)` | 翻译完成，携带 `TranslationResult` |
| `error` | `(str)` | 全局异常 |

**LLM 流式日志目录**:
- `run()` 启动时创建 `data/log/{esp_stem}_{YYYYMMDD_HHMMSS}/` 目录
- 每个并发批次独立写入 `batch_{idx:03d}.log`（`stream_callback(batch_idx, chunk)` 绑定 idx 后写入），并发批次互不干扰
- `stream_log_dir` 属性暴露目录路径，供进度窗口的日志查看器使用

**日志格式（`log` 信号内容示例）**:

```
── 第一轮开始（3 批，专有名词）──

开始翻译：
任务1：专有名词
-----------------------
  术语精确匹配: 1 条直接填充
Alvor -> 阿尔沃尔 [直填]
  LLM 响应中...
Hadvar -> 哈德瓦尔
Ralof -> 拉洛夫
-----------------------
已完成：
术语匹配时长:0.01 s
LLM调用时长:1.23 s
解析时长：0.002 s
总时长：1.26 s
翻译词条数：3
新增术语数：2

── 第一轮完成: 3.45s ──

总耗时: 12.34s
```

每批次结构：头（`开始翻译：` / `任务N：类型` / `---`）→ 体（翻译条目行 + 状态行）→ 尾（`---` / `已完成：` / 各项计时 / 词条数 / 新增术语数）。
`_run_batch` 的 `_timing_out` dict 负责将计时数据回传给 `_run_one_batch` 以填充尾部。

**`_LLMLogViewer`**:
- 独立窗口，接受日志目录路径（`data/log/{esp_stem}_{timestamp}/`）
- 使用 `QTabWidget`，每个 `batch_{idx:03d}.log` 文件对应一个 Tab（「批次 N」）
- 定时器每 800ms 扫描目录，自动为新出现的日志文件创建 Tab
- 各 Tab 独立刷新：保持滚动到底部（仅当用户未上滚时）
- 翻译结束后自动停止定时器，进行最后一次全量刷新
- 进度窗口右下角「📄 LLM 日志」按钮打开，可重复点击（已打开则激活）

**`_TranslationProgressWindow` 详细日志区 — 并发批次子组件**:

进度窗口「详细日志」区分为两层：

- **轮次日志区**（`_round_log: QTextEdit`，固定高度 70px）：接收 `batch_idx=-1` 的消息，即 `_log()` 在 `translate()` 中发出的轮次级消息（`── 第N轮开始/完成 ──` / 总耗时）及进度消息（`▶ ...`）。追加前检测是否已在底部（`value >= maximum - 4`），仅在底部时自动跟随滚动。
- **批次滚动区**（`QScrollArea`）：每个批次 `batch_idx>=1` 对应一个 `_BatchWidget(QFrame)` 子组件，懒创建并插入滚动区。同样仅在用户处于底部时才跟随滚动，用户向上翻阅时不强制拉回。

**`_BatchWidget` 状态机**:

```
phase: 'init' → 'header' → 'trans' → 'footer' → 'done'

'header': 收到"开始翻译："后进入，等待任务标题行 和 第一条 ---
'trans':  收到第一条 --- 后进入，对含 " -> " 的行维护 deque 滚动窗口（最多10条）
'footer': 收到第二条 --- 后进入，收集计时行
'done':   收到"新增术语数："行后折叠为单行摘要（或调用 force_collapse）
```

**折叠行为**:
- **正常完成**: footer 末行触发 `_collapse()`，`QTextEdit` 隐藏，`QFrame` 变为绿色摘要行（`✅ 任务N：类型 — X 条 | Xs | 新增术语 N`）
- **中断/停止**: `_on_result`/`_on_error` 调用所有未完成 widget 的 `force_collapse()`，变为黄色警告行（`⚠ 任务N：类型（未完成）`）
- **内存约束**: 活跃批次最多 `max_concurrent` 个 `QTextEdit`；完成后替换为空白 label，bounded

**滚动窗口（trans 阶段）**:
- 同单 QTextEdit 方案：`QTextCursor` 存入 `deque`，超过 10 条时弹出最旧游标并 `removeSelectedText()`
- 每个 `_BatchWidget` 有独立的 `deque`，并发批次互不干扰

**配置区**:
| 区域 | 字段 |
|------|------|
| LLM 配置 | 供应商、**目标语言**、模型名、API Key、Base URL、并发数、拆批 Token、输出 Token |
| 术语库来源 | 优先级列表（可拖拽排序）、本地 JSON 路径、本地 Excel 路径、Excel 列配置 |
| 翻译范围 | 全部未翻译 / 筛选可见 / 选中词条、覆盖已有译文选项 |
| 后处理配置 | 各阶段独立开关、润色范围/强度、裁决模式 |

**后处理配置（QGroupBox）**:

新增独立分组框，位于「翻译范围」下方，提供细粒度的后处理控制：

```
后处理配置
├── 总开关: [☑] 启用翻译后质量检查与优化
│
├── 阶段1: 质量检测
│   ├── [☑] 术语一致性检查
│   ├── [☑] 格式验证（占位符、标签、引号等）
│   └── [☑] LLM质量检测
│
├── 阶段2: 修复与润色
│   ├── [☑] 启用LLM自动修复
│   ├── [☑] 启用润色优化（需要额外LLM调用）
│   │       润色范围: (●全部条目 ○仅通过检测的条目 ○仅问题项)
│   │       润色强度: (●适中 ○轻微 ○深度)
│   └── 提示：润色会在修复后执行，优先采用润色结果
│
└── 阶段3: 质量裁决
    ├── [☑] 启用质量裁决
    └── [☑] 严格模式（uncertain→reject而非pending）
```

**控件联动**:
- 总开关未选中时，所有阶段配置控件禁用
- 润色开关未选中时，润色范围和强度下拉框禁用
- 裁决开关未选中时，严格模式复选框禁用

**配置字段**（14个INI字段）：

| 阶段 | 字段 | 类型 | 默认值 | UI控件 |
|------|------|------|--------|--------|
| 总开关 | `enable_post_process` | bool | true | 总开关复选框 |
| 检测 | `pp_enable_consistency_check` | bool | true | 术语一致性检查 |
| | `pp_enable_format_validation` | bool | true | 格式验证 |
| | `pp_enable_quality_gate` | bool | true | LLM质量检测 |
| 修复 | `pp_enable_refinement` | bool | true | 启用LLM修复 |
| | `pp_refinement_batch_size` | int | 5 | 内部使用 |
| 润色 | `pp_enable_polish` | bool | false | 启用润色 |
| | `pp_polish_scope` | str | "all" | 润色范围下拉框 |
| | `pp_polish_level` | str | "moderate" | 润色强度下拉框 |
| | `pp_polish_batch_size` | int | 5 | 内部使用 |
| 裁决 | `pp_enable_arbitration` | bool | true | 启用裁决 |
| | `pp_strict_arbitration` | bool | false | 严格模式 |
| | `pp_arbitration_batch_size` | int | 10 | 内部使用 |

**状态管理**:
- `_on_pp_enable_changed()`: 总开关状态变化时更新所有子控件启用状态
- `_on_polish_changed()`: 润色开关状态变化时更新润色选项启用状态
- 所有控件变更实时触发 `_save_config()` 持久化到INI文件

**向后兼容**:
- 旧配置（仅含 `enable_post_process`）加载时，新增字段使用默认值
- 首次打开窗口时显示默认配置（检测/修复/裁决启用，润色禁用）

**配置自动保存**:
- 所有配置字段的变更（文本输入、下拉选择、数值调整、拖拽排序）均实时触发 `_save_config()` 写入 INI 文件
- 自动保存信号在 `_load_config()` 完成后才连接（`_connect_auto_save()`），避免初始化时误写文件
- 点击「开始翻译」时仍会执行一次 `_save_config()`，确保最新值持久化

**信号**:
- `progress_window_created.emit(progress_win)` → 翻译启动时发出

**断点续传**:
- 启动时检测未完成的翻译任务，询问是否从断点继续

---

### 批量翻译组件

**入口方法** `AITranslatorWindow.open_for_translation()`:
1. 弹出 `_TranslationTargetDialog` 选择翻译目标（当前插件/批量翻译）
2. 单插件模式：打开 `AITranslatorWindow` 配置窗口
3. 批量模式：打开 `_BatchTranslationDialog` 批量翻译对话框

#### _TranslationTargetDialog

**路径**: `src/transbridge/ui/tools/ai_translator/_translation_target_dialog.py`

**职责**: 翻译目标选择对话框，选择翻译当前插件还是批量翻译已加载插件。

| 选项 | 说明 | 禁用条件 |
|------|------|---------|
| 翻译当前插件 | 进入单插件翻译流程 | `ctx.active_slot is None` |
| 批量翻译已加载插件 | 进入批量翻译流程 | `len(ctx.slots) <= 1` |

显示统计信息：当前插件的条目数/未翻译数，批量模式下的总插件数/总条目数。

#### _BatchTranslationDialog

**路径**: `src/transbridge/ui/tools/ai_translator/_batch_translation_dialog.py`

**职责**: 批量翻译对话框，支持插件排序、勾选、配置编辑。

**功能**:
- **可拖拽排序**：调整翻译顺序（从上到下依次翻译）
- **勾选插件**：全选/全不选/仅选未翻译
- **覆盖选项**：是否覆盖已有译文
- **LLM 配置显示与修改**：显示当前配置摘要，点击「修改配置」打开 `_BatchConfigDialog`
- **统计信息**：已选插件数、总条目数、未翻译条目数

**公共方法**:
- `get_selected_slots() -> list[CollectionSlot]` → 返回按列表顺序排列的选中 slot
- `is_overwrite() -> bool` → 返回是否覆盖已有译文
- `get_llm_config() -> LLMConfig | None` → 返回当前 LLM 配置

#### _BatchConfigDialog

**路径**: `src/transbridge/ui/tools/ai_translator/_batch_config_dialog.py`

**职责**: 批量翻译的简化版 LLM 配置对话框，仅包含核心选项。

**字段**:
| 字段 | 说明 |
|------|------|
| 供应商 | OpenAI 兼容 / Anthropic |
| 模型名 | 如 gpt-4o / deepseek-chat |
| API Key | 密码输入框 |
| Base URL | 仅 OpenAI 兼容模式启用 |
| 并发数 | 1-50，默认 20 |

**功能**:
- 测试连接按钮：验证配置有效性
- 保存时保留其他配置字段（如 max_tokens_per_batch 等）

#### _BatchTranslationWorker

**路径**: `src/transbridge/ui/tools/ai_translator/_batch_translation_worker.py`

**职责**: 批量翻译后台线程，依次翻译多个插件，支持暂停/停止/断点续传。

**数据类**:

```python
@dataclass
class PluginTranslationResult:
    plugin_name: str
    success: bool
    result: TranslationResult | None = None
    error: str | None = None

@dataclass
class BatchTranslationSummary:
    total_plugins: int
    success_plugins: int
    failed_plugins: int
    total_success_entries: int
    total_failed_entries: int
    details: list[PluginTranslationResult]
```

**信号**:
| 信号 | 类型 | 说明 |
|------|------|------|
| `plugin_started` | `(str, int, int)` | 插件开始翻译：插件名、当前索引、总数 |
| `plugin_finished` | `(str, object)` | 插件完成：插件名、TranslationResult |
| `plugin_progress` | `(int, int, str, int, int, int)` | 当前条目、总条目、消息、成功数、失败数、新术语数 |
| `log` | `(int, str)` | 日志：batch_idx, line |
| `all_finished` | `(object)` | 全部完成：BatchTranslationSummary |
| `error` | `(str)` | 全局异常 |

**共享 in-flight 缓存**:
- `_shared_in_flight_terms: dict[str, str]` — 插件间实时共享的术语缓存
- `_shared_in_flight_lock: threading.Lock` — 并发锁
- 每个插件的 `AutoTranslator` 实例注入共享缓存，实现插件间术语实时共享

**LLM 流式日志**:
- 日志目录：`data/log/batch_{YYYYMMDD_HHMMSS}/`
- 每个插件独立子目录：`{plugin_name}/`
- 每个批次独立文件：`batch_{idx:03d}.log`

#### _BatchTranslationProgressWindow

**路径**: `src/transbridge/ui/tools/ai_translator/_batch_translation_progress_window.py`

**职责**: 批量翻译进度窗口，显示总体进度和当前插件详细进度。

**结构**:
```
_BatchTranslationProgressWindow
├── 总体进度区（QGroupBox）
│   ├── 进度条 + 插件计数
│   └── 状态消息
├── 当前插件区（QGroupBox）
│   ├── 进度条 + 条目计数
│   ├── 状态消息
│   └── 统计行（成功/失败/新增术语）
├── 详细日志区（QGroupBox）
│   ├── 轮次日志（QTextEdit，固定高度 70px）
│   └── 批次滚动区（QScrollArea，动态创建 _BatchWidget）
└── 按钮行
    ├── 暂停/继续
    ├── 停止
    └── LLM 日志
```

**信号**:
- `translation_completed` → 批量翻译完成时发出

**关闭行为**:
- 翻译进行中关闭时弹出对话框，选择「停止翻译并关闭」或「后台继续，关闭窗口」

#### _BatchLLMLogViewer

**路径**: `src/transbridge/ui/tools/ai_translator/_batch_llm_log_viewer.py`

**职责**: 批量翻译 LLM 日志查看窗口，两级 Tab 结构（插件 → 批次）。

**结构**:
```
_BatchLLMLogViewer
├── 工具栏（路径标签 + 自动刷新复选框 + 刷新按钮）
└── 插件 Tab 区域（QTabWidget）
    ├── 插件1 Tab
    │   └── _PluginLogWidget
    │       └── 批次 Tab 区域（batch_001.log, batch_002.log, ...）
    ├── 插件2 Tab
    │   └── ...
    └── ...
```

**功能**:
- 定时刷新（800ms）：自动扫描新出现的日志文件并创建 Tab
- 滚动跟随：仅在用户处于底部时自动滚动
- 翻译结束后停止定时器，进行最后一次全量刷新

---

### ParaTranzWidget

**路径**: `src/transbridge/ui/paratranz/widget.py`

**职责**: ParaTranz 管理模式主界面，左侧项目列表 + 右侧多 Tab 页。

**结构**:
```
ParaTranzWidget
├── QSplitter (Horizontal)
│   ├── ProjectListPanel（左侧项目列表）
│   └── QTabWidget
│       ├── 概览 (OverviewTab)
│       ├── 文件管理 (FilesTab)
│       ├── 词条管理 (StringsTab)
│       ├── 术语管理 (TermsTab)
│       ├── 成员管理 (MembersTab)
│       ├── 历史记录 (HistoryTab)
│       ├── 贡献统计 (ContributionTab)
│       ├── 导出管理 (ExportTab)
│       └── 讨论 (IssuesTab)
```

---

### ProjectListPanel

**路径**: `src/transbridge/ui/paratranz/project_panel.py`

**职责**: 左侧项目列表面板，支持「全部项目」/「我参与的」视图切换、关键词搜索、新建项目。

**信号**:
- `project_chosen.emit(project)` → 点击项目时发出

**公共方法**:
- `load_projects()` → 加载项目列表
- `switch_to_mine()` → 切换到「我参与的」视图
- `refresh_projects()` → 刷新项目列表

---

### ConfigDialog

**路径**: `src/transbridge/ui/paratranz/config_dialog.py`

**职责**: API Token 配置对话框。启动时若无有效 Token 则自动弹出，也可通过菜单手动打开。

**字段**:
- API Token（密码输入框）
- 请求超时（秒）

**验证流程**:
1. 调用 `ParatranzProjectAPI.list_projects()` 验证 Token
2. 调用 `ParatranzUserAPI.get_my_user()` 获取用户 ID
3. 保存配置并触发 `config_changed` 信号

---

## 信号流程

### AppContext 信号连接

```
AppContext
│
├── config_changed ───────────> MainWindow._on_config_changed
│                              ProjectListPanel._on_config_changed
│
├── user_changed ─────────────> MainWindow._on_user_changed
│                              （更新状态栏用户标签）
│
├── project_selected ─────────> MainWindow._on_project_selected
│                              Step3OpsWidget._on_project_changed
│                              各 Tab 加载项目数据
│
├── collection_changed ───────> MainWindow._on_collection_changed
│                              Step2PreviewWidget.refresh
│                              CollectionStatsPanel.refresh
│                              Step3OpsWidget._on_collection_changed
│
├── collection_list_changed ──> Step1SourceWidget._refresh_combo
│                              （集合 ComboBox 重建）
│
├── navigate_to ──────────────> MainWindow._on_navigate_to
│                              （切换主 Tab）
│
└── project_list_changed ─────> ProjectListPanel.load_projects
```

### HTTP 错误处理

```
ApiWorker (后台线程)
│
├── 成功 ──> result.emit(data)
│            _api_status_bus.request_finished.emit(True)
│
└── 异常
    │
    ├── 状态码 401/403 ──> _http_error_bus.http_error.emit(status, msg)
    │                       _api_status_bus.request_finished.emit(False)
    │                       （不触发 worker.error，由 MainWindow 统一处理）
    │
    └── 其他异常 ──> worker.error.emit(err_str)
                     _api_status_bus.request_finished.emit(False)
```

---

## 坑点与注意事项

### 1. ApiWorker 引用保持

```python
# 错误：worker 会被 GC，可能导致崩溃或信号丢失
ApiWorker(fn).start()

# 正确：保留引用
self._workers: list[ApiWorker] = []
w = ApiWorker(fn)
w.start()
self._workers.append(w)
```

### 2. QTableWidget 绑定数据

```python
# 使用 UserRole 存储条目对象
item = QTableWidgetItem(text)
item.setData(Qt.ItemDataRole.UserRole, entry)
self._table.setItem(row, col, item)

# 读取时
entry = item.data(Qt.ItemDataRole.UserRole)
```

### 3. 信号阻塞

```python
# 临时断开信号避免递归
self._table.itemChanged.disconnect(self._on_item_changed)
# ... 批量修改 ...
self._table.itemChanged.connect(self._on_item_changed)

# 或使用 blockSignals（批量填充表格时优先使用此方式）
widget.blockSignals(True)
# ... 修改 ...
widget.blockSignals(False)
```

> **Step2 特别说明**: `_populate_table` 在填充期间必须调用 `blockSignals(True/False)`。
> 若不屏蔽，每行的 `setCheckState` 都会触发 `itemChanged → _on_item_changed → _update_count_label → get_selected_entries()`，
> 总复杂度 O(n²)，词条量大时主线程将冻结。
> 同时严禁在 `_populate_table` 内重复 `connect`，否则信号监听数量会随每次刷新线性增长。

### 4. 主线程禁止 API 调用

所有 API 请求必须通过 `ApiWorker` 在后台执行，严禁在主线程直接调用，否则会导致界面冻结。

### 5. 401/403 错误处理

401/403 错误通过全局 `_http_error_bus` 处理，不会触发 `worker.error` 信号。这意味着：
- 各 Tab 不需要单独处理认证错误
- MainWindow 统一弹出配置对话框

---

## 依赖关系

```
ui
├── PyQt6
├── converter (TranslationEntry, Collection)
├── parser (PluginParser, EET_XmlParser, XT_XmlParser)
├── writer (PluginWriter, EETWriter, XTWriter)
├── paratranz (ParatranzConfig, API 客户端, 工作流)
└── ai_translator (AutoTranslator, LLMConfig, 术语库)
```

---

## 扩展点

### 新增操作卡片

1. 继承 `OpCard` 基类
2. 在 `step3.py` 中实例化并添加到布局
3. 在 `_update_button_states()` 中控制按钮状态

### 新增 ParaTranz Tab

1. 在 `ui/paratranz/` 下创建 Tab 组件
2. 在 `ParaTranzWidget._init_ui()` 中添加 Tab

### 新增工具窗口

1. 在 `ui/tools/` 下创建子包或单文件窗口组件
2. 在 `WorkbenchWidget.open_tool()` 中添加打开逻辑
3. 在 `MainWindow._init_menu()` 中添加菜单入口
