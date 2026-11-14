# S17：启动询问一次与自动术语定位

- 日期：2026-11-13
- 关联：FR5.18.17 / FR5.18.22、ADR-039、terminology-localization-profiles Story 17
- 范围：承接首版计数分流与冻结判断；按用户追加要求取消日常使用中的手动启动步骤。

## 行为

主窗口显示后，每次应用运行至多询问一次是否启用 AI 术语位置判断。多个窗口共享本次选择；勾选“永不再问”后，使用 QSettings 持久记住启用或不启用，后续启动直接采用。未勾选只影响本次运行；关闭弹窗不构成允许，也不保存勾选状态。弹窗说明疑点原文/译文会交给当前 AI 服务，可能产生服务费用。

“设置 → AI 术语位置判断…”可更改选择，也可取消“永不再问”恢复下次启动询问；该入口无需打开项目。

允许后，方案预览完成本地规则计算，自动在后台检查全部来源的未解决条目。稳定选择延迟 300ms 调度；快速切换只处理最后选择，相同上下文及方案已尝试过则不自动重跑。完整请求对应的确定与不确定结果均可供自动路径复用；不确定结果只有显式重试才重新判断。规则快路和缓存命中不初始化 AI 客户端。全来源任务独立于当前活动来源，翻页或切换来源不中断；正文 revision 变化仍使旧任务失效。

定位过程中可取消；完成、失败或取消后可主动“重新定位”。这些按钮只用于恢复操作，首次定位无需点击。保存、导出仍消费冻结结果，不新增网络请求。旧结果继续受版本、generation 和完整输入校验。无新增依赖或项目数据迁移；仅新增启动选择偏好，不持久化模型定位结果。

## 实现

- `src/transbridge/ui/workbench/alignment_consent.py`：应用级选择、窗口显示后非阻塞询问和偏好保存；`ui/shell/window_lifecycle.py` 接入启动。
- `ui/shell/action_catalog.py`、`menu_builder.py`、`intent_composition.py`：新增统一设置 intent 和菜单恢复入口；`ui/i18n/messages.pot` 补菜单及说明字符串。新增 `tests/ui/test_alignment_settings_menu.py` 验证无项目访问与记住拒绝后重新启用。
- `ui/workbench/terminology_alignment.py`：QObject 生命周期内自动调度、已尝试输入去重、取消/重试；`terminology_profile_controller.py` 提供工程/版本 revision；bar 首次隐藏恢复按钮。
- `ui/workbench/terminology_preview.py` 与 `application/terminology_profiles/profile_alignment.py`：传递自动/显式重试策略，自动路径复用不确定结果，保留显式重试能力；`step2.py` 通过窄接线为全来源任务提供独立的版本身份，避免来源切换取消任务。
- `tests/ui/conftest.py`：UI 测试隔离启动选择与真实用户偏好，未增加生产测试旁路。
- 同步 requirements、ADR、plan 及直接索引；首版历史增量保持不变。

## 验证

最终合并回归 **333 项通过**：术语相关 267 项，包含启动询问 13 项、自动调度与既有定位 19 项，以及应用层、模型适配器、bootstrap、预览、保存、导出及比较回归；另有不重复的窗口生命周期与项目创建 31 项、设置菜单及 shell/命令面板/locale 35 项。覆盖记住启用/拒绝、下次启动重问、多个窗口只问一次、关闭弹窗、未允许不调度、快速切换、返回旧方案、输入 revision 变化、取消不自动重跑、控件销毁、未知判断自动复用与显式重试。独立审查发现并复核修复来源切换中断任务的问题，分页与驻留两种刷新路径均新增回归。菜单集成测试暴露弹窗销毁回调引用已销毁对象的问题；改用 QObject 绑定方法并在正常完成时断开销毁回调，新增服务销毁与立即重开回归后合并通过。

运行以下命令（已有 `.venv`，临时目录与 TEMP/TMP 均隔离；需要硬链接的工程测试经自动批准在沙箱外运行）：

```powershell
.venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp .tmp-term-auto-alignment-qa/pytest tests/application/terminology_profiles tests/application/projects/test_profile_variant_commands.py tests/infra/test_terminology_occurrence_aligner.py tests/bootstrap/test_terminology_alignment_wiring.py tests/ui/workbench/test_terminology_alignment.py tests/ui/workbench/test_alignment_consent.py tests/ui/workbench/test_terminology_preview_async.py tests/ui/workbench/test_terminology_profile_switching.py tests/ui/workbench/test_terminology_projection_consistency.py tests/ui/workbench/test_profile_variant_save.py tests/ui/operations/test_write_schemes.py tests/ui/operations/test_scheme_comparison.py tests/contracts/io/test_terminology_profile_write.py -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
git -c core.safecrlf=false diff --check
```

最终合并运行在上述 pytest 命令中追加 `tests/ui/test_ai_task_shutdown.py tests/ui/test_worker_shutdown_registry.py tests/ui/test_runtime_task_shutdown.py tests/ui/test_project_creation_reuse.py tests/ui/test_alignment_settings_menu.py tests/ui/test_main_window_shell.py tests/ui/test_intent_router.py tests/ui/test_command_palette_help.py tests/ui/foundation/test_locale_service.py::test_source_template_covers_menu_catalog_and_critical_settings_msgids`，使用隔离 `--basetemp .tmp-term-auto-alignment-qa/pytest-delivery`，结果为 333 passed。

全仓 Ruff 检查与 2,631 个文件格式检查通过。启动弹窗经离屏视觉检查；未运行真实模型请求、原生交互验收、全仓 pytest 或安装包。真实模型的语义判断精度仍未验证；本轮未重复首版真实数据分流实验，也未修改用户工程正文或真实偏好。
