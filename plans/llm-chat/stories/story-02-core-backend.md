# Story 02: 核心后端

**所属方案**: `plans/llm-chat/plan.md`
**技术模块**: `src/transbridge/ui/tools/smart_assistant/` (新建), `src/transbridge/ai_translator/` (修改)
**状态**: ✅ 已确认
**创建日期**: 2026-05-06

## 前置依赖

### 上游 Story
- Story-01 (同 plan): SmartAssistantPanel 基础框架 → 提供 ChatWidget.message_sent 信号 + SmartAssistantPanel 实例

### 跨 Plan 依赖
- `ai-translation/plan.md` → `LLMClient.chat_stream()`, `create_llm_client()`, `PromptBuilder` — 复用流式调用和配置
- `ai-translation/plan.md` → `LLMConfig.load_from_file()` — 读取 LLM 配置

### 引用的架构决策
- [ADR-004: QThread + 信号总线异步模式](../../../docs/adr/004-qthread-async-pattern.md) — ChatWorker 采用 QThread 模式，BaseException 取消控制
- [ADR-005: TOML Prompt 模板，不使用 LangChain](../../../docs/adr/005-toml-prompt-no-langchain.md) — 不引入 LangChain/LangGraph，自建 PromptBuilder 扩展

## 验收标准

- [ ] ConversationManager 正确维护多轮对话，max_turns=20
- [ ] ChatWorker 后台调用 LLM 流式接口，chunk 信号逐字传递
- [ ] ChatWorker 支持 cancel() 中断（threading.Event + LLMClient.cancel()）
- [ ] parse_hybrid_response() 正确解析 mode/thought/steps JSON 格式
- [ ] parse_hybrid_response() 兼容旧 tool_calls 格式和纯文本响应
- [ ] ExecutionEngine 对 steps 进行拓扑排序分层
- [ ] ExecutionEngine 同层级步骤在线程池中并行执行
- [ ] ExecutionEngine 支持 cancel() 优雅中断

## 数据流

```
用户消息
  │
  ▼
ConversationManager.add_user(text)
  │  get_messages() → [system, ..., user]
  ▼
ChatWorker.run()  ← QThread (后台线程)
  │  self._client.chat_stream(messages, max_tokens, chunk_cb)
  ├─► chunk_cb(chunk) → self.chunk.emit(chunk)  → ChatWidget 流式追加
  ├─► 完成后: self.finished.emit(full_text)       → ChatWidget._on_llm_finished
  └─► 异常: self.error.emit(msg)                   → ChatWidget 显示错误
  │
  ▼
PromptBuilder.parse_hybrid_response(full_text)
  │  1. 尝试提取 ```json ... ``` 块
  │  2. JSON 解析 → {mode, thought, steps}
  │  3. 兼容 tool_calls 旧格式
  │  4. 解析失败 → 降级纯文本
  ▼
  {"mode": "plan"|"react", "thought": "...", "steps": [...]}
  │
  ▼ (Story-03 处理 mode 分发)
ExecutionEngine.execute(steps)
  │
  ├─ _topological_levels(steps) → [[step_a, step_b], [step_c]]
  │    │
  │    ▼
  ├─ Level 0: ThreadPoolExecutor.map(_run_single, [step_a, step_b])
  │    ├─ step_started.emit(id, tool)
  │    ├─ ToolRegistry.execute(args, ctx) → StepResult
  │    └─ step_finished.emit(result)
  │    │
  │    ▼
  ├─ Level 1: ThreadPoolExecutor.map(_run_single, [step_c])
  │    ...
  │    ▼
  └─ all_finished.emit([StepResult, ...])
```

## 关键接口

### conversation_manager.py

