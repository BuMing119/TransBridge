# 分类上传的任务子目录

- 日期：2026-10-25
- Epic / Story：`paratranz-categorized-upload` / S08
- 计划：[plan](../../../../plans/paratranz-categorized-upload/plan.md)
- 需求：[FR3.4](../../../requirements.md)
- 决策：[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)
- 使用说明：[分类上传](../../../dev/paratranz-categorized-upload.md)
- 范围：新增上传路径层级；用户明确本轮不考虑已有云端

## 用户可见变化

大量 Quest 对话文件此前与物品、人名等分类直接并列在插件目录。现在 Quest 对话、未关联对话和合并的任务日志统一进入 `插件/任务/`，例如 `BSHeartland.esm/任务/对话_[000A8A86].json` 和 `BSHeartland.esm/任务/任务日志.json`。其他分类仍在插件目录。

单独和批量分类共用同一规则。对话仍按 Quest 分文件，QUST 名称、目标和日志仍汇总为一个文件；原分类 ID、key、原文、译文及上下文投射规则保持不变。嵌套目录沿用现有 multipart `path`，不创建目录实体。本轮不新增旧布局迁移，也不改普通上传或本地分类导出。

## 逐文件变化

本轮开始时分类功能及其他工作线均已有未提交修改。以下归属以本轮启动副本为基线，不把既有未跟踪文件全部算作新增。

- **修改 `src/transbridge/application/categorized_upload/classification.py`**：`_category` 为 INFO/DIAL 对话及任务日志文件增加 `任务/` 路径，保留原分类标识；现有冲突拆分继续使用同一目录。
- **修改 `tests/application/categorized_upload/test_planning.py`**：更新已有对话路径断言；覆盖各 Quest 分组、未关联对话、QUST 三类内容合并、分类选择、路径特殊字符和同 key 不同原文拆分的保真与稳定性。
- **修改 `tests/paratranz/test_categorized_file_import.py`**：单来源/多来源参数化用例解析真实 multipart，核对 `插件/任务/` 目录及文件名；验证云端模拟回读路径、再次对照不需建文件、无内容变化及各来源词条身份隔离。
- **修改 `docs/requirements.md`**：在 FR3.4 明确任务子目录、保留分组和本轮不含旧云端迁移。
- **修改 `docs/adr/047-opt-in-categorized-project-upload.md`**：补齐路径层级决策及稳定分类身份约束，记录用户给定的范围。
- **修改 `docs/dev/paratranz-categorized-upload.md`**：更新目录示例与 multipart 路径说明，链接本次验证。
- **修改 `plans/paratranz-categorized-upload/plan.md`**：新增并完成 S08，写明文件落点、范围和验证结果，保留 S01～S07 历史证据。
- **修改 `docs/INDEX.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`**：仅更新本功能状态及记录入口。
- **新增本记录**：保存任务目录增量；既有 changelog 文件不改写。

## 验证

使用仓库既有 uv 环境与锁文件，临时目录为本轮独有：

```powershell
$env:UV_CACHE_DIR = 'D:\MyCode\TransBridge\.tmp-categorized-quest-directory\uv-cache'
$env:TMP = 'D:\MyCode\TransBridge\.tmp-categorized-quest-directory'
$env:TEMP = $env:TMP
$env:PYTHONIOENCODING = 'utf-8'
$env:QT_QPA_PLATFORM = 'offscreen'
```

先修改路径验收用例，再运行聚焦测试，旧实现 **6 failed、27 passed**；实现后最终聚焦 **33 passed**（0.50 秒，退出码 0）：

```powershell
uv run pytest tests/application/categorized_upload/test_planning.py tests/paratranz/test_categorized_file_import.py -q --tb=short --show-capture=no
```

最终相关回归 **295 passed、3 warnings**（8.27 秒，退出码 0）：

```powershell
uv run pytest tests/application/categorized_upload tests/application/projects/test_categorized_upload.py tests/paratranz/test_categorized_upload_gateway.py tests/paratranz/test_categorized_file_import.py tests/paratranz/test_categorized_snapshot.py tests/contracts/paratranz/test_categorized_unbound_source_download.py tests/ui/operations/test_categorized_upload_bridge.py tests/ui/operations/test_categorized_upload_dialog.py tests/ui/operations/test_upload_mode_routing.py tests/ui/operations/test_paratranz_reference_metadata.py tests/ui/test_logging_setup.py tests/contracts/paratranz/test_sync_execution_logging.py tests/contracts/paratranz/test_sync_diagnostics_logging.py tests/ui/operations/test_paratranz_upload_roundtrip.py tests/contracts/paratranz/test_upload_resolution.py tests/paratranz/test_key_roundtrip.py -q --tb=short --show-capture=no
```

3 条警告均为既有 SWIG 类型缺少 `__module__` 的 DeprecationWarning。聚焦集合包含在最终集合内，不相加；本轮新增 5 个参数化展开后的用例。

全库静态检查均通过（退出码 0），2134 个 Python 文件格式合格：

```powershell
uv run ruff check src tests
uv run ruff format --check src tests
```

按启动副本审查本轮 11 个文件的增量，生产分类模块为 101 行。新增/修改行中的 16 个本地文档链接、8 个代码路径及 S08 编号唯一性检查通过；对本次范围执行 `git -c core.safecrlf=false diff --check -- <本次文件路径>` 退出码 0，未跟踪文件另做行尾空白检查。

## 兼容与边界

- 变更仅调整新建分类文件的路径，没有 schema 或依赖变更，分类选择和词条身份保持原样。
- 按用户要求不实现已有云端布局迁移；未进行真实云端写入。测试使用离线 session 和受控端口。
- 未运行全仓 pytest、人工桌面上传或发行构建；当前验证覆盖本次路径调整及相关上传/UI 行为。
- 重启应用后重新打开分类预览即可使用新路径。
- 未提交、推送或发布；保留其他工作线修改，任务临时基线和测试目录在复核后清理。
