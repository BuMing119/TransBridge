# Story 12: 完成流程集成

**所属方案**: `plans/ai-post-process/plan.md`
**技术模块**: UI (PyQt6) + AI Translator Backend
**状态**: ✔️ 已实现
**创建日期**: 2026-05-09

## 前置依赖

### 上游 Story
- Story-10（报告生成后端）：已完成 → 提供 `ReportGenerator` 类
- Story-11（应用内报告对话框）：已完成 → 提供 `_TranslationReportDialog` 类

### 跨 Plan 依赖
- `ai-translation/plan.md` → `AutoTranslator.translate()`, `TranslationResult`
- `ui-workbench/plan.md` → `MainWindow`, `AppContext`

### 引用的架构决策
- ADR-004: QThread + 信号总线异步模式 — Worker 信号传递后处理中间数据
- ADR-003: 三轮 AI 翻译策略 — 翻译完成后触发报告

## 验收标准

（从 plan 原样复制）

- [ ] 单插件翻译完成后：生成 Excel + 弹出报告对话框（替代 QMessageBox）
- [ ] 单插件润色完成后：生成 Excel + 弹出报告对话框（替代 QMessageBox）
- [ ] 翻译被停止时：仍生成部分报告（已完成的条目）
- [ ] 批量翻译完成后：弹出跨插件汇总弹窗（列出每插件状态）→ 点击单个插件打开对应报告
- [ ] 润色预览（_PolishPreviewDialog）保留不变，报告在预览确认后弹出
- [ ] Excel 写入失败时报告对话框仍正常弹出（「打开 Excel」按钮禁用）
- [ ] 兼容 `--bg` 后台运行模式（不弹窗）

## 数据流

```
翻译模式:
  AutoTranslator.translate()
    │
    ├── 翻译轮次完成 + 后处理完成
    │     └── TranslationResult 携带 post_process_result + refine/polish/decisions
    │
    ▼
  _TranslationWorker.finished signal → result 对象
    │
    ▼
  _TranslationProgressWindow._on_result(result)
    │
    ├── 1. 创建 ReportGenerator(esp_stem)
    ├── 2. generator.generate_translate_report(result, refine, polish, decisions)
    ├── 3. result.report_path = path
    ├── 4. if not self._was_stopped and not self._bg_mode:
    │         dialog = _TranslationReportDialog(translate_result=result, ...)
    │         dialog.entry_activated.connect(main_window._on_report_entry_activated)
    │         dialog.show()
    │    （移除原有的 QMessageBox.information）
    └── 5. self._ctx.collection_changed.emit(...)

润色模式:
  _PolishWorker.run()
    │
    ▼
  finished_all signal → {entry_id: PolishResult}
    │
    ▼
  AITranslatorWindow._on_polish_finished(results)
    │
    ├── if polish_preview_enabled:
    │     ├── 弹出 _PolishPreviewDialog (用户逐条接受/拒绝)
    │     └── 用户确认 → 汇总 stats
    ├── else:
    │     └── 直接应用所有润色结果
    ├── 1. 创建 ReportGenerator(esp_stem)
    ├── 2. generator.generate_polish_report(results, entries, stats)
    ├── 3. dialog = _TranslationReportDialog(polish_entries=..., polish_stats=...)
    ├── 4. dialog.entry_activated.connect(main_window._on_report_entry_activated)
    └── 5. dialog.show()

批量模式:
  每个插件独立走翻译流程
    │
    ▼
  _BatchTranslationProgressWindow._on_all_finished()
    │
    ├── 1. 收集所有插件的 TranslationResult
    ├── 2. 为每个成功插件生成独立报告
    ├── 3. 弹出 _BatchReportSummaryDialog
    │     ├── 表格: 插件名 | 状态(✅⚠❌) | 成功数 | 失败数 | 需审核
    │     ├── 双击行 → 打开该插件 _TranslationReportDialog
    │     └── 「打开报告目录」按钮
    └── 4. （移除原有的 QMessageBox.information）

后台模式:
  翻译完成 → 仅生成 Excel (不弹窗)
    → 状态栏提示「翻译完成，报告已生成」
```

## 关键接口

### TranslationResult 扩展

