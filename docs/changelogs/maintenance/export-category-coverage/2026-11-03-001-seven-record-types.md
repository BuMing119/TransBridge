# 补全七种记录类型的导出分类

- 日期：2026-11-03
- Epic/Story：maintenance/export-category-coverage（直接维护，按用户确认的分类映射实现）

## 变化与原因

- `src/transbridge/converter/context_categories.py`：修改权威 `EXPORT_CATEGORIES`，把 `WOOP:FULL`、`WOOP:TNAM` 归入“法术_龙吼_技能”，`BOOK:CNAM` 归入“书籍_内容”，`APPA:FULL`、`AMMO:DESC`、`LIGH:FULL` 归入“物品”，`REGN:RDMP` 归入“地名与门”。这七种已知类型不再落入“其他.json”；复用既有分类，无新增类别。
- `tests/contracts/io/test_paratranz_facades.py`：新增参数化回归，覆盖原始 context 和八位显示序号前缀；实际写出 JSON 并核对四个目标文件及完整记录集合，核对分类上传与本地导出一致、原文译文及本地源条目保持。
- `docs/changelogs/INDEX.md`：新增本记录链接，保留所有既有工作区内容。本记录不包含其他未提交变更。

## 验证

测试和 Ruff 使用现有 uv 环境，设置 `UV_CACHE_DIR=D:/MyCode/TransBridge/.uv-cache-agent`。

- 修改规则前：`uv run pytest tests/contracts/io/test_paratranz_facades.py -q -k additional_record_types --basetemp=.tmp-export-category-20261103`：两个新增用例均失败，实际七种类型全部进入“其他.json”，证明回归有效。
- 修改规则后：`uv run pytest tests/contracts/io/test_paratranz_facades.py tests/contracts/io/test_paratranz_classification.py tests/application/categorized_upload -q --basetemp=.tmp-export-category-20261103`：37 通过，包含未知类型兜底和对话分类的既有回归。
- `uv run ruff check src tests`：通过。
- `uv run ruff format --check src tests`：2352 个文件通过。
- 初次测试分别遇到默认 uv 缓存和 pytest 临时目录写权限问题，改用仓库现有缓存和任务专用临时目录后正常运行。
- 未运行全库 pytest、UI 手工测试或云端写入：本次仅补导出分类常量，已验证共享导出和上传入口。

## 兼容与遗留

后续本地分类导出和分类上传使用新归属。未知类型仍保留“其他.json”兜底；不改变 UI 分类、AI 批次策略、普通上传或存储格式。已有本地导出目录和云端“其他.json”不会自动删除或迁移。本次规则修改无已知遗留；历史文件整理不在本次范围。未提交 Git。
