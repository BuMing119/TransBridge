# 修复工程空白原文导致 ESP 写回定位失败

- 日期：2026-10-09
- Epic / Story：maintenance / write-save-feedback（非计划维护）
- 入口：用户补充写回窗口截图，明确显示 `RENDER_FAILED: SOURCE_LOCATOR_NOT_FOUND`。

## 证据与原因

工程来源准备默认使用 `skip_empty=False`，hydration 保留空字符串和纯空白原文。写回从不可变快照重建源定位时却默认使用 `skip_empty=True`，这些仍在请求中的条目被过滤，导致整个渲染失败。主会话和独立只读复核均用真实 NPC / QUST 二进制复现了截图中的同一错误。

新增回归通过真实 `TranslationIoProjectSourcePreparer` 获取 hydration，再执行预检、渲染、保真校验及原子输出。修复前 12 项中 8 项失败，均为默认或显式 `skip_empty=True` 的定位失败；显式 False 的 4 项成功。修复后 12 项全部成功。没有用户原始 ESP，未宣称已完成朋友现场文件复验。

## 逐文件变更

- 修改 `src/transbridge/application/io/plugin_write.py` 的 `_parser_from_snapshot`：固定完整读取源条目 `skip_empty=False`。读取/显示筛选不能缩减写回定位集合；保留原有冲突识别、原文匹配、来源身份及 string ID 检查。
- 新增 `tests/integration/publish/test_project_hydrated_write.py`：12 项真实建项至输出回归，覆盖 NPC 独立记录和 QUST 同定位日志，空串/空格/换行原文，默认/显式 True/False 选项，以及保留空白或明确填写译文。核对实际二进制文本与源文件不变。
- 修改 `docs/changelogs/INDEX.md`：仅新增本记录入口。

本增量仅修改既有写回定位职责；生产模块 390 行，无新增模块责任或公共接口。之前的保存、日志及写回反馈修复和工作区其他既有变更不计入本增量。

## 验证

```powershell
uv run --no-sync pytest tests/integration/publish/test_project_hydrated_write.py -q
```

修复后 **12 passed**。

```powershell
uv run --no-sync pytest tests/integration/publish tests/contracts/io tests/parser/test_original_match_plugin.py tests/writer/test_plugin_writer.py tests/persistence/v2/test_project_provisioning.py tests/application/projects/test_provisioning.py tests/ui/operations -q
```

**410 passed、1 skipped**，62.85 秒。跳过项为 Windows 符号链接检查，7 条警告为既有 SWIG / 兼容 API 弃用提示。

```powershell
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

静态检查、1579 个文件格式检查及差异空白检查通过。复用现有 uv 环境，未修改依赖或锁文件。

## 兼容与遗留项

代码修复无遗留项，无数据迁移要求，已有工程译文可继续使用。未运行全仓库测试、安装包构建或朋友原始 ESP 现场复验；安装包需重新构建才能包含该修复。未提交、推送或发布。测试临时插件由 pytest 临时目录管理，未将用户日志或插件数据写入仓库。