```python
class ConversationManager:
    """维护多轮对话历史，封装 message list 操作"""

    def __init__(self, max_turns: int = 20):
        self._messages: list[dict] = []
        self._max_turns = max_turns

    def add_system(self, content: str) -> None:
        """system 消息始终在列表最前（索引 0）"""

    def add_user(self, content: str) -> None:
        """追加 user 消息，超过 max_turns 则移除最早的 user+assistant 对"""

    def add_assistant(self, content: str) -> None:
        """追加 assistant 消息"""

    def add_observation(self, tool_name: str, result: str) -> None:
        """追加工具执行结果作为 user 消息（[工具执行结果 - {tool_name}]\n{result}）"""

    def add_plan_result(self, summary: str) -> None:
        """追加计划执行聚合结果作为 user 消息（[计划执行完成]\n{summary}）"""

    def get_messages(self) -> list[dict]:
        """返回消息列表副本"""

    def clear(self) -> None:
        """清空所有消息"""
```

### chat_worker.py

```python
class ChatWorker(QThread):
    """后台线程：调用 LLM 流式 API，信号回调"""

    chunk = pyqtSignal(str)       # 流式 chunk
    finished = pyqtSignal(str)    # 完整响应
    error = pyqtSignal(str)       # 错误信息

    def __init__(self, llm_client, messages: list[dict], max_tokens: int = 2048):
        super().__init__()
        self._client = llm_client
        self._messages = messages
        self._max_tokens = max_tokens
        self._cancelled = threading.Event()

    def run(self) -> None:
        """在后台线程调用 llm_client.chat_stream()，通过信号发射 chunk/结果/错误"""

    def cancel(self) -> None:
        """设置取消标志 + 调用 llm_client.cancel() 关闭 HTTP 连接"""
```

### execution_engine.py

```python
@dataclass
class StepResult:
    step_id: int
    tool: str
    success: bool
    message: str
    data: Any = None
    duration_ms: int = 0

class ExecutionEngine(QObject):
    """统一执行引擎：DAG 拓扑排序 + 层级并行执行"""

    step_started = pyqtSignal(int, str)       # step_id, tool_name
    step_finished = pyqtSignal(StepResult)
    all_finished = pyqtSignal(list)            # list[StepResult]
    progress = pyqtSignal(int, int)            # completed, total

    def __init__(self, tool_registry: "ToolRegistry", ctx: AppContext):
        super().__init__()
        self._registry = tool_registry
        self._ctx = ctx
        self._cancelled = threading.Event()

    def execute(self, steps: list[dict]) -> list[StepResult]:
        """拓扑排序 → 层级并行 → 返回所有结果"""

    def cancel(self) -> None:
        """设置取消标志，正在执行的步骤完成后不再执行新步骤"""

    def _topological_levels(self, steps: list[dict]) -> list[list[dict]]:
        """按 depends_on 依赖关系分层，同层可并行。有环时兜底为单层串行"""

    def _run_single(self, step: dict) -> StepResult:
        """执行单个步骤：查 ToolRegistry → 调用 execute → 构建 StepResult"""
```

### prompt_builder.py (扩展)

```python
class PromptBuilder:
    # 现有方法保持不变

    def parse_hybrid_response(self, response: str) -> dict:
        """
        解析 LLM 混合模式响应。

        1. 提取 ```json ... ``` 代码块或裸 JSON
        2. 解析 mode / thought / steps
        3. 兼容旧 tool_calls 格式 → 自动转换为 steps
        4. 纯文本降级 → mode=react, thought=原文, steps=[]

        Returns: {"mode": "plan"|"react", "thought": str, "steps": [{"id", "tool", "args", "depends_on"}]}
        """
```

## 实现步骤

### 步骤 1: 创建 ConversationManager

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/conversation_manager.py`（新建）

**实现要点**:
- `add_system()` 插入到索引 0，替换已有 system 消息
- `add_user()` + `add_assistant()` 成对管理，超 max_turns 时移除最早的 user+assistant 对
- `add_observation()` / `add_plan_result()` 作为 user 角色消息追加

**边界条件**:
- `add_system()` 多次调用 → 最后一次覆盖
- `max_turns=0` → 不限制
- `clear()` 后 get_messages() → 返回空列表
- 消息列表为空的 LLM 调用 → 至少包含一条 system 消息

**伪代码**:
```python
def add_user(self, content):
    self._messages.append({"role": "user", "content": content})
    self._trim()

