# 项目多插件的显式分类上传

- 日期：2026-10-23
- Epic / Story：`paratranz-categorized-upload` / S01～S05
- 计划：[plan](../../../../plans/paratranz-categorized-upload/plan.md)
- 需求：[FR3.4](../../../requirements.md)
- 决策：[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)
- 类型：分类上传、来源归属、部分完成恢复与下载往返

## 用户可见行为

只有点击独立的“分类上传”按钮才进入本流程。读取当前工程登记且启用的全部主来源，以及当前翻译版本的权威译文；支持多选插件与分类文件，先只读对照，再确认上传。普通上传和普通批量上传的入口与上传策略保持原流程。

文件按“插件目录 / 内容分类”组织。未识别内容归入“其他”；重名插件、任务对话与同 key 不同原文使用稳定标识消歧，不改写原 key。已经绑定的文件即使插件改名仍按 file_id 复用，预览显示真实云端路径。未知归属和歧义项暂缓，确定项可先执行。

“整理已有云端词条到分类文件”默认不勾选。显式选择后，只移动已可靠确认的词条，并核验 ID 和内容保留；不删除旧文件、不删除重建词条。日常更新只发送译文和状态。

每个成功的文件或词条关联及时保存；当前工程及版本的未保存译文也随该权威事务一并保存，不丢弃工作内容。取消、部分失败和结果不明显示逐项回执；再次对照核实已完成内容，禁止盲重发创建。工程、版本、账号、本地修订或远端内容变化会使旧确认失效。

建立分类绑定后，下载按来源拥有的文件过滤候选；尚未分类的来源排除其他来源已绑定的文件。确认过的旧布局词条仅按 ID 单独补读，不扫描整个旧混合文件。完全没有分类绑定的工程保持普通下载。普通下载更新译文后仍保留相同 ID 的文件/端点元数据。

## 逐文件变化

以下范围通过任务开始时的文件副本与最终内容比较确认，不将工作区已有的同步反馈、拖放建项和 AI 术语修改归入本记录。

