# ui 模块

## 职责

PyQt6 用户界面，包括主窗口、工作台、ParaTranz 管理面板、浮动工具窗口。负责用户交互、状态展示、后台任务调度。

---

## 目录结构

```
ui/
├── __init__.py
├── app.py                     # QApplication 入口
├── main_window.py             # 主窗口（开始、工作台、ParaTranz）
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
├── dialogue/                  # 词条弹窗、渐进任务上下文、草稿与导航
│   ├── controller.py          # DialogueEditorController
│   ├── index_delivery.py      # 预览/最终索引交付与工作线程清理
│   └── view.py                # 任务树分栏、可见状态提示和正文编辑
│
├── tools/                     # 浮动工具窗口
│   ├── __init__.py
│   └── ai_translator/         # AI 翻译工具（子包）
│       ├── __init__.py
│       ├── ai_translator_window.py          # AITranslatorWindow 配置窗口
│       ├── task_session.py                 # 固定输入、成果与局部合入
│       ├── task_adapter.py                 # TaskRuntime 适配
│       ├── task_worker.py                  # 统一任务后台线程
│       ├── task_progress.py                # 统一进度窗口
│       ├── task_history_dialog.py          # 按工程隔离的任务历史
│       └── task_registry.py                # 后台任务窗口登记
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

**路径**: `src/transbridge/ui/projection_types.py`（`context.py` 重导出）

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
    source_snapshot: object = None      # 已保存来源的捕获字节
    format_id: object = None            # 来源格式资格
```

`plugin=None` 不足以判定来源为 EET：保存后恢复的插件也可能只保留 `source_snapshot`。任务树能力由 `format_id`、正式来源登记及插件/EET 路径判断；插件快照可重建任务目录。写回资格由操作层判定。`strings_lang` 记录解析时选择的语言。

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

**职责**：通过 `WorkspaceShell` 组合开始、工作台和 ParaTranz 主页面，协调全局状态、菜单及独立词条编辑窗口。

