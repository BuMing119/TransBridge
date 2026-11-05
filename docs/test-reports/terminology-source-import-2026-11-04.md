# 外部来源完整导入译名方案：独立 QA

- 日期：2026-11-04
- 对应需求：FR5.18.25
- 对应计划：[Story 10](../../plans/terminology-localization-profiles/plan.md#story-10外部来源完整导入与整批译名冲突策略)
- 结论：本次功能验收通过，未发现待修复的功能问题。审查发现的四项问题均已修正并复验。
- 环境：Windows、仓库现有 Python 3.12.12 / uv 环境；使用 `--no-sync` 保留已有依赖文件和环境，不执行联机服务或 LLM。

## 审查与修复

1. **Major，预览漏计未变化映射**：底板 `A=甲`、来源 `A=甲/B=乙` 曾显示新增 1、覆盖 0、保留 0。现按最终保存结果统计，A 归保留，三类完整覆盖映射。
2. **Major，无底板时有效绑定被拒绝**：独立术语的可信出现位置绑定曾在纯投影完成后被 `ProfileEntryProjector` 的底板门禁撤销。现明确无版本且变化仅属于方案独立词时允许受控投影；缺少证据、底板不可用或损坏仍保留原译文。绑定本身有效时不额外要求原文包含相同字面术语。
3. **Major，保留策略未保留原始译文文本**：规范化等价的 `字母A/字母Ａ` 曾在保留策略下写入来源文本。现保留策略保持已有文本，采用策略保存来源文本；宽度、大小写、内部空白的实际变化计为覆盖，并以两种策略的参数化回归验证。
4. **Minor，来源异译被误计为完全重复**：来源独有同键的 `A/Ａ` 会按策略产生不同保存结果，但规范化比较曾将其计为重复且不计差异。修复将来源译文重复及差异统计改为原始文本比较，逻辑术语身份的规范化不变。

同时检查了 `scheme_owned` 默认关闭及严格布尔读取、旧孤立映射不复活、底板抑制保护、选择隔离、来源作用域与顺序摘要、无底板精确冻结恢复、SQLite/复制/管理器编辑往返、取消与上下文变化保护。修改未引入数据库 schema 迁移。

实际执行 HEAD 原始 `models.py` 于独立 Python 模块，与当前实现比较空内容和含插件映射、条目特例、出现位置绑定的内容摘要，两者均相同。非空样本摘要为 `f3d8593621876bfb527e29880c83050f173f64b8865f874d9f45c631fa034259`；此检查不是仅比较两个由新实现构造的对象。

## 验证结果

- 首次扩展集成运行：`360 passed, 2 failed`，共 362 项。两项均在已有报告发布使用 `os.link` 时被 sandbox 拒绝，非功能断言失败。
- 两项报告测试单独退出 sandbox 后复验：`2 passed, 6 deselected`。没有修改、跳过或 mock 掉硬链接逻辑。
- 窄窗口布局：`4 passed`。
- 最后一次原始文本保留与来源重复统计修复后，重新运行全部直接相关应用、真实 SQLite、来源读取和 UI 测试：`173 passed`，包含窄窗口检查与新增参数化用例。
- 最终 `ruff check src tests` 通过；`ruff format --check src tests` 通过（2367 files already formatted）；`git diff --check` 通过，仅有 Git CRLF 提示。
- pytest 输出 3 项既有 SWIG 类型弃用警告，无本次新增失败。

上述批次有覆盖重叠，不能将通过数相加，也不能把首次 362 项运行写成单次全绿。最后的 173 项运行发生在全部功能修复之后。

### 精确命令

首次扩展集成：

```powershell
uv --cache-dir D:/MyCode/TransBridge/.tmp-scheme-import-20261104-b39e/uv-cache run --no-sync pytest tests/application/terminology_profiles tests/persistence/terminology tests/ai_translator/test_term_source_reader.py tests/ai_translator/test_term_source_compatibility.py tests/ai_translator/test_project_terminology_adapter.py tests/ai_translator/test_project_terminology_runtime.py tests/application/translation/test_terminology_run_snapshot.py tests/application/translation/test_frozen_terminology_lookup.py tests/ui/tools/test_terminology_source_import_controller.py tests/ui/tools/test_terminology_source_import_dialog.py tests/ui/tools/test_terminology_profile_mapping_model.py tests/ui/tools/test_terminology_profile_manager.py tests/ui/tools/test_terminology_profile_edit_protection.py tests/ui/tools/test_profile_mapping_search.py tests/ui/tools/test_profile_manager_workflows.py tests/ui/tools/test_profile_archive_keyboard.py tests/ui/tools/terminology/test_schemes_view.py tests/ui/tools/terminology/test_source_browser.py tests/ui/tools/terminology/test_scheme_sources.py tests/ui/workbench/test_terminology_profile_switching.py tests/ui/workbench/test_profile_refresh_async.py tests/integration/bootstrap/test_terminology_browsing.py tests/integration/bootstrap/test_terminology_task_wiring.py tests/contracts/io/test_terminology_profile_write.py -q -p no:cacheprovider --basetemp D:/MyCode/TransBridge/.tmp-scheme-import-20261104-b39e/qa-pytest
```

报告发布复验（通过工具自动审查，使用 `require_escalated`）：

```powershell
uv --cache-dir D:/MyCode/TransBridge/.tmp-scheme-import-20261104-b39e/uv-cache run --no-sync pytest tests/integration/bootstrap/test_terminology_task_wiring.py -k production_runtime_closes -q -p no:cacheprovider --basetemp D:/MyCode/TransBridge/.tmp-scheme-import-20261104-b39e/qa-render-pytest
```

独立窄窗口检查：

```powershell
uv --cache-dir D:/MyCode/TransBridge/.tmp-scheme-import-20261104-b39e/uv-cache run --no-sync pytest tests/ui/tools/test_narrow_terminology_surfaces.py -q -p no:cacheprovider --basetemp D:/MyCode/TransBridge/.tmp-scheme-import-20261104-b39e/qa-narrow-pytest
```

全部功能修复后的最终复验：

```powershell
uv --cache-dir D:/MyCode/TransBridge/.tmp-scheme-import-20261104-b39e/uv-cache run --no-sync pytest tests/application/terminology_profiles tests/persistence/terminology/test_profiles.py tests/ai_translator/test_term_source_reader.py tests/ui/tools/test_terminology_source_import_controller.py tests/ui/tools/test_terminology_source_import_dialog.py tests/ui/tools/test_terminology_profile_mapping_model.py tests/ui/tools/test_terminology_profile_manager.py tests/ui/tools/test_terminology_profile_edit_protection.py tests/ui/tools/terminology/test_schemes_view.py tests/ui/tools/terminology/test_source_browser.py tests/ui/workbench/test_terminology_profile_switching.py tests/ui/tools/test_narrow_terminology_surfaces.py -q -p no:cacheprovider --basetemp D:/MyCode/TransBridge/.tmp-scheme-import-20261104-b39e/qa-final-2-pytest
uv --cache-dir D:/MyCode/TransBridge/.tmp-scheme-import-20261104-b39e/uv-cache run --no-sync ruff check src tests
uv --cache-dir D:/MyCode/TransBridge/.tmp-scheme-import-20261104-b39e/uv-cache run --no-sync ruff format --check src tests
git diff --check
```

### Sandbox 失败归因

运行时捕获报告任务原始错误为 `commit_mutation_failed: PermissionError [WinError 5]`，发生于 `application/terminology/renderers/_artifact.py` 的 `os.link(staging, target)`。该文件本轮未修改。在任务自有临时目录内，两个普通文本文件的最小 `os.link` 操作亦得到相同错误；工具允许退出 sandbox 后，两项原始测试通过，因此未更改报告生产代码。

## 验证边界

未运行完整仓库 pytest、真实 LLM、真实 ParaTranz 网络、用户游戏资产及人工桌面操作；本功能用已有本地 fixtures、offscreen Qt 和真实 SQLite 完成验证。无基准数据可证明新增大规模导入的端到端性能，沿用既有后台来源读取与虚拟列表模式。没有执行 commit/push。

本报告中的 `.tmp-scheme-import-20261104-b39e` 是本任务拥有的临时验证目录；结束时由主会话确认归属后统一清理，命令作为复现记录保留。
