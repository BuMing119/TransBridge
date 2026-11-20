# 六组主菜单与工程管理入口 QA

- 日期：2026-11-17。
- 范围：[现代工作台 S08](../../plans/modern-workbench-visual-shell/plan.md#s08-六组主菜单与工程管理入口)。保留已完成 S07 变更。
- 结论：相关 156 项不同用例通过，Ruff 和差异空白检查通过。未发现本轮交付范围内的阻塞问题。

## 实现与审查

主菜单为工程、翻译、ParaTranz、视图、设置、帮助。工程与译文输出、云端操作分组对应已确认草案；管理翻译版本、历史还原和工程属性打开实际 Qt 对话框。工程包主入口不显示扩展名，格式筛选及实际文件名仍保留 `.transbridge`。

旧 Intent 和兼容句柄仍存在。快捷键使用同一 QAction 关联主窗口，避免菜单收缩后失效；没有增加第二套回调。列表使用稳定版本或快照标识，关闭后丢弃异步回调。历史窗口绑定打开时的工程和版本，切换后失效，返回原版本需显式刷新。新建或复制前的保存结束后再次检查目标；来源修订恢复完成后才刷新还原点列表。实际恢复前备份和删除确认仍由原协调器及应用服务执行。

## 验证命令与结果

以下 pytest 均设置 `QT_QPA_PLATFORM=offscreen`、`PYTHONIOENCODING=utf-8`，使用仓库既有 uv 环境 `.venv`，没有安装或更新依赖。

1. `.venv/Scripts/python.exe -m pytest tests/ui/test_project_management_dialogs.py tests/ui/test_main_window_shell.py tests/ui/test_alignment_settings_menu.py tests/ui/foundation/test_locale_service.py tests/ui/test_command_palette_help.py tests/ui/test_source_update.py::test_project_menu_exposes_source_update_with_its_own_intent -q --basetemp qa-tmp-menu-s08-workbench-agent-20261117/final`：57 通过。覆盖六组菜单结构与唯一性、兼容动作、四个快捷键在收缩和展开状态只执行一次、新窗口关闭/加载失败/上下文过期/显式重试、属性入口、AI 设置页面、旧搜索别名和本地化。
2. `.venv/Scripts/python.exe -m pytest tests/ui/test_v2_project_transfer.py tests/ui/test_v2_variant_hydration.py tests/ui/test_project_text_encoding.py tests/ui/operations/test_batch_download_production.py tests/ui/test_workbench_context_layout.py tests/ui/guidance -q --basetemp qa-tmp-menu-s08-root-20261117/regression`：初次 86 通过，12 项仅因下载菜单旧名称断言失败。对齐该断言后，`.venv/Scripts/python.exe -m pytest tests/ui/operations/test_batch_download_production.py -q -k real_shell_and_current_project_routes --basetemp qa-tmp-menu-s08-root-20261117/download-rerun` 的 12 项全部通过。该组共 98 项不同用例通过。
3. `.venv/Scripts/python.exe -m pytest tests/ui/test_project_management_integration.py -q --basetemp qa-tmp-menu-s08-root-20261117/list-integration`：1 通过。从新列表按钮创建实际快照，修改后恢复标签，确认生成恢复前备份，再删除原还原点并确认当前内容不变。
4. `.venv/Scripts/ruff.exe check src tests`：通过。
5. `.venv/Scripts/ruff.exe format --check src tests`：通过。
6. `git -c core.safecrlf=false diff --check`：通过。

首次普通沙箱快照测试在数据库发布所需的 `os.link` 处遭遇 Windows `WinError 5`；实际数据回归随后以已获准的沙箱外测试方式在任务专属目录运行。测试没有接触用户工程或外部云端写入。聚焦测试曾发现收缩菜单的快捷键失效，修正 QAction 主窗口关联后复验通过。

补充：最后将工作台“更多”中的同一来源操作复用为“添加翻译文件…”后，分别执行 `.venv/Scripts/python.exe -X faulthandler -m pytest tests/ui/test_workbench_slices.py -qs -p no:cacheprovider`（11 项）及替换为 `tests/ui/test_workbench_context_layout.py`（8 项），两者均正常退出 0。按 slices → context 顺序同进程运行时出现测试进度完成后退出 1；主代理用 `-vv -s` 可见 19 项全部 PASSED 和 pytest 总结，但进程仍返回 1。以只存在于该测试进程的 import loader 加载本轮修改前的应用模块（S08 独有模块取 HEAD，混合 widget 仅撤回本轮两行标签改动），保持工作文件不变，复现相同退出异常。该证据表明异常并非本轮菜单改动新增，但尚未定位组合运行退出的根因；未计入上述 156 项通过统计，也未改动无关生命周期代码。

## 实际界面复核

通过生产 MenuBuilder、ProgressiveMenuBar 和三个生产管理对话框，配合合成工程及只读测试回调离屏渲染。使用实际 ThemeService 的浅色、深色主题；沙箱未枚举中文字体，因此截图脚本显式加载 Windows 已安装的微软雅黑，产品代码没有增加字体依赖。

输出位于 `C:/Users/admin/.codex/visualizations/2026/11/17/01a1e86c-f338-7663-8c31-d54109440461/`：

- `menu-s08-bar-{light,dark}.png`、`menu-s08-collapsed-{light,dark}.png`：六组菜单与收缩栏。
- `menu-s08-{project,translation,paratranz,view,settings,help}-{light,dark}.png`：六组实际菜单。工程菜单高 430，翻译菜单高 295，标签及快捷键可读。
- `menu-s08-{versions,history,properties}-{light,dark}.png`：三个实际管理窗口，版本及历史窗口 540×360，属性窗口 460×185；按钮完整，无新增常驻工作台图标。

已人工查看六组菜单、版本和还原列表及浅深主题的代表性截图。离屏渲染不等同于用户当前运行实例的交互验收；没有执行真实云端上传、下载或全仓测试，也未对所有系统 DPI 组合验收。此次无需数据迁移。

任务专属测试目录及截图脚本已清理，截图作为交付产物保留；既有 `.transbridge/` 用户数据未改动。
