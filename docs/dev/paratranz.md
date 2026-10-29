# paratranz 模块

## 概述

ParaTranz 平台集成模块，提供 API 客户端、配置管理和文件上传/下载工作流。作为 TransBridge 与 ParaTranz 协作翻译平台之间的桥梁，支持项目同步、译文导入导出等功能。

当前普通/分类、单独/批量 GUI 共用文件 API 流程，普通使用根目录 `<插件名>.json`，分类使用 `<插件名>/<分类>.json`，无额外模式目录，也不跨路径整理。原文限定词条使用可还原的哈希传输 key，保持在正常文件内；本地与插件 key 不变。下载按插件层级读取全部分类文件、在本地匹配，本轮结束清理临时输入。API 语义和排查见[文件上传开发说明](paratranz-categorized-upload.md)。下文 `OrdinaryUploadAdapter`、`ParaTranzUploader` 属于兼容接口。

---

## 目录结构

```
paratranz/
├── __init__.py
├── config_manager.py          # 配置管理（ParatranzConfig + LLMConfig）
├── paratranz_client.py        # API 客户端基类
├── api/                       # 各 API 模块
│   ├── __init__.py
│   ├── paratranz_project_api.py      # 项目 CRUD
│   ├── paratranz_files_api.py        # 文件上传/下载/列表
│   ├── paratranz_strings_api.py      # 翻译条目 CRUD
│   ├── paratranz_terms_api.py        # 术语管理
│   ├── paratranz_members_api.py      # 成员管理
│   ├── paratranz_contribution_api.py # 贡献统计
│   ├── paratranz_export_api.py       # 导出翻译文件
│   ├── paratranz_history_api.py      # 历史记录
│   ├── paratranz_issues_api.py       # 问题反馈
│   ├── paratranz_mails_api.py        # 站内信
│   └── paratranz_user_api.py         # 用户信息
├── download_paths.py           # 插件层级范围与新旧布局选择
└── workflow/                   # 兼容上传与导出工作流
    ├── __init__.py
    ├── artifact.py            # 导出工作流（触发导出 + 下载）
    └── uploader.py            # 单文件兼容上传
```

---

## 核心类

### ParatranzConfig

配置管理器，负责 API 认证信息持久化。

**路径**: `src/transbridge/paratranz/config_manager.py`

**配置文件**: `data/paratranz_config.ini`（与 `LLMConfig` 共享）

**INI 节**: `[api]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `token` | str | None | API 认证令牌 |
| `user_id` | int | None | 当前用户 ParaTranz 数字 ID（缓存） |
| `base_url` | str | `https://paratranz.cn/api` | API 基础 URL |
| `timeout` | int | 10 | 请求超时时间（秒） |

**关键方法**:

| 方法 | 说明 |
|------|------|
| `save_to_file()` | 持久化配置到 INI 文件 |
| `load_from_file(token)` | 类方法，从 INI 加载配置 |
| `create_or_load(token)` | 类方法，加载或创建新配置 |
| `get_data_dir()` | 静态方法，获取数据目录路径（打包/开发环境自适应） |
| `get_config_file_path()` | 静态方法，获取配置文件完整路径 |
| `update_token(new_token)` | 更新认证令牌 |
| `get_headers()` | 获取完整请求头（含 Authorization） |

**数据目录自适应**:

```python
# 打包环境: %APPDATA%/TransBridge/data/
# 开发环境: {项目根}/data/

import sys
if getattr(sys, "frozen", False):
    # PyInstaller 打包环境
    data_dir = os.path.join(os.environ["APPDATA"], "TransBridge", "data")
else:
    # 开发环境
    data_dir = os.path.join(project_root, "data")
```

---

### LLMConfig

AI 翻译功能配置，与 `ParatranzConfig` 共享同一 INI 文件的 `[llm]` 节。

**路径**: `src/transbridge/paratranz/config_manager.py`

