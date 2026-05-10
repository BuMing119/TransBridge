## ADR-007: AI翻译混合模式架构

**状态**: 已接受
**日期**: 2026-05-09
**对应需求**: FR5.11 AI翻译混合模式
**引用**: ADR-003（三轮翻译策略）、ADR-004（QThread异步模式）

### 背景

当前 AI 翻译窗口通过翻译/润色 RadioButton 实现全局互斥的二选一。用户希望在一次任务中同时执行翻译和润色，并通过分类、标记等维度为不同条目指定不同动作。这要求模式系统、作用域数据模型、执行引擎、进度UI、报告系统进行架构升级。

### 决策

#### 1. 模式系统：三 RadioButton 并存

在现有翻译/润色 RadioButton 基础上新增「混合」按钮。选中混合时，作用域选择器面板替换为规则映射表视图。翻译/润色模式完全向后兼容，无行为变化。

**影响**: `ai_translator_window.py` — `_mode_group` 新增 `_mode_mixed` RadioButton，`_on_mode_changed()` 增加混合分支

#### 2. 动作分配：规则映射表

定义 `ActionRule` 数据类表示一条分配规则：

```python
@dataclass
class ActionRule:
    rule_id: str                    # UUID
    priority: int = 0               # 越小越高
    status_filter: set[int] | None = None
    label_filter: set[str] | None = None
    category_filter: set[str] | None = None
    action: str = "skip"            # "translate" | "polish" | "skip"
```

规则按优先级降序匹配，命中第一条规则后停止。未匹配条目默认 `skip`。UI 使用 QTableWidget 展示规则列表（每行一条规则：状态筛选/标记筛选/分类筛选/动作），支持上移/下移/删除/添加。规则列表可保存到 LLMConfig INI 中持久化。

备选方案：统一动作维度标签（被否决——粒度不足）；条目级手动标记（被否决——与 FR5.10.6 解耦原则冲突）。

**影响**: 新建 `_rule_editor_widget.py`（规则列表 UI），`config_manager.py` 新增 `ActionRule` 序列化/反序列化

#### 3. 混合执行引擎：MixedWorker

新建 `_mixed_worker.py`，`_MixedWorker(QThread)` 作为统一调度线程：

```
信号协议:
  progress(part: str, current: int, total: int, msg: str)
       # part = "translate" | "polish"
  translate_finished(result: TranslationResult)
  polish_finished(results: dict[str, PolishResult])
  all_finished(translate_result, polish_results)
  error(part: str, err: str)

内部调度:
  串行模式: translate() → polish()
  并行模式: ThreadPoolExecutor(max_workers=2)，两个子任务并发
  共享 stop_event / pause_event 控制启停
```

备选方案：复用现有 Worker 在窗口层编排（被否决——双Worker信号协议不同，统一进度窗口难实现）。

**影响**: 新建 `_mixed_worker.py`，引用 `AutoTranslator` 和 `LLMPolisher`

#### 4. 统一进度窗口

扩展进度窗口支持双进度区域：

```
┌───────────────────────────────────┐
│  翻译进度  ████████░░  80%  8/10  │
│  成功:8  失败:2  跳过:0           │
├───────────────────────────────────┤
│  润色进度  ██████████  100% 5/5  │
│  接受:4  拒绝:1  失败:0           │
├───────────────────────────────────┤
│  [⏸ 暂停]  [⏹ 停止]              │
└───────────────────────────────────┘
```

**影响**: 修改 `_translation_progress_window.py` 或新建 `_mixed_progress_window.py`

#### 5. 合并报告

`ReportGenerator` 新增 `generate_mixed_report()`：

```
Excel 结构:
  └── 翻译-Summary / 翻译-Entries / 翻译-Issues / 翻译-Refinements / 翻译-Arbitrations
  └── 润色-Summary / 润色-Entries / 润色-Polish
```

应用内对话框新增「混合报告」模板：QTabWidget 含「翻译部分」和「润色部分」两个父级 Tab，各自内部包含原有的子 Tab。

**影响**: `report_generator.py` 新增方法，`_translation_report_dialog.py` 新增混合模板

#### 6. 后处理润色冲突处理

混合模式下，`_on_start()` 在创建 `PostProcessorConfig` 后强制设置 `pp_config.enable_polish = False`。UI 上对应 checkbox 置灰，tooltip 显示「混合模式下由独立润色流水线处理」。

**影响**: `ai_translator_window.py` — `_on_start()` 增加判断

### 备选方案

| 方案 | 考量 | 结论 |
|------|------|------|
| 删除翻译/润色模式，统一为混合 | 向后不兼容 | ❌ |
| 统一动作维度标签（非规则表） | 粒度不足 | ❌ |
| 复用现有Worker编排 | 双信号协议不统一 | ❌ |
| 配置层面持久禁用后处理润色 | 用户切换模式后需手动改回 | ❌ |

### 影响

- **目录变更**: 新建 `_mixed_worker.py`、`_rule_editor_widget.py`，可能新建 `_mixed_progress_window.py`
- **接口变更**: `TranslationResult` 无需变更；`ReportGenerator` 新增 `generate_mixed_report()`；`LLMConfig` 新增 `action_rules` 持久化
- **依赖变更**: 无新增外部依赖
- **向后兼容**: 翻译/润色两种现有模式行为完全不变