```python
# translator.py (修改)

@dataclass
class TranslationResult:
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    new_dynamic_terms: int = 0
    failed_entries: list[str] = field(default_factory=list)
    post_process_result: "PostProcessResult | None" = None
    # ── 新增字段 ──
    refine_results: dict | None = None     # {entry_id: RefineResult}
    polish_results: dict | None = None     # {entry_id: PolishResult}
    decisions: dict | None = None          # {entry_id: ArbiterDecision}
    report_path: str | None = None         # 生成的 Excel 报告路径
```

### _BatchReportSummaryDialog

```python
# _batch_report_summary_dialog.py (新建)

class _BatchReportSummaryDialog(QDialog):
    open_plugin_report = pyqtSignal(str)  # esp_stem
    open_report_dir = pyqtSignal()

    def __init__(
        self,
        plugin_results: list[dict],  # [{esp_stem, status, success, failed, needs_review, report_path}]
        parent=None,
    ): ...
```

## 实现步骤

### 步骤 1: 翻译 Worker 数据传递

**涉及文件**: `src/transbridge/ai_translator/translator.py`（修改）

**实现要点**:
- 在 `TranslationResult` 新增 4 个字段：`refine_results`, `polish_results`, `decisions`, `report_path`
- 在 `AutoTranslator.translate()` 的后处理部分，将中间结果保存到 `result`：
  - `result.refine_results = refine_results`（后处理阶段2a的输出）
  - `result.polish_results = polish_results`（后处理阶段2b的输出）
  - `result.decisions = decisions`（后处理阶段3的输出）

**边界条件**:
- 后处理未启用 → 这些字段保持 None
- 后处理部分阶段未启用 → 对应字段为 None 或空 dict

**伪代码**:
```python
# translator.py — AutoTranslator.translate() 结尾部分
result = TranslationResult(
    success_count=...,
    failed_count=...,
    ...
    post_process_result=pp_result,
    # 新增
    refine_results=refine_results if 'refine_results' in dir() else None,
    polish_results=polish_results if 'polish_results' in dir() else None,
    decisions=decisions if 'decisions' in dir() else None,
)
return result
```

**测试策略**: 运行翻译流程，验证 TranslationResult 中新增字段非 None（后处理启用时）。

### 步骤 2: 重构单插件翻译完成流程

**涉及文件**: `src/transbridge/ui/tools/ai_translator/_translation_progress_window.py`（修改）

**实现要点**:
- 修改 `_on_result(result)` 方法：
  1. 生成报告（调用 ReportGenerator）
  2. 设置 `result.report_path`
  3. 判断是否弹对话框（非后台模式 + 非停止或有成功条目）
  4. 删除 `QMessageBox.information()` 和 `QMessageBox.critical()` 调用
- 信号连接：报告的 `entry_activated` → 主窗口的 `_on_report_entry_activated`
- 访问主窗口：通过 `self._ctx` 或 `self.parent()` 获取

**边界条件**:
- 后台模式（`self._was_background` 标记）→ 不弹窗，日志中输出「报告已生成」
- 翻译被停止（`self._was_stopped`）→ 仍生成报告（基于已完成条目），对话框标题加「(已停止)」
- 翻译完全失败（success_count=0）→ 生成报告（全0数据），弹对话框
- Excel 生成失败 → report_path=None，对话框正常弹出

**伪代码**:
```python
# _translation_progress_window.py — _on_result() 方法修改
def _on_result(self, result):
    self._total_progress_bar.setValue(100)
    self._total_progress_lbl.setText("完成")

    # ... 折叠批次组件、更新日志等（保持不变）

    self._collection_synced = True
    self._ctx.collection_changed.emit(self._ctx.collection)
    self.translation_completed.emit()

    # ── 新增：生成报告 ──
    stem = Path(self._esp_path).stem
    generator = ReportGenerator(stem)
    report_path = generator.generate_translate_report(
        result,
        refine_results=getattr(result, 'refine_results', None),
        polish_results=getattr(result, 'polish_results', None),
        decisions=getattr(result, 'decisions', None),
    )
    result.report_path = report_path

    # ── 新增：弹出报告对话框（替代 QMessageBox）──
    if not getattr(self, '_was_background', False):
        dialog = _TranslationReportDialog(
            translate_result=result,
            refine_results=getattr(result, 'refine_results', None),
            polish_results=getattr(result, 'polish_results', None),
            decisions=getattr(result, 'decisions', None),
            report_path=report_path,
        )
        # 连接条目跳转信号
        main_win = self._find_main_window()
        if main_win:
            dialog.entry_activated.connect(main_win._on_report_entry_activated)
        dialog.show()
        self._report_dialog = dialog  # 保持引用防止被GC

    # 删除原有的 QMessageBox.information / QMessageBox.critical 调用
```