**INI 节**: `[llm]`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `provider` | str | `openai_compatible` | LLM 提供商：`openai_compatible` / `anthropic` |
| `api_key` | str | "" | API 密钥 |
| `base_url` | str | `https://api.openai.com/v1` | API 基础 URL（本地模型可修改） |
| `model` | str | "" | 模型名称 |
| `max_concurrent` | int | 3 | 最大并发请求数 |
| `max_tokens_per_batch` | int | 2500 | 每批输入 token 上限（用于拆批） |
| `max_output_tokens` | int | 0 | 输出 token 限制（0=供应商支持时不设置应用上限；Anthropic 必须配置正数） |
| `term_priority` | list | `["dynamic", "paratranz", "json", "excel"]` | 术语来源优先级 |
| `local_json_path` | str | "" | 本地术语 JSON 文件路径 |
| `local_excel_path` | str | "" | 本地术语 Excel 文件路径 |
| `excel_original_col` | str | "A" | Excel 原文列 |
| `excel_translation_col` | str | "B" | Excel 译文列 |
| `game_profile` | str | `skyrim_se` | 游戏配置文件名（`data/prompts/games/{profile}.toml`） |
| `target_lang` | str | `zh_CN` | 目标语言配置文件名（`data/prompts/langs/{lang}.toml`） |
| `enable_semantic_match` | bool | True | 是否启用语义召回 |
| `semantic_similarity_threshold` | float | 0.7 | 语义相似度阈值（高阈值减少噪声） |
| `semantic_top_k` | int | 5 | 每条原文召回的候选数 |
| `max_terms_per_batch` | int | 50 | 每批次术语表硬上限 |
| `embedding_provider` | str | `local` | Embedding 提供商：`local` / `openai` / `custom` |
| `embedding_model` | str | `text-embedding-3-small` | API Embedding 模型名称 |
| `embedding_api_key` | str | "" | Embedding API Key（留空复用 `api_key`） |
| `embedding_base_url` | str | "" | Embedding API URL（留空复用 `base_url`） |
| `embedding_local_model` | str | `paraphrase-multilingual-MiniLM-L12-v2` | 本地 Embedding 模型名称 |

**静态方法**:

| 方法 | 说明 |
|------|------|
| `get_ai_translator_dir(esp_stem)` | 获取 AI 翻译数据目录 `data/ai_translator/{esp_stem}/` |

---

### ParatranzClient

API 客户端基类，封装 HTTP 请求和错误处理。

**路径**: `src/transbridge/paratranz/paratranz_client.py`

**关键特性**:
- 使用 `requests.Session` + 自定义 `_SSLAdapter`，设置 `ssl.OP_IGNORE_UNEXPECTED_EOF`，兼容 paratranz.cn 服务端不发 TLS `close_notify` 的行为（Python 3.12 默认拒绝此行为）
- 仅允许重试的操作按 `RetryPolicy` 处理限流、暂时不可用及传输异常；默认最多 4 次尝试，读取 `Retry-After`。分类 gateway 明确只尝试一次
- 401 错误抛出带认证类别的 `ExternalServiceError`；诊断隐藏已知凭据，不打印 Token
- 支持 RFC 5987 文件名编码（解决中文文件名乱码）

**核心方法**:

| 方法 | 说明 |
|------|------|
| `_request(method, endpoint, **kwargs)` | 通用 HTTP 请求，返回 JSON 或 None |
| `_request_multipart(method, endpoint, body, content_type)` | 发送预先编码的 multipart 请求 |

**请求流程**:

```
ParatranzClient._request()
    │
    ├─► 构建 URL: {base_url}{endpoint}
    │
    ├─► 通过 self._session（含 _SSLAdapter）发送请求
    │
    ├─► 处理响应:
    │       ├─ 可重试错误 → 核对操作与重试策略，等待后重试或抛出类型化错误
    │       ├─ 204 → 返回 None
    │       ├─ 401 → 抛出 authentication 类别的 ExternalServiceError
    │       ├─ 其他错误 → 抛出对应类别的 ExternalServiceError
    │       └─ 成功 → 返回 JSON 或 None
    │
    └─► 返回结果
```

---

## API 模块

所有 API 模块继承自 `ParatranzClient`，调用 `_request()` 访问对应端点。

### API 端点对照表

