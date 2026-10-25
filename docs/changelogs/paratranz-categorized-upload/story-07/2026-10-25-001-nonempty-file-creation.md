# 分类文件以真实新增内容创建

- 日期：2026-10-25
- Epic / Story：`paratranz-categorized-upload` / S07
- 计划：[plan](../../../../plans/paratranz-categorized-upload/plan.md)
- 需求：[FR3.4.2](../../../requirements.md)
- 决策：[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)
- 使用与排查：[分类上传说明](../../../dev/paratranz-categorized-upload.md)
- 类型：分类建文件契约修复、离线回归与文档同步

## 故障与修复

用户新日志 `data/log/20261025_002306.log` 显示：本次选择两个来源、280 个分类文件、20162 条词条，云端快照为空。创建请求提交空 JSON 数组，服务端返回 HTTP 200、`{"status":"empty"}`；无法获得文件 ID，后续逐条新增未开始，最终全部结果待核实。日志 13699～13700 行连接首个响应与创建异常，15376 行为执行汇总。故障在文件内容契约，虚拟目录无需独立创建。

现在分类文件携带本分类全部确定新增的真实词条，一次导入并回读其远端身份。核对路径、完整 key 集合、唯一 ID、文件归属及原文/上下文后保存关联，不再重复逐条创建。服务端调整译文或状态时，再按已确认 ID 补齐并核验。已有分类文件中的新增词条沿用逐条创建。

明确的 `status=empty` 拒绝会标记相关新增项失败并停止后续文件写入。响应丢失或回读不符保留结果待核实，重新对照后依靠实际文件和词条身份恢复；无自动重试。导入后的取消或本地保存失败保留已写事实，同一文件中尚未开始移动的旧词条单独保持待处理。

整理保留原 ID：已有目标文件或可用真实新增项创建目标时继续按 ID 移动；目标不存在且只有旧词条时，对照即暂缓，等待目标文件可用。不通过空数组、占位词条或重复旧词条创建文件。

## 逐文件变化

工作区已有分类功能、诊断、UI 模式路由及其他未提交变更。以下按本次启动副本与当前内容的差异归属；Git 中未跟踪的既有文件不算作本轮整文件新增。

- **修改 `src/transbridge/application/categorized_upload/models.py`**：`CategorizedUploadPort.create_file` 必传真实新增条目，返回包含已核验身份的文件；新增 `FileCreationRejected` 区分明确未接受内容与未知写入结果。
- **新增 `src/transbridge/application/categorized_upload/file_creation.py`**：验证非空输入、唯一 key，以及创建结果的路径、完整集合、唯一正整数 ID、文件归属和源内容，供执行器与 HTTP 适配器共用。
- **修改 `src/transbridge/application/categorized_upload/planner.py`**：`_require_creation_content` 在最终计划中暂缓目标不存在且无新增项的纯移动内容，避免执行阶段发送空文件。
- **修改 `src/transbridge/application/categorized_upload/executor.py`**：新文件仅提交 `create` 项，复用导入身份并拒绝与已知远端身份冲突；保存关联后按需补齐译文/状态。处理已导入后的取消、保存失败、明确拒绝与未知结果，区分新增内容和未移动旧条目。
- **修改 `src/transbridge/paratranz/categorized_upload.py`**：`create_file` 序列化真实 JSON 到 multipart，日志记录真实提交数，支持已有文件响应形态并回读导入条目；`_wire_entry` 统一文件导入与逐条新增字段。保持单次尝试、虚拟目录 path 和整项目文件列表语义。
- **修改 `tests/application/categorized_upload/test_execution.py`**：内存端口模拟真实导入；验证单次建文件、逐项回执、取消与已有远端 ID 冲突；原逐条新增异常改用已存在目标文件覆盖。
- **修改 `tests/application/categorized_upload/test_planning.py`**：已有目标文件场景继续验证绑定优先级和原 ID 迁移，避免依赖空文件创建假设。
- **修改 `tests/application/categorized_upload/test_diagnostics.py`**：按单次导入调整写入统计，保留逐条新增/移动失败的明确场景及诊断断言。
- **修改 `tests/paratranz/test_categorized_upload_gateway.py`**：核对完整 UTF-8 JSON 字段、目录参数、响应形态、回读集合/身份/源内容及无效输入；覆盖 `empty` 停止后续写入和未知响应保留结果待核实，沿用正文/凭据排除检查。
- **新增 `tests/paratranz/test_categorized_file_import.py`**：离线 session 解析实际 multipart 内容并维护文件/词条状态，空数组返回观察到的 `status=empty`；覆盖多来源导入、响应丢失、回读超时、状态归一化、混合整理、纯移动暂缓、两类保存失败及导入后取消，验证再次对照不重复创建。
- **修改 `tests/ui/operations/test_categorized_upload_bridge.py`**：工程会话端口随建文件导入真实条目；文件响应丢失仅保存已核实关联，重新对照恢复；纯移动缺目标先待处理，目标就绪后保留原 ID。
- **修改 `docs/requirements.md`**：新增 FR3.4.2，定义真实内容创建、回读身份、取消/恢复和纯移动限制。
- **修改 `docs/adr/047-opt-in-categorized-project-upload.md`**：补齐虚拟目录、真实导入与身份契约，替换尚未定位故障的当前状态，记录缺少整理目标时的处理和兼容影响。
- **修改 `plans/paratranz-categorized-upload/plan.md`**：增加并完成 S07，更新文件落点、验收和当前验证；保留 S06 与初版历史证据。
- **修改 `docs/dev/paratranz-categorized-upload.md`**：更新创建流程、取消/恢复与整理限制，替换空数组仍在使用的旧说明，给出实际故障原因和新日志判断方法。
- **修改 `docs/INDEX.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`**：仅更新本功能当前状态和新记录入口。
- **新增本记录**：归档当前修复，不改写 S01～S06 的历史增量文件。

