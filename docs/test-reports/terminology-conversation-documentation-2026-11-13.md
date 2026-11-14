# 本对话术语文档补齐与证据核验

日期：2026-11-13。范围为本对话术语工作流；本轮只修改文档，不新增业务功能，不归入其他未提交工作。

## 阶段与覆盖

阶段计划：仓库/对话证据核对完成；需求、ADR、计划、使用说明补齐完成；聚焦回归与文档检查完成；缺失增量补记完成。

- 抽取、缓存身份、LLM 真假冲突与整组 50 路并发：已有 [S15 记录目录](../changelogs/project-terminology-build-versioning-reporting/story-15)；保留历史修复记录，需求明确缓存复用后仍判断，不改成多义术语系统。
- 状态/列表性能与自动翻译统一退出：已有 [S16 记录目录](../changelogs/project-terminology-build-versioning-reporting/story-16)；纠正需求/ADR 的旧三秒终态和不再调用模型承诺，不把既有合成基准当作真实用户项目数据。
- 冲突主从界面：已有 [S17 原记录](../changelogs/project-terminology-build-versioning-reporting/story-17/2026-11-13-001-in-place-conflict-review.md)；补记后续“排除此术语”直接保存前进与清理项目提取缓存。
- 现有方案导入和可编辑新名称：补充 FR5.18.25、ADR-039、方案 S15 和增量；以 existing_import 与真实窗口绑定测试核验。
- 多方案目录导出：补充 FR5.18.26、ADR-039、方案 S16 和增量；复用原导出工作流，不写成新导出引擎。
- 方案差异与 Excel：已有 [S14 记录](../changelogs/terminology-localization-profiles/story-14-scheme-comparison/2026-11-13-001-scheme-comparison.md)，本轮补齐需求与 ADR，并从索引链接。
- [完整用户流程](../terminology-workflow.md)：术语/方案/翻译版本区分、提取范围、冲突排除、缓存、命名导入、多版本输出和三类文件导出。

## 本轮实际运行

以下命令 37 项通过（2.62 秒，3 条既有 SWIG DeprecationWarning）：

```powershell
uv run --no-sync --cache-dir .tmp-terminology-docs/uv pytest tests/integration/bootstrap/test_terminology_cache_cleanup.py tests/ui/tools/terminology/test_extraction_cache_actions.py tests/ui/tools/terminology/test_conflict_review_pane.py tests/ui/tools/terminology/test_conflict_review_controller.py tests/application/terminology_profiles/test_existing_import.py tests/ui/tools/terminology/test_workbench_source_import.py tests/ui/operations/test_write_schemes.py tests/ui/operations/test_scheme_comparison.py -q --tb=short --basetemp .tmp-terminology-docs/tests
```

缓存隔离 4 项，冲突控件/协调器 13 项，已有方案合并/真实窗口导入 7 项，多方案导出 8 项，差异/工作簿/取消 5 项。真实插件测试只使用临时 fixture，因 Windows 沙箱临时快照限制使用已批准的测试执行权限。

此前同对话的 125 项方案相关回归、90 项导出回归和 68 项差异/工作簿回归属于各轮历史证据，不与本轮 37 项相加宣称独立覆盖总数。未重跑整个仓库、未使用真实模型、未打包、未测真实大项目总体耗时或整体退出；Qt offscreen 交互不是用户桌面视觉验收。

## 文档验证

新增文件及修改行中的 65 个本地链接目标均存在；FR5.16 的 45 个、FR5.18 的 27 个定义编号连续且唯一，索引与 plan 状态一致。`git -c core.safecrlf=false diff --check` 通过。本轮仅改文档，未重复运行 Python Ruff 检查。历史 changelog 保持不修改。临时核验脚本及 `.tmp-terminology-docs` 在完成后删除；不清理用户缓存或导出文件。