| 模块 | 端点前缀 | 主要功能 |
|------|----------|----------|
| `ParatranzProjectAPI` | `/projects` | 项目 CRUD |
| `ParatranzFilesAPI` | `/projects/{id}/files` | 文件上传/下载/管理 |
| `ParatranzStringsAPI` | `/projects/{id}/strings` | 翻译条目 CRUD |
| `ParatranzTermsAPI` | `/projects/{id}/terms` | 术语管理 |
| `ParatranzMembersAPI` | `/projects/{id}/members` | 成员管理 |
| `ParatranzContributionAPI` | `/projects/{id}/contributions` | 贡献统计 |
| `ParatranzExportAPI` | `/projects/{id}/artifacts` | 导出翻译文件 |
| `ParatranzHistoryAPI` | `/projects/{id}/history` | 历史记录 |
| `ParatranzIssuesAPI` | `/projects/{id}/issues` | 问题反馈 |
| `ParatranzMailsAPI` | `/mails` | 站内信 |
| `ParatranzUserAPI` | `/users` | 用户信息 |

### ParatranzFilesAPI

**路径**: `src/transbridge/paratranz/api/paratranz_files_api.py`

**关键方法**:

| 方法 | HTTP | 端点 | 说明 |
|------|------|------|------|
| `list_files(project_id)` | GET | `/projects/{id}/files` | 获取文件列表 |
| `list_files_with_path(project_id)` | GET | `/projects/{id}/files` | 获取文件列表，返回完整路径映射 |
| `find_file_by_name(project_id, filename)` | GET | `/projects/{id}/files` | 根据文件名查找所有匹配文件 |
| `upload_file(project_id, filepath, path)` | POST | `/projects/{id}/files` | 上传新文件 |
| `reupload_file(project_id, file_id, filepath)` | POST | `/projects/{id}/files/{fid}` | 重新上传（更新原文） |
| `get_file_translation(project_id, file_id)` | GET | `/projects/{id}/files/{fid}/translation` | 获取文件翻译数据 |
| `update_file_translation(project_id, file_id, filepath, force)` | POST | `/projects/{id}/files/{fid}/translation` | 更新文件译文 |
| `delete_file(project_id, file_id)` | DELETE | `/projects/{id}/files/{fid}` | 删除文件 |

**文件路径处理**:

当 ParaTranz 项目中使用文件夹组织文件时，需要使用完整路径来定位文件。

注意：ParaTranz API 的 `name` 字段可能包含路径（如 `"path/to/filename.csv"`），也可能分开为 `name` 和 `folder` 字段。SDK 会自动处理这两种情况。

```python
# 获取完整路径映射 {full_path: file_id}
path_mapping = api.list_files_with_path(project_id)
# 返回: {"上古卷轴5/人物/人名.json": 123, "上古卷轴5/物品/物品.json": 456}

# 根据文件名查找所有匹配（支持同名文件在不同路径）
files = api.find_file_by_name(project_id, "人名.json")
# 返回: [{"id": 123, "name": "人名.json", "folder": "上古卷轴5/人物"}, ...]
```

**RFC 5987 文件名编码**:

```python
def _make_file_field(field_name: str, filename: str, data: bytes) -> RequestField:
    """
    urllib3 2.x 改为 WHATWG 标准，将文件名以原始 UTF-8 字节放入 quoted-string，
    而 ParaTranz 服务端按 Latin-1 解析 HTTP 头，导致中文文件名乱码。
    RFC 5987 格式 (filename*=UTF-8''...) 可被所有现代服务端正确识别。
    """
    rf = RequestField(name=field_name, data=data, filename=None)
    rf.make_multipart()
    encoded = quote(filename, safe="")
    rf.headers["Content-Disposition"] = (
        f'form-data; name="{field_name}"; '
        f'filename="{filename}"; '
        f"filename*=UTF-8''{encoded}"
    )
    return rf
```

### ParatranzStringsAPI

**路径**: `src/transbridge/paratranz/api/paratranz_strings_api.py`

**词条状态 (stage)**:

| 值 | 状态 |
|----|------|
| 0 | 未翻译 |
| 1 | 已翻译 |
| 2 | 有疑问 |
| 3 | 已检查 |
| 5 | 已审核 |
| 9 | 已锁定 |
| -1 | 已隐藏 |

**关键方法**:

| 方法 | HTTP | 端点 | 说明 |
|------|------|------|------|
| `list_strings(project_id, page, page_size, file, stage, detailed)` | GET | `/projects/{id}/strings` | 分页获取词条列表 |
| `create_string(project_id, data)` | POST | `/projects/{id}/strings` | 创建词条 |
| `get_string(project_id, string_id)` | GET | `/projects/{id}/strings/{sid}` | 获取单个词条 |
| `update_string(project_id, string_id, data)` | PUT | `/projects/{id}/strings/{sid}` | 更新词条 |
| `delete_string(project_id, string_id)` | DELETE | `/projects/{id}/strings/{sid}` | 删除词条（仅管理员） |
| `batch_strings(project_id, op, ids, stage, translation)` | PUT | `/projects/{id}/strings` | 批量操作 |

### ParatranzExportAPI

**路径**: `src/transbridge/paratranz/api/paratranz_export_api.py`

**关键方法**:

| 方法 | HTTP | 端点 | 说明 |
|------|------|------|------|
| `get_artifacts(project_id)` | GET | `/projects/{id}/artifacts` | 获取最近导出结果 |
| `trigger_export(project_id)` | POST | `/projects/{id}/artifacts` | 触发导出（仅管理员） |
| `download_artifacts(project_id, save_path)` | GET | `/projects/{id}/artifacts/download` | 下载导出压缩包 |

---

## 工作流模块

### GUI 文件上传

当前普通/分类 GUI 共用 `application/file_upload.py` 与 `paratranz/file_publication.py`。
每次读取一次文件列表，按完整路径定位后整文件创建、增量更新原文或批量导入译文/状态；
不调用逐词条写接口、不逐条回读、不保存上传关联。安全/强制覆盖、哈希跳过由服务端执行。
API 参数、权限、423/429、取消和下载范围见 [文件上传说明](paratranz-categorized-upload.md)。

### ParaTranzUploader（兼容调用）

将本地 `TranslationEntryCollection` 上传到 ParaTranz 项目。

**路径**: `src/transbridge/paratranz/workflow/uploader.py`

**上传模式**:

```
translation_mode:
  "orig_only"   — 仅更新原文，不碰译文（默认）
  "trans_safe"  — 仅导入译文，不覆盖已人工编辑的词条；新建文件跳过
  "trans_force" — 仅导入译文，强制覆盖所有译文；新建文件跳过
  "both"        — 更新原文并安全导入译文（不覆盖人工编辑）；新建文件创建后再导入译文
```

**逻辑矩阵**:

| mode | `reupload_file` | `update_file_translation` | force | 新文件 |
|------|:-:|:-:|:-:|------|
| `orig_only` | ✓ | — | — | `upload_file` |
| `trans_safe` | — | ✓ | False | 跳过 |
| `trans_force` | — | ✓ | True | 跳过 |
| `both` | ✓ | ✓ | False | `upload_file` → `update_file_translation` |

**当前兼容入口**：`upload_collection_as_single(collection, project_id, filename, ...)` 将完整集合导出为单个 JSON，按根目录完整路径定位，遇到 413 对半拆分。返回 UploadResult 的 created、updated、skipped、translation_updated 和 files。

分类上传使用 application.file_upload 的文件执行器和共享分类结果；本地分类导出由 export_to_categorized_json_files 写入同一套相对文件。旧分类上传、手动冲突映射及对应 UI 已移除。

**大文件自动拆分**:

`upload_collection_as_single()` 支持大文件自动拆分上传：

普通 GUI 的文件名由稳定插件名生成（保留 `.esm`/`.esp` 等扩展名，同名来源加稳定摘要），单独与批量一致。单文件方法只匹配根目录完整路径，不再按 basename 认领其他目录文件；拆分后的文件同样位于根目录。

```
_upload_entries_recursive(entries, stem, ext, ..., is_split=False)
    │
    ├─► 首次上传（is_split=False）→ 使用原始文件名（如 Plugin.json）
    │
    ├─► 尝试上传
    │       ├─ 成功 → 返回
    │       └─ 失败 (413/too large) → 对半拆分，递归上传（is_split=True）
    │
    └─► 分割后上传（is_split=True）→ 使用序号后缀（如 Plugin_1.json、Plugin_2.json）
```

