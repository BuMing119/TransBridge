# paratranz 模块

## 概述

ParaTranz 平台集成模块，提供 API 客户端、配置管理和文件上传/下载工作流。作为 TransBridge 与 ParaTranz 协作翻译平台之间的桥梁，支持项目同步、译文导入导出等功能。

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
└── workflow/                   # 上传/下载工作流
    ├── __init__.py
    ├── artifact.py            # 导出工作流（触发导出 + 下载）
    ├── uploader.py            # 上传工作流
    └── downloader.py          # 下载工作流
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
- 429 错误自动重试（最多 3 次，解析 `Retry-After` 头）
- 401 错误时在控制台打印当前 token（调试用）
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
    │       ├─ 429 → 读取 Retry-After，等待后重试（最多 3 次）
    │       ├─ 204 → 返回 None
    │       ├─ 401 → 打印 token，抛出 RuntimeError
    │       ├─ 其他错误 → 抛出 RuntimeError
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

### ParaTranzUploader

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

**工作流程**:

```
upload_collection(collection, project_id, file_filter, translation_mode, path_mapping, file_id_override, prefetched_maps)
    │
    ├─► 1. 导出到分类 JSON 文件（临时目录）
    │       export_to_categorized_json_files(collection, tmp_dir)
    │
    ├─► 2. 按 file_filter 过滤文件列表（None = 全部）
    │
    ├─► 3. 获取已有文件列表
    │       ├─ [prefetched_maps 存在] 直接使用传入的映射（避免重复 API 调用）
    │       └─ [否则] 调用 _fetch_file_maps() 查询 API
    │           ├─ 建立 name → id 映射
    │           ├─ 建立 full_path → id 映射（支持 path_mapping）
    │           └─ 记录同名文件冲突信息（写入 result.name_conflicts）
    │
    ├─► 4. 逐文件处理：
    │       ├─ 查找 file_id（优先级从高到低）：
    │       │       ├─ [file_id_override 存在] 直接使用用户指定的 file_id
    │       │       ├─ [path_mapping 存在]    使用完整路径匹配
    │       │       └─ [否则]                 使用文件名匹配
    │       │
    │       ├─ 已存在 → [do_reupload] reupload_file（更新原文）
    │       │            [do_trans]    update_file_translation（导入译文）
    │       │            ※ 纯译文模式下译文失败视为跳过；搭配原文更新时译文失败静默忽略
    │       └─ 不存在 → [do_reupload] upload_file（新建，返回值含新 file_id）
    │                    [do_trans]    update_file_translation（用新 file_id）
    │                    ※ 纯译文模式下新建文件直接跳过
    │
    └─► 5. 返回 UploadResult
```

**UploadResult 数据类**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `created` | int | 新建文件数 |
| `updated` | int | 更新原文文件数 |
| `skipped` | int | 因错误跳过的文件数 |
| `translation_updated` | int | 成功导入译文的文件数 |
| `files` | list[str] | 成功处理的文件名列表 |
| `name_conflicts` | dict[str, list[dict]] | 同名文件冲突信息（文件名 → 文件信息列表）|

**同名文件冲突处理（UI 流程）**:

分类上传时，UI 会在上传前（而非上传后）自动完成两阶段处理，且**只调用一次 `list_files` API**：

```
Phase 1 — detect_conflicts(progress_callback)（后台）
    │
    ├─► 查询 ParaTranz 文件列表（单次 API 调用）
    ├─► 进度回调: "正在获取 ParaTranz 文件列表..."
    └─► 返回 (conflicts, FileMaps)
            │
            ├─ 无冲突 → 直接进入 Phase 2（传入 FileMaps）
            └─ 有冲突 → 弹出 _ConflictResolveDialog
                            每个冲突文件对应一个下拉框，列出所有同名候选
                            用户选择各冲突文件对应的目标（folder + id）
                            ↓
                        得到 file_id_override: dict[str, int]
                        ↓
                        进入 Phase 2（传入 file_id_override + FileMaps）

Phase 2 — upload_collection(..., file_id_override=..., prefetched_maps=FileMaps)（后台）
    │
    ├─► 直接使用 prefetched_maps，跳过 list_files API 调用
    └─► 使用 file_id_override 直接定位冲突文件，跳过名称/路径匹配
```

