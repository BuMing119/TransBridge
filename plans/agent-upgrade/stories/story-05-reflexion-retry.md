# Story 05: Reflexion 自纠错

**所属方案**: `plans/agent-upgrade/plan.md`
**技术模块**: smart_assistant/reflexion
**状态**: 已确认
**创建日期**: 2026-05-10

## 前置依赖

### 上游 Story
- Story-01（同 plan）：infra/llm_client 就绪（RetryHandler 需要 LLM 分析失败原因）

### 引用的架构决策
- [ADR-009: ExecutionEngine 注入 Reflexion](../../../docs/adr/009-agent-file-memory-reflexion.md)

## 验收标准

- [ ] `RetryHandler` 类实现（LLM 分析失败原因 + 参数调整 + 重试，max 3 次）
- [ ] `ExecutionEngine._run_single()` 注入重试包裹
- [ ] 重试过程对用户可见：ToolCard 显示 "重试中 (n/3)…"
- [ ] 3 次全失败后优雅降级，不阻塞后续步骤
- [ ] 非工具错误（如网络超时）不触发 Reflexion（直接报错）

## 数据流

```
ExecutionEngine._run_single(step)
  → _execute_step(step)
  → 抛出异常 (非网络错误)
  → RetryHandler.analyze_and_adjust(step, error, attempt):
      1. 构建分析 prompt：工具名+参数+错误信息+尝试次数
      2. LLM 返回 JSON：{"retry": bool, "adjusted_args": {...}, "reason": "..."}
      3. 若 retry=true → 更新 step["args"] → 重新 _execute_step
      4. 若 retry=false → 返回 None → 放弃重试
  → 重试成功 → 返回 StepResult(success=True)
  → 重试耗尽 → 返回 StepResult(success=False, message=原始错误)
```

## 关键接口

### RetryHandler（retry_handler.py）

```python
class RetryHandler:
    MAX_RETRIES = 3
    # 不触发重试的错误类型
    NON_RETRYABLE_ERRORS = [
        "timeout", "connection", "network", "refused", 
        "unreachable", "401", "403", "429"
    ]
    
    def __init__(self, llm_client): ...
    
    def should_retry(self, error: str) -> bool:
        """判断错误类型是否应触发 Reflexion"""
        err_lower = error.lower()
        return not any(kw in err_lower for kw in self.NON_RETRYABLE_ERRORS)
    
    def analyze_and_adjust(self, step: dict, error: str, attempt: int) -> dict | None:
        """LLM 分析 → 返回调整后的 step 或 None"""
```

### ExecutionEngine 修改

在 `_run_single` 中包裹重试循环：
```python
def _run_single(self, step: dict) -> StepResult:
    attempt = 0
    current_step = dict(step)  # 浅拷贝，避免修改原始 step
    while True:
        try:
            result = self._execute_step(current_step)
            return result
        except Exception as exc:
            if not self._retry_handler.should_retry(str(exc)):
                return StepResult(step_id=step["id"], tool=..., success=False, message=str(exc))
            if attempt >= RetryHandler.MAX_RETRIES:
                return StepResult(step_id=step["id"], tool=..., success=False, message=str(exc))
            adjusted = self._retry_handler.analyze_and_adjust(current_step, str(exc), attempt)
            if adjusted is None:
                return StepResult(step_id=step["id"], tool=..., success=False, message=str(exc))
            current_step = adjusted
            attempt += 1
            self.step_retrying.emit(step["id"], attempt)  # 通知 UI
```

## 实现步骤

### 步骤 1: RetryHandler
**涉及文件**: `smart_assistant/reflexion/retry_handler.py` + `__init__.py`（新建）

### 步骤 2: ExecutionEngine 注入
**涉及文件**: `execution_engine.py`（改）
- `__init__` 新增 `self._retry_handler = RetryHandler(llm_client)`
- `_run_single` 包裹重试循环
- 新增 `step_retrying` 信号

### 步骤 3: UI 重试状态
**涉及文件**: `tool_card.py`（改）, `chat_widget.py`（改）
- ToolCard 新增 `set_retrying(attempt: int)` 方法
- chat_widget 连接 `step_retrying` 信号 → 更新对应 ToolCard 显示

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `reflexion/__init__.py` | 新建 | 子包导出 |
| `reflexion/retry_handler.py` | 新建 | RetryHandler 类 |
| `execution_engine.py` | 修改 | _run_single 注入重试 + step_retrying 信号 |
| `tool_card.py` | 修改 | set_retrying 方法 |
| `chat_widget.py` | 修改 | 连接 step_retrying 信号 |

## 风险与注意事项

- **风险**: 重试增加 token 消耗（每次重试额外 1 次 LLM 调用） → max_retries=3 限制上限
- **风险**: LLM 分析返回非 JSON → JSON 解析容错 + 降级为直接重试（不调整参数）
- **注意**: `_run_single` 中需浅拷贝 step 避免修改原始数据影响后续重试