## 验证证据

使用仓库既有 uv 环境与锁文件，临时设置：

```powershell
$env:UV_CACHE_DIR = 'D:\MyCode\TransBridge\.tmp-categorized-create-fix\uv-cache'
$env:TMP = 'D:\MyCode\TransBridge\.tmp-categorized-create-fix'
$env:TEMP = $env:TMP
$env:PYTHONIOENCODING = 'utf-8'
$env:QT_QPA_PLATFORM = 'offscreen'
```

先新增解析真实 multipart 的回归，再运行：

```powershell
uv run pytest tests/paratranz/test_categorized_file_import.py -q
```

修复前 **7 failed**（0.68 秒），实际空数组触发服务器模拟拒绝；实现修复后 **7 passed**（0.37 秒）。后续继续补充取消、混合写入及恢复边界，最终集合包含这些新增场景；阶段数量有重叠，不相加。

最终相关回归（退出码 0，**290 passed**，6.94 秒）：

```powershell
uv run pytest tests/application/categorized_upload tests/application/projects/test_categorized_upload.py tests/paratranz/test_categorized_upload_gateway.py tests/paratranz/test_categorized_file_import.py tests/paratranz/test_categorized_snapshot.py tests/contracts/paratranz/test_categorized_unbound_source_download.py tests/ui/operations/test_categorized_upload_bridge.py tests/ui/operations/test_categorized_upload_dialog.py tests/ui/operations/test_upload_mode_routing.py tests/ui/operations/test_paratranz_reference_metadata.py tests/ui/test_logging_setup.py tests/contracts/paratranz/test_sync_execution_logging.py tests/contracts/paratranz/test_sync_diagnostics_logging.py tests/ui/operations/test_paratranz_upload_roundtrip.py tests/contracts/paratranz/test_upload_resolution.py tests/paratranz/test_key_roundtrip.py -q --tb=short --show-capture=no
```

出现 3 条既有 SWIG 类型 `__module__` 缺失的 DeprecationWarning。测试覆盖分类核心、工程保存、gateway、UI 会话/模式、下载隔离、普通上传往返及诊断；290 项不是全部新增。

最终全库静态检查（退出码 0）：

```powershell
uv run ruff check src tests
uv run ruff format --check src tests
```

Ruff 检查通过，**2134 个 Python 文件**格式合格。此次修改的生产模块均小于 500 行；输入/回读校验独立为单一职责模块，没有继续扩张其他同步或 UI 模块。

对照启动副本复核本次 19 个文件的增量；检查 19 个新增/修改行的文档链接、15 个代码路径和 S07 编号唯一性，全部通过。对上述范围执行 `git -c core.safecrlf=false diff --check -- <本次文件路径>`，退出码 0；未跟踪文件另行检查行尾空白与链接，避免只依赖 Git 漏查。

## 兼容与遗留

- 不改变工程 schema、原 key、普通上传策略或单独/批量模式入口；内部分类 `create_file` 端口现在要求真实新增条目，并返回其经核验身份。
- 重启应用加载修复，重新对照生成新计划后再确认上传；不能复用旧执行结果盲目重发。
- 纯移动且目标文件不存在的分类会显示待处理，目标可用后才能整理；不删除重建原词条。
- 未进行修复后的真实云端上传、移动及历史保留验收；本轮仅本地受控 HTTP/端口与 UI 测试，没有改动用户云端项目。
- 未运行全库 pytest、发行构建或人工桌面上传；历史其他工作线的已知失败不归入本次修复。
- 未提交、推送或发布，保留工作区既有修改。任务临时基线和测试目录在差异复核后清理。
