# 开始中心已有译文长路径宽度修复

- 日期：2026-11-03
- Epic/Story：maintenance/start-center-long-path（直接缺陷修复）

## 变化与原因

- `src/transbridge/ui/shell/start_center.py`：`_build_draft_page` 的已有译文标签改用现有 `ElidedLabel`，按可用宽度省略并明确使用纯文本；`render_draft` 使用 `set_full_text` 更新，完整路径通过逐行 tooltip 和无障碍描述保留，清空选择时清除 tooltip。普通单行 QLabel 的文本最小宽度会撑大整个窗口，隐藏的草稿页也会继续约束堆叠布局。
- `tests/ui/test_start_center_responsive_layout.py`：新增单条/四条长路径参数回归，覆盖选择后不自动撑宽、1200/720/850 像素往返缩放、选择按钮可达、完整路径可访问、返回开始页仍可缩小，以及清空译文来源。
- `docs/changelogs/INDEX.md`：新增本记录链接，保留既有工作区条目。

## 验证

- 修复前 `uv run pytest tests/ui/test_start_center_responsive_layout.py -q`：7 通过、2 失败；两个新增用例把 850 像素窗口分别撑到 2313 和 8469 像素，证实回归测试有效。
- 修复后 `uv run pytest tests/ui/test_start_center_responsive_layout.py tests/ui/test_start_center_multi_plugin.py tests/ui/test_start_center_draft_focus.py tests/ui/test_start_center_guided_project.py tests/ui/test_start_center_primary_contrast.py -q`：51 通过、1 失败；全部 9 项响应布局测试通过。
- 失败为 `test_choose_multiple_plugins_creates_one_project_and_loads_every_collection` 的第 166 行：实际 slot key 为 `source-0` 等，断言期待路径。用 `git show HEAD:src/transbridge/ui/shell/start_center.py` 在 Python 内存加载修改前模块后，单独调用 `pytest.main` 重跑该用例，同样失败；属于当前工作区既有问题，不纳入本次修复。
- `uv run ruff check src tests`：通过。
- `uv run ruff format --check src tests`：2352 个文件通过。
- 本次代码及测试 `git diff --check`：通过。
- 首次沙箱测试被 uv 缓存写权限阻断，后续测试与 Ruff 在获批的正常环境运行。Qt 测试采用 offscreen 和实际主题字体，未执行全库测试、打包或真实桌面人工拖拽验收。

## 兼容与遗留

仅改变路径展示，不修改来源选择、导入数据或工程格式，无迁移要求。本次缺陷无已知遗留；上述既有多插件断言失败保持原样。未创建临时文件，未提交 Git。
