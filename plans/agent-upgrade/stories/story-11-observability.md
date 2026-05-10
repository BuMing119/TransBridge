# Story 11: 可观测性系统

**所属方案**: `plans/agent-upgrade/plan.md`
**技术模块**: smart_assistant/observability
**状态**: 已确认
**创建日期**: 2026-05-10

## 前置依赖

### 上游 Story
- Story-05（同 plan）：已完成 → ExecutionEngine 信号管道就绪（step_started/step_finished/step_retrying）
- Story-09（同 plan）：已完成 → Graph 引擎信号扩展就绪

### 引用的架构决策
- ADR-012: pyqtSignal 遥测管道 + ObservabilityCollector + ConversationTrace + TokenStats

## 验收标准

- [ ] `ObservabilityCollector` 类：监听 pyqtSignal，聚合数据
- [ ] `ConversationTrace` 数据类：conv_id/rounds/tools_called/token_stats
- [ ] `ReActRound` 数据类：round_num/llm_input_tokens/llm_output_summary/tools/duration_ms
- [ ] `ToolCallRecord` 数据类：timestamp/tool_name/input_summary(截断500字符)/output_summary(截断500字符)/duration_ms/success/retry_count
- [ ] `TokenStats` 数据类：input_tokens/output_tokens/by_model
- [ ] 对话结束时自动保存 JSON → `data/projects/{project}/{variant}/observability/{conv_id}.json`
- [ ] 每轮 ReAct 结束在消息底部显示 token 摘要
- [ ] 会话级别 token 统计在状态栏持久显示
- [ ] 观测面板 Tab：Token 仪表盘 + 工具调用列表 + 对话轮次时间线
- [ ] 30 天自动清理

## 数据流

```
对话开始 → ObservabilityCollector.start_conversation(conv_id)
  │
  ├─→ step_started 信号 → 记录本轮工具调用开始
  ├─→ step_finished 信号 → ToolCallRecord 聚合
  ├─→ step_retrying 信号 → retry_count++
  ├─→ LLM chunk 回调 → token 计数累加
  │
  ▼
对话结束 → ObservabilityCollector.end_conversation()
  │
  ├─→ ConversationTrace.tools_called 聚合
  ├─→ ConversationTrace.token_stats 汇总
  ├─→ 序列化 JSON 到 observability/{conv_id}.json
  │
  └─→ 发射 token_stats_updated 信号 → UI 状态栏 + 观测面板刷新
```

## 关键接口

### models.py（新建）

```python
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class ToolCallRecord:
    timestamp: str = ""
    tool_name: str = ""
    input_summary: str = ""     # 截断至 500 字符
    output_summary: str = ""    # 截断至 500 字符
    duration_ms: int = 0
    success: bool = False
    retry_count: int = 0

@dataclass
class ReActRound:
    round_num: int = 0
    llm_input_tokens: int = 0
    llm_output_summary: str = ""  # 截断至 200 字符
    tools: list[str] = field(default_factory=list)
    duration_ms: int = 0

@dataclass
class TokenStats:
    input_tokens: int = 0
    output_tokens: int = 0
    by_model: dict[str, dict] = field(default_factory=dict)  # model → {input, output}

@dataclass
class ConversationTrace:
    conv_id: str = ""
    rounds: list[ReActRound] = field(default_factory=list)
    tools_called: list[ToolCallRecord] = field(default_factory=list)
    token_stats: TokenStats = field(default_factory=TokenStats)
    started_at: str = ""
    finished_at: str = ""
```

### collector.py（新建）

