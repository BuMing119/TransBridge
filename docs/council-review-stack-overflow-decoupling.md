# 评审委员会讨论纪要 — Smart Assistant C 栈溢出与 PyQt 解耦

**日期**: 2026-05-13
**评审对象**: Smart Assistant 后端 QObject 耦合 → Windows C 栈溢出 (0xC00000FD)
**参与角色**: 架构师 / 开发者 / QA / 安全专家
**触发**: 用户点击 AI 助手 → 发送消息 → 进程闪退，退出码 -1073741571 (0xC00000FD)

## 各角色独立意见

### 架构师
- **总体评价**: 保留意见。ADR-008 要求的「UI 与业务逻辑分离」停留在文件目录层面，ExecutionEngine/TaskManager/ObservabilityCollector 通过继承 QObject 在声明层绑死 PyQt6。
- **发现的问题/建议**:
  1. ExecutionEngine 7 个 pyqtSignal 的 QObject 继承不必要 — 优先级: 高
  2. TaskManager 的 pyqtSignal 用于已被 threading.Event 覆盖的场景 — 优先级: 高
  3. ObservabilityCollector 唯一信号可用回调替代 — 优先级: 中
  4. 懒加载 + 延迟初始化是治标之策 — 优先级: 中
  5. ADR-008 分离不彻底（依赖 QtCore 而非 QtWidgets） — 优先级: 中
  6. pyqtSignal 作为「便利性陷阱」掩盖设计缺陷 — 优先级: 低
- **推荐**: 优先移除 ExecutionEngine 和 TaskManager 的 QObject 继承，信号改为纯 Python 回调。

### 开发者
- **总体评价**: 保留意见。`_run_llm_round()` 单帧创建 3+ QObject (TaskManager + MessageBubble + ChatWorker) + 4 个 signal 连接是直接原因。
- **发现的问题/建议**:
  1. `_run_llm_round()` 单帧创建过多 QObject — 优先级: 高 → 短期 micro-stage 拆分
  2. TaskManager 单例的 QObject 创建代价被低估 — 优先级: 高 → 去 QObject + 回调列表
  3. MemoryWriterThread 完全不使用 pyqtSignal — 优先级: 中 → 改 threading.Thread
  4. ChatWorker 与 AgentWorker 重复模式 — 优先级: 中 → 提取 AsyncWorker 基类
  5. ExecutionEngine 信号依赖最深，短期不宜硬改 — 优先级: 低
  6. `__init__.py` 惰性导入引入 IDE/静态分析复杂性 — 优先级: 中
- **推荐**: 短期 micro-stage 拆分 (0.5h)，中期去 QObject 化 3 个类 (1.5h)。

### QA
- **总体评价**: 保留意见。QObject 耦合是架构层技术债务，0 自动化回归测试覆盖 Windows C 栈边界，7 处静默异常掩盖崩溃路径。
- **发现的问题/建议**:
  1. QObject 继承阻碍单元测试可编写性 — 优先级: 高 → 核心逻辑抽纯 Python
  2. 测试完全未覆盖 Windows C 栈边界 — 优先级: 高 → 增加 Windows CI runner
  3. `except Exception: pass` 掩盖崩溃路径诊断信息 — 优先级: 高 → 全量日志审计
  4. 缺乏自动化回归测试策略 — 优先级: 高 → pytest-qt L2 骨架
  5. 降级策略缺乏分层设计 — 优先级: 中 → Fatal/Error/Degraded/Retryable 四层
- **推荐**: 修复前先建立日志桩点 + pytest-qt 回归测试骨架。

### 安全专家
- **总体评价**: 保留意见。多层护栏设计框架严谨，但大面积静默异常吞没、双路径权限不一致、磁盘明文持久化是实质风险。
- **发现的问题/建议**:
  1. 静默异常吞没导致安全组件降级不可感知 — 优先级: 高 → fail_secure 策略
  2. ReAct 与 Plan 路径权限检查不一致 (TOCTOU) — 优先级: 高 → 统一到 ExecutionEngine
  3. `_MAX_REACT_DEPTH=10` 仅检查深度未限制步骤规模 (DoS) — 优先级: 中
  4. 磁盘持久化敏感数据无加密 — 优先级: 中 → 脱敏 + Windows DPAPI
  5. MemoryWriterThread QThread 继承增加 C 栈面 — 优先级: 低
- **推荐**: 优先审计 `except: pass` + 统一双路径权限检查。

## 共识汇总

以下建议获得**多个角色一致认同**：

