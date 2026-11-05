# 术语文件直达与 ParaTranz 独立入口 QA

- 日期：2026-11-04；对应 FR5.18.25、[S16](../../plans/terminology-ui-remediation/plan.md)。
- 结论：验收通过，未发现待修复问题。独立验证覆盖 **81 个不同用例**；没有将重叠复验累加。
- 环境：Windows、Python 3.12.12、现有 uv 环境；`--no-sync` 保留已有依赖配置。

## 范围与审查

工作台本地导入现在先打开文件选择器，再选择两种整批策略，进入既有后台读取、预览和确认创建。ParaTranz 使用独立可见次要按钮；仅读取当前工程绑定，未绑定、不可用和读取失败有明确反馈。删除插件/共享库/旧插件库和历史本地配置的候选列表；其他 AI 动态库读取能力保留。方案管理器原本没有该外部入口，本轮未新增。

审查补正了配置或账号修正后禁用按钮不恢复的问题：除绑定变化外，监听 `config_changed`、`user_changed`，仅在本地重新解析状态。策略窗口确认时重新核对 ParaTranz 绑定，变化或失效拒绝读取旧项目。像素检查发现完整路径挤掉文件名，最终改为来源类型和文件名，完整路径保留在 tooltip/无障碍描述。两入口在读取及预览期间保持互斥，失败后恢复；文件/策略/预览取消和上下文变化不创建方案。

核对本轮 `baseline-hashes.json`，生产修改仅以下 7 个文件（含新增/删除）：

- `src/transbridge/ui/tools/terminology/`：`schemes_controller.py`、`schemes_view.py`。
- `src/transbridge/ui/tools/terminology_profiles/`：`__init__.py`、`source_catalog.py`、`source_import_controller.py`；新增 `import_policy_dialog.py`，删除 `source_picker_dialog.py`。

测试修改仅以下 6 个文件：

- `tests/ui/tools/terminology/`：`test_scheme_sources.py`、`test_schemes_view.py`、`test_workbench_source_import.py`。
- `tests/ui/tools/`：`test_dynamic_terminology_sources.py`、`test_terminology_source_import_controller.py`、`test_terminology_source_import_dialog.py`。

真实窗口回归从 header 按钮进入，保留窗口上下文包装器、真实 JSON reader、应用服务与仓库，两种策略均验证创建结果；仅自动接受文件/策略/预览对话框，后台任务以确定性方式运行。ParaTranz 目标与失败测试使用夹具，不访问远端；代码路径只调用 `snapshot_terms`，没有远端写接口。

## 验证结果与精确命令

首次相关集合 **49 passed**，独立布局/上下文集合 **30 passed**。文件名显示微调后复验 **5 passed**；绑定确认保护补充后方案入口文件 **20 passed**（原 18 项加 2 项）。因此不同用例为 49 + 30 + 2 = 81，后两次复验不是额外 25 项。所有命令退出码 0，pytest 仅有 3 项既有 SWIG 弃用警告。

```powershell
uv --cache-dir D:/MyCode/TransBridge/.tmp-term-import-entry-20261104-82ab/uv-cache run --no-sync pytest tests/ui/tools/terminology/test_scheme_sources.py tests/ui/tools/terminology/test_schemes_view.py tests/ui/tools/terminology/test_workbench_source_import.py tests/ui/tools/test_terminology_source_import_dialog.py tests/ui/tools/test_terminology_source_import_controller.py tests/ui/tools/test_dynamic_terminology_sources.py tests/ai_translator/test_term_source_reader.py -q -p no:cacheprovider --basetemp D:/MyCode/TransBridge/.tmp-term-import-entry-20261104-82ab/qa-entry-pytest
uv --cache-dir D:/MyCode/TransBridge/.tmp-term-import-entry-20261104-82ab/uv-cache run --no-sync pytest tests/ui/tools/terminology/test_workbench_simplification.py tests/ui/tools/terminology/test_context_isolation.py tests/ui/tools/terminology/test_package_imports.py tests/ui/tools/test_narrow_terminology_surfaces.py tests/ui/workbench/test_terminology_profile_switching.py -q -p no:cacheprovider --basetemp D:/MyCode/TransBridge/.tmp-term-import-entry-20261104-82ab/qa-layout-context-pytest
uv --cache-dir D:/MyCode/TransBridge/.tmp-term-import-entry-20261104-82ab/uv-cache run --no-sync pytest tests/ui/tools/test_terminology_source_import_dialog.py tests/ui/tools/terminology/test_workbench_source_import.py -q -p no:cacheprovider --basetemp D:/MyCode/TransBridge/.tmp-term-import-entry-20261104-82ab/qa-policy-final-pytest
uv --cache-dir D:/MyCode/TransBridge/.tmp-term-import-entry-20261104-82ab/uv-cache run --no-sync pytest tests/ui/tools/terminology/test_schemes_view.py -q -p no:cacheprovider --basetemp D:/MyCode/TransBridge/.tmp-term-import-entry-20261104-82ab/qa-binding-final-pytest
uv --cache-dir D:/MyCode/TransBridge/.tmp-term-import-entry-20261104-82ab/uv-cache run --no-sync ruff check --no-cache src tests
uv --cache-dir D:/MyCode/TransBridge/.tmp-term-import-entry-20261104-82ab/uv-cache run --no-sync ruff format --check --no-cache src tests
git -c core.safecrlf=false diff --check
```

最终全树 Ruff 通过，格式检查 **2371 files already formatted**，diff 检查通过。初次有缓存写入权限提示，最终使用 `--no-cache` 复验通过，没有放宽检查。

## 实际画面与限制

检查了真实窗口 920×620 的最紧头部状态（已选择方案、可用 ParaTranz、编辑按钮同时存在）、实际 Qt 文件选择器、最终本地策略框和 ParaTranz 策略框。无插件候选列表；两个导入入口、文件名、目标项目、两策略与继续/取消按钮可辨认且未截断。布局回归另覆盖 920/1180 宽度。

渲染使用真实 `TerminologyWindow`、方案控制器及上下文包装器，项目名、方案和绑定来自内存测试夹具；截图中的列表未加载不用于证明浏览结果。文件选择器在 offscreen 环境使用 Qt 支持的 `DontUseNativeDialog`，不是 Windows 原生对话框截图；策略框通过真实按钮路线打开并取消，未读取远端或创建新方案。最终三张交付图：

- [工作台入口](C:/Users/admin/.codex/visualizations/2026/11/04/01a1a5fc-6441-7d83-ba9c-43ba54cfab06/import-entry-header-920-20261104-82ab.png)
- [本地文件策略](C:/Users/admin/.codex/visualizations/2026/11/04/01a1a5fc-6441-7d83-ba9c-43ba54cfab06/import-entry-file-policy-20261104-82ab.png)
- [ParaTranz 策略](C:/Users/admin/.codex/visualizations/2026/11/04/01a1a5fc-6441-7d83-ba9c-43ba54cfab06/import-entry-paratranz-policy-20261104-82ab.png)

未重跑上轮 431 项全集或全仓 pytest；未进行真实网络、LLM、原生文件对话框手工测试。无依赖、数据库格式或自动发布语义变化，未提交或推送。临时 QA 目录由主会话核验清理，三张交付图保留。