**结构**:
```
MainWindow
├── MenuBar
│   ├── 小工具 → AI 自动翻译
│   ├── 文件 → 刷新项目列表 / 设置 / 退出
│   ├── 账户 → 我的信息 / 私信
│   └── 帮助 → 关于
├── WorkspaceShell（主导航与页面栈）
│   ├── 开始
│   ├── WorkbenchWidget（工作台）
│   └── ParaTranzWidget（ParaTranz 管理）
├── EntryEditorDialog（按需打开的非模态独立窗口）
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

**职责**：显示解析／操作进度、全部／未翻译／有疑问／已翻译摘要，以及支持选择、搜索、筛选和排序的词条表格。

`TranslationTable` 使用 `TranslationTableModel`，七列依次为复选框、序号、标签、Key、原文、译文和类型／状态。搜索及筛选只改变表格投影；完整集合继续由 `AppContext.collection` 提供。

公共接口区分标记、实际行选和筛选投影：`get_selected_entries()`／`selected_entry_ids()` 返回历史兼容的有标签条目，`selected_row_entry_ids()` 返回实际选择行，`filtered_entries()` 返回包含离屏行的完整筛选结果。`editor_navigation_keys()` 按当前显示顺序提供打开时导航，`get_filtered_count()` 返回筛选数量；`refresh(collection)` 由集合变化信号驱动。

双击数据列通过完整 `EntryKey` 打开非模态 `EntryEditorDialog`，复选框只选择，F2 保留译文行内编辑。有任务关联时导航使用完整任务／场景范围；首次使用先显示已有词条关系，再在后台补全源目录，状态提示独立于任务树分栏。详见 [词条编辑与任务上下文](dialogue-context-editor.md)。

表格刷新由 render session 与模型通知管理，不使用旧 `QTableWidget.itemChanged` 批量填充链路。双击激活直接处理鼠标事件，避免两次点击间模型刷新导致弹窗入口丢失。

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
- 批量写回：打开写回计划 → 写回各原位置或选择统一另存目录 → 预检 → 确认执行

**按钮状态控制**:
- 上传/下载：需 `collection` + `current_project` + `is_member`
- 写回：需完整来源快照、格式信息及 `collection`，统一进入安全写回计划
- 批量上传/下载按钮：需 `current_project` + `is_member`
- 批量写回按钮：至少两个来源且存在可写来源；无效来源保留失败回执，其他独立来源继续

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
| `WriteCard` | `cards/write_card.py` | 写回插件/XML，点击后打开含另存选项、预检和执行结果的写回计划 |

#### 批量操作通用对话框

各卡片均使用以下对话框：

| 类 | 用途 |
|----|------|
| `_SlotSelectDialog` | 插件选择对话框，全选/全不选，滚动区域支持大量插件 |
| `_BatchConfirmDialog` | 批量操作确认对话框，最大高度 400px，支持大量项目滚动查看 |
| `_BatchResultDialog` | 批量操作结果展示对话框，显示成功/失败/总数及详情 |

#### WriteCard — 写回与另存为

单文件和批量写回共用 `operations/write_dialog.py` 中的 `WritePlanDialog`，在原窗口完成选项、后台预检、确认和逐文件结果展示。

- 默认写回原位置，保持来源文件名，不自动添加 `_translated`。单文件输出路径旁的「另存为…」允许选择其他位置或自定义名称，「写回原位置」可恢复当前来源或选中的套用插件路径。
- 批量计划提供「另存目录（可选）」及「另存为…」目录选择按钮。留空写回各原位置；选定目录后，多个来源以各自原名保存到同一目录；「写回原位置」清空另存目录。
- 改变输出位置会清除覆盖确认并使旧预检失效；取消文件/目录选择不改变选项。覆盖已有输出必须显式确认，执行前为已有文件创建备份。
- 带本地化资源的写回计划提供「将 STRINGS 打包为 BSA」复选框，默认不勾选，每次打开仍默认生成散装文件。批量勾选只影响带本地化资源的插件；不含 STRINGS 的来源继续按原格式输出。切换此选项同样清除覆盖确认并要求重新预检。
- 同名文件及配套资源之间的输出冲突由预检检查；冲突来源不互相覆盖，独立可写来源可继续完成并返回逐来源结果。
- 仅插件来源提供「套用到其他插件」选项；普通写回始终使用已确认来源快照，不把已有输出当作模板。DSD 导出使用独立入口。

#### WriteCard — 插件与 STRINGS 输出

本地化插件的资源位于输出目录下的 `Strings/`，名称为 `{输出插件名去扩展名}_{Lang}.*strings`，使用来源解析时确认的语言。默认保持插件原名及资源原名；另存时如果用户主动改名，配套资源同步改名。

纯外部字符串翻译保留插件本体字节；另存时复制插件以提供完整输出。混合插件同时保存内嵌文本修改和字符串资源；非本地化插件将译文写入插件本体。插件及其全部配套资源沿用统一预检、备份、发布和失败恢复流程。

显式勾选 BSA 时，仅将本次完整 STRINGS 表打包到输出 BSA，不向正式目录同时写出散装表。归档使用现有资源发现的命名规则：普通插件为 `{插件名}.bsa`，基础游戏插件使用 `Skyrim - Interface.bsa`。归档成员逐字节核对后，还需通过字符串表及插件译文回读验证；BSA 与插件作为同一组输出备份、提交和失败恢复。

BSA 模式禁止覆盖来源依赖的原归档，避免仅包含 STRINGS 的输出替换原归档中的其他资源。目标位置存在同名散装表时要求选择其他另存目录，避免散装内容优先读取而遮住 BSA 译文。批量输出归档重名也会进入既有冲突检查；不自动合并原 BSA，不删除既有散装文件。

#### AI 助手 — 写回与界面能力一致

`writer.write_back` 保留 admin 权限、确认及授权目录要求。默认仍处理当前活动来源，未指定路径时写回原位置；覆盖必须显式传 `overwrite=true`，并先备份。

- `output_dir` 适用于 ESP/ESM/ESL、EET XML、XT XML，按来源原名另存到同一目录，不添加后缀。单文件 `path` 可自定义文件名并优先于 `output_dir`；保留 `target=strings` 必须提供目录且优先使用目录的旧语义。
- `source_keys` 是 `list_collections` 返回的 `key` 列表，只处理明确选择的来源，不切换活动集合；省略仍是当前来源。空列表、重复或未知 key 拒绝执行；多个来源不接受同一个 `path`，可指定统一 `output_dir` 或按原位置写回。
- `target` 保留 `esp/eet/xt/strings`；显式选择来源时可用 `auto`，混合批量保持每个来源自己的格式。
- `pack_bsa` 默认 `false`，继续生成散装 STRINGS；`true` 只对带本地化资源的插件打包，混合批量中的 XML 仍正常输出。沿用原归档、散装优先读取及输出重名保护。
- 批量先固定来源、译文和译名方案，检查全部授权及逐来源预检；无效来源选择、快照或授权直接拒绝建任务。独立来源的输出冲突/已有文件等预检失败保留回执，其他可写来源继续；全部阻断时不建任务。
- 单文件启动结果仍为 `{task_id,path}`；批量返回 `{task_id,paths,source_keys}`。等待终态后查看 `outcomes` 中的来源 key、状态和实际 `artifacts`。部分完成或取消会保留已提交文件及未执行来源的回执，不能只凭任务的成功/失败标记判断是否有文件写出。

界面和助手共用 `application/io/operation_write_batch.py` 的输出冲突及批量执行逻辑。助手文件撤销凭证按预检确定的实际目标记录插件、BSA、散装资源或 XML，整批一次捕获；撤销可恢复原文件并移除本轮新建输出，不包含未选择的来源。

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

**路径**：`src/transbridge/ui/tools/ai_translator/ai_translator_window.py`。

配置窗口通过 TaskLaunch 固定输入并提交 TaskRuntime；TaskRuntimeAiTaskAdapter、AiTaskWorker 和 AiTaskProgressWindow 负责统一执行、暂停、停止和进度。单来源、多来源、翻译及混合任务共用此流程，关闭工具窗口后仍可由后台任务登记重新打开。

TaskSession 保存固定请求、独立成果和合入状态。TaskRuntime 分配的 run ID 同步给成果账本和覆盖前证据。历史窗口及正在运行的窗口按工程身份筛选，不用共享存储目录判定归属。

结果通过当前工程命令执行局部三方合入，成功后保存工程；没有任务前后自动创建正式历史的第二套逻辑。旧报告和检查点由读取入口转换，不能调用已删除的旧 worker 或旧版本写入器。

配置、术语库、报告和恢复契约详见 [AI 翻译开发说明](ai_translator.md)；任务生命周期和冲突策略详见 [ADR-049](../adr/049-task-result-lifecycle.md)。

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
