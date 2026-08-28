"""Smart Assistant — AI 驱动的翻译智能助手（多 Agent + 工具系统 + 护栏 + 可观测）。

为避免 Windows 1MB C 栈溢出 (0xC00000FD)，本 __init__.py 不进行任何模块级
导入。所有子模块按需惰性加载。
"""

import importlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agents import AgentInstance, AgentRegistry, AgentSpec
    from .chat_worker import ChatWorker
    from .checkpoint_manager import CheckpointManager
    from .condition_evaluator import ConditionEvaluator
    from .context_builder import ContextBuilder
    from .conversation_manager import ConversationManager
    from .conversation_orchestrator import ConversationOrchestrator
    from .execution_engine import ExecutionEngine
    from .graph_executor import StepResult
    from .graph_types import ActionNode, ConditionNode, GraphSpec, HumanConfirmNode, LoopNode
    from .guardrails import (
        GuardMiddleware,
        GuardResult,
        InputValidationGuard,
        OutputValidationGuard,
        PermissionGuard,
    )
    from .mcp import MCPAdapter, MCPServer
    from .observability import ConversationTrace, ObservabilityCollector, TokenStats
    from .prompts import build_system_prompt
    from .session_controller import SessionController
    from .session_manager import SessionManager
    from .tool_registry import ToolRegistry, ToolSpec
    from .tools import (
        ExecutionContext,
        HITLRequest,
        HITLResponse,
        HITLType,
        ToolResult,
        execute_with_guardrails,
        filter_entries,
    )

logger = logging.getLogger(__name__)

__all__ = [
    "ConversationManager",
    "ChatWorker",
    "ExecutionEngine",
    "StepResult",
    "ToolRegistry",
    "ToolSpec",
    "ContextBuilder",
    "build_system_prompt",
    "AgentSpec",
    "AgentInstance",
    "AgentRegistry",
    "GraphSpec",
    "ActionNode",
    "ConditionNode",
    "LoopNode",
    "HumanConfirmNode",
    "GuardMiddleware",
    "GuardResult",
    "PermissionGuard",
    "InputValidationGuard",
    "OutputValidationGuard",
    "ObservabilityCollector",
    "ConversationTrace",
    "TokenStats",
    "MCPServer",
    "MCPAdapter",
    "ToolResult",
    "ExecutionContext",
    "HITLRequest",
    "HITLResponse",
    "HITLType",
    "execute_with_guardrails",
    "filter_entries",
    "ConversationOrchestrator",
    "ConditionEvaluator",
    "CheckpointManager",
    "SessionController",
    "SessionManager",
]

# 符号 → 子模块映射表（供 __getattr__ 惰性加载用）
_SYMBOL_MODULES: dict[str, str] = {
    "ConversationManager": ".conversation_manager",
    "ChatWorker": ".chat_worker",
    "ExecutionEngine": ".execution_engine",
    "StepResult": ".graph_executor",
    "ToolRegistry": ".tool_registry",
    "ToolSpec": ".tool_registry",
    "ContextBuilder": ".context_builder",
    "build_system_prompt": ".prompts",
    "AgentSpec": ".agents",
    "AgentInstance": ".agents",
    "AgentRegistry": ".agents",
    "GraphSpec": ".graph_types",
    "ActionNode": ".graph_types",
    "ConditionNode": ".graph_types",
    "LoopNode": ".graph_types",
    "HumanConfirmNode": ".graph_types",
    "GuardMiddleware": ".guardrails",
    "GuardResult": ".guardrails",
    "PermissionGuard": ".guardrails",
    "InputValidationGuard": ".guardrails",
    "OutputValidationGuard": ".guardrails",
    "ObservabilityCollector": ".observability",
    "ConversationTrace": ".observability",
    "TokenStats": ".observability",
    "MCPServer": ".mcp",
    "MCPAdapter": ".mcp",
    "ToolResult": ".tools",
    "ExecutionContext": ".tools",
    "HITLRequest": ".tools",
    "HITLResponse": ".tools",
    "HITLType": ".tools",
    "execute_with_guardrails": ".tools",
    "filter_entries": ".tools",
    "ConversationOrchestrator": ".conversation_orchestrator",
    "ConditionEvaluator": ".condition_evaluator",
    "CheckpointManager": ".checkpoint_manager",
    "SessionController": ".session_controller",
    "SessionManager": ".session_manager",
}


def __getattr__(name: str):
    if name in _SYMBOL_MODULES:
        try:
            mod = importlib.import_module(_SYMBOL_MODULES[name], __package__)
            return getattr(mod, name)
        except ImportError:
            logger.warning("惰性加载模块失败: %s (符号=%s)", _SYMBOL_MODULES[name], name)
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