- **新增 `src/transbridge/application/categorized_upload/models.py`**：定义来源、分类工件、远端快照、计划、回执和执行端口，应用层不依赖 Qt 或 HTTP。
- **新增 `src/transbridge/application/categorized_upload/classification.py`**：完整分类、稳定目录/对话名、重复 key 分文件及全来源上下文顺序。
- **新增 `src/transbridge/application/categorized_upload/planner.py`**：按 source_id、category_id、file_id、entry_id 规划；拒绝跨来源占用、失效引用、未知旧归属，选择过滤不改变全工程身份。
- **新增 `src/transbridge/application/categorized_upload/fingerprints.py`**：冻结本地、远端及计划指纹，阻止预览后修改投影继续执行。
- **新增 `src/transbridge/application/categorized_upload/executor.py`**：逐项写入及关联保存、取消、迁移结果核验、部分失败和结果不明回执。
- **新增 `src/transbridge/application/categorized_upload/__init__.py`**：显式导出分类上传契约。
- **新增 `src/transbridge/application/projects/categorized_upload.py`**：读取全部登记来源与当前 Variant 状态；校验来源指纹、作用域及归属；通过既有生命周期 CAS 和工作单元持久化关联。
- **修改 `src/transbridge/application/projects/gui_facade.py`**：仅增加可选分类服务注入及委托，保留既有保存和来源命令改动。
- **修改 `src/transbridge/bootstrap/persistence.py`**：组装分类服务并复用既有来源准备器、基线、生命周期和工作单元。
- **新增 `src/transbridge/paratranz/categorized_upload.py`**：完整远端快照、空分类文件创建、词条创建、译文更新和显式移动；写入无自动重试，写前核验、写后读取校验，支持取消和 Unicode 文件名。
- **新增 `src/transbridge/paratranz/pagination.py`**：按服务器声明的页数/总数确认快照完整性，兼容服务器缩小 pageSize，拒绝总数变化及不完整分页。
- **修改 `src/transbridge/paratranz/service.py`**：`list_entries` 增加可选 file_id 过滤与分页校验；新增按 ID 精确读取，校验返回身份。
- **修改 `src/transbridge/paratranz/api/paratranz_strings_api.py`**：单词条 GET 支持取消参数。
- **修改 `src/transbridge/application/ports/paratranz.py`**：远端 DTO 增加可选 file_id；普通上传 payload 不变。
- **新增 `src/transbridge/paratranz/categorized_snapshot.py`**：下载候选限制到所属文件，补读已确认旧词条，排除其他来源的分类文件。
- **新增 `src/transbridge/ui/operations/categorized_download_scope.py`**：使用同步请求已捕获的工程/版本身份读取绑定；无活动工程时沿用普通适配器。
- **修改 `src/transbridge/ui/operations/sync_preparation.py`、`src/transbridge/ui/operations/paratranz_sync.py`**：仅将下载预检及执行的快照构造接到同一归属适配入口。
- **修改 `src/transbridge/ui/operations/production_support.py`**：写回相同远端身份时复用当前引用的 metadata；新 ID 不继承旧 metadata，不改变同步指纹契约。
- **新增 `src/transbridge/ui/operations/categorized_upload_bridge.py`**：组装权威快照、成员权限检查、单次确认、后台执行和关联回执保存。
- **新增 `src/transbridge/ui/operations/categorized_upload_dialog.py`**：插件/分类选择、实际路径与预计变化、默认关闭的整理选项、确定项先执行、进度及取消收尾。
- **修改 `src/transbridge/ui/workbench/cards/upload_card.py`**：增加独立“分类上传”按钮并修正说明；原 `upload` 与 `batch_upload` 路由不变。
- **新增 `tests/application/categorized_upload/test_planning.py`**：16 项分类和规划测试。
- **新增 `tests/application/categorized_upload/test_execution.py`**：14 项执行、关联失败、取消、过期及再次对照测试。
- **新增 `tests/application/categorized_upload/test_adversarial_upload.py`**：8 项跨来源占用、未选来源身份与预览篡改测试。
- **新增 `tests/application/projects/test_categorized_upload.py`**：23 项权威快照、来源变更、CAS、持久化及无重新解析的作用域测试。
- **新增 `tests/paratranz/test_categorized_upload_gateway.py`**：36 项受控 HTTP 契约、分页、Unicode、迁移、创建不重试与批次内远端变化测试。
- **新增 `tests/paratranz/test_categorized_snapshot.py`**：8 项分类文件及确认旧词条的下载隔离测试。
- **新增 `tests/contracts/paratranz/test_categorized_unbound_source_download.py`**：8 项未分类来源隔离、普通入口和兼容上下文测试。
- **新增 `tests/ui/operations/test_categorized_upload_bridge.py`**：16 项生产组装、保存重开、源更新、显式迁移、重复执行及普通上传回归。
- **新增 `tests/ui/operations/test_categorized_upload_dialog.py`**：13 项真实 Qt 控件、独立入口、选择失效、确定项先执行、后台与取消测试。
- **新增 `tests/ui/operations/test_paratranz_reference_metadata.py`**：6 项普通下载的 metadata 往返及更换远端 ID 测试；修复前其中 4 项明确失败。
- **修改 `docs/requirements.md`**：仅更新本任务 FR3.4，明确 opt-in、持久化、整理、恢复及普通入口兼容。
- **新增 `docs/adr/047-opt-in-categorized-project-upload.md`、`plans/paratranz-categorized-upload/plan.md`**：记录已接受的范围、职责、验收与验证限制。
- **修改 `plans/INDEX.md`、`docs/changelogs/INDEX.md`**：仅更新当前工作线状态及本记录链接。

新生产模块均未超过 500 行；工程服务 473 行、对话框 421 行，未把新职责塞入已有大型同步模块。

## 验证证据