**测试策略**: 触发翻译完成 → 验证QMessageBox不再出现，报告对话框正常弹出。后台模式下验证不弹窗。

### 步骤 3: 集成润色完成流程

**涉及文件**: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`（修改）

**实现要点**:
- 修改润色完成处理逻辑（`_on_polish_worker_finished` 或等效方法）：
  1. 收集 `PolishResult` 字典
  2. 若配置了预览确认 → 弹出 `_PolishPreviewDialog` → 用户确认后收集 stats
  3. 若未配置预览 → 直接应用润色结果到条目，收集 stats
  4. 创建 `ReportGenerator` → 生成润色 Excel 报告
  5. 创建 `_TranslationReportDialog(润色模式)` → `show()`
- stats 字典构造：
  ```python
  stats = {
      "total": len(results),
      "accepted": accepted_count,
      "rejected": rejected_count,
      "failed": failed_count,
      "polish_level": self._polish_level_combo.currentText(),
      "avg_confidence": avg_confidence,
  }
  ```

**边界条件**:
- 选中条目均无译文 → 在润色开始时已拦截，不会走到此流程
- LLM 调用部分失败 → 失败条目计入 stats["failed"]
- 预览确认取消 → 不生成报告，不写回数据

**伪代码**:
```python
# ai_translator_window.py — 润色完成处理
def _on_polish_worker_finished(self, results: dict):
    """润色Worker完成后的处理。"""
    entries = self._get_polish_target_entries()
    esp_stem = Path(self._ctx.esp_path).stem if self._ctx.esp_path else "unknown"

    if self._preview_enabled.isChecked():
        # 弹出预览对话框
        preview = _PolishPreviewDialog(entries, results, parent=self)
        if preview.exec() != QDialog.DialogCode.Accepted:
            return  # 用户取消
        accepted = preview.get_accepted()
        rejected = preview.get_rejected()
        # 应用接受的润色结果到条目
        for entry_id, result in accepted.items():
            entry = self._find_entry(entry_id)
            if entry:
                entry._replace(entry._replace(translation=result.polished_translation))
    else:
        # 直接应用所有润色结果
        accepted = results
        rejected = {}
        for entry_id, result in results.items():
            entry = self._find_entry(entry_id)
            if entry:
                entry._replace(entry._replace(translation=result.polished_translation))

    # ── 生成报告 ──
    failed = {eid: r for eid, r in results.items() if r.confidence == 0.0}
    avg_conf = sum(r.confidence for r in results.values()) / len(results) if results else 0
    stats = {
        "total": len(results),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "failed": len(failed),
        "polish_level": self._cfg.polish_level if hasattr(self, '_cfg') else "moderate",
        "avg_confidence": avg_conf,
    }

    generator = ReportGenerator(esp_stem)
    report_path = generator.generate_polish_report(results, entries, stats)

    # ── 弹出报告对话框 ──
    dialog = _TranslationReportDialog(
        polish_entries=entries,
        polish_results_dict=results,
        polish_stats=stats,
        report_path=report_path,
    )
    main_win = self._find_main_window()
    if main_win:
        dialog.entry_activated.connect(main_win._on_report_entry_activated)
    dialog.show()
    self._report_dialog = dialog

    self._ctx.collection_changed.emit(self._ctx.collection)