def _trim(self):
    # 计算 user+assistant 对数
    pairs = [(i, i+1) for i in range(len(self._messages))
             if self._messages[i]["role"] == "user"
             and i+1 < len(self._messages)
             and self._messages[i+1]["role"] == "assistant"]
    while len(pairs) > self._max_turns and self._max_turns > 0:
        # 移除最早的一对
        u_idx, a_idx = pairs.pop(0)
        del self._messages[a_idx]
        del self._messages[u_idx]
```

**测试策略**:
- 单测：添加 21 对 user+assistant → 确认只保留 20 对
- 单测：system 消息始终在索引 0
- 单测：add_observation 消息为 user 角色

### 步骤 2: 创建 ChatWorker

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/chat_worker.py`（新建）

**实现要点**:
- 继承 QThread，在 run() 中阻塞调用 `llm_client.chat_stream()`
- chunk_callback 检查 `_cancelled` 标志，决定是否继续发射 chunk 信号
- cancel() 先设置 Event，再调 llm_client.cancel() 关闭连接
- 异常捕获：`_CancelledByPause`/`_CancelledByStop` 不触发 error 信号，其他异常发射 error 信号

**边界条件**:
- cancel() 后 run() 中的 chunk_callback 不再发射 chunk → 避免取消后仍有 UI 更新
- LLM 客户端未配置 → finished 前先校验 llm_client 非 None
- ChatWorker 在 QThread 中执行 → `moveToThread` 不必要（本身是 QThread 子类）
- 线程安全：只有信号发射是线程安全的，ChatWidget 的槽函数在主线程执行

**伪代码**:
```python
def run(self):
    try:
        full_text = ""
        def chunk_cb(chunk):
            nonlocal full_text
            if self._cancelled.is_set():
                raise _CancelledByStop()
            full_text += chunk
            self.chunk.emit(chunk)

        self._client.chat_stream(
            self._messages, self._max_tokens, chunk_cb
        )
        if not self._cancelled.is_set():
            self.finished.emit(full_text)
    except (_CancelledByPause, _CancelledByStop):
        pass  # 不触发 error，静默终止
    except Exception as e:
        if not self._cancelled.is_set():
            self.error.emit(str(e))

def cancel(self):
    self._cancelled.set()
    if self._client:
        self._client.cancel()
```

**测试策略**:
- 集成测试：启动 ChatWorker，发送 chunk → 确认 UI 逐字更新
- 集成测试：启动后立即 cancel → 确认 finished 不触发，error 不触发
- 手动验证：网络断开时 → error 信号触发，UI 显示错误提示

### 步骤 3: 创建 ExecutionEngine

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/execution_engine.py`（新建）

**实现要点**:
- `_topological_levels()`：计算入度 → 逐层取出入度为 0 的节点 → 递减下游入度 → 循环
- `execute()`：逐层 ThreadPoolExecutor.map → 回收结果 → 发射信号
- `cancel()`：设置标志 → 当前层级完成后不进入下一层级
- 有环检测：剩余节点无法达到入度 0 → 兜底单层串行执行所有剩余步骤

**边界条件**:
- steps 为空列表 → 直接发射 all_finished([])
- 单步骤无依赖 → 一层一次执行
- 所有步骤都有依赖 → 正常拓扑排序
- 步骤依赖不存在的 step_id → 视为无依赖
- 最大并发数 = min(len(level_steps), 4)

**伪代码**:
```python
def _topological_levels(self, steps):
    step_map = {s["id"]: s for s in steps}
    in_degree = {s["id"]: len(s.get("depends_on", [])) for s in steps}
    remaining = set(step_map.keys())
    levels = []

    while remaining:
        current = [step_map[sid] for sid in remaining if in_degree[sid] == 0]
        if not current:  # 有环，兜底
            return [[step_map[sid] for sid in remaining]]
        levels.append(current)
        for step in current:
            remaining.remove(step["id"])
            for sid in remaining:
                if step["id"] in step_map[sid].get("depends_on", []):
                    in_degree[sid] -= 1
    return levels