**文件命名规则**:
| 场景 | 文件名 |
|------|--------|
| 文件未超过大小限制 | `{filename}` |
| 文件过大分割为N份 | `{stem}_1.ext`, `{stem}_2.ext`, ..., `{stem}_N.ext` |

---

### GUI 下载

GUI 单/多插件与 AI 助手 download_entries 共用应用层下载用例；GUI 使用“下载并保存”，助手传入当前或显式 source_ids。打开窗口只列出工程插件；点击后在同一后台 worker 中读取所选来源、下载、匹配并保存。

下载只定位插件层级：普通根文件、数字分片及插件目录下全部 JSON 文件。任务标题命名保持；下载前不运行分类器，不重建任务标题或分类路径，不以本地缺少标题排除云端文件，不建立目录索引或新增校验。对话引用的任务即使只在主文件中定义，也不影响其云端命名文件被读取。

新布局优先，缺失时才用省略插件后缀的旧布局。插件隔离、大小写消歧、全部启用来源共同决定旧名归属与同代 404 重定位保持，不混读两代内容。所选插件目录内的旧任务名、ID 和拆分文件一并匹配，其他插件目录不参与。

原有本地匹配按同插件精确原文、key 及已有译文确定候选，唯一非空译文自动采用，真正多译文在本轮确认。仅更新已有译文及对应状态，保留原文、上下文、顺序，不新增或删除源词条。

下载列表、文件内容、匹配输入和选择只留本轮内存，不写下载成果、合入回执、覆盖前内容或还原点。完成、失败、取消、关闭或改变插件/策略结束本轮时清理，下一次下载重新获取。窗口不再提供重开、另存下载成果或恢复覆盖前译文；旧下载成果也不再作为输入。助手不再返回可供 get_task_result/merge_task_result 处理的下载 result_id，不设项目分页 2000 条上限；只返回合入统计和本次未采用说明。正常诊断日志、工程来源成果和用户主动创建的工程历史保持原有职责。

成功合入后自动保存当前版本。至少一个所选来源完整读取成功时，也可保存已有未保存编辑；全部来源缺文件或失败时不宣称保存成功。保存失败保持原工作状态、清理下载输入，重试重新下载。既有原子保存、并发编辑和取消处理保持。

在线下载及离线 JSON 沿用已有传输 key 编解码。无需重传、改名、迁移或删除云端文件；历史工程包附件兼容保持。

当前工程使用已保存的来源原文并叠加最新 Variant 译文，只有显式更新来源才重读外部插件；来源成果属于工程输入，不是下载缓存。

application/sync/project_download.py 管理本轮下载、匹配和清理，download_merge.py 负责正式合入，download_run.py 负责自动接续，review_view.py 与 download_view.py 提供公共结果。UI 的 batch_download_session.py 只注入任务执行器并创建窗口；助手 _project_download.py 只适配来源参数、取消及请求许可。传输由 paratranz 层实现，两个入口都调用同一用例，应用层不导入 Qt 或 GUI。

### ArtifactWorkflow

触发 ParaTranz 导出，轮询完成状态，下载并解压压缩包。

**路径**: `src/transbridge/paratranz/workflow/artifact.py`

**工作流程**:

```
trigger_and_download(project_id, save_path, poll_interval, timeout)
    │
    ├─► 1. 记录当前最新 artifact 的 createdAt 时间 t0
    │
    ├─► 2. 调用 trigger_export() 触发新导出
    │
    ├─► 3. 轮询 get_artifacts()，直到出现 createdAt > t0 的新记录
    │       （或超时抛出 TimeoutError）
    │
    ├─► 4. 调用 download_artifacts() 下载压缩包
    │
    └─► 5. 返回保存路径
```

**注意**: ParaTranz 目前无独立 Job 状态查询接口，采用轮询 `artifacts.createdAt` 的降级策略。

**extract() 方法**: 解压导出压缩包到目标目录。

---

## 使用示例

### 配置初始化