```

**测试策略**: 润色3条条目，预览接受2条拒绝1条 → 验证报告stats中accepted=2, rejected=1。

### 步骤 4: 集成批量完成流程

**涉及文件**: `src/transbridge/ui/tools/ai_translator/_batch_translation_progress_window.py`（修改）

**实现要点**:
- 修改 `_on_all_finished()` 方法：
  1. 收集所有插件的翻译结果（已在 `_plugin_results` 中保存）
  2. 为每个成功插件生成独立报告
  3. 弹出 `_BatchReportSummaryDialog`
  4. 删除原有的 `QMessageBox.information()` 调用
- 插件结果数据结构：
  ```python
  plugin_results = [
      {
          "esp_stem": str,
          "esp_path": str,
          "status": "success" | "failed" | "stopped",
          "success": int,
          "failed": int,
          "skipped": int,
          "needs_review": int,  # 从 post_process_result.needs_review 获取
          "report_path": str | None,
          "result": TranslationResult,  # 完整结果对象（用于打开报告对话框）
      },
      ...
  ]
  ```

**边界条件**:
- 批量中某插件完全失败 → status="failed"，不生成报告，汇总中标记❌
- 批量中某插件被中断 → status="stopped"，仍生成部分报告
- 只有一个插件 → 直接打开该插件报告对话框，跳过汇总弹窗
- 所有插件失败 → 汇总弹窗显示全❌，不弹出单个报告

**伪代码**:
```python
# _batch_translation_progress_window.py
def _on_all_finished(self):
    # 收集并生成报告
    for plugin in self._plugin_results:
        if plugin["status"] in ("success", "stopped") and plugin["result"].success_count > 0:
            stem = Path(plugin["esp_path"]).stem
            generator = ReportGenerator(stem)
            r = plugin["result"]
            report_path = generator.generate_translate_report(
                r,
                refine_results=getattr(r, 'refine_results', None),
                polish_results=getattr(r, 'polish_results', None),
                decisions=getattr(r, 'decisions', None),
            )
            plugin["report_path"] = report_path

    # 如果只有一个成功的插件，直接打开报告
    success_plugins = [p for p in self._plugin_results if p["status"] == "success"]
    if len(success_plugins) == 1:
        self._show_single_report(success_plugins[0])
        self.close()
        return

    # 弹出批量汇总对话框
    summary_dialog = _BatchReportSummaryDialog(self._plugin_results, parent=self)
    summary_dialog.open_plugin_report.connect(self._show_single_report_by_stem)
    summary_dialog.show()
    self.close()
```

**测试策略**: 批量翻译2个插件（1成功1失败）→ 验证汇总弹窗显示对应状态，成功插件可双击打开报告。

### 步骤 5: 批量汇总对话框

**涉及文件**: `src/transbridge/ui/tools/ai_translator/_batch_report_summary_dialog.py`（新建）

**实现要点**:
- 创建 `_BatchReportSummaryDialog(QDialog)`
- QTableWidget 列：插件名 / 状态(✅⚠❌) / 成功数 / 失败数 / 跳过数 / 需审核数
- 状态列使用 emoji 或彩色文字
- 双击行 → 发射 `open_plugin_report(esp_stem)` 信号
- 底部按钮：「打开报告目录」（左侧）/ 「关闭」（右侧）
- 「打开报告目录」→ `os.startfile(data/ai_translator/)` 或最后一个报告的目录

**边界条件**:
- 报告目录不存在 → 按钮禁用
- 插件数量 > 10 → 正常显示，QTableWidget 原生支持滚动

**伪代码**:
```python
class _BatchReportSummaryDialog(QDialog):
    open_plugin_report = pyqtSignal(str)  # esp_stem

    def __init__(self, plugin_results, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量翻译报告汇总")
        self.resize(700, 400)
        self._plugin_results = plugin_results

        layout = QVBoxLayout(self)
        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["插件名", "状态", "成功", "失败", "跳过", "需审核"])
        table.setRowCount(len(plugin_results))
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.cellDoubleClicked.connect(self._on_double_click)

        status_icons = {"success": "✅", "stopped": "⚠", "failed": "❌"}
        for i, p in enumerate(plugin_results):
            table.setItem(i, 0, QTableWidgetItem(p["esp_stem"]))
            table.setItem(i, 1, QTableWidgetItem(status_icons.get(p["status"], "?")))
            table.setItem(i, 2, QTableWidgetItem(str(p["success"])))
            table.setItem(i, 3, QTableWidgetItem(str(p["failed"])))
            table.setItem(i, 4, QTableWidgetItem(str(p.get("skipped", 0))))
            table.setItem(i, 5, QTableWidgetItem(str(p.get("needs_review", 0))))

        layout.addWidget(table)

        # 底部按钮
        bar = QHBoxLayout()
        btn_dir = QPushButton("打开报告目录")
        btn_dir.clicked.connect(self._on_open_dir)
        bar.addWidget(btn_dir)
        bar.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bar.addWidget(btn_close)
        layout.addLayout(bar)

    def _on_double_click(self, row, col):
        p = self._plugin_results[row]
        if p["status"] in ("success", "stopped"):
            self.open_plugin_report.emit(p["esp_stem"])
            self.accept()