```

**测试策略**:
- 单测：3 步骤 [1→2, 1→3] → 分两层: [[1], [2,3]]
- 单测：环依赖 [1→2, 2→1] → 兜底单层串行
- 单测：空列表 → all_finished([])

### 步骤 4: 扩展 PromptBuilder

**涉及文件**: `src/transbridge/ai_translator/prompt_builder.py`（修改）

**实现要点**:
- 新增 `parse_hybrid_response(response)` 方法
- 提取 JSON 块：`re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)`
- JSON 解析失败 → 尝试截断修复（参考 `_extract_partial_json_pairs` 的容错思路）
- 兼容 `tool_calls` 旧格式：`{tool_calls: [...]}` → 自动转为 `{steps: [{id, tool, args, depends_on:[]}]}`
- 最终兜底：纯文本 → `{mode: "react", thought: response, steps: []}`

**边界条件**:
- 响应为空字符串 → 降级纯文本
- JSON 中 steps 为 null → 改为空列表
- mode 字段缺失 → 默认 "react"
- tool_calls 和 steps 同时存在 → steps 优先
- 响应包含多个 JSON 块 → 取第一个

**伪代码**:
```python
def parse_hybrid_response(self, response: str) -> dict:
    json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
    raw = json_match.group(1) if json_match else response

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 尝试修复截断 JSON
        data = self._try_fix_truncated_json(raw)
        if data is None:
            return {"mode": "react", "thought": response, "steps": []}

    mode = data.get("mode", "react")
    thought = data.get("thought", "")
    steps = data.get("steps") or []
    tool_calls = data.get("tool_calls") or []

    if tool_calls and not steps:
        steps = [
            {"id": i+1, "tool": tc["tool"],
             "args": tc.get("args", {}), "depends_on": []}
            for i, tc in enumerate(tool_calls)
        ]

    return {"mode": mode, "thought": thought, "steps": steps}
```

**测试策略**:
- 单测：合法 JSON plan 模式 → 正确解析
- 单测：旧 tool_calls 格式 → 自动转换为 steps
- 单测：纯文本 → mode=react, steps=[]
- 单测：截断 JSON → 容错修复后解析
- 单测：空 response → 降级

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/tools/smart_assistant/conversation_manager.py` | 新建 | ConversationManager (多轮对话管理) |
| `src/transbridge/ui/tools/smart_assistant/chat_worker.py` | 新建 | ChatWorker (QThread LLM 流式调用) |
| `src/transbridge/ui/tools/smart_assistant/execution_engine.py` | 新建 | ExecutionEngine + StepResult |
| `src/transbridge/ai_translator/prompt_builder.py` | 修改 | 新增 parse_hybrid_response() |

## 风险与注意事项

- **QThread 跨线程安全**: 所有 UI 更新必须通过信号/槽，ChatWorker 中严禁直接操作 widget → ChatWidget 槽函数在主线程执行
- **_CancelledByStop 穿透**: 自定义 BaseException 不会被 `except Exception` 捕获，确保取消信号不被吞掉
- **JSON 截断容错**: LLM 的 max_tokens 可能在 JSON 中间截断 → `_try_fix_truncated_json()` 尝试补全 `}]}` 等结束符
- **ConversationManager 的 system 消息位置**: system 消息必须始终在列表最前面（符合 OpenAI/Anthropic API 规范）
- **ChatWorker 生命周期**: 必须保留引用（`self._workers.append(worker)`），防止被 GC 回收导致信号丢失
