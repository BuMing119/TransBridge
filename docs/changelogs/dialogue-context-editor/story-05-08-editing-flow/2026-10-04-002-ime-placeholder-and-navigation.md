# 输入法组字重叠与导航计数换行修复

- 日期：2026-10-04
- Epic / Story：dialogue-context-editor / 05–08
- 来源：用户截图显示译文框的占位提示与输入法预编辑文字重叠，底部 `2 / 8291` 换行。

## 本次差异

- `src/transbridge/ui/dialogue/view.py`：移除译文框内冗余占位提示，保留页顶应用说明及无障碍名称；避免空文档处于输入法组字状态时提示和预编辑文字共用首行。导航标签不再自动换行，完整计数获得单行所需宽度。未改变输入法事件、复制粘贴或快捷键。
- 新增 `tests/ui/test_dialogue_input_visuals.py`：通过 QInputMethodEvent 覆盖组字、提交、撤销及取消；验证最小窗口尺寸下 `2 / 8291` 不换行、不与操作按钮重叠。

## 验证

- `uv run --no-sync --no-cache pytest tests/ui/test_dialogue_input_visuals.py tests/ui/test_dialogue_editing_flow.py tests/ui/test_dialogue_editor.py -q -p no:cacheprovider`：41 passed。
- `uv run --no-sync --no-cache ruff check src tests`：通过。
- `uv run --no-sync --no-cache ruff format --check src tests`：通过。
- `git -c core.safecrlf=false diff --check -- src/transbridge/ui/dialogue/view.py`：通过。
- 未运行全仓库测试或真实 Windows 输入法人工验收；本次使用 Qt 离屏事件回归。无临时文件、依赖或数据格式变更。
