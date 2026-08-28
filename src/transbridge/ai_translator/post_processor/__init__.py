"""
AI翻译后处理模块

提供译文质量检查、一致性校验、格式验证、LLM修复、润色与裁决等功能。
"""

from .base import BaseChecker, PostProcessIssue, PostProcessResult
from .llm_arbiter import (
    ArbiterDecision,
    ArbitrationContext,
    LLMArbiter,
)
from .llm_refiner import (
    FixApplied,
    LLMRefiner,
    RefineResult,
)
from .polisher import (
    LLMPolisher,
    PolishResult,
)
from .post_processor import (
    PostProcessExecutionResult,
    PostProcessor,
    PostProcessorConfig,
)
from .quality_gate import QualityGateChecker, QualityVerdict

__all__ = [
    # 基础
    "BaseChecker",
    "PostProcessIssue",
    "PostProcessResult",
    # 主控器
    "PostProcessor",
    "PostProcessorConfig",
    "PostProcessExecutionResult",
    # 检查器
    "QualityGateChecker",
    "QualityVerdict",
    # 修复者
    "LLMRefiner",
    "RefineResult",
    "FixApplied",
    # 润色者
    "LLMPolisher",
    "PolishResult",
    # 裁决者
    "LLMArbiter",
    "ArbiterDecision",
    "ArbitrationContext",
]