```python
class ObservabilityCollector(QObject):
    token_stats_updated = pyqtSignal(TokenStats)

    def __init__(self, storage_dir: Path | None = None, parent=None):
        super().__init__(parent)
        self._storage_dir = storage_dir
        self._active_conversation: ConversationTrace | None = None
        self._session_token_stats = TokenStats()
        self._current_round: ReActRound | None = None

    def start_conversation(self, conv_id: str): ...
    def on_step_started(self, step_id: int, tool_name: str): ...
    def on_step_finished(self, result: StepResult): ...
    def on_step_retrying(self, step_id: int, attempt: int): ...
    def on_llm_tokens(self, model: str, input_tokens: int, output_tokens: int): ...
    def end_conversation(self) -> ConversationTrace: ...
    def _cleanup_old_traces(self, max_age_days: int = 30): ...
```

## 实现步骤

### 步骤 1: 观测数据模型

**涉及文件**: `src/transbridge/smart_assistant/observability/models.py`（新建）

**实现要点**:
- 4 个 dataclass：ToolCallRecord / ReActRound / TokenStats / ConversationTrace
- 所有字段有默认值（可增量构建）
- ConversationTrace 含 to_json() / from_json() 方法

### 步骤 2: ObservabilityCollector

**涉及文件**: `src/transbridge/smart_assistant/observability/collector.py`（新建）

**实现要点**:
- QObject 子类，token_stats_updated 信号
- 连接 ExecutionEngine 的 step_started/step_finished/step_retrying 信号
- start_conversation → 创建 ConversationTrace → 开始计时
- on_step_started → 创建 ToolCallRecord 暂存
- on_step_finished → 完成 ToolCallRecord → 追加到 trace.tools_called
- end_conversation → 聚合 token_stats → 序列化 JSON → 发射更新信号
- _cleanup_old_traces：遍历 observability/ 目录，删除超过 30 天的 JSON 文件

**边界条件**:
- 无 storage_dir → 禁用持久化（仅内存保留）
- JSON 写入失败 → logging.error，不影响对话流程
- 重复 start_conversation → 先 end 上一个，再 start 新的

### 步骤 3: observability/__init__.py

**涉及文件**: `src/transbridge/smart_assistant/observability/__init__.py`（新建）

### 步骤 4: ExecutionEngine 集成

**涉及文件**: `src/transbridge/smart_assistant/execution_engine.py`（修改）

**实现要点**:
- __init__ 新增 `collector: ObservabilityCollector | None = None` 参数
- _run_single 中：执行前调用 collector.on_step_started，执行后调用 on_step_finished
- step_retrying 信号发射时同步调用 collector.on_step_retrying
- 职责边界：ExecutionEngine 不负责观测逻辑，仅转发信号给 Collector

### 步骤 5: ChatWidget UI 集成

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/chat_widget.py`（修改）

**实现要点**:
- 观测面板 Tab（QWidget 含 QVBoxLayout）：
  - Token 仪表盘：3 个 QLabel（今日/本周/本月）显示 input/output token 数
  - 工具调用列表：QTableWidget，列：时间/工具/耗时/状态/重试
  - 对话轮次时间线：QListWidget，每项显示轮次编号 + 调用工具数 + token 消耗
- 状态栏 Token 统计：session 级别的 input/output 总数（statusBar 永久 QLabel）
- 每轮对话结束后在消息底部显示 token 摘要（QHBoxLayout 含 token 统计文本）

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/observability/__init__.py` | 新建 | 子包入口 |
| `smart_assistant/observability/models.py` | 新建 | 观测数据模型 |
| `smart_assistant/observability/collector.py` | 新建 | ObservabilityCollector |
| `smart_assistant/execution_engine.py` | 修改 | 集成 Collector |
| `ui/tools/smart_assistant/chat_widget.py` | 修改 | 观测面板 + Token 摘要 + 状态栏 |

## 风险与注意事项

- **风险**: 观测 JSON 文件积累占用磁盘 → 缓解：30 天自动清理 + 单文件上限 10MB（序列化前检查）
- **注意**: Collector 是可选组件（ExecutionEngine 的 collector 参数默认为 None），不启用时不产生开销
- **注意**: TokenStat 的 by_model 字典用 model_name 作为 key，同一模型多次调用累加 input/output 值
