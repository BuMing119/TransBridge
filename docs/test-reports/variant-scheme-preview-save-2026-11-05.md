# 译名方案实际关联、预览与独立保存 QA

- 日期：2026-11-05
- 范围：FR5.18、ADR-039、terminology-localization-profiles Story 11～13
- 状态：本地实现与相关自动化 QA 完成，未发现尚未解决的本轮问题
- 工作区：以本轮开始时 `.tmp-variant-schemes-pilot-20261105/manifest.json` 和完整 baseline 对比归属；原有未提交变更保留，不使用 HEAD 代表本轮起点。

## 检查结论与修正

1. 实际关联固定 profile/revision；预览不持久化、发布不推进实际 revision、归档保留历史读取。旧测试中“切换即保存”“归档清选择”“发布自动采用新版”的断言已按新明确需求重写，并保留对具体 revision/身份的检查。
2. 发现无关联旧正文仍使用目标方案创建时 `base_translation`，会导致当前基础术语译名无法转换。已交编码修正为 READY 基础快照与目标方案按共同 original/scope 转换；补充过期历史译名、部分交集、目标独有词与插件作用域遮蔽测试。
3. 发现管理器重命名与说明仍暗示发布/预览立即改变实际版本。已交编码修正文案，并验证发布仅发出 preview 请求、实际关联保持旧 revision。
4. 新增重复全替换、原文门控、一次非级联替换、无变化与空译文、同 profile 不同 revision、实际方案A/B与导出一致性测试。300 条、两个插件来源的工作台在筛选前计算完整快照，筛选后仍显示 B；回 A 直接显示原正文并可编辑。
5. 新增 UI 保存默认另存、已有 B 明确新建/覆盖/取消、先保存脏 A、捕获原 revision 不被对话期间变化重新基准化的检查；SQLite migration 校验旧预览选择备份、一次清除、新关联重开保留及失败事务回滚。

6. 保存独立 QA 发现真实 `Names.esp` 来源的 scope 在 materialization 时丢失，使插件映射错误回落到 project。已在来源 materialization/reopen 的 canonical 边界保留 parser 的明确插件身份 metadata；真实 `Names.esp` 命中和 `Other.esp` 隔离两项回归通过。
7. AI 有效映射语义扩大后不能重用旧 v1 ID/digest。新任务版本升级 v2，旧 v1 恢复保留原术语集合与摘要；真实生产 gate 支持已保存方案的无基础版本 B。

## 已执行验证

在仓库已有 `.venv` 中运行，未安装新依赖、未修改既有锁文件。各批次独立，重叠结果不累加。

```powershell
.venv/Scripts/python.exe -m pytest tests/application/terminology_profiles tests/persistence/terminology tests/contracts/io/test_terminology_profile_write.py -q --basetemp=.tmp-variant-schemes-pilot-20261105/qa-core-v2-final
```

结果：**245 passed**，包含最新基础来源交集修复、迁移及旧 v1/new v2 AI 快照兼容测试。

UI 最终命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/ui/workbench tests/ui/tools/test_terminology_profile_manager.py tests/ui/tools/test_terminology_profile_edit_protection.py tests/ui/tools/test_profile_manager_workflows.py tests/ui/tools/test_profile_mapping_search.py tests/ui/tools/test_profile_archive_keyboard.py tests/ui/tools/terminology/test_schemes_view.py -q --basetemp=.tmp-variant-schemes-pilot-20261105/qa-ui-final2
```

结果：**113 passed**。

AI 生产接线验证：

```powershell
.venv/Scripts/python.exe -m pytest tests/ai_translator/test_project_terminology_runtime.py tests/ai_translator/test_project_terminology_adapter.py tests/ui/tools/test_ai_project_terminology.py -q --basetemp=.tmp-variant-schemes-pilot-20261105/qa-ai-final2
```

结果：**32 passed**。新增真实 SQLite + `ProductionTerminologyComposition.effective_adapter` → freeze → adapter load 检查，实际 B 无基础术语版本仍可使用 B 译名，不发网络请求。

保存/恢复独立 QA 命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/application/projects/test_profile_variant_commands.py tests/persistence/v2/test_profile_selection_activation.py tests/persistence/v2/test_project_activation_transactions.py tests/persistence/v2/test_project_open_activation.py tests/persistence/v2/test_saved_project_sources.py tests/persistence/v2/test_source_artifact_archive.py tests/application/projects/test_saved_source_consumers.py tests/application/projects/test_known_original_consumers.py tests/application/projects/test_durable_variant.py -q -p no:cacheprovider --basetemp .tmp-variant-schemes-pilot-20261105/qa-save-final
```

结果：**134 passed, 2 failed**。两项失败为系统 `TemporaryDirectory` 路径 WinError 5；使用本任务拥有的 TMP/TEMP 后原 archive 测试完整复验：

```powershell
$env:TMP = (Resolve-Path .tmp-variant-schemes-pilot-20261105/qa-save-runtime-temp).Path
$env:TEMP = $env:TMP
.venv/Scripts/python.exe -m pytest tests/persistence/v2/test_source_artifact_archive.py -q -p no:cacheprovider --basetemp .tmp-variant-schemes-pilot-20261105/qa-save-7
```

结果：**6 passed**。39 项新保存/恢复测试全部通过；扩大批次全部唯一用例有通过证据，但不写成单次 136 passed。覆盖新建/覆盖失败、prepared/committed 两次重开、SQLite/正文/pointer 各阶段故障、外部较新关联保护、独立编辑重开、copy/delete 关联和真实插件 scope。

额外兼容检查从本轮 baseline 的旧 effective 实现取得两个 v1 摘要固定夹具（有底板/无底板）；恢复不追加旧非 owned 映射，保留原 ID/摘要，新 v2 完整映射与摘要域独立。

测试文件 Ruff check/format 已通过。主会话执行 `uv run --offline ruff check src tests` 与 `uv run --offline ruff format --check src tests`，使用任务目录 `UV_CACHE_DIR`；在 v2、scope 与新增 AI 测试完成后最终重验通过，格式检查为 2381 文件。`git diff --check` 已通过，只出现既有行尾提示。

## 过程失败与归因

首轮 109 项中 24 项、后续扩展中的发布/归档/预览等失败对应本轮明确改变的旧合同；逐项核实实现与新需求后更新测试，而非跳过或 mock 掉业务检查。新测试自身将 `filtered_entries` 当属性、以及旧“不存在译名必须诊断”的预期已修正。

一次混合核心/UI 批次在导入 `faiss.swigfaiss_avx2` 时出现 Windows native `0xc0000008`，未产生完整 pytest 结果；不把该次记为通过，也未声称在 baseline 复现。随后分别运行核心/UI进程；以完整结束的最终批次作为证据。未修改依赖或 FAISS 行为。

## 验证边界

Qt 测试为 offscreen 自动化。未运行真实用户游戏资产、真实 LLM/ParaTranz 网络、打包安装程序或人工桌面端到端；导入和上传不在本轮行为范围。未运行整个仓库 pytest，已覆盖本轮相邻工作台、术语持久化、导出与保存合同。清理本轮临时目录由主会话在汇总后执行，不删除预先存在的数据或其他未提交改动。
