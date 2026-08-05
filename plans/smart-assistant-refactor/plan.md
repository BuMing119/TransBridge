# Smart Assistant 模块超重文件拆分重构

**对应需求**: [FR10](../../docs/requirements.md#FR10-Smart-Assistant-模块超重文件拆分重构)
**技术模块**: backend
**业务域**: smart-assistant 代码健康度
**状态**: 已实现
**创建日期**: 2026-05-22

## 功能边界

### 范围内
- execution_engine.py: 上帝类拆分为 3 模块（组合模式）
- tools/base.py: 类型定义独立（types.py）
- tools/tool_translator.py: 模块级函数 → TranslationController 类
- tools/tool_proofreader.py: 模块级函数 → ProofreaderController 类
- tools/tool_editor.py: 模块级函数 → EditorController 类
- tools/_common.py: 提取重复的 LLM 配置/PostProcessor 构建逻辑
- conversation_orchestrator.py: 修复重复属性 bug + LLM 工厂提取
- memory/memory_writer.py: MemoryWriterThread 独立
- tools/task_manager.py: 移除废弃方法 + 合并重复方法对
- 全量测试保持通过（~223 用例）
- 所有新模块通过原路径重导出保持兼容

### 范围外
- UI 层超重文件（chat_widget.py 1107行, main_window.py 1480行）
- tool_paratranz.py / tool_parser.py 封装（结构可接受）
- 工具行为/API 签名变更
- 新功能引入
- 新依赖引入

## Story 清单

### Story 01: ExecutionEngine 上帝类拆分 (P0)

**验收标准**:
- [ ] `condition_evaluator.py` 存在，包含 `ConditionEvaluator` 类（~252行，10个 `_eval_ast_*` 方法 + `eval_condition` + `_eval_compare_op` + `_resolve_isinstance_type`）
- [ ] `checkpoint_manager.py` 存在，包含 `CheckpointManager` 类（~79行，`save_checkpoint` / `load_checkpoint` / `checkpoint_path` / `_safe_serialize`）
- [ ] `graph_executor.py` 存在，包含 `GraphExecutor` 类（~606行）+ `StepResult` dataclass，承接全部 BFS 执行/护栏链/重试/生命周期/回调注册逻辑
- [ ] `execution_engine.py` 从 888行 缩减至 ~84行，作为委托门面组合 `GraphExecutor`（含 `ConditionEvaluator` + `CheckpointManager` 重导出）
- [ ] `ExecutionEngine.__init__` 创建 `GraphExecutor` 实例（组合），通过 `__getattr__` 代理公开内部属性
- [ ] `execution_engine.py` 顶部重导出：`from .condition_evaluator import ConditionEvaluator` / `from .checkpoint_manager import CheckpointManager`
- [ ] 所有 BFS 执行路径行为不变（`execute_graph` → `_bfs_one_level` → `_dispatch_node` 调用链正常）
- [ ] `StepResult` 定义在 `graph_executor.py`（`execution_engine.py` 重导出），消除循环导入
- [ ] 现有测试全部通过（328/330 通过，2 个预存失败与本次重构无关）

**涉及文件**:
- 修改: `src/transbridge/smart_assistant/execution_engine.py`
- 新增: `src/transbridge/smart_assistant/condition_evaluator.py`
- 新增: `src/transbridge/smart_assistant/checkpoint_manager.py`
- 新增: `src/transbridge/smart_assistant/graph_executor.py`
- 修改: `src/transbridge/smart_assistant/__init__.py`

**详细文档**: `plans/smart-assistant-refactor/stories/story-01-execution-engine-split.md`

### Story 02: base.py 类型定义分离 (P0)

**验收标准**:
- [ ] `tools/types.py` 存在，包含 `ToolResult` / `ExecutionContext` / `HITLType` / `HITLRequest` / `HITLResponse`（~340行）
- [ ] `tools/base.py` 从 605行 缩减至 ≤300行，仅保留执行函数
- [ ] `base.py` 顶部 `from .types import ToolResult, ExecutionContext, HITLType, HITLRequest, HITLResponse` 重导出
- [ ] 所有工具模块的 `from .base import ToolResult, ExecutionContext` 继续可用
- [ ] `tools/__init__.py` 导出列表更新（如需要新增 types 模块导出）
- [ ] `ToolResult` 的 8 个方法行为不变
- [ ] `ExecutionContext` 的 `__getattr__` 代理逻辑不变
- [ ] 现有测试全部通过

**涉及文件**:
- 修改: `src/transbridge/smart_assistant/tools/base.py`
- 新增: `src/transbridge/smart_assistant/tools/types.py`
- 可能更新: `src/transbridge/smart_assistant/tools/__init__.py`

**详细文档**: `plans/smart-assistant-refactor/stories/story-02-types-separation.md`

### Story 03: 工具模块 Controller 封装 (P1)

**验收标准**:
- [ ] `tool_translator.py`: `TranslationController` 类（~350行），构造器注入 `AppContext` + `TaskManager`，9 个 `_tool_*` 函数 + 4 个内部辅助函数转为实例方法（`_load_llm_config` 提取到 `_common.py`，`_register_translator_tools` 保留为模块级）
- [ ] `tool_proofreader.py`: `ProofreaderController` 类（~280行），3 个 `_tool_*` 函数 + 5 个内部辅助函数转为实例方法（`set_last_report`/`get_last_report` 保留为模块级跨模块访问，`_register_proofreader_tools` 保留为模块级）
- [ ] `tool_editor.py`: `EditorController` 类（~350行），7 个 `_tool_*` 函数 + 1 个内部辅助函数转为实例方法（`_register_editor_tools` 保留为模块级）
- [ ] `tools/_common.py`: `load_llm_config()` 共享函数（~30行），消除 LLM 配置加载重复。`build_postprocessor` 暂不提取（两处签名差异较大）
- [ ] `_register_*_tools()` 使用惰性初始化 + 模块级 wrapper 兼容模式（避免空 AppContext 问题 + 保持测试向后兼容）
- [ ] `tool_translator.py` 和 `tool_proofreader.py` 中不再有重复的 LLM 配置加载逻辑
- [ ] 跨模块导入 `set_last_report` 保留为模块级函数（`tool_translator.py:260` 依赖）
- [ ] 158 处 `_tool_*` 测试引用全部有对应模块级 wrapper（`grep -rn "_tool_" tests/` 逐项验证）
- [ ] 所有 39 个工具的注册和执行行为不变
- [ ] 现有测试全部通过（~223 用例，0 ImportError）

**涉及文件**:
- 修改: `src/transbridge/smart_assistant/tools/tool_translator.py`
- 修改: `src/transbridge/smart_assistant/tools/tool_proofreader.py`
- 修改: `src/transbridge/smart_assistant/tools/tool_editor.py`
- 新增: `src/transbridge/smart_assistant/tools/_common.py`

**详细文档**: `plans/smart-assistant-refactor/stories/story-03-controller-encapsulation.md`

### Story 04: 剩余模块精简收尾 (P2)

**验收标准**:
- [ ] `conversation_orchestrator.py`: LLM 客户端创建逻辑提取为模块级 `_create_llm_client()` 函数（~60行）
- [ ] `conversation_orchestrator.py`: 去除 `_get_prompt_builder()` 中与 `prompts.build_system_prompt()` 重复的内联逻辑
- [ ] `memory/memory_writer.py` 存在，包含 `MemoryWriterThread` 类（~42行，5参数构造器保持不变）
- [ ] `memory/memory_store.py` 顶部 `from .memory_writer import MemoryWriterThread` 重导出
- [ ] `memory/__init__.py` 新增 `MemoryWriterThread` 导出
- [ ] `tools/task_manager.py`: 新增 `on_finished` / `notify_finished` 统一回调方法
- [ ] `tools/task_manager.py`: `on_completed`/`on_failed`/`notify_completed`/`notify_failed` 保留为 deprecated wrapper
- [ ] `tools/task_manager.py`: `set_main_thread_dispatcher`/`reset_dispatcher`/`get_handle` 保留不动（有活跃调用方）
- [ ] 所有公开 API 签名不变
- [ ] 现有测试全部通过

**涉及文件**:
- 修改: `src/transbridge/smart_assistant/conversation_orchestrator.py`
- 修改: `src/transbridge/smart_assistant/memory/memory_store.py`
- 新增: `src/transbridge/smart_assistant/memory/memory_writer.py`
- 修改: `src/transbridge/smart_assistant/tools/task_manager.py`

**详细文档**: `plans/smart-assistant-refactor/stories/story-04-orchestrator-memory-task-cleanup.md`

## 架构依赖

- [ADR-008](../../docs/adr/008-smart-assistant-code-layering.md) — 2026-05-22 更新节：组合拆分 + 模块粒度规范（文件≤450行/类≤22方法/模块级函数≤3）
- [ADR-009](../../docs/adr/009-agent-file-memory-reflexion.md) — Reflexion 边界不变：`_execute_tool_with_retry` 保留在 ExecutionEngine
- [ADR-012](../../docs/adr/012-safety-observability-mcp.md) — 护栏体系不变：guard_chain 保留在 base.py

## 风险与回退方案

| 风险 | 等级 | 缓解 | 回退 |
|------|------|------|------|
| ExecutionEngine 拆分后 BFS 执行顺序问题 | 中 | 拆分前后运行全量测试，对比 `_bfs_one_level` 调用链 | `git revert` 单 Story 的 commit |
| import 重导出遗漏导致外部调用断裂 | 低 | 全部通过原路径重导出，验证 `from ...base import ToolResult` 等关键路径 | 补加遗漏的重导出语句 |
| Controller 封装后闭包行为异常 | 低 | Controller 实例绑定方法签名与模块级函数签名一致 | `git revert` |
| 测试 import 路径过期 | 中 | 拆分后立即运行全量测试，更新过期 import | 批量搜索替换 import 路径 |

### 回退方案
- 每个 Story 独立 commit，出问题后 `git revert <commit>` 单 Story 即可
- 不涉及数据格式/API 变更，回退无副作用
