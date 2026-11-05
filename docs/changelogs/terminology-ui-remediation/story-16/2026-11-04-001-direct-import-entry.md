# 本地词表直达与独立 ParaTranz 导入

- 日期：2026-11-04
- Epic / Story：terminology-ui-remediation / S16
- 需求：FR5.18.25；[计划](../../../../plans/terminology-ui-remediation/plan.md)
- 验证：[独立 QA 报告](../../../test-reports/terminology-import-entry-2026-11-04.md)

## 用户可见变化

按用户要求删除冗长的插件词库列表。“导入术语…”直接打开文件选择器，选定 JSON/CSV/Excel 后选择“保留已有译名”或“采用导入译名”，再预览并确认创建。ParaTranz 在同排使用独立次要按钮，只读取当前工程绑定的项目；未绑定或配置不可用时明确提示。

取消文件或策略选择不读取、不创建。读取和预览期间两个入口互斥，失败后恢复。配置或账号改变时刷新 ParaTranz 状态，策略确认时再次核对绑定，防止读取过期目标。来源标签显示文件名，完整路径保留在提示中。

## 逐文件增量

本轮开始时保存工作区差异和文件哈希，以区分之前两轮及其他未提交修改。

- 修改 `src/transbridge/ui/tools/terminology/schemes_controller.py`：分别接入文件选择和当前绑定 ParaTranz，选择策略后才读取，核验绑定及忙态。
- 修改 `src/transbridge/ui/tools/terminology/schemes_view.py`：可见 ParaTranz 次按钮、准确可用状态和两入口互斥。
- 修改 `src/transbridge/ui/tools/terminology_profiles/source_catalog.py`：删除插件、动态库和历史路径候选枚举，仅构造明确文件或绑定项目请求。
- 修改 `src/transbridge/ui/tools/terminology_profiles/source_import_controller.py`：忙态覆盖读取、预览和创建，结束或失败恢复。
- 修改 `src/transbridge/ui/tools/terminology_profiles/__init__.py`：移除旧来源选择器导出，导出策略框。
- 新增 `src/transbridge/ui/tools/terminology_profiles/import_policy_dialog.py`：短来源标签和两种整批策略，默认保留已有译名。
- 删除 `src/transbridge/ui/tools/terminology_profiles/source_picker_dialog.py`：移除旧来源列表界面，不保留不可达兼容层。
- 修改 `tests/ui/tools/terminology/test_scheme_sources.py`：显式文件、Excel 列配置、绑定项目和冻结读取配置。
- 修改 `tests/ui/tools/terminology/test_schemes_view.py`：文件直达、取消、ParaTranz 独立路由、配置恢复、忙态及绑定变化保护。
- 修改 `tests/ui/tools/terminology/test_workbench_source_import.py`：真实窗口两种策略经读取器、应用服务和仓库导入。
- 修改 `tests/ui/tools/test_terminology_source_import_dialog.py`：简短策略框、文件名与既有预览结果。
- 修改 `tests/ui/tools/test_terminology_source_import_controller.py`：完整导入流程忙态起止。
- 修改 `tests/ui/tools/test_dynamic_terminology_sources.py`：移除旧目录枚举断言，保留其他 AI 动态词库读取回归。
- 修改 `docs/requirements.md` 的 FR5.18.25、`docs/adr/039-terminology-localization-profiles.md` 的第 10 节：原位修正现行导入入口约定，不将自身动态词库作为外部来源。
- 修改 `plans/terminology-ui-remediation/plan.md`：追加 S16 范围、验收与结果，保留历史实施记录。
- 新增 `docs/test-reports/terminology-import-entry-2026-11-04.md`：独立验证命令、真实截图与边界。
- 修改 `plans/INDEX.md`、`docs/changelogs/INDEX.md`：仅更新本轮相关入口；新增本记录。

## 验证

按用户指定分工：Astra high 负责需求、计划、独立审核及 QA；GPT-6.1 Sol high 负责扫描和编码。

开发最终六个相关测试文件共 43 passed。独立 QA 两组 49 / 30 passed，最后文件名显示复验 5 passed、绑定保护复验 20 passed（包含新增 2 项），独立覆盖共 **81 个不同用例**，不重复累计。精确 pytest 命令见 QA 报告，均使用现有 uv 环境和 `--no-sync`，退出码均为 0；保留既有 SWIG 弃用警告。

全树检查通过：

```powershell
uv --cache-dir D:/MyCode/TransBridge/.tmp-term-import-entry-20261104-82ab/uv-cache run --no-sync ruff check --no-cache src tests
uv --cache-dir D:/MyCode/TransBridge/.tmp-term-import-entry-20261104-82ab/uv-cache run --no-sync ruff format --check --no-cache src tests
git -c core.safecrlf=false diff --check
```

格式检查覆盖 2371 文件。检查实际 Qt 工作台窄窗口、文件选择器、本地与 ParaTranz 策略框，最终三张截图保留在本次可视化目录，见 QA 报告。项目及来源来自测试夹具，ParaTranz 未访问远端；文件选择器截图为 Qt 非原生模式，不作为 Windows 原生对话框手工验证。

## 兼容与限制

保留全部有效词导入、来源独有词及两种批量策略，不修改翻译使用的共享动态库、其他 AI 遗留读取、数据库或依赖，无迁移要求。旧插件来源列表不再提供；仍可显式选择术语文件。方案管理器原本没有外部导入入口，本次未扩展。

未跑全仓 pytest、上轮 431 项全集、真实网络或 LLM、原生对话框手工测试。无待修复问题。测试临时数据经归属核验后清理，交付截图保留；未提交或推送。