```python
from src.transbridge.paratranz.config_manager import ParatranzConfig, LLMConfig

# 加载或创建配置
config = ParatranzConfig.create_or_load(token="your_token")
config.save_to_file()

# 加载 LLM 配置
llm_config = LLMConfig.load_from_file()
llm_config.model = "gpt-4o"
llm_config.save_to_file()
```

### 上传翻译条目

当前 GUI 通过共享文件执行器上传，接口和分类规则见[文件上传说明](paratranz-categorized-upload.md)。程序化使用仍保留的单文件兼容入口：

```python
from transbridge.paratranz.workflow.uploader import ParaTranzUploader

uploader = ParaTranzUploader(config)
result = uploader.upload_collection_as_single(
    collection,
    project_id=12345,
    filename="Plugin.esp.json",
    translation_mode="trans_safe",
    progress_callback=lambda current, total, name: print(current, total, name),
)
```

### 导出分类 JSON

本地导出与上传共用分类规则，任务原名优先于 Quest ID，任务文件位于输出目录的 `任务/` 子目录：

```python
from transbridge.converter.translation_entry_collection_export import export_to_categorized_json_files

export_to_categorized_json_files(collection, "output_dir")
```

两个脚本均使用命令行输入路径，详见[脚本使用说明](paratranz-categorized-upload.md#本地脚本与清理边界)。

### 下载译文

```python
from transbridge.paratranz.path_snapshot import CurrentPathSnapshotAdapter, current_category_paths

entries = tuple(collection)
adapter = CurrentPathSnapshotAdapter(
    config,
    "Plugin.esp.json",
    category_paths=current_category_paths("Plugin.esp.json", entries),
)
snapshots = adapter.fetch(12345, entries[0].identity.namespace, limit=100_000)
# 这是只读快照；本地合并由下载计划、复核和原子事务完成。
```

### 导出翻译文件

```python
from src.transbridge.paratranz.workflow.artifact import ArtifactWorkflow

workflow = ArtifactWorkflow(config)
zip_path = workflow.trigger_and_download(
    project_id=12345,
    save_path="export.zip",
    progress_callback=lambda msg: print(msg)
)

# 解压
files = workflow.extract(zip_path, "output_dir")
```

---

## 错误处理

### 常见错误

| 状态码 | 场景 | 处理方式 |
|--------|------|----------|
| 401 | 认证失败 | 检查账户凭据；日志保留错误类别并隐藏 Token |
| 403 | 权限不足 | 检查用户是否有项目访问权限 |
| 429 | 请求过频 | 仅可重试操作按策略处理 `Retry-After`；分类 gateway 不自动重试 |
| 413 | 文件过大 | 使用 `upload_collection_as_single()` 自动拆分 |

### 异常类

HTTP 客户端使用 `ExternalServiceError`，携带错误类别、可用状态码、请求编号及脱敏上下文；缺失凭据使用 `ParatranzCredentialRequiredError`。分类 gateway 还会校验返回身份及写后内容，校验失败可产生 `ValueError`。分类执行器会将这些异常转换为失败或结果待核实回执，并在转换前记录操作及堆栈；详情见[诊断说明](paratranz-categorized-upload.md)。

---

## 坑点与注意事项

1. **配置文件路径**: 打包环境下变为 `%APPDATA%/TransBridge/data/`
2. **SSL 兼容性**: `_SSLAdapter` 设置 `ssl.OP_IGNORE_UNEXPECTED_EOF`，解决 Python 3.12 下连接 paratranz.cn 时的 `UNEXPECTED_EOF_WHILE_READING` 报错
3. **诊断脱敏**: 认证错误按类别记录，已知凭据不进入日志；排查使用请求编号、操作编号和错误摘要
4. **目录自动创建**: `get_data_dir()` 会在目录不存在时自动创建
5. **中文文件名**: 使用 RFC 5987 编码解决 urllib3 2.x 的乱码问题
6. **导出轮询**: 无独立 Job 状态接口，采用轮询 `createdAt` 策略

---

## 依赖关系

```
paratranz
    │
    ├─► requests           # HTTP 请求（Session + _SSLAdapter）
    ├─► urllib3            # multipart 编码
    │
    └─► converter          # TranslationEntryCollection（workflow 依赖）
```

**被依赖**: `ui`, `ai_translator`（术语库来源之一）
