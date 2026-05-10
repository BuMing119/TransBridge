# Story 09: 独立润色入口

**所属方案**: `plans/ai-post-process/plan.md`
**技术模块**: ui / ai_translator
**状态**: ✔️ 已确认（已实现）
**创建日期**: 2026-05-07

## 前置依赖

### 上游 Story
- Story-05（同 plan）：LLMPolisher 已实现 → 提供 `polish()` / `polish_batch()` 和 `PolishResult`

### 跨 Plan 依赖
- `plans/ai-translation/plan.md` → AI 翻译窗口框架 (`AITranslatorWindow`) 已存在
- `plans/paratranz-integration/plan.md` → `LLMConfig` 配置持久化

### 引用的架构决策
- ADR-003: 三轮 AI 翻译策略 — 润色作为独立阶段，可单独启用
- ADR-004: QThread + 信号总线异步模式 — PolishWorker 复用该模式
- ADR-005: TOML Prompt 模板 — LLMPolisher 使用 TOML 提示词

## 验收标准

（来自 plan）

- [x] AI 翻译窗口顶部有翻译/润色模式切换控件
- [x] 润色模式下，翻译范围选项替换为「润色选中已翻译条目」，无译文条目自动跳过
- [x] 「开始润色」按钮替换「开始翻译」按钮
- [x] 润色配置（强度 light/moderate/aggressive）复用后处理标签页现有控件
- [x] 新增配置项「润色后预览确认」（checkbox，默认关闭），自动保存到 LLMConfig
- [x] 预览确认开启：弹出 `_PolishPreviewDialog`，三列对比原文/原译文/润色结果，逐条接受/拒绝
- [x] 预览确认关闭：润色结果直接写入条目
- [x] 润色过程显示进度窗口，支持暂停/停止
- [x] 选中条目均无译文时弹出提示
- [x] LLM 调用失败时保留原译文并标注失败原因

## 数据流

```
Step2.get_selected_entries()
    │
    ▼
[过滤: e.translation 非空]
    │
    ▼
LLMPolisher.polish(entry) × N     ← _PolishWorker (QThread)
    │ 逐条调用，信号: progress / entry_done / finished_all
    ▼
┌─────────────────────────────────────────────┐
│ polish_preview_enabled?                      │
│                                              │
│  true → _PolishPreviewDialog                 │
│         三列对比 → 用户逐条接受/拒绝          │
│         get_results() → {id: str|None}       │
│                                              │
│  false → 直接写入                            │
│          PolishResult.polished_translation    │
│          → collection.add(entry, overwrite)   │
└─────────────────────────────────────────────┘
    │
    ▼
collection_changed.emit(collection)
```

## 关键接口

### `_PolishWorker` (QThread)

```python
class _PolishWorker(QThread):
    progress = pyqtSignal(int, int, str)       # current, total, message
    entry_done = pyqtSignal(str, object)       # entry_id, PolishResult
    finished_all = pyqtSignal(dict)            # {entry_id: PolishResult}
    error = pyqtSignal(str)

    def __init__(self, polisher: LLMPolisher, entries: list[TranslationEntry])
    def stop()      # 设置 stop_event，中断循环
    def pause()     # 清除 pause_event，阻塞循环
    def resume()    # 设置 pause_event，恢复循环
```

### `_PolishPreviewDialog` (QDialog)

```python
class _PolishPreviewDialog(QDialog):
    def __init__(self, entries: list[TranslationEntry], results: dict[str, PolishResult])
    def get_results() -> dict[str, str | None]
        # entry_id → 润色后译文（None = 拒绝润色，保留原译文）
```

### AITranslatorWindow 新增方法

```python
def _on_mode_changed()      # 翻译/润色模式切换 → 更新 UI 可见性
def _on_polish_start()      # 润色模式入口：校验 → 创建 PolishWorker → 分流
def _polish_direct()        # 直接写入模式
def _polish_with_preview()  # 预览确认模式
def _apply_polish_results() # 应用用户确认的润色结果
```

