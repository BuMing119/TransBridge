# Story 10: Checkpoint 与人机协同

**所属方案**: `plans/agent-upgrade/plan.md`
**技术模块**: smart_assistant
**状态**: 已确认
**创建日期**: 2026-05-10

## 前置依赖

### 上游 Story
- Story-09（同 plan）：必须已完成 → StatefulDAGExecutor + GraphSpec + HumanConfirmNode 类型就绪

### 引用的架构决策
- ADR-011: Checkpoint 序列化契约 + HITL QEventLoop 模式

## 验收标准

- [ ] `Checkpoint` 数据类（graph_id/current_node_id/completed_results/graph_state/timestamp）
- [ ] `save_checkpoint()` → `data/projects/{project}/{variant}/checkpoints/{graph_id}_{timestamp}.json`
- [ ] `load_checkpoint()` / `resume_from_checkpoint()`：恢复执行，跳过已完成节点
- [ ] `StepResult.data` 序列化前校验：仅允许 JSON 可序列化类型，不可序列化对象跳过 + 警告日志
- [ ] `HumanConfirmNode`：暂停 → node_paused 信号 → QEventLoop 等待 → provide_decision() → 继续
- [ ] 超时兜底：timeout_seconds 超时后自动采用 default_choice
- [ ] LoopNode 循环控制：每轮迭代后评估 exit_condition
- [ ] 异常中断 → 保留最近 checkpoint → 下次调用询问是否恢复

## 数据流

### Checkpoint 序列化

```
StatefulDAGExecutor.execute_graph(graph)
  │
  ├─→ 检查是否有未完成的 checkpoint → 询问用户是否恢复
  │
  ├─→ 每层执行完成后:
  │     checkpoint = Checkpoint(
  │       graph_id=graph.graph_id,
  │       current_node_id=last_completed_node,
  │       completed_results={node_id: StepResult, ...},
  │       graph_state=serializable_state,
  │       timestamp=datetime.now().isoformat(),
  │     )
  │     save_checkpoint(checkpoint)
  │
  ├─→ 异常中断:
  │     保留最近 checkpoint
  │     下次调用 execute_graph() 时检测到现有 checkpoint → 询问用户
  │
  └─→ 成功完成:
       清理 checkpoint 文件
```

### HITL 暂停/恢复

```
HumanConfirmNode 执行:
  │
  ├─→ node_paused.emit(node_id, prompt, choices)
  │     UI 端: QMessageBox.question() 弹窗
  │     后台线程: QEventLoop() local loop 等待
  │
  ├─→ 用户点击选择 → provide_decision(node_id, choice)
  │     → 退出 QEventLoop → 继续执行
  │
  └─→ 超时 → QTimer 触发 → provide_decision(node_id, default_choice)
```

## 关键接口

### Checkpoint 数据模型（追加到 graph_types.py）

```python
@dataclass
class Checkpoint:
    graph_id: str
    current_node_id: str
    completed_results: dict[str, dict]  # node_id → StepResult 的 JSON 序列化版本
    graph_state: dict                    # 可 JSON 序列化的状态字典
    timestamp: str                       # ISO format
```

### StatefulDAGExecutor checkpoint 方法

```python
class StatefulDAGExecutor:
    def save_checkpoint(self, graph, completed, state) -> Checkpoint:
        # 1. 过滤 StepResult.data 中不可序列化对象
        # 2. 序列化为 JSON
        # 3. 写入 .../checkpoints/{graph_id}_{timestamp}.json
    
    def load_checkpoint(self, graph_id: str) -> Checkpoint | None:
        # 查找该 graph_id 的最新 checkpoint 文件
    
    def resume_from_checkpoint(self, ckpt: Checkpoint) -> list[StepResult]:
        # 从 current_node_id 的下一个节点继续执行
        # 跳过 completed_results 中已有的节点
```

## 实现步骤

### 步骤 1: Checkpoint 数据类 + 序列化

**涉及文件**: `src/transbridge/smart_assistant/graph_types.py`（修改——追加 Checkpoint 类）

