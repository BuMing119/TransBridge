# Story 13: 历史报告查看

**所属方案**: `plans/ai-post-process/plan.md`
**状态**: ✔️ 已实现
**对应需求**: FR6.10.7

## 概述

在 AI 翻译窗口或工具面板中提供「历史报告」入口，列出过往生成的所有报告文件，支持双击用系统默认程序打开 Excel 文件。

## 验收标准

- [ ] AI 翻译窗口底部或工具栏中有「历史报告」按钮
- [ ] 点击后弹出历史报告列表对话框
- [ ] 列表显示：报告文件名、类型(翻译/润色)、插件名、生成时间
- [ ] 双击某行 → 使用系统默认程序打开 Excel 文件
- [ ] 无历史报告时显示空状态提示「暂无历史报告」
- [ ] 支持多选删除历史报告（右键菜单或 Delete 键）

## 实现步骤

### 步骤 1: 创建历史报告列表对话框
- 新建 `src/transbridge/ui/tools/ai_translator/_report_history_dialog.py`
- 类：`_ReportHistoryDialog(QDialog)`
- QTableWidget 列：文件名 / 类型(翻译/润色) / 插件名 / 生成时间 / 文件大小
- 数据来源：扫描 `data/ai_translator/*/reports/` 目录下所有 `.xlsx` 文件
- 按生成时间降序排列
- 双击行 → `os.startfile(filepath)` 打开 Excel
- 右键菜单：「打开」「打开所在目录」「删除」
- 支持多选删除（`ExtendedSelection`），删除前弹出确认框
- 窗口大小：`700x450`，标题：「历史报告」
- 涉及文件: `src/transbridge/ui/tools/ai_translator/_report_history_dialog.py` (新)

### 步骤 2: 解析报告文件名
- 从文件名 `{esp_stem}_{mode}_report_{YYYYMMDD_HHMMSS}.xlsx` 中提取：
  - esp_stem（插件名）
  - mode（translate/polish → 显示为「翻译」/「润色」）
  - timestamp（解析为 datetime，格式化显示）
- 文件大小通过 `os.path.getsize()` 获取，格式化为 KB/MB
- 涉及文件: `src/transbridge/ui/tools/ai_translator/_report_history_dialog.py`

### 步骤 3: 添加入口按钮
- 在 AI 翻译窗口（`AITranslatorWindow`）底部按钮栏添加「历史报告」按钮
- 位置：在现有的「开始翻译/润色」按钮左侧或底部
- 点击 → 创建并显示 `_ReportHistoryDialog`（非模态）
- 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`

### 步骤 4: 空状态处理
- 无历史报告时：QTableWidget 隐藏，显示 QLabel「暂无历史报告」（居中，灰色文字）
- 删除最后一份报告后：自动切换到空状态
- 涉及文件: `src/transbridge/ui/tools/ai_translator/_report_history_dialog.py`

## 关键接口

```python
class _ReportHistoryDialog(QDialog):
    def __init__(self, parent=None):
        # 扫描 data/ai_translator/*/reports/*.xlsx
        ...

    def _scan_reports(self) -> list[dict]:
        """扫描所有报告文件，返回 [{path, esp_stem, mode, timestamp, size}, ...]"""
        ...

    def _open_report(self, path: str):
        """使用系统默认程序打开报告文件"""
        os.startfile(path)

    def _delete_reports(self, paths: list[str]):
        """删除选中的报告文件"""
        ...
```

## 架构依赖

- `LLMConfig.get_ai_translator_dir()` — 获取数据目录
- `os.startfile` — Windows 打开文件
- 文件系统扫描 — 纯 Python 标准库

## 边界条件

- `data/ai_translator/` 目录不存在 → 空状态
- 报告文件命名不符合预期格式 → 跳过，仍列出但显示原始文件名
- 文件正在被其他程序打开 → 删除可能失败，捕获异常并提示
- 大量历史报告（>100份）→ 扫描无性能问题，列表支持滚动