### LLMConfig 新增字段

```python
polish_preview_enabled: bool = False  # 润色后预览确认开关
```

## 实现步骤

### 步骤 1: AITranslatorWindow 模式切换

**涉及文件**: `ai_translator_window.py`（改）

**实现要点**:
- 在 `_init_ui()` 中最顶部添加 QButtonGroup + 两个 QRadioButton（翻译/润色）
- `_on_mode_changed()`: 润色模式下隐藏 scope_box 三个选项和 overwrite_check，按钮文案变为「开始润色」，显示预估标签「润色范围：选中的已翻译词条」
- 翻译模式恢复原状

**边界条件**:
- 默认翻译模式，窗口初始显示与改造前完全一致
- 切换模式时立即更新按钮文案和控件可见性

### 步骤 2: 新增 _PolishWorker

**涉及文件**: `_polish_worker.py`（新）

**实现要点**:
- 继承 QThread，构造函数接收 LLMPolisher 和条目列表
- `run()` 逐条调用 `polisher.polish(entry)`，捕获异常生成带错误信息的 PolishResult
- 支持 stop/pause 通过 threading.Event 控制

**边界条件**:
- 单条抛光失败 → 生成 PolishResult(confidence=0, note="润色失败: ...")，继续下一条
- stop 信号 → 立即中断，已完成的保留在 results 中
- pause → 阻塞等待 resume 或 stop

### 步骤 3: 新增 _PolishPreviewDialog

**涉及文件**: `_polish_preview_dialog.py`（新）

**实现要点**:
- QDialog，顶部工具栏（全部接受/拒绝 + 统计标签），中间 QTableWidget 三列（原文/原译文/润色结果）
- 点击润色列切换接受（绿）/拒绝（红），默认待处理（黄）
- 底部「确认应用」按钮，未处理条目弹窗提醒
- `get_results()` 返回 entry_id → 润色后译文（None=拒绝）

**边界条件**:
- 润色失败条目 → 红色背景，不可切换，自动视为拒绝
- 全部接受 → 所有非拒绝行变为绿色
- 全部拒绝 → 所有行变为红色

### 步骤 4: _on_start() 模式分流

**涉及文件**: `ai_translator_window.py`（改）

**实现要点**:
- `_on_start()` 顶部检查 `_mode_polish.isChecked()`，是则委托 `_on_polish_start()`
- `_on_polish_start()`: 获取选中条目 → 过滤有译文的 → 创建 LLMClient + LLMPolisher → 创建 _PolishWorker
- 根据 `polish_preview_enabled` 分流：preview → `_polish_with_preview()`，direct → `_polish_direct()`
- 写入方式：构造新 TranslationEntry（translation=润色结果），`collection.add(updated, overwrite=True)`

**边界条件**:
- 选中条目均无译文 → 弹窗提示返回
- LLM API 失败 → 进度窗口显示错误，已完成的批次保留
- 翻译模式 → 完全保持原有逻辑不变

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/tools/ai_translator/ai_translator_window.py` | 改 | 模式切换 UI + _on_start 分流 + 润色流程方法 |
| `src/transbridge/ui/tools/ai_translator/_polish_worker.py` | 新 | QThread 封装 LLMPolisher 调用 |
| `src/transbridge/ui/tools/ai_translator/_polish_preview_dialog.py` | 新 | 润色结果三列对比预览对话框 |
| `src/transbridge/paratranz/config_manager.py` | 改 | LLMConfig 新增 polish_preview_enabled 字段 |

## 风险与注意事项

- **风险 1**: 润色直接写入模式下，用户误操作选择错误条目润色 → 可通过预览确认模式缓解，或使用 Git 回滚
- **注意 1**: `collection.add(overwrite=True)` 会完全替换条目，确保拷贝所有字段（dsd_type, dsd_index, editor_id, form_id_with_plugin, string_id）
- **注意 2**: 润色模式下术语库配置仍有效 — LLMPolisher 使用 term_manager 匹配术语辅助润色