**实现要点**:
- Checkpoint dataclass：graph_id/current_node_id/completed_results(graph_state/timestamp
- 序列化辅助函数 `_serialize_data(value) → Any`：递归检查类型，dict/list/str/int/float/bool/None → 直接返回；其他 → 返回 None + 写 logging.warning
- 反序列化：JSON 读回 Checkpoint，completed_results 中的 dict 转回 StepResult

**边界条件**:
- data 含 datetime 对象 → 转为 ISO 字符串
- data 含 Qt 对象 → 跳过（Qt 对象不应出现在 StepResult.data 中）
- JSON 文件损坏 → 删除损坏文件，返回 None

### 步骤 2: save/load/resume checkpoint

**涉及文件**: `src/transbridge/smart_assistant/execution_engine.py`（修改）

**实现要点**:
- `save_checkpoint()`：每层执行完成后自动调用（在 execute_graph 的层级循环末尾）
- checkpoint 文件路径：`data/projects/{project}/{variant}/checkpoints/{graph_id}_{timestamp}.json`
- `load_checkpoint(graph_id)`：查找最新 checkpoint（按 timestamp 排序取最新）
- `resume_from_checkpoint(ckpt)`：构建节点索引，从 current_node_id 的下一个节点开始 BFS，跳过 completed_results 中的节点
- execute_graph 入口：检查是否有未完成 checkpoint → 有则询问用户是否恢复
- 全部完成后清理 checkpoint 文件

**边界条件**:
- project_path 未设置（ctx 中无项目）→ 使用默认临时目录
- checkpoint 目录不存在 → 创建
- 同一 graph_id 多个 checkpoint → 取最新；恢复后删除旧 checkpoint

### 步骤 3: HumanConfirmNode HITL 实现

**涉及文件**: `src/transbridge/smart_assistant/execution_engine.py`（修改）

**实现要点**:
- 在 dispatch 中处理 HumanConfirmNode：
  1. 发射 `node_paused.emit(node_id, prompt, choices)`
  2. 在后台线程中创建 `QEventLoop()` local loop
  3. `provide_decision()` 被 UI 线程调用 → 记录选择 → `loop.quit()`
  4. 超时兜底：启动 `QTimer.singleShot(timeout_seconds * 1000, lambda: provide_decision(node_id, default_choice))`
- provide_decision 方法：存储选择到 _pending_decisions dict，退出对应 QEventLoop

**边界条件**:
- 多个 HumanConfirmNode 同时暂停 → 各自独立 QEventLoop + QTimer
- 取消操作 → 提供 "终止" 选择 → 设置 _cancelled 标志并退出 loop
- QTimer 跨线程 → 使用 Qt.QueuedConnection 确保信号安全

### 步骤 4: ChatWidget HITL 确认弹窗

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/chat_widget.py`（修改）

**实现要点**:
- 连接 `node_paused` 信号 → QMessageBox.question()
- 弹窗内容：prompt + 选择按钮（映射 choices 列表）
- 用户点击后 → 调用 `executor.provide_decision(node_id, choice)`
- 连接 `node_resumed` 信号 → 关闭弹窗（超时场景）

**边界条件**:
- 用户在弹窗前最小化窗口 → 弹窗应在任务栏可见（QMessageBox 默认行为）
- 多条确认同时到达 → 弹窗排队（Qt 模态对话框自然排队）

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/graph_types.py` | 修改 | 追加 Checkpoint 数据类 + 序列化辅助函数 |
| `smart_assistant/execution_engine.py` | 修改 | save/load/resume checkpoint + HumanConfirmNode HITL 实现 + provide_decision |
| `ui/tools/smart_assistant/chat_widget.py` | 修改 | node_paused/node_resumed 信号连接 + 确认弹窗 |

## 风险与注意事项

- **风险**: QEventLoop 在后台线程中阻塞，如果 UI 线程崩溃则 loop 永不退出 → 缓解：超时兜底保证最终退出
- **注意**: QTimer 必须在有事件循环的线程中创建——后台线程创建 QEventLoop 后才有事件循环，QTimer 在 loop.exec() 之后创建
- **注意**: checkpoint 文件可能在磁盘上积累——自动清理策略（每次 execute_graph 开始前清理超过 7 天的 checkpoint）
- **注意**: LoopNode exit_condition 使用与 ConditionNode 相同的条件表达式引擎（S09 实现的 _eval_condition）
