# 术语工作台入口与预检反馈：独立 QA

- 日期：2026-11-04
- 对应需求：FR5.16.30～32、FR5.18.25
- 对应计划：[S14～S15](../../plans/terminology-ui-remediation/plan.md)
- 范围：用户实际截图反馈后的界面简化；保留此前完整来源导入和其他未提交修改。以本轮开始的 `baseline.patch` 区分增量，不将此前功能全部记为本轮修改。
- 环境：Windows、Python 3.12.12、现有 uv 环境；所有 uv 命令使用 `--no-sync`。

## 审查范围与已修复问题

默认工作区以当前术语选择、直接导入和可用词条为主；当前编辑按对象显示，提取、管理和完整方案条目按需进入。审核、待保存、失败和发布保护仍保留，没有自动发布或放宽领域来源上限。

1. **真实导入入口故障**：`TerminologyWindowContext.preview_source_import` 未转发整批 `policy`，真实窗口调用会出现 `TypeError`。本轮修复包装器，并通过真实窗口、包装器、共享 JSON reader、应用服务及仓库验证两种策略。此前仅模拟底层控制器的测试未覆盖该缺口；本轮明确补上，不能用上一轮通过记录代替本次验证。
2. **失败投影伪造零来源**：51 个启用来源超过 50 个限制时，捕获尚未读取任何文件，空明细元组曾被显示为零。新投影独立保留已知数量、显示名和技术诊断；未知数量为 `None`，明确没有来源才为 0。真实捕获回归验证 51/50，且没有取得文件租约；未修改领域限制。
3. **更多菜单目标不可见**：从版本/报告页执行提取或查看方案条目，曾只改变隐藏的术语页；现先切换目标页。默认项目编辑也经相同真实窗口路由验证。
4. **空态编辑误导与重复说明**：无可编辑内容时隐藏编辑入口；草稿编辑保留一处发布说明及保存状态。布局复核同时修复选择标签与选择器间的大空隙、提取详情过矮导致技术详情按钮被截断。

审查同时覆盖取消/上下文变化保护、方案编辑定位、双击编辑对象、搜索分页、保存中发布阻断、版本/历史入口和任务取消。新增预检投影集中在 `preflight_presentation.py`；presenter 为 395 行/37 方法，window 为 466 行/31 方法，均保持已有公共门面，窗口仅窄接线，未向超过方法数复核门槛的类增加业务实现职责。

## 实际截图检查

Astra 逐张检查实际 `TerminologyWindow.grab()` 生成的 8 张 PNG：空态、已导入方案、草稿编辑、来源超限，分别为 920×620 与 1180×760。截图使用仓库主题和实际字体、`build_runtime` 与 SQLite 仓库、真实窗口上下文和方案控制器、实际 JSON 词表读取；项目显示名及示例词条属于测试夹具。提取超限使用真实 `BuildInputCaptureService` 的 51 个登记来源/50 上限；命令驱动复用既有 `_Harness`，关闭真实同步服务。不是用户数据截图，也不是模拟绘图。

最终检查：直接导入始终可见；空态只有一处简短指引；已有方案直接显示有效词条；编辑区保留发布入口和未发布状态，去掉重复说明；主动展开的提取错误完整显示 51/50 和技术详情，普通浏览不展示该错误面板。两种尺寸下无所检查控件截断，选择器和标签相邻，列表保有主要空间。

持久交付图：

- [空态 920](C:/Users/admin/.codex/visualizations/2026/11/04/01a1a5fc-6441-7d83-ba9c-43ba54cfab06/terminology-empty-920-20261104-c83f.png)
- [已导入 1180](C:/Users/admin/.codex/visualizations/2026/11/04/01a1a5fc-6441-7d83-ba9c-43ba54cfab06/terminology-imported-1180-20261104-c83f.png)
- [编辑 920](C:/Users/admin/.codex/visualizations/2026/11/04/01a1a5fc-6441-7d83-ba9c-43ba54cfab06/terminology-edit-920-20261104-c83f.png)
- [来源超限 920](C:/Users/admin/.codex/visualizations/2026/11/04/01a1a5fc-6441-7d83-ba9c-43ba54cfab06/terminology-source-limit-920-20261104-c83f.png)

同一交付目录保留全部 `workbench-{empty,imported,edit,source-limit}-{920,1180}.png` 原图。没有声称完成真实桌面鼠标手工操作或高 DPI 全平台验证。

## 验证命令与结果