**性能优化**：通过在两阶段间传递 `FileMaps`，避免了重复的网络请求。当冲突解决对话框显示时，文件列表数据已通过 `FileMaps` 缓存，不会再次查询 ParaTranz API。

**ConflictInfo 数据类**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `local_name` | str | 本地文件名（如 `"人名.json"`）|
| `candidates` | list[dict] | ParaTranz 上所有同名文件，每个含 `id`, `name`, `folder` 字段 |

**FileMaps 数据类**:

用于在两阶段上传流程中传递已获取的文件映射，避免重复 API 调用。

| 字段 | 类型 | 说明 |
|------|------|------|
| `existing` | dict[str, int] | 文件名 → file_id 映射 |
| `path_based` | dict[str, int] | 完整路径 → file_id 映射 |
| `name_to_files` | dict[str, list[dict]] | 文件名 → [文件信息列表]（用于冲突检测）|

**file_id_override 与 prefetched_maps 参数**:

- `file_id_override`: 直接指定文件名到 `file_id` 的映射，优先级高于 `path_mapping` 和文件名匹配，用于用户手动解决同名冲突的场景。
- `prefetched_maps`: 传入 `FileMaps` 对象复用已获取的文件映射，避免重复调用 `list_files` API。

```python
# UI 冲突解决后传入（同时复用 file_maps 避免重复查询）
result = uploader.upload_collection(
    collection,
    project_id=12345,
    file_id_override={"人名.json": 12345, "物品.json": 67890},
    prefetched_maps=file_maps,  # 复用 detect_conflicts 返回的 FileMaps
)

# 程序化场景：仅用 prefetched_maps 避免重复 API 调用
conflicts, file_maps = uploader.detect_conflicts(project_id, local_names)
result = uploader.upload_collection(
    collection,
    project_id=12345,
    prefetched_maps=file_maps,  # 跳过 list_files 查询
)
```

**文件路径映射**:

当 ParaTranz 项目中的文件被移动到子文件夹后，可使用 `path_mapping` 参数指定完整路径（供程序化场景使用；交互式场景通过冲突对话框处理）。
注意：ParaTranz API 使用 `folder` 字段而非 `path`。

```python
path_mapping = {
    "人名.json": "上古卷轴5/人物/人名.json",
    "物品.json": "上古卷轴5/物品/物品.json",
}

result = uploader.upload_collection(
    collection,
    project_id=12345,
    path_mapping=path_mapping,
)
```

**大文件自动拆分**:

`upload_collection_as_single()` 支持大文件自动拆分上传：

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

### ParaTranzDownloader

从 ParaTranz 项目下载译文并合并到本地 `TranslationEntryCollection`。

**路径**: `src/transbridge/paratranz/workflow/downloader.py`

**工作流程**:

```
download_to_collection(project_id, collection, min_stage, file_ids)
    │
    ├─► 1. 获取项目所有文件列表
    │
    ├─► 2. 逐文件处理：
    │       ├─ get_file_translation(project_id, file_id) → strings
    │       └─ 遍历 strings：
    │               ├─ stage < min_stage 或 translation 为空 → 跳过
    │               ├─ key 不在本地集合 → 跳过
    │               └─ 匹配成功 → 更新本地 entry
    │
    └─► 3. 返回 DownloadResult
```

**匹配规则**: ParaTranz 条目的 `key` 字段 == 本地条目的 `entry.id`

**DownloadResult 数据类**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `merged` | int | 成功合并到本地集合的词条数 |
| `skipped_no_match` | int | key 在本地集合中不存在的词条数 |
| `skipped_low_stage` | int | stage 不足或译文为空，跳过的词条数 |
| `total_strings` | int | 从 ParaTranz 拉取的词条总数 |

---

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

