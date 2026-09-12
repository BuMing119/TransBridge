# AI 任务中的项目术语展示

- 状态：已完成（实现、回归与离屏布局验证通过）
- 路线：bm-pilot → bm-dev → 验证。当前工具无 update_plan，以本文件记录阶段。
- 目标：在“术语库”页直接识别当前项目术语的可用状态、已发布版本和数量，并进入项目术语工作台查看条目。

## 实施与验收

1. 已完成：核对任务冻结术语、译名方案及工作台入口；复用现有项目术语读取链路，不改变翻译或文件格式行为。
2. 已完成：独立展示组件与后台读取控制器；展示当前工程、术语版本、有效条目数量，以及无工程、未发布和读取失败状态。将原“前往术语工作台创建译名方案”入口改为明确的“查看项目术语库…”。
3. 已验证：切换项目/翻译版本/方案、重新激活窗口或手动刷新后更新状态；忽略过期结果，关闭窗口不阻塞或访问已销毁控件。
4. 已验证：聚焦 UI 与术语运行时回归、全仓库 Ruff 检查、真实 Qt 离屏布局检查。

数量表示当前已发布术语版本中的有效条目总数，不承诺每条都会命中本次插件或每个翻译批次。任务启动时仍由原有链路固定最终使用版本。

## 验证结果

- `uv run pytest tests/ui/tools tests/ui/workbench/test_terminology_profile_switching.py tests/ai_translator/test_project_terminology_runtime.py tests/ai_translator/test_project_terminology_adapter.py tests/integration/bootstrap/test_terminology_task_wiring.py -q`：388 passed（含新增 16 项状态、刷新、线程及生命周期测试）。
- `uv run ruff check src tests`：通过。
- `uv run ruff format --check src tests`：1243 个文件通过。
- 使用合成术语、应用主题和中文字体渲染真实 Qt 窗口；1120×900、720×620 和字体放大场景无横向溢出，项目术语入口可见。
- `git diff --check`：通过。未运行全仓库 pytest、真实 LLM/ParaTranz 请求或安装包构建；本次不更改业务执行或数据格式，无迁移要求。
- 沙箱内 uv 缓存和 Python 启动受限，验证已通过获批的沙箱外执行使用原有 uv 环境完成；没有修改依赖或锁文件。
