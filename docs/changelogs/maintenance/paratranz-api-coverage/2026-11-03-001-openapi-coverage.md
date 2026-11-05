# ParaTranz OpenAPI 接口补全与注释校准

- 日期：2026-11-03
- Epic/Story：maintenance/paratranz-api-coverage（非计划维护）
- 官方依据：https://paratranz.cn/api-docs?format=json，读取版本 0.5.7。
- 目标：补齐 19 个接口，纠正现有路径和参数，重点完善调用契约注释，并补充离线测试。

## 实现

以下路径均相对于仓库根目录。

| 文件 | 操作与具体变化 | 原因 |
| --- | --- | --- |
| `src/transbridge/paratranz/api/paratranz_tasks_api.py` | 新增任务列表、创建、详情、操作、更新、删除、任务文件列表/添加/移除、词条关联共 10 个方法 | 覆盖 Tasks 全部接口；说明权限、完成状态限制、文件/词条模式、请求字段与响应结构 |
| `src/transbridge/paratranz/api/paratranz_announcements_api.py` | 新增公告列表、发布、详情、更新、删除 5 个方法 | 说明列表与详情权限区别、通知副作用、置顶和可见性语义 |
| `src/transbridge/paratranz/api/paratranz_suggestions_api.py` | 新增 `get_suggestions`，支持 scope/rate/ignore/force 和原始响应 | 说明串行调用、30 秒生产缓存、最多 30 条、包含关系及检索响应头；禁用自动重试 |
| `src/transbridge/paratranz/api/paratranz_files_api.py` | 新增 `get_file_content`，支持 rev/tid/dl | 区分归档 JSON 与原文附件 bytes，保留 BOM；关闭附件响应 |
| `src/transbridge/paratranz/api/paratranz_export_api.py` | 新增 `get_latest_artifact`；列表补分页并纠正注释；完善触发/下载注释并关闭下载响应 | 最近结果允许 JSON null；列表与最近结果权限和结构不同 |
| `src/transbridge/paratranz/api/paratranz_user_api.py` | 新增 `get_user_projects`；用户动态改为 `/users/.../activities`，补 operation/begin/end | 修复旧路径拼写并说明成员关系 ID 与项目 ID 的区别 |
| `src/transbridge/paratranz/api/paratranz_history_api.py` | 项目历史补 begin/end/keyword 和多 tid 说明；文件历史补 filename/begin/end | 补齐查询能力，纠正历史类型、uid/tid 生效条件与权限说明 |
| `src/transbridge/paratranz/api/paratranz_contribution_api.py` | 发送 begin，保留 start 别名；冲突别名抛 ValueError | 保留旧位置/关键字调用兼容性，避免错误时间筛选 |
| `src/transbridge/paratranz/api/paratranz_strings_api.py` | 列表补 task；批量修改改为官方 edit/items 请求体，支持逐条字段 | 核对文档时发现旧 update/id 请求不符；旧 update 调用转换为 edit/items，保留 0/空译文 |
| `src/transbridge/paratranz/api/__init__.py`、`src/transbridge/paratranz/__init__.py` | 导出三个新 API 类 | 同时支持 API 包和惰性公共包入口 |
| `src/transbridge/ui/paratranz/export_tab.py` | 最新导出信息改调 `get_latest_artifact` | 不再把管理员分页历史当作单个 Artifact |
| `tests/paratranz/test_tasks_api.py` | 新增 36 项契约测试 | 验证 10 个接口、请求序列化、响应、取消、错误和写入重试边界 |
| `tests/paratranz/test_announcements_api.py` | 新增 20 项契约测试 | 验证 5 个接口及显式 False、查询、写入、权限错误和取消 |
| `tests/paratranz/test_query_api_contract.py` | 新增 22 项契约测试 | 验证用户项目、动态路径、筛选、重复 tid、begin/start 兼容性 |
| `tests/paratranz/test_export_content_suggestions_api.py` | 新增 23 项契约测试 | 验证 null、分页、JSON/附件、BOM、建议诊断/限流、task、批量编辑及公共导出 |
| `tests/ui/test_paratranz_secondary_state.py` | 新增最近导出元数据回归 | 执行后台回调并验证真实标签内容 |
| `tests/contracts/io/test_paratranz_facades.py` | 更新公共导出集合断言 | 纳入三个新类 |

新增及修正方法的中文注释覆盖路径、参数含义、返回值和官方声明的权限/限制；不为官方未说明的权限自行作保证。
公告和任务写操作不自动重试，避免未知结果后重复通知或修改远端状态。

## 验证证据

1. 读取官方 JSON，并用 AST 提取 API 封装的 HTTP 方法与路径（归一化路径变量）：官方 67 个操作，覆盖 67，缺失 0。此检查仅证明路由覆盖，不等同于真实服务验证。
2. `uv run pytest tests/paratranz/test_announcements_api.py tests/paratranz/test_tasks_api.py tests/paratranz/test_query_api_contract.py tests/paratranz/test_export_content_suggestions_api.py tests/contracts/io/test_paratranz_facades.py tests/ui/test_paratranz_secondary_state.py -q`：**144 passed**。
3. `uv run pytest tests/paratranz tests/contracts/paratranz tests/contracts/io/test_paratranz_facades.py tests/ui/test_paratranz_secondary_state.py tests/ui/test_paratranz_async_lifecycle_regressions.py tests/ui/test_paratranz_profile_contribution_state.py -q`：首次 **561 passed、6 failed、1 skipped**。其中公共导出集合断言随新增 API 更新，并已在上项复验通过；剩余 5 项为下述既有失败。
4. 独立 Python 进程通过导入钩子加载 `git show HEAD:<本次修改的生产模块>` 原始源码，不改写工作区，再执行 `pytest.main` 对应 `tests/paratranz/test_entry_upload_order.py`、`tests/paratranz/test_entry_upsert.py`、`tests/paratranz/test_file_transport_keys.py`：**18 passed、相同 5 failed**，确认不是本次 API 修改引入。
5. `uv run ruff check src tests`：通过。
6. `uv run ruff format --check src tests`：通过。
7. `git diff --check`：通过。未修改依赖、锁文件或其他已有 UI 变更。

测试走 Requests 请求准备/内存响应或现有本地 HTTP 契约设施，不向真实项目写入。uv 默认缓存受沙箱限制，改用获准的原有 uv 环境执行。

## 兼容性与遗留项

- 无数据迁移。旧贡献接口 start 位置和关键字参数保留，发送为官方 begin；旧批量 update 调用保留并转换为官方 edit/items。别名仅在未来明确移除旧公共接口时才可删除。
- 既有失败：`test_entry_upload_order.py` 两项缺少当前工程版本；`test_entry_upsert.py` 两项测试上下文缺 config；`test_file_transport_keys.py` 一项重复本地身份在构建上传材料时已被拒绝，与原断言阶段不符。本次不修改这些无关流程。
- 未执行全仓库测试或真实 ParaTranz 业务请求。已有真实术语集成测试因未显式配置专用凭据/项目而跳过；真实账号权限和服务端运行行为仍需部署环境验证。