```python
from src.transbridge.paratranz.workflow.uploader import ParaTranzUploader

uploader = ParaTranzUploader(config)
result = uploader.upload_collection(
    collection,
    project_id=12345,
    translation_mode="trans_safe",  # 不覆盖人工编辑
    file_filter={"人名.json", "物品.json"},  # 只上传这两个文件；None = 全部
    progress_callback=lambda i, t, name: print(f"[{i+1}/{t}] {name}")
)
print(f"新建: {result.created}, 更新: {result.updated}, 跳过: {result.skipped}")
```

### 上传前检测同名文件冲突（程序化场景）

UI 已自动处理冲突解决流程。在程序化场景中，可手动调用：

```python
from src.transbridge.paratranz.workflow.uploader import ParaTranzUploader

uploader = ParaTranzUploader(config)

# 先检测冲突（返回冲突列表 + 文件映射，避免重复 API 调用）
local_names = {"人名.json", "物品.json", "对话_[任务名].json"}
conflicts, file_maps = uploader.detect_conflicts(
    project_id=12345,
    file_names=local_names,
    progress_callback=lambda i, t, msg: print(f"[{i}/{t}] {msg}")
)

if conflicts:
    for c in conflicts:
        print(f"冲突文件: {c.local_name}")
        for f in c.candidates:
            folder = f.get('folder', '') or '根目录'
            print(f"  候选: {folder}/{f['name']} (id={f['id']})")

# 手动指定冲突文件要更新的目标 file_id
# 传入 prefetched_maps 避免重复查询
result = uploader.upload_collection(
    collection,
    project_id=12345,
    file_id_override={"人名.json": 12345},  # 直接指定 file_id，跳过名称匹配
    prefetched_maps=file_maps,  # 复用已获取的文件映射
)
```

### 上传到子文件夹（文件被移动后，程序化场景）

当 ParaTranz 项目中的文件被移动到子文件夹后，可使用 `path_mapping` 指定完整路径。
交互式场景下，UI 冲突解决对话框会自动处理此情况，无需手动指定。
注意：ParaTranz API 使用 `folder` 字段而非 `path`。

```python
from src.transbridge.paratranz.workflow.uploader import ParaTranzUploader

uploader = ParaTranzUploader(config)

path_mapping = {
    "人名.json": "上古卷轴5/人物/人名.json",
    "物品.json": "上古卷轴5/物品/物品.json",
    "对话_[任务名].json": "上古卷轴5/对话/对话_[任务名].json",
}

result = uploader.upload_collection(
    collection,
    project_id=12345,
    translation_mode="orig_only",
    path_mapping=path_mapping,
)
```

### 下载译文

```python
from src.transbridge.paratranz.workflow.downloader import ParaTranzDownloader

downloader = ParaTranzDownloader(config)
result = downloader.download_to_collection(
    project_id=12345,
    collection=collection,
    min_stage=1,  # 仅接受已翻译及以上
    progress_callback=lambda i, t, name: print(f"[{i+1}/{t}] {name}")
)
print(f"合并: {result.merged}, 未匹配: {result.skipped_no_match}")
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
| 401 | 认证失败 | 检查 token 是否有效，控制台会打印当前 token |
| 403 | 权限不足 | 检查用户是否有项目访问权限 |
| 429 | 请求过频 | 自动重试（最多 3 次），读取 `Retry-After` 头 |
| 413 | 文件过大 | 使用 `upload_collection_as_single()` 自动拆分 |

### 异常类

所有 API 错误抛出 `RuntimeError`，格式为：

```python
raise RuntimeError(f"API Error {status_code}: {response_text}")
```

---

## 坑点与注意事项

1. **配置文件路径**: 打包环境下变为 `%APPDATA%/TransBridge/data/`
2. **SSL 兼容性**: `_SSLAdapter` 设置 `ssl.OP_IGNORE_UNEXPECTED_EOF`，解决 Python 3.12 下连接 paratranz.cn 时的 `UNEXPECTED_EOF_WHILE_READING` 报错
3. **401 调试**: 401 错误会在控制台打印当前 token（调试用，生产环境注意安全）
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