```

**测试策略**: 构造3个插件的 plugin_results 列表，验证表格行数、状态图标、双击信号发射正确。

### 步骤 6: 信号连接与跳转

**涉及文件**: `src/transbridge/ui/main_window.py`（修改）

**实现要点**:
- 新增 `_on_report_entry_activated(entry_id: str)` 方法（Step 7-详见于Story-11步骤6）
- 新增 `_find_main_window()` 辅助方法或通过 AppContext 获取 MainWindow 引用
  - 推荐在 `AppContext` 中新增 `main_window` 属性（弱引用）
  - 或在创建进度窗口时传入 `main_window` 引用

**边界条件**:
- MainWindow 已关闭 → 不连接信号，静默跳过

**伪代码**:
```python
# main_window.py
def _on_report_entry_activated(self, entry_id: str):
    """报告对话框中双击条目后跳转到Step2定位。"""
    if not self._ctx.collection:
        self.statusBar().showMessage("请先加载翻译集合", 5000)
        return
    entry = self._ctx.collection.get(entry_id)
    if entry is None:
        self.statusBar().showMessage(f"条目不存在或已被删除: {entry_id}", 5000)
        return
    # 切换到 Step2 tab
    self._workbench.setCurrentIndex(1)
    self._step2.locate_entry(entry_id)

# 辅助方法：从进度窗口获取主窗口引用
def _find_main_window(widget):
    """向上查找 MainWindow 父窗口。"""
    parent = widget.parent()
    while parent is not None:
        if isinstance(parent, MainWindow):
            return parent
        parent = parent.parent()
    return None
```

**测试策略**: 报告对话框中双击条目 → 验证主窗口切换到Step2并高亮。删除条目后双击 → 验证状态栏提示。

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ai_translator/translator.py` | 修改 | TranslationResult 新增4字段（~5行） |
| `src/transbridge/ui/tools/ai_translator/_translation_progress_window.py` | 修改 | _on_result() 替换QMessageBox → 报告对话框（~30行改） |
| `src/transbridge/ui/tools/ai_translator/ai_translator_window.py` | 修改 | 润色完成后新增报告生成+弹窗（~40行） |
| `src/transbridge/ui/tools/ai_translator/_batch_translation_progress_window.py` | 修改 | _on_all_finished() 替换QMessageBox → 汇总弹窗（~35行改） |
| `src/transbridge/ui/tools/ai_translator/_batch_report_summary_dialog.py` | 新建 | 批量汇总对话框（~80行） |
| `src/transbridge/ui/main_window.py` | 修改 | 新增 _on_report_entry_activated() + _find_main_window()（~25行） |

## 风险与注意事项

- **风险1**: 翻译 Worker 与进度窗口的耦合 — Worker 的 finished 信号携带的 result 对象需要包含后处理中间数据。当前实现可能只在 `_on_result` 中访问 result。缓解：确认 Worker 的 `finished` 信号参数包含完整的 TranslationResult。
- **风险2**: 报告弹出时机 — 如果翻译完成时 Step2 表格正在刷新（`collection_changed` 信号触发），可能导致 UI 短暂卡顿。缓解：报告对话框使用非模态 `show()`，不阻塞主事件循环。
- **注意1**: `_TranslationProgressWindow` 的生命周期 — 对话框关闭后进度窗口应保留引用防止被 GC，但报告对话框需要独立管理生命周期。使用 `WA_DeleteOnClose` 或父窗口管理引用。
- **注意2**: 润色模式中 `_PolishPreviewDialog` 已有写回逻辑，报告生成不要重复写回条目数据，只读取最终状态。
- **注意3**: 批量模式中多个插件的报告可能不在同一个 `reports/` 目录下（每个插件独立目录），汇总对话框的「打开报告目录」应打开共用的 `data/ai_translator/` 目录或第一个报告的目录。
- **注意4**: `_BatchReportSummaryDialog.open_plugin_report` 信号发出后，调用方需根据 `esp_stem` 找到对应的完整 `TranslationResult` 对象来创建 `_TranslationReportDialog`。
