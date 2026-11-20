# 插件主来源与不兼容导入 QA

- 日期：2026-11-17。
- 范围：[start-center-launch-hub S07](../../plans/start-center-launch-hub/plan.md#story-07插件主来源与不兼容导入)。
- 仅收敛新工程主来源入口，保留已有译文导入、工作台格式能力和数据存储格式。前序工作台、菜单改动不重复归入此轮。

## 专项结果

环境与下方独立回归相同，执行：

```powershell
.venv\Scripts\python.exe -m pytest tests/ui/test_project_creation_form.py tests/ui/test_start_center_multi_plugin.py tests/ui/test_start_center_guided_project.py tests/ui/test_guided_file_options.py tests/ui/test_open_file_dialog.py --deselect tests/ui/test_open_file_dialog.py::test_open_dialog_distinguishes_all_formats_without_old_mode_switch_or_preview -q --basetemp qa-tmp-start-s07-plugin-agent-20261117/final
.venv\Scripts\python.exe -m pytest tests/ui/test_project_creation_file_workflow.py tests/persistence/v2/test_project_creation_file_options.py -q --basetemp qa-tmp-start-s07-plugin-agent-20261117/real
```

分别 **71 passed、1 deselected，exit 0**；**40 passed，exit 0**。真实持久化组通过工具审批后运行硬链接发布流程。覆盖普通入口拒绝非插件、大小写插件后缀、格式锁定、独立 Intent 路由、警告确认/取消、忙碌状态保护、混合来源格式、名称/译文选项保留。真实 worker 和存储覆盖插件、EET、ParaTranz JSON 成功建项，以及将 XML 改名为 ESP 后实际解析拒绝且不创建工程；已有译文格式、替换和 Strings 选项保留。

首轮未排除工作台测试时为 71 passed、1 failed。唯一失败是 `tests/ui/test_open_file_dialog.py:35`，断言没有三种 `strings.*`，而既有 `FILE_SOURCE_FORMATS` 已包含它们。单独进程直接读取并加载 `git show HEAD:src/transbridge/ui/workbench/file_source_formats.py` 的模块，再运行该测试，复现相同失败。`OpenFileDialog` 和该测试文件未改动；此项列为既有失败，不算通过，不通过修改无关工作台行为来消除。结合下面独立组，本轮最终为 **211 项不同用例通过，1 项系统能力跳过，1 项已复现既有失败被排除**。

## 独立关联回归

使用仓库已有 `.venv` 执行以下命令，环境 `QT_QPA_PLATFORM=offscreen`、`PYTHONIOENCODING=utf-8`：

```powershell
.venv\Scripts\python.exe -m pytest tests/ui/test_start_center_responsive_layout.py tests/ui/test_start_center_primary_contrast.py tests/ui/test_start_center_draft_focus.py tests/ui/test_start_center_directory.py tests/ui/test_start_center_window_isolation.py tests/ui/test_project_creation_deadlock.py tests/ui/test_project_creation_reuse.py tests/ui/test_project_creation_transition.py tests/ui/test_plugin_drop_workflow.py tests/ui/test_safe_drop_router.py tests/ui/test_safe_drop_binding.py -q --basetemp=qa-tmp-plugin-entry-root-20261117/regression-full --tb=short
```

结果：**100 passed、1 skipped，exit 0**。跳过项为系统符号链接能力限制。首次在受限沙箱运行时，真实数据库发布使用的 `os.link` 被 Windows 拒绝；获得工具审批后以新的任务临时目录重跑上述同组，全部有效用例通过。未修改持久化代码绕开权限。

## 静态与视觉检查

```powershell
.venv\Scripts\ruff.exe check src tests
.venv\Scripts\ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

三条均 exit 0；格式检查覆盖 2699 个文件。

使用生产 `StartCenterWidget`、`ProjectCreationForm` 和实际不兼容确认窗口，注入合成路径与空工程目录，按生产主题服务渲染浅色和深色。检查开始页新增小入口、来源列表三个按钮、插件格式锁定及中文警告按钮，未见新增裁切。确认默认与 Escape 均取消。截图没有读取用户真实工程。

产物在 `C:/Users/admin/.codex/visualizations/2026/11/17/01a1e86c-f338-7663-8c31-d54109440461/`：`plugin-entry-landing-light.png`、`plugin-entry-landing-dark.png`、`plugin-entry-form-light.png`、`plugin-entry-form-dark.png`、`plugin-entry-warning-light.png`、`plugin-entry-warning-dark.png`。截图临时加载系统中文字体，不改产品字体设置。

## 职责审查与边界

`guided_project_coordinator.py` 达 503 行，主要类 27 个方法，按仓库 500 行阈值复审：新增仅是既有建项草稿的格式约束和逐来源请求组装，没有新增持久化、解析或文件对话框职责，仍属于单一草稿生命周期；此次无需拆分。其他相关模块和主要类未越过阈值。

未执行全仓 pytest、安装包验证、真实用户 Mod 及用户运行中窗口的交互验收；实际格式解析与测试工程验证见专项结果。无数据迁移，无云端调用，未提交 Git；`.transbridge/` 等已有用户数据未触碰。