结论：本轮验收通过，未发现待修复的功能问题。六个独立进程分组分别 **47、62、52、79、71、120 项通过**，每组退出码为 0，JUnit 用例去重为 **431 项**；不将此前聚焦 40 项、独立主题 5 项或开发者复验累加。最后的分组均在最终代码和草稿文案调整后运行。

首次将同一 431 项集中运行时，累积 Qt 控件后的主题测试明显变慢；运行仍有进展但耗时过长，于精确执行会话中断，退出码 1，没有获得完整测试结论。相同主题文件独立运行 **5 passed，1.31 秒**；包含该文件的最终分组 **52 passed，5.27 秒**。这些证据支持测试进程累积影响，不证明所有长期桌面性能场景；没有修改测试、跳过主题用例或声称首次联合运行全绿。

独立静态检查 `ruff check src tests`、`ruff format --check src tests` 均退出 0，格式检查 **2371 files already formatted**；`git diff --check` 退出 0，仅有 Git CRLF 提示。pytest 各进程有 3 项既有 SWIG 弃用警告。

实际分组执行入口：

```powershell
.venv/Scripts/python.exe .tmp-terminology-ux-20261104-c83f/run_qa_groups.py
```

该临时调度脚本在清理前的完整内容如下，保留精确测试集合、排序和命令以便重现（每个分组使用独立临时目录；无需保留生成的 XML 或缓存）：

```python
import subprocess
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

root = Path('D:/MyCode/TransBridge')
task = root / '.tmp-terminology-ux-20261104-c83f'
files = sorted(str(p.relative_to(root)) for p in (root / 'tests/ui/tools/terminology').glob('test_*.py'))
groups = [files[i:i+12] for i in range(0, len(files), 12)]
groups.append([
    'tests/ui/tools/test_terminology_source_import_controller.py',
    'tests/ui/tools/test_terminology_source_import_dialog.py',
    'tests/ui/tools/test_terminology_profile_mapping_model.py',
    'tests/ui/tools/test_terminology_profile_manager.py',
    'tests/ui/tools/test_terminology_profile_edit_protection.py',
    'tests/ui/tools/test_narrow_terminology_surfaces.py',
    'tests/ui/workbench/test_terminology_profile_switching.py',
    'tests/ui/workbench/test_terminology_projection_consistency.py',
    'tests/ui/workbench/test_terminology_preview_async.py',
])
groups.append([
    'tests/application/terminology_profiles',
    'tests/application/terminology/test_input_capture.py',
    'tests/persistence/terminology/test_profiles.py',
    'tests/ai_translator/test_term_source_reader.py',
])
failed = False
cases = set()
for index, paths in enumerate(groups, 1):
    xml = task / f'qa-group-{index}.xml'
    cmd = ['uv', '--cache-dir', str(task / 'uv-cache'), 'run', '--no-sync', 'pytest', *paths,
           '-q', '-p', 'no:cacheprovider', '--basetemp', str(task / f'qa-group-{index}-pytest'),
           '--junitxml', str(xml)]
    print(f'GROUP {index}: {subprocess.list2cmdline(cmd)}', flush=True)
    result = subprocess.run(cmd, cwd=root)
    failed |= result.returncode != 0
    if xml.exists():
        for case in ET.parse(xml).iter('testcase'):
            cases.add((case.attrib.get('classname'), case.attrib.get('name')))
    print(f'GROUP {index} EXIT {result.returncode}', flush=True)
print(f'UNIQUE CASES {len(cases)}; ANY FAILED {failed}', flush=True)
sys.exit(1 if failed else 0)
```

主题独立排查和静态检查：

```powershell
uv --cache-dir D:/MyCode/TransBridge/.tmp-terminology-ux-20261104-c83f/uv-cache run --no-sync pytest tests/ui/tools/terminology/test_shell_icon_theme.py -q -p no:cacheprovider --basetemp D:/MyCode/TransBridge/.tmp-terminology-ux-20261104-c83f/qa-theme-pytest
uv --cache-dir D:/MyCode/TransBridge/.tmp-terminology-ux-20261104-c83f/uv-cache run --no-sync ruff check src tests
uv --cache-dir D:/MyCode/TransBridge/.tmp-terminology-ux-20261104-c83f/uv-cache run --no-sync ruff format --check src tests
git diff --check
```

未运行全仓 pytest、真实 LLM、真实远端写入或完整应用长时间性能测试；本轮无领域/持久化格式变更，不重跑上一轮无关报告硬链接测试。没有提交或推送。主会话负责增量记录和核验清理本轮临时目录，截图属于保留的交付物。