使用既有 `uv` 环境、Python 3.12.12 与锁定依赖。实际命令先设置：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.tmp-categorized-upload-20261023/uv-cache'
$env:QT_QPA_PLATFORM = 'offscreen'
```

最终新增功能专项（退出码 0，**148 passed**，8.83 秒）：

```powershell
uv run pytest tests/application/categorized_upload tests/application/projects/test_categorized_upload.py tests/paratranz/test_categorized_upload_gateway.py tests/paratranz/test_categorized_snapshot.py tests/contracts/paratranz/test_categorized_unbound_source_download.py tests/ui/operations/test_categorized_upload_bridge.py tests/ui/operations/test_categorized_upload_dialog.py tests/ui/operations/test_paratranz_reference_metadata.py -q --basetemp=.tmp-categorized-upload-20261023/pytest-feature-final --junitxml=.tmp-categorized-upload-20261023/feature-final.xml
```

工程与 ParaTranz 扩大回归（退出码 1，**577 passed、1 skipped、1 failed**，29.15 秒）：

```powershell
uv run pytest tests/application/categorized_upload tests/application/projects tests/contracts/paratranz tests/paratranz -q --basetemp=.tmp-categorized-upload-20261023/pytest-backend --junitxml=.tmp-categorized-upload-20261023/backend.xml
```

跳过项是未配置专用账号的真实术语 API 冒烟测试。唯一失败为 `test_source_update.py::test_reordered_source_projects_into_context_only_remote_plan`：预期两个 `context_order_changed`，当前得到 `upload_association_confirmed`。使用任务开始时的生产源码副本，通过以下命令复现同一失败，确认为已有同步断言差异，未扩大任务修改它：

```powershell
uv run pytest tests/application/projects/test_source_update.py::test_reordered_source_projects_into_context_only_remote_plan -q -o pythonpath=.tmp-categorized-upload-20261023/baseline/src --basetemp=.tmp-categorized-upload-20261023/pytest-baseline
```

最终 UI 与普通同步回归（退出码 0，**223 passed**，11.85 秒）：

```powershell
$uiTests = @(rg --files tests/ui/operations | Where-Object { $_ -match 'categorized|paratranz|sync|production_|operation_plan' } | Sort-Object)
uv run pytest @uiTests -q --basetemp=.tmp-categorized-upload-20261023/pytest-ui-final --junitxml=.tmp-categorized-upload-20261023/ui-final.xml
```

首次 UI 扩大回归发现 14 项失败，原因是新增下载适配器直接读取旧测试上下文不存在的 `active_variant_id`。已改为使用同步请求捕获的身份，并验证无活动工程不读分类绑定；原失败集合与新增隔离测试共 29 项复验通过，最终 223 项全通过。

静态检查均退出码 0：

```powershell
uv run ruff check src tests
uv run ruff format --check src tests
git diff --check
```

Ruff 检查通过，**1986 个 Python 文件**格式合格。Git 仅提示已有 LF/CRLF 转换，不含空白错误。

最初的混合大型测试进程曾在 UI 阶段直接退出，没有完整结果；沙箱内 Faiss 导入还曾出现 Windows `0xc0000008`。之后在获准的本地测试环境中分别完成后端及离屏 UI 回归，不将异常退出记为通过。已有本地 HTTP 客户端测试需要 127.0.0.1，沙箱阻止监听时在获准环境复验，16 项通过。未修改依赖或测试断言以绕开环境问题。

各组覆盖存在重叠，不将上述数量相加宣称总测试数。

## 兼容与验证限制

- 工程新增可选 `paratranz_categorized_uploads`，按端点和远端项目隔离；没有 schema 升级，旧工程无此字段时保持兼容。
- 成功关联提交会持久化当前工程及版本的工作状态，包括尚未手动保存的译文；失败或过期提交不覆盖其他活动版本。
- [ParaTranz 官方 OpenAPI](https://paratranz.cn/api-docs?format=json) 已于当日只读核对；目录、分页与 `file: {id}` 移动载荷按当前声明实现。**空 JSON 建文件、实际服务器移动行为及历史记录保留尚未用真实账号验收**，受控 HTTP 测试不等于线上证据。服务器不支持或返回不一致时显示明确失败/结果待核实，不降级为删除重建。
- API 未提供本实现可用的条件写入 CAS；已检查执行前快照及每项 PUT 前读取，并核验写后结果，但无法消除最后一次 GET 与 PUT 之间的服务端并发窗口。
- 未运行全仓 pytest、打包版人工操作或真实账号写入；本地 UI 使用真实 Qt 控件及后台线程。
- 保留上述 1 项已证实的基线测试失败。未提交、推送或发布；任务临时缓存和基线副本在收尾时清理。