- [ ] **TaskManager 去 QObject 化** (架构师 + 开发者 + QA + 安全，4/4) — 改用纯 Python 回调。改动小 (~35行)，收益大（减少 2 个 pyqtSignal 的 C++ 元对象开销）。
- [ ] **`except Exception: pass` 全量审计** (QA + 安全，2/4) — 8 处静默异常改为 logger.warning + 安全路径 fail_secure。在栈溢出前写入 final-will 日志。
- [ ] **MemoryWriterThread 改 threading.Thread** (开发者 + 安全 + 架构师，3/4) — 最简单解耦，~3 行更改。
- [ ] **补 Windows CI runner + pytest-qt 回归测试** (QA + 开发者，2/4) — 建立 L2 集成测试骨架，验收标准：10 轮完整发送路径无 0xC00000FD。
- [ ] **ObservabilityCollector 去 QObject 化** (架构师 + 开发者 + 安全，3/4) — 1 个 pyqtSignal 改为回调注入，~10 行改动。
- [ ] **统一 ReAct/Plan 双路径权限检查** (安全 + 架构师，2/4) — 消除不一致，统一到 `ExecutionEngine._run_guard_chain`。
- [ ] **短期 `_run_llm_round()` micro-stage 拆分** (开发者 + QA，2/4) — 3 阶段 QTimer 分帧，立竿见影缓解崩溃。

## 分歧与冲突

以下问题**存在角色间分歧**，需用户裁决：

- **冲突点**: ExecutionEngine 是否应立即去 QObject 化？
  - **甲方观点** (架构师): 应立即做。ExecutionEngine 的 7 个 pyqtSignal 是 C 栈消耗大户，作为核心引擎不应绑定 PyQt。
  - **乙方观点** (开发者): 短期不宜硬改。7 个信号连到 4 个消费者，内部 `step_requires_confirmation.emit()` + `_await_decision()` 的同步等待模式依赖 Qt 跨线程信号槽自动投递，重构风险高。建议等其他类解耦后作为最后一步处理。

## 综合建议清单

### 高优先级
- [ ] **TaskManager 去 QObject 化** — 改为纯 Python 类 + 回调列表 (`on_completed`/`on_failed`)，`chat_widget.py` 适配。~35 行改动，0.5h。
- [ ] **MemoryWriterThread 改 threading.Thread** — `memory_store.py` L42: `QThread` → `threading.Thread`，`wait(3000)` → `join(timeout=3)`。~3 行，5 分钟。
- [ ] **`except: pass` 全量日志审计** — 8 处 `except Exception: pass` 改为 `logger.warning`，安全路径 (`_ensure_task_manager`、`_ensure_middlewares`) 增加 fail_secure 降级通知。~30 行改动，0.5h。
- [ ] **_run_llm_round() micro-stage 拆分** — 3 阶段 QTimer 分帧 (A: 纯 Python 准备 → B: MessageBubble 创建 → C: ChatWorker 创建+信号连接+启动)。~50 行改动，0.5h。
- [ ] **统一 ReAct/Plan 双路径权限检查** — 将 ReAct 模式的权限逻辑委托给 `ExecutionEngine._run_guard_chain`，消除双路径分歧。~20 行改动，0.5h。

### 中优先级
- [ ] **ObservabilityCollector 去 QObject 化** — `pyqtSignal` → 回调函数注入，~10 行。
- [ ] **补 Windows CI runner + pytest-qt 回归测试** — L2 集成测试骨架 + 压力测试（10 轮发送不崩溃）。
- [ ] **MemoryStore 持久化数据脱敏** — 写入前应用 OutputValidationGuard 脱敏正则，当前明文写入对话内容。
- [ ] **增加单轮步骤数上限** — `_MAX_STEPS_PER_ROUND = 20`，DoS 防护。
- [ ] **错误路径熔断器** — N 分钟滑动窗口内错误率超阈值时暂停工具执行。

### 低优先级
- [ ] **ChatWorker/AgentWorker 提取 AsyncWorker 基类** — threading.Thread 基类，回调注入。~90 行。
- [ ] **ExecutionEngine 去 QObject 化** — 等其他类解耦验证稳定后再处理。~100 行，3-4h。
- [ ] **磁盘持久化数据加密** — cryptography.fernet + Windows DPAPI。
- [ ] **`__init__.py` 惰性导入映射表精简** — 从 47 个符号减到 10-15 个最重的符号。
- [ ] **ADR-008 更新** — 明确「后端包禁止新增 QObject/QThread 继承」的硬性编码规范。

## 短期修复路径（建议 2h 内执行）

按依赖关系排序：

```
Phase 1 (0.5h, 独立可并行):
  ├── MemoryWriterThread 改 threading.Thread
  ├── except:pass → logger.warning 审计
  └── TaskManager 去 QObject 化

Phase 2 (0.5h, 依赖 Phase 1 TaskManager):
  └── _run_llm_round() micro-stage 拆分

Phase 3 (0.5h, 独立):
  └── 统一 ReAct/Plan 双路径权限检查

Phase 4 (0.5h, 独立):
  └── pytest-qt 回归测试骨架 + Windows CI
```

## 纪要不构成决议

本文件仅为各角色独立意见的客观汇总，**不强制要求采纳任何建议**。最终决策权归用户所有。
